## Why

YAS scatters seven files and one directory directly across `$CLAUDE_CONFIG_DIR` (`~/.claude` by default): `statusline-tokens.log`, `statusline-token-rate.log`, `statusline-render.log`, `statusline-theme`, `terminal-width`, `yas-last-prompt.json`, `yas.toml.cache`, and `statusline-output/`. They sit next to Claude Code's own `settings.json`, `projects/`, and `plugins/`, with no naming discipline, no separation of regenerable cache from durable state, and — apart from a one-line `statusline-info-*` sweep — no cleanup on uninstall. A user cannot tell which files are ours, which are safe to delete, or how to remove YAS's footprint.

## What Changes

- All YAS-owned files move under a single `$CLAUDE_CONFIG_DIR/yas/` subtree, split into `cache/` (regenerable, delete-anytime) and `state/` (`runtime/` yas-written logs, `signals/` externally-written inputs, `sessions/` render payloads). `yas.toml` stays at `$CLAUDE_CONFIG_DIR/yas.toml`.
- A one-shot, idempotent migration moves or deletes every legacy path. It runs in two places: lazily at render time (guarded by a single stat of `yas/state/version.json`) and eagerly from `ops/install.sh`.
- `yas/state/version.json` (`{schema_version, yas_version, migrated_at}`) is written last and atomically, and is the migration's completion marker.
- **BREAKING (internal paths)**: `statusline-token-rate.log`, `statusline-render.log`, `yas.toml.cache`, and `statusline-output/` are deleted rather than moved — the first two are 300-second rolling windows, the last two are regenerable caches/payloads.
- **BREAKING (deprecated feature removal)**: the legacy `statusline-theme` file is retired. Migration folds its value into `yas.toml` (only when `yas.toml` sets no theme), deletes the file, and `config._legacy_theme_sources` is removed from the theme-precedence chain.
- All YAS paths are defined centrally in `claude/yas/constants.py` as call-time path functions, so `test/conftest.py` patches one symbol (`constants.CLAUDE_DIR`) instead of eight module-local copies. `session.py`'s duplicate `CLAUDE_DIR` declaration is removed.
- `ops/alacritty.py` is fixed to honour `CLAUDE_CONFIG_DIR` (it currently hardcodes `$HOME/.claude`) and writes the new signals path.
- `ops/install.sh uninstall` removes the whole `yas/` subtree plus every legacy path, preserving `yas.toml`; `--dry-run` lists what it would remove.
- `claude/mon/discovery.py` reads render payloads from the new `yas/state/sessions/` directory.

## Capabilities

### New Capabilities

- `claude-dir-layout`: the canonical on-disk layout of YAS-owned files under `$CLAUDE_CONFIG_DIR`, the central path API in `constants.py`, and the uninstall contract.
- `layout-migration`: the one-shot, idempotent, crash-safe migration from the legacy flat layout to the `yas/` subtree, its completion marker, and its two trigger points.

### Modified Capabilities

*(none — no existing spec under `openspec/specs/` owns these paths; the behaviour users observe from the statusline is unchanged.)*

## Impact

- `claude/yas/constants.py` — new path API (`yas_root()`, `cache_dir()`, `state_dir()`, `runtime_dir()`, `signals_dir()`, `sessions_dir()`, and per-file helpers).
- `claude/yas/app.py` (`statusline-output` write, `Config.load(config_dir=…)`, migration hook), `claude/yas/tokens.py` (three log paths), `claude/yas/config.py` (toml cache path, legacy theme removal), `claude/yas/session.py` (duplicate `CLAUDE_DIR`), `claude/yas/info/subagents.py` (last-prompt read + projects paths), `claude/yas/info/workflows.py` (projects path), `claude/yas/render/text.py` (terminal-width read).
- New module-free migration code in `claude/yas/` (single function, imported lazily by `app.main`).
- `claude/mon/discovery.py` — payloads root default.
- `hooks/yas-prompt-hook.py` — new signals path (stays self-contained, no yas import).
- `ops/install.sh` — eager migration + theme fold + uninstall sweep; `ops/alacritty.py` — config-dir resolution + new path.
- `test/conftest.py` — `tmp_home` patches one symbol; `test/test_mon_discovery.py`, token-log, parse-cache, config-theme, and subagent tests follow the new paths. New tests for migration.
- Docs: `README.md` (theme file deprecation at ~:123, terminal-width at ~:230), `CONTEXT.md` (:21, :24, :35, :78, :112).
