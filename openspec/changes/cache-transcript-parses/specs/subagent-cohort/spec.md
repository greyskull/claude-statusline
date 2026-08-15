## ADDED Requirements

### Requirement: Conclusively-retired agents may be parsed in totals-only mode

Cohort assembly SHALL be permitted to reduce work for retired agents as follows.
When it has no cached parse for an agent's transcript and that agent
is *conclusively retired* — it has a terminal status from the cheap
notification/tool-result signals, its `end_ts` is older than both the finished
linger and cohort grace windows, and its transcript mtime is older than the
abandoned horizon, each with a skew margin — the statusline MAY derive that
agent's fields with a reduced, totals-only parse instead of a full parse. A
totals-only parse SHALL still produce exact `billed_in`, `cache_read_in`,
`output`, `first_timestamp`, `end_ts` and `run_start_ts` values, so that the
Session In/Out denominator and every retirement decision are identical to those a
full parse would yield. It MAY omit only fields that a rendered row consumes —
the model and the last-activity triple.

#### Scenario: Retired agent contributes exact token totals

- **WHEN** a conclusively-retired agent is built with a totals-only parse
- **THEN** its `total_input` and `output` equal the values a full parse produces
- **AND** the Session In/Out denominator is unchanged

#### Scenario: Retirement timestamps are exact

- **WHEN** a conclusively-retired agent is built with a totals-only parse
- **THEN** its `first_timestamp`, `end_ts` and `mtime` equal the full-parse values, so visibility decisions are unchanged

#### Scenario: A live agent is never reduced

- **WHEN** an agent's transcript was written within the abandoned horizon, or it has no terminal status, or it finished within the grace windows
- **THEN** it is parsed in full

### Requirement: A totals-only agent that turns out visible is re-parsed in full

Cohort assembly SHALL record which agents were built with a totals-only parse.
If any such agent appears in the visible cohort, the statusline SHALL re-parse
that agent's transcript in full and rebuild its record before any row is
rendered. No rendered row SHALL ever be built from a totals-only record.

#### Scenario: Mispredicted retirement still renders correctly

- **WHEN** an agent built totals-only is nonetheless returned by `visible()`
- **THEN** it is re-parsed in full and its row shows the same model, last activity and figures as a fully-parsed render

#### Scenario: The common case costs nothing extra

- **WHEN** no totals-only agent is visible
- **THEN** no transcript is re-parsed
