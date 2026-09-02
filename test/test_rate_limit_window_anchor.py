"""Tests for RateLimitLog.window_anchor: the account-wide rolling-window
anchor that replaces `now - window_seconds` so 5h/7d countdowns actually
count down instead of perpetually reporting `window_seconds` remaining."""
from __future__ import annotations

from pathlib import Path

from yas.constants import rate_limit_log
from yas.tokens import RateLimitLog

WINDOW = 5 * 3600  # 5h, matches the real five_hour bucket's window_seconds


def _write(tmp_home: Path, *rows: str) -> None:
    log = rate_limit_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('\n'.join(rows) + '\n')


def test_missing_log_anchors_at_now(tmp_home: Path) -> None:
    now = 10_000.0
    assert RateLimitLog.window_anchor(WINDOW, now) == now


def test_empty_log_anchors_at_now(tmp_home: Path) -> None:
    _write(tmp_home)  # zero rows, just the trailing newline
    now = 10_000.0
    assert RateLimitLog.window_anchor(WINDOW, now) == now


def test_samples_inside_one_window_anchor_at_earliest_sample(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '1000 sess-a 500',
        '1200 sess-a 900',
        '1500 sess-b 300',
    )
    now = 2000.0  # well inside [1000, 1000 + WINDOW)
    assert RateLimitLog.window_anchor(WINDOW, now) == 1000.0


def test_resets_at_is_stable_across_two_now_values_within_the_window(tmp_home: Path) -> None:
    _write(tmp_home, '1000 sess-a 500')
    anchor_early = RateLimitLog.window_anchor(WINDOW, now=2000.0)
    anchor_later = RateLimitLog.window_anchor(WINDOW, now=1000.0 + WINDOW - 1)
    # Same window -> same anchor -> same resets_at, even though `now` advanced.
    assert anchor_early == anchor_later == 1000.0


def test_now_past_first_window_advances_anchor_to_next_windows_first_sample(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '1000 sess-a 500',            # opens window [1000, 1000+WINDOW)
        f'{1000 + WINDOW} sess-a 900',  # first activity of the next window
    )
    now = 1000 + WINDOW + 10  # inside the second window
    assert RateLimitLog.window_anchor(WINDOW, now) == 1000 + WINDOW


def test_multiple_lapsed_windows_are_skipped_in_one_call(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '0 sess-a 100',                    # window 1: [0, WINDOW)
        f'{3 * WINDOW} sess-a 200',         # window 4: [3*WINDOW, 4*WINDOW) -- only sample after window 1
    )
    now = 3 * WINDOW + 10
    # Windows 2 and 3 have no samples at all, so the anchor jumps straight
    # from window 1's start to the next sample that actually exists.
    assert RateLimitLog.window_anchor(WINDOW, now) == 3 * WINDOW


def test_lapsed_window_with_no_later_samples_falls_back_to_now(tmp_home: Path) -> None:
    _write(tmp_home, '0 sess-a 100')  # window [0, WINDOW), nothing since
    now = WINDOW + 500  # window has lapsed, no activity since
    assert RateLimitLog.window_anchor(WINDOW, now) == now


def test_anchor_is_account_wide_not_per_session(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '1000 sess-a 500',   # earliest overall -> opens the window
        '1400 sess-b 200',
    )
    now = 2000.0
    # sess-b's own first sample (1400) must not shadow sess-a's earlier one.
    assert RateLimitLog.window_anchor(WINDOW, now) == 1000.0
