"""Tests for lines-read/lines-changed counting in yas.info.toolcounts.

Covers: the cat -n sniff test for lines_read, the Edit max(old, new) and Write
whole-content rules for lines_changed, the NotebookEdit exclusion, the
replace_all undercount, the main-vs-subagent sidechain asymmetry, the
clear_epoch window applied to line counts, the by-construction
session-total invariant across ToolCounts.gather, and pre-filter/naive-walk
equivalence (design.md Decision 6).
"""

import json
from pathlib import Path

from yas.constants import META_EXCLUDE_TOOLS
from yas.info.subagents import RunningSubagent, _parse_iso_to_epoch
from yas.info.toolcounts import ToolCounts, count_transcript

TS_EARLY = '2026-01-01T00:00:00Z'
TS_LATE  = '2026-01-01T12:00:00Z'


def _assistant_line(
    mid: str,
    blocks: list[dict],
    ts: str = TS_LATE,
    is_sidechain: bool = False,
) -> str:
    """One assistant JSONL line with fully-specified tool_use content blocks."""
    record = {
        'timestamp': ts,
        'type':      'assistant',
        'message':   {'id': mid, 'content': blocks},
    }
    if is_sidechain:
        record['isSidechain'] = True
    return json.dumps(record)


def _user_line(
    mid: str,
    tool_use_id: str,
    content: object,
    ts: str = TS_LATE,
    is_sidechain: bool = False,
) -> str:
    """One user JSONL line carrying a tool_result paired to tool_use_id."""
    record = {
        'timestamp': ts,
        'type':      'user',
        'message':   {
            'id':      mid,
            'content': [{
                'type':        'tool_result',
                'tool_use_id': tool_use_id,
                'content':     content,
            }],
        },
    }
    if is_sidechain:
        record['isSidechain'] = True
    return json.dumps(record)


def _read_use(mid_block_id: str, path: str = '/f') -> dict:
    return {'type': 'tool_use', 'id': mid_block_id, 'name': 'Read', 'input': {'file_path': path}}


def _edit_use(block_id: str, old: str, new: str, replace_all: bool = False) -> dict:
    inp = {'old_string': old, 'new_string': new}
    if replace_all:
        inp['replace_all'] = True
    return {'type': 'tool_use', 'id': block_id, 'name': 'Edit', 'input': inp}


def _write_use(block_id: str, content: str) -> dict:
    return {'type': 'tool_use', 'id': block_id, 'name': 'Write', 'input': {'content': content}}


def _designsync_use(block_id: str, method: str, path: str = '_ds/x/tokens/base.css') -> dict:
    return {
        'type':  'tool_use',
        'id':    block_id,
        'name':  'DesignSync',
        'input': {'method': method, 'projectId': 'p1', 'path': path},
    }


def _designsync_result(method: str, path: str, content: str | None) -> str:
    """JSON-encode the DesignSync tool_result content shape."""
    return json.dumps({'method': method, 'path': path, 'content': content})


def _notebook_edit_use(block_id: str) -> dict:
    return {
        'type':  'tool_use',
        'id':    block_id,
        'name':  'NotebookEdit',
        'input': {'new_source': 'a\nb\nc\n'},
    }


def _write_file(tmp_path: Path, name: str, lines: list[str]) -> str:
    p = tmp_path / name
    p.write_text('\n'.join(lines) + '\n')
    return str(p)


def _sub(jsonl_path: str) -> RunningSubagent:
    return RunningSubagent(
        agent_type      = 'Explore',
        description     = '',
        billed_in       = 0,
        output          = 0,
        first_timestamp = 0.0,
        jsonl_path      = jsonl_path,
    )


# --- 7.2: hand-written jsonl fixtures for each counting rule -----------------

def test_cat_n_result_counts_newlines_into_lines_read(tmp_path: Path) -> None:
    path = _write_file(tmp_path, 'main.jsonl', [
        _assistant_line('m1', [_read_use('r1')]),
        _user_line('u1', 'r1', '1\ta\n2\tb\n3\tc\n'),
    ])
    stats = count_transcript(path, None, skip_sidechain=True)
    assert stats.lines_read == 3


