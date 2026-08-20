## ADDED Requirements

### Requirement: Transcript-cache knob

The statusline SHALL expose a `transcript_cache` boolean knob, resolved through
the standard precedence chain: canonical `YAS_TRANSCRIPT_CACHE` env var →
`[cache].transcript_cache` in `yas.toml` → default. The value SHALL be a boolean
parsed by the shared boolean parser (`0`, `false`, `no` are false; any other
non-empty value is true) and the default SHALL be `true`. An invalid value SHALL
fall back to the default like every other knob, and SHALL be reported through the
same visible config-error path. When the knob resolves false, the statusline
SHALL neither read nor write the transcript parse cache file and SHALL take the
uncached read path everywhere, producing identical rendered output.

#### Scenario: Default is on

- **WHEN** no `transcript_cache` is configured from any source
- **THEN** the resolved `transcript_cache` is `true`

#### Scenario: Env var disables the cache

- **WHEN** `YAS_TRANSCRIPT_CACHE=0` is set
- **THEN** the resolved `transcript_cache` is `false` and no cache file is read or written during a render

#### Scenario: Env overrides toml

- **WHEN** `YAS_TRANSCRIPT_CACHE=0` is set and `[cache].transcript_cache = true` is configured
- **THEN** the resolved `transcript_cache` is `false`

#### Scenario: Invalid value falls back

- **WHEN** `[cache].transcript_cache = "maybe"` is configured
- **THEN** the resolved value is the default `true` and a config error is recorded
