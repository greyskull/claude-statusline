## ADDED Requirements

### Requirement: Per-session on-disk transcript parse cache

The statusline SHALL persist, between renders, everything it derives from a
transcript file, in a single JSON cache file per session located under
`CLAUDE_DIR / 'yas-cache'` and named for the session id. The file SHALL carry a
version stamp, the session id, a save timestamp, and a map keyed by absolute
transcript path. Because the statusline re-execs per render, this file SHALL be
the only mechanism by which derived transcript state survives a tick; no
behaviour SHALL depend on a process-lifetime cache persisting.

#### Scenario: Cache file is written after a render

- **WHEN** a render completes with the cache enabled and at least one transcript parsed
- **THEN** a JSON cache file for that session exists under `CLAUDE_DIR / 'yas-cache'`
- **AND** it contains a version stamp and one entry per transcript read during the render

#### Scenario: Second render reads without reopening unchanged transcripts

- **WHEN** a render completes and a second render runs with no transcript file modified
- **THEN** no `agent-*.jsonl` file is opened during the second render
- **AND** the second render produces byte-identical output to the first

### Requirement: Cached entries are keyed by path plus mtime and size

Each cache entry SHALL record the transcript's `st_mtime` and `st_size` as
observed when the entry was written. A stored whole-file result SHALL be reused
only when the current `st_mtime` and `st_size` are exactly equal to the recorded
values. Any difference — larger, smaller, or a changed mtime at equal size — SHALL
be treated as a miss and SHALL cause the file to be re-read.

#### Scenario: Unchanged file hits

- **WHEN** a transcript's mtime and size match its cache entry
- **THEN** the stored result is returned and the file is not opened

#### Scenario: Appended file misses

- **WHEN** a transcript has grown since its entry was written
- **THEN** the whole-file entry is not reused for that transcript

#### Scenario: Truncated file misses

- **WHEN** a transcript is smaller than its recorded size
- **THEN** the whole-file entry is not reused and the file is re-read from the start

### Requirement: Parse-input sub-keys are part of the cache key

Results whose value depends on a caller-supplied input SHALL be stored under a
sub-key naming that input, and SHALL be reused only for an identical input. The
`parse_transcript` result SHALL be sub-keyed by its `resume_after` argument,
because `resume_after` determines `run_start_ts`. The `count_transcript` result
SHALL be sub-keyed by the pair `(clear_epoch, skip_sidechain)`, because both
change the counts. Float sub-keys SHALL round-trip exactly through the JSON file.
Each sub-key map SHALL be bounded, retaining only the most recent few entries per
transcript, so a drifting input cannot grow the file without bound.

#### Scenario: Different resume_after does not hit

- **WHEN** a cached parse exists for `resume_after = 0.0` and a parse is requested with `resume_after = 1700000000.0`
- **THEN** the cached result is not returned and the transcript is parsed

#### Scenario: Different clear epoch does not hit

- **WHEN** a cached count exists for one `clear_epoch` and counts are requested for another
- **THEN** the cached counts are not returned

#### Scenario: Sidechain flag is part of the key

- **WHEN** counts were cached with `skip_sidechain=True` and are requested with `skip_sidechain=False` for the same file
- **THEN** the cached counts are not returned

### Requirement: Tail-read state resumes from the cached byte offset

The cache SHALL store, per transcript, the notification tail state and the
tool-result tail state as `(mtime, size, offset, findings)`. At the start of a
render these SHALL be loaded into the existing in-memory tail caches so that the
existing incremental tail-read algorithm resumes from the stored byte offset
rather than from byte 0. The algorithm itself — its hit test, its shrunk-file
rescan, and its "stop at the last complete newline" rule — SHALL be unchanged. A
transcript that has grown SHALL be read only from the cached offset to the end of
file.

#### Scenario: Grown transcript is read incrementally

- **WHEN** a cached tail offset exists for a transcript and new lines have been appended
- **THEN** only the appended bytes are read
- **AND** the resulting findings equal those of a full read of the whole file

#### Scenario: Shrunk transcript is rescanned

- **WHEN** a cached tail offset exists and the transcript is now smaller than the recorded size
- **THEN** the transcript is rescanned from byte 0 and the cached findings are discarded

