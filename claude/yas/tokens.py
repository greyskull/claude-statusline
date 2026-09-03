"""Token accounting, rate tracking, and daily-cost log helpers.

Imports:
  - yas.session  for Model and usage types
  - yas.constants for the runtime/log path helpers (tokens_log, token_rate_log, render_log)
"""

from __future__ import annotations

import functools
import time
from bisect import bisect_left
from typing import Any, TYPE_CHECKING

from yas.constants import tokens_log, token_rate_log, rate_limit_log, render_log
from yas.session import Model

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# TickRecord (forward-declared here to avoid circular imports — app.py imports
# from layout.py which must not import from app.py)
# ---------------------------------------------------------------------------

class TickRecord:
    __slots__ = ('token_log', 'day_cost', 'tok_rate')

    def __init__(self, token_log: 'TokenLog', day_cost: float, tok_rate: int) -> None:
        self.token_log = token_log
        self.day_cost  = day_cost
        self.tok_rate  = tok_rate


# ---------------------------------------------------------------------------
# TokenAccounting
# ---------------------------------------------------------------------------

class TokenAccounting:
    @staticmethod
    def rates_for(model_name: str) -> tuple[float, float]:
        m = model_name.lower()
        if 'opus' in m:
            return 15.00, 75.00
        if 'haiku' in m:
            return 0.80, 4.00
        if 'fable' in m:
            return 10.00, 50.00
        if 'mythos' in m:
            return 10.00, 50.00
        return 3.00, 15.00

    @staticmethod
    def session_cost(model: Model, usage: Any) -> float:
        rate_in, rate_out = TokenAccounting.rates_for(
            model.display_name or model.id
        )
        cost = (
            usage.input_tokens * rate_in
            + usage.cache_creation_input_tokens * rate_in * 1.25
            + usage.cache_read_input_tokens * rate_in * 0.1
            + usage.output_tokens * rate_out
        )
        return float(cost) / 1_000_000

    @staticmethod
    def day_cost(model: Model, token_log: 'TokenLog') -> float:
        rate_in, rate_out = TokenAccounting.rates_for(
            model.display_name or model.id
        )
        cost = (
            token_log.day_in * rate_in
            + token_log.day_cache_read * rate_in * 0.1
            + token_log.day_out * rate_out
        )
        return cost / 1_000_000


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def compute_session_cost(model: Model, usage: Any) -> float:
    return TokenAccounting.session_cost(model, usage)


def compute_day_cost(model: Model, token_log: 'TokenLog') -> float:
    return TokenAccounting.day_cost(model, token_log)


# ---------------------------------------------------------------------------
# TokenLog
# ---------------------------------------------------------------------------

class TokenLog:
    __slots__ = ('day_in', 'day_cache_read', 'day_out')

    def __init__(self, day_in: int = 0, day_cache_read: int = 0, day_out: int = 0) -> None:
        self.day_in         = day_in
        self.day_cache_read = day_cache_read
        self.day_out        = day_out

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TokenLog):
            return NotImplemented
        return (self.day_in, self.day_cache_read, self.day_out) == \
               (other.day_in, other.day_cache_read, other.day_out)

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f'TokenLog(day_in={self.day_in}, day_cache_read={self.day_cache_read}, day_out={self.day_out})'

    @classmethod
    def update(cls, session_id: str, today: str, total_in: int, cache_read: int, total_out: int) -> TokenLog:
        log = tokens_log()
        lines = []
        if log.exists():
            for ln in log.read_text().splitlines():
                parts = ln.split()
                if len(parts) >= 2 and parts[1] == session_id:
                    continue
                lines.append(ln)
        if session_id and (total_in > 0 or cache_read > 0 or total_out > 0):
            lines.append(f'{today} {session_id} {total_in} {cache_read} {total_out}')
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text('\n'.join(lines) + '\n')
        day_in = day_cache_read = day_out = 0
        for ln in lines:
            parts = ln.split()
            if len(parts) < 4 or parts[0] != today:
                continue
            try:
                if len(parts) == 6:
                    day_in += int(parts[2])
                    day_out += int(parts[3])
                elif len(parts) >= 5:
                    day_in += int(parts[2])
                    day_cache_read += int(parts[3])
                    day_out += int(parts[4])
                else:
                    day_in += int(parts[2])
                    day_out += int(parts[3])
            except ValueError:
                pass
        return cls(day_in=day_in, day_cache_read=day_cache_read, day_out=day_out)


