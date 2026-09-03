"""Tests for RateLimitLog.record: dedupe-on-write, first-sample-always-written,
and pruning-only-on-write (yas.tokens)."""
from __future__ import annotations

from pathlib import Path

from yas.constants import rate_limit_log
from yas.tokens import RateLimitLog

KEEP = 6 * 3600


def _lines(tmp_home: Path) -> list[str]:
    log = rate_limit_log()
    if not log.exists():
        return []
    return [ln for ln in log.read_text().splitlines() if ln]


def test_first_sample_for_a_session_is_always_written(tmp_home: Path) -> None:
    RateLimitLog.record('sess-a', 100, 20, 5, 50, keep_seconds=KEEP, now=100.0)
    assert _lines(tmp_home) == ['100.000 sess-a 100 20 5 50']


def test_first_sample_is_written_even_when_all_components_are_zero(tmp_home: Path) -> None:
    RateLimitLog.record('sess-a', 0, 0, 0, 0, keep_seconds=KEEP, now=100.0)
    assert _lines(tmp_home) == ['100.000 sess-a 0 0 0 0']


def test_unchanged_value_writes_nothing(tmp_home: Path) -> None:
    RateLimitLog.record('sess-a', 100, 20, 5, 50, keep_seconds=KEEP, now=100.0)
    RateLimitLog.record('sess-a', 100, 20, 5, 50, keep_seconds=KEEP, now=200.0)
    # Still just the one line from the first (changed) tick.
    assert _lines(tmp_home) == ['100.000 sess-a 100 20 5 50']


def test_changed_value_appends_a_new_line(tmp_home: Path) -> None:
    RateLimitLog.record('sess-a', 100, 20, 5, 50, keep_seconds=KEEP, now=100.0)
    RateLimitLog.record('sess-a', 150, 20, 5, 50, keep_seconds=KEEP, now=200.0)
    assert _lines(tmp_home) == ['100.000 sess-a 100 20 5 50', '200.000 sess-a 150 20 5 50']


def test_a_single_changed_component_is_enough_to_write(tmp_home: Path) -> None:
    # Only cache_read moves; input/cache_creation/output are unchanged --
    # still counts as a real change, not a heartbeat.
    RateLimitLog.record('sess-a', 100, 20, 5, 50, keep_seconds=KEEP, now=100.0)
    RateLimitLog.record('sess-a', 100, 20, 9, 50, keep_seconds=KEEP, now=200.0)
    assert _lines(tmp_home) == ['100.000 sess-a 100 20 5 50', '200.000 sess-a 100 20 9 50']


def test_decreasing_counter_is_still_recorded(tmp_home: Path) -> None:
    # A reset/truncated running total is a real change, not a heartbeat --
    # it must be written even though a value went down (e.g. /compact).
    RateLimitLog.record('sess-a', 5000, 100, 50, 200, keep_seconds=KEEP, now=100.0)
    RateLimitLog.record('sess-a', 200, 10, 5, 20, keep_seconds=KEEP, now=200.0)
    assert _lines(tmp_home) == ['100.000 sess-a 5000 100 50 200', '200.000 sess-a 200 10 5 20']


def test_return_value_reflects_final_state_whether_or_not_it_wrote(tmp_home: Path) -> None:
    by_session = RateLimitLog.record('sess-a', 100, 20, 5, 50, keep_seconds=KEEP, now=100.0)
    assert by_session == {'sess-a': [(100.0, 100, 20, 5, 50)]}
    # Unchanged tick: still returns the current (unwritten-to) state.
    by_session = RateLimitLog.record('sess-a', 100, 20, 5, 50, keep_seconds=KEEP, now=200.0)
    assert by_session == {'sess-a': [(100.0, 100, 20, 5, 50)]}


def test_pruning_applies_only_on_a_tick_that_writes(tmp_home: Path) -> None:
    RateLimitLog.record('sess-a', 100, 0, 0, 0, keep_seconds=100, now=0.0)
    # This tick changes the value and is well past the 100s retention window
    # for the first sample -- the write must prune it.
    RateLimitLog.record('sess-a', 200, 0, 0, 0, keep_seconds=100, now=500.0)
    assert _lines(tmp_home) == ['500.000 sess-a 200 0 0 0']


def test_other_sessions_are_pruned_too_on_a_writing_tick(tmp_home: Path) -> None:
    RateLimitLog.record('sess-a', 100, 0, 0, 0, keep_seconds=100, now=0.0)
    RateLimitLog.record('sess-b', 200, 0, 0, 0, keep_seconds=100, now=10.0)
    # sess-b's change at t=500 triggers a write that prunes both sessions'
    # stale entries -- sess-a's t=0 sample is outside the 100s window.
    RateLimitLog.record('sess-b', 300, 0, 0, 0, keep_seconds=100, now=500.0)
    assert _lines(tmp_home) == ['500.000 sess-b 300 0 0 0']


def test_empty_session_id_is_a_no_op(tmp_home: Path) -> None:
    RateLimitLog.record('', 100, 0, 0, 0, keep_seconds=KEEP, now=100.0)
    assert _lines(tmp_home) == []


def test_dedupe_does_not_change_usage_since_versus_a_heartbeat_full_log(tmp_home: Path) -> None:
    # Dedupe: only the real changes land on disk.
    RateLimitLog.record('sess-a', 1000, 0, 0, 0, keep_seconds=KEEP, now=0.0)
    for t in range(50, 250, 50):
        RateLimitLog.record('sess-a', 1000, 0, 0, 0, keep_seconds=KEEP, now=float(t))  # idle heartbeats, skipped
    RateLimitLog.record('sess-a', 4000, 0, 0, 0, keep_seconds=KEEP, now=300.0)
    deduped_usage = RateLimitLog.usage_since(window_start=100.0)

    # Same story, but every tick (including the idle heartbeats) is written
    # directly -- the pre-dedupe behaviour.
    log = rate_limit_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_rows = ['0.000 sess-a 1000 0 0 0']
    heartbeat_rows += [f'{t}.000 sess-a 1000 0 0 0' for t in range(50, 250, 50)]
    heartbeat_rows.append('300.000 sess-a 4000 0 0 0')
    log.write_text('\n'.join(heartbeat_rows) + '\n')
    heartbeat_usage = RateLimitLog.usage_since(window_start=100.0)

    # input weight is 1.0 -> the weighted total equals the raw delta.
    assert deduped_usage == heartbeat_usage == 3000  # 4000 - 1000 baseline
