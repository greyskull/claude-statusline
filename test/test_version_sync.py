"""Version-sync guard.

The release version is written in three places that no build step reconciles:
`pyproject.toml` (the packaged version), `claude/yas/constants.py` (the runtime
copy, since ops/install.sh runs the loose files and can't read package
metadata), and `.claude-plugin/plugin.json` (what Claude Code's plugin installer
resolves, and therefore what decides the installed cache path). Drift in the
last one is invisible at runtime -- it only shows up as the installer reporting
a stale "latest version" -- so it gets a test rather than a convention.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from yas.constants import VERSION

REPO = Path(__file__).parent.parent


def _pyproject_version() -> str:
    text = (REPO / 'pyproject.toml').read_text()
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert match, 'no [project] version in pyproject.toml'
    return match.group(1)


def _plugin_version() -> str:
    return json.loads((REPO / '.claude-plugin' / 'plugin.json').read_text())['version']


def test_constants_version_matches_pyproject():
    assert VERSION == _pyproject_version()


def test_plugin_json_version_matches_pyproject():
    # A mismatch here means `make version/bump` failed to rewrite plugin.json,
    # and the plugin installer will keep serving the stale version.
    assert _plugin_version() == _pyproject_version()
