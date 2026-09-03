"""Synthesise 5h/7d rate-limit buckets from local token history.

Feeds the [rate_limits] config knob (yas.config.RateLimitRule): on setups
where Claude Code doesn't supply real `rate_limits.{five_hour,seven_day}`
values, a user can define a budget/window/anchor per bucket and this module
derives a used_percentage + resets_at pair, in the same shape as the real
payload (yas.session.RateBucket), from the RateLimitLog history.

The input signal is each session's LIFETIME transcript usage (yas.info.
transcript.TranscriptUsage's input/cache_creation/cache_read/output sums,
deduped by message id) -- monotonic for the life of a session, unlike the
raw payload's context_window fields, which describe only the most recent
request and can silently DROP on /compact or /clear. `RateLimitLog` records
the four components separately per tick and `usage_since` (yas.tokens)
combines them with a per-component weighting (a `yas.config.
RateLimitWeights`, resolved once by the caller and threaded down as
`weights`; falls back to RATE_LIMIT_WEIGHT_* in yas.tokens when omitted)
before summing a window's usage -- those weights mirror the public API's
cache/output pricing ratios but are an explicit ASSUMPTION, since Anthropic
does not document how the 5h/7d windows actually weight cache or output
tokens. Tunable per-key via `[rate_limits.weights]` in yas.toml.

`anchor = 'rolling'` is an account-wide window anchored at first activity
(RateLimitLog.window_anchor): the window opens at the first activity not
already inside a live window, runs for its FULL `window_seconds` regardless
of how idle it goes, every concurrent session shares that same window (they
all read the same account-wide log), and only advances once `now` actually
passes its end. This is what makes resets_at actually count down instead of
perpetually reporting "window_seconds from now". `anchor = 'fixed'` is
unchanged: aligned to a cron schedule.
"""

from __future__ import annotations

import time
from datetime import datetime

from yas.cron import CronSchedule
from yas.session import RateBucket, RateLimits
from yas.tokens import RateLimitLog

if False:  # TYPE_CHECKING without importing yas.config here (no cycle risk, but keep it lazy)
    from yas.config import RateLimitRule, RateLimitWeights


# Fixed-anchor cron periods are expected to roughly match the configured
# `window`, but a pathological cron (e.g. yearly) could put the previous
# firing far in the past. Cap how far back a lookback can reach so a single
# bad config can't make RateLimitLog.usage_since scan an unbounded log.
MAX_LOOKBACK_SECONDS = 40 * 86400

# Minute-stability across statusline renders comes from quantizing `now`:
# `now` is floored to the minute before it ever reaches
# _rolling_bucket/_fixed_bucket, so used_percentage and resets_at are stable
# across every render within that minute (no jitter on resets_at for
# anchor='rolling'). Each statusline render is a fresh process
# (statusline_command.py -> yas.app.main, then exit), so this quantization
# -- not the cache below -- is what makes two renders in the same minute agree.
_BUCKET_SECONDS = 60

# (session_id, bucket_name) -> (minute_index, RateBucket). This cache's only
# job is to skip a redundant RateLimitLog.usage_since scan for calls that land
# in the same process within the same quantized minute -- e.g. the two
# buckets computed for one render, or successive ticks of `mon`'s
# long-lived `while True: tick(args)` loop. It cannot span renders: it's a
# module-level dict, and each statusline render is its own process with its
# own empty cache. One entry per key, so a minute rollover simply overwrites
# the previous one instead of the cache growing unbounded over a long-running
# `mon`/statusline session.
_bucket_cache: dict[tuple[str, str], tuple[int, RateBucket]] = {}


def reset_rate_limit_cache() -> None:
    """Clear the minute-bucket cache. Call from a test fixture, never from
    production code -- each worker/process owns its own module dict, so this
    only matters for isolating tests within a single pytest-xdist worker."""
    _bucket_cache.clear()


def _clamp_pct(used: int, budget: int) -> float:
    if budget <= 0:
        return 0.0
    pct = (used / budget) * 100.0
    return round(max(0.0, min(100.0, pct)), 2)


