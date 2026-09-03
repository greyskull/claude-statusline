"""Tests for RateLimitLog.usage_since: cross-session summation with a
per-session pre-window baseline and per-component weighting (yas.tokens)."""
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
        '0 sess-a 1000 0 0 0',    # pre-window baseline
        '200 sess-a 4000 0 0 0',  # in-window
        '0 sess-b 500 0 0 0',     # pre-window baseline
        '200 sess-b 2500 0 0 0',  # in-window
    )
    # sess-a: 4000 - 1000 = 3000, sess-b: 2500 - 500 = 2000 -> 5000 total
    # (input weight is 1.0, so the weighted total equals the raw delta here).
    assert RateLimitLog.usage_since(window_start=50) == 5000


def test_session_starting_inside_window_counts_first_sample_in_full(tmp_home: Path) -> None:
    _write(tmp_home, '200 sess-new 45572 0 0 0')
    # No pre-window sample for sess-new -> baseline is 0, full value counts.
    assert RateLimitLog.usage_since(window_start=100) == 45572


def test_pre_window_sample_is_diffed_not_first_in_window_sample(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '50 sess-a 900 0 0 0',    # pre-window baseline
        '150 sess-a 1200 0 0 0',  # first in-window sample
        '250 sess-a 1500 0 0 0',  # latest sample
    )
    # Baseline must be the pre-window sample (900), not the first in-window
    # sample (1200) -- 1500 - 900 = 600, not 1500 - 1200 = 300.
    assert RateLimitLog.usage_since(window_start=100) == 600


def test_session_with_no_in_window_samples_contributes_zero(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '10 sess-stale 100 0 0 0',
        '20 sess-stale 200 0 0 0',
    )
    assert RateLimitLog.usage_since(window_start=1000) == 0


def test_malformed_lines_are_skipped(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '0 sess-a 1000 0 0 0',    # pre-window baseline
        'garbage line',
        '150 sess-a notanumber 0 0 0',
        '200 sess-a 1500 0 0 0',  # latest in-window sample
    )
    assert RateLimitLog.usage_since(window_start=50) == 500


def test_legacy_three_field_lines_are_skipped(tmp_home: Path) -> None:
    # The old `ts session_id cumulative_tokens` format is a context-size
    # GAUGE, not a lifetime sum -- not interpretable under the new 4-field
    # scheme, so it must be ignored entirely rather than misread.
    _write(
        tmp_home,
        '0 sess-a 156905',        # legacy line -- skipped
        '200 sess-a 1000 0 0 0',  # only real sample -> counts in full (no baseline)
    )
    assert RateLimitLog.usage_since(window_start=50) == 1000


def test_missing_log_returns_zero(tmp_home: Path) -> None:
    assert RateLimitLog.usage_since(window_start=0) == 0


def test_negative_delta_is_clamped_to_zero_per_component(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '50 sess-a 5000 100 50 200',   # pre-window baseline, higher than later reset values
        '150 sess-a 200 10 5 20',      # counter reset mid-window (e.g. /compact)
    )
    # Every component dropped -> each clamps to 0, not a negative contribution.
    assert RateLimitLog.usage_since(window_start=100) == 0


def test_a_context_size_drop_can_no_longer_read_as_negative_usage(tmp_home: Path) -> None:
    # Regression guard: the old context_window-gauge signal could drop after
    # /compact and (with the old max(0, ...) clamp) silently read as zero
    # usage even though real usage happened. Under the new monotonic
    # transcript-sum signal, a within-window drop in one component (e.g.
    # cache_read reset by /compact) still nets out to >= 0 usage from the
    # OTHER components that kept growing, never negative overall.
    _write(
        tmp_home,
        '50 sess-a 1000 0 500 0',   # pre-window baseline: cache_read=500
        '150 sess-a 1200 0 10 0',   # /compact: cache_read collapses to 10, input still grew
    )
    used = RateLimitLog.usage_since(window_start=100)
    assert used >= 0
    # input delta (200 * 1.0) counts; cache_read's drop clamps to 0, not -490.
    assert used == 200


def test_weighted_components_are_combined_per_the_documented_ratios(tmp_home: Path) -> None:
    # input=100 (x1.0) + cache_creation=100 (x1.25) + cache_read=100 (x0.1)
    # + output=100 (x5.0, the pricing-derived default) = 100 + 125 + 10 + 500 = 735.
    _write(
        tmp_home,
        '0 sess-a 0 0 0 0',
        '200 sess-a 100 100 100 100',
    )
    assert RateLimitLog.usage_since(window_start=50) == 735


def test_explicit_weights_override_the_defaults(tmp_home: Path) -> None:
    from yas.config import RateLimitWeights
    _write(
        tmp_home,
        '0 sess-a 0 0 0 0',
        '200 sess-a 100 100 100 100',
    )
    weights = RateLimitWeights(input=1.0, cache_creation=1.0, cache_read=1.0, output=1.0)
    assert RateLimitLog.usage_since(window_start=50, weights=weights) == 400


def test_cache_read_is_weighted_far_lower_than_input(tmp_home: Path) -> None:
    _write(
        tmp_home,
        '0 sess-cache-heavy 0 0 0 0',
        '200 sess-cache-heavy 0 0 1_000_000 0',  # a turn that re-reads a huge cached prefix
        '0 sess-input-heavy 0 0 0 0',
        '200 sess-input-heavy 100_000 0 0 0',
    )
    cache_only  = RateLimitLog.usage_since(window_start=50, by_session={'sess-cache-heavy': [(0.0, 0, 0, 0, 0), (200.0, 0, 0, 1_000_000, 0)]})
    input_only  = RateLimitLog.usage_since(window_start=50, by_session={'sess-input-heavy': [(0.0, 0, 0, 0, 0), (200.0, 100_000, 0, 0, 0)]})
    assert cache_only == 100_000    # 1,000,000 * 0.1
    assert input_only == 100_000    # 100,000 * 1.0
    assert cache_only < 1_000_000   # NOT counted 1:1 the way the old gauge signal implicitly did
