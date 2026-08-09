"""Tests for OpenSpec._find_root and OpenSpec.from_cwd."""
from pathlib import Path

import yas.info.openspec as openspec_mod


def test_find_root_walks_upward(tmp_path: Path) -> None:
    """_find_root walks up from a subdirectory to find openspec/."""
    openspec_dir = tmp_path / 'openspec' / 'specs'
    openspec_dir.mkdir(parents=True)
    sub = tmp_path / 'sub'
    sub.mkdir()

    result = openspec_mod.OpenSpec._find_root(str(sub))
    assert result == str(tmp_path / 'openspec')


def test_find_root_no_openspec_returns_empty(tmp_path: Path) -> None:
    """_find_root returns '' when no openspec/ directory is found."""
    result = openspec_mod.OpenSpec._find_root(str(tmp_path))
    assert result == ''


def test_counts_open_and_done_tasks(tmp_path: Path) -> None:
    """from_cwd counts - [ ] and - [x] lines per tasks.md."""
    changes_dir = tmp_path / 'openspec' / 'changes' / 'my-change'
    changes_dir.mkdir(parents=True)
    (changes_dir / 'tasks.md').write_text(
        '- [ ] one\n'
        '- [x] two\n'
        '- [x] three\n'
    )

    result = openspec_mod.OpenSpec.from_cwd(str(tmp_path))
    assert len(result.changes) == 1
    name, done, total = result.changes[0]
    assert name == 'my-change'
    assert done == 2
    assert total == 3


def test_archived_changes_excluded(tmp_path: Path) -> None:
    """Changes under /archive/ are excluded from results."""
    archive_dir = tmp_path / 'openspec' / 'changes' / 'archive' / 'old-change'
    archive_dir.mkdir(parents=True)
    (archive_dir / 'tasks.md').write_text('- [ ] task\n')

    result = openspec_mod.OpenSpec.from_cwd(str(tmp_path))
    assert result.changes == []


def test_empty_tasks_excluded(tmp_path: Path) -> None:
    """A tasks.md with no checkbox lines is excluded from results."""
    changes_dir = tmp_path / 'openspec' / 'changes' / 'empty-change'
    changes_dir.mkdir(parents=True)
    (changes_dir / 'tasks.md').write_text('# No tasks here\nJust prose.\n')

    result = openspec_mod.OpenSpec.from_cwd(str(tmp_path))
    assert result.changes == []


def test_from_cwd_no_openspec_anywhere(tmp_path: Path) -> None:
    """No openspec/ found upward or downward returns an empty OpenSpec."""
    (tmp_path / 'sub' / 'deeper').mkdir(parents=True)

    result = openspec_mod.OpenSpec.from_cwd(str(tmp_path))
    assert result.changes == []


def test_from_cwd_finds_nested_repo_downward(tmp_path: Path) -> None:
    """A repo nested below cwd (monorepo-of-repos) is found by the downward scan."""
    changes_dir = tmp_path / 'repo-a' / 'openspec' / 'changes' / 'add-thing'
    changes_dir.mkdir(parents=True)
    (changes_dir / 'tasks.md').write_text('- [ ] one\n- [x] two\n')

    result = openspec_mod.OpenSpec.from_cwd(str(tmp_path))
    assert len(result.changes) == 1
    name, done, total = result.changes[0]
    assert name == 'add-thing'
    assert done == 1
    assert total == 2


def test_from_cwd_finds_multiple_nested_repos(tmp_path: Path) -> None:
    """Multiple nested openspec/ roots are all found and their change names
    are prefixed with the owning repo directory to avoid collisions."""
    for repo in ('repo-a', 'repo-b'):
        changes_dir = tmp_path / repo / 'openspec' / 'changes' / 'add-thing'
        changes_dir.mkdir(parents=True)
        (changes_dir / 'tasks.md').write_text('- [ ] one\n')

    result = openspec_mod.OpenSpec.from_cwd(str(tmp_path))
    names = sorted(n for n, _, _ in result.changes)
    assert names == ['repo-a/add-thing', 'repo-b/add-thing']


