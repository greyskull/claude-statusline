"""BorderRenderer: elbow/pill/fill math for top, bottom, separator, and line borders."""

from __future__ import annotations

from yas.constants import (
    BOX_ARC_BL,
    BOX_ARC_BR,
    BOX_ARC_TL,
    BOX_ARC_TR,
    BOX_CROSS,
    BOX_H,
    BOX_H_DASH,
    BOX_H_DASH2,
    BOX_H_DASH4,
    BOX_T_DOWN,
    BOX_T_LEFT,
    BOX_T_RIGHT,
    BOX_T_UP,
    BOX_V,
    ELLIPSIS,
    BOLD,
    BOLD_OFF,
    ITALIC,
    LABEL_ABBREVIATIONS,
    RESET,
)
from yas.render.gradient import GradientEngine
from yas.render.pill import Pill
from yas.render.text import _visible_width, superscript


def _shrink_to_boundary(text: str, run_len: int) -> str | None:
    """Longest whole-word prefix of `text` (splitting on spaces, so a `/`
    surrounded by spaces is its own token) that, plus a trailing `ELLIPSIS`,
    fits within `run_len` visible columns. Returns `None` if not even the
    first token fits — this is a last-resort shrink, never a mid-word cut.
    """
    tokens = text.split(' ')
    for i in range(len(tokens), 0, -1):
        prefix = ' '.join(tokens[:i])
        cand = superscript(prefix) + ELLIPSIS
        if len(cand) <= run_len:
            return cand
    return None


def _fit_label(text: str, run_len: int) -> str | None:
    """Best renderable form of `text` that fits in `run_len` columns, or
    `None` if the label should be dropped. Tried in order:

    1. The label in full (no shrink needed).
    2. A caller-recognised abbreviation (`LABEL_ABBREVIATIONS`), in full.
    3. A word/separator-boundary-safe shrink of the abbreviation (if one
       exists) or, failing that, of the original text — a shortened prefix
       plus `ELLIPSIS`, built only from whole tokens so it never cuts a word
       in half.
    4. Dropped — no readable form fits this run.
    """
    sup = superscript(text)
    if len(sup) <= run_len:
        return sup
    abbrev = LABEL_ABBREVIATIONS.get(text, '')
    if abbrev:
        sup_abbrev = superscript(abbrev)
        if len(sup_abbrev) <= run_len:
            return sup_abbrev
        shrunk = _shrink_to_boundary(abbrev, run_len)
        if shrunk is not None:
            return shrunk
    return _shrink_to_boundary(text, run_len)


def _overlay_labels(chars: list[str], fills: list[bool], labels: tuple[tuple[str, int], ...]) -> None:
    """Overlay superscript labels onto fill-only columns of a 0-indexed buffer.

    Each label is `(text, start_col)` with `start_col` 1-indexed — the column
    it names, not a fixed print position. Elbows, corners, session id, and
    pill columns are never fill, so they split the border into runs of
    contiguous fill columns that a label can never cross into or overwrite.

    A label whose anchor doesn't sit in a fill run is dropped outright (its
    named column carries no border dash to write on). Otherwise, resolving a
    label happens in two independent steps:

    - **Fit** (`_fit_label`): pick the best renderable form of the label —
      full text, a caller-supplied abbreviation, or a whole-word-boundary
      shrink of either — that fits within the anchor's *containing* fill
      run, or drop the label if nothing readable fits. This never produces a
      mid-word fragment: every candidate is either the complete label/
      abbreviation or a prefix built from whole tokens plus a trailing
      `ELLIPSIS` marker.
    - **Place**: if the fitted text is longer than would remain from the
      anchor to the run's end, shift the write position left (never past the
      run's start, and never past the anchor itself, so the label always
      still covers the column it names) just far enough for the whole thing
      to land inside the run. A label that already fits at its anchor is
      never shifted.

    A column a label writes is itself marked non-fill, so a later label in
    the same call is confined to whatever run remains — it shrinks, shifts,
    or drops against an already-placed label exactly as it would against an
    elbow. Labels are processed in the given order, so anchors should be
    left-to-right. Placement is a pure function of the run's fixed boundaries
    and the label's own text, so it is deterministic and never jitters as
    surrounding widths change by one column.
    """
    n = len(chars)
    for text, start_col in labels:
        idx = start_col - 1
        if idx < 0 or idx >= n or not fills[idx]:
            continue  # anchor itself isn't on a fill column -> drop
        # Contiguous fill run containing the anchor.
        run_start = idx
        while run_start - 1 >= 0 and fills[run_start - 1]:
            run_start -= 1
        run_end = idx
        while run_end + 1 < n and fills[run_end + 1]:
            run_end += 1
        run_len = run_end - run_start + 1
        out = _fit_label(text, run_len)
        if out is None:
            continue  # nothing readable fits this run; drop it
        length = len(out)
        # Shift left just enough to fit, never past the run's start and
        # never past the anchor (the label must still cover its column).
        start = min(idx, run_end - length + 1)
        start = max(start, run_start)
        for offset, g in enumerate(out):
            i = start + offset
            chars[i] = g
            fills[i] = False  # claim the column so later labels yield to it


