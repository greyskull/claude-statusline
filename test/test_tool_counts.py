"""Tests for the per-tool tool_use counting aggregator (yas.info.toolcounts)."""

import json
from pathlib import Path

from yas.info.subagents import RunningSubagent, _parse_iso_to_epoch
from yas.info.toolcounts import ToolCounts, count_transcript

TS_EARLY = '2026-01-01T00:00:00Z'
TS_LATE  = '2026-01-01T12:00:00Z'


def _line(mid: str, tools: list[str], ts: str = TS_LATE) -> str:
    """One assistant JSONL line with the given message id and tool_use names."""
    return json.dumps({
        'timestamp': ts,
        'type':      'assistant',
        'message':   {
            'id':      mid,
            'content': [{'type': 'tool_use', 'name': t, 'input': {}} for t in tools],
        },
    })


def _write(tmp_path: Path, name: str, lines: list[str]) -> str:
    p = tmp_path / name
    p.write_text('\n'.join(lines) + '\n')
    return str(p)


def test_per_tool_counting(tmp_path: Path) -> None:
    path = _write(tmp_path, 'main.jsonl', [
        _line('m1', ['Bash']),
        _line('m2', ['Read', 'Read']),
        _line('m3', ['Edit']),
    ])
    result = count_transcript(path, None, skip_sidechain=True)
    assert result.counts == {'Bash': 1, 'Read': 2, 'Edit': 1}


def test_dedup_keeps_last_write(tmp_path: Path) -> None:
    """A later, fuller write for the same message.id supersedes the partial."""
    path = _write(tmp_path, 'main.jsonl', [
        _line('m1', ['Bash']),            # early partial: 1 block
        _line('m1', ['Bash', 'Read']),    # final write: 2 blocks
    ])
    result = count_transcript(path, None, skip_sidechain=True)
    assert result.counts == {'Bash': 1, 'Read': 1}


def test_repeated_identical_final_write_not_double_counted(tmp_path: Path) -> None:
    path = _write(tmp_path, 'main.jsonl', [
        _line('m1', ['Bash', 'Read']),
        _line('m1', ['Bash', 'Read']),
    ])
    result = count_transcript(path, None, skip_sidechain=True)
    assert result.counts == {'Bash': 1, 'Read': 1}


def test_clear_epoch_excludes_before_and_none_counts_all(tmp_path: Path) -> None:
    path = _write(tmp_path, 'main.jsonl', [
        _line('m1', ['Bash'], ts=TS_EARLY),
        _line('m2', ['Read'], ts=TS_LATE),
    ])
    # None counts the whole transcript.
    assert count_transcript(path, None, skip_sidechain=True).counts == {'Bash': 1, 'Read': 1}
    # A clear epoch between the two excludes the early Bash line.
    boundary = (_parse_iso_to_epoch(TS_EARLY) + _parse_iso_to_epoch(TS_LATE)) / 2
    assert count_transcript(path, boundary, skip_sidechain=True).counts == {'Read': 1}


def test_meta_excluded_task_kept(tmp_path: Path) -> None:
    path = _write(tmp_path, 'main.jsonl', [
        _line('m1', ['TodoWrite']),
        _line('m2', ['ExitPlanMode']),
        _line('m3', ['AskUserQuestion']),
        _line('m4', ['Task']),
        _line('m5', ['Bash', 'TodoWrite']),
    ])
    counts = count_transcript(path, None, skip_sidechain=True).counts
    assert 'TodoWrite' not in counts
    assert 'ExitPlanMode' not in counts
    assert 'AskUserQuestion' not in counts
    assert counts['Task'] == 1
    assert counts['Bash'] == 1


def test_mcp_name_normalized_to_last_segment(tmp_path: Path) -> None:
    path = _write(tmp_path, 'main.jsonl', [
        _line('m1', ['mcp__github__create_issue']),
        _line('m2', ['mcp__github__create_issue']),
    ])
    result = count_transcript(path, None, skip_sidechain=True)
    assert result.counts == {'create_issue': 2}


