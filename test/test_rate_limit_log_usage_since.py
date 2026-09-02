"""Tests for RateLimitLog.usage_since: cross-session summation with a
per-session pre-window baseline (yas.tokens)."""
from __future__ import annotations

from pathlib import Path

from yas.constants import rate_limit_log
from yas.tokens import RateLimitLog


def _write(tmp_home: Path, *rows: str) -> None:
    log = rate_limit_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('\n'.join(rows) + '\n')


def test_two_sessions_in_window_deltas_are_summed(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '0 sess-a 1000',    # pre-window baseline
        '200 sess-a 4000',  # in-window
        '0 sess-b 500',     # pre-window baseline
        '200 sess-b 2500',  # in-window
    )
    # sess-a: 4000 - 1000 = 3000, sess-b: 2500 - 500 = 2000 -> 5000 total.
    assert RateLimitLog.usage_since(window_start=50) == 5000


def test_session_starting_inside_window_counts_first_sample_in_full(tmp_home: Path) -> None:
    _write(tmp_home, '200 sess-new 45572')
    # No pre-window sample for sess-new -> baseline is 0, full value counts.
    assert RateLimitLog.usage_since(window_start=100) == 45572


def test_pre_window_sample_is_diffed_not_first_in_window_sample(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '50 sess-a 900',    # pre-window baseline
        '150 sess-a 1200',  # first in-window sample
        '250 sess-a 1500',  # latest sample
    )
    # Baseline must be the pre-window sample (900), not the first in-window
    # sample (1200) -- 1500 - 900 = 600, not 1500 - 1200 = 300.
    assert RateLimitLog.usage_since(window_start=100) == 600


def test_session_with_no_in_window_samples_contributes_zero(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '10 sess-stale 100',
        '20 sess-stale 200',
    )
    assert RateLimitLog.usage_since(window_start=1000) == 0


def test_malformed_lines_are_skipped(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '0 sess-a 1000',    # pre-window baseline
        'garbage line',
        '150 sess-a notanumber',
        '200 sess-a 1500',  # latest in-window sample
    )
    assert RateLimitLog.usage_since(window_start=50) == 500


def test_missing_log_returns_zero(tmp_home: Path) -> None:
    assert RateLimitLog.usage_since(window_start=0) == 0


def test_negative_delta_is_clamped_to_zero(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '50 sess-a 5000',   # pre-window baseline, higher than later reset value
        '150 sess-a 200',   # counter reset mid-window
    )
    assert RateLimitLog.usage_since(window_start=100) == 0
