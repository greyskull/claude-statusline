'''Guards the new-layout invariant: a render tick must never scatter files
directly into $CLAUDE_CONFIG_DIR beyond the yas/ subtree and yas.toml.
'''
import json
from pathlib import Path

from yas.app import render

_EXAMPLE = Path(__file__).resolve().parent.parent / 'ops' / 'session-info-example.json'


def _load_example() -> dict:
    return json.loads(_EXAMPLE.read_text())


def test_render_tick_only_touches_yas_and_yas_toml(tmp_home):
    claude_dir = tmp_home / '.claude'
    info = _load_example()

    result = render(info, 160)
    assert isinstance(result, str)
    assert len(result) > 0

    # claude_dir may not even exist yet if the render wrote nothing to disk;
    # that's still a trivially-satisfied containment.
    if not claude_dir.exists():
        return

    entries = {p.name for p in claude_dir.iterdir()}
    assert entries <= {'yas', 'yas.toml'}, f'stray entries directly under $CLAUDE_CONFIG_DIR: {entries}'
