## 1. Constants and glyphs

- [x] 1.1 In `claude/yas/constants.py`, alongside `TOKENS_COST_MIN_WIDTH = 85` (line ~55), add `LINES_SEGMENT_MIN_WIDTH = 103` with a comment stating that it gates ONLY the lines segment, that `TOKENS_COST_MIN_WIDTH` must stay at 85, and why (bumping it would regress every 85–103-column terminal into `context_line_compact`).
- [x] 1.2 In `claude/yas/constants.py`, in the Nerd Font PUA block (near `GLYPH_MODEL`, line ~151), add `GLYPH_LINES_READ = '\U000f0208'  # nf-md-eye` and `GLYPH_LINES_CHANGED = '\uf040'  # nf-fa-pencil`. Write them as escapes — never raw PUA glyphs in source (see the PUA refactor rule in the `tmck-code-statusline` skill).
- [x] 1.3 Add both glyphs to `ASCII_GLYPHS` (line ~229): `GLYPH_LINES_READ: 'R'`, `GLYPH_LINES_CHANGED: 'W'`.
- [x] 1.4 Add both glyphs to `UNICODE_PUA` (line ~324) with single non-PUA width-1 BMP substitutes (suggested: `⌖` for read, `✎` for changed). Then run `python3 -c "import unicodedata as u; print(u.east_asian_width('⌖'), u.east_asian_width('✎'))"` and, for any target reporting `A`, add an EAW-narrow entry to `GITHUB_ICON_OVERRIDE` (line ~372) as the existing five do.
- [x] 1.5 Add `LINES_LABEL = 'lines read/changed'` next to `TOOL_COUNTS_LABEL` (line ~109). Plain ASCII — the separator overlay applies `superscript()` itself.

## 2. Counting: `claude/yas/info/toolcounts.py`

