# line-counts Specification

## Purpose
TBD - created by archiving change add-subagent-line-counts. Update Purpose after archive.
## Requirements
### Requirement: Lines-read and lines-changed measurement scope

The system SHALL derive two per-transcript numbers, `lines_read` and
`lines_changed`, from `Read`, `Write`, and `Edit` tool activity only. `Read`
SHALL feed `lines_read`; `Write` and `Edit` SHALL feed `lines_changed`.
`NotebookEdit` SHALL NOT contribute to either number, and no other tool
(including `Bash`) SHALL contribute. Tool names SHALL be matched after the
existing MCP normalisation (last `__`-delimited segment), so an MCP-wrapped
`Read` counts as `Read`.

#### Scenario: Read feeds lines read

- **WHEN** a transcript contains a `Read` whose result is a 120-line text file
- **THEN** `lines_read` increases by 120 and `lines_changed` is unchanged

#### Scenario: Write and Edit feed lines changed

- **WHEN** a transcript contains a `Write` and an `Edit`
- **THEN** both contribute to `lines_changed` and neither contributes to
  `lines_read`

#### Scenario: NotebookEdit is ignored

- **WHEN** a transcript contains a `NotebookEdit` tool use
- **THEN** neither `lines_read` nor `lines_changed` changes

### Requirement: lines_read counts newlines in the paired cat -n tool_result

For each `Read` `tool_use`, the system SHALL add the number of newline characters
in the content of the `tool_result` paired with that `tool_use` by `tool_use_id`,
and SHALL do so ONLY when that content is a string beginning with `1\t` (the
`cat -n` shape of a text read). Content that is not a string, or that is a string
not beginning with `1\t`, SHALL contribute zero. The system SHALL NOT derive
`lines_read` from the `offset` or `limit` fields of the `Read` `tool_use` input,
because `limit` is usually absent (the tool defaults it) and would produce a
wildly wrong count. A `tool_result` whose `tool_use_id` was never seen as a `Read`
SHALL be ignored.

#### Scenario: Text read counts its numbered lines

- **WHEN** a `Read` tool_result content is the string `"1\tfoo\n2\tbar\n3\tbaz\n"`
- **THEN** `lines_read` increases by 3

#### Scenario: Image read is skipped by the sniff test

- **WHEN** a `Read` tool_result content is the list
  `[{"type":"image","source":{"type":"base64","data":"..."}}]`
- **THEN** `lines_read` is unchanged

#### Scenario: Non-cat-n string result is skipped

- **WHEN** a `Read` tool_result content is a string that does not begin with `1\t`
  (for example an error message)
- **THEN** `lines_read` is unchanged

#### Scenario: Offset and limit are not used

- **WHEN** a `Read` `tool_use` input carries `offset` but no `limit` and its
  result is a 30-line `cat -n` string
- **THEN** `lines_read` increases by 30, not by the tool's default limit

### Requirement: lines_changed counts newlines in the tool_use input

For an `Edit` `tool_use`, the system SHALL add
`max(newlines(old_string), newlines(new_string))` to `lines_changed`. For a
`Write` `tool_use`, the system SHALL add `newlines(content)`. An `Edit` with
`replace_all: true` SHALL be counted once regardless of how many occurrences were
replaced; this undercount is accepted and SHALL be documented in `CONTEXT.md`.

#### Scenario: Edit takes the larger side

- **WHEN** an `Edit` replaces a 2-line `old_string` with a 9-line `new_string`
- **THEN** `lines_changed` increases by 9

#### Scenario: Deletion counts the removed hunk

- **WHEN** an `Edit` replaces a 12-line `old_string` with a 1-line `new_string`
- **THEN** `lines_changed` increases by 12

#### Scenario: Write counts the whole content

- **WHEN** a `Write` creates a file whose `content` has 250 newlines
- **THEN** `lines_changed` increases by 250

#### Scenario: replace_all counts once

- **WHEN** an `Edit` with `replace_all: true` replaces a 1-line string at 40 sites
- **THEN** `lines_changed` increases by 1

### Requirement: Sidechain skip on the main transcript only