#### Scenario: Partial trailing line is not consumed

- **WHEN** the appended bytes end without a newline
- **THEN** the stored offset does not advance past the last complete line

### Requirement: Cache is loaded once and saved once per render

The cache SHALL be loaded exactly once at the start of a render by the
application layer and passed to the readers, and SHALL be written back exactly
once after the render completes. Readers SHALL NOT write the cache file. The
cache instance SHALL be an optional argument to every reader it serves,
defaulting to absent, and an absent cache SHALL select exactly today's uncached
behaviour.

#### Scenario: One write per render

- **WHEN** a render reads forty-eight transcripts
- **THEN** the cache file is written exactly once

#### Scenario: Readers work without a cache

- **WHEN** a reader is called with no cache argument
- **THEN** it performs its full read and returns the same result as before this change

### Requirement: Cache failures degrade to a full re-parse

Every cache failure SHALL degrade to a full re-parse. A missing, unreadable,
truncated, non-JSON, wrong-version, or structurally
invalid cache file SHALL be treated as an empty cache. An individual entry that
fails to decode SHALL be discarded without discarding the rest of the file. No
cache read or write error SHALL propagate into the render path or alter rendered
output. Cache writes SHALL be made to a temporary file and moved into place, so
that a render interrupted mid-write leaves the previous file intact.

#### Scenario: Corrupt file is ignored

- **WHEN** the cache file contains invalid JSON or truncated content
- **THEN** the render proceeds with a full re-parse and produces correct output
- **AND** the render does not raise

#### Scenario: Version bump invalidates

- **WHEN** the cache file's version stamp differs from the current version
- **THEN** the whole file is discarded and every transcript is re-parsed

#### Scenario: One bad entry does not poison the file

- **WHEN** a single entry has a malformed stored result
- **THEN** that transcript is re-parsed and the remaining entries are still used

#### Scenario: Interrupted write leaves the previous file

- **WHEN** a render is killed while the cache is being written
- **THEN** the previously saved cache file is still valid and loadable

### Requirement: Cache contents are pruned and bounded

On save, entries whose transcript path no longer exists SHALL be dropped, and
entries not seen within the retention horizon SHALL be dropped. Each entry SHALL
record when it was last seen. An entry for an agent that has reached a terminal
status and is older than the abandoned horizon MAY be flagged terminal so that
cohort assembly can avoid redundant work for it, and that flag SHALL never
suppress a transcript whose mtime or size has since changed.

#### Scenario: Vanished transcripts are dropped

- **WHEN** a cached transcript path no longer exists on disk at save time
- **THEN** its entry is not written to the new cache file

#### Scenario: Ancient entries expire

- **WHEN** an entry has not been seen within the retention horizon
- **THEN** it is dropped on the next save

#### Scenario: A changed terminal-flagged file is still re-read

- **WHEN** a terminal-flagged entry's transcript has a new mtime or size
- **THEN** the transcript is re-read and the entry is refreshed

### Requirement: Cache is disableable by configuration

A boolean configuration knob SHALL enable or disable the transcript parse cache,
resolved through the standard configuration precedence, defaulting to enabled.
When disabled, no cache file SHALL be read or written and every reader SHALL take
its uncached path, producing output identical to the enabled case.

#### Scenario: Disabled cache performs no cache I/O

- **WHEN** the knob is set false
- **THEN** no cache file is read or written during a render

#### Scenario: Output is identical either way

- **WHEN** the same session is rendered with the knob true and with it false
- **THEN** the rendered output is byte-identical

### Requirement: Rendered output is invariant to cache state

For any given set of transcripts and a fixed clock, the rendered statusline SHALL
be byte-identical whether the cache is cold, warm, partially warm, disabled, or
corrupt. The cache SHALL be a performance mechanism only, with no observable
effect on the visible agent cohort, token figures, tool counts, line counts, or
the Session In/Out denominator.

#### Scenario: Cold and warm renders agree

- **WHEN** a session is rendered with no cache file and again with a warm cache at the same frozen clock
- **THEN** both renders produce identical output

#### Scenario: Partial warmth agrees

- **WHEN** some transcripts are cached and others have changed since
- **THEN** the render matches a fully cold render at the same frozen clock