- [x] 2.1 Add a `TranscriptToolStats` value (slots class or `NamedTuple`, matching the module's style) with fields `counts: dict[str, int]`, `lines_read: int`, `lines_changed: int`.
- [x] 2.2 Change `count_transcript(path, clear_epoch)` (line 27) to `count_transcript(path: str, clear_epoch: float | None, *, skip_sidechain: bool) -> TranscriptToolStats`. Keep the existing last-write-wins-per-`message.id` tool_use dedup and the `META_EXCLUDE_TOOLS` / `name.split('__')[-1]` handling exactly as they are — the module docstring's warning about first-wins undercounting still applies.
- [x] 2.3 Switch the file open to binary (`open(path, 'rb')`) and apply the V2 pre-filters per line, before any `json.loads`:
      (a) skip when the line contains neither `b'"tool_use"'` nor `b'"tool_result"'`;
      (b) when the line contains `b'"tool_result"'` but not `b'"tool_use"'`, skip unless it also contains the JSON-escaped `cat -n` marker `b'1\\t'`;
      (c) when `skip_sidechain` is true, skip lines containing `b'"isSidechain":true'` (also test the spaced variant `b'"isSidechain": true'`).
      Decode surviving lines with `json.loads(raw)` (`json` accepts bytes) inside the existing `try/except (ValueError, TypeError)`.
- [x] 2.4 Keep the existing `clear_epoch` guard (`_parse_iso_to_epoch(d.get('timestamp',''))< clear_epoch → continue`) as the single window for BOTH the tool counts and the new line counts.
- [x] 2.5 While walking `msg['content']` blocks for `tool_use`, additionally record file-activity per block: for `name == 'Read'`, remember `block['id']` in a `read_ids: set[str]`; for `name == 'Edit'`, add `max(newlines(old_string), newlines(new_string))` to `lines_changed`; for `name == 'Write'`, add `newlines(content)`. Use a local `def _nl(s: object) -> int: return s.count('\n') if isinstance(s, str) else 0`. Do NOT count `NotebookEdit`.
- [x] 2.6 Handle `tool_result` blocks: they live under `message.content[]` on `user`-type records. For each block with `type == 'tool_result'` whose `tool_use_id` is in `read_ids`, take `content`; if it `isinstance(content, str)` and `content.startswith('1\t')`, add `content.count('\n')` to `lines_read`. Skip list-valued content (image/document reads) and strings not starting with `1\t`. Never use `offset`/`limit` from the `Read` input.
- [x] 2.7 Note the ordering constraint in a comment: a `tool_result` always appears on a LATER line than its `tool_use`, so a single forward pass with `read_ids` accumulated as it goes is sufficient — no second pass, no lookahead.
- [x] 2.8 The `lines_changed` accumulation must respect the same last-write-wins dedup as the tool counts: accumulate per `message.id` into a `per_id_changed: dict[str, int]` overwritten on each write of that id, and sum at the end — otherwise a streamed message's partial writes double-count its edits.
- [x] 2.9 Extend `ToolCounts.__slots__` (line 80) with `lines_read`, `lines_changed`, and `per_agent: dict[str, tuple[int, int]]` (key: transcript path). Update `__init__`, `__eq__`, and `__repr__` to include them.
- [x] 2.10 Update `ToolCounts.gather` (line 104): call `count_transcript(main_path, clear_epoch, skip_sidechain=True)` for the main transcript and `count_transcript(agent.jsonl_path, clear_epoch, skip_sidechain=False)` for each subagent; sum `lines_read`/`lines_changed` across main + all subagents into the session totals, and store each subagent's own pair in `per_agent[agent.jsonl_path]`. Add a comment stating the asymmetry is deliberate and that skipping sidechain records in subagent files zeroes the entire subagent contribution.
- [x] 2.11 Extend the module docstring to document: the three in-scope tools, the `1\t` sniff test, the `replace_all` undercount, and the main-vs-subagent sidechain asymmetry.

## 3. Gather seam

- [x] 3.1 In `claude/yas/info/__init__.py`, update the `tool_counts` `@cached_property` docstring (line ~122) to state that the same single pass now also yields the session `lines_read`/`lines_changed` totals and the `per_agent` breakdown. No signature change: `ToolCounts.gather(self.session.transcript_path, self.subagents.subagents, self.clear_epoch)` is unchanged.

## 4. Renderer: tokens/cost row segment

- [x] 4.1 In `claude/yas/renderer.py`, add a keyword parameter `lines: tuple[int, int] | None = None` to `Renderer.tokens_cost` (line 1316) and widen its return annotation's divider element to `tuple[int, ...]`.
- [x] 4.2 Build the segment with a local `build_lines()` returning `f'{GLYPH_LINES_READ}  {fmt_tok(read)}{sep}{GLYPH_LINES_CHANGED}  {fmt_tok(changed)}'` in the row's existing colour vocabulary (`self.TOK` for values, `self.LABEL` for the glyphs/separator), measured with `_visible_width` — never `len()`.
- [x] 4.3 Compute `min_width` twice: the existing without-segment value (line 1416, unchanged formula) and a with-segment value adding `lines_w + vsep_lines_w` (use `vsep_lines_w = 4`, matching `vsep_w`/`vsep_leader_w`). Include the segment only when `lines is not None and box_width >= max(min_width_with_lines, LINES_SEGMENT_MIN_WIDTH)`; otherwise shed it. Return the WITHOUT-segment `min_width` when shed so `tokens_fits` in `build_wide` is unaffected.
- [x] 4.4 When included: subtract `vsep_lines_w` from `inner` (line 1388), give the segment a content-measured budget floored at its own width (same "honest floor" pattern as `w_middle`/`w_end`, lines 1472-1475), place its divider column between `col1` and the cost divider, and emit `self.vsep_block(...)` for it. The leader keeps absorbing the remainder via `leader_w = max(label_w + 1, inner - w_middle - w_lines - w_end)`.
- [x] 4.5 Return `[line], (col1, col2, col3), 0, min_width` when included and `[line], (col1, col2), 0, min_width` when shed. Update the method docstring to describe both shapes and the shed rule.
- [x] 4.6 Leave the `justify` block (lines 1418-1454) semantics intact: the new segment takes no justify pad slot in this change; note that in a comment so a later reader does not assume an omission.

## 5. Renderer: per-subagent lines field

- [x] 5.1 In `Renderer.subagent_row` (line 753), add a keyword parameter `lines: tuple[int, int] | None = None`.
- [x] 5.2 Near the `share_str`/`tok_field` construction (lines 856-874), build `lines_field` as `f'{GLYPH_LINES_READ} {fmt_tok(read)} {GLYPH_LINES_CHANGED} {fmt_tok(changed)}'`; when `lines` is `None` or both values are 0, set it to `' ' * <that same fixed width>` so the cluster width stays deterministic (same reasoning as the `rjust(6)` comment at lines 858-862 and `SUBAGENT_STATS_ACTIVITY_GAP`). In `tree_single` mode pad each humanised number to a fixed width (`rjust(6)`, matching `tok_field`).
- [x] 5.3 Change `build_cluster` (line 894) to `build_cluster(show_lines, show_share, show_tok)` and emit the lines field first in the segment, before share%, using the same done/live colour split (`self.CTX_DIM` when `is_done`).
- [x] 5.4 Update the shed ladder at line 924 from `((True, True), (False, True))` to `((True, True, True), (False, True, True), (False, False, True))`, and update the model-only fallback calls at lines 916 and 923 to `build_cluster(False, False, False)`. This makes lines the first field shed.
- [x] 5.5 Verify the non-`tree_single` (classic two-line) path still renders correctly: the lines field participates in the same ladder, so a narrow classic row sheds it first and matches today's output.

## 6. Layout wiring

- [x] 6.1 In `claude/yas/layout.py` `build_wide`, pass the session totals into the row: `lines=(view.tool_counts.lines_read, view.tool_counts.lines_changed)` in the `r.tokens_cost(...)` call at line 602. Note this now forces `view.tool_counts` evaluation on every wide render (previously only when `cfg.show_tool_uses`); that is the accepted +2.9 ms measured in design.md Decision 6.
- [x] 6.2 `vsep_cols` is now variable-length. Update the label block at lines 995-1001 to index from the END: `_cost_mid = (vsep_cols[-2] + vsep_cols[-1]) // 2`, `tok_labels.append((_cost_lbl, max(vsep_cols[-2] + 1, ...)))`, and `tok_labels.append(('tokens over time', vsep_cols[-1] + 2))`.
- [x] 6.3 When `len(vsep_cols) == 3`, append a `LINES_LABEL` caption centred between `vsep_cols[0]` and `vsep_cols[1]`, mirroring the `cost` centring (with the same `max(vsep_cols[0] + 1, ...)` left clamp so it never cannibalises the token labels).
- [x] 6.4 `RowSpec('separator_dim', downs=vsep_cols, labels=tok_labels)` (line 1002) and `pending_ups = vsep_cols if tokens_fits else ()` (line 1010) already pass the tuple wholesale — confirm the elbow count follows automatically for both the 2- and 3-tuple forms and that no call site indexes `vsep_cols[1]` assuming it is the last element.
- [x] 6.5 At every `r.subagent_row(...)` call site in `build_wide` (tree and non-tree paths, around lines 1048-1147), pass `lines=view.tool_counts.per_agent.get(sub.jsonl_path)`. Do NOT add this to `build_narrow`/`build_medium` — those paths must keep `tool_counts` unevaluated (see the `statusline-info` laziness requirement).

## 7. Tests

- [x] 7.1 `test/test_tool_counts.py`: update every existing `count_transcript` call for the new `skip_sidechain` keyword and the `TranscriptToolStats` return (assert on `.counts` instead of the bare dict). Confirm the existing tool-count assertions still pass unchanged.
- [x] 7.2 New counting tests (same file or a new `test/test_line_counts.py`), each from a hand-written jsonl fixture in `tmp_path`: (a) `cat -n` string result counts its newlines; (b) list-valued image result contributes 0; (c) non-`1\t` string result contributes 0; (d) `Edit` takes `max(old, new)`; (e) `Write` counts `content`; (f) `NotebookEdit` contributes 0; (g) `replace_all: true` counts once (document the accepted undercount in the test name).
- [x] 7.3 Sidechain tests: a main-transcript fixture with `isSidechain: true` records is skipped under `skip_sidechain=True`; the SAME fixture under `skip_sidechain=False` counts in full. State in a comment that no real session on this machine emits these records, so this synthetic fixture is the only defence (design.md Risks).
- [x] 7.4 `clear_epoch` test: records before the epoch contribute nothing to `lines_read`/`lines_changed`; `clear_epoch=None` counts everything.
- [x] 7.5 Invariant test: build a main fixture plus two subagent fixtures, call `ToolCounts.gather`, and assert `lines_read == main + sum(per_agent)` and likewise for `lines_changed`.
- [x] 7.6 Pre-filter equivalence test: run the pre-filtered walk and a naive "decode every line" reference over the same fixture and assert identical `counts`, `lines_read`, `lines_changed`.
- [x] 7.7 `test/test_tokens_cost.py` (23 existing tests): extend the divider/min-width/justify assertions for the variable-length `vsep_cols`. Add: segment absent at box 85–102 with a 2-tuple returned and output byte-identical to the pre-change render; segment present at box 103+ with a 3-tuple; every `│` in the rendered line matches its reported divider column in both forms (extend `test_tokens_cost_divider_cols_match_rendered_bars`, line 49, and `test_tokens_cost_dividers_match_rendered_at_narrow_boxes`, line 190); `min_width` never rises above the pre-change value when the segment is shed (extend `test_tokens_cost_min_width_is_consistent_with_fit`, line 303); the sparkline still degrades at `bar_w < 10` with the segment present (extend line 199).
- [x] 7.8 `test/test_layout_seam.py`: assert three elbows are threaded at a wide box and two at a box in the 85–102 band, and that the row is NOT dropped to `context_line_compact` anywhere at or above 85.
- [x] 7.9 `test/test_subagent_rows.py`: lines field appears in the cluster with the correct humanised values; blank (spaces, not `0`) when both are 0; cluster width identical between an idle and a populated row; shed order is lines → share% → tok under decreasing widths; a narrow width that sheds the field renders byte-identically to the pre-change output.
- [x] 7.10 Self-scoping test: a parent and a child subagent with different figures render their own numbers, with no rollup onto the parent.
- [x] 7.11 Run the gate via the `verifier` agent (`make test`), not on the main thread.

## 8. Visual gate and docs

- [x] 8.1 Capture the baseline BEFORE any renderer edits: `make demo/img && .claude/skills/yas-demo-text/scripts/demo-text.sh && cp -r demo/text /tmp/yas-base`.
- [x] 8.2 Re-golden the fixtures: the tokens/cost row appears in 21 of ~23 `demo/text/*.txt` files (`kitchen-sink`, `full-context`, `openspec`, `tasks`, `subagents`, `workflows`, `opus-thinking`, `sonnet-thinking`, `config-error`, all `cohort-*`, all `subagent-tree-*`). Re-run `make demo/img` + `.claude/skills/yas-demo-text/scripts/demo-text.sh`, `diff -ru /tmp/yas-base demo/text`, and review every diff for column drift rather than content change. Commit the re-goldened fixtures.
- [x] 8.3 Check the demo scenarios' rendering widths: confirm at least one fixture renders in the 85–102 band (segment shed) and one at 103+ (segment present); if none exists in the shed band, add a scenario or note the gap explicitly.
- [x] 8.4 Verify glyph modes end to end: render once per `glyph_mode` (`nerdfont`, `ascii`, `unicode`, `github`) and confirm the two new glyphs fold to width-1 replacements and the box geometry is unchanged (`ascii` must show `R` and `W`).
- [x] 8.5 `CONTEXT.md`: add glossary entries for **Lines Read** (newlines in the `cat -n` result of a `Read`; image reads excluded) and **Lines Changed** (`Edit` → larger of old/new hunk, `Write` → whole content; `replace_all` counted once — a documented undercount), plus a note that the tokens/cost row's lines segment is the session total (main + all subagents) while the per-subagent field is self-scoped.
- [x] 8.6 Run `uv run ruff check` (via `verifier`).
