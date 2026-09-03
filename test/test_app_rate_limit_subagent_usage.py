"""Tests that the rate-limit-sim call site sums main + subagent transcript
usage (TranscriptUsage.from_session), not main-only (TranscriptUsage.
from_transcript). Companion to test_app_rate_limit_sim.py, kept separate so
each file has a single writer during concurrent branch work."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import yas.app as app
from yas.constants import rate_limit_log, version_file
from yas.rate_limits_sim import reset_rate_limit_cache


def _example_info() -> dict:
    example = Path(__file__).resolve().parent.parent / 'ops' / 'session-info-example.json'
    return json.loads(example.read_text())


def _write_transcript(path: Path, samples: list[tuple[str, int, int, int, int]], sidechain: bool = False) -> None:
    """Each sample is (message_id, input, cache_creation, cache_read, output)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for mid, i, cc, cr, o in samples:
        lines.append(json.dumps({
            'type': 'assistant',
            'isSidechain': sidechain,
            'message': {
                'id': mid,
                'usage': {
                    'input_tokens': i,
                    'cache_creation_input_tokens': cc,
                    'cache_read_input_tokens': cr,
                    'output_tokens': o,
                },
            },
            'timestamp': '2024-01-01T00:00:00Z',
        }))
    path.write_text('\n'.join(lines) + '\n')


def _rate_limit_log_lines(tmp_home: Path) -> list[str]:
    log = rate_limit_log()
    if not log.exists():
        return []
    return [ln for ln in log.read_text().splitlines() if ln]


def _rig(tmp_home: Path, monkeypatch, transcript_path: Path) -> dict:
    vf = version_file()
    vf.parent.mkdir(parents=True, exist_ok=True)
    vf.write_text(json.dumps({'schema_version': 1, 'yas_version': '0.0.0', 'migrated_at': 0}))
    monkeypatch.setattr(app, 'terminal_width', lambda: 200)
    monkeypatch.setattr(app.sys, 'stdout', io.StringIO())

    info = _example_info()
    info['transcript_path'] = str(transcript_path)
    info['context_window'] = {'total_input_tokens': 999_999_999, 'total_output_tokens': 999_999_999}
    monkeypatch.setattr(app.sys, 'stdin', io.StringIO(json.dumps(info)))

    claude_dir = tmp_home / '.claude'
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / 'yas.toml').write_text(
        '[rate_limits]\n'
        'five_hour = { budget = 1_000_000_000, window = "5h", anchor = "rolling" }\n'
    )
    monkeypatch.setattr(sys, 'argv', ['statusline_command.py'])
    reset_rate_limit_cache()
    return info


def test_rate_limit_log_records_main_plus_subagent_totals(tmp_home: Path, monkeypatch) -> None:
    """The value RateLimitLog.record sees is main + every subagent file, not main-only."""
    transcript = tmp_home / 'sess.jsonl'
    _write_transcript(transcript, [('m1', 100, 20, 5, 50)])

    subdir = tmp_home / 'sess' / 'subagents'
    subdir.mkdir(parents=True)
    _write_transcript(subdir / 'agent-a1.jsonl', [('s1', 10, 2, 1, 5)], sidechain=True)
    _write_transcript(subdir / 'agent-a2.jsonl', [('s2', 20, 4, 2, 10)], sidechain=True)

    _rig(tmp_home, monkeypatch, transcript)
    app.main()

    lines = _rate_limit_log_lines(tmp_home)
    assert len(lines) == 1
    parts = lines[0].split()
    # main (100,20,5,50) + sub-a1 (10,2,1,5) + sub-a2 (20,4,2,10) = 130,26,8,65
    assert parts[2:] == ['130', '26', '8', '65']


def test_rate_limit_log_is_main_only_when_no_subagents_dir(tmp_home: Path, monkeypatch) -> None:
    """No subagents/ sibling dir -> identical to today's main-only behaviour."""
    transcript = tmp_home / 'sess.jsonl'
    _write_transcript(transcript, [('m1', 100, 20, 5, 50)])

    _rig(tmp_home, monkeypatch, transcript)
    app.main()

    lines = _rate_limit_log_lines(tmp_home)
    assert lines[0].split()[2:] == ['100', '20', '5', '50']