When counting the main session transcript, the system SHALL skip records whose
top-level `isSidechain` field is `true`. When counting a subagent
`agent-*.jsonl` transcript, the system SHALL NOT apply any sidechain filter — the
subagent file SHALL be counted in full. This asymmetry SHALL be preserved: applying
the sidechain skip to subagent files zeroes the entire subagent contribution.
Together with the disjointness of `tool_use` ids between the main transcript and
subagent transcripts, this SHALL make
`session total == main thread + sum of every subagent` true by construction.

#### Scenario: Sidechain record in the main transcript is skipped

- **WHEN** the main transcript contains a `Read` record with `isSidechain: true`
- **THEN** it contributes nothing to the main transcript's `lines_read`

#### Scenario: Subagent transcript is counted in full

- **WHEN** every record in a subagent `agent-*.jsonl` carries `isSidechain: true`
- **THEN** all of its `Read`/`Write`/`Edit` records are still counted

#### Scenario: Session total equals main plus subagents

- **WHEN** the session total and each per-transcript figure are computed
- **THEN** the session `lines_read` equals the main transcript's `lines_read` plus
  the sum over every subagent transcript, and likewise for `lines_changed`

### Requirement: Line counts reset at the last /clear

The system SHALL count only records at or after `clear_epoch`, inheriting the
existing `count_transcript` clear-window behaviour. When `clear_epoch` is `None`
the whole transcript SHALL be counted.

#### Scenario: Pre-clear activity is excluded

- **WHEN** a `Read` record's timestamp precedes `clear_epoch`
- **THEN** it contributes nothing to `lines_read`

#### Scenario: No clear marker counts the whole session

- **WHEN** `clear_epoch` is `None`
- **THEN** every eligible record in the transcript is counted

### Requirement: Counting is fused into the existing single transcript walk

The system SHALL accumulate both line counts inside the existing
`count_transcript` pass over each transcript file. It SHALL NOT add a second walk
of the same file, an on-disk cache, a state file, or any incremental/offset
reading. Pre-filtering MAY reject lines before JSON decoding, and any such
pre-filter SHALL yield results identical to decoding every line.

#### Scenario: One pass per file

- **WHEN** the gather runs for a session with a main transcript and N subagent
  transcripts
- **THEN** each of those N+1 files is opened and walked exactly once

#### Scenario: Pre-filters do not change results

- **WHEN** the byte-level pre-filtered walk and a naive full-JSON walk are run
  over the same transcript
- **THEN** both produce identical `counts`, `lines_read`, and `lines_changed`

### Requirement: Session totals render as a segment in the tokens/cost row

The wide tokens/cost row SHALL render a lines segment between the tokens column
and the cost column, giving the segment order tokens, lines, cost, then the
rate-and-sparkline leader. The segment SHALL show one pair of numbers — the
session total, being the main transcript plus every subagent transcript combined —
NOT a main/sub split. Each number SHALL be humanised in the same form as the token
fields (for example `1.2k`). The segment SHALL be gated by no configuration flag
of its own: it renders whenever the tokens/cost row renders and the width rule
below permits.

#### Scenario: Segment sits between tokens and cost

- **WHEN** the wide tokens/cost row renders at a width that permits the segment
- **THEN** the content reads tokens, then lines, then cost, then the leader, left
  to right, divided by gradient `│` separators

#### Scenario: Value is the combined session total

- **WHEN** the main transcript read 400 lines and two subagents read 900 and 100
- **THEN** the segment shows a read figure of 1.4k

#### Scenario: Numbers are humanised

- **WHEN** the session has changed 1,200 lines
- **THEN** the segment renders `1.2k`, matching the token field's formatting

### Requirement: Lines segment sheds below its own minimum width

The lines segment AND its `│` divider SHALL be omitted when the box width is
below `LINES_SEGMENT_MIN_WIDTH` (103) or below the row's measured with-segment
minimum width. When omitted, the tokens/cost row SHALL render exactly as it does
without this change: three segments, two dividers, and a reported `min_width`
computed without the segment. `TOKENS_COST_MIN_WIDTH` SHALL remain 85, so the
tokens/cost row SHALL continue to render — rather than degrading to
`context_line_compact` — at every width between 85 and 103 columns. Above the
threshold the added width SHALL be absorbed by the rate-and-sparkline leader,
which already drops its sparkline below 10 columns.

