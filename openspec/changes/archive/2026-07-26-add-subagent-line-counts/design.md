## Context

`claude/yas/info/toolcounts.py` already walks every transcript once per render:
`ToolCounts.gather(main_path, subagents, clear_epoch)` calls `count_transcript`
on the main session `.jsonl` and once per `subagents/agent-*.jsonl`. That walk
today parses only `tool_use` blocks (last-write-wins per `message.id`) and
returns `{tool_name: count}`. It is exposed as `SessionView.tool_counts`
(`claude/yas/info/__init__.py:122-134`), a `@cached_property`, so a wide render
pays for it once and narrow/medium never touch it.

Three separate parsers currently walk the same bytes each render:
`count_transcript` (toolcounts), `TranscriptUsage.from_transcript`
(`info/transcript.py`), and `parse_transcript` (`info/subagents.py`). YAS
re-execs per statusline tick, so an in-process cache buys nothing across ticks —
this is why the `_TailCacheEntry` byte-offset cache in `subagents.py` is largely
ineffective in practice.

Transcript shapes, verified against real sessions on this machine (see
`.scratch/explore-sidechain-doublecount.md`):

- A text `Read`'s `tool_result.content` is a plain string in `cat -n` shape:
  `"1\t<line>\n2\t<line>\n…"`. There is no line-count field.
- An image `Read`'s `tool_result.content` is a **list**:
  `[{"type":"image","source":{...}}]`.
- `Edit`'s result is the fixed string `"The file … has been updated
  successfully…"`; `Write`'s is `"File created successfully at: …"`. Neither
  carries a diff or a line count, so the sizes must come from the `tool_use`
  input.
- `tool_use` ids are **fully disjoint** between the top-level session `.jsonl`
  and `subagents/agent-*.jsonl` (249 subagent ids, 0 overlap, across 8 concurrent
  subagents). Summing main + all subagents does not double-count.
- No session on this machine emits `isSidechain: true` records; this setup uses
  the async `Agent` tool with one `agent-*.jsonl` per subagent, not the
  synchronous `Task` inline-sidechain convention.

The wide tokens/cost row (`Renderer.tokens_cost`, `renderer.py:1316-1509`) is
three segments and two `│` dividers: `tokens_col │ cost_col │ leader`. Segments 1
and 2 are content-measured; segment 3 (rate label + sparkline) is the sole
slack-absorbing segment (`leader_w = max(label_w + 1, inner - w_middle - w_end)`)
and already self-drops the sparkline below `bar_w < 10`. `build_wide` gates the
whole row on `tokens_fits = width >= max(tokens_min_w, TOKENS_COST_MIN_WIDTH)`
with `TOKENS_COST_MIN_WIDTH = 85`; below that the row disappears and the context
line degrades to `context_line_compact`.

The per-subagent row (`Renderer.subagent_row`, `renderer.py:753+`) ends line 1
with a `· share% tok · model` cluster built by a nested `build_cluster(show_share,
show_tok)` and a shed ladder that tries `(True, True)`, then `(False, True)`, then
the model-only fallback.

## Goals / Non-Goals

**Goals:**
- Two numbers, lines read and lines changed, per subagent (self-scoped) and for
  the session as a whole (main + every subagent).
- Fuse the counting into the existing `count_transcript` walk — one pass per
  file, no cache, no state file, no new I/O.
- Keep the invariant "session total == main thread + sum of subagent rows" true
  **by construction**.
- Zero rendering regression between 85 and 103 columns: the tokens/cost row must
  render exactly as it does today in that band.

**Non-Goals:**
- No `NotebookEdit` accounting, no `MultiEdit`, no Bash-side file writes.
- No subtree rollup: a parent subagent does **not** absorb its children's counts;
  a `fork` counts only to itself.
- No separate config flag for the session segment — it rides the tokens/cost row.
- No narrow/medium display of either surface.
- No per-file, mtime/size-keyed gather cache shared by the three parsers (see
  Decision 9 — recorded as a follow-up, explicitly out of scope here).

## Decisions

### 1. Tools in scope: `Read`, `Write`, `Edit` only

`Read` feeds `lines_read`; `Write` and `Edit` feed `lines_changed`.
`NotebookEdit` is excluded (rare, and its cell model does not map onto a line
count). There is no `Update` tool. MCP-normalised names (`name.split('__')[-1]`,
as `count_transcript` already does) are matched, so an MCP-wrapped `Read` counts
the same as the built-in.

*Alternative rejected:* counting every tool that touches a file (including `Bash`
heredocs / `sed`) — unbounded parsing surface with no reliable size signal.

### 2. `lines_read` comes from the paired `tool_result`, gated on a `1\t` sniff