def _rolling_bucket(
    rule:        'RateLimitRule',
    session_id:  str,
    now:         float,
    by_session:  dict[str, list[tuple[float, int, int, int, int]]] | None = None,
    weights:     'RateLimitWeights | None' = None,
) -> RateBucket:
    # Anchored at first account-wide activity, not at `now` -- see
    # RateLimitLog.window_anchor. Every concurrent session shares the same
    # log, so they all derive the identical (window_start, resets_at) pair.
    # `by_session` lets a caller that already parsed the log (record(), via
    # simulate_rate_limits) reuse that parse instead of re-reading the file.
    window_start = RateLimitLog.window_anchor(rule.window_seconds, now, by_session)
    used         = RateLimitLog.usage_since(window_start, by_session, weights)
    return RateBucket(
        used_percentage = _clamp_pct(used, rule.budget),
        resets_at        = int(window_start + rule.window_seconds),
    )


def _fixed_bucket(
    rule:        'RateLimitRule',
    session_id:  str,
    now:         float,
    by_session:  dict[str, list[tuple[float, int, int, int, int]]] | None = None,
    weights:     'RateLimitWeights | None' = None,
) -> RateBucket:
    assert rule.epoch is not None  # enforced at config-load time
    sched   = CronSchedule.parse(rule.epoch)
    now_dt  = datetime.fromtimestamp(now)
    window_start_dt = sched.prev_at_or_before(now_dt)
    reset_dt        = sched.next_after(now_dt)
    window_start = max(window_start_dt.timestamp(), now - MAX_LOOKBACK_SECONDS)
    used = RateLimitLog.usage_since(window_start, by_session, weights)
    return RateBucket(
        used_percentage = _clamp_pct(used, rule.budget),
        resets_at        = int(reset_dt.timestamp()),
    )


def _synth_bucket(
    rule:        'RateLimitRule',
    session_id:  str,
    bucket_name: str,
    now:         float,
    by_session:  dict[str, list[tuple[float, int, int, int, int]]] | None = None,
    weights:     'RateLimitWeights | None' = None,
) -> RateBucket:
    minute = int(now // _BUCKET_SECONDS)
    key     = (session_id, bucket_name)
    cached  = _bucket_cache.get(key)
    if cached is not None and cached[0] == minute:
        return cached[1]

    minute_start = minute * _BUCKET_SECONDS  # quantized `now`: stable resets_at + window basis all render this minute
    if rule.anchor == 'fixed':
        bucket = _fixed_bucket(rule, session_id, minute_start, by_session, weights)
    else:
        bucket = _rolling_bucket(rule, session_id, minute_start, by_session, weights)
    _bucket_cache[key] = (minute, bucket)
    return bucket


def simulate_rate_limits(
    session_id:            str,
    rules:                  dict[str, 'RateLimitRule'],
    real_rate_limits:       RateLimits,
    input_tokens:           int,
    cache_creation_tokens:  int,
    cache_read_tokens:      int,
    output_tokens:          int,
    now:                    float | None = None,
    weights:                'RateLimitWeights | None' = None,
) -> RateLimits:
    """Override real_rate_limits' buckets with synthesised ones per `rules`.

    A bucket absent from `rules` passes its real value through untouched
    (today's behaviour: 5h renders unlimited, 7d is omitted, when Claude Code
    supplies nothing). The four *_tokens args are this tick's lifetime
    transcript totals (yas.info.transcript.TranscriptUsage's input,
    cache_creation, cache_read, and output sums -- monotonic across a
    session, unlike the raw payload's context_window gauge) and are
    recorded to RateLimitLog before any bucket is computed -- but only when
    at least one component actually differs from this session's last
    recorded sample (RateLimitLog.record dedupes idle heartbeats), so a
    fresh tick's real usage is still counted towards the trailing window
    even though most ticks write nothing.

    `record()` parses the log to do that dedupe check, and returns the
    parsed (and, when it wrote, updated) `by_session` structure; that same
    structure is threaded into `_synth_bucket` so the log is parsed once per
    render rather than once for the record and again per bucket.
    """
    if not rules:
        return real_rate_limits
    t = now if now is not None else time.time()
    keep_seconds = max(rule.window_seconds for rule in rules.values()) * 2
    keep_seconds = min(keep_seconds, MAX_LOOKBACK_SECONDS) + 3600  # small safety margin
    by_session = RateLimitLog.record(
        session_id, input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens,
        keep_seconds=keep_seconds, now=t,
    )

    five_hour = _synth_bucket(rules['five_hour'], session_id, 'five_hour', t, by_session, weights) if 'five_hour' in rules else real_rate_limits.five_hour
    seven_day = _synth_bucket(rules['seven_day'], session_id, 'seven_day', t, by_session, weights) if 'seven_day' in rules else real_rate_limits.seven_day
    return RateLimits(five_hour=five_hour, seven_day=seven_day)
