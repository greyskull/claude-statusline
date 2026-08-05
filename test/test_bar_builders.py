import re

import yas.renderer as renderer
from yas.constants import BarChars, BG_LUM_THRESHOLD
from yas.render.text import _visible_width
from yas.themes import THEMES
from helper import strip_ansi


_r = renderer.Renderer()


_FG_RGB = re.compile(r'\x1b\[38;2;(\d+);(\d+);(\d+)m')
_FG_256 = re.compile(r'\x1b\[38;5;(\d+)m')


def _luminance(seq: str) -> int:
    """Luma (0-255) of the first SGR foreground colour in `seq`.

    Same weights as the pill foreground flip in `render/gradient.py`, so the
    test measures brightness with the yardstick the renderer itself uses.
    """
    m = _FG_RGB.search(seq)
    if m:
        r, g, b = (int(x) for x in m.groups())
        return (r * 299 + g * 587 + b * 114) // 1000
    m = _FG_256.search(seq)
    assert m, f'no foreground colour in {seq!r}'
    n = int(m.group(1))
    # xterm greyscale ramp: index 232..255 → grey level 8..238 in steps of 10.
    assert 232 <= n <= 255, f'expected a greyscale index for a bar track, got {n}'
    return 8 + (n - 232) * 10


def test_gradient_bar_zero_fill_is_empty() -> None:
    assert _r.gradient_bar(0, 30) == ''


def test_gradient_bar_visible_width() -> None:
    # filled=5 → 5 FILLED glyphs + 1 MID leading-edge glyph = 6 visible chars
    result = _r.gradient_bar(5, 30)
    stripped = strip_ansi(result)
    assert _visible_width(stripped) == 6


def test_gradient_bar_mid_glyph_has_no_background() -> None:
    # Filled cells are painted as space-on-BG for gapless coverage, but the MID
    # leading-edge cap glyph must carry no background: it's emitted after a BG
    # reset (\x1b[49m), so everything from that reset on is BG-free.
    result = _r.gradient_bar(5, 30)
    after_reset = result.split('\x1b[49m', 1)[1]
    assert BarChars.MID in after_reset
    assert '\x1b[48;' not in after_reset


def test_empty_section_fades_leading_chars() -> None:
    # First 3 empty chars ramp from a darker shade up to BAR_EMPTY; remainder
    # share BAR_EMPTY. Smaller `empty` only emits the ramp prefix.
    full = _r._empty_section(10)
    fade = _r._empty_fade_colors()
    for step in fade:
        assert step in full
    assert _r.BAR_EMPTY in full
    assert strip_ansi(full) == BarChars.EMPTY * 10
    assert _r._empty_section(0) == ''
    short = strip_ansi(_r._empty_section(2))
    assert short == BarChars.EMPTY * 2


def test_empty_fade_ramps_toward_background_for_every_theme() -> None:
    # The ramp softens the fill→empty seam by starting *dim* and rising to
    # BAR_EMPTY, where "dim" means closer to the terminal background. On a dark
    # theme that is darker; on a light theme darker is the wrong way — it walks
    # away from a pale background and lands as a dark smudge beside the fill.
    # So each step must sit on the background side of the track it joins.
    for name, theme in THEMES.items():
        r = renderer.Renderer(theme=theme)
        track = _luminance(r.BAR_EMPTY)
        for step in r._empty_fade_colors():
            if track >= BG_LUM_THRESHOLD:
                assert _luminance(step) >= track, (
                    f'{name}: fade step {step!r} is darker than its pale '
                    f'track {r.BAR_EMPTY!r} — reads as a smudge on a light bg'
                )
            else:
                assert _luminance(step) <= track, (
                    f'{name}: fade step {step!r} is lighter than its dark '
                    f'track {r.BAR_EMPTY!r}'
                )


def test_spec_gradient_bar_idx_wraps() -> None:
    palette_len = len(renderer.Renderer.SPEC_GRADIENTS)
    result_zero = strip_ansi(_r.spec_gradient_bar(3, 30, idx=0))
    result_wrap = strip_ansi(_r.spec_gradient_bar(3, 30, idx=palette_len))
    assert result_zero == result_wrap


def test_spec_gradient_bar_content_is_heavy_glyphs() -> None:
    # After stripping ANSI, should be 3 HEAVY glyphs
    stripped = strip_ansi(_r.spec_gradient_bar(3, 30, idx=0))
    assert stripped == BarChars.HEAVY * 3