For each `Read` `tool_use`, the count is the number of `\n` in the **paired**
`tool_result.content`, and only when that content is a `str` that
`startswith('1\t')`. This is the `cat -n` shape produced by a text read.

- Image/document reads have list-valued content and fail the sniff, so they
  contribute 0 rather than a garbage count.
- `offset`/`limit` from the `tool_use` input are **not** usable: `limit` is
  usually absent (the tool defaults to 2000), so `limit`-based accounting would
  be wildly wrong. Verified in real transcripts.

Pairing is by `tool_use_id`: the walk remembers, per `tool_use` id, whether that
id was a `Read`, and attributes the newline count of the matching `tool_result`
to it. A `tool_result` whose id was never seen as a `Read` is ignored.

### 3. `lines_changed` comes from the `tool_use` input

- `Edit` → `max(newlines(old_string), newlines(new_string))` — the size of the
  hunk touched, which is the honest "how big was this change" for both
  insertions and deletions.
- `Write` → `newlines(content)` — the whole file written.

`replace_all: true` is counted **once regardless of the number of occurrences
replaced**, so a bulk rename undercounts. This is accepted and documented in
`CONTEXT.md`; the alternative (re-reading the target file to count occurrences)
would add real I/O per edit for a cosmetic gain.

Newline counting is `s.count('\n')` on the raw string. A file with no trailing
newline undercounts its final line by one — accepted, sub-1% error at any
realistic size.

### 4. Sidechain skip on the main transcript, full count on subagent files

Records with `isSidechain: true` in the **main** transcript are skipped;
`agent-*.jsonl` files are counted in **full** with no sidechain filter. This
asymmetry is load-bearing: an earlier benchmark draft applied the sidechain skip
to subagent files too and silently zeroed the entire subagent contribution,
because under some dispatch conventions every subagent record carries
`isSidechain: true`.

Together with the verified id-disjointness (Context), this makes
`session_total == main + Σ(subagents)` true by construction — the same record is
never counted on both sides.

`count_transcript` therefore takes a new `skip_sidechain: bool` parameter,
`True` for the main transcript and `False` for every subagent transcript.

### 5. Counters reset on `/clear` for free

`count_transcript` already skips records whose `timestamp` predates
`clear_epoch`. Line counts accumulate inside the same windowed loop, so they
inherit the reset with no extra code.

### 6. V2 byte-level pre-filters, benchmarked

The file is opened in binary and filtered before any `json.loads`:

- Skip any line containing neither `b'"tool_use"'` nor `b'"tool_result"'`.
- For a line that carries `tool_result` but no `tool_use`, require the literal
  `1\t` marker (`b'1\\t'` as it appears JSON-escaped) before decoding — this
  rejects the vast majority of large `tool_result` payloads (Bash output, Edit
  confirmations) without paying JSON decode.
- Byte-level `b'"isSidechain":true'` test on the main transcript, before decode.

Benchmarked (harness at `.scratch/bench-lines/bench.py`, raw results in
`run-warm.json` / `run-cold.json`) as **result-identical** to a naive full JSON
walk, and costing **+2.9 ms median** on the largest real YAS session (4.2 MB
across 11 files) — +6% of the ~48 ms in-process render, +2% of the ~130 ms
end-to-end tick. Worst case found (8.8 MB) was +9.1 ms.

*Alternative rejected:* a second dedicated walk over the same files (~2× the
measured cost for no structural benefit).

### 7. Return-shape change: `TranscriptToolStats` and `ToolCounts.per_agent`

`count_transcript` returns a small `TranscriptToolStats` value —
`counts: dict[str, int]`, `lines_read: int`, `lines_changed: int` — instead of a
bare dict. This is an internal breaking change with two call sites
(`ToolCounts.gather` and `test/test_tool_counts.py`).

`ToolCounts` gains `lines_read` / `lines_changed` (session totals: main plus every
subagent) and `per_agent: dict[str, tuple[int, int]]` keyed by transcript path —
the same key `gather` already iterates — so `build_wide` can look up a subagent's
own numbers by `sub.jsonl_path` without the renderer reaching into the view.

*Alternative rejected:* keying `per_agent` by `agent_id` — `jsonl_path` is what
`gather` already has in hand and is unique by construction.

### 8. Session segment sheds below 103 columns; `TOKENS_COST_MIN_WIDTH` stays 85

`tokens_cost` computes the with-segment `min_width` first and includes the new
segment only when
`box_width >= max(min_width_with_segment, LINES_SEGMENT_MIN_WIDTH)` with
`LINES_SEGMENT_MIN_WIDTH = 103`. Otherwise the segment **and its `│` divider**
are dropped and the method returns exactly today's shape — three segments, a
2-tuple of divider columns, and the without-segment `min_width`.