def test_missing_message_id_skipped(tmp_path: Path) -> None:
    """A tool_use line with no message.id contributes nothing."""
    line = json.dumps({
        'timestamp': TS_LATE,
        'message':   {'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {}}]},
    })
    path = _write(tmp_path, 'main.jsonl', [line, _line('m1', ['Read'])])
    result = count_transcript(path, None, skip_sidechain=True)
    assert result.counts == {'Read': 1}


def test_unreadable_path_returns_empty() -> None:
    assert count_transcript('', None, skip_sidechain=True).counts == {}
    assert count_transcript('/no/such/file.jsonl', None, skip_sidechain=True).counts == {}


def _read_use_line(mid: str, tool_use_id: str, ts: str = TS_LATE) -> str:
    """An assistant-role JSONL line containing a single Read tool_use block."""
    return json.dumps({
        'timestamp': ts,
        'type':      'assistant',
        'message':   {
            'id':      mid,
            'content': [
                {'type': 'tool_use', 'id': tool_use_id, 'name': 'Read', 'input': {}},
            ],
        },
    })


def _read_result_line(tool_use_id: str, content: str, ts: str = TS_LATE) -> str:
    """A user-role JSONL line with a tool_result — the REAL shape: no message.id.

    Real Claude Code transcripts never put `message.id` on user-role records,
    including the tool_result records paired against a preceding tool_use.
    """
    return json.dumps({
        'timestamp': ts,
        'type':      'user',
        'message':   {
            'role':    'user',
            'content': [
                {'type': 'tool_result', 'tool_use_id': tool_use_id, 'content': content},
            ],
        },
    })


def test_lines_read_counted_from_real_shaped_tool_result(tmp_path: Path) -> None:
    """A user-role tool_result with no message.id must still count lines_read.

    Regression test: prior to the fix, the `mid` guard rejected every
    tool_result record (since real transcripts never set message.id on
    user-role messages), so lines_read was unconditionally 0.
    """
    path = _write(tmp_path, 'main.jsonl', [
        _read_use_line('m1', 'toolu_1'),
        _read_result_line('toolu_1', '1\tfirst line\n2\tsecond line\n3\tthird line'),
    ])
    result = count_transcript(path, None, skip_sidechain=True)
    assert result.counts == {'Read': 1}
    assert result.lines_read == 2  # 2 newlines in the 3-line cat -n payload


def test_lines_read_ignores_non_cat_n_content(tmp_path: Path) -> None:
    """Image/document tool_result content (not starting with '1\\t') is skipped."""
    path = _write(tmp_path, 'main.jsonl', [
        _read_use_line('m1', 'toolu_1'),
        _read_result_line('toolu_1', 'not a cat -n payload\nline2'),
    ])
    result = count_transcript(path, None, skip_sidechain=True)
    assert result.lines_read == 0


def test_lines_read_dedups_by_tool_use_id(tmp_path: Path) -> None:
    """A retransmitted tool_result for the same tool_use_id doesn't double-count."""
    path = _write(tmp_path, 'main.jsonl', [
        _read_use_line('m1', 'toolu_1'),
        _read_result_line('toolu_1', '1\ta\n2\tb'),
        _read_result_line('toolu_1', '1\ta\n2\tb'),  # duplicate/retransmit
    ])
    result = count_transcript(path, None, skip_sidechain=True)
    assert result.lines_read == 1


def test_v2_prefilter_does_not_reject_genuine_cat_n_payload(tmp_path: Path) -> None:
    """The binary pre-filter (Decision 6) must let a real '1\\t...' payload through."""
    path = _write(tmp_path, 'main.jsonl', [
        _read_use_line('m1', 'toolu_1'),
        _read_result_line('toolu_1', '1\tsome content here\n2\tmore content'),
    ])
    result = count_transcript(path, None, skip_sidechain=True)
    assert result.lines_read == 1


def test_lines_read_summed_in_per_agent_breakdown(tmp_path: Path) -> None:
    """ToolCounts.per_agent must include lines_read from real-shaped tool_results."""
    main = _write(tmp_path, 'main.jsonl', [_line('m1', ['Bash'])])
    sub1 = _write(tmp_path, 'a1.jsonl', [
        _read_use_line('s1', 'toolu_sub1'),
        _read_result_line('toolu_sub1', '1\tone\n2\ttwo\n3\tthree'),
    ])
    tc = ToolCounts.gather(main, [_sub(sub1)], None)
    assert tc.per_agent[sub1] == (2, 0)
    assert tc.lines_read == 2


def _sub(jsonl_path: str) -> RunningSubagent:
    return RunningSubagent(
        agent_type      = 'Explore',
        description     = '',
        billed_in       = 0,
        output          = 0,
        first_timestamp = 0.0,
        jsonl_path      = jsonl_path,
    )


def test_gather_main_vs_sub_summed_across_subagents(tmp_path: Path) -> None:
    main = _write(tmp_path, 'main.jsonl', [
        _line('m1', ['Edit', 'Edit', 'Edit']),
    ])
    sub1 = _write(tmp_path, 'a1.jsonl', [_line('s1', ['Grep'] * 6)])
    sub2 = _write(tmp_path, 'a2.jsonl', [_line('s2', ['Grep'] * 9)])

    tc = ToolCounts.gather(main, [_sub(sub1), _sub(sub2)], None)
    assert tc.counts['Edit'] == (3, 0)
    assert tc.counts['Grep'] == (0, 15)
    assert tc.total_types == 2


def test_gather_zero_fills_both_columns(tmp_path: Path) -> None:
    main = _write(tmp_path, 'main.jsonl', [_line('m1', ['Bash'])])
    sub1 = _write(tmp_path, 'a1.jsonl', [_line('s1', ['Bash', 'Read'])])
    tc = ToolCounts.gather(main, [_sub(sub1)], None)
    assert tc.counts['Bash'] == (1, 1)
    assert tc.counts['Read'] == (0, 1)


def test_gather_empty_when_nothing_counted(tmp_path: Path) -> None:
    main = _write(tmp_path, 'main.jsonl', [_line('m1', ['TodoWrite'])])
    tc = ToolCounts.gather(main, [], None)
    assert tc.counts == {}
    assert tc.total_types == 0
