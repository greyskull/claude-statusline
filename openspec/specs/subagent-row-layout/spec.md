# subagent-row-layout Specification

## Purpose

Define the field set and layout of an individual subagent row: the two-line form (duration-first line 1 with a right-aligned `<lines> · <N> · <model>` cluster and an activity-continuation line 2), the shedding order under width pressure, and the one-line collapse form. The t/m rate, ↑output, and session-share% fields are removed from all forms.
## Requirements
### Requirement: Two-line row field set

A subagent rendered in two-line form SHALL place the elapsed duration at the front of line 1, followed by the agent type, a `·` separator, and the description. Line 1 SHALL end with a right-aligned cluster of `<lines> · <N> · <model>`. Line 2 SHALL show only the activity continuation (`└` + activity glyph + tool/verb), with no right-aligned metrics. The t/m rate, ↑output, and session-share% fields SHALL NOT appear in either line.

#### Scenario: Two-line row renders duration-first with the line-1 cluster

- **WHEN** a subagent is rendered in two-line form with room for all fields
- **THEN** line 1 reads `<dur> <type> · <description>` with a right-aligned `<lines> · <N> · <model>` cluster, and line 2 reads `└ <glyph> <Tool[arg]>`

#### Scenario: No t/m rate, output token, or share% field

- **WHEN** any subagent row is rendered
- **THEN** neither the t/m rate field, the ↑output field, nor a session-share `(N.N%)` suffix appears

### Requirement: Line-1 cluster shedding

When line 1 lacks room for the full `<lines> · <N> · <model>` cluster, the description SHALL truncate first. If the cluster still does not fit, fields SHALL shed in order: the lines field first, then the `<N>` token count. The model and the front duration SHALL always be retained.

#### Scenario: Description truncates before the cluster sheds

- **WHEN** line 1 is too wide for the full description plus cluster
- **THEN** the description truncates with an ellipsis while the full cluster is retained

#### Scenario: Cluster sheds lines, then tok, under width pressure

- **WHEN** the truncated description plus full cluster still exceeds the width
- **THEN** the lines field is dropped first, then the `<N>` token count, while model and the front duration remain

#### Scenario: Narrow widths behave as before the lines field existed

- **WHEN** the width is tight enough that the lines field is shed
- **THEN** the remaining cluster is byte-identical to the pre-change cluster at that width

### Requirement: One-line collapse form

A subagent rendered in one-line (collapsed) form SHALL omit the ↑output field. Its remaining structure — leading marker, agent type, model, activity verb, and the right-aligned token and duration fields — SHALL be unchanged.

#### Scenario: One-line form drops output but keeps token and duration

- **WHEN** a subagent is rendered in one-line collapsed form
- **THEN** the ↑output field is absent and the token count and duration fields remain

### Requirement: Activity verb derivation

The activity continuation's verb SHALL be derived from the latest assistant
message in the subagent transcript by preferring the last `tool_use` content
block in that message. When the message contains no `tool_use` block, the verb
SHALL fall back to the first non-empty line of the last `text` block, passed
through the untrusted-input sanitizer. A `thinking` block SHALL continue to
render as the thinking indicator. The system SHALL NOT render a contentless
`(replying)` placeholder when text content is available.

The rendered text snippet (and tool-arg) SHALL use a dynamic activity
truncation cap that grows with the available line-2 width, measured via the
visible-width helper, appending a single `…` when the content exceeds that cap.
The cap defaults to 36 visible columns when no wider space is available and for
callers without width context (the floor); the line-2 renderer passes
`min(100, available_width)`, so the cap rises up to a ceiling of 100 visible
columns when the terminal has spare horizontal space.

When the tool argument contains newline characters, only the first line SHALL
be used for display. Subsequent lines SHALL be discarded before the width cap
is applied.

#### Scenario: Multi-line tool argument shows only first line

- **WHEN** the tool argument string contains newline characters (e.g. a multi-line Bash command)
- **THEN** only the content before the first newline is displayed; subsequent lines are not rendered

#### Scenario: Tool use wins over trailing text in the same message

- **WHEN** the latest assistant message contains both a `tool_use` block and a
  trailing `text` block
- **THEN** the activity continuation shows the tool verb (`<glyph> Tool[arg]`),
  not the text snippet

#### Scenario: Text-only message shows a snippet instead of bare replying

- **WHEN** the latest assistant message ends with a `text` block and contains
  no `tool_use` block
- **THEN** the activity continuation shows the replying glyph followed by the
  first non-empty line of that text, sanitized

#### Scenario: Snippet within the available width is shown in full

- **WHEN** the first non-empty line of the text block exceeds 36 visible columns
  but fits within the available line-2 width (≤ 100 visible columns)
- **THEN** the snippet is shown in full with no trailing `…`

#### Scenario: Long text snippet truncates at the dynamic cap

- **WHEN** the first non-empty line of the text block exceeds the cap derived
  from the available line-2 width (`min(100, available_width)`)
- **THEN** the snippet is truncated to that cap with a trailing `…`

#### Scenario: Snippet beyond the ceiling truncates at 100 columns

- **WHEN** the first non-empty line of the text block exceeds the 100-column
  ceiling
- **THEN** the snippet is truncated to 100 visible columns with a trailing `…`

#### Scenario: Thinking block is unchanged

- **WHEN** the latest assistant message's selected block is a `thinking` block
- **THEN** the activity continuation shows the thinking indicator

### Requirement: Line-1 lines field

The line-1 stats cluster SHALL carry a lines field showing the subagent's own
`lines_read` and `lines_changed`, each humanised in the same form as the tok field
(for example `1.2k`), rendered as a tight `<read> / <changed>` ratio — a
space on both sides of the `/`, with no icon on either side. This no-icon
notation was adopted (commit ad10872) in place of an earlier per-figure-glyph
form specifically because per-figure icons cost extra width the crowded
subagent cohort display could not spare. The field SHALL be fixed-width in the
`tree_single` cluster so the constant activity gap after the cluster lands at
the same absolute column down the cohort. The field's read and changed sides
SHALL shed independently: a subagent that only wrote renders a blank (not `0`)
on the read side while still showing the changed side, and vice versa, with
each blank occupying exactly the width the populated value would have. The
field renders as blank padding of the full field width, not `0`, when the
subagent has neither read nor changed anything.

#### Scenario: Field shows the subagent's own figures

- **WHEN** a subagent has read 1,200 lines and changed 30
- **THEN** its row's lines field shows `1.2k /30`

#### Scenario: Field is blank when idle

- **WHEN** a subagent has read 0 lines and changed 0 lines
- **THEN** the field renders as spaces and the cluster's total width is unchanged

#### Scenario: Cluster width stays deterministic across the cohort

- **WHEN** several subagent rows with differing figures render in tree mode
- **THEN** every row's activity column begins at the same absolute column

