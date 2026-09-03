"""Tests for yas.rate_limits_sim: rolling/fixed bucket derivation, override of
real values, and absent-key fallback (RateLimitLog is exercised directly
against a tmp_path-patched runtime dir via the `tmp_home` fixture).

All the *_tokens components below use `input_tokens` for the whole amount
(cache_creation/cache_read/output left at 0) unless a test is specifically
about weighting -- RATE_LIMIT_WEIGHT_INPUT is 1.0, so that keeps these
tests' expected percentages numerically identical to the pre-refactor
single-cumulative-number tests they're descended from."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from yas.config import RateLimitRule
from yas.rate_limits_sim import _rolling_bucket, reset_rate_limit_cache, simulate_rate_limits
from yas.session import RateBucket, RateLimits
from yas.tokens import RateLimitLog


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_home: object) -> None:
    # tmp_home (conftest) already redirects CLAUDE_DIR for yas.constants,
    # which rate_limit_log() is derived from. The minute-bucket cache is
    # module-level state (yas.rate_limits_sim._bucket_cache); each pytest-xdist
    # worker owns its own process/module dict, but tests within one worker
    # still share it across test functions, so clear it before every test.
    reset_rate_limit_cache()


def test_absent_rules_passes_real_values_through_unchanged() -> None:
    real = RateLimits(five_hour=RateBucket(used_percentage=12.0, resets_at=999))
    out = simulate_rate_limits('sess-a', {}, real, 1000, 0, 0, 0)
    assert out is real


def test_rolling_bucket_sums_history_within_window(monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = 'sess-rolling'
    first_sample_ts = 1_700_000_000.0
    monkeypatch.setattr(time, 'time', lambda: first_sample_ts)
    # Two samples 100s apart, both inside a 5h window, and no earlier
    # (pre-window) sample for this session -> baseline is 0, so the *full*
    # latest cumulative value (5000) counts, not the 1000->5000 delta.
    RateLimitLog.record(session_id, 1000, 0, 0, 0, keep_seconds=6 * 3600)
    rule = RateLimitRule(budget=8000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    real = RateLimits()
    monkeypatch.setattr(time, 'time', lambda: first_sample_ts + 100)
    out = simulate_rate_limits(session_id, {'five_hour': rule}, real, 5000, 0, 0, 0, now=first_sample_ts + 100)
    # resets_at is anchored at the *first* sample's timestamp, not `now`.
    assert out.five_hour.used_percentage == pytest.approx(5000 / 8000 * 100, abs=0.01)
    assert out.five_hour.resets_at == int(first_sample_ts + 5 * 3600)


def test_rolling_bucket_clamps_to_100_percent() -> None:
    session_id = 'sess-clamp'
    now = time.time()
    RateLimitLog.record(session_id, 0, 0, 0, 0, keep_seconds=6 * 3600)
    rule = RateLimitRule(budget=100, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    out = simulate_rate_limits(session_id, {'five_hour': rule}, RateLimits(), 10_000, 0, 0, 0, now=now + 60)
    assert out.five_hour.used_percentage == 100.0


def test_fixed_bucket_reset_is_next_cron_firing() -> None:
    session_id = 'sess-fixed'
    now = time.time()
    RateLimitLog.record(session_id, 0, 0, 0, 0, keep_seconds=8 * 86400)
    rule = RateLimitRule(budget=1000, window_seconds=7 * 86400, anchor='fixed', epoch='0 0 * * 0')
    out = simulate_rate_limits(session_id, {'seven_day': rule}, RateLimits(), 500, 0, 0, 0, now=now)
    # resets_at is a real epoch int strictly after `now`.
    assert isinstance(out.seven_day.resets_at, int)
    assert out.seven_day.resets_at > now


def test_override_replaces_real_value_even_when_present() -> None:
    session_id = 'sess-override'
    now = time.time()
    RateLimitLog.record(session_id, 0, 0, 0, 0, keep_seconds=6 * 3600)
    real = RateLimits(five_hour=RateBucket(used_percentage=77.0, resets_at=123))
    rule = RateLimitRule(budget=1000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    out = simulate_rate_limits(session_id, {'five_hour': rule}, real, 200, 0, 0, 0, now=now + 30)
    assert out.five_hour != real.five_hour
    assert out.five_hour.resets_at != 123


def test_absent_key_falls_back_to_real_seven_day() -> None:
    session_id = 'sess-partial'
    now = time.time()
    RateLimitLog.record(session_id, 0, 0, 0, 0, keep_seconds=6 * 3600)
    real = RateLimits(seven_day=RateBucket(used_percentage=42.0, resets_at=555))
    rule = RateLimitRule(budget=1000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    out = simulate_rate_limits(session_id, {'five_hour': rule}, real, 200, 0, 0, 0, now=now + 30)
    assert out.seven_day == real.seven_day


def test_single_sample_in_window_with_no_baseline_counts_in_full() -> None:
    session_id = 'sess-one-sample'
    now = time.time()
    rule = RateLimitRule(budget=1_000_000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    # No prior RateLimitLog.record call for this session -> only one sample
    # (the one taken inside simulate_rate_limits itself) ever lands in the
    # window, with no pre-window baseline -> it counts in full (session
    # started inside the window).
    out = simulate_rate_limits(session_id, {'five_hour': rule}, RateLimits(), 999_999, 0, 0, 0, now=now)
    assert out.five_hour.used_percentage == pytest.approx(999_999 / 1_000_000 * 100, abs=0.01)


def test_weighted_components_feed_the_bucket_percentage() -> None:
    # cache_creation (x1.25), cache_read (x0.1), and output (x5.0, the
    # pricing-derived default) are weighted differently from input (x1.0):
    # 100*1.0 + 100*1.25 + 100*0.1 + 100*5.0 = 735.
    session_id = 'sess-weighted'
    now = time.time()
    rule = RateLimitRule(budget=1000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    out = simulate_rate_limits(session_id, {'five_hour': rule}, RateLimits(), 100, 100, 100, 100, now=now)
    assert out.five_hour.used_percentage == pytest.approx(73.5, abs=0.01)


def test_custom_weights_change_the_bucket_percentage() -> None:
    """An explicit `weights` overrides the pricing-derived defaults end-to-end."""
    from yas.config import RateLimitWeights
    session_id = 'sess-custom-weighted'
    now = time.time()
    rule = RateLimitRule(budget=1000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    weights = RateLimitWeights(input=1.0, cache_creation=1.0, cache_read=1.0, output=1.0)
    out = simulate_rate_limits(
        session_id, {'five_hour': rule}, RateLimits(), 100, 100, 100, 100, now=now, weights=weights)
    assert out.five_hour.used_percentage == pytest.approx(40.0, abs=0.01)


def test_config_sourced_cache_read_weight_visibly_changes_used_percentage(tmp_path: Path) -> None:
    """End-to-end: a [rate_limits.weights] cache_read override in yas.toml
    changes the resulting bucket's used_percentage, exercising the full
    Config.load() -> cfg.rate_limit_weights -> simulate_rate_limits path
    rather than constructing a RateLimitWeights by hand.

    Both percentages are derived from the SAME recorded sample (rate limits
    are account-wide, so a second `simulate_rate_limits` call for a second
    session would double-count into the shared log) -- only the weights
    passed to `RateLimitLog.usage_since` differ between the two assertions.
    """
    from yas.config import Config

    (tmp_path / 'yas.toml').write_text(
        '[rate_limits.weights]\n'
        'cache_read = 1.0\n'  # default is 0.1 -- a 10x change should show up
    )
    cfg = Config.load(env={}, config_dir=tmp_path)
    assert not cfg.errors

    session_id = 'sess-cfg-weighted'
    now = time.time()
    rule = RateLimitRule(budget=1000, window_seconds=5 * 3600, anchor='rolling', epoch=None)

    # One recorded sample: cache_read=100, everything else 0.
    simulate_rate_limits(session_id, {'five_hour': rule}, RateLimits(), 0, 0, 100, 0, now=now)

    window_start = 0.0
    default_used    = RateLimitLog.usage_since(window_start)
    configured_used = RateLimitLog.usage_since(window_start, weights=cfg.rate_limit_weights)

    assert default_used == 10       # 100 * 0.1 (built-in default)
    assert configured_used == 100   # 100 * 1.0 (yas.toml override)
    assert configured_used != default_used


def test_same_minute_calls_are_stable_and_skip_recompute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two calls within the same process and quantized minute dedup via _bucket_cache
    (no second usage_since scan). This is in-process dedup only, not cross-render
    caching: a real statusline render is a fresh process each time, so cross-render
    minute-stability comes from the `now` quantization exercised separately below,
    not from this cache surviving between renders."""
    session_id = 'sess-minute-stable'
    minute_start = int(time.time() // 60) * 60
    rule = RateLimitRule(budget=1000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    real = RateLimits()

    calls = []
    real_usage_since = RateLimitLog.usage_since
    monkeypatch.setattr(RateLimitLog, 'usage_since', staticmethod(lambda *a, **kw: (calls.append(1), real_usage_since(*a, **kw))[1]))

    first  = simulate_rate_limits(session_id, {'five_hour': rule}, real, 200, 0, 0, 0, now=minute_start + 5)
    second = simulate_rate_limits(session_id, {'five_hour': rule}, real, 9_000, 0, 0, 0, now=minute_start + 45)

    assert first.five_hour.used_percentage == second.five_hour.used_percentage
    assert first.five_hour.resets_at == second.five_hour.resets_at
    assert len(calls) == 1  # second call within the same minute hit the cache, no re-scan


def test_minute_rollover_recomputes_percentage_but_resets_at_stays_anchored() -> None:
    """A minute rollover forces a recompute (cache miss), and usage can grow,
    but resets_at must NOT drift with `now` any more -- it stays pinned to
    the window's anchor until the window actually lapses. This is the
    regression this whole fix targets: resets_at used to be `now + window`,
    which reset the countdown to ~5h on every single render."""
    session_id = 'sess-minute-rollover'
    minute_start = int(time.time() // 60) * 60
    rule = RateLimitRule(budget=1000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    real = RateLimits()

    first  = simulate_rate_limits(session_id, {'five_hour': rule}, real, 100, 0, 0, 0, now=minute_start + 5)
    second = simulate_rate_limits(session_id, {'five_hour': rule}, real, 800, 0, 0, 0, now=minute_start + 60)

    assert second.five_hour.used_percentage != first.five_hour.used_percentage
    assert second.five_hour.resets_at == first.five_hour.resets_at


def test_two_sessions_share_the_same_anchored_window() -> None:
    """The 5h/7d limit is account-wide: two concurrent sessions computing
    their own bucket against the identical log state must land on the same
    (used_percentage, resets_at) pair -- neither the anchor nor the usage sum
    depend on which session_id is asking. Writes both sessions' history
    directly (rather than through simulate_rate_limits, which would append a
    fresh record per call and make the log states diverge between the two
    calls) so both buckets are computed against one fixed, shared log."""
    RateLimitLog.record('sess-a', 1_000, 0, 0, 0, keep_seconds=6 * 3600)
    RateLimitLog.record('sess-b', 2_500, 0, 0, 0, keep_seconds=6 * 3600)
    rule = RateLimitRule(budget=10_000, window_seconds=5 * 3600, anchor='rolling', epoch=None)

    now = time.time() + 300
    out_a = _rolling_bucket(rule, 'sess-a', now)
    out_b = _rolling_bucket(rule, 'sess-b', now)

    assert out_a.resets_at == out_b.resets_at
    assert out_a.used_percentage == out_b.used_percentage


def test_idle_ticks_after_a_lapsed_window_do_not_open_a_new_window() -> None:
    """Regression guard for the bug dedupe fixes: before dedupe, every tick
    (including idle heartbeats that repeat the same cumulative value) was
    appended to the log, so an idle session's heartbeat could anchor a brand
    new window having spent zero tokens. With dedupe, unchanged ticks never
    reach the log, so a lapsed window with only idle activity since must
    still fall back to `now` (RateLimitLog.window_anchor's "lapsed with
    nothing after it" case) rather than anchoring on an idle heartbeat."""
    session_id = 'sess-idle'
    window = 5 * 3600
    rule = RateLimitRule(budget=1000, window_seconds=window, anchor='rolling', epoch=None)
    real = RateLimits()

    # Opens a window at t=0 with real usage.
    simulate_rate_limits(session_id, {'five_hour': rule}, real, 100, 0, 0, 0, now=0.0)
    # The window has fully lapsed by t=window+100. Every tick since is an
    # idle heartbeat repeating the same cumulative value -- these must not
    # land on disk, and must not anchor a fresh window.
    now = window + 100.0
    for t in (window + 20.0, window + 60.0, now):
        out = simulate_rate_limits(session_id, {'five_hour': rule}, real, 100, 0, 0, 0, now=t)

    # A window opened by a zero-spend idle heartbeat would anchor at one of
    # those idle timestamps; instead it must fall back to `now` (no activity
    # since the lapsed window) and report zero usage. `now` is quantized to
    # the minute before it reaches the anchor logic (see rate_limits_sim's
    # _BUCKET_SECONDS), so compare against that same quantized value.
    quantized_now = int(now // 60) * 60
    assert out.five_hour.resets_at == quantized_now + window
    assert out.five_hour.used_percentage == 0.0


def test_regression_guard_update_interval_is_60s_not_300s(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards against the minute cache regressing to the shared 300s
    CACHE_TTL_SECONDS: advancing the clock by 61s (> 60, < 300) must still
    trigger a recompute -- if this ever fails, someone wired the rate-limit
    cache to CACHE_TTL_SECONDS instead of its own 60s bucket."""
    session_id = 'sess-regression-guard'
    minute_start = int(time.time() // 60) * 60
    rule = RateLimitRule(budget=1000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    real = RateLimits()

    calls = []
    real_usage_since = RateLimitLog.usage_since
    monkeypatch.setattr(RateLimitLog, 'usage_since', staticmethod(lambda *a, **kw: (calls.append(1), real_usage_since(*a, **kw))[1]))

    simulate_rate_limits(session_id, {'five_hour': rule}, real, 100, 0, 0, 0, now=minute_start)
    simulate_rate_limits(session_id, {'five_hour': rule}, real, 800, 0, 0, 0, now=minute_start + 61)

    assert len(calls) == 2  # would be 1 if the TTL were 300s instead of 60s
