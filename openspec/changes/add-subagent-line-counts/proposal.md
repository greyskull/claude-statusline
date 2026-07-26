## Why

YAS already tells you how many tokens a session burned and how many times each
tool ran, but not the thing a human actually recognises as *work done*: how much
source was read and how much was written. Two numbers — lines read and lines
changed — turn "Read 40/188" into "we read 9.4k lines and changed 1.2k", and per
subagent they make the difference between a researcher and an editor visible at a
glance. The bytes are already read every render by `count_transcript`, so the
numbers cost one extra field per line, not an extra file walk.

## What Changes

- Count **lines read** and **lines changed** per transcript, fused into the
  existing `count_transcript` walk in `claude/yas/info/toolcounts.py` (one pass
  per file, no second walk, no cache, no state file).
  - `lines_read`: newlines in the `tool_result` paired with each `Read` `tool_use`,
    counted only when that result content is a string starting with `1\t` (the
    `cat -n` shape). Image/document reads (list-valued content) are skipped.
  - `lines_changed`: `Edit` → `max(newlines(old_string), newlines(new_string))`;
    `Write` → `newlines(content)`. `Read`/`Write`/`Edit` only — **not**
    `NotebookEdit`.
- **BREAKING (internal):** `count_transcript` returns a `TranscriptToolStats`
  value (`counts`, `lines_read`, `lines_changed`) instead of a bare
  `dict[str, int]`. `ToolCounts` grows `lines_read` / `lines_changed` session
  totals and a `per_agent` map keyed by transcript path.
- Add a **new second segment** to the wide tokens/cost row
  (`Renderer.tokens_cost`), between the tokens column and the cost column, giving
  the row order `tokens │ lines │ cost │ leader(rate+sparkline)`. The segment
  shows the session total (main transcript **plus every** subagent transcript) as
  one read/changed pair, humanised (`1.2k`) like the token fields.
- The new segment and its `│` divider **shed** below a new
  `LINES_SEGMENT_MIN_WIDTH` (103) so `TOKENS_COST_MIN_WIDTH` stays at **85** and
  the row renders byte-identically to today between 85 and 103 columns. The
  sparkline leader absorbs the width cost above 103.
- Add a self-scoped **lines field** to the per-subagent stats cluster
  (`Renderer.subagent_row`), alongside share% / tokens / model. It is the *first*
  field shed under width pressure and renders blank (not `0`) when the subagent
  read and changed nothing.
- Add two Nerd Font PUA glyph constants (read/changed) with `ascii`, `unicode`
  and `github` fallbacks, plus the `lines read/changed` section caption.

## Capabilities

### New Capabilities
- `line-counts`: the lines-read / lines-changed measurement — which tools are in
  scope, the `cat -n` sniff test, the `Edit`/`Write` counting rules, the
  sidechain/subagent double-count rules, the `/clear` window — and its two display
  surfaces: the session-total segment in the tokens/cost row (with its shed rule)
  and the self-scoped per-subagent field (with its shed order).

### Modified Capabilities
- `statusline-info`: the `tool_counts` gather field additionally exposes session
  lines-read / lines-changed totals and a per-transcript breakdown, still computed
  from the same files in the same single pass.
- `compact-tokens-row`: the wide tokens/cost row gains a fourth segment and a
  third `│` divider above `LINES_SEGMENT_MIN_WIDTH`, and keeps exactly today's
  three-segment two-divider form below it.
- `subagent-row-layout`: the line-1 stats cluster gains a lines field, and the
  shed order becomes lines → share% → tok (model and duration still always kept).

## Impact

- `claude/yas/info/toolcounts.py` — fused counting, `TranscriptToolStats`,
  `ToolCounts.lines_read` / `.lines_changed` / `.per_agent`, V2 byte-level
  pre-filters.
- `claude/yas/info/__init__.py` — `SessionView.tool_counts` docstring/contract.
- `claude/yas/renderer.py` — `tokens_cost` (new segment, third divider column),
  `subagent_row` (new cluster field + shed ladder).
- `claude/yas/layout.py` — `build_wide` handles a variable-length `vsep_cols`
  (2- or 3-tuple), a new centred `lines read/changed` caption, and passes each
  subagent's own counts into `subagent_row`.
- `claude/yas/constants.py` — `LINES_SEGMENT_MIN_WIDTH`, two PUA glyph constants,
  their `ASCII_GLYPHS` / `UNICODE_PUA` / `GITHUB_ICON_OVERRIDE` entries, and the
  `LINES_LABEL` caption.
- `test/test_tokens_cost.py`, `test/test_layout_seam.py`,
  `test/test_tool_counts.py`, `test/test_subagent_rows.py` — extended; new
  counting-semantics tests.
- `demo/text/*.txt` — 21 of ~23 fixtures contain the tokens/cost row and need
  re-goldening.
- `CONTEXT.md` — glossary entries for Lines Read and Lines Changed.
