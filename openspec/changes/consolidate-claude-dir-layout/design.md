## Context

Every YAS path today is composed ad hoc at its use site from a module-level `CLAUDE_DIR` constant:

| Legacy path | Code |
|---|---|
| `statusline-tokens.log` | `tokens.py:116` |
| `statusline-token-rate.log` | `tokens.py:171, 207, 248` |
| `statusline-render.log` | `tokens.py:292, 308` |
| `statusline-output/statusline.<sid>.json` | write `app.py:100-102`, read `mon/discovery.py:45` |
| `yas.toml.cache` | `config.py:254` |
| `statusline-theme` | `config.py:154-160` |
| `yas-last-prompt.json` | read `info/subagents.py:36`, write `hooks/yas-prompt-hook.py:23-24` |
| `terminal-width` | read `render/text.py:36`, write `ops/alacritty.py:26` (hardcoded `$HOME`) |

`CLAUDE_DIR = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(HOME / '.claude')))` is declared at `constants.py:14` and duplicated verbatim at `session.py:21-22`. Because it is a module-level constant frozen at import, `test/conftest.py:72-86`'s `tmp_home` fixture must monkeypatch it in **eight** modules; every new module that touches disk has to be added there or its tests write to the real `~/.claude`.

Constraints:
- The statusline runs on every render tick; the hot path must not pay for migration after the first run.
- `hooks/yas-prompt-hook.py` is executed standalone by Claude Code with no `yas` package on `sys.path` — it cannot import `constants`.
- `ops/install.sh` is POSIX-ish bash and already owns legacy cleanup (`:825-882` install, `:966-979` uninstall) and the `yas.toml` writer (`:1144-1240`).
- Demo (`ops/demo.py:1670`) and installer preview (`install.sh:1114`) already point `CLAUDE_CONFIG_DIR` at scratch dirs, so they get the new layout for free.

## Goals / Non-Goals

**Goals:**
- One `yas/` subtree under `$CLAUDE_CONFIG_DIR` holding everything YAS owns except `yas.toml`.
- A visible cache/state split so `yas/cache/` is documented as safe to `rm -rf` at any time.
- Existing users migrated automatically, with no data the user cares about lost.
- One patch point for tests; one place in source that knows a path.
- A real uninstall that leaves no YAS files behind except the user's `yas.toml`.

**Non-Goals:**
- XDG base directories (`$XDG_STATE_HOME` etc.). Rejected: Claude Code itself centralises on `$CLAUDE_CONFIG_DIR`, and a second root would make "where are my files" worse, not better.
- Changing any rendered output, config key, or knob semantics.
- Migrating or relocating Claude Code's own files (`settings.json`, `projects/`, `plugins/`).
- Backwards-compatible dual reads (read new, fall back to old at every call site). The migration makes them unnecessary.
- File locking or cross-process coordination.

## Decisions

### 1. Root is `$CLAUDE_CONFIG_DIR/yas/`; `yas.toml` stays put

Target layout:

```
$CLAUDE_DIR/
    yas.toml                          # user config — NOT moved
    yas/
        cache/                        # regenerable; safe to delete at any time
            config.toml.cache         # was yas.toml.cache
        state/
            version.json              # {schema_version, yas_version, migrated_at}
            runtime/                  # yas writes and reads these
                tokens.log            # was statusline-tokens.log
                token-rate.log        # was statusline-token-rate.log
                render.log            # was statusline-render.log
            signals/                  # written by external processes, read by yas
                last-prompt.json      # was yas-last-prompt.json (UserPromptSubmit hook)
                terminal-width        # was terminal-width (ops/alacritty.py)
            sessions/<sid>.json       # was statusline-output/statusline.<sid>.json
```

`yas.toml` stays at `$CLAUDE_DIR/yas.toml` because it is the one path users type, document, and share; moving it would break every existing README, blog post, and dotfile repo. Its *cache*, being ours and regenerable, does move (and is renamed `config.toml.cache` since it no longer sits beside the file it caches).

`signals/` is separated from `runtime/` because those two files are the only ones written by a process that is not the renderer (the prompt hook, and a terminal integration script); the boundary documents which paths a third party may write.

### 2. Central path API in `constants.py`, resolved at call time

No new module — the paths join the existing constants. Each path is a **function** that reads the module-global `CLAUDE_DIR` when called:

