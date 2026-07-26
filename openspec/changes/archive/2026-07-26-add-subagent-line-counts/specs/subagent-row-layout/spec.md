## MODIFIED Requirements

### Requirement: Line-1 cluster shedding

When line 1 lacks room for the full `lines · share% · tok · model` cluster, the description SHALL truncate first. If the cluster still does not fit, fields SHALL shed in order: the lines field first, then share%, then tok. The model and the front duration SHALL always be retained.

#### Scenario: Description truncates before the cluster sheds

- **WHEN** line 1 is too wide for the full description plus cluster
- **THEN** the description truncates with an ellipsis while the full cluster is retained

#### Scenario: Cluster sheds lines, then share%, then tok under width pressure

- **WHEN** the truncated description plus full cluster still exceeds the width
- **THEN** the lines field is dropped first, then share%, then tok, while model and the front duration remain

#### Scenario: Narrow widths behave as before the lines field existed

- **WHEN** the width is tight enough that the lines field is shed
- **THEN** the remaining cluster is byte-identical to the pre-change cluster at that width

## ADDED Requirements

### Requirement: Line-1 lines field

The line-1 stats cluster SHALL carry a lines field showing the subagent's own
`lines_read` and `lines_changed`, each humanised in the same form as the tok field
(for example `1.2k`), each preceded by its glyph. The field SHALL be
fixed-width in the `tree_single` cluster so the constant activity gap after the
cluster lands at the same absolute column down the cohort. The field SHALL render
as blank padding of that same width, not `0`, when the subagent has neither read
nor changed anything.

#### Scenario: Field shows the subagent's own figures

- **WHEN** a subagent has read 1,200 lines and changed 30
- **THEN** its row's lines field shows `1.2k` for read and `30` for changed

#### Scenario: Field is blank when idle

- **WHEN** a subagent has read 0 lines and changed 0 lines
- **THEN** the field renders as spaces and the cluster's total width is unchanged

#### Scenario: Cluster width stays deterministic across the cohort

- **WHEN** several subagent rows with differing figures render in tree mode
- **THEN** every row's activity column begins at the same absolute column
