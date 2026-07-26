# prototype/

Point-in-time, plain-text (ANSI-stripped) renders of the YAS statusline in
**ASCII glyph mode** (`YAS_GLYPH_MODE=ascii`), captured to sanity-check the
subagent-tree-row work landed on this branch. Nerd Font PUA icons don't
paste or display outside a Nerd-Font terminal, so ASCII mode is used here to
keep these files legible in any editor or PR review.

These are **snapshots, not a maintained gate** — they will drift as the
renderer changes and nobody is expected to keep them in sync. The real gates
are `make test` and the `make demo` / `make demo/img` visual check (see
`.claude/skills/tmck-code-statusline/SKILL.md`).

## Files

- `subagents-width-sweep.txt` — the single-column tree-mode SUBAGENTS
  section (`subagent-tree-lines` demo scenario) at three widths (115 / 160 /
  280), showing the reordered stats cluster (`desc · lines · tok (share%) ·
  model · activity`), constant-width 3-sig-fig numbers, blank-on-zero
  read/changed fields, the new header labels (`name` / `loc read / written`
  / `model` / `current activity`), and the elastic-width behaviour across
  the narrow/medium/wide thresholds.
- `subagents-real-cohort-wide.txt` — the same section rendered against a
  genuine mined 6-agent production fan-out (`subagent-tree-cohort1`) at
  width 220, to see the columns hold up against real descriptions and a
  wide token-magnitude range.
- `subagent-tree-plan-side-by-side.txt` — the plan-checklist + tree-mode
  cohort side-by-side layout (`subagent-tree-plan`) across the same width
  range, included specifically because it still only labels the two group
  headers (`plan` / `subagents`) and does **not** carry the per-column
  labels into the subagents half — a gap worth seeing next to the
  single-column renders above.

## How these were generated

From the repo root, using the existing demo harness (no new tooling):

```bash
env -u TMUX_PANE -u TMUX COLUMNS=<width> YAS_GLYPH_MODE=ascii YAS_LABELS=true \
  DEMO_ONLY=<scenario> uv run python ops/demo.py --snapshots <tmp-dir>
.claude/skills/yas-demo-text/scripts/strip-ansi.sh <tmp-dir>/<scenario>.txt
```

`-u TMUX_PANE -u TMUX` is required: `terminal_width()` prefers the real tmux
pane width over `$COLUMNS` when present, so without unsetting those every
width in a sweep would silently render identically. `YAS_LABELS=true` turns
on the column-label overlay (labels are stamped directly onto the
`separator_dim` border row at each column's anchor position, which is why
the border dashes read as fused words like `loc read / writ...model`). Each
scenario's own `yas.toml` (baked into the demo fixture) sets
`subagent_tree = true` and a wide enough `max_width` for the sweep to reach
the "wide" tier without being clamped by the 140-col default.

Every rendered width in these files was verified against its intended
`COLUMNS` value (visible column count of the top-border row) before being
committed.