def test_image_result_list_content_contributes_zero(tmp_path: Path) -> None:
    path = _write_file(tmp_path, 'main.jsonl', [
        _assistant_line('m1', [_read_use('r1')]),
        _user_line('u1', 'r1', [{'type': 'image', 'source': {}}]),
    ])
    stats = count_transcript(path, None, skip_sidechain=True)
    assert stats.lines_read == 0


def test_result_not_cat_n_shaped_contributes_zero(tmp_path: Path) -> None:
    path = _write_file(tmp_path, 'main.jsonl', [
        _assistant_line('m1', [_read_use('r1')]),
        _user_line('u1', 'r1', 'not cat -n shaped\nsecond line\n'),
    ])
    stats = count_transcript(path, None, skip_sidechain=True)
    assert stats.lines_read == 0


def test_edit_takes_max_of_old_and_new_string_newlines(tmp_path: Path) -> None:
    path = _write_file(tmp_path, 'main.jsonl', [
        _assistant_line('m1', [
            _edit_use('e1', old='a\nb\nc\n', new='x\n'),  # old has 3 nl, new has 1
        ]),
    ])
    stats = count_transcript(path, None, skip_sidechain=True)
    assert stats.lines_changed == 3


def test_write_counts_newlines_of_whole_content(tmp_path: Path) -> None:
    path = _write_file(tmp_path, 'main.jsonl', [
        _assistant_line('m1', [_write_use('w1', content='a\nb\nc\nd\n')]),
    ])
    stats = count_transcript(path, None, skip_sidechain=True)
    assert stats.lines_changed == 4


def test_notebook_edit_not_counted(tmp_path: Path) -> None:
    """NotebookEdit contributes nothing to lines_changed and is excluded from counts."""
    path = _write_file(tmp_path, 'main.jsonl', [
        _assistant_line('m1', [_notebook_edit_use('n1')]),
    ])
    stats = count_transcript(path, None, skip_sidechain=True)
    assert stats.lines_changed == 0
    assert 'NotebookEdit' in stats.counts  # tool_use is still counted...
    # ...but contributes zero lines_changed regardless of its input shape.


def test_edit_replace_all_counts_once_documented_undercount(tmp_path: Path) -> None:
    """replace_all is counted once, not once per replaced occurrence.

    This is an accepted undercount (design.md Decision 3): the alternative
    requires re-reading the edited file to count occurrences, adding real I/O
    per Edit for a cosmetic gain.
    """
    path = _write_file(tmp_path, 'main.jsonl', [
        _assistant_line('m1', [
            _edit_use('e1', old='x\n', new='y\n', replace_all=True),
        ]),
    ])
    stats = count_transcript(path, None, skip_sidechain=True)
    # Regardless of how many times 'x' actually occurred in the real file,
    # the hunk size is counted exactly once (max(1, 1) == 1 here).
    assert stats.lines_changed == 1


# --- DesignSync get_file counts as a read; other methods do not --------------

def test_designsync_get_file_result_counts_newlines_into_lines_read(tmp_path: Path) -> None:
    file_text = '<!DOCTYPE html>\n<html>\n<body></body>\n</html>'  # 3 newlines, non-empty
    path = _write_file(tmp_path, 'main.jsonl', [
        _assistant_line('m1', [_designsync_use('d1', 'get_file')]),
        _user_line('u1', 'd1', _designsync_result('get_file', '_ds/x/tokens/base.css', file_text)),
    ])
    stats = count_transcript(path, None, skip_sidechain=True)
    assert stats.lines_read == file_text.count('\n') + 1
    assert stats.counts['DesignSync'] == 1


def test_designsync_list_files_not_counted_as_read(tmp_path: Path) -> None:
    path = _write_file(tmp_path, 'main.jsonl', [
        _assistant_line('m1', [_designsync_use('d1', 'list_files')]),
        _user_line('u1', 'd1', json.dumps({'method': 'list_files', 'files': ['a', 'b']})),
    ])
    stats = count_transcript(path, None, skip_sidechain=True)
    assert stats.lines_read == 0
    assert stats.counts['DesignSync'] == 1  # tool_use still counted, just not as a read


# --- 7.3: sidechain asymmetry -------------------------------------------------