class BorderRenderer:
    def __init__(self, gradient: GradientEngine):
        self.gradient = gradient
        self.SESSION  = gradient.theme.session

    R = RESET

    def border_top(self, width: int, session_id: str = '', downs: tuple[int, ...] = (), fill: float = 1.0, pill: Pill | None = None, labels: tuple[tuple[str, int], ...] = ()) -> str:
        downs_set = set(downs)
        p = pill or Pill()
        def _ch(col: int) -> str:
            pc = p.border_char(col, 'top')
            if pc:
                return pc
            return BOX_T_DOWN if col in downs_set else BOX_H
        def _clr(col: int, pos: int) -> str:
            if p.active and p.start <= col <= p.end:
                return p.border_fg(col)
            return self.gradient.grad_at(pos, width, fill=fill)
        # Per-column base glyph + fill-only mask (1..width stored 0-indexed). A
        # column is fill only when it is plain '─' (overwritable by a label);
        # corners, elbows, session id, and pill columns are never fill.
        chars: list[str] = [''] * width
        fills: list[bool] = [False] * width
        # Colour prefix per column; session-id run is emitted as one block on
        # its first column so the ordered pass stays byte-identical to before.
        prefix: list[str] = [''] * width
        suffix: list[str] = [''] * width

        if p.active and p.start <= 1:
            prefix[0] = p.border_fg(p.start)
            chars[0] = p.border_char(p.start, 'top')
        else:
            prefix[0] = self.gradient.grad_at(0, width, fill=fill)
            chars[0] = BOX_ARC_TL
        if session_id:
            avail = max(0, width - 4)
            if p.active and p.end == width and p.start > 5:
                avail = max(0, min(avail, p.start - 5))
            sid = session_id if len(session_id) <= avail else session_id[:max(0, avail - 1)] + ELLIPSIS
            sid_w = _visible_width(sid)
            # cols 2 and 3 are fill-form '─'/'┬'/pill; the session id occupies
            # the next sid_w columns as a single coloured italic run.
            for col in (2, 3):
                prefix[col - 1] = _clr(col, col - 1)
                chars[col - 1] = _ch(col)
                fills[col - 1] = (chars[col - 1] == BOX_H)
            prefix[3] = self.SESSION + ITALIC
            chars[3] = sid
            suffix[3 + sid_w - 1] = '\033[23m'
            offset = 3 + sid_w
            rest = max(0, width - 4 - sid_w)
            for i in range(rest):
                col = offset + i + 1
                prefix[col - 1] = _clr(col, offset + i)
                chars[col - 1] = _ch(col)
                fills[col - 1] = (chars[col - 1] == BOX_H)
        else:
            for i in range(1, width - 1):
                col = i + 1
                prefix[col - 1] = _clr(col, i)
                chars[col - 1] = _ch(col)
                fills[col - 1] = (chars[col - 1] == BOX_H)

        if p.active and p.start <= width <= p.end:
            prefix[width - 1] = p.border_fg(width)
            chars[width - 1] = p.border_char(width, 'top')
        else:
            prefix[width - 1] = self.gradient.grad_at(width - 1, width, fill=fill)
            chars[width - 1] = BOX_ARC_TR

        _overlay_labels(chars, fills, labels)

        parts: list[str] = []
        for i in range(width):
            parts += [prefix[i], chars[i], suffix[i]]
        parts.append(self.R)
        return ''.join(parts)

    # Version-tag glyphs sweep from the theme grey (first char) to a
    # brighter-but-still-muted grey (last char) — a quiet ramp that stays
    # legible without shouting pure white against the border.
    VERSION_BRIGHT_RGB = (160, 160, 160)

    def border_bottom(self, width: int, ups: tuple[int, ...] = (), fill: float = 1.0, timing: str = '', version: str = '') -> str:
        ups_set = set(ups)
        chars: list[str] = [BOX_ARC_BL]
        for i in range(width - 2):
            chars.append(BOX_T_UP if (i + 2) in ups_set else BOX_H)
        chars.append(BOX_ARC_BR)
        # Overlay the annotation (`[timing ]version`) right-aligned into the
        # bottom edge, leaving two fill cells before the corner
        # (`…47.2ms┈v0.6.2┈┄╌─╯` — a dashed cell separates the two). Glyphs
        # land only on plain fill columns, so an
        # elbow or the corner is never disturbed and the visible width stays
        # exactly `width`. Version glyphs are remembered so the paint loop can
        # style them (bold, grey→muted-grey gradient, merging into the
        # border's own fill colour once it reaches them) apart from the timing.
        annotation = f'{timing}{BOX_H_DASH4}{version}' if timing and version else (timing or version)
        version_cols: set[int] = set()
        if annotation:
            start = width - 3 - _visible_width(annotation)
            if start >= 1:
                version_from = len(annotation) - len(version) if version else len(annotation)
                for off, g in enumerate(annotation):
                    idx = start + off
                    if 0 <= idx < width and chars[idx] == BOX_H:
                        chars[idx] = g
                        if off >= version_from:
                            version_cols.add(idx)
                # Dashed lead-in/out: up to three fill cells on each side of
                # the annotation ramp between the solid rule and the glyphs
                # (`──╌┄┈47.2ms┈v0.6.2┈┄╌──`, densest dash nearest the text).
                # Only plain fill cells are converted, so an elbow or corner
                # inside the ramp zone stops it short.
                for dist, dash in enumerate((BOX_H_DASH4, BOX_H_DASH, BOX_H_DASH2), 1):
                    left, right = start - dist, start + len(annotation) - 1 + dist
                    if left >= 1 and chars[left] == BOX_H:
                        chars[left] = dash
                    if right < width - 1 and chars[right] == BOX_H:
                        chars[right] = dash
        parts: list[str] = []
        # Same denom/t formula `grad_at` uses internally to decide fill vs
        # off -- reusing it (rather than re-deriving a separate boundary)
        # guarantees the version tag's per-character fill/grey split lands on
        # exactly the same column the border's own fill reaches, no off-by-one.
        denom = max(1, width - 1)
        for i in range(width):
            if i in version_cols:
                if (i / denom) <= fill:
                    # The border's own fill has already reached/passed this
                    # column -- let the glyph merge into the fill colour
                    # instead of the grey sweep, staying bold.
                    clr = self.gradient.grad_at(i, width, fill=fill)
                else:
                    lo, hi = min(version_cols), max(version_cols)
                    u = (i - lo) / max(1, hi - lo)
                    gr, gg, gb = self.gradient.GREY_RGB
                    br, bg, bb = self.VERSION_BRIGHT_RGB
                    vr, vg, vb = (int(gr + (br - gr) * u), int(gg + (bg - gg) * u), int(gb + (bb - gb) * u))
                    clr = f'\033[38;2;{vr};{vg};{vb}m'
                parts += [f'{BOLD}{clr}', chars[i]]
            else:
                clr = self.gradient.grad_at(i, width, fill=fill)
                if (i - 1) in version_cols:
                    clr = BOLD_OFF + clr
                parts += [clr, chars[i]]
        parts.append(self.R)
        return ''.join(parts)

    def border_separator(self, width: int, ups: tuple[int, ...] = (), downs: tuple[int, ...] = (), fill: float = 1.0, labels: tuple[tuple[str, int], ...] = ()) -> str:
        ups_set = set(ups)
        downs_set = set(downs)
        chars: list[str] = [''] * width
        fills: list[bool] = [False] * width
        prefix: list[str] = [''] * width
        prefix[0] = self.gradient.grad_at(0, width, fill=fill)
        chars[0] = BOX_T_RIGHT
        for i in range(width - 2):
            col = i + 2
            if col in downs_set and col in ups_set:
                ch = BOX_CROSS
            elif col in downs_set:
                ch = BOX_T_DOWN
            elif col in ups_set:
                ch = BOX_T_UP
            else:
                ch = BOX_H
            prefix[col - 1] = self.gradient.grad_at(i + 1, width, fill=fill)
            chars[col - 1] = ch
            fills[col - 1] = (ch == BOX_H)
        prefix[width - 1] = self.gradient.grad_at(width - 1, width, fill=fill)
        chars[width - 1] = BOX_T_LEFT
        _overlay_labels(chars, fills, labels)
        parts: list[str] = []
        for i in range(width):
            parts += [prefix[i], chars[i]]
        parts.append(self.R)
        return ''.join(parts)

    DIM_MIN  = 0.6
    DIM_RAMP = 5

    def _dim_for_col(self, col: int, elbow_cols: set[int]) -> float:
        d = min(abs(col - e) for e in elbow_cols)
        if d == 0:
            return 1.0
        return max(self.DIM_MIN, 1.0 - (1.0 - self.DIM_MIN) * (d / self.DIM_RAMP))

    def border_separator_dim(self, width: int, downs: tuple[int, ...] = (), ups: tuple[int, ...] = (), fill: float = 1.0, pill: Pill | None = None, pill_edge: str = 'bottom', labels: tuple[tuple[str, int], ...] = ()) -> str:
        downs_set = set(downs)
        ups_set = set(ups)
        elbow_cols = {1, width} | downs_set | ups_set
        p = pill or Pill()
        edge = pill_edge if pill_edge == 'top' else 'bottom'
        chars: list[str] = [''] * width
        fills: list[bool] = [False] * width
        prefix: list[str] = [''] * width
        if p.active and p.start <= 1:
            prefix[0] = p.border_fg(p.start)
            chars[0] = p.border_char(p.start, edge)
        else:
            prefix[0] = self.gradient.grad_at(0, width, self._dim_for_col(1, elbow_cols), fill=fill)
            chars[0] = BOX_T_RIGHT
        for i in range(width - 2):
            col = i + 2
            pc = p.border_char(col, edge) if p.active else ''
            if pc:
                prefix[col - 1] = p.border_fg(col)
                chars[col - 1] = pc
            else:
                if col in downs_set and col in ups_set:
                    ch = BOX_CROSS
                elif col in downs_set:
                    ch = BOX_T_DOWN
                elif col in ups_set:
                    ch = BOX_T_UP
                else:
                    ch = BOX_H_DASH
                # Per-column dim factor stays baked into the colour prefix, so an
                # overlaid label glyph inherits the same dim for free.
                prefix[col - 1] = self.gradient.grad_at(i + 1, width, self._dim_for_col(col, elbow_cols), fill=fill)
                chars[col - 1] = ch
                fills[col - 1] = (ch == BOX_H_DASH)
        if p.active and p.start <= width <= p.end:
            prefix[width - 1] = p.border_fg(width)
            chars[width - 1] = p.border_char(width, edge)
        else:
            prefix[width - 1] = self.gradient.grad_at(width - 1, width, self._dim_for_col(width, elbow_cols), fill=fill)
            chars[width - 1] = BOX_T_LEFT
        _overlay_labels(chars, fills, labels)
        parts: list[str] = []
        for i in range(width):
            parts += [prefix[i], chars[i]]
        parts.append(self.R)
        return ''.join(parts)

    def border_line(self, content: str, width: int, fill: float = 1.0, bg_lead: str = '', bg_trail: str = '', pill_flush: bool = False, right_pill: str = '') -> str:
        if right_pill:
            pill_w  = _visible_width(right_pill)
            pad     = max(0, width - 2 - _visible_width(content) - pill_w)
            left    = self.gradient.grad_at(0, width, fill=fill)
            lead    = f'{bg_lead} \033[49m' if bg_lead else ' '
            return f'{left}{BOX_V}{self.R}{lead}{content}{" " * pad}{right_pill}{self.R}'
        if pill_flush:
            pad = max(0, width - 1 - _visible_width(content))
            right = self.gradient.grad_at(width - 1, width, fill=fill)
            pad_str = ' ' * pad
            return f'{content}{pad_str}{right}{BOX_V}{self.R}'
        pad = max(0, width - 3 - _visible_width(content))
        left  = self.gradient.grad_at(0, width, fill=fill)
        right = self.gradient.grad_at(width - 1, width, fill=fill)
        lead = f'{bg_lead} \033[49m' if bg_lead else ' '
        if bg_trail and pad > 0:
            pad_str = f'{" " * (pad - 1)}{bg_trail} \033[49m'
        else:
            pad_str = ' ' * pad
        return f'{left}{BOX_V}{self.R}{lead}{content}{pad_str}{right}{BOX_V}{self.R}'
