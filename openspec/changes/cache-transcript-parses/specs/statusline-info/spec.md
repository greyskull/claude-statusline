## MODIFIED Requirements

### Requirement: Lazy pure-read SessionView gather

The statusline SHALL gather all *derived* session state through a single `SessionView` module (`claude/statusline/info.py`), constructed once per render from a parsed `SessionInfo` plus a `Config`. `SessionView` SHALL expose the derived state as lazily-evaluated, cached fields: `git`, `skills`, `subagents`, `tasks`, `transcript_usage`, `changes` (OpenSpec changes), `elapsed`, `session_cost`, `session_inout`, and `cache_countdown`. A field SHALL read its underlying source on first access and cache the result; a second access SHALL NOT re-read. Constructing a `SessionView` SHALL perform no source reads. `SessionView` SHALL perform no disk writes and SHALL NOT call `TokenLog.update` or `TokenRate.update`; in particular, the transcript parse cache SHALL be *loaded* before the view is constructed and *saved* by `app` after the render, never written by a view field. `SessionView` MAY hold a loaded transcript parse cache and pass it to the readers, since holding it performs no I/O. The `cache_countdown` field SHALL be derived from `transcript_usage`'s raw cache anchor and the view's single frozen `now`, reusing the already-cached transcript scan rather than re-reading the transcript.

#### Scenario: A narrow render reads only what it draws

- **WHEN** a `SessionView` is constructed and a narrow-width build reads only `view.subagents`
- **THEN** the git subprocess, the transcript scan, and the openspec walk are not triggered (only the subagent source is read)

#### Scenario: A field is read at most once per view

- **WHEN** `view.session_inout` and `view.transcript_usage` are both accessed on one `SessionView`
- **THEN** the transcript is scanned exactly once (the cached value feeds both)

#### Scenario: Cache countdown reuses the cached transcript scan

- **WHEN** `view.transcript_usage` and `view.cache_countdown` are both accessed on one `SessionView`
- **THEN** the transcript is scanned exactly once (the cached usage feeds both, and `cache_countdown` triggers no additional read)

#### Scenario: Constructing a view writes nothing

- **WHEN** a `SessionView` is constructed and any subset of its fields is accessed
- **THEN** no token-log, token-rate, or transcript-parse-cache file is written by the view

### Requirement: Tool-counts gather field

`SessionView` SHALL expose a `tool_counts` `@cached_property` returning a
`ToolCounts` value that holds, per tool name, the `(main, sub)` `tool_use` counts
and the total number of distinct tool types. The same value SHALL additionally
hold the session's `lines_read` and `lines_changed` totals (the main transcript
plus every subagent transcript) and a per-transcript breakdown keyed by transcript
path, so a caller can look up any one subagent's own figures. It SHALL be
constructed from the main
transcript, the subagent cohort, and `clear_epoch` — all fields already available
on the view — and SHALL perform no I/O beyond reopening those same transcript
files, walking each file exactly once for both the tool counts and the line
counts. A transcript whose counts are already held in the transcript parse cache
for the same `clear_epoch` and sidechain setting, and whose mtime and size are
unchanged, SHALL NOT be reopened at all; the totals and the per-transcript
breakdown SHALL be identical to those a full walk would produce. The cohort
covered by the totals SHALL remain the main transcript plus **every** subagent
transcript, not only the visible cohort. As a `@cached_property`, it SHALL be
computed at most once per view and SHALL NOT be evaluated when a render path never
reads it (narrow/medium). The `info` layer SHALL NOT import `renderer` or `layout`
to provide it.

#### Scenario: Field exposes per-tool main/sub counts

- **WHEN** a `SessionView` is constructed and `tool_counts` is read
- **THEN** it returns a `ToolCounts` whose per-tool entries each carry a `main` and
  a `sub` count derived from the main transcript and the subagent cohort
  respectively

#### Scenario: Field exposes session line totals

- **WHEN** `tool_counts` is read
- **THEN** it also exposes `lines_read` and `lines_changed` totalled over the main
  transcript and every subagent transcript

#### Scenario: Field exposes a per-transcript breakdown

- **WHEN** a caller has a subagent's transcript path
- **THEN** it can obtain that subagent's own `(lines_read, lines_changed)` pair
  from the same `ToolCounts` value

#### Scenario: Field is satisfied from the cache when nothing changed

- **WHEN** every transcript's counts are cached under the current `clear_epoch` and their mtime and size are unchanged
- **THEN** `tool_counts` opens no transcript file and returns the same value a full walk would produce

#### Scenario: Field is lazy

- **WHEN** a narrow or medium render is produced without reading `tool_counts`
- **THEN** the tool-counts aggregation is never computed

#### Scenario: Field respects the clear window

- **WHEN** `clear_epoch` is set on the view
- **THEN** `tool_counts` reflects only `tool_use` messages at or after that epoch,
  and the line totals reflect only activity at or after that epoch
