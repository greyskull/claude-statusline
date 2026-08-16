'''Tests for the pre-0.9 -> yas/{cache,state} layout migration.

Covers the full nine-path migration, idempotency, move-skips-existing-dest,
version.json shape, crash-resume, and the empty-config-dir case.
'''
import json
import time

from yas.constants import (
    LAYOUT_SCHEMA_VERSION, VERSION,
    cache_dir, runtime_dir, sessions_dir, signals_dir, state_dir,
    version_file,
)
from yas.migrate import _DELETE_DIRS, _DELETE_FILES, _MOVES, migrate


def _seed_legacy(claude_dir):
    '''Populate claude_dir with all nine legacy paths this module knows about.'''
    claude_dir.mkdir(parents=True, exist_ok=True)
    for name, _dst_fn in _MOVES:
        (claude_dir / name).write_text(f'content-of-{name}')
    for name in _DELETE_FILES:
        (claude_dir / name).write_text(f'content-of-{name}')
    for name in _DELETE_DIRS:
        d = claude_dir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / 'marker').write_text('dead weight')


def test_full_migration_moves_land_with_contents(tmp_home):
    claude_dir = tmp_home / '.claude'
    _seed_legacy(claude_dir)

    assert migrate() is True

    for name, dst_fn in _MOVES:
        dst = dst_fn()
        assert dst.exists(), f'{name} did not land at {dst}'
        assert dst.read_text() == f'content-of-{name}'
        assert not (claude_dir / name).exists()


def test_full_migration_deletes_are_gone(tmp_home):
    claude_dir = tmp_home / '.claude'
    _seed_legacy(claude_dir)

    assert migrate() is True

    for name in _DELETE_FILES:
        assert not (claude_dir / name).exists()
    for name in _DELETE_DIRS:
        assert not (claude_dir / name).exists()


def test_full_migration_creates_six_dirs(tmp_home):
    claude_dir = tmp_home / '.claude'
    _seed_legacy(claude_dir)

    assert migrate() is True

    for d in (cache_dir(), state_dir(),
              runtime_dir(), signals_dir(), sessions_dir()):
        assert d.is_dir(), f'{d} was not created'


def test_migration_is_idempotent(tmp_home):
    claude_dir = tmp_home / '.claude'
    _seed_legacy(claude_dir)

    assert migrate() is True
    assert migrate() is True  # second run: nothing left to move/delete, still succeeds

    for name, dst_fn in _MOVES:
        dst = dst_fn()
        assert dst.exists()
        assert dst.read_text() == f'content-of-{name}'


def test_move_skipped_when_destination_exists(tmp_home):
    claude_dir = tmp_home / '.claude'
    _seed_legacy(claude_dir)

    # Pre-create every destination with sentinel content so a clobber is
    # observable, then confirm the legacy source content never lands.
    for name, dst_fn in _MOVES:
        dst = dst_fn()
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(f'preexisting-{name}')

    assert migrate() is True  # must not raise on an existing destination

    for name, dst_fn in _MOVES:
        dst = dst_fn()
        assert dst.read_text() == f'preexisting-{name}', 'destination was clobbered'


def test_version_file_shape(tmp_home):
    claude_dir = tmp_home / '.claude'
    _seed_legacy(claude_dir)

    before = time.time()
    assert migrate() is True
    after = time.time()

    payload = json.loads(version_file().read_text())
    assert payload['schema_version'] == LAYOUT_SCHEMA_VERSION
    assert payload['schema_version'] == 1
    assert payload['yas_version'] == VERSION
    assert isinstance(payload['migrated_at'], (int, float))
    assert before <= payload['migrated_at'] <= after


def test_crash_resume(tmp_home):
    claude_dir = tmp_home / '.claude'
    _seed_legacy(claude_dir)

    assert migrate() is True
    version_file().unlink()  # simulate a crash between the writes and the version stamp

    assert migrate() is True
    assert version_file().exists()

    for name, dst_fn in _MOVES:
        dst = dst_fn()
        assert dst.exists()
        assert dst.read_text() == f'content-of-{name}'
    for name in _DELETE_FILES:
        assert not (claude_dir / name).exists()
    for name in _DELETE_DIRS:
        assert not (claude_dir / name).exists()


def test_empty_config_dir_writes_marker_without_error(tmp_home):
    # No legacy files at all — every move/delete is a no-op, but the dirs
    # still get created and version.json still gets written.
    assert migrate() is True
    assert version_file().exists()

    for d in (cache_dir(), state_dir(),
              runtime_dir(), signals_dir(), sessions_dir()):
        assert d.is_dir()
