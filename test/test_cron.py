"""Unit tests for yas.cron's narrow 5-field subset (*, N, N,N, */n)."""

from __future__ import annotations

from datetime import datetime

import pytest

from yas.cron import CronSchedule


def test_wildcard_every_minute_matches_anything() -> None:
    sched = CronSchedule.parse('* * * * *')
    assert sched._matches(datetime(2026, 1, 1, 0, 0))
    assert sched._matches(datetime(2026, 6, 15, 13, 47))


def test_plain_int_fields_match_only_exact_value() -> None:
    sched = CronSchedule.parse('30 6 1 1 *')
    assert sched._matches(datetime(2026, 1, 1, 6, 30))
    assert not sched._matches(datetime(2026, 1, 1, 6, 31))
    assert not sched._matches(datetime(2026, 1, 2, 6, 30))


def test_comma_list_matches_any_listed_value() -> None:
    sched = CronSchedule.parse('0,30 * * * *')
    assert sched._matches(datetime(2026, 3, 3, 9, 0))
    assert sched._matches(datetime(2026, 3, 3, 9, 30))
    assert not sched._matches(datetime(2026, 3, 3, 9, 15))


def test_step_field_matches_every_nth_value() -> None:
    sched = CronSchedule.parse('*/15 * * * *')
    assert sched.minutes == {0, 15, 30, 45}


def test_weekly_epoch_next_and_prev_firing() -> None:
    # "0 0 * * 0" = every Sunday at midnight. Cron weekday 0 == Sunday.
    sched = CronSchedule.parse('0 0 * * 0')
    # 2026-08-28 is a Friday.
    now = datetime(2026, 8, 28, 12, 0)
    nxt = sched.next_after(now)
    assert nxt > now
    assert nxt.weekday() == 6  # Sunday in datetime.weekday() terms
    assert (nxt.hour, nxt.minute) == (0, 0)

    prev = sched.prev_at_or_before(now)
    assert prev <= now
    assert prev.weekday() == 6
    assert (prev.hour, prev.minute) == (0, 0)
    assert prev < nxt


@pytest.mark.parametrize('expr', [
    '0 0 * * 0 extra',      # wrong field count
    '@weekly',
    '1-5 * * * *',          # ranges unsupported
    'L * * * *',
    '0 0 * * MON',          # day-name aliases unsupported
    '99 * * * *',           # out of range
    '*/0 * * * *',          # zero step
])
def test_rejects_unsupported_grammar(expr: str) -> None:
    with pytest.raises(ValueError):
        CronSchedule.parse(expr)


def test_prev_and_next_bracket_a_matching_instant() -> None:
    sched = CronSchedule.parse('0 */6 * * *')  # every 6 hours on the hour
    now = datetime(2026, 4, 10, 8, 0)
    nxt  = sched.next_after(now)
    prev = sched.prev_at_or_before(now)
    assert nxt == datetime(2026, 4, 10, 12, 0)
    assert prev == datetime(2026, 4, 10, 6, 0)