def test_from_cwd_skips_ignored_dirs(tmp_path: Path) -> None:
    """The downward scan never descends into .git, node_modules, venv, etc."""
    for ignored in ('.git', 'node_modules', 'venv', '.venv', '__pycache__'):
        changes_dir = tmp_path / ignored / 'openspec' / 'changes' / 'hidden-change'
        changes_dir.mkdir(parents=True)
        (changes_dir / 'tasks.md').write_text('- [ ] one\n')

    result = openspec_mod.OpenSpec.from_cwd(str(tmp_path))
    assert result.changes == []


def test_from_cwd_merges_upward_and_downward_roots(tmp_path: Path) -> None:
    """cwd sitting inside a repo (found upward) plus a nested repo below it
    (found downward) both contribute changes, without duplication."""
    own_changes = tmp_path / 'openspec' / 'changes' / 'own-change'
    own_changes.mkdir(parents=True)
    (own_changes / 'tasks.md').write_text('- [ ] one\n')

    sub = tmp_path / 'sub'
    sub.mkdir()
    nested_changes = sub / 'nested-repo' / 'openspec' / 'changes' / 'nested-change'
    nested_changes.mkdir(parents=True)
    (nested_changes / 'tasks.md').write_text('- [ ] one\n')

    result = openspec_mod.OpenSpec.from_cwd(str(sub))
    names = sorted(n for n, _, _ in result.changes)
    assert names == sorted([
        'nested-repo/nested-change',
        f'{tmp_path.name}/own-change',
    ])


def test_scan_downward_depth_boundary(tmp_path: Path) -> None:
    """A repo exactly 1 level below cwd is found; one 2 levels below is not."""
    shallow = tmp_path / 'repo-a' / 'openspec' / 'changes' / 'shallow-change'
    shallow.mkdir(parents=True)
    (shallow / 'tasks.md').write_text('- [ ] one\n')

    deep = tmp_path / 'group' / 'repo-b' / 'openspec' / 'changes' / 'deep-change'
    deep.mkdir(parents=True)
    (deep / 'tasks.md').write_text('- [ ] one\n')

    result = openspec_mod.OpenSpec.from_cwd(str(tmp_path))
    names = sorted(n for n, _, _ in result.changes)
    assert names == ['shallow-change']


def test_scan_downward_respects_max_depth(tmp_path: Path) -> None:
    """openspec/ nested deeper than max_depth is not found."""
    deep = tmp_path
    for i in range(8):
        deep = deep / f'd{i}'
    changes_dir = deep / 'openspec' / 'changes' / 'too-deep'
    changes_dir.mkdir(parents=True)
    (changes_dir / 'tasks.md').write_text('- [ ] one\n')

    result = openspec_mod.OpenSpec.from_cwd(str(tmp_path))
    assert result.changes == []


def test_from_cwd_max_depth_2_finds_repo_two_levels_down(tmp_path: Path) -> None:
    """max_depth=2 (repo-levels) finds a repo 2 levels below cwd, which the
    default max_depth=1 does not."""
    deep = tmp_path / 'group' / 'repo-b' / 'openspec' / 'changes' / 'deep-change'
    deep.mkdir(parents=True)
    (deep / 'tasks.md').write_text('- [ ] one\n')

    default_result = openspec_mod.OpenSpec.from_cwd(str(tmp_path))
    assert default_result.changes == []

    deep_result = openspec_mod.OpenSpec.from_cwd(str(tmp_path), max_depth=2)
    names = sorted(n for n, _, _ in deep_result.changes)
    assert names == ['deep-change']


def test_from_cwd_max_depth_0_disables_downward_scan(tmp_path: Path) -> None:
    """max_depth=0 disables the downward scan entirely (upward-found
    openspec/ still applies)."""
    changes_dir = tmp_path / 'repo-a' / 'openspec' / 'changes' / 'add-thing'
    changes_dir.mkdir(parents=True)
    (changes_dir / 'tasks.md').write_text('- [ ] one\n')

    result = openspec_mod.OpenSpec.from_cwd(str(tmp_path), max_depth=0)
    assert result.changes == []