# ---------------------------------------------------------------------------
# TokenRate
# ---------------------------------------------------------------------------

@functools.cache
def _token_window() -> float:
    from yas.config import Config
    return Config.load().token_window


class TokenRate:
    # Resolved lazily (see _token_window): evaluating it at import time forced a
    # full Config.load() — and, when a yas.toml exists, the tomllib import — into
    # every startup. None means "resolve from config on first use"; an explicit
    # float (e.g. set by tests) is honoured as-is.
    WINDOW: float | None = None
    KEEP = 300.0

    @classmethod
    def update(cls, session_id: str, total_in: int, total_out: int) -> int:
        if not session_id:
            return 0
        log = token_rate_log()
        now = time.time()
        rows: list[tuple[float, str, int, int]] = []
        if log.exists():
            for ln in log.read_text().splitlines():
                parts = ln.split()
                if len(parts) < 4:
                    continue
                try:
                    ts = float(parts[0])
                    ti = int(parts[2])
                    to = int(parts[3])
                except ValueError:
                    continue
                if now - ts > cls.KEEP:
                    continue
                rows.append((ts, parts[1], ti, to))
        rows.append((now, session_id, total_in, total_out))
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text('\n'.join(f'{ts:.3f} {sid} {ti} {to}' for ts, sid, ti, to in rows) + '\n')
        except OSError:
            pass
        window  = cls.WINDOW if cls.WINDOW is not None else _token_window()
        samples = [(ts, ti, to) for ts, sid, ti, to in rows if sid == session_id and now - ts <= window]
        if len(samples) < 2:
            return 0
        samples.sort()
        _, ti0, to0 = samples[0]
        _, ti1, to1 = samples[-1]
        return max(0, (ti1 + to1) - (ti0 + to0))

    @classmethod
    def history(cls, session_id: str, n_buckets: int, window: float) -> list[int]:
        if n_buckets <= 0 or not session_id:
            return []
        log = token_rate_log()
        now = time.time()
        samples: list[tuple[float, int, int]] = []
        if log.exists():
            for ln in log.read_text().splitlines():
                parts = ln.split()
                if len(parts) < 4:
                    continue
                try:
                    ts = float(parts[0])
                    sid = parts[1]
                    ti = int(parts[2])
                    to = int(parts[3])
                except ValueError:
                    continue
                if sid == session_id and now - ts <= window + window / n_buckets:
                    samples.append((ts, ti, to))
        if len(samples) < 2:
            return [0] * n_buckets
        samples.sort()
        bucket_size = window / n_buckets
        last_bucket  = int(now // bucket_size)
        first_bucket = last_bucket - n_buckets + 1
        buckets = [0] * n_buckets
        for i in range(len(samples) - 1):
            ts0, ti0, to0 = samples[i]
            ts1, ti1, to1 = samples[i + 1]
            delta = max(0, (ti1 + to1) - (ti0 + to0))
            if delta == 0:
                continue
            midpoint = (ts0 + ts1) / 2
            abs_bucket = int(midpoint // bucket_size)
            if first_bucket <= abs_bucket <= last_bucket:
                buckets[abs_bucket - first_bucket] += delta
        return buckets

    @classmethod
    def recently_active(cls, session_id: str, window: float = 10.0) -> tuple[bool, bool]:
        """Return (in_active, out_active) — True if that count grew in the last `window` seconds."""
        if not session_id:
            return False, False
        log = token_rate_log()
        if not log.exists():
            return False, False
        now = time.time()
        samples: list[tuple[float, int, int]] = []
        for ln in log.read_text().splitlines():
            parts = ln.split()
            if len(parts) < 4:
                continue
            try:
                ts, sid, ti, to = float(parts[0]), parts[1], int(parts[2]), int(parts[3])
            except ValueError:
                continue
            if sid == session_id and now - ts <= window:
                samples.append((ts, ti, to))
        if len(samples) < 2:
            return False, False
        samples.sort()
        ti0, to0 = samples[0][1], samples[0][2]
        ti1, to1 = samples[-1][1], samples[-1][2]
        return ti1 > ti0, to1 > to0


# ---------------------------------------------------------------------------
# RateLimitLog
# ---------------------------------------------------------------------------

#: Per-component weights applied by `RateLimitLog.usage_since` when summing
#: a window's consumption. These mirror the public API's per-token *pricing*
#: ratios for cache-creation (1.25x) and cache-read (0.1x) relative to a
#: plain input/output token -- but that is a borrowed assumption, NOT a
#: documented fact: Anthropic does not publish how Max's 5h/7d rate-limit
#: windows weight cache tokens internally. Treat these as our best guess,
#: not ground truth, and revisit if observed usage% drifts from reality.
RATE_LIMIT_WEIGHT_INPUT          = 1.0
RATE_LIMIT_WEIGHT_CACHE_CREATION = 1.25
RATE_LIMIT_WEIGHT_CACHE_READ     = 0.1
RATE_LIMIT_WEIGHT_OUTPUT         = 1.0


class RateLimitLog:
    """Per-session history of cumulative transcript usage, backing the
    [rate_limits] simulator (yas.rate_limits_sim).

    One line per tick: `ts session_id input cache_creation cache_read
    output` -- the four raw usage components (yas.info.transcript.
    TranscriptUsage's lifetime sums across the transcript, deduped by
    message id), each a per-session RUNNING TOTAL, logged separately so the
    per-component weighting in `usage_since` can be tuned later without
    invalidating already-recorded history. Unlike TokenRate's 300s log,
    retention here is caller-supplied (up to 7d for a seven_day bucket)
    since the simulator needs to sum usage over a much longer trailing
    window.

    Older 3-field lines (`ts session_id cumulative_tokens`) are a different,
    incompatible signal -- see `_parse` -- and are skipped on read rather
    than migrated.
    """

    @classmethod
    def record(
        cls,
        session_id:           str,
        input_tokens:          int,
        cache_creation_tokens: int,
        cache_read_tokens:     int,
        output_tokens:         int,
        keep_seconds:          float,
        now:                   float | None = None,
    ) -> dict[str, list[tuple[float, int, int, int, int]]]:
        """Append this tick's sample -- but only when at least one of the
        four components actually changed since this session's last sample.
        Measured on a real log, ~96% of ticks are idle heartbeats repeating
        the previous values; appending those forced a full read-parse-rewrite
        of a 300KB+ file every render for nothing. A session's very first
        sample is always written (including all-zero components), since
        there's no previous value to compare against.

        Returns the parsed `by_session` structure (see `_parse`), with
        this tick's sample folded in when one was written, so
        `simulate_rate_limits` can thread it straight into
        `_rolling_bucket`/`_fixed_bucket` instead of those re-parsing the
        file that was just read here -- the log is parsed once per render,
        not once per record() call plus once per bucket.
        """
        if not session_id:
            return cls._parse()
        t = now if now is not None else time.time()
        by_session = cls._parse()
        sample = (input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens)
        samples = by_session.get(session_id)
        if samples and samples[-1][1:] == sample:
            return by_session  # unchanged since the last sample -- skip the write
        by_session.setdefault(session_id, []).append((t, *sample))
        # Pruning only runs on a tick that actually writes -- the common
        # (unchanged) tick above returns before this, so most renders never
        # pay for a full prune-rewrite; only the ~4% of ticks with a real
        # change do.
        for sid in list(by_session):
            pruned = [row for row in by_session[sid] if t - row[0] <= keep_seconds]
            if pruned:
                by_session[sid] = pruned
            else:
                del by_session[sid]
        try:
            log = rate_limit_log()
            log.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                f'{ts:.3f} {sid} {i} {cc} {cr} {o}'
                for sid, rows in by_session.items()
                for ts, i, cc, cr, o in rows
            ]
            log.write_text('\n'.join(lines) + '\n')
        except OSError:
            pass
        return by_session

    @classmethod
    def _parse(cls) -> dict[str, list[tuple[float, int, int, int, int]]]:
        """Read and parse the log exactly once: session_id -> sorted
        [(ts, input, cache_creation, cache_read, output), ...]. Tolerates
        malformed lines (skipped) and a missing/unreadable log (empty
        dict), matching the tolerance `usage_since`/`window_anchor` used to
        apply per-call.

        Lines with exactly 3 fields are the legacy `ts session_id
        cumulative_tokens` format from before this fix -- that number was a
        context-size GAUGE (the most recent request's total, not a lifetime
        sum), not interpretable under this scheme, and is skipped rather
        than migrated; retention is at most ~15 days so stale 3-field lines
        age out of the log on their own.

        Both `usage_since` and `window_anchor` derive from this same parsed
        structure, and `record()` reuses it too -- so a whole render parses
        the log at most once (record's dedupe check) and reuses that
        `by_session` for both `_rolling_bucket`/`_fixed_bucket`, instead of
        parsing once per record() call plus once per bucket.
        """
        log = rate_limit_log()
        if not log.exists():
            return {}
        by_session: dict[str, list[tuple[float, int, int, int, int]]] = {}
        try:
            for ln in log.read_text().splitlines():
                parts = ln.split()
                if len(parts) != 6:
                    continue  # includes legacy 3-field lines -- see docstring
                try:
                    ts = float(parts[0])
                    i, cc, cr, o = int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])
                except ValueError:
                    continue
                by_session.setdefault(parts[1], []).append((ts, i, cc, cr, o))
        except OSError:
            return {}
        for samples in by_session.values():
            samples.sort()
        return by_session

    @classmethod
    def usage_since(
        cls,
        window_start: float,
        by_session:   dict[str, list[tuple[float, int, int, int, int]]] | None = None,
    ) -> int:
        """Weighted tokens accrued across ALL sessions since `window_start`.

        5h/7d rate limits are account-wide, not per-session, so this sums a
        per-session delta rather than diffing a single session's log. Each
        line is `ts session_id input cache_creation cache_read output` (each
        component a per-session running total), grouped by session_id:

        - `baseline` is that session's last sample with ts < window_start
          (all-zero if it has none -- the session started inside the
          window, so all of its usage counts).
        - `latest` is that session's last sample overall, but only counted
          if the session has at least one sample with ts >= window_start;
          a session with no in-window samples contributes 0.
        - per-component contribution is `max(0, latest - baseline)`,
          clamped so a truncated/reset running total can't go negative --
          this also means a `/compact` or `/clear`, which drops the
          transcript's lifetime sums, can never register as *negative*
          usage; it just stops contributing until the totals climb again.
        - contributions are weighted by RATE_LIMIT_WEIGHT_* (see there for
          the pricing-ratio assumption) and summed into that session's total.

        Returns the sum of weighted contributions across sessions, rounded
        to the nearest int. `by_session` lets a caller that already parsed
        the log (e.g. `simulate_rate_limits`, via `RateLimitLog.record`)
        reuse that parse instead of re-reading the file; omit it to parse
        fresh.
        """
        if by_session is None:
            by_session = cls._parse()
        total = 0.0
        for samples in by_session.values():
            # samples is sorted by ts (see _parse); bisect straight to the
            # boundary instead of building two filtered copies per session.
            cut = bisect_left(samples, (window_start,))
            if cut >= len(samples):
                continue  # nothing at/after window_start -> contributes 0
            baseline = samples[cut - 1][1:] if cut > 0 else (0, 0, 0, 0)
            latest = samples[-1][1:]
            d_input, d_cache_creation, d_cache_read, d_output = (
                max(0, latest[j] - baseline[j]) for j in range(4)
            )
            total += (
                d_input          * RATE_LIMIT_WEIGHT_INPUT
                + d_cache_creation * RATE_LIMIT_WEIGHT_CACHE_CREATION
                + d_cache_read     * RATE_LIMIT_WEIGHT_CACHE_READ
                + d_output         * RATE_LIMIT_WEIGHT_OUTPUT
            )
        return round(total)

    @classmethod
    def window_anchor(
        cls,
        window_seconds: float,
        now:             float,
        by_session:      dict[str, list[tuple[float, int, int, int, int]]] | None = None,
    ) -> float:
        """Anchor of the current account-wide rolling window.

        A rolling window opens at the first activity that is not already
        inside a live window, runs for the full `window_seconds` regardless
        of how idle it goes, and is shared by every concurrent session
        (this drives both `_rolling_bucket`'s usage_since and its resets_at,
        so they always agree). Walks the account-wide timestamps, sorted
        ascending, advancing past each window that has already elapsed:

        - No samples at all -> the window starts now.
        - Otherwise the earliest sample opens the first window; while `now`
          is at or past that window's end, jump to the next sample at or
          after the end (the first activity of the next window). If no such
          sample exists, the window lapsed with nothing after it -> anchor
          at `now`.

        `by_session` lets a caller that already parsed the log (e.g.
        `simulate_rate_limits`, via `RateLimitLog.record`) reuse that parse
        instead of re-reading the file; omit it to parse fresh.
        """
        if by_session is None:
            by_session = cls._parse()
        timestamps = sorted(row[0] for samples in by_session.values() for row in samples)
        if not timestamps:
            return now
        window_start = timestamps[0]
        i = 0  # single ordered walk over timestamps -- advance past, never rescan
        while now >= window_start + window_seconds:
            while i < len(timestamps) and timestamps[i] < window_start + window_seconds:
                i += 1
            if i >= len(timestamps):
                return now
            window_start = timestamps[i]
        return window_start


