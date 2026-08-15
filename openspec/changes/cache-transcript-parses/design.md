## Context

The statusline re-execs per tick (`claude/statusline_command.py`), so every
module-level cache starts empty. Three whole-file walkers dominate a
subagent-heavy render:

- `parse_transcript(jsonl, resume_after)` (`claude/yas/info/subagents.py:391`)
  returns a plain 8-tuple `(billed_in, cache_read_in, output, first_ts, model,
  last_activity, end_ts, run_start_ts)`; `last_activity` is
  `(kind, name, input_dict)`. Called once per agent from
  `RunningSubagents.from_session` (`:942`, call site `:1114`) — 101 ms / 48
  agents in the profile.
- `_tail_read_notifications(path)` (`:134`) and `_tail_read_tool_results(path)`
  (`:238`) share one algorithm: stat, hit-test `(mtime, size)`, else seek to the
  cached `offset` and read to the last complete newline. Their state lives in
  `_notif_tail_cache: dict[str, _TailCacheEntry]` (`:56-70`) and
  `_tool_result_tail_cache: dict[str, _ToolResultCacheEntry]` (`:188-198`), both
  `(mtime, size, offset, findings)`. In production the offset is always 0 —
  36 ms wasted per render.
- `count_transcript(path, clear_epoch, *, skip_sidechain)`
  (`claude/yas/info/toolcounts.py:94`) returns a `TranscriptToolStats(counts,
  lines_read, lines_changed)` dataclass. `ToolCounts.gather` (`:351`) walks the
  main transcript plus every agent transcript a second time — 125 ms.

`build_wide` (`claude/yas/layout.py:915-920`) forces `view.tool_counts` on every
wide render for the lines segment; layout consumes only `lines_read`,
`lines_changed` and `per_agent` (`:1596`, `:1609`, `:1638`, `:1702`) unless
`cfg.show_tool_uses` is on.

`SessionView` (`claude/yas/info/__init__.py:78-140`) is a pure-read,
`@cached_property` façade; the `statusline-info` spec states it performs **no**
disk writes. The house persistence pattern is `RenderTiming`
(`claude/yas/tokens.py:274-330`): a `CLAUDE_DIR`-rooted file, `read`/`write`, a
`KEEP` retention constant, all I/O in `try/except OSError`. `app.py:88-100`
already writes one file per session under `CLAUDE_DIR / 'statusline-output'`.

## Goals / Non-Goals

**Goals:**
- Subagent-heavy render (48 agents, 23.5 MB) drops from ~319 ms to ~60–80 ms on a
  warm cache.
- Fresh-session render unchanged or faster; the cache must not add measurable
  cost when there is nothing to cache.
- Byte-identical rendering: same visible agents, same token figures, same tool
  and line counts, same `session_inout` denominator.
- Corruption, staleness, version skew and partial writes fail safe to a full
  re-parse.
- Reuse the existing incremental tail-read machinery rather than replacing it.

**Non-Goals:**
- No change to what is rendered, no new row, glyph, colour or width threshold —
  therefore **no demo golden churn**.
- Not removing `build_wide`'s force of `view.tool_counts` (Decision 7): the lines
  segment genuinely needs the session totals; the fix is to make the work cheap,
  not conditional.
- No cross-session or global cache; no shared cache between concurrent renders of
  *different* sessions.
- No caching of `TranscriptUsage.from_transcript`, `LoadedSkills`, `TaskList`,
  `read_clear_epoch` or the git subprocess — each is ≤5 ms and does not scale
  with agent count (report §"Per-phase timings").
