'''Pins the cold-path/hot-path split in yas.app.main's migration guard:
`yas.migrate` is imported only when version.json is absent, never on the
steady-state path once migrated.
'''
import io
import json
import sys

import yas.app as app
from yas.constants import version_file


def _stdin_for(info: dict) -> io.StringIO:
    return io.StringIO(json.dumps(info))


def _example_info() -> dict:
    from pathlib import Path
    example = Path(__file__).resolve().parent.parent / 'ops' / 'session-info-example.json'
    return json.loads(example.read_text())


def test_main_does_not_import_migrate_when_version_file_exists(tmp_home, monkeypatch):
    sys.modules.pop('yas.migrate', None)  # start from a clean slate

    vf = version_file()
    vf.parent.mkdir(parents=True, exist_ok=True)
    vf.write_text(json.dumps({'schema_version': 1, 'yas_version': '0.0.0', 'migrated_at': 0}))

    monkeypatch.setattr(app, 'terminal_width', lambda: 200)
    monkeypatch.setattr(app.sys, 'stdout', io.StringIO())
    monkeypatch.setattr(app.sys, 'stdin', _stdin_for(_example_info()))

    app.main()

    assert 'yas.migrate' not in sys.modules, 'migrate imported on the hot path'


def test_main_imports_migrate_when_version_file_absent(tmp_home, monkeypatch):
    sys.modules.pop('yas.migrate', None)  # start from a clean slate

    assert not version_file().exists()

    monkeypatch.setattr(app, 'terminal_width', lambda: 200)
    monkeypatch.setattr(app.sys, 'stdout', io.StringIO())
    monkeypatch.setattr(app.sys, 'stdin', _stdin_for(_example_info()))

    app.main()

    assert 'yas.migrate' in sys.modules, 'migrate not imported on the cold path'
    assert version_file().exists()
