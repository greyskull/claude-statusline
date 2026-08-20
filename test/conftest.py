import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from helper import strip_ansi as _strip_ansi
import yas.constants as _sl_constants
import yas.renderer as _sl_renderer
import yas.render.gradient as _sl_gradient

_SRC = Path(__file__).resolve().parent.parent / 'claude' / 'statusline_command.py'


def _hooks_active() -> bool:
    'True if core.hooksPath points at the committed hooks (or git is unavailable — then stay quiet).'
    try:
        result = subprocess.run(
            ['git', 'config', '--local', '--get', 'core.hooksPath'],
            cwd            = _SRC.parent.parent,
            capture_output = True,
            text           = True,
        )
    except OSError:
        return True
    return result.stdout.strip() == '.github/hooks'


def pytest_report_header(config: pytest.Config) -> str | None:
    'Nudge contributors to enable the pre-commit hook, unless on CI or an xdist worker.'
    if hasattr(config, 'workerinput') or os.environ.get('CI') or _hooks_active():
        return None
    return 'NOTE: git pre-commit hooks not active — run `make hooks` (CI runs the same checks on push)'


@pytest.fixture(name='strip_ansi')
def strip_ansi_fixture() -> Callable[[str], str]:
    return _strip_ansi


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> float:
    """Freeze every live clock the render path reads — for byte-identity tests.

    Two back-to-back renders of the same state are otherwise not byte-equal:
    `rainbow_step` (`int(time.time()) % len(RAINBOW_PALETTE)`) rolls the border
    palette on any second boundary, and `Renderer.task_row`'s elapsed timer can
    flip digit width (`9:59` -> `10:00`), which the content-sized plan column
    then propagates into the layout. Shim each module's own `time` reference
    rather than the stdlib, so nothing outside the render path sees a stopped
    clock. Pass the returned instant to `SessionView(..., now)` — that is the
    third live-clock seam.
    """
    now   = time.time()
    clock = SimpleNamespace(time=lambda: now)
    monkeypatch.setattr(_sl_renderer, 'time', clock)
    monkeypatch.setattr(_sl_gradient, 'time', clock)
    return now


@pytest.fixture
def tmp_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    # One patch point, by design: every YAS path is a *function* in
    # yas.constants (tokens_log(), config_path(), ...) that reads the
    # module-global CLAUDE_DIR when called, not a Path frozen at import. Modules
    # import those helpers rather than CLAUDE_DIR itself, so rebinding the single
    # constant here redirects every read and write in the process — including
    # modules imported long before this fixture runs. Adding a new module that
    # touches disk no longer requires touching this fixture; it only has to go
    # through yas.constants like everything else.
    claude_dir = tmp_path / '.claude'
    monkeypatch.setattr(_sl_constants, 'CLAUDE_DIR', claude_dir)
    return tmp_path
