"""One-shot migration from the pre-0.9 flat ~/.claude layout to the yas/
cache+state layout defined in yas.constants.

# REMOVE AFTER 0.11.0
This module (and the `app.main` startup guard that calls it) exists only to
carry users forward from the flat pre-0.9 layout onto the new yas/cache,
yas/state tree. Once a few releases have passed and the flat layout is no
longer expected in the wild, delete this module and its call site.
"""

from __future__ import annotations
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from yas import constants
from yas.constants import (
    LAYOUT_SCHEMA_VERSION,
    VERSION,
    cache_dir,
    last_prompt_path,
    runtime_dir,
    sessions_dir,
    signals_dir,
    state_dir,
    terminal_width_path,
    tokens_log,
    version_file,
)

# Legacy basename -> new-path callable (not a precomputed Path) so a patched
# constants.CLAUDE_DIR is honoured both for the legacy source (resolved at
# call time in migrate() below) and the destination.
_MOVES: tuple[tuple[str, Callable[[], Path]], ...] = (
    ('statusline-tokens.log', tokens_log),
    ('yas-last-prompt.json', last_prompt_path),
    ('terminal-width', terminal_width_path),
)

# Legacy files with no new-layout home — deleted outright. Note:
# 'statusline-theme' is handled separately below (guarded on yas.toml already
# carrying the folded value): folding its value into yas.toml is the
# installer's job, not this module's, and this module must never delete the
# only copy of a theme choice that failed to fold.
_DELETE_FILES: tuple[str, ...] = (
    'statusline-token-rate.log',
    'statusline-render.log',
    'yas.toml.cache',
)

_DELETE_DIRS: tuple[str, ...] = (
    'statusline-output',
)


def _move(src: Path, dst: Path, *, verbose: bool = False) -> None:
    """Move src to dst, skipping (never clobbering) when dst already exists."""
    if dst.exists():
        return
    if not src.exists():
        return
    os.rename(src, dst)
    if verbose:
        try:
            rel_dst = dst.relative_to(constants.CLAUDE_DIR)
        except ValueError:
            rel_dst = dst
        print(f'  moved {src.name} -> {rel_dst}')


def migrate(verbose: bool = False) -> bool:
    """Convert a pre-0.9 flat ~/.claude layout into the yas/cache, yas/state
    tree. Every step is individually idempotent (mkdir exist_ok, moves skip
    an existing destination, deletes tolerate a missing source), so this is
    safe to call on every startup. Returns True only if every step succeeded;
    on any OSError, version.json is left unwritten so the next run retries.

    When verbose is True, each move/delete that actually acts on an existing
    legacy path prints a one-line summary to stdout; no-ops (legacy path
    absent) stay silent. Defaults to False so the lazy first-render call in
    app.py never spams a normal user's statusline.
    """
    ok = True

    for d in (cache_dir(), state_dir(),
              runtime_dir(), signals_dir(), sessions_dir()):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            ok = False

    # Legacy sources are resolved from constants.CLAUDE_DIR at call time
    # (rather than a precomputed module-level Path) so this module respects
    # test/tooling patches of constants.CLAUDE_DIR the same way the new-path
    # callables above do — the sanctioned exception to the "no module
    # imports CLAUDE_DIR" rule, since this is a call-time attribute read.
    for name, dst_fn in _MOVES:
        try:
            _move(constants.CLAUDE_DIR / name, dst_fn(), verbose=verbose)
        except OSError:
            ok = False

    for name in _DELETE_FILES:
        src = constants.CLAUDE_DIR / name
        try:
            existed = src.exists()
            src.unlink(missing_ok=True)
            if verbose and existed:
                print(f'  removed {name}')
        except OSError:
            ok = False

    for name in _DELETE_DIRS:
        src = constants.CLAUDE_DIR / name
        try:
            existed = src.exists()
            shutil.rmtree(src, ignore_errors=True)
            if verbose and existed:
                print(f'  removed {name}')
        except OSError:
            ok = False

    # Only retire the legacy statusline-theme file once yas.toml already
    # carries a `theme =` line — ops/install.sh's fold_legacy_theme() writes
    # that line before this runs. If yas.toml has no theme line (fold failed
    # its parse-validation, or no yas.toml exists), keep the legacy file in
    # place rather than losing the user's theme choice outright.
    toml_path = constants.CLAUDE_DIR / 'yas.toml'
    try:
        has_theme = toml_path.exists() and bool(
            re.search(r'(?m)^[ \t]*theme[ \t]*=', toml_path.read_text())
        )
    except OSError:
        has_theme = False
    if has_theme:
        theme_path = constants.CLAUDE_DIR / 'statusline-theme'
        try:
            existed = theme_path.exists()
            theme_path.unlink(missing_ok=True)
            if verbose and existed:
                print('  removed statusline-theme')
        except OSError:
            ok = False

    if ok:
        payload = json.dumps({
            'schema_version': LAYOUT_SCHEMA_VERSION,
            'yas_version': VERSION,
            'migrated_at': time.time(),
        })
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=state_dir(), prefix='.version-', suffix='.tmp')
            with os.fdopen(fd, 'w') as f:
                f.write(payload)
            os.replace(tmp_path, version_file())
        except OSError:
            ok = False
            if tmp_path is not None:
                Path(tmp_path).unlink(missing_ok=True)

    return ok


if __name__ == '__main__':
    _verbose = '--verbose' in sys.argv[1:] or os.environ.get('YAS_MIGRATE_VERBOSE') == '1'
    raise SystemExit(0 if migrate(verbose=_verbose) else 1)
