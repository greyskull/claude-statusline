from __future__ import annotations

import re
from pathlib import Path

# Directories skipped during the downward recursive scan for nested openspec/
# roots (monorepo-of-repos layout). Kept small and cheap to check per entry.
_IGNORED_DIRS = frozenset((
    '.git', 'node_modules', 'venv', '.venv', '__pycache__',
    '.tox', '.mypy_cache', '.pytest_cache', '.ruff_cache',
))
# How many path segments below the scan root we're willing to descend. Caps
# the walk on huge monorepos-of-repos without missing typical nesting depths.
_MAX_SCAN_DEPTH = 6


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
    def from_cwd(cls, cwd: str) -> OpenSpec:
        roots = cls._find_roots(cwd)
        if not roots:
            return cls()
        multi = len(roots) > 1
        out: list[tuple[str, int, int]] = []
        for root in roots:
            # Nested repos found downward can share change names ('add-x' is
            # common); prefix with the repo dir (openspec/'s parent) so the
            # display stays unambiguous once there's more than one root.
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
    def _find_roots(cls, cwd: str) -> list[str]:
        """All openspec/ roots relevant to ``cwd``: the nearest ancestor (if
        cwd sits inside a repo) plus every openspec/ found by recursively
        walking down from cwd (for monorepo-of-repos layouts where sibling
        or nested repos each carry their own openspec/)."""
        if not cwd:
            return []
        seen: set[str] = set()
        roots: list[str] = []

        upward = cls._find_root(cwd)
        if upward:
            seen.add(upward)
            roots.append(upward)

        base = Path(cwd)
        if base.is_dir():
            for found in cls._scan_downward(base):
                if found not in seen:
                    seen.add(found)
                    roots.append(found)
        return roots

    @classmethod
    def _scan_downward(cls, base: Path, max_depth: int = _MAX_SCAN_DEPTH) -> list[str]:
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