This mirrors the shed pattern already used by `elapsed_section`
(`layout.py:665-677`) and `cache_section` (`layout.py:651`). Bumping
`TOKENS_COST_MIN_WIDTH` to ~103 instead was explicitly rejected: it would drop
the whole tokens/cost row — and degrade the context line to
`context_line_compact` — for every terminal between 85 and 103 columns.

Because inclusion is gated on the *with-segment* floor, the row can never be
included at a width where it would overflow, so `tokens_fits` in `build_wide` is
unaffected.

Structural consequence: `tokens_cost`'s divider-column return becomes a
**variable-length tuple** — `(col1, col2)` when shed, `(col1, col2, col3)` when
present. `build_wide` (`layout.py:602`, `:999-1002`) must index from the end
(`vsep_cols[-2]`, `vsep_cols[-1]`) for the `cost` and `tokens over time`
captions, add a centred `lines read/changed` caption between `vsep_cols[0]` and
`vsep_cols[1]` when `len(vsep_cols) == 3`, and thread all of `vsep_cols` as
`downs`/`ups` (it already passes the tuple wholesale, so the elbow count follows
automatically).

### 9. Follow-up (out of scope): shared per-file gather cache

The three parsers (`count_transcript`, `TranscriptUsage.from_transcript`,
`parse_transcript`) each walk every transcript once per render — three passes
over the same bytes. An in-process, `(mtime, size)`-keyed per-file gather cache
shared by all three would collapse that to one. It is **not** part of this
change. Note the ceiling: YAS re-execs per tick, so such a cache only helps
*within* one render (and under the long-lived `mon` process) — it cannot amortise
across ticks. That is also why `subagents.py`'s existing `_TailCacheEntry` is
largely ineffective.

### 10. Per-subagent field is self-scoped and shed first

Each subagent row shows only its **own** transcript's numbers. No subtree
rollup onto parents; a `fork` counts to itself. The field joins the fixed-width
`tree_single` stats cluster next to share% / tokens / model, humanised with
`fmt_tok` (so `1.2k`, consistent with the token field), and renders **blank**
(spaces, preserving the fixed cluster width) when both numbers are zero — a `0`
would add noise to the many subagents that neither read nor write.

It is the **first** field dropped by `build_cluster`'s shed ladder, so any
terminal narrow enough to shed today behaves exactly as it does today.

*Alternative rejected:* rolling children into parents — it would double-count
against the session total and break the by-construction invariant of Decision 4.

### 11. Glyphs follow the existing PUA + fallback-table convention

Two new PUA constants in `constants.py` (`GLYPH_LINES_READ` = nf-md-eye
`U+F0208`, `GLYPH_LINES_CHANGED` = nf-fa-pencil `U+F040`), written as the
escapes `'\U000f0208'` and `'\uf040'` per the repo's PUA rule (never raw glyphs
in source). Glyph mode is a post-render
`str.translate` (`render/text.py:apply_glyphs`), so both constants need entries in
`ASCII_GLYPHS` (`R` and `W`) and `UNICODE_PUA`, plus a `GITHUB_ICON_OVERRIDE`
entry for any unicode target whose `unicodedata.east_asian_width` is `A`.

## Risks / Trade-offs

- **[Sidechain skip is not gate-defended]** → No session on this machine emits
  `isSidechain: true` records (this setup uses the async `Agent` tool with one
  `agent-*.jsonl` per subagent, not the synchronous `Task` inline-sidechain
  convention), so **no demo or transcript fixture can cover it end-to-end**. It is
  correctness-by-construction, defended only by a synthetic unit-test fixture
  written by hand.
- **[+2.9 ms per render]** → Measured, bounded (+9.1 ms worst case on an 8.8 MB
  session), and paid only on wide renders that read `tool_counts`. Mitigated by
  the V2 byte pre-filters; further mitigation deferred to Decision 9.
- **[`replace_all` undercounts]** → Accepted and documented; the alternative
  requires reading the edited file.
- **[21 demo fixtures need re-goldening]** → The tokens/cost row appears in 21 of
  ~23 `demo/text/*.txt` fixtures. Re-golden in one commit via `make demo/img` +
  the `yas-demo-text` skill, and review the diff for column drift rather than
  content change.
- **[Variable-length `vsep_cols`]** → A 2-or-3-tuple return is easy to index
  wrongly. Mitigated by indexing from the end for the two pre-existing captions
  and asserting divider/elbow agreement in `test_tokens_cost.py` and
  `test_layout_seam.py` at widths straddling 103.