- No interpreter/import startup work (report recommendation #5).
- No archiving or deletion of the user's `*.meta.json` / `agent-*.jsonl` files.
  Recommendation #4 is implemented only as cache-side pruning.

## Decisions

### 1. One JSON cache file per session, `CLAUDE_DIR`-rooted

`CLAUDE_DIR / 'yas-cache' / f'transcripts.{session_id}.json'`, mirroring
`app.py:88-100`'s `statusline-output` convention (`mkdir(parents=True,
exist_ok=True)`, whole-file overwrite, `except OSError: pass`).

Per-session rather than one global file: the natural working set is exactly one
session's transcripts, the file stays small (~48 entries), and abandoning a
session abandons its cache file wholesale. Rejected: SQLite (a new dependency and
concurrency surface for ~50 records) and one file per transcript (48 opens per
render — the very cost being removed).

### 2. Envelope: `{"v": <int>, "session": <id>, "saved": <epoch>, "entries": {...}}`

`v` is a module constant (`CACHE_VERSION`) bumped by hand whenever any stored
shape changes; a mismatch discards the file. This is the migration story — there
is no reader for old versions, because everything in the file is re-derivable.

### 3. Entry shape: one record per transcript path, sub-keyed by parse inputs

```
entries[str(path)] = {
  "mtime": float, "size": int, "seen": float, "terminal": bool,
  "parse":  {"<resume_after>": [8-tuple]},
  "counts": {"<clear_epoch>|<skip_sidechain>": {"counts":{}, "lines_read":n, "lines_changed":n}},
  "notif":  {"offset": int, "items": [[task_id, tool_use_id, status, ts], ...]},
  "tres":   {"offset": int, "results": {"<tool_use_id>": [status, ts]}}
}
```

The sub-keys are load-bearing: `resume_after` is an *input* to `parse_transcript`
that changes `run_start_ts`, and `clear_epoch` + `skip_sidechain` both change
`count_transcript`'s result. Keying on `(path, mtime, size)` alone would be a
correctness bug (research report, Hazards 1–2). Float sub-keys are formatted with
`repr(float)` so they round-trip exactly. Each sub-map is capped (keep the most
recent 4 entries per transcript) so a drifting `resume_after` cannot grow the
file without bound.

`_Notification` is a `__slots__` class, not JSON-native, so `notif.items` uses a
positional 4-list codec (`task_id, tool_use_id, status, ts`) with an explicit
`_notif_to_json` / `_notif_from_json` pair. Tuples in `tres.results` and
`last_activity` round-trip through lists and are re-tupled on load.

### 4. Validity: exact `(mtime, size)` for whole-file results, `size >=` for tails

A `parse`/`counts` hit requires `st_mtime == entry.mtime and st_size ==
entry.size` **and** a matching sub-key; anything else is a miss and a full
re-read. Float mtime is compared exactly (as the existing tail hit-test at `:150`
already does) — no epsilon, because a false hit is a wrong render and a false
miss only costs the status quo.

Tail state is seeded into `_notif_tail_cache` / `_tool_result_tail_cache` before
the first read and then left entirely to the existing algorithm, which already
handles "grew" (resume from `offset`) and "shrank" (`cached.size <= st.st_size`
fails → rescan from 0). This is why the change touches so little of the hot path:
the incremental logic already exists and is correct, it just never had a warm
start.

### 5. Load once in `app`, save once in `app`; `SessionView` stays write-free

`app` loads the cache alongside its existing `RenderTiming.read(session_id)` call
(`app.py:104-110`), hands the instance to `SessionView`, and calls `flush()` after
the render completes. The `statusline-info` requirement "SessionView SHALL perform
no disk writes" is preserved verbatim in spirit and amended in text to name the
cache save as an `app`-owned step — the same treatment `record_tick` already got.

The readers (`parse_transcript`, `count_transcript`, the tail readers) receive the
cache as an **optional** argument defaulting to `None`, meaning "no cache" — so
every existing call site and every existing test keeps working unchanged, and
`mon` (which benefits from the in-process caches) is unaffected.

Rejected: writing from inside each reader. It would put 3–50 writes on a render,
break the view's no-write contract, and interleave badly with concurrent renders.

### 6. Cold-cache fallback: totals-only parse for conclusively retired agents

`session_inout` (`info/__init__.py:149-155`) sums `total_input + output` over
**all** subagents, not just visible ones, so a retired agent cannot simply be
stubbed to zero — that would change a rendered number. Instead
`parse_transcript` gains `totals_only: bool = False`, which:

- byte-pre-filters each raw line to those containing `b'"usage"'` before
  `json.loads` (the same style as the existing `b'<task-notification>'` filter at
  `:176`), so the token sums stay exact;
- still records `first_ts`, `end_ts` and `run_start_ts` (needed by `visible()`);
- returns `model=''` and `last_activity=('', '', {})` — fields only ever read by a
  rendered row.

An agent qualifies as conclusively retired when it has a terminal status from the
cheap tier-1/tier-2 maps **and** `now - end_ts` exceeds
`max(FINISHED_LINGER_SECONDS, COHORT_GRACE_SECONDS)` **and** `now - mtime >
ABANDONED_HORIZON_SECONDS`, all with a safety margin (`+ TERMINAL_SKEW_SECONDS`).

**Fail-safe:** `from_session` records which agents were built totals-only; after
`visible(now, last_prompt_ts)` is computed for the first time, any totals-only
agent appearing in the visible list is re-parsed in full and its row rebuilt
before it can render. In practice this never fires; when it does it costs exactly
one full parse. This makes the optimisation unobservable rather than
merely-usually-right.

Rejected: skipping the parse entirely (changes `session_inout`), and trusting
`visible()`'s predicate without the re-parse check (a predicate drift becomes a
blank model column in production).

### 7. `ToolCounts.gather` reuses the cache; `build_wide` keeps forcing it

`gather` threads the cache into each `count_transcript` call. With a warm cache
the 125 ms second pass becomes ~48 dict lookups. Restricting `gather` to the
*visible* cohort (report recommendation #3's alternative) was rejected: the
session `lines_read`/`lines_changed` totals are documented as "main plus every
subagent transcript" (`line-counts` spec), so narrowing the cohort would silently
shrink a rendered number — exactly the behavioural change this change forbids.

### 8. Pruning and the terminal flag

On `save()`: drop entries whose path no longer exists, and entries whose `seen`
is older than `CACHE_KEEP_SECONDS` (default 24 h — comfortably beyond
`ABANDONED_HORIZON_SECONDS`), following `RenderTiming.KEEP`. Entries flagged
`terminal` (agent finished and older than the abandoned horizon) are kept; the
flag lets `from_session` skip re-stating them beyond the single stat it already
does. This is recommendation #4, scoped to the cache only.

### 9. Config knob `transcript_cache`, default on

Five-touch-point boolean in `claude/yas/config.py` (slots list `:354`, typed
attribute `:372`, `__init__` + setter `:396/:419`, `__repr__` `:440`, `_resolve`
`:534`), env `YAS_TRANSCRIPT_CACHE`, TOML `[cache].transcript_cache`, documented
in `yas.example.toml`. When false, `app` passes `None` and every reader takes the
existing uncached path — the one-line rollback for a suspected staleness bug.

### 10. Atomic-enough writes

Write to `<file>.tmp` then `os.replace`, so a render killed mid-write leaves the
previous good file rather than a truncated one. Combined with the version stamp
and the blanket `except Exception -> empty cache` on load, there is no corrupt
state that survives one render.

## Risks / Trade-offs

- **[A same-second write is invisible to `(mtime, size)`]** → `st_mtime` is a
  float with sub-second resolution on every filesystem YAS targets, and an append
  always changes `size`. A truncate-and-rewrite to exactly the same size within
  the same mtime tick is the only blind spot; transcripts are append-only, so
  this is accepted.
- **[Stale cache renders stale numbers]** → validity is exact-match; every stored
  value is re-derivable; the knob disables the cache outright; the version stamp
  invalidates on any shape change.
- **[Concurrent renders of the same session race on the cache file]** →
  last-writer-wins with `os.replace`; both writers hold correct supersets of the
  truth, so a lost write costs one re-parse, never a wrong value.
- **[The totals-only stub leaks into a rendered row]** → the post-`visible()`
  re-parse check (Decision 6) makes it unobservable; a test asserts a
  deliberately-mispredicted agent still renders its full model and last activity.
- **[Cache load itself costs time on a fresh session]** → one `open` + `json.loads`
  of a file that is absent or a few hundred bytes; guard by returning immediately
  when the file does not exist, and measure the SMALL-session payload before and
  after (task 7.4).
- **[`json.loads` of a 48-entry cache is not free on the BIG session]** → the file
  holds derived scalars, not transcript text; expected well under 100 KB versus
  23.5 MB re-read. Task 7.3 measures it.
