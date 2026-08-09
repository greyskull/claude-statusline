from __future__ import annotations

import re
from pathlib import Path

# Directories skipped during the downward recursive scan for nested openspec/
# roots (monorepo-of-repos layout). Kept small and cheap to check per entry.
_IGNORED_DIRS = frozenset((
    '.git', 'node_modules', 'venv', '.venv', '__pycache__',
    '.tox', '.mypy_cache', '.pytest_cache', '.ruff_cache',
))
# Default repo-levels below the scan root the downward walk descends before
# pruning (matches yas.constants.DEFAULT_OPENSPEC_SCAN_DEPTH, the yas.toml-
# configurable knob threaded in via from_cwd's max_depth param). A nested
# openspec/ is detected when the repo/dir containing it sits at most this
# many levels below the scan root (repo-levels=1: cwd/repo-a/openspec is
# found, cwd/group/repo-a/openspec is not). _scan_downward takes the
# path-segment form of this (repo_levels + 1, since openspec/ itself is one
# segment deeper than its containing repo dir) — see _find_roots.
_MAX_SCAN_DEPTH = 1


class OpenSpec:
    __slots__ = ('changes',)

    def __init__(self, changes: list[tuple[str, int, int]] | None = None) -> None:
        self.changes = changes if changes is not None else []

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, OpenSpec):
            return NotImplemented
        return self.changes == other.changes

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f'OpenSpec(changes={self.changes!r})'

    @classmethod
    def from_cwd(cls, cwd: str, max_depth: int = _MAX_SCAN_DEPTH) -> OpenSpec:
        """``max_depth`` is repo-levels below cwd (matches the yas.toml
        ``[openspec] scan_depth`` knob), not raw path segments — see
        _scan_downward for the internal conversion."""
        roots = cls._find_roots(cwd, max_depth)
        if not roots:
            return cls()
        # Change names ('add-x' is common) can collide across repos, so once
        # more than one root is in play every entry is prefixed with its
        # repo dir (openspec/'s parent) to keep the display unambiguous. A
        # single root — upward or downward — is never ambiguous, so it's
        # left bare.
        multi = len(roots) > 1
        out: list[tuple[str, int, int]] = []
        for root in roots:
            prefix = f'{Path(root).parent.name}/' if multi else ''
            for name, d, t in cls._changes_in_root(root):
                out.append((f'{prefix}{name}', d, t))
        return cls(changes=out)

    @staticmethod
    def _changes_in_root(root: str) -> list[tuple[str, int, int]]:
        out: list[tuple[str, int, int]] = []
        open_re = re.compile(r'^\s*- \[ \]')
        done_re = re.compile(r'^\s*- \[x\]')
        for tasks in sorted(Path(root).rglob('tasks.md')):
            if '/archive/' in str(tasks):
                continue
            try:
                text = tasks.read_text()
            except OSError:
                continue
            t = sum(1 for ln in text.splitlines() if open_re.match(ln))
            d = sum(1 for ln in text.splitlines() if done_re.match(ln))
            total = t + d
            if total == 0:
                continue
            out.append((tasks.parent.name, d, total))
        return out

    @staticmethod
    def _find_root(cwd: str) -> str:
        curr = Path(cwd) if cwd else None
        while curr:
            if (curr / 'openspec').is_dir():
                return str(curr / 'openspec')
            if curr == curr.parent:
                break
            curr = curr.parent
        return ''

    @classmethod
    def _find_roots(cls, cwd: str, max_depth: int = _MAX_SCAN_DEPTH) -> list[str]:
        """All openspec/ roots relevant to ``cwd``: the nearest ancestor (if
        cwd sits inside a repo) plus every openspec/ found by recursively
        walking down from cwd (for monorepo-of-repos layouts where sibling
        or nested repos each carry their own openspec/). ``max_depth`` is
        repo-levels below cwd; a value of 0 disables the downward scan."""
        if not cwd:
            return []
        seen: set[str] = set()
        roots: list[str] = []

        upward = cls._find_root(cwd)
        if upward:
            seen.add(upward)
            roots.append(upward)

        base = Path(cwd)
        if base.is_dir() and max_depth > 0:
            # +1: openspec/ itself is one path segment deeper than the repo
            # dir that contains it, so a repo-levels max_depth of N requires
            # walking N+1 path segments to see its openspec/ entry.
            for found in cls._scan_downward(base, max_depth + 1):
                if found not in seen:
                    seen.add(found)
                    roots.append(found)
        return roots

    @classmethod
    def _scan_downward(cls, base: Path, max_depth: int) -> list[str]:
        found: list[str] = []
        base_depth = len(base.parts)
        stack = [base]
        while stack:
            curr = stack.pop()
            if len(curr.parts) - base_depth >= max_depth:
                continue
            try:
                entries = sorted(curr.iterdir())
            except OSError:
                continue
            for entry in entries:
                if not entry.is_dir() or entry.name in _IGNORED_DIRS or entry.name.startswith('.'):
                    continue
                if entry.name == 'openspec':
                    found.append(str(entry))
                    continue  # no changes/specs live below openspec/ worth descending into
                stack.append(entry)
        return sorted(found)
