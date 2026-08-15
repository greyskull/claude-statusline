## Why

A statusline render is a fresh process, so the carefully-built in-memory tail
caches in `claude/yas/info/subagents.py` never survive a tick: every render
re-reads every subagent transcript from byte 0, twice (once for
`parse_transcript`, once for `count_transcript`). A measured session with 48
accumulated subagents (23.5 MB of `agent-*.jsonl`) spends **319 ms** per render —
137 ms in `RunningSubagents.from_session`, 126 ms in `ToolCounts.gather` — to
produce a subagent section with **zero visible agents**, while a fresh session in
the same project renders in 61 ms (40 ms of which is interpreter + import). The
cost is linear in agents-ever-spawned and never goes down, so every long session
gets permanently slower after each subagent burst. A finished agent's transcript
is immutable; re-deriving its token totals and tool counts from raw JSON on every
tick is pure waste.

## What Changes

- Add a **per-session, on-disk transcript parse cache** (new module
  `claude/yas/info/parsecache.py`) holding, per transcript path, everything the
  render derives from that file, validated by `(mtime, size)`:
  - the `parse_transcript` 8-tuple (sub-keyed by `resume_after`),
  - the notification tail state (`offset` + `_Notification` list) and the
    tool-result tail state (`offset` + `tool_use_id -> (status, ts)` map),
  - the `count_transcript` `TranscriptToolStats` (sub-keyed by
    `(clear_epoch, skip_sidechain)`).
  One JSON file per session under `CLAUDE_DIR / 'yas-cache'`, loaded once at the
  start of a render and written once at the end by `app` — `SessionView` stays
  write-free.
- **Seed the existing in-memory tail caches from disk** so `_tail_read_notifications`
  (`subagents.py:134`) and `_tail_read_tool_results` (`subagents.py:238`) resume
  from the cached byte offset instead of 0. Their existing incremental algorithm
  is unchanged; only its starting state changes.
- **`parse_transcript` and `count_transcript` become cache-backed**: an exact
  `(mtime, size)` match on a fully-keyed entry returns the stored result with no
  file open. In the measured session that is 47 of 48 agents plus (partially) the
  main transcript.
- **Cold-cache fallback for retired agents:** `RunningSubagents.from_session`
  (`subagents.py:942`) gains a cheap conclusively-retired predicate; a retired
  agent with no cache entry is parsed in a new **totals-only** mode that
  byte-pre-filters to `"usage"` lines and skips model/last-activity/tag
  extraction. If such an agent nonetheless survives `visible()`, it is re-parsed
  in full before rendering, so no rendered row can ever be built from a stub.
- **`ToolCounts.gather` (`toolcounts.py:351`) reuses the cache**, so the second
  full byte-level pass over the same 23.5 MB disappears. `build_wide`
  (`layout.py:915`) keeps forcing `view.tool_counts` — the lines segment needs the
  session totals — but the forced work becomes dict lookups.
- **Bound the growth:** cache entries for transcripts that no longer exist, or
  whose last-seen time is older than a retention horizon, are pruned on save, and
  entries recorded as terminal-and-ancient carry a flag so the per-render
  `*.meta.json` glob can skip re-stating them.
- New config knob `transcript_cache` (`YAS_TRANSCRIPT_CACHE`, `[cache]` table,
  default on) to disable the cache entirely — the escape hatch for any suspected
  staleness bug.
- **Fail-safe by construction:** a missing, unreadable, wrong-version, malformed
  or partially-corrupt cache file behaves exactly like an empty cache (full
  re-parse). Cache I/O never raises into the render path.

## Capabilities

### New Capabilities
- `transcript-parse-cache`: the on-disk per-session cache — what is stored, the
  `(path, mtime, size)` validity key and the per-entry sub-keys (`resume_after`,
  `clear_epoch`, `skip_sidechain`), incremental tail resumption from a cached
  offset, the load-once/save-once lifecycle, pruning and retention, the config
  knob, and the fail-safe-to-full-reparse rule.

### Modified Capabilities
- `statusline-info`: the "`SessionView` performs no disk writes" guarantee is
  restated to name the cache save as an `app`-owned step (not a view step), and
  the `tool_counts` gather field is required to satisfy itself from the cache
  when the transcripts are unchanged, still walking each changed file at most
  once.
- `subagent-cohort`: cohort assembly SHALL be allowed to derive a conclusively
  retired agent's fields via a totals-only parse, with the hard constraint that
  visibility decisions and `session_inout` are byte-identical to a full parse, and
  that any stubbed agent that turns out to be visible is re-parsed in full.
- `statusline-config`: adds the `transcript_cache` boolean knob with the standard
  five-layer precedence.

## Impact

- `claude/yas/info/parsecache.py` — **new**: `TranscriptCache` (load / lookup /
  record / save / prune), JSON codec for `_Notification` and the tail entries,
  version stamp, `CLAUDE_DIR`-rooted paths.
- `claude/yas/info/subagents.py` — tail caches seeded from and written back to the
  disk cache (`:70`, `:198`, `:134`, `:238`); `parse_transcript` cache-backed and
  gaining a `totals_only` mode (`:391`); `from_session` widened stat (`:1006`,
  keep `st_size`), retired predicate, stub-then-verify pass (`:942-1166`).
- `claude/yas/info/toolcounts.py` — `count_transcript` (`:94`) cache-backed;
  `ToolCounts.gather` (`:351`) threads the cache through.
- `claude/yas/info/__init__.py` — `SessionView` owns the loaded cache instance and
  passes it to the readers; still performs no writes.
- `claude/yas/app.py` — load the cache next to the existing statusline-output
  write (`:88-100`) / `RenderTiming` read, and flush it once after the render.
- `claude/yas/config.py`, `claude/yas/constants.py`, `yas.example.toml` — the
  `transcript_cache` knob and its retention/version constants.
- `test/conftest.py` — register the new module in the `tmp_home` `CLAUDE_DIR`
  monkeypatch list; new `test/test_parse_cache.py`; extensions to
  `test/test_running_subagents.py`, `test/test_tool_counts.py`,
  `test/test_cohort_visibility.py`.
- No demo-fixture churn expected: rendering output is unchanged by design.
