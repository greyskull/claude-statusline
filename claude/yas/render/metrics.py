"""Token-usage metric helpers extracted from statusline_command."""

from __future__ import annotations

import time

from yas.constants import subagent_is_terminal, subagent_status
from yas.render.text import fmt_dur, fmt_tok


def fmt_lines_pair(read: int, changed: int, *, width: int = 0) -> tuple[str, str]:
    """Format the Lines Read / Lines Changed numeric pair.

    Shared by the wide tokens/cost row's session-level segment (`width=0` —
    a single, non-cohort row where each value renders at its own natural
    width, no cross-row alignment needed) and the per-subagent tree-row
    cluster (`width=` the cohort's measured max — see `layout.tree_lines_width`
    — so every row's digits land on the same column regardless of how many
    digits any one row's count happens to have).

    `width` must be the cohort's *measured* max `fmt_tok` width, never a
    hardcoded guess: `fmt_tok`'s own output width varies from 1 char ('0') to
    6 ('999.9B'), the same "not fixed-width" hazard `fmt_dur`/`subagent_dur_str`
    already carry — assuming a width narrower than what the cohort actually
    needs reintroduces exactly the off-by-one drift that hardcoding `fmt_dur`'s
    width once caused for the duration field.
    """
    read_s    = fmt_tok(read)
    changed_s = fmt_tok(changed)
    if width:
        read_s    = read_s.rjust(width)
        changed_s = changed_s.rjust(width)
    return read_s, changed_s


def subagent_dur_str(sub: object, now: float) -> str:
    """The right-justified elapsed-time string a subagent row displays.

    Pulled out as its own function so every caller that needs to reserve
    column width for the duration field (`layout.tree_columns`,
    `Renderer.subagent_row`) measures the SAME string instead of assuming a
    fixed width. `fmt_dur` is not fixed-width — minutes/hours grow an extra
    digit past 9 (e.g. '3m36s' is 5 chars, '40m23s' is 6) — so a caller that
    hardcodes '5' silently drifts by a column once any row crosses that
    threshold, which is exactly the tree-mode misalignment this guards.
    """
    status = subagent_status(sub)
    if subagent_is_terminal(status):
        dur = max(0.0, sub.end_ts - sub.first_timestamp)  # type: ignore[attr-defined]
    else:
        first_ts = sub.first_timestamp  # type: ignore[attr-defined]
        dur = max(0.0, now - first_ts) if first_ts > 0 else 0.0
    return fmt_dur(dur).rjust(5)


def burndown_delta(
    used_pct: float,
    resets_at: int,
    window_minutes: int,
    warmup_minutes: int,
    now: float | None = None,
) -> float | None:
    if not resets_at:
        return None
    t = now if now is not None else time.time()
    if t >= resets_at:
        return None
    window_start_ts = resets_at - window_minutes * 60
    elapsed_minutes = (t - window_start_ts) / 60
    if elapsed_minutes < warmup_minutes:
        return None
    ideal_pct = (elapsed_minutes / window_minutes) * 100
    return used_pct - ideal_pct


def subagent_avg_tpm(
    total_input: int,
    output: int,
    first_timestamp: float,
    now: float,
    floor_seconds: float = 3.0,
) -> int | None:
    if first_timestamp == 0 or now - first_timestamp < floor_seconds:
        return None
    return round((total_input + output) / ((now - first_timestamp) / 60))


def subagent_share(sub_inout: int, session_inout: int) -> float | None:
    if session_inout <= 0:
        return None
    return sub_inout / session_inout
