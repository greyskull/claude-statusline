"""RunningSubagent and RunningSubagents — active sub-agent discovery."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from yas.constants import CLAUDE_DIR, _sanitize


def read_last_prompt_ts(session_id: str) -> float | None:
    '''Return the last UserPromptSubmit timestamp for session_id, or None.

    Reads the yas-last-prompt.json state file (a JSON map of session_id →
    float epoch seconds) from CLAUDE_DIR.  Returns None when the file is
    missing, unreadable, contains invalid JSON, or does not include an entry
    for session_id.  Never raises.
    '''
    try:
        state = CLAUDE_DIR / 'yas-last-prompt.json'
        text = state.read_text()
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        val = data.get(session_id)
        if val is None:
            return None
        return float(val)
    except Exception:
        return None


def _parse_iso_to_epoch(ts: str) -> float:
    try:
        if ts.endswith('Z'):
            ts = ts[:-1] + '+00:00'
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return 0.0


# Closed enum confirmed across a real-world sample of 2974 <task-notification>
# records. An unrecognised status string (or no notification at all) MUST be
# treated as still-running — never as done — per the bias rule: prose/absence
# is never a completion signal, only this structured tag is.
_TERMINAL_STATUSES = frozenset(('completed', 'killed', 'failed', 'stopped'))

_TASK_NOTIF_RE      = re.compile(r'<task-notification>(.*?)</task-notification>', re.DOTALL)
_TASK_ID_TAG_RE      = re.compile(r'<task-id>(.*?)</task-id>', re.DOTALL)
_TOOL_USE_ID_TAG_RE  = re.compile(r'<tool-use-id>(.*?)</tool-use-id>', re.DOTALL)
_STATUS_TAG_RE       = re.compile(r'<status>(.*?)</status>', re.DOTALL)

class _TailCacheEntry(NamedTuple):
    '''One cached tail-read state: (mtime, size, byte-offset consumed, notifications found).'''
    mtime:         float
    size:          int
    offset:        int
    notifications: list['_Notification']


# Tail-read cache for notification scanning, keyed by absolute path string.
# Transcripts only ever grow, so an unchanged (mtime, size) pair means "no new
# notifications possible" — return the cached list with zero I/O. This runs
# on every statusline render, so re-parsing whole transcripts each time would
# be far too slow; only the bytes appended since the last read are scanned.
_notif_tail_cache: dict[str, _TailCacheEntry] = {}


class _Notification:
    '''One parsed <task-notification> occurrence.'''
    __slots__ = ('task_id', 'tool_use_id', 'status', 'ts')

    def __init__(self, task_id: str, tool_use_id: str, status: str, ts: float) -> None:
        self.task_id     = task_id
        self.tool_use_id = tool_use_id
        self.status      = status
        self.ts          = ts


def _extract_notifications(line: str) -> list[_Notification]:
    '''Extract zero or more <task-notification> blocks from one JSONL line.

    Handles both confirmed record shapes: a top-level
    ``{"type":"queue-operation","content":"<task-notification>..."}`` record,
    and a ``{"type":"user","message":{"content":"<task-notification>..."}}``
    record whose message content is a plain string (not the usual
    content-block list) carrying the same XML fragment. Falls back to a raw
    substring scan of the line when the JSON doesn't parse cleanly or doesn't
    match either known shape, so a notification embedded in some other record
    shape is never silently dropped.
    '''
    out: list[_Notification] = []
    text_blob: str | None = None
    ts = 0.0
    try:
        d = json.loads(line)
    except (ValueError, TypeError):
        d = None
    if isinstance(d, dict):
        ts_raw = d.get('timestamp', '')
        if ts_raw:
            ts = _parse_iso_to_epoch(str(ts_raw))
        rtype = d.get('type')
        if rtype == 'queue-operation':
            content = d.get('content')
            if isinstance(content, str):
                text_blob = content
        elif rtype == 'user':
            msg_raw = d.get('message')
            msg = msg_raw if isinstance(msg_raw, dict) else {}
            content = msg.get('content')
            if isinstance(content, str):
                text_blob = content
    if text_blob is None and '<task-notification>' in line:
        text_blob = line
    if text_blob is None or '<task-notification>' not in text_blob:
        return out
    for block in _TASK_NOTIF_RE.findall(text_blob):
        tid_m    = _TASK_ID_TAG_RE.search(block)
        tool_m   = _TOOL_USE_ID_TAG_RE.search(block)
        status_m = _STATUS_TAG_RE.search(block)
        task_id     = tid_m.group(1).strip()    if tid_m    else ''
        tool_use_id = tool_m.group(1).strip()   if tool_m   else ''
        status      = status_m.group(1).strip() if status_m else ''
        if task_id:
            out.append(_Notification(task_id, tool_use_id, status, ts))
    return out


def _tail_read_notifications(path: Path) -> list[_Notification]:
    '''Read new <task-notification> records from path since it was last seen.

    Cached by (path, mtime, size): an unchanged file returns the cached list
    with no I/O. A changed file is read only from the previously recorded
    byte offset onward — never the whole transcript — and the offset only
    ever advances to a completed line boundary, so a line still being
    streamed mid-write is re-read whole on the next call rather than parsed
    partially. Never raises; an unreadable file yields whatever was already
    cached (or nothing, on first sight).
    '''
    key = str(path)
    try:
        st = path.stat()
    except OSError:
        cached = _notif_tail_cache.get(key)
        return cached.notifications if cached else []

    cached = _notif_tail_cache.get(key)
    if cached is not None and cached.mtime == st.st_mtime and cached.size == st.st_size:
        return cached.notifications

    # A shrunk file (rotated/truncated) can't be tailed sanely — rescan from 0.
    reusable    = cached is not None and cached.size <= st.st_size
    prev_offset = cached.offset if reusable and cached is not None else 0
    notifications = list(cached.notifications) if reusable and cached is not None else []

    try:
        with path.open('rb') as fh:
            fh.seek(prev_offset)
            chunk = fh.read()
    except OSError:
        _notif_tail_cache[key] = _TailCacheEntry(st.st_mtime, st.st_size, prev_offset, notifications)
        return notifications

    last_nl = chunk.rfind(b'\n')
    if last_nl == -1:
        # No complete line has arrived since the last read; leave the offset
        # put so the still-growing partial line is re-read whole next time.
        _notif_tail_cache[key] = _TailCacheEntry(st.st_mtime, st.st_size, prev_offset, notifications)
        return notifications

    new_offset = prev_offset + last_nl + 1
    for raw_line in chunk[:last_nl].split(b'\n'):
        if b'<task-notification>' not in raw_line:
            continue
        notifications.extend(_extract_notifications(raw_line.decode('utf-8', errors='ignore')))

    _notif_tail_cache[key] = _TailCacheEntry(st.st_mtime, st.st_size, new_offset, notifications)
    return notifications


class _ToolResultCacheEntry(NamedTuple):
    '''One cached tail-read state for toolUseResult scanning: (mtime, size,
    byte-offset consumed, tool_use_id -> (status, ts) map found so far).'''
    mtime:   float
    size:    int
    offset:  int
    results: dict[str, tuple[str, float]]


# Tail-read cache for toolUseResult scanning, keyed by absolute path string.
# Same rationale as _notif_tail_cache: transcripts only grow, so re-scanning
# whole files on every render would be too slow.
_tool_result_tail_cache: dict[str, _ToolResultCacheEntry] = {}


def _extract_tool_results(line: str) -> list[tuple[str, str, float]]:
    '''Extract zero or more (tool_use_id, status, ts) triples from one JSONL line.

    Looks for a top-level ``toolUseResult`` field — a sibling of ``message``,
    not nested inside it — on a ``type: "user"`` record whose
    ``message.content`` carries the matching ``tool_result`` block. This is
    written by Claude Code core itself for every resolved Agent/Task tool
    call, independent of whether a ``<task-notification>`` was ever emitted.
    See the subagent-completion-signals investigation for the confirmed shape.
    '''
    out: list[tuple[str, str, float]] = []
    try:
        d = json.loads(line)
    except (ValueError, TypeError):
        return out
    if not isinstance(d, dict) or d.get('type') != 'user':
        return out
    tur = d.get('toolUseResult')
    if not isinstance(tur, dict):
        return out
    status = tur.get('status')
    if not isinstance(status, str) or not status:
        return out
    msg = d.get('message')
    content = msg.get('content') if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return out
    ts_raw = d.get('timestamp') or tur.get('timestamp') or ''
    ts = _parse_iso_to_epoch(str(ts_raw)) if ts_raw else 0.0
    for item in content:
        if isinstance(item, dict) and item.get('type') == 'tool_result':
            tool_use_id = str(item.get('tool_use_id', '') or '')
            if tool_use_id:
                out.append((tool_use_id, status, ts))
    return out


def _tail_read_tool_results(path: Path) -> dict[str, tuple[str, float]]:
    '''Read new tool_use_id -> (status, ts) pairs from path's toolUseResult
    sibling fields since it was last seen.

    Same tail-cache shape as _tail_read_notifications: an unchanged (mtime,
    size) pair skips all I/O; a changed file is read only from the previously
    recorded byte offset onward, never re-parsing the whole transcript.
    '''
    key = str(path)
    try:
        st = path.stat()
    except OSError:
        cached = _tool_result_tail_cache.get(key)
        return cached.results if cached else {}

    cached = _tool_result_tail_cache.get(key)
    if cached is not None and cached.mtime == st.st_mtime and cached.size == st.st_size:
        return cached.results

    reusable    = cached is not None and cached.size <= st.st_size
    prev_offset = cached.offset if reusable and cached is not None else 0
    results     = dict(cached.results) if reusable and cached is not None else {}

    try:
        with path.open('rb') as fh:
            fh.seek(prev_offset)
            chunk = fh.read()
    except OSError:
        _tool_result_tail_cache[key] = _ToolResultCacheEntry(st.st_mtime, st.st_size, prev_offset, results)
        return results

    last_nl = chunk.rfind(b'\n')
    if last_nl == -1:
        # No complete line has arrived since the last read; leave the offset
        # put so the still-growing partial line is re-read whole next time.
        _tool_result_tail_cache[key] = _ToolResultCacheEntry(st.st_mtime, st.st_size, prev_offset, results)
        return results

    new_offset = prev_offset + last_nl + 1
    for raw_line in chunk[:last_nl].split(b'\n'):
        if b'"toolUseResult"' not in raw_line:
            continue
        for tool_use_id, status, ts in _extract_tool_results(raw_line.decode('utf-8', errors='ignore')):
            results[tool_use_id] = (status, ts)

    _tool_result_tail_cache[key] = _ToolResultCacheEntry(st.st_mtime, st.st_size, new_offset, results)
    return results


class _NotifLookup(NamedTuple):
    '''Aggregated notification state for one task-id: latest status/ts, total occurrence count.'''
    status:       str
    ts:           float
    notif_count:  int


def _collect_task_notifications(session_jsonl: Path, subagents_dir: Path) -> dict[str, _NotifLookup]:
    '''Build a ``{task_id: _NotifLookup(status, ts, count)}`` map for one session tree.

    Scans the top-level session ``.jsonl`` AND every ``subagents/agent-*.jsonl``
    — a nested agent's completion notification lands in its PARENT AGENT's own
    transcript, not necessarily in the top-level session file, so every
    transcript in the tree must be scanned. Aggregates every
    ``<task-notification>`` seen per task-id: the occurrence with the latest
    timestamp decides ``status``/``ts`` (so a later re-notification of a
    resumed agent wins), and ``count`` is the total number of notifications
    observed for that task-id (a resumed agent notifies more than once).
    '''
    by_task: dict[str, list[_Notification]] = {}

    def _absorb(path: Path) -> None:
        for note in _tail_read_notifications(path):
            if note.task_id:
                by_task.setdefault(note.task_id, []).append(note)

    if session_jsonl.is_file():
        _absorb(session_jsonl)
    if subagents_dir.is_dir():
        try:
            for jsonl in subagents_dir.glob('agent-*.jsonl'):
                _absorb(jsonl)
        except OSError:
            pass

    result: dict[str, _NotifLookup] = {}
    for task_id, notes in by_task.items():
        latest = max(notes, key=lambda n: n.ts)
        result[task_id] = _NotifLookup(status=latest.status, ts=latest.ts, notif_count=len(notes))
    return result


def parse_transcript(jsonl: Path) -> tuple[int, int, int, float, str, tuple[str, str, dict[str, object]], float]:
    """Parse one agent-*.jsonl transcript into the subagent metric tuple.

    Module-level so the workflow cohort reader (info/workflows.py) can call the
    identical token/activity/Done logic without duplicating it. Returns
    ``(billed_in, cache_read_in, output, first_ts, model, last_activity, end_ts)``.
    Never raises; an unreadable transcript yields zeroes.
    """
    seen: set[str] = set()
    # Usage is keyed by message id with last-line-wins: streaming re-writes
    # the same id as it appends content blocks, and the usage counters GROW
    # across those writes — the final one carries the message's real totals.
    # Accumulating only the first write (behind the dedup) freezes usage at
    # the first partial snapshot and undercounts output tokens.
    usage_by_id: dict[str, tuple[int, int, int]] = {}
    first_ts     = 0.0
    end_ts       = 0.0
    model        = ''
    last_activity: tuple[str, str, dict[str, object]] = ('', '', {})
    # Activity is scoped to the FINAL message: block memory accumulates across
    # the streamed writes of one message id and resets when the id changes, so
    # a message's later tool_use/text writes are observed — its first streamed
    # write is usually the thinking block, and computing activity only behind
    # the dedup would leave every streamed agent stuck on "(thinking)".
    cur_mid  = ''
    cur_tool: dict[str, object] | None = None
    cur_text: dict[str, object] | None = None
    cur_has_content = False
    try:
        with jsonl.open('r', errors='ignore') as fh:
            for ln in fh:
                if first_ts == 0.0 and '"timestamp"' in ln:
                    try:
                        d = json.loads(ln)
                        ts = d.get('timestamp', '')
                        if ts:
                            first_ts = _parse_iso_to_epoch(ts)
                    except (ValueError, TypeError):
                        pass
                if '"usage"' not in ln or '"assistant"' not in ln:
                    continue
                try:
                    d = json.loads(ln)
                except (ValueError, TypeError):
                    continue
                msg = d.get('message') or {}
                mid = msg.get('id')
                # Terminal-state check runs on EVERY assistant+usage line,
                # independent of message-id dedup. Streaming writes the same
                # message.id several times (early partials with
                # stop_reason: null, a final write with end_turn); the dedup
                # below must not let an already-seen id suppress this capture.
                # Last-write-wins: a later end_turn overwrites an earlier
                # end_ts, and a later NON-terminal line clears it — a subagent
                # can be resumed after its turn ends (SendMessage to a warm
                # agent), and the stale end_ts would render a working agent as
                # Done. This end_turn-derived end_ts is a real API field, not
                # prose pattern-matching, and is kept for callers that still
                # consult parse_transcript directly (e.g. info/workflows.py);
                # RunningSubagent.end_ts is always overwritten from the
                # authoritative <task-notification> map instead — see
                # _collect_task_notifications above.
                try:
                    stop   = msg.get('stop_reason')
                    ts_raw = d.get('timestamp', '')
                    line_ts = _parse_iso_to_epoch(ts_raw) if ts_raw else 0.0
                    if stop == 'end_turn' and line_ts:
                        end_ts = line_ts
                    elif stop != 'end_turn':
                        end_ts = 0.0
                except (ValueError, TypeError, AttributeError):
                    pass
                if not mid:
                    continue
                # Update usage_by_id with last-line-wins: streamed usage counters
                # grow across an id's writes; the final write carries real totals.
                u = msg.get('usage') or {}
                usage_by_id[mid] = (
                    (u.get('input_tokens', 0) or 0) + (u.get('cache_creation_input_tokens', 0) or 0),
                    u.get('cache_read_input_tokens', 0) or 0,
                    u.get('output_tokens', 0) or 0,
                )
                # Activity is message-scoped: accumulate across streamed writes of the
                # same message id so later tool_use/text blocks (after the thinking
                # block) are observed with the usual tool_use > text > thinking priority.
                if mid != cur_mid:
                    cur_mid  = mid
                    cur_tool = None
                    cur_text = None
                    cur_has_content = False
                for item in (msg.get('content') or []):
                    if not isinstance(item, dict):
                        continue
                    cur_has_content = True
                    kind = item.get('type', '')
                    if kind == 'tool_use':
                        cur_tool = item
                    elif kind == 'text':
                        cur_text = item
                if mid in seen:
                    continue
                seen.add(mid)
                if not model:
                    m = msg.get('model') or ''
                    if m:
                        model = m
    except OSError:
        pass
    billed_in     = sum(billed for billed, _, _ in usage_by_id.values())
    cache_read_in = sum(cached for _, cached, _ in usage_by_id.values())
    output        = sum(out for _, _, out in usage_by_id.values())
    # Activity reflects the final message. Prefer its last tool_use block —
    # a trailing text narration must not mask an actual tool call (Claude
    # often emits [text, tool_use, text]) — then the first non-empty line of
    # its last text block, then thinking. The priority applies across the
    # id's streamed writes exactly as it does within a whole-message array.
    if cur_tool is not None:
        raw_inp = cur_tool.get('input') or {}
        inp = {
            k: _sanitize(v) if isinstance(v, str) else v
            for k, v in raw_inp.items()
        } if isinstance(raw_inp, dict) else {}
        last_activity = ('tool_use', _sanitize(str(cur_tool.get('name', '') or '')), inp)
    elif cur_text is not None:
        snippet = ''
        for line in str(cur_text.get('text', '') or '').splitlines():
            stripped = line.strip()
            if stripped:
                snippet = _sanitize(stripped)
                break
        last_activity = ('text', snippet, {})
    elif cur_has_content:
        last_activity = ('thinking', '', {})
    # Terminal-text Done fallback. Some sidechain (sub-agent) transcripts
    # never emit stop_reason: "end_turn" — every assistant line is either
    # "tool_use" or null, including the final result message. A finished
    # agent's LAST assistant line is then terminal text: a text block with no
    # tool_use awaiting a result. A still-running agent's last assistant line
    # is a tool_use (or it is mid-streaming), so this cannot fire once work
    # is genuinely done. Only the last line is considered, so interstitial
    # null-stop text mid-stream never triggers it.
    # NOTE: this used to also infer "done" from prose (a terminal-looking text
    # block with no trailing tool_use) and from a StructuredOutput tool_use as
    # the final action. Both were deleted: prose/heuristic completion caused a
    # confirmed false positive (an agent narrating "still waiting for the
    # actual completion notification..." was marked done while still alive).
    # The authoritative signal is now the <task-notification> record scanned
    # in RunningSubagents.from_session — see _collect_task_notifications
    # below. This function's end_ts remains end_turn-only (a real API field,
    # not prose pattern-matching) for callers that still consult it (e.g.
    # info/workflows.py), but RunningSubagent.end_ts is overwritten from the
    # notification map, never from this heuristic.
    return billed_in, cache_read_in, output, first_ts, model, last_activity, end_ts


def _build_tree_index(
    subs: list[RunningSubagent],
) -> tuple[dict[int, list[RunningSubagent]], list[RunningSubagent]]:
    '''Build the parent→children map and root list shared by the tree helpers.

    Matches each ``sub.parent_id`` against a sibling's ``agent_id`` (with or
    without the ``agent-`` filename prefix); an agent whose parent is unknown
    or not present in ``subs`` becomes a root. Returns ``(children, roots)``
    where ``children`` is keyed by ``id(parent)`` — the shared traversal
    primitive behind ``tree_order``, ``group_trees``, and (transitively)
    ``cap_tree_groups``.
    '''
    by_id: dict[str, RunningSubagent] = {}
    for sub in subs:
        if sub.agent_id:
            by_id[sub.agent_id] = sub
            by_id[sub.agent_id.removeprefix('agent-')] = sub
    children: dict[int, list[RunningSubagent]] = {}
    roots: list[RunningSubagent] = []
    for sub in subs:
        parent = by_id.get(sub.parent_id) if sub.parent_id else None
        if parent is not None and parent is not sub:
            children.setdefault(id(parent), []).append(sub)
        else:
            roots.append(sub)
    return children, roots


def tree_order(subs: list[RunningSubagent]) -> list[tuple[RunningSubagent, int, bool]]:
    '''Order a visible cohort parent-first for the tree view.

    Returns ``(sub, depth, is_last_child)`` triples in depth-first order:
    children group directly under their parent (matched by ``parent_id``
    against the parent's ``agent_id``, with or without the ``agent-`` filename
    prefix), siblings keep first_timestamp order, and an agent whose parent is
    unknown or not in ``subs`` renders as a top-level root (depth 0,
    is_last_child False). Pure ordering — no ANSI, no glyphs.
    '''
    children, roots = _build_tree_index(subs)
    out: list[tuple[RunningSubagent, int, bool]] = []

    def walk(sub: RunningSubagent, depth: int, last: bool) -> None:
        out.append((sub, depth, last))
        kids = children.get(id(sub), [])
        for i, kid in enumerate(kids):
            walk(kid, depth + 1, i == len(kids) - 1)

    for root in roots:
        walk(root, 0, False)
    return out


def group_trees(subs: list[RunningSubagent]) -> list[list[RunningSubagent]]:
    '''Group a candidate cohort into parent-rooted trees.

    Each group is a root (an agent whose parent isn't in ``subs``) plus every
    transitive descendant, linked the same way as ``tree_order`` (matched by
    ``parent_id`` against ``agent_id``, with or without the ``agent-``
    prefix). Within a group, members appear in discovery order (root first,
    then each child's own subtree); groups are returned in root
    ``first_timestamp`` order (the order ``subs`` arrives in).
    '''
    children, roots = _build_tree_index(subs)

    def collect(sub: RunningSubagent, out: list[RunningSubagent]) -> None:
        out.append(sub)
        for kid in children.get(id(sub), []):
            collect(kid, out)

    groups: list[list[RunningSubagent]] = []
    for root in roots:
        group: list[RunningSubagent] = []
        collect(root, group)
        groups.append(group)
    return groups


def cap_tree_groups(subs: list[RunningSubagent], cap: int) -> list[RunningSubagent]:
    '''Cap a visible cohort for tree mode without splitting a parent from a
    still-active child.

    Groups ``subs`` into parent-rooted trees (``group_trees``) and evicts
    whole groups — fully-finished groups first (lowest max ``end_ts``
    evicted first), then still-active groups (lowest max ``mtime`` evicted
    first) — until the total entry count is <= ``cap``. Eviction always
    removes a complete group, so a parent with a still-running descendant
    is never separated from it; only entirely-finished groups are dropped
    ahead of any group containing a live agent. A group is never evicted
    down to zero: whole-group eviction stops once a single group remains,
    and if that last group alone still exceeds ``cap``, it is trimmed in
    place (root kept, only its most-recently-active ``cap - 1`` descendants
    kept) rather than dropped entirely.
    '''
    groups = group_trees(subs)
    total = sum(len(g) for g in groups)
    if total <= cap:
        return subs

    finished = sorted(
        (g for g in groups if all(sub.end_ts > 0 for sub in g)),
        key=lambda g: max(sub.end_ts for sub in g),
    )
    active = sorted(
        (g for g in groups if any(sub.end_ts == 0 for sub in g)),
        key=lambda g: max(sub.mtime for sub in g),
    )

    kept = groups[:]
    for g in finished:
        if total <= cap or len(kept) == 1:
            break
        kept.remove(g)
        total -= len(g)
    if total > cap:
        for g in active:
            if total <= cap or len(kept) == 1:
                break
            kept.remove(g)
            total -= len(g)

    kept_ids = {id(g) for g in kept}
    ordered = [g for g in groups if id(g) in kept_ids]

    if total > cap and len(ordered) == 1:
        # A single group survives eviction but still exceeds cap on its own
        # (e.g. one root plus more live children than fit). Trim within it
        # instead of dropping it wholesale: keep the root, plus the
        # most-recently-active cap - 1 descendants, in original order.
        # Live members outrank finished ones here regardless of recency: a
        # finished row lingering out its retention window must never displace
        # a still-running sibling. Among equals, most-recently-active wins,
        # which for finished members is oldest-finished-evicted-first.
        root, *descendants = ordered[0]
        keep_ids = {
            id(sub) for sub in
            sorted(
                descendants,
                key=lambda sub: (sub.end_ts == 0, sub.mtime if sub.end_ts == 0 else sub.end_ts),
                reverse=True,
            )[:cap - 1]
        }
        trimmed = [root] + [sub for sub in descendants if id(sub) in keep_ids]
        return trimmed

    return [sub for g in ordered for sub in g]


class RunningSubagent:
    __slots__ = (
        'agent_type', 'description', 'billed_in', 'output', 'first_timestamp',
        'model', 'cache_read_in', 'total_input', 'last_activity', 'end_ts',
        'mtime', 'agent_id', 'jsonl_path', 'parent_id', 'spawn_depth',
        'status', 'run_count', 'is_fork', 'resumed',
    )

    def __init__(
        self,
        agent_type:      str,
        description:     str,
        billed_in:       int,
        output:          int,
        first_timestamp: float,  # epoch seconds; baseline for live duration
        model:           str = '',
        cache_read_in:   int = 0,
        total_input:     int = 0,
        last_activity:   tuple[str, str, dict[str, object]] | None = None,
        end_ts:          float = 0.0,  # authoritative <task-notification> ts; 0 while running
        mtime:           float = 0.0,  # transcript last-modified time (st_mtime)
        agent_id:        str = '',     # transcript filename stem; matches run-JSON agentId (workflow cohort)
        jsonl_path:      str = '',     # absolute path to this agent's transcript (for tool-count rescan)
        parent_id:       str = '',     # meta.json parentAgentId — spawner's agent id ('' → top-level)
        spawn_depth:     int = 0,      # meta.json spawnDepth (1 = spawned by main; 0 when absent)
        status:          str = 'running',  # "running"|"completed"|"killed"|"failed"|"stopped"
        run_count:       int = 0,      # <task-notification> occurrences seen for this agent; 0 while never finished
        is_fork:         bool = False,  # meta.json "isFork" (equivalently agentType == "fork")
        resumed:         bool = False,  # a later notification/activity postdates the last-seen notification
    ) -> None:
        self.agent_type      = agent_type
        self.description      = description
        self.billed_in        = billed_in
        self.output           = output
        self.first_timestamp  = first_timestamp
        self.model            = model
        self.cache_read_in    = cache_read_in
        self.total_input      = total_input
        self.last_activity    = last_activity if last_activity is not None else ('', '', {})
        self.end_ts           = end_ts
        self.mtime            = mtime
        self.agent_id         = agent_id
        self.jsonl_path       = jsonl_path
        self.parent_id        = parent_id
        self.spawn_depth      = spawn_depth
        self.status           = status
        self.run_count        = run_count
        self.is_fork          = is_fork
        self.resumed          = resumed

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RunningSubagent):
            return NotImplemented
        return self._key() == other._key()

    def _key(self) -> tuple[object, ...]:
        return (
            self.agent_type, self.description, self.billed_in, self.output,
            self.first_timestamp, self.model, self.cache_read_in, self.total_input,
            self.last_activity, self.end_ts, self.mtime, self.agent_id,
            self.jsonl_path, self.parent_id, self.spawn_depth,
            self.status, self.run_count, self.is_fork, self.resumed,
        )

    __hash__ = None  # type: ignore[assignment]

    @property
    def is_done(self) -> bool:
        '''Derived, not stored: true once an authoritative end_ts is set.'''
        return self.end_ts > 0

    def __repr__(self) -> str:
        return (f'RunningSubagent(agent_type={self.agent_type!r}, description={self.description!r}, '
                f'billed_in={self.billed_in}, output={self.output}, first_timestamp={self.first_timestamp}, '
                f'model={self.model!r}, cache_read_in={self.cache_read_in}, total_input={self.total_input}, '
                f'last_activity={self.last_activity!r}, end_ts={self.end_ts}, mtime={self.mtime}, '
                f'agent_id={self.agent_id!r}, jsonl_path={self.jsonl_path!r}, '
                f'parent_id={self.parent_id!r}, spawn_depth={self.spawn_depth}, '
                f'status={self.status!r}, run_count={self.run_count}, is_fork={self.is_fork}, '
                f'resumed={self.resumed})')


class RunningSubagents:
    __slots__ = ('subagents',)

    def __init__(self, subagents: list[RunningSubagent] | None = None) -> None:
        self.subagents = subagents if subagents is not None else []

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RunningSubagents):
            return NotImplemented
        return self.subagents == other.subagents

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f'RunningSubagents(subagents={self.subagents!r})'

    # Cohort grace: seconds after the last end_ts before a fully-Done section
    # retires. Matches FINISHED_LINGER_SECONDS (and constants.
    # SUBAGENT_RETENTION_SECONDS at the layout layer) so a fully-done cohort
    # doesn't retire on a shorter horizon than a lingering member of a still-
    # dirty cohort would.
    COHORT_GRACE_SECONDS = 120
    # Janitor horizon: total-silence threshold to sweep a dirty cohort (no end_turn);
    # also the recency-window fallback when no prompt-marker is available
    JANITOR_HORIZON_SECONDS = 60
    # Abandoned horizon: silence threshold applied to a still-running member
    # (end_ts == 0) before the janitor sweep treats it as orphaned rather than
    # merely quiet. A genuinely-alive subagent can go transcript-silent for
    # well over a minute (long tool call, extended thinking); only a much
    # longer gap is real evidence of a crashed/abandoned agent-*.jsonl.
    ABANDONED_HORIZON_SECONDS = 1800
    # Liveness window: silence threshold for "still writing" vs "idle/done" (straggler keep)
    LIVENESS_WINDOW_SECONDS = 30
    # Finished-member linger: how long a Done member of a still-dirty cohort
    # stays visible after its end_ts. Matches constants.SUBAGENT_RETENTION_SECONDS
    # so the info layer no longer retires a finished row an entire minute before
    # the layout's own retention horizon would. It is a MAXIMUM, not a
    # guarantee — cap_tree_groups still evicts finished rows early (oldest
    # end_ts first) whenever live members need the room.
    FINISHED_LINGER_SECONDS = 120
    # Terminal-signal skew: how far a transcript write may postdate a terminal
    # status/end_ts and still be attributed to clock skew between the writer of
    # the signal and the writer of the transcript. Beyond it, the write is
    # proof the agent outlived the signal (see from_session's invalidation).
    TERMINAL_SKEW_SECONDS = 5
    # Keep the old name as an alias so existing code that references it still works
    STALE_SECONDS = LIVENESS_WINDOW_SECONDS

    @classmethod
    def from_session(cls, session_id: str, project_dir: str, now: float | None = None) -> RunningSubagents:
        if not session_id or not project_dir:
            return cls()
        # now is injectable for tests; defaults to wall-clock time so the
        # ABANDONED_HORIZON_SECONDS fallback below is deterministic under test.
        if now is None:
            now = time.time()
        # Match Claude Code's projects/ dir convention: replace every non-
        # alphanumeric character with '-'. Works on both Unix
        # ('/home/user/my-project' -> '-home-user-my-project') and Windows
        # ('C:\\Users\\desal\\Project' -> 'C--Users-desal-Project'). The old
        # logic was Unix-only because it normalized only '/' and relied on a
        # leading slash producing the '-' prefix that Claude Code uses on
        # Unix; on Windows paths start with a drive letter (no leading '-'
        # in CC's dir name) so the f-string prefix gave a wrong path.
        project_slug = re.sub(r'[^A-Za-z0-9]', '-', project_dir)
        session_dir = CLAUDE_DIR / 'projects' / project_slug / session_id
        subagents_dir = session_dir / 'subagents'
        if not subagents_dir.is_dir():
            return cls()
        # Authoritative completion source (never prose): scan the top-level
        # session .jsonl AND every subagents/agent-*.jsonl for structured
        # <task-notification> records, keyed by task-id == agent-<id>.jsonl
        # filename stem minus the "agent-" prefix. See _collect_task_notifications.
        session_jsonl = CLAUDE_DIR / 'projects' / project_slug / f'{session_id}.jsonl'
        notif_map = _collect_task_notifications(session_jsonl, subagents_dir)
        subagents: list[RunningSubagent] = []
        try:
            for meta in subagents_dir.glob('*.meta.json'):
                agent_type = ''
                description = ''
                parent_id = ''
                spawn_depth = 0
                is_fork = False
                meta_model = ''
                tool_use_id = ''
                try:
                    data = json.loads(meta.read_text())
                    agent_type = _sanitize(data.get('agentType', '') or '')
                    description = _sanitize(data.get('description', '') or '')
                    # Parentage (tree view): parentAgentId names the spawning
                    # agent's id; spawnDepth is 1 for main-spawned agents. Both
                    # are absent in older metas → top-level fallback.
                    parent_id = str(data.get('parentAgentId', '') or '')
                    raw_depth = data.get('spawnDepth', 0)
                    spawn_depth = int(raw_depth) if isinstance(raw_depth, (int, float)) else 0
                    is_fork = bool(data.get('isFork', False)) or agent_type == 'fork'
                    meta_model = str(data.get('model', '') or '')
                    # toolUseId names the Agent/Task tool_use that spawned this
                    # agent — the join key for the tier-1 toolUseResult signal
                    # below (found in the spawning transcript's tool_result).
                    tool_use_id = str(data.get('toolUseId', '') or '')
                except Exception:
                    continue
                # The spawning transcript: top-level session file when there's
                # no parentAgentId, else the parent agent's own transcript
                # (a nested spawn's tool_result lands in ITS spawner's file,
                # same locality rule <task-notification> scanning documents).
                parent_jsonl = subagents_dir / f'agent-{parent_id}.jsonl' if parent_id else session_jsonl

                jsonl = meta.with_suffix('').with_suffix('.jsonl')
                if not jsonl.is_file():
                    continue
                try:
                    mtime = jsonl.stat().st_mtime
                except OSError:
                    continue

                billed_in, cache_read_in, output, first_ts, model, last_activity, transcript_end_ts = \
                    cls._parse_transcript(jsonl)
                if meta_model:
                    model = meta_model

                # Authoritative status, checked in priority order:
                #
                # Tier 1 (preferred): the SPAWNING transcript's own tool_result
                # record for the Agent/Task tool_use that created this agent —
                # a structured `toolUseResult.status` field written by Claude
                # Code core itself, not agent-authored prose. It fires even
                # when no <task-notification> was ever emitted. Only a
                # confirmed 'completed' status is trusted here; other values
                # haven't been observed in the wild yet (see the
                # subagent-completion-signals investigation), so anything else
                # falls through to the tiers below rather than risk a false
                # positive.
                status      = 'running'
                run_count   = 0
                notif_ts    = 0.0
                end_ts      = 0.0
                if tool_use_id and parent_jsonl.is_file():
                    tool_result = _tail_read_tool_results(parent_jsonl).get(tool_use_id)
                    if tool_result is not None and tool_result[0] == 'completed':
                        status = 'completed'
                        end_ts = tool_result[1] if tool_result[1] > 0 else mtime

                # Tier 2: <task-notification> scan. Always consulted for
                # run_count/resumed bookkeeping (a same-task-id notifying more
                # than once is the resume signal, independent of tier 1), but
                # only overrides `status` while tier 1 hasn't already resolved
                # it — an unrecognised or missing notification NEVER means
                # done on its own (bias rule).
                task_id = jsonl.stem.removeprefix('agent-')
                lookup = notif_map.get(task_id) or notif_map.get(jsonl.stem)
                if lookup is not None:
                    run_count = lookup.notif_count
                    notif_ts  = lookup.ts
                    raw_status = lookup.status
                    if status == 'running' and raw_status in _TERMINAL_STATUSES:
                        status = raw_status
                        end_ts = notif_ts
                # Resumed: more than one notification seen, or the transcript
                # kept being written after the last-seen notification (a
                # resumed agent appends more turns to the same jsonl).
                resumed = run_count > 1 or (notif_ts > 0 and mtime > notif_ts)

                # Tier 3 (last resort): lost-notification staleness fallback.
                # Neither tier 1 nor tier 2 ever fired — an upstream
                # event-emission gap — but the agent's own last assistant line
                # already carries a terminal stop_reason (end_turn, via
                # parse_transcript's transcript_end_ts) AND the transcript has
                # gone silent for the full ABANDONED_HORIZON_SECONDS — the same
                # long horizon visible() already uses to sweep orphaned
                # end_ts==0 members. Gating on both conditions (not stop_reason
                # alone) is what keeps this from reintroducing the reverted
                # end_turn-only false positive described in parse_transcript's
                # NOTE: a normal fast-finishing agent is still well within the
                # horizon and keeps waiting for a real tier-1/tier-2 signal.
                if status == 'running' and transcript_end_ts > 0 and now - mtime > cls.ABANDONED_HORIZON_SECONDS:
                    status = 'completed'
                    end_ts = mtime

                # Terminal-signal invalidation, applied AFTER every tier so no
                # tier can re-stamp it: a terminal signal is only believable
                # while the transcript agrees with it. A stall watchdog can
                # emit <status>failed</status> for an agent that is in fact
                # still working; the stamp then retires a live agent and
                # re-roots its children. A transcript write postdating the
                # signal by more than the skew tolerance proves the agent
                # outlived it, so drop the stamp and let it render live again.
                # `resumed` is computed above and deliberately survives this.
                if end_ts > 0 and mtime - end_ts > cls.TERMINAL_SKEW_SECONDS:
                    status = 'running'
                    end_ts = 0.0

                subagents.append(RunningSubagent(
                    agent_type      = agent_type,
                    description     = description,
                    billed_in       = billed_in,
                    output          = output,
                    first_timestamp = first_ts,
                    model           = model,
                    cache_read_in   = cache_read_in,
                    total_input     = billed_in + cache_read_in,
                    last_activity   = last_activity,
                    end_ts          = end_ts,
                    mtime           = mtime,
                    agent_id        = jsonl.stem,
                    jsonl_path      = str(jsonl),
                    parent_id       = parent_id,
                    spawn_depth     = spawn_depth,
                    status          = status,
                    run_count       = run_count,
                    is_fork         = is_fork,
                    resumed         = resumed,
                ))
        except OSError:
            pass
        subagents.sort(key=lambda s: s.first_timestamp)
        return cls(subagents=subagents)

    @classmethod
    def _live_ancestors(cls, subs: list[RunningSubagent], now: float) -> set[int]:
        '''ids of the agents that have a still-writing descendant.

        An agent is live when it carries no terminal signal (end_ts == 0) and
        its transcript was written within LIVENESS_WINDOW_SECONDS. Walking up
        each live agent's parent chain marks the whole branch above it, keyed
        by ``id(sub)`` to match _build_tree_index's identity convention.
        '''
        by_id: dict[str, RunningSubagent] = {}
        for sub in subs:
            if sub.agent_id:
                by_id[sub.agent_id] = sub
                by_id[sub.agent_id.removeprefix('agent-')] = sub
        ancestors: set[int] = set()
        for sub in subs:
            if sub.end_ts > 0 or now - sub.mtime > cls.LIVENESS_WINDOW_SECONDS:
                continue
            parent = by_id.get(sub.parent_id) if sub.parent_id else None
            # The membership guard also terminates on a parent_id cycle.
            while parent is not None and parent is not sub and id(parent) not in ancestors:
                ancestors.add(id(parent))
                parent = by_id.get(parent.parent_id) if parent.parent_id else None
        return ancestors

    def visible(self, now: float, last_prompt_ts: float | None) -> list[RunningSubagent]:
        '''Compute the turn-scoped cohort visible in the statusline.

        When last_prompt_ts is provided (from the prompt-boundary hook), an
        agent is a candidate if it started this turn (first_timestamp >=
        last_prompt_ts) OR it is still being written (transcript written within
        LIVENESS_WINDOW_SECONDS), which keeps stragglers from the previous turn
        that haven't finished yet, OR it has a live descendant (a supervising
        parent is transcript-silent while it waits on its children).  A
        still-running agent (end_ts == 0) that is actively writing is always
        included regardless.

        When last_prompt_ts is None (hook unavailable), fall back to the
        JANITOR_HORIZON_SECONDS recency window: include any agent written within
        60 s, or still running (end_ts == 0).

        After computing candidates, retirement rules apply:
        - If all candidates are Done (end_ts > 0): hide once
          now - max(end_ts) > COHORT_GRACE_SECONDS (120 s clean-retire).
        - Otherwise (dirty cohort): hide once every member's transcript has
          been silent for JANITOR_HORIZON_SECONDS (60 s janitor sweep).
        '''
        if last_prompt_ts is not None:
            # Turn-scoped membership (Tasks 3.2 + 3.3), plus the supervising-
            # parent keep: a parent blocked in a long wait loop writes nothing
            # while its children work, so mtime alone would evict it and
            # re-root its live children at the top level.
            live_parents = self._live_ancestors(self.subagents, now)
            candidates = [
                sub for sub in self.subagents
                if sub.first_timestamp >= last_prompt_ts
                or now - sub.mtime <= self.LIVENESS_WINDOW_SECONDS
                or id(sub) in live_parents
            ]
        else:
            # No-marker fallback (Task 3.4): recency window
            candidates = [
                sub for sub in self.subagents
                if now - sub.mtime <= self.JANITOR_HORIZON_SECONDS
                or sub.end_ts == 0
            ]

        if not candidates:
            return []

        # Retirement logic (Task 3.3), applied per member — not all-or-
        # nothing. A single still-active sibling in the same turn-scoped
        # cohort must not keep a long-finished member visible forever; each
        # candidate is independently dropped once IT satisfies the horizon
        # for its own state. Aggregate counts ("N active") that read
        # visible() see the smaller live set as a result, which is correct.
        all_done = all(sub.end_ts > 0 for sub in candidates)

        def _retired(sub: RunningSubagent) -> bool:
            if sub.end_ts > 0:
                # Done member: a fully-clean cohort retires on
                # COHORT_GRACE_SECONDS; a done member sitting inside a
                # still-dirty cohort lingers for FINISHED_LINGER_SECONDS so it
                # doesn't vanish mid-turn while a sibling is still working.
                # The two constants are equal by design (both 120s, matching
                # the layout layer's SUBAGENT_RETENTION_SECONDS) but this is
                # a select, not a sum: a candidate never accumulates both
                # horizons, so raising one doesn't compound with the other.
                horizon = self.COHORT_GRACE_SECONDS if all_done else self.FINISHED_LINGER_SECONDS
                return now - sub.end_ts > horizon
            # Still-running (end_ts == 0): no terminal signal at all, so
            # silence alone under an hour is not evidence it is dead -- it
            # may just be mid long-tool-call or extended thinking. Require
            # the much longer ABANDONED_HORIZON_SECONDS before sweeping.
            return now - sub.mtime > self.ABANDONED_HORIZON_SECONDS

        return [sub for sub in candidates if not _retired(sub)]

    @staticmethod
    def _parse_transcript(jsonl: Path) -> tuple[int, int, int, float, str, tuple[str, str, dict[str, object]], float]:
        # Thin delegator to the module-level parse_transcript, kept so existing
        # callers/tests referencing RunningSubagents._parse_transcript still work.
        return parse_transcript(jsonl)
