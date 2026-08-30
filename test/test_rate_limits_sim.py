"""Tests for yas.rate_limits_sim: rolling/fixed bucket derivation, override of
real values, and absent-key fallback (RateLimitLog is exercised directly
against a tmp_path-patched runtime dir via the `tmp_home` fixture)."""

from __future__ import annotations

import time

import pytest

from yas.config import RateLimitRule
from yas.rate_limits_sim import reset_rate_limit_cache, simulate_rate_limits
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
    out = simulate_rate_limits('sess-a', {}, real, cumulative_tokens=1000)
    assert out is real


def test_rolling_bucket_sums_history_within_window() -> None:
    session_id = 'sess-rolling'
    now = time.time()
    # Two samples 100s apart, well inside a 5h window: 1000 -> 5000 tokens.
    RateLimitLog.record(session_id, 1000, keep_seconds=6 * 3600)
    # Fake an earlier sample by writing directly then a fresh one via record().
    rule = RateLimitRule(budget=8000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    real = RateLimits()
    out = simulate_rate_limits(session_id, {'five_hour': rule}, real, cumulative_tokens=5000, now=now + 100)
    minute_start = int((now + 100) // 60) * 60  # resets_at is derived from the minute-quantized `now`
    assert out.five_hour.used_percentage == pytest.approx((5000 - 1000) / 8000 * 100, abs=0.01)
    assert out.five_hour.resets_at == int(minute_start + 5 * 3600)


def test_rolling_bucket_clamps_to_100_percent() -> None:
    session_id = 'sess-clamp'
    now = time.time()
    RateLimitLog.record(session_id, 0, keep_seconds=6 * 3600)
    rule = RateLimitRule(budget=100, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    out = simulate_rate_limits(session_id, {'five_hour': rule}, RateLimits(), cumulative_tokens=10_000, now=now + 60)
    assert out.five_hour.used_percentage == 100.0


def test_fixed_bucket_reset_is_next_cron_firing() -> None:
    session_id = 'sess-fixed'
    now = time.time()
    RateLimitLog.record(session_id, 0, keep_seconds=8 * 86400)
    rule = RateLimitRule(budget=1000, window_seconds=7 * 86400, anchor='fixed', epoch='0 0 * * 0')
    out = simulate_rate_limits(session_id, {'seven_day': rule}, RateLimits(), cumulative_tokens=500, now=now)
    # resets_at is a real epoch int strictly after `now`.
    assert isinstance(out.seven_day.resets_at, int)
    assert out.seven_day.resets_at > now


def test_override_replaces_real_value_even_when_present() -> None:
    session_id = 'sess-override'
    now = time.time()
    RateLimitLog.record(session_id, 0, keep_seconds=6 * 3600)
    real = RateLimits(five_hour=RateBucket(used_percentage=77.0, resets_at=123))
    rule = RateLimitRule(budget=1000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    out = simulate_rate_limits(session_id, {'five_hour': rule}, real, cumulative_tokens=200, now=now + 30)
    assert out.five_hour != real.five_hour
    assert out.five_hour.resets_at != 123


def test_absent_key_falls_back_to_real_seven_day() -> None:
    session_id = 'sess-partial'
    now = time.time()
    RateLimitLog.record(session_id, 0, keep_seconds=6 * 3600)
    real = RateLimits(seven_day=RateBucket(used_percentage=42.0, resets_at=555))
    rule = RateLimitRule(budget=1000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    out = simulate_rate_limits(session_id, {'five_hour': rule}, real, cumulative_tokens=200, now=now + 30)
    assert out.seven_day == real.seven_day


def test_single_sample_in_window_yields_zero_usage() -> None:
    session_id = 'sess-one-sample'
    now = time.time()
    rule = RateLimitRule(budget=1000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    # No prior RateLimitLog.record call for this session -> only one sample
    # (the one taken inside simulate_rate_limits itself) ever lands in the window.
    out = simulate_rate_limits(session_id, {'five_hour': rule}, RateLimits(), cumulative_tokens=999_999, now=now)
    assert out.five_hour.used_percentage == 0.0


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

    first  = simulate_rate_limits(session_id, {'five_hour': rule}, real, cumulative_tokens=200, now=minute_start + 5)
    second = simulate_rate_limits(session_id, {'five_hour': rule}, real, cumulative_tokens=9_000, now=minute_start + 45)

    assert first.five_hour.used_percentage == second.five_hour.used_percentage
    assert first.five_hour.resets_at == second.five_hour.resets_at
    assert len(calls) == 1  # second call within the same minute hit the cache, no re-scan


def test_minute_rollover_recomputes_and_can_change_value() -> None:
    session_id = 'sess-minute-rollover'
    minute_start = int(time.time() // 60) * 60
    rule = RateLimitRule(budget=1000, window_seconds=5 * 3600, anchor='rolling', epoch=None)
    real = RateLimits()

    first  = simulate_rate_limits(session_id, {'five_hour': rule}, real, cumulative_tokens=100, now=minute_start + 5)
    second = simulate_rate_limits(session_id, {'five_hour': rule}, real, cumulative_tokens=800, now=minute_start + 60)

    assert second.five_hour.used_percentage != first.five_hour.used_percentage
    assert second.five_hour.resets_at != first.five_hour.resets_at


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

    simulate_rate_limits(session_id, {'five_hour': rule}, real, cumulative_tokens=100, now=minute_start)
    simulate_rate_limits(session_id, {'five_hour': rule}, real, cumulative_tokens=800, now=minute_start + 61)

    assert len(calls) == 2  # would be 1 if the TTL were 300s instead of 60s