def _sidechain_fixture(tmp_path: Path) -> str:
    # NOTE: no real session observed on this machine emits isSidechain: true
    # records (design.md Risks — the async Agent tool with per-subagent
    # agent-*.jsonl files is used instead of the synchronous inline-sidechain
    # Task convention). This hand-written fixture is the only defence for the
    # skip_sidechain asymmetry in count_transcript.
    return _write_file(tmp_path, 'main.jsonl', [
        _assistant_line('side1', [_read_use('sr1')], is_sidechain=True),
        _user_line('side1u', 'sr1', '1\tside a\n2\tside b\n', is_sidechain=True),
        _assistant_line('m1', [_edit_use('e1', old='a\n', new='b\nc\n')]),
        _assistant_line('m2', [_write_use('w1', content='x\ny\n')]),
    ])


def test_sidechain_records_excluded_when_skip_sidechain_true(tmp_path: Path) -> None:
    path = _sidechain_fixture(tmp_path)
    stats = count_transcript(path, None, skip_sidechain=True)
    assert 'Read' not in stats.counts
    assert stats.lines_read == 0
    assert stats.counts['Edit'] == 1
    assert stats.counts['Write'] == 1
    assert stats.lines_changed == 2 + 2  # Edit max(1,2)=2, Write nl(content)=2


def test_sidechain_records_counted_in_full_when_skip_sidechain_false(tmp_path: Path) -> None:
    path = _sidechain_fixture(tmp_path)
    stats = count_transcript(path, None, skip_sidechain=False)
    assert stats.counts['Read'] == 1
    assert stats.lines_read == 2
    assert stats.counts['Edit'] == 1
    assert stats.counts['Write'] == 1
    assert stats.lines_changed == 2 + 2


# --- 7.4: clear_epoch window applies to line counts too ----------------------

def test_clear_epoch_zeroes_lines_before_boundary(tmp_path: Path) -> None:
    path = _write_file(tmp_path, 'main.jsonl', [
        _assistant_line('m1', [_read_use('r1')], ts=TS_EARLY),
        _user_line('u1', 'r1', '1\ta\n2\tb\n', ts=TS_EARLY),
        _assistant_line('m2', [_edit_use('e1', old='a\n', new='b\nc\nd\n')], ts=TS_EARLY),
        _assistant_line('m3', [_read_use('r2')], ts=TS_LATE),
        _user_line('u2', 'r2', '1\tx\n2\ty\n3\tz\n', ts=TS_LATE),
        _assistant_line('m4', [_write_use('w1', content='p\nq\n')], ts=TS_LATE),
    ])
    # clear_epoch=None counts the whole transcript.
    stats_all = count_transcript(path, None, skip_sidechain=True)
    assert stats_all.lines_read == 2 + 3
    assert stats_all.lines_changed == 3 + 2

    # A clear epoch between the two windows excludes the early records
    # entirely — no contribution to counts, lines_read, or lines_changed.
    boundary = (_parse_iso_to_epoch(TS_EARLY) + _parse_iso_to_epoch(TS_LATE)) / 2
    stats_after = count_transcript(path, boundary, skip_sidechain=True)
    assert 'Edit' not in stats_after.counts
    assert stats_after.lines_read == 3
    assert stats_after.lines_changed == 2


# --- 7.5: session-total invariant across ToolCounts.gather -------------------

def test_gather_lines_totals_equal_main_plus_sum_of_subagents(tmp_path: Path) -> None:
    main_path = _write_file(tmp_path, 'main.jsonl', [
        _assistant_line('m1', [_read_use('r1')]),
        _user_line('u1', 'r1', '1\ta\n2\tb\n'),
        _assistant_line('m2', [_write_use('w1', content='x\ny\nz\n')]),
    ])
    sub1_path = _write_file(tmp_path, 'a1.jsonl', [
        _assistant_line('s1', [_read_use('sr1')]),
        _user_line('su1', 'sr1', '1\ta\n2\tb\n3\tc\n4\td\n'),
    ])
    sub2_path = _write_file(tmp_path, 'a2.jsonl', [
        _assistant_line('s2', [_edit_use('se1', old='a\n', new='b\nc\n')]),
    ])

    main_stats = count_transcript(main_path, None, skip_sidechain=True)
    sub1_stats = count_transcript(sub1_path, None, skip_sidechain=False)
    sub2_stats = count_transcript(sub2_path, None, skip_sidechain=False)

    tc = ToolCounts.gather(main_path, [_sub(sub1_path), _sub(sub2_path)], None)

    assert tc.lines_read == main_stats.lines_read + sub1_stats.lines_read + sub2_stats.lines_read
    assert (
        tc.lines_changed
        == main_stats.lines_changed + sub1_stats.lines_changed + sub2_stats.lines_changed
    )
    # Per-agent breakdown matches each subagent's own (self-scoped) stats.
    assert tc.per_agent[sub1_path] == (sub1_stats.lines_read, sub1_stats.lines_changed)
    assert tc.per_agent[sub2_path] == (sub2_stats.lines_read, sub2_stats.lines_changed)