```python
def yas_root() -> Path:          return CLAUDE_DIR / 'yas'
def cache_dir() -> Path:         return yas_root() / 'cache'
def state_dir() -> Path:         return yas_root() / 'state'
def runtime_dir() -> Path:       return state_dir() / 'runtime'
def signals_dir() -> Path:       return state_dir() / 'signals'
def sessions_dir() -> Path:      return state_dir() / 'sessions'
def version_file() -> Path:      return state_dir() / 'version.json'
def config_path() -> Path:       return CLAUDE_DIR / 'yas.toml'
def toml_cache_path() -> Path:   return cache_dir() / 'config.toml.cache'
def tokens_log() -> Path:        return runtime_dir() / 'tokens.log'
def token_rate_log() -> Path:    return runtime_dir() / 'token-rate.log'
def render_log() -> Path:        return runtime_dir() / 'render.log'
def last_prompt_path() -> Path:  return signals_dir() / 'last-prompt.json'
def terminal_width_path() -> Path: return signals_dir() / 'terminal-width'
def session_payload_path(session_id: str) -> Path: return sessions_dir() / f'{session_id}.json'
def projects_dir() -> Path:      return CLAUDE_DIR / 'projects'
def settings_path() -> Path:     return CLAUDE_DIR / 'settings.json'
```

Functions over module-level `Path` constants: a constant would freeze at import exactly like today's `CLAUDE_DIR` and re-create the eight-module patching problem. Functions close over `constants.__dict__`, so patching `constants.CLAUDE_DIR` alone redirects every path in the process regardless of which module imported which helper. `conftest.tmp_home` therefore drops to a single `monkeypatch.setattr(_sl_constants, 'CLAUDE_DIR', claude_dir)`.

Consequence: **no module outside `constants.py` may import `CLAUDE_DIR`.** `projects_dir()` and `settings_path()` exist purely so `subagents.py`, `workflows.py`, and `session.py` have no reason to. `session.py:21-22`'s duplicate declaration is deleted.

Second consequence: default arguments evaluated at import (`mon/discovery.py:23,45`) must become `None` sentinels resolved inside the function body, otherwise a patched `CLAUDE_DIR` is ignored.

