"""Token-usage metric helpers extracted from statusline_command."""

from __future__ import annotations

import time

from yas.constants import subagent_is_terminal, subagent_status
from yas.render.text import fmt_tok, fmt_tok_fixed


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
    fixed width. Formatted as ``M:SS`` (no leading zero on minutes, e.g.
    ``1:29``), rolling to ``H:MM:SS`` at or above one hour — matches the
    **Session Timer** clock convention rather than `fmt_dur`'s ``1m29s``
    style. Not fixed-width — minutes/hours grow an extra digit past 9 (e.g.
    ``9:05`` is 4 chars, ``59:05`` is 5) — so a caller that hardcodes a width
    silently drifts by a column once any row crosses that threshold, which is
    exactly the tree-mode misalignment this guards.
    """
    status = subagent_status(sub)
    if subagent_is_terminal(status):
        dur = max(0.0, sub.end_ts - sub.first_timestamp)  # type: ignore[attr-defined]
    else:
        first_ts = sub.first_timestamp  # type: ignore[attr-defined]
        dur = max(0.0, now - first_ts) if first_ts > 0 else 0.0
    total_s = int(dur)
    minutes, seconds = divmod(total_s, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        clock = f'{hours}:{minutes:02d}:{seconds:02d}'
    else:
        clock = f'{minutes}:{seconds:02d}'
    return clock.rjust(5)


def subagent_type_label(sub: object) -> str:
    """The agent-type text a subagent row displays, including the ``×N``
    resume suffix on a live resumed run.

    Pulled out (like `subagent_dur_str`) so callers that reserve column
    width for the name field — `layout.oneline_name_width` — measure the
    SAME string `Renderer.subagent_row` renders.
    """
    label     = getattr(sub, 'agent_type', '') or '?'
    run_count = getattr(sub, 'run_count', 0)
    is_done   = subagent_is_terminal(subagent_status(sub))
    if not is_done and (getattr(sub, 'resumed', False) or run_count >= 1):
        label = f'{label} ×{run_count + 1}'
    return label


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
    lines_w: int, *, tok_w: int = 5,
) -> tuple[int, int]:
    """0-indexed offsets, from a tree-single row's `stats_col`, of the tok
    field and the lines field in the fully-populated stats cluster
    (`Renderer.subagent_row`'s ``· tok · lines``).

    The model field no longer lives in this cluster — it's embedded in the
    row's front field (`<time> <elbow> <name> <model>`, see
    `layout.tree_columns`'s `model_w` parameter) — so this only covers the
    two fields that remain here.

    Single source of truth for that layout so the agent header's
    'LOC r/w'/'log' labels (built in `layout.py`)
    can anchor over the SAME columns the data rows use, instead of a
    hardcoded guess that drifts the moment `lines_w` changes per cohort.
    ``tok_w=5`` is `fmt_tok_fixed`'s own guaranteed max width (3 significant
    figures + 1-char unit suffix, e.g. '7.52M'; below 1000 it's an
    unsuffixed int of at most 3 digits) — the same "measure the ceiling, not
    a guess" reasoning `fmt_tok`'s `rjust(6)` already relies on. (The
    `(N.N%)` session-share suffix that used to widen the tok field has been
    removed — the field is always exactly `tok_w` wide now.)
    """
    sep          = 3  # ' · ' — space, middle-dot, space
    tok_off      = sep
    lines_off    = tok_off + tok_w + sep
    return tok_off, lines_off


def subagent_cluster_width(lines_w: int, *, tok_w: int = 5) -> int:
    """Visible width of the FULLY-populated tree-single stats cluster
    (``· tok · lines``), given the cohort's measured lines-field width.

    The model field is no longer part of this cluster (it moved into the
    row's front field — see `layout.tree_columns`'s `model_w` parameter).
    Single source of truth for "how much room does the (remaining) cluster
    need if nothing in it is shed", used by `layout.tree_columns` to decide
    how much of the row's width can go to the (now elastic) description
    column before it would have to start shedding cluster fields — the
    description truncates first under width pressure, the cluster only once
    the description is already at its floor. Mirrors
    `Renderer.subagent_row.build_cluster(True, True)`'s plain-text width
    exactly (dot + tok + sep + lines_field); see
    `subagent_cluster_field_offsets` for the matching per-field offsets.
    """
    dot          = 2  # '· '
    sep          = 3  # ' · '
    # <read> + ' / ' + <changed>, each side `lines_w` wide.
    lines_full_w = lines_w + 3 + lines_w
    return dot + tok_w + sep + lines_full_w
