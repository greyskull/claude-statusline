#!/bin/bash
#
# Width sweep: render one demo scenario at every max_width in a range and store
# the frames in a single gzipped, delimited archive.
#
# The archive is *storage*, not a playback stream: no cursor control, no \033[K,
# frames separated by a form feed and headed by their width. SGR colour codes are
# kept — they are the thing under test. Playback/extraction/diffing are reader
# concerns, below.
#
#   ./f.sh render [scenario]     render the sweep into the archive (default)
#   ./f.sh play                  replay the archive as an animation
#   ./f.sh extract <width>       print one width's frame
#   ./f.sh changes               list only the widths where output changed
#
# Env: FROM/TO override the width range, JOBS the parallelism.

set -uo pipefail

DEMO="${DEMO:-kitchen-sink}"
FROM="${FROM:-1}"
TO="${TO:-350}"
JOBS="${JOBS:-12}"
ARCHIVE="${ARCHIVE:-demo/widths.txt.gz}"

# Render one width and emit its frame: a header line with the width, the frame
# body, then a form feed terminating the record.
function demo {
  local demo="$1"
  local i="$2"
  # Per-job snapshot dir: parallel renders would otherwise race on demo/<demo>.txt.
  local dir="demo/w$i"
  local out="$dir/${demo}.txt"

  DEMO_ONLY="$demo" COLUMNS=800 YAS_JUSTIFY=1 YAS_MAX_WIDTH="$i" \
    uv run python3 ops/demo.py --snapshots "$dir/" >&2 || return 0
  [[ -f "$out" ]] || return 0

  printf '=== width %s\n' "$i"
  cat "$out"
  printf '\f\n'
  rm -rf "$dir"
}
export -f demo

function do_render {
  mkdir -p "$(dirname "$ARCHIVE")"
  rm -f "$ARCHIVE"
  # -k releases each job's output in width order, so the archive stays sorted by
  # width while the renders themselves run $JOBS-wide.
  seq "$FROM" "$TO" \
    | parallel -k -n1 -P"$JOBS" -I{} demo "$DEMO" {} \
    | gzip > "$ARCHIVE"
  printf 'wrote %s (%s frames, %s)\n' \
    "$ARCHIVE" "$(zgrep -c '^=== width ' "$ARCHIVE")" \
    "$(du -h "$ARCHIVE" | cut -f1)" >&2
}

# Replay the archive: cursor-home each frame and re-add the erase-to-EOL that the
# archive deliberately omits, so short lines don't leave residue from the last.
function do_play {
  local delay="${DELAY:-0.02}"
  clear -x
  printf '\e[?25l'
  trap 'printf "\e[?25h"' EXIT
  while IFS= read -r line; do
    case "$line" in
      $'\f') sleep "$delay" ;;
      '=== width '*) printf '\e[H%s\e[K\n\n' "$line" ;;
      *) printf '%s\e[K\n' "$line" ;;
    esac
  done < <(zcat "$ARCHIVE")
  printf '\e[J'
}

function do_extract {
  local want="$1"
  zcat "$ARCHIVE" | awk -v w="$want" '
    /^=== width /  { on = ($3 == w); next }
    /^\f$/         { if (on) exit; next }
    on             { print }
  '
}

# Hash each frame and report only the widths where the rendered output differs
# from the previous width — the layout transitions worth actually looking at.
function do_changes {
  zcat "$ARCHIVE" | awk '
    /^=== width / { w = $3; body = ""; next }
    /^\f$/        { if (body != prev) { print w; prev = body } next }
                  { body = body $0 "\n" }
  '
}

case "${1:-render}" in
  render)  [[ $# -ge 2 ]] && DEMO="$2"; do_render ;;
  play)    do_play ;;
  extract) do_extract "${2:?usage: f.sh extract <width>}" ;;
  changes) do_changes ;;
  *)       printf 'usage: f.sh [render <scenario>|play|extract <width>|changes]\n' >&2; exit 2 ;;
esac
