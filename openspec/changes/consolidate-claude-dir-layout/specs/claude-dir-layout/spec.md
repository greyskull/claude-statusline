## ADDED Requirements

### Requirement: All YAS-owned files live under a single `yas/` subtree

Every file YAS creates under `$CLAUDE_CONFIG_DIR` SHALL live under `$CLAUDE_CONFIG_DIR/yas/`, with the sole exception of the user config file `yas.toml`, which SHALL remain at `$CLAUDE_CONFIG_DIR/yas.toml`. The subtree SHALL use this layout:

```
yas/cache/config.toml.cache
yas/state/version.json
yas/state/runtime/tokens.log
yas/state/runtime/token-rate.log
yas/state/runtime/render.log
yas/state/signals/last-prompt.json
yas/state/signals/terminal-width
yas/state/sessions/<session_id>.json
```

YAS SHALL NOT create or write any other file directly in `$CLAUDE_CONFIG_DIR`, and SHALL NOT modify files owned by Claude Code (`settings.json` outside the installer, `projects/`, `plugins/`).

#### Scenario: Renderer writes only inside the subtree

- **WHEN** a full render tick runs against an empty `$CLAUDE_CONFIG_DIR`
- **THEN** every file created is under `$CLAUDE_CONFIG_DIR/yas/`
- **AND** no `statusline-*` file, `statusline-output/` directory, `terminal-width` file, or `yas.toml.cache` is created at the top level of `$CLAUDE_CONFIG_DIR`

#### Scenario: Config file is not relocated

- **WHEN** the user's config lives at `$CLAUDE_CONFIG_DIR/yas.toml`
- **THEN** YAS reads it from that exact path
- **AND** never creates `$CLAUDE_CONFIG_DIR/yas/yas.toml`

#### Scenario: Missing directories are created on demand

- **WHEN** a writer (token log, session payload, toml cache) runs and its parent directory does not exist
- **THEN** the directory is created with `parents=True, exist_ok=True` and the write succeeds

### Requirement: Cache directory is safe to delete at any time

`$CLAUDE_CONFIG_DIR/yas/cache/` SHALL contain only regenerable data (the parsed-config cache and per-session transcript parse caches). Deleting the directory, or any file in it, SHALL NOT change rendered output, only performance.

#### Scenario: Cache deleted between renders

- **WHEN** `yas/cache/` is removed entirely and a render tick runs
- **THEN** the rendered output is byte-identical to the same render with the cache present
- **AND** the cache files are recreated

### Requirement: Central path API resolved at call time

All YAS paths SHALL be defined by functions in `claude/yas/constants.py` that read the module-global `CLAUDE_DIR` at call time. No module other than `constants.py` SHALL import or hold its own copy of `CLAUDE_DIR`, and no module SHALL evaluate a YAS path at import time (including as a default argument value).

#### Scenario: One patch point redirects every path

- **WHEN** a test patches only `yas.constants.CLAUDE_DIR` to a temporary directory
- **THEN** every subsequent read and write from the renderer, the token logs, the caches, the session payloads, and the `mon` observer resolves under that temporary directory
- **AND** no file is created under the real `~/.claude`

#### Scenario: No duplicated root constants

- **WHEN** the tree is searched for `CLAUDE_CONFIG_DIR` / `.claude` root resolution in Python source
- **THEN** the only occurrences are `claude/yas/constants.py`, `hooks/yas-prompt-hook.py`, and `ops/alacritty.py` (both standalone scripts that cannot import the package)

### Requirement: Standalone writers honour CLAUDE_CONFIG_DIR and the new paths

The `UserPromptSubmit` hook (`hooks/yas-prompt-hook.py`) SHALL write `$CLAUDE_CONFIG_DIR/yas/state/signals/last-prompt.json`, and the terminal-width helper (`ops/alacritty.py`) SHALL write `$CLAUDE_CONFIG_DIR/yas/state/signals/terminal-width`. Both SHALL resolve `$CLAUDE_CONFIG_DIR` from the environment, falling back to `~/.claude`, and SHALL create the `signals/` directory if absent.

#### Scenario: Hook writes where the renderer reads

- **WHEN** the prompt hook runs with `CLAUDE_CONFIG_DIR` set to a temporary directory and stamps a session
- **THEN** the renderer's subagent-cohort code reads that timestamp from `yas/state/signals/last-prompt.json` under the same directory

#### Scenario: Width helper honours a custom config dir

- **WHEN** `CLAUDE_CONFIG_DIR=/custom/claude` is set and `ops/alacritty.py` writes a width
- **THEN** the file is written to `/custom/claude/yas/state/signals/terminal-width`
- **AND** not to `$HOME/.claude/terminal-width`

### Requirement: Observer reads session payloads from the new sessions directory

The `mon` observer SHALL index render payloads from `$CLAUDE_CONFIG_DIR/yas/state/sessions/`, where each file is named `<session_id>.json`, and SHALL resolve that root at call time rather than at import.

#### Scenario: Payload round-trip

- **WHEN** a render tick writes a payload for session `abc` and `mon` then indexes payloads
- **THEN** `mon` finds the payload for session `abc` from `yas/state/sessions/abc.json`

### Requirement: Legacy theme file is no longer read

The deprecated `$CLAUDE_CONFIG_DIR/statusline-theme` file SHALL NOT be consulted as a theme source. The theme precedence chain SHALL be CLI flag, then environment, then `yas.toml` `[appearance] theme`, then the built-in default.

#### Scenario: Legacy theme file is ignored

- **WHEN** `statusline-theme` exists containing a valid theme name and `yas.toml` sets no theme
- **THEN** the rendered theme is the built-in default, not the file's value

### Requirement: Uninstall leaves no YAS files behind except yas.toml

`ops/install.sh uninstall` SHALL remove `$CLAUDE_CONFIG_DIR/yas/` recursively and every legacy top-level path (`statusline-tokens.log`, `statusline-token-rate.log`, `statusline-render.log`, `statusline-theme`, `terminal-width`, `yas-last-prompt.json`, `yas.toml.cache`, `statusline-output/`, `statusline-info-*`). It SHALL NOT remove `$CLAUDE_CONFIG_DIR/yas.toml` or any Claude Code-owned file.

#### Scenario: Full sweep

- **WHEN** `install.sh uninstall` runs against a config dir containing both a populated `yas/` subtree and every legacy path
- **THEN** all of them are gone afterwards
- **AND** `yas.toml`, `settings.json`, `projects/`, and `plugins/` are untouched apart from the existing `statusLine`/hook key removal in `settings.json`

#### Scenario: Dry run removes nothing

- **WHEN** `install.sh uninstall --dry-run` runs against the same config dir
- **THEN** each existing target is listed as "Would remove"
- **AND** every file still exists afterwards
