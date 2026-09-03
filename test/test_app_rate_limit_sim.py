"""Tests for yas.app's rate-limit-sim wiring: the signal fed to
`simulate_rate_limits` comes from the transcript's lifetime usage totals
(TranscriptUsage), not the raw payload's context_window gauge, and `main`
parses the transcript at most once per render even with rate_limit_rules
configured."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import yas.app as app
from yas.constants import rate_limit_log, version_file
from yas.info.transcript import TranscriptUsage
from yas.rate_limits_sim import reset_rate_limit_cache


def _example_info() -> dict:
    example = Path(__file__).resolve().parent.parent / 'ops' / 'session-info-example.json'
    return json.loads(example.read_text())


def _write_transcript(path: Path, samples: list[tuple[str, int, int, int, int]]) -> None:
    """Each sample is (message_id, input, cache_creation, cache_read, output)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for mid, i, cc, cr, o in samples:
        lines.append(json.dumps({
            'type': 'assistant',
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


def _rig(tmp_home: Path, monkeypatch, transcript_path: Path | None) -> dict:
    """Version-file guard prevents a migrate() import mid-test; terminal_width
    is patched wide enough to reach build_wide (record_tick) so nothing about
    the rate-limit path is short-circuited by a narrow layout."""
    vf = version_file()
    vf.parent.mkdir(parents=True, exist_ok=True)
    vf.write_text(json.dumps({'schema_version': 1, 'yas_version': '0.0.0', 'migrated_at': 0}))
    monkeypatch.setattr(app, 'terminal_width', lambda: 200)
    monkeypatch.setattr(app.sys, 'stdout', io.StringIO())

    info = _example_info()
    if transcript_path is not None:
        info['transcript_path'] = str(transcript_path)
    else:
        info.pop('transcript_path', None)
    # A context_window gauge deliberately far from the transcript sums below,
    # so a test failing back onto the old signal is caught by a wrong number
    # rather than a coincidental match.
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


def test_rate_limit_sim_uses_transcript_totals_not_context_window_gauge(tmp_home: Path, monkeypatch) -> None:
    transcript = tmp_home / 'transcript.jsonl'
    _write_transcript(transcript, [('msg-1', 100, 20, 5, 50)])
    _rig(tmp_home, monkeypatch, transcript)

    app.main()

    lines = _rate_limit_log_lines(tmp_home)
    assert len(lines) == 1
    # ts session_id input cache_creation cache_read output -- must reflect
    # the transcript's sums (100 20 5 50), never the huge context_window
    # gauge values injected by _rig.
    parts = lines[0].split()
    assert parts[2:] == ['100', '20', '5', '50']


def test_rate_limit_sim_falls_back_to_zeros_when_transcript_is_missing(tmp_home: Path, monkeypatch) -> None:
    missing = tmp_home / 'does-not-exist.jsonl'
    _rig(tmp_home, monkeypatch, missing)

    app.main()  # must not raise

    lines = _rate_limit_log_lines(tmp_home)
    assert len(lines) == 1
    assert lines[0].split()[2:] == ['0', '0', '0', '0']


def test_missing_transcript_fallback_does_not_corrupt_an_existing_baseline(tmp_home: Path, monkeypatch) -> None:
    transcript = tmp_home / 'transcript.jsonl'
    _write_transcript(transcript, [('msg-1', 100, 0, 0, 0)])
    info = _rig(tmp_home, monkeypatch, transcript)
    app.main()
    first_lines = _rate_limit_log_lines(tmp_home)
    assert first_lines[0].split()[2:] == ['100', '0', '0', '0']

    # Second render for the SAME session, but now the transcript is gone.
    # The fallback-to-zero tick must not silently overwrite/erase the real
    # baseline already on disk -- it's a genuine change (100 -> 0), so it's
    # recorded as a new sample, not a corruption of the first one.
    reset_rate_limit_cache()
    monkeypatch.setattr(app.sys, 'stdin', io.StringIO(json.dumps({**info, 'transcript_path': str(tmp_home / 'gone.jsonl')})))
    app.main()

    lines = _rate_limit_log_lines(tmp_home)
    assert lines[0].split()[2:] == ['100', '0', '0', '0']  # original baseline intact
    assert len(lines) == 2


def test_render_and_rate_limit_sim_parse_the_transcript_exactly_once(tmp_home: Path, monkeypatch) -> None:
    transcript = tmp_home / 'transcript.jsonl'
    _write_transcript(transcript, [('msg-1', 100, 20, 5, 50)])
    _rig(tmp_home, monkeypatch, transcript)

    calls = []
    real_from_transcript = TranscriptUsage.from_transcript

    def _counting(path: str) -> TranscriptUsage:
        calls.append(path)
        return real_from_transcript(path)

    monkeypatch.setattr(TranscriptUsage, 'from_transcript', staticmethod(_counting))

    app.main()

    assert calls == [str(transcript)]  # exactly one parse for the whole render


def test_rate_limit_sim_result_reaches_the_rendered_output_not_just_the_payload(tmp_home: Path, monkeypatch) -> None:
    """Regression for the ordering bug where `_apply_rate_limit_sim` mutated
    only `info` (used for the persisted payload) while `render` drew from a
    `SessionInfo` snapshotted before the mutation -- so with all-zero real
    buckets in the raw payload, the renderer saw used_percentage=0/resets_at=0
    and drew GLYPH_UNLIMITED ('inf') on both rows instead of a percentage,
    even though rate_limit_rules were configured precisely to avoid that."""
    from yas.constants import GLYPH_UNLIMITED

    transcript = tmp_home / 'transcript.jsonl'
    _write_transcript(transcript, [('msg-1', 100_000, 0, 0, 0)])
    info = _rig(tmp_home, monkeypatch, transcript)
    # _rig's yas.toml only configures five_hour; add seven_day too, since an
    # unconfigured bucket legitimately renders GLYPH_UNLIMITED and would
    # otherwise mask the bug this test targets.
    (tmp_home / '.claude' / 'yas.toml').write_text(
        '[rate_limits]\n'
        'five_hour = { budget = 1_000_000_000, window = "5h", anchor = "rolling" }\n'
        'seven_day = { budget = 1_000_000_000, window = "7d", anchor = "rolling" }\n'
    )
    # The raw payload's rate_limits are all-zero, exactly the shape that
    # falsely reads as "unlimited" if the renderer sees pre-synthesis data.
    info['rate_limits'] = {
        'five_hour': {'used_percentage': 0, 'resets_at': 0},
        'seven_day': {'used_percentage': 0, 'resets_at': 0},
    }
    monkeypatch.setattr(app.sys, 'stdin', io.StringIO(json.dumps(info)))

    app.main()

    rendered = app.sys.stdout.getvalue()
    assert GLYPH_UNLIMITED not in rendered


def test_rate_limit_sim_result_is_written_into_the_persisted_session_payload(tmp_home: Path, monkeypatch) -> None:
    from yas.constants import session_payload_path

    transcript = tmp_home / 'transcript.jsonl'
    _write_transcript(transcript, [('msg-1', 1000, 0, 0, 0)])
    info = _rig(tmp_home, monkeypatch, transcript)

    app.main()

    persisted = json.loads(session_payload_path(info['session_id']).read_text())
    # Synthesised (real payload had 61%, see ops/session-info-example.json)
    # -- the tiny 1000/1_000_000_000 budget rounds to 0.0 at 2dp, but it must
    # not be 61.
    assert persisted['rate_limits']['five_hour']['used_percentage'] != 61
    assert persisted['rate_limits']['five_hour']['used_percentage'] == 0.0
