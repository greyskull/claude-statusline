"""A narrow, hand-rolled subset of 5-field cron for the [rate_limits] `epoch`.

Supported per field: `*`, a plain non-negative int, a comma list of ints
(`1,3,5`), and a step (`*/n`). No ranges (`1-5`), no named specials
(`@weekly`), no day-name/month-name aliases, no `L`/`W`/`#`. Anything outside
that grammar raises ValueError with a message naming the offending field —
callers (yas.config) surface it as a load-time error rather than silently
guessing a schedule.
"""

from __future__ import annotations

from datetime import datetime, timedelta

FIELD_NAMES  = ('minute', 'hour', 'day', 'month', 'weekday')
FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def _parse_field(raw: str, lo: int, hi: int, name: str) -> set[int]:
    raw = raw.strip()
    if raw == '*':
        return set(range(lo, hi + 1))
    if raw.startswith('*/'):
        step_s = raw[2:]
        if not step_s.isdigit() or int(step_s) <= 0:
            raise ValueError(f'cron {name}: bad step {raw!r}')
        step = int(step_s)
        return set(range(lo, hi + 1, step))
    out: set[int] = set()
    for part in raw.split(','):
        part = part.strip()
        if not part.lstrip('-').isdigit() or part.startswith('-'):
            raise ValueError(f'cron {name}: unsupported token {part!r} (only *, N, N,N, */N)')
        n = int(part)
        if not (lo <= n <= hi):
            raise ValueError(f'cron {name}: {n} out of range [{lo},{hi}]')
        out.add(n)
    if not out:
        raise ValueError(f'cron {name}: empty field {raw!r}')
    return out


class CronSchedule:
    """A parsed 5-field cron expression (minute hour day month weekday)."""

    __slots__ = ('minutes', 'hours', 'days', 'months', 'weekdays')

    def __init__(
        self,
        minutes:  set[int],
        hours:    set[int],
        days:     set[int],
        months:   set[int],
        weekdays: set[int],
    ) -> None:
        self.minutes  = minutes
        self.hours    = hours
        self.days     = days
        self.months   = months
        self.weekdays = weekdays

    @classmethod
    def parse(cls, expr: str) -> CronSchedule:
        fields = expr.strip().split()
        if len(fields) != 5:
            raise ValueError(f'cron: expected 5 fields, got {len(fields)} in {expr!r}')
        parsed = [
            _parse_field(f, lo, hi, name)
            for f, (lo, hi), name in zip(fields, FIELD_RANGES, FIELD_NAMES)
        ]
        return cls(*parsed)

    def _matches(self, dt: datetime) -> bool:
        # weekday: cron 0=Sunday..6=Saturday; datetime.weekday() is 0=Monday.
        cron_weekday = (dt.weekday() + 1) % 7
        return (
            dt.minute in self.minutes
            and dt.hour in self.hours
            and dt.day in self.days
            and dt.month in self.months
            and cron_weekday in self.weekdays
        )

    def next_after(self, after: datetime, horizon_minutes: int = 366 * 24 * 60) -> datetime:
        """The earliest firing strictly after `after`, minute-resolution.

        A brute-force minute walk is fine here: the field grammar is narrow
        (no ranges), schedules are sparse, and horizon_minutes (~1 year)
        bounds the search so a pathological config fails loud instead of
        hanging.
        """
        candidate = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
        for _ in range(horizon_minutes):
            if self._matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise ValueError('cron: no firing found within horizon')

    def prev_at_or_before(self, at: datetime, horizon_minutes: int = 366 * 24 * 60) -> datetime:
        """The latest firing at-or-before `at`, minute-resolution."""
        candidate = at.replace(second=0, microsecond=0)
        for _ in range(horizon_minutes):
            if self._matches(candidate):
                return candidate
            candidate -= timedelta(minutes=1)
        raise ValueError('cron: no firing found within horizon')
