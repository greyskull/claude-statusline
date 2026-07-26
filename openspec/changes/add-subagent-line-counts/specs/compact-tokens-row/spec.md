## MODIFIED Requirements

### Requirement: Single-line tokens/cost/rate row

The wide layout's tokens/cost row (`Renderer.tokens_cost`) SHALL render as exactly
**one** content line, not two. The line SHALL retain its columns in order —
tokens, then the optional lines column, then cost, then rate-and-sparkline —
separated by the standard gradient `│` vertical dividers. The lines column and its
divider SHALL be present only at or above `LINES_SEGMENT_MIN_WIDTH` and the row's
measured with-lines minimum width; below that the row SHALL be the original
three-column, two-divider form. `tokens_cost` SHALL return a single-element list of
content lines together with the divider columns — three columns when the lines
column is present, two when it is shed — so the builder can thread one matching
`┬`/`┴` elbow per rendered `│` onto the separators above and below the row. The
previous 60s sparkline tick marker (`spark_mark_col`) SHALL be removed, since a
midpoint marker has no referent once the whole bar spans 60s.

#### Scenario: Row occupies one content line

- **WHEN** the wide layout renders the tokens/cost row
- **THEN** `tokens_cost` returns exactly one content line
- **AND** every `│` in that line has a matching `┬` on the separator above and `┴`
  on the separator below at the same visual column

#### Scenario: Three columns preserved in order

- **WHEN** the single-line row is rendered with day stats enabled below
  `LINES_SEGMENT_MIN_WIDTH`
- **THEN** the content reads tokens, then cost, then rate-and-sparkline, left to
  right, divided by the gradient `│` separators

#### Scenario: Four columns at wide widths

- **WHEN** the single-line row is rendered at or above `LINES_SEGMENT_MIN_WIDTH`
  and its with-lines minimum width
- **THEN** the content reads tokens, then lines, then cost, then
  rate-and-sparkline, and `tokens_cost` returns three divider columns

#### Scenario: Row still renders across the whole 85-plus band

- **WHEN** the wide layout renders at any box width from `TOKENS_COST_MIN_WIDTH`
  (85) upward
- **THEN** the tokens/cost row is present, and the context line is NOT degraded to
  `context_line_compact`