`hooks/yas-prompt-hook.py` is the documented exception — it runs without the package on `sys.path`, so it keeps its self-contained resolution and hardcodes `<config_dir>/yas/state/signals/last-prompt.json`, with a comment pointing at `constants.last_prompt_path()` as the source of truth. Same for `ops/alacritty.py`, which additionally starts honouring `CLAUDE_CONFIG_DIR` instead of hardcoding `$HOME` (a latent bug: a `CLAUDE_CONFIG_DIR` user's width signal was written where nothing reads it).

### 3. Per-file disposition: move what's durable, delete what regenerates

| Legacy | Disposition | Rationale |
|---|---|---|
| `statusline-tokens.log` | **MOVE** → `state/runtime/tokens.log` | Day totals; losing it resets today's figure. |
| `yas-last-prompt.json` | **MOVE** → `state/signals/last-prompt.json` | Cross-process handshake; a stale-free rebuild needs a new user prompt. |
| `terminal-width` | **MOVE** → `state/signals/terminal-width` | Written by an external script that may not run again soon. |
| `statusline-token-rate.log` | **DELETE** | 300 s rolling window (`tokens.py:167`); self-heals in five minutes. |
| `statusline-render.log` | **DELETE** | 300 s rolling window; cosmetic, off by default. |
| `yas.toml.cache` | **DELETE** | Pure parse cache; regenerates on next render. |
| `statusline-output/` (whole dir) | **DELETE** | Payloads are rewritten every render tick; `mon` recovers within one tick per live session. |
| `statusline-theme` | **FOLD, then DELETE** | See decision 4. |

Deleting beats moving wherever the file regenerates within one render tick or one rolling window: a move is more code, more failure modes, and buys at most minutes of history.

### 4. Retire the legacy `statusline-theme` file

`config._legacy_theme_sources` (`config.py:154-160`) is deleted along with its entry in the theme-precedence chain. The migration preserves user intent instead: if `statusline-theme` exists, is non-empty, and `yas.toml` does **not** already set `[appearance] theme`, its value is written into `yas.toml`; then the file is deleted either way.

The `yas.toml` write is owned exclusively by `ops/install.sh` (which already has an atomic, validating TOML writer at `:1144-1240`). The runtime migration never edits `yas.toml` — a render tick must not rewrite the user's config file, and a renderer that can only be reached through a working `yas.toml` read path has no business writing one. So a user who never re-runs the installer loses the deprecated theme file's value: acceptable, given it has been documented as deprecated (`README.md:123`) and the theme is a one-line re-set in `yas.toml`.

### 5. Migration runs lazily at render time and eagerly at install time

**Lazy (runtime).** `app.main` calls a single guard before anything else touches disk:

```python
if not version_file().exists():
    from yas.migrate import migrate   # imported only on the cold path
    migrate()
```

Cost on the steady-state path is one `stat()` — well inside the render budget — and the `yas.migrate` import is not paid at all once migrated. The guard and module are marked in-source as removable a few releases after ship (the deletion is a two-line diff plus one file).

**Eager (installer).** `ops/install.sh do_wire` runs the same migration through the resolved interpreter (`"$PYTHON_BIN" -c 'from yas.migrate import migrate; migrate()'` with `PYTHONPATH="$PLUGIN_ROOT/claude"`), immediately after the existing legacy `statusline-info-*` sweep. The installer additionally performs the `statusline-theme` → `yas.toml` fold (decision 4) *before* invoking the migration, so the migration only has to delete the file. A migration failure inside the installer is reported and non-fatal — the lazy path will retry on the next render.

Both paths, rather than one: the installer gives a clean, observable, one-shot migration for the common upgrade route, while the lazy path covers users who update the plugin through `claude plugin update` without ever running `install.sh`.

### 6. `version.json` is the completion marker, written last and atomically

```json
{"schema_version": 1, "yas_version": "0.8.0", "migrated_at": 1770000000.0}
```

Written via `mkstemp` in `state/` + `os.replace`, as the final step of `migrate()`. `schema_version` is the layout contract version (bumped by any future relayout); `yas_version` is `constants.VERSION` at migration time, for support/debugging; `migrated_at` is epoch seconds.

Since it is written last, a crash at any earlier point leaves the marker absent and the next run re-runs the whole migration — which is safe because every step is idempotent (decision 7). Presence of the file is the *only* thing the runtime guard checks; its contents are never parsed on the hot path.

### 7. Concurrency: idempotent, per-file atomic, no locks

Every step is one of:
- `mkdir(parents=True, exist_ok=True)` for the six directories,
- a move that is skipped when the destination already exists, otherwise `os.rename(src, dst)` — atomic within a filesystem, and never clobbering,
- a delete that tolerates `FileNotFoundError` (`unlink(missing_ok=True)` / `shutil.rmtree(..., ignore_errors=True)`).

Every individual step is wrapped so that an `OSError` is swallowed and recorded but does not abort the remaining steps — except that any failure means `version.json` is **not** written, so the migration retries next run.

Two processes racing (a render tick and the installer, or two sessions) is therefore harmless: the loser's rename fails with the destination already present and is skipped, and both write the same marker content. No lock file, no lock-file staleness problem.

Accepted trade-off: during the upgrade window an *old* renderer process may still be writing `statusline-tokens.log` while a new one writes `state/runtime/tokens.log`. The worst case is a day-total figure that undercounts a handful of ticks, and it resolves the moment every process is on the new code. Not worth a compatibility shim.

### 8. Uninstall removes the subtree and every legacy path

`ops/install.sh do_uninstall` extends the existing `statusline-info-*` sweep to remove:
- `$CLAUDE_CONFIG_DIR/yas/` (recursive), and
- each legacy path from decision 3's table (both MOVE and DELETE rows, plus `statusline-theme`),

and explicitly **never** touches `$CLAUDE_CONFIG_DIR/yas.toml` or anything owned by Claude Code. Under `--dry-run` each existing target is printed as `Would remove <name>` and nothing is deleted, matching the existing dry-run idiom at `:966-979`.

## Risks / Trade-offs

- **A user's day-total token count is lost if the tokens.log move fails** → the move is a same-filesystem `os.rename` (both paths are under `$CLAUDE_CONFIG_DIR`), the only realistic failure is a permissions problem that would equally break writing the new file; the counter self-heals from the next tick.
- **`mon` shows "(no active sessions)" briefly after upgrade** → `statusline-output/` is deleted, not moved, so payloads are absent until each session's next render tick (sub-second for an active session). Documented in the proposal as an accepted, self-healing regression.
- **Deprecated theme file silently stops working for non-installer upgraders** → mitigated by the installer fold; residual risk accepted per decision 4, and `README.md`/`CONTEXT.md` gain an explicit "removed in this release, set `[appearance] theme` in yas.toml" note.
- **The lazy guard is dead weight forever if nobody removes it** → the guard, the `yas/migrate.py` module, and the legacy-path table carry an in-source `# REMOVE AFTER <version>` marker naming the release, and a task in this change adds that marker.
- **A test that forgets the `tmp_home` fixture now writes to the real `~/.claude/yas/`** → unchanged in kind from today, but the blast radius is smaller (one subtree) and a session-scoped autouse safety net is out of scope here.

## Migration Plan

1. Ship the new path API and rewire every reader/writer in one release (no dual-read period).
2. On first render after upgrade the lazy guard fires; on `install.sh` re-run the eager path fires first.
3. `version.json` marks completion; subsequent runs pay one `stat()`.
4. A later release deletes `yas/migrate.py`, the guard in `app.main`, and the legacy tables in `install.sh`. Users who skip that many releases can re-run `install.sh`, or lose only regenerable data.

Rollback: downgrading to a pre-change release makes YAS write the legacy paths again from scratch (all of them are created on demand); the `yas/` subtree is left orphaned and can be deleted by hand.

## Open Questions

- Whether the day-total tokens log should be pruned to the current day during the move (it is a whole-file rewrite per render anyway). Deferred: this change moves bytes, it does not change formats.
