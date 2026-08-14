#!/bin/bash
#
# Width sweep: render demo scenarios at every max_width in a range and store each
# scenario's frames in its own gzipped, delimited archive under demo/widths/.
#
# An archive is *storage*, not a playback stream: no cursor control, no \033[K,
# frames separated by a form feed and headed by their width. SGR colour codes are
# kept -- they are the thing under test. Playback/extraction/diffing are reader
# concerns, below.
#
#   ./f.sh render [scenario]     sweep one scenario (default kitchen-sink)
#   ./f.sh list                  list the archives that exist
#   ./f.sh play [scenario]       replay an archive as an animation
#   ./f.sh extract <width> [sc]  print one width's frame
#   ./f.sh changes [scenario]    list only the widths where output changed
#
# Env: FROM/TO override the width range, JOBS the parallelism (default: half
# the machine's cores; set JOBS higher to trade responsiveness for speed).
#
# Glyph mode defaults to ascii, not the renderer's usual nerdfont: an archive is
# measured, not looked at, and Nerd Font PUA glyphs have no dependable terminal
# cell width, so column positions in a nerdfont sweep can't be interpreted. Set
# YAS_GLYPH_MODE=nerdfont to sweep the real glyphs anyway.

set -uo pipefail

FROM="${FROM:-1}"
TO="${TO:-350}"
# Half the cores, not all of them: a sweep is a background chore, and each job
# is a full uv/python start-up, so saturating every core makes the machine
# unusable for the length of the run without finishing it much sooner.
JOBS="${JOBS:-$(( $( (nproc 2>/dev/null || echo 4) ) / 2 ))}"
(( JOBS < 1 )) && JOBS=1
OUT="${OUT:-demo/widths}"
YAS_GLYPH_MODE="${YAS_GLYPH_MODE:-ascii}"
export OUT YAS_GLYPH_MODE   # parallel re-execs render_width in a fresh shell; it needs these
SCENARIO="${DEMO:-kitchen-sink}"

# Render one width into a per-width staging dir. Left as raw .txt files;
# fold_archives turns them into per-scenario archives afterwards, since
# interleaved parallel appends to one archive would scramble width order.
#
# DEMO_ONLY (optional) restricts the render to a single scenario -- one process
# renders all 44 snapshots anyway, so the sweep costs the same either way.
function render_width {
  local width="$1"
  local only="${2:-}"
  local dir="$OUT/.raw/$width"

  DEMO_ONLY="$only" COLUMNS=800 YAS_JUSTIFY=1 YAS_MAX_WIDTH="$width" \
    YAS_GLYPH_MODE="$YAS_GLYPH_MODE" \
    uv run python3 ops/demo.py --snapshots "$dir/" >/dev/null 2>&1
}
export -f render_width

# Fold the staged .txt files into one archive per scenario, in width order.
# Scenario name is the staged path minus the width dir and .txt, so nested
# renders (subagents/, themes/dark/) keep a unique, path-shaped name.
function fold_archives {
  local raw="$OUT/.raw"
  local scenarios
  scenarios=$(cd "$raw" && find . -mindepth 2 -name '*.txt' -printf '%P\n' \
    | sed 's|^[0-9]*/||; s|\.txt$||' | sort -u)

  local n=0
  while IFS= read -r sc; do
    [[ -n "$sc" ]] || continue
    local archive="$OUT/${sc}.txt.gz"
    mkdir -p "$(dirname "$archive")"
    {
      for w in $(seq "$FROM" "$TO"); do
        local f="$raw/$w/${sc}.txt"
        [[ -f "$f" ]] || continue
        printf '=== width %s\n' "$w"
        cat "$f"
        printf '\f\n'
      done
    } | gzip > "$archive"
    n=$((n + 1))
  done <<< "$scenarios"

  rm -rf "$raw"
  printf 'wrote %s archives to %s/ (%s)\n' \
    "$n" "$OUT" "$(du -sh "$OUT" | cut -f1)" >&2
}

function sweep {
  local only="${1:-}"
  mkdir -p "$OUT"
  rm -rf "$OUT/.raw"
  seq "$FROM" "$TO" | parallel -n1 -P"$JOBS" -I{} render_width {} "$only"
  fold_archives
}

function archive_path {
  local sc="${1:-$SCENARIO}"
  local p="$OUT/${sc}.txt.gz"
  if [[ ! -f "$p" ]]; then
    printf 'no archive for "%s" -- run ./f.sh render %s, or ./f.sh list\n' "$sc" "$sc" >&2
    exit 1
  fi
  printf '%s' "$p"
}

function do_list {
  find "$OUT" -name '*.txt.gz' -printf '%P\n' 2>/dev/null \
    | sed 's|\.txt\.gz$||' | sort
}

# Replay an archive: cursor-home each frame and re-add the erase-to-EOL that the
# archive deliberately omits, so short lines don't leave residue from the last.
function do_play {
  local archive delay="${DELAY:-0.02}"
  archive=$(archive_path "${1:-}") || exit 1
  clear -x
  printf '\e[?25l'
  trap 'printf "\e[?25h"' EXIT
  while IFS= read -r line; do
    case "$line" in
      $'\f') sleep "$delay" ;;
      '=== width '*) printf '\e[H%s\e[K\n\n' "$line" ;;
      *) printf '%s\e[K\n' "$line" ;;
    esac
  done < <(zcat "$archive")
  printf '\e[J'
}

function do_extract {
  local want="$1" archive
  archive=$(archive_path "${2:-}") || exit 1
  zcat "$archive" | awk -v w="$want" '
    /^=== width /  { on = ($3 == w); next }
    /^\f$/         { if (on) exit; next }
    on             { print }
  '
}

# Hash each frame and report only the widths where the rendered output differs
# from the previous width -- the layout transitions worth actually looking at.
function do_changes {
  local archive
  archive=$(archive_path "${1:-}") || exit 1
  zcat "$archive" | awk '
    /^=== width / { w = $3; body = ""; next }
    /^\f$/        { if (body != prev) { print w; prev = body } next }
                  { body = body $0 "\n" }
  '
}

case "${1:-render}" in
  render)     sweep "${2:-$SCENARIO}" ;;
  list)       do_list ;;
  play)       do_play "${2:-}" ;;
  extract)    do_extract "${2:?usage: f.sh extract <width> [scenario]}" "${3:-}" ;;
  changes)    do_changes "${2:-}" ;;
  *) printf 'usage: f.sh [render <sc>|list|play <sc>|extract <w> <sc>|changes <sc>]\n' >&2
     exit 2 ;;
esac
