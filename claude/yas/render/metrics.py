"""Token-usage metric helpers extracted from statusline_command."""

from __future__ import annotations

import time

from yas.constants import subagent_is_terminal, subagent_status
from yas.render.text import fmt_dur, fmt_tok, fmt_tok_fixed


def fmt_lines_pair(read: int, changed: int, *, width: int = 0, fixed: bool = False) -> tuple[str, str]:
    """Format the Lines Read / Lines Changed numeric pair.

    Shared by the wide tokens/cost row's session-level segment (`width=0`,
    `fixed=False` — a single, non-cohort row where each value renders at its
    own natural `fmt_tok` width, no cross-row alignment needed) and the
    per-subagent row cluster (`width=` the cohort's measured max — see
    `layout.tree_lines_width` — plus `fixed=True`, so every row's digits land
    on the same column AND the same value renders at the same width across
    rows regardless of mantissa digit count; see `fmt_tok_fixed`).

    `width` must be the cohort's *measured* max width, never a hardcoded
    guess: `fmt_tok`/`fmt_tok_fixed`'s own output width varies with magnitude,
    the same "not fixed-width" hazard `fmt_dur`/`subagent_dur_str` already
    carry — assuming a width narrower than what the cohort actually needs
    reintroduces exactly the off-by-one drift that hardcoding `fmt_dur`'s
    width once caused for the duration field.
    """
    fmt = fmt_tok_fixed if fixed else fmt_tok
    read_s    = fmt(read)
    changed_s = fmt(changed)
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


def subagent_cluster_field_offsets(
    lines_w: int, model_w: int, share_w: int, *, tok_w: int = 5,
) -> tuple[int, int, int]:
    """0-indexed offsets, from a tree-single row's `stats_col`, of the lines
    field, the tok(+share) field, and the model field in the fully-populated
    stats cluster (`Renderer.subagent_row`'s ``· lines · tok (share%) · model``).

    Single source of truth for that layout so the SUBAGENTS header's
    'name'/'loc read / written'/'model'/'current activity' labels (built in
    `layout.py`) can anchor over the SAME columns the data rows use, instead
    of a hardcoded guess that drifts the moment `lines_w`/`share_w`/`model_w`
    change per cohort. ``tok_w=5`` is `fmt_tok_fixed`'s own guaranteed max
    width (3 significant figures + 1-char unit suffix, e.g. '7.52M'; below
    1000 it's an unsuffixed int of at most 3 digits) — the same "measure the
    ceiling, not a guess" reasoning `fmt_tok`'s `rjust(6)` already relies on.
    """
    sep          = 3  # ' · ' — space, middle-dot, space
    lines_off    = sep
    lines_full_w = 2 * (1 + 1 + lines_w) + 1  # glyph + space + value, twice, +1 gap
    tok_off      = lines_off + lines_full_w + sep
    tok_full_w   = tok_w + (3 + share_w if share_w else 0)  # ' (' + share + ')'
    model_off    = tok_off + tok_full_w + sep
    return lines_off, tok_off, model_off


def subagent_cluster_width(lines_w: int, model_w: int, share_w: int, *, tok_w: int = 5) -> int:
    """Visible width of the FULLY-populated tree-single stats cluster
    (``· lines · tok (share%) · model``), given the cohort's measured field
    widths.

    Single source of truth for "how much room does the cluster need if
    nothing in it is shed", used by `layout.tree_columns` to decide how much
    of the row's width can go to the (now elastic) description column before
    it would have to start shedding cluster fields — the description
    truncates first under width pressure, the cluster only once the
    description is already at its floor. Mirrors
    `Renderer.subagent_row.build_cluster(True, True, True)`'s plain-text
    width exactly (dot + lines_field + sep + tok(+share) + sep + model); see
    `subagent_cluster_field_offsets` for the matching per-field offsets.
    """
    dot          = 2  # '· '
    sep          = 3  # ' · '
    lines_full_w = 2 * (1 + 1 + lines_w) + 1  # glyph + space + value, twice, +1 gap
    tok_full_w   = tok_w + (3 + share_w if share_w else 0)  # ' (' + share + ')'
    return dot + lines_full_w + sep + tok_full_w + sep + model_w


def subagent_share(sub_inout: int, session_inout: int) -> float | None:
    if session_inout <= 0:
        return None
    return sub_inout / session_inout