# ---------------------------------------------------------------------------
# RenderTiming
# ---------------------------------------------------------------------------

class RenderTiming:
    """Per-session persistence of the last render's wall-clock duration.

    A render can't know its own total time before it has finished drawing, so
    the bottom-border annotation shows the *previous* run's duration: each run
    reads the last value (to display) at the start and writes its own (for the
    next run) at the end. Keyed by session_id in one log file — like
    TokenRate — so panes don't show each other's timings; lines idle longer
    than KEEP are pruned so the file can't grow without bound.
    """

    KEEP = 300.0

    @classmethod
    def read(cls, session_id: str) -> float | None:
        if not session_id:
            return None
        log = render_log()
        if not log.exists():
            return None
        try:
            for ln in log.read_text().splitlines():
                parts = ln.split()
                if len(parts) >= 3 and parts[1] == session_id:
                    return float(parts[2])
        except (OSError, ValueError):
            return None
        return None

    @classmethod
    def write(cls, session_id: str, ms: float) -> None:
        if not session_id:
            return
        log = render_log()
        now = time.time()
        rows: list[str] = []
        if log.exists():
            try:
                for ln in log.read_text().splitlines():
                    parts = ln.split()
                    if len(parts) < 3 or parts[1] == session_id:
                        continue
                    try:
                        ts = float(parts[0])
                    except ValueError:
                        continue
                    if now - ts > cls.KEEP:
                        continue
                    rows.append(ln)
            except OSError:
                pass
        rows.append(f'{now:.3f} {session_id} {ms:.1f}')
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text('\n'.join(rows) + '\n')
        except OSError:
            pass