#### Scenario: Row is unchanged in the 85-to-103 band

- **WHEN** the wide layout renders at box width 90
- **THEN** the tokens/cost row renders with exactly three segments and two
  dividers, identical to its pre-change output

#### Scenario: Segment appears at wide widths

- **WHEN** the wide layout renders at box width 140
- **THEN** the tokens/cost row includes the lines segment and three dividers

#### Scenario: Row is never dropped by the new segment

- **WHEN** the box width is at or above `max(tokens_min_w, 85)` but below the
  with-segment minimum
- **THEN** the tokens/cost row still renders, with the lines segment shed

#### Scenario: Leader absorbs the added width

- **WHEN** the lines segment is included at a wide width
- **THEN** the sparkline shortens by the segment's footprint and the tokens and
  cost columns keep their measured widths

### Requirement: Lines segment threads its own elbow and caption

When present, the lines segment SHALL contribute a third divider column to the
value `tokens_cost` returns for elbow threading, so every `│` in the row has a
matching `┬` above and `┴` below. When shed, the returned divider columns SHALL be
the two-column form. With section labels enabled (`cfg.labels`), the segment SHALL
carry the caption `lines read/changed`, centred over the segment, and the existing
`cost` and `tokens over time` captions SHALL remain anchored to their own
segments in both the shed and present forms.

#### Scenario: Three elbows when present

- **WHEN** the row renders with the lines segment
- **THEN** three `┬` marks appear on the separator above and three `┴` below, each
  aligned with a rendered `│`

#### Scenario: Two elbows when shed

- **WHEN** the row renders with the lines segment shed
- **THEN** exactly two elbows are threaded, as today

#### Scenario: Caption shown when labels are on

- **WHEN** `cfg.labels` is true and the lines segment is present
- **THEN** the separator above shows the `lines read/changed` caption over the
  segment, and the `cost` and `tokens over time` captions remain over theirs

### Requirement: Per-subagent lines field is self-scoped

Each subagent row SHALL show the line counts of its OWN transcript only. A parent
SHALL NOT roll up its descendants' counts, and a `fork` subagent SHALL count to
itself. The field SHALL sit in the line-1 stats cluster alongside share%, tokens,
and model, and SHALL humanise both numbers in the same form as the tokens field.

#### Scenario: Parent excludes its children

- **WHEN** a parent subagent read 100 lines and its child read 900
- **THEN** the parent's row shows 100 and the child's row shows 900

#### Scenario: Fork counts to itself

- **WHEN** a `fork` subagent reads 250 lines
- **THEN** those 250 lines appear on the fork's own row

#### Scenario: Rows sum to the session segment

- **WHEN** every subagent row's figure is added to the main thread's contribution
- **THEN** the total equals the figure shown in the tokens/cost row's lines segment

### Requirement: Per-subagent lines field is blank when idle and sheds first

The lines field SHALL render as blank padding — not `0` — when the subagent has
neither read nor changed anything, preserving the fixed cluster width. Under width
pressure the field SHALL be the FIRST cluster field dropped, before share% and
before tok, so the shed order becomes lines, then share%, then tok, with the model
and the front duration always retained. Narrow terminals SHALL therefore render
exactly as they do today.

#### Scenario: Idle subagent shows blank

- **WHEN** a subagent has read 0 lines and changed 0 lines
- **THEN** its lines field renders as spaces, not as `0`, and the cluster keeps its
  width

#### Scenario: Lines sheds before share%

- **WHEN** the cluster does not fit at the available width
- **THEN** the lines field is dropped first while share%, tok, and model remain

#### Scenario: Existing shed ladder is preserved below

- **WHEN** the cluster still does not fit after the lines field is dropped
- **THEN** share% is dropped, then tok, with model and duration always retained

