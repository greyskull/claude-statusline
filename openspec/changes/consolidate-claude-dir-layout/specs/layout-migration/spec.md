## ADDED Requirements

### Requirement: One-shot migration from the legacy flat layout

YAS SHALL provide a single migration routine that converts a legacy `$CLAUDE_CONFIG_DIR` to the `yas/` subtree layout. The routine SHALL create `yas/cache/`, `yas/state/`, `yas/state/runtime/`, `yas/state/signals/`, and `yas/state/sessions/`, and then apply exactly these dispositions:

| Legacy path | Disposition |
|---|---|
| `statusline-tokens.log` | move → `yas/state/runtime/tokens.log` |
| `yas-last-prompt.json` | move → `yas/state/signals/last-prompt.json` |
| `terminal-width` | move → `yas/state/signals/terminal-width` |
| `statusline-token-rate.log` | delete |
| `statusline-render.log` | delete |
| `yas.toml.cache` | delete |
| `statusline-output/` (recursive) | delete |
| `statusline-theme` | delete (after the installer's fold, see below) |

#### Scenario: Full legacy layout is migrated

- **WHEN** the migration runs against a config dir containing all nine legacy paths
- **THEN** the three moved files exist at their new paths with their original contents
- **AND** the six deleted paths no longer exist
- **AND** all six new directories exist

#### Scenario: Nothing to migrate

- **WHEN** the migration runs against a config dir with no legacy paths at all
- **THEN** it completes without error
- **AND** the directory skeleton and `version.json` are created

### Requirement: Migration is idempotent and never clobbers a destination

Every migration step SHALL be safe to repeat. Directory creation SHALL use `exist_ok=True`. A move SHALL be skipped when the destination already exists, and otherwise performed with a single atomic rename. A delete SHALL tolerate an already-absent target. An `OSError` in any individual step SHALL be swallowed so the remaining steps still run, but SHALL prevent the completion marker from being written.

#### Scenario: Second run is a no-op

- **WHEN** the migration runs twice in a row against the same config dir
- **THEN** the second run changes no file contents and raises no error

#### Scenario: Destination already populated

- **WHEN** `statusline-tokens.log` exists AND `yas/state/runtime/tokens.log` already exists with different contents
- **THEN** the new file's contents are left untouched
- **AND** no exception propagates

#### Scenario: Concurrent migrations

- **WHEN** two processes run the migration simultaneously against the same config dir
- **THEN** each moved file ends up exactly once at its destination with intact contents
- **AND** neither process raises

### Requirement: Completion marker written last and atomically

On successful completion the migration SHALL write `$CLAUDE_CONFIG_DIR/yas/state/version.json` as its final action, via a temporary file in the same directory replaced into place with `os.replace`. Its contents SHALL be a JSON object with `schema_version` (integer, `1` for this layout), `yas_version` (the string from `constants.VERSION`), and `migrated_at` (epoch seconds).

#### Scenario: Marker contents

- **WHEN** the migration completes
- **THEN** `yas/state/version.json` parses as JSON with `schema_version == 1`, `yas_version` equal to `constants.VERSION`, and a numeric `migrated_at`

#### Scenario: Crash before completion re-runs cleanly

- **WHEN** the migration is interrupted after moving some files but before writing `version.json`
- **THEN** the marker is absent
- **AND** a subsequent run completes the remaining steps and writes the marker, leaving already-moved files intact

### Requirement: Lazy migration at render time behind a single stat

`app.main` SHALL check for the existence of `yas/state/version.json` before any other filesystem access, and SHALL invoke the migration only when the marker is absent. The migration module SHALL be imported lazily inside that branch, so a migrated installation pays one `stat()` and no import cost. The guard and its module SHALL carry an in-source marker identifying them as removable in a later release.

#### Scenario: Migrated installation skips the migration

- **WHEN** `version.json` exists and a render tick runs
- **THEN** the migration routine is not invoked and the migration module is not imported

#### Scenario: Un-migrated installation migrates then renders

- **WHEN** `version.json` is absent, legacy files are present, and a render tick runs
- **THEN** the migration runs first, the marker is written, and the render output is identical to the same render on an already-migrated dir

### Requirement: Eager migration and theme fold at install time

`ops/install.sh` SHALL run the migration during `do_wire`, using the interpreter and plugin root it has already resolved. Before invoking it, the installer SHALL fold the deprecated `statusline-theme` file into `yas.toml`: when that file exists and is non-empty AND `yas.toml` does not already set `[appearance] theme`, the installer SHALL write that theme name into `yas.toml` using its existing atomic, validating TOML write path. A migration failure SHALL be reported but SHALL NOT abort the install.

#### Scenario: Theme folded into yas.toml

- **WHEN** `statusline-theme` contains `gruvbox`, `yas.toml` exists and sets no theme, and the installer runs
- **THEN** `yas.toml` afterwards sets `[appearance] theme = "gruvbox"` and still parses
- **AND** `statusline-theme` no longer exists

#### Scenario: Existing yas.toml theme wins

- **WHEN** `statusline-theme` contains `gruvbox` and `yas.toml` already sets `theme = "claude-dark"`
- **THEN** `yas.toml` is left unchanged
- **AND** `statusline-theme` is still deleted

#### Scenario: Runtime migration never writes yas.toml

- **WHEN** the lazy render-time migration runs with a `statusline-theme` file present
- **THEN** `yas.toml` is not created or modified
- **AND** `statusline-theme` is deleted

#### Scenario: Migration failure does not fail the install

- **WHEN** the migration invocation exits non-zero during `do_wire`
- **THEN** the installer prints a warning and continues to the settings.json wiring step