# --- 7.6: pre-filter (V2 byte-level) vs naive full-decode equivalence --------

def _naive_count_transcript(path: str, clear_epoch: float | None, *, skip_sidechain: bool):
    """Reference walk: decode every line with no byte-level pre-filtering.

    Applies the exact same tool_use/tool_result semantics as count_transcript
    but skips the Decision 6 pre-filters entirely, to defend against the real
    implementation silently diverging from these semantics.
    """
    def _nl(s: object) -> int:
        return s.count('\n') if isinstance(s, str) else 0

    per_id: dict[str, list[str]] = {}
    per_id_changed: dict[str, int] = {}
    read_ids: set[str] = set()
    lines_read = 0

    with open(path, encoding='utf-8') as fh:
        for raw in fh:
            try:
                d = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if skip_sidechain and d.get('isSidechain') is True:
                continue
            msg = d.get('message') or {}
            mid = msg.get('id')
            if not mid:
                continue
            if clear_epoch is not None:
                ts = d.get('timestamp', '') or ''
                if _parse_iso_to_epoch(ts) < clear_epoch:
                    continue

            names: list[str] = []
            id_changed = 0
            for block in msg.get('content') or []:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'tool_use':
                    name = block.get('name') or ''
                    if not name:
                        continue
                    name = name.split('__')[-1]
                    if name in META_EXCLUDE_TOOLS:
                        continue
                    names.append(name)
                    if name == 'Read':
                        block_id = block.get('id')
                        if block_id:
                            read_ids.add(block_id)
                    elif name == 'Edit':
                        inp = block.get('input') or {}
                        id_changed += max(_nl(inp.get('old_string')), _nl(inp.get('new_string')))
                    elif name == 'Write':
                        inp = block.get('input') or {}
                        id_changed += _nl(inp.get('content'))

            for block in msg.get('content') or []:
                if not isinstance(block, dict) or block.get('type') != 'tool_result':
                    continue
                tool_use_id = block.get('tool_use_id')
                if tool_use_id not in read_ids:
                    continue
                content = block.get('content')
                if isinstance(content, str) and content.startswith('1\t'):
                    lines_read += content.count('\n')

            per_id[mid] = names
            per_id_changed[mid] = id_changed

    counts: dict[str, int] = {}
    for names in per_id.values():
        for name in names:
            counts[name] = counts.get(name, 0) + 1
    return counts, lines_read, sum(per_id_changed.values())


def test_prefiltered_walk_matches_naive_full_decode_reference(tmp_path: Path) -> None:
    path = _write_file(tmp_path, 'main.jsonl', [
        _assistant_line('side1', [_read_use('sr1')], is_sidechain=True),
        _user_line('side1u', 'sr1', '1\tside a\n2\tside b\n', is_sidechain=True),
        _assistant_line('m1', [_read_use('r1')]),
        _user_line('u1', 'r1', '1\ta\n2\tb\n3\tc\n'),
        _assistant_line('m2', [_edit_use('e1', old='a\nb\n', new='c\n')]),
        _assistant_line('m3', [_write_use('w1', content='x\ny\nz\nq\n')]),
        _assistant_line('m4', [_notebook_edit_use('n1')]),
        _assistant_line('m5', [_read_use('r2')]),
        _user_line('u2', 'r2', [{'type': 'image', 'source': {}}]),
        _assistant_line('m6', [_read_use('r3')]),
        _user_line('u3', 'r3', 'not cat -n shaped\n'),
    ])

    for skip in (True, False):
        real = count_transcript(path, None, skip_sidechain=skip)
        naive_counts, naive_lines_read, naive_lines_changed = _naive_count_transcript(
            path, None, skip_sidechain=skip,
        )
        assert real.counts == naive_counts
        assert real.lines_read == naive_lines_read
        assert real.lines_changed == naive_lines_changed
