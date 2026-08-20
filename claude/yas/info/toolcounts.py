"""Per-tool tool_use counting with a main-vs-sub split, plus lines read/changed.

Counts ``tool_use`` blocks per tool name across the main transcript and the
session's subagent transcripts, windowed to the last ``/clear`` and split into a
``main`` column (the session's own transcript) and a ``sub`` column (summed over
every subagent transcript). Also counts lines read and changed per transcript.

Dedup differs from the sibling readers on purpose. ``transcript.py`` and
``subagents.py`` keep the FIRST occurrence per ``message.id`` — correct for token
accounting, where usage is stable across the streamed writes and first-wins
avoids double-counting. Here we keep the LAST occurrence per ``message.id``:
``tool_use`` blocks carry no stable id of their own, and streaming writes the
same ``message.id`` several times where earlier partial writes may contain FEWER
``tool_use`` blocks than the final write. To count the true number of tool calls
we must count the content of the last write per id. Do NOT "fix" this to match
the sibling parsers — first-wins would undercount.

## In-scope tools

Four tools contribute to line counts: ``Read``, ``Write``, ``Edit``, and the MCP
``DesignSync`` tool's ``get_file`` method (all other ``DesignSync`` methods,
e.g. ``list_files``, are not reads). Others (``Bash``, ``NotebookEdit``, etc.)
are excluded. ``NotebookEdit`` is excluded because its cell model does not map
cleanly to line counts.

## Lines read measurement

For each ``Read`` ``tool_use``, ``lines_read`` accumulates the newline count of
the paired ``tool_result.content``, but only when that content is a string
whose first line starts with a numeric ``cat -n``-style prefix (one or more
digits followed by a tab). The numbering starts at the ``offset`` argument
when one is given, not necessarily at line 1 — ``Read(offset=500)`` yields
content beginning ``"500\t..."``, which still counts. Image and document
reads have list-valued content and are skipped. This is the canonical sniff
test for text-shaped reads.

For each ``DesignSync`` ``tool_use`` with ``input.method == 'get_file'``,
``lines_read`` accumulates the newline count of the ``.content`` field inside
the paired ``tool_result``, whose content is a JSON *string* shaped like
``{"method":"get_file","path":...,"content":"<file text>"}`` rather than a
``cat -n`` blob. If the result content doesn't parse as that shape (e.g. a
harness-truncated ``<persisted-output>`` wrapper), the entry is skipped, not
raised.

## Lines changed measurement

- ``Edit``: counted as ``max(newlines(old_string), newlines(new_string))``, the
  size of the touched hunk.
- ``Write``: counted as ``newlines(content)``, the whole file written.

``replace_all: true`` is counted **once regardless of the number of replacements**,
so a bulk rename undercounts. This is accepted — the alternative requires
re-reading the edited file for every ``Edit``, which adds I/O cost.

## Main vs subagent sidechain asymmetry

Records with ``isSidechain: true`` in the **main** transcript are skipped;
``agent-*.jsonl`` files are counted in full with no sidechain filter. This is
deliberate: some dispatch conventions emit ``isSidechain: true`` for every
subagent record, so applying the skip to subagents would silently zero their
entire contribution, breaking the by-construction invariant
``session_total == main + Σ(subagents)``. The main-transcript skip alone suffices
because tool_use ids are fully disjoint between the two files.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from yas.constants import META_EXCLUDE_TOOLS
from yas.info.parsecache import TranscriptCache
from yas.info.subagents import RunningSubagent, _parse_iso_to_epoch

# Matches a cat -n style leading line number (any starting offset, not just
# line 1 — Read(offset=N) numbers its cat -n blob starting at N).
_CAT_N_PREFIX_RE = re.compile(r'^\d+\t')
# Byte-level equivalent of the above, for the pre-filter below (cheaper than
# json.loads; must not assume the numbering starts at 1). Matched against the
# raw JSON-encoded line, where a tab is escaped as the two-byte sequence
# b'\t' (backslash, t), not a literal tab byte.
_CAT_N_PREFIX_BYTES_RE = re.compile(rb'\d\\t')


@dataclass(slots=True)
class TranscriptToolStats:
    """Tool use and file-activity counts from one transcript walk."""

    counts: dict[str, int]  # tool name (MCP-normalized) -> count
    lines_read: int
    lines_changed: int


def count_transcript(
    path: str,
    clear_epoch: float | None,
    *,
    skip_sidechain: bool,
    cache: TranscriptCache | None = None,
    st: os.stat_result | None = None,
) -> TranscriptToolStats:
    """Count tool_use blocks and line activity in one transcript file.

    Returns a ``TranscriptToolStats`` with tool counts, lines read, and lines
    changed for ``tool_use`` blocks at or after ``clear_epoch`` (whole file when
    ``clear_epoch`` is None), deduped by ``message.id`` keeping the LAST
    occurrence, meta-excluded, MCP-normalized.

    When ``skip_sidechain`` is True, records with ``isSidechain: true`` are
    skipped; when False, they are counted in full. This asymmetry is deliberate:
    see the module docstring.

    Never raises; an unreadable/malformed file yields empty counts and zeros.
    """
    if not path:
        return TranscriptToolStats(counts={}, lines_read=0, lines_changed=0)

    # Attempt cache hit when cache is present (Task 4.1).
    if cache is not None:
        try:
            st = st or os.stat(path)
        except OSError:
            st = None
        if st is not None:
            cached = cache.get_counts(path, st, clear_epoch, skip_sidechain)
            if cached is not None:
                cached_counts = cached['counts']
                cached_lines_read = cached['lines_read']
                cached_lines_changed = cached['lines_changed']
                if (
                    isinstance(cached_counts, dict)
                    and isinstance(cached_lines_read, int)
                    and isinstance(cached_lines_changed, int)
                ):
                    return TranscriptToolStats(
                        counts=cached_counts,
                        lines_read=cached_lines_read,
                        lines_changed=cached_lines_changed,
                    )

    def _nl(s: object) -> int:
        """Count newlines in a string, or 0 if not a string."""
        return s.count('\n') if isinstance(s, str) else 0

    # message.id -> tool names from the most recent line seen for that id.
    per_id: dict[str, list[str]] = {}
    # message.id -> total lines_changed from all Edit/Write in that id.
    per_id_changed: dict[str, int] = {}
    # tool_use id -> True for each Read seen; used to match tool_result.
    read_ids: set[str] = set()
    # tool_use id -> True for each DesignSync get_file call seen; matched
    # against tool_result the same way, but the result is a JSON string
    # rather than a cat -n blob (see the DesignSync branch below).
    designsync_read_ids: set[str] = set()
    # tool_use_id -> True once its tool_result has contributed to lines_read,
    # so a retransmitted/duplicate tool_result can't double-count.
    counted_read_ids: set[str] = set()
    lines_read = 0
    lines_changed = 0

    try:
        with open(path, 'rb') as fh:
            for raw in fh:
                # V2 pre-filters (Decision 6): filter before json.loads.

                # (a) skip lines lacking both tool_use and tool_result
                if b'"tool_use"' not in raw and b'"tool_result"' not in raw:
                    continue

                # (b) if line has tool_result but not tool_use, require either
                #     a cat -n style digit-tab marker (JSON-escaped as e.g.
                #     '500\t', native Read — numbering may start at any
                #     offset, not just line 1) or the DesignSync get_file
                #     marker before decoding.
                if b'"tool_result"' in raw and b'"tool_use"' not in raw:
                    if (
                        not _CAT_N_PREFIX_BYTES_RE.search(raw)
                        and b'get_file' not in raw
                    ):
                        continue

                # (c) if skip_sidechain, reject sidechain records before decoding.
                if skip_sidechain:
                    if b'"isSidechain":true' in raw or b'"isSidechain": true' in raw:
                        continue

                try:
                    d = json.loads(raw)
                    msg = d.get('message') or {}

                    # Clear_epoch guard: the single window for both tool counts
                    # and line counts (Decision 4). Applied to every record,
                    # tool_use and tool_result alike, before either walk below.
                    if clear_epoch is not None:
                        ts = d.get('timestamp', '') or ''
                        if _parse_iso_to_epoch(ts) < clear_epoch:
                            continue

                    # Walk tool_result blocks to extract lines_read (Decision 6).
                    # This runs unconditionally, independent of message.id: a
                    # tool_result always lives on a user-role message, which in
                    # real transcripts never carries a message.id at all — gating
                    # this walk on `mid` (as tool_use accounting does) would skip
                    # every tool_result, unconditionally. Keyed only on
                    # tool_use_id membership in read_ids, which the tool_use walk
                    # below populates from assistant-role lines that always
                    # precede the matching tool_result in the file (Decision 7).
                    for block in msg.get('content') or []:
                        if not isinstance(block, dict):
                            continue
                        if block.get('type') != 'tool_result':
                            continue
                        tool_use_id = block.get('tool_use_id')
                        if tool_use_id in counted_read_ids:
                            continue
                        content = block.get('content')
                        if tool_use_id in read_ids:
                            # Only count if content is a string starting with
                            # a cat -n style digit-tab prefix (Decision 2).
                            # The numbering may start at any offset, not just
                            # line 1 (Read(offset=N) numbers from N).
                            if isinstance(content, str) and _CAT_N_PREFIX_RE.match(
                                content
                            ):
                                lines_read += content.count('\n')
                            counted_read_ids.add(tool_use_id)
                        elif tool_use_id in designsync_read_ids:
                            # DesignSync's result is a JSON string shaped like
                            # {"method":"get_file","path":...,"content":...}
                            # rather than a cat -n blob. Parse it and count
                            # newlines in the .content field; skip (don't
                            # crash) on any shape mismatch, e.g. a
                            # <persisted-output> wrapper from truncation.
                            if isinstance(content, str):
                                try:
                                    parsed = json.loads(content)
                                except (ValueError, TypeError):
                                    parsed = None
                                if isinstance(parsed, dict):
                                    file_text = parsed.get('content')
                                    if isinstance(file_text, str):
                                        lines_read += (
                                            file_text.count('\n') + 1
                                            if file_text
                                            else 0
                                        )
                            counted_read_ids.add(tool_use_id)

                    # The mid guard below only protects the tool_use-side
                    # accounting (`counts`, `lines_changed` via per_id /
                    # per_id_changed last-write-wins dedup) — tool_result
                    # records legitimately have no message.id and must not be
                    # gated by this check.
                    mid = msg.get('id')
                    if not mid:
                        continue

                    names: list[str] = []
                    id_changed = 0

                    # Walk tool_use blocks to record tool names and file activity.
                    for block in msg.get('content') or []:
                        if not isinstance(block, dict):
                            continue

                        if block.get('type') == 'tool_use':
                            name = block.get('name') or ''
                            if not name:
                                continue
                            name = name.split('__')[-1]  # MCP normalization
                            if name in META_EXCLUDE_TOOLS:
                                continue
                            names.append(name)

                            # Record file-activity per block (Decision 5).
                            if name == 'Read':
                                # Remember Read ids for tool_result pairing.
                                block_id = block.get('id')
                                if block_id:
                                    read_ids.add(block_id)
                            elif name == 'DesignSync':
                                # Only method 'get_file' is a read; other
                                # methods (e.g. list_files) are not.
                                inp = block.get('input') or {}
                                if inp.get('method') == 'get_file':
                                    block_id = block.get('id')
                                    if block_id:
                                        designsync_read_ids.add(block_id)
                            elif name == 'Edit':
                                # lines_changed += max(old_string, new_string)
                                inp = block.get('input') or {}
                                old = inp.get('old_string')
                                new = inp.get('new_string')
                                id_changed += max(_nl(old), _nl(new))
                            elif name == 'Write':
                                # lines_changed += content
                                inp = block.get('input') or {}
                                content = inp.get('content')
                                id_changed += _nl(content)
                            # Note: NotebookEdit is not counted (Decision 1).

                    # Last-write-wins dedup: replace per message.id and sum
                    # at the end (Decision 8).
                    per_id[mid] = names
                    per_id_changed[mid] = id_changed

                except (ValueError, TypeError):
                    continue
    except OSError:
        return TranscriptToolStats(counts={}, lines_read=0, lines_changed=0)

    # Sum tool counts and lines_changed across the final per-id state.
    counts: dict[str, int] = {}
    for names in per_id.values():
        for name in names:
            counts[name] = counts.get(name, 0) + 1
    lines_changed = sum(per_id_changed.values())

    result = TranscriptToolStats(
        counts=counts,
        lines_read=lines_read,
        lines_changed=lines_changed,
    )

    # Cache the result when cache is present and we have stat info (Task 4.1).
    if cache is not None and st is not None:
        cache.put_counts(
            path, st, clear_epoch, skip_sidechain,
            {'counts': result.counts, 'lines_read': result.lines_read, 'lines_changed': result.lines_changed},
        )

    return result


class ToolCounts:
    """Per-tool ``(main, sub)`` tool_use counts, session line totals, and per-agent breakdown."""

    __slots__ = ('counts', 'lines_read', 'lines_changed', 'per_agent')

    def __init__(
        self,
        counts: dict[str, tuple[int, int]] | None = None,
        lines_read: int = 0,
        lines_changed: int = 0,
        per_agent: dict[str, tuple[int, int]] | None = None,
    ) -> None:
        # tool name (MCP-normalized) -> (main_count, sub_count)
        self.counts = counts if counts is not None else {}
        # Session totals: main + all subagents.
        self.lines_read = lines_read
        self.lines_changed = lines_changed
        # Per-subagent breakdown: transcript path -> (lines_read, lines_changed)
        self.per_agent = per_agent if per_agent is not None else {}

    @property
    def total_types(self) -> int:
        """Number of distinct tool types counted (for +k overflow math)."""
        return len(self.counts)

    # Backwards-compatible alias.
    type_count = total_types

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ToolCounts):
            return NotImplemented
        return (
            self.counts == other.counts
            and self.lines_read == other.lines_read
            and self.lines_changed == other.lines_changed
            and self.per_agent == other.per_agent
        )

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return (
            f'ToolCounts('
            f'counts={self.counts!r}, '
            f'lines_read={self.lines_read}, '
            f'lines_changed={self.lines_changed}, '
            f'per_agent={self.per_agent!r})'
        )

    @classmethod
    def gather(
        cls,
        main_path:   str,
        subagents:   list[RunningSubagent],
        clear_epoch: float | None,
        cache: TranscriptCache | None = None,
    ) -> ToolCounts:
        """Build the merged ``(main, sub)`` counts and session line totals.

        Also computes per-subagent line counts. The sidechain skip is asymmetric
        (main only, not subagents) — if applied to subagents, some dispatch
        conventions would zero the entire subagent contribution, breaking the
        by-construction invariant ``session_total == main + Σ(subagents)``.
        The id-disjointness verified in design.md Context makes this safe.
        """
        # Gather main transcript with sidechain skip (Decision 4).
        main_stats = count_transcript(
            main_path, clear_epoch, skip_sidechain=True, cache=cache
        )
        main_counts = main_stats.counts

        # Gather subagents with NO sidechain skip (Decision 4).
        sub_counts: dict[str, int] = {}
        per_agent_lines: dict[str, tuple[int, int]] = {}
        total_lines_read = main_stats.lines_read
        total_lines_changed = main_stats.lines_changed

        for agent in subagents:
            agent_stats = count_transcript(
                agent.jsonl_path, clear_epoch, skip_sidechain=False, cache=cache
            )
            # Accumulate tool counts across subagents.
            for name, n in agent_stats.counts.items():
                sub_counts[name] = sub_counts.get(name, 0) + n
            # Record per-subagent line counts and accumulate to session total.
            per_agent_lines[agent.jsonl_path] = (
                agent_stats.lines_read,
                agent_stats.lines_changed,
            )
            total_lines_read += agent_stats.lines_read
            total_lines_changed += agent_stats.lines_changed

        # Build the final (main, sub) tool counts.
        counts: dict[str, tuple[int, int]] = {}
        for name in main_counts.keys() | sub_counts.keys():
            counts[name] = (main_counts.get(name, 0), sub_counts.get(name, 0))

        return cls(
            counts=counts,
            lines_read=total_lines_read,
            lines_changed=total_lines_changed,
            per_agent=per_agent_lines,
        )
