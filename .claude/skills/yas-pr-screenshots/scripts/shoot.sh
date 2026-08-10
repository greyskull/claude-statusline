#!/usr/bin/env bash
# Render before/after PNGs for one or more demo variants, combine each pair into
# a single side-by-side image, stage them into a local checkout of the
# yas-pr-screenshots repo, then print a markdown table.
#
#   shoot.sh <pr_id> <screenshots_repo_dir> <variant>...
#
# variant = LABEL:SCENARIO:ENV      (ENV optional)
#   LABEL     basename for the png + table row label      (e.g. narrow, claude-dark)
#   SCENARIO  ops/demo.py scenario name                   (e.g. kitchen-sink)
#   ENV       space-separated YAS_* knobs, may be empty   (e.g. 'YAS_MAX_WIDTH=40')
#
# Example:
#   shoot.sh 79 ../yas-pr-screenshots \
#     'kitchen-sink:kitchen-sink:' \
#     'narrow:kitchen-sink:YAS_MAX_WIDTH=40'
#
# "after"  = current working tree (the branch).
# "before" = main, rendered in a throwaway git worktree.
#
# Output layout in the screenshots repo:
#   screenshots/<pr_id>/<label>.png          combined before|after image (embedded)
#   screenshots/<pr_id>/before/<label>.png   raw before half (kept for reference)
#   screenshots/<pr_id>/after/<label>.png    raw after half  (kept for reference)
#
# The combined image is: each half topped by a small "before"/"after" heading,
# joined side by side with a visible vertical separator strip (ImageMagick).
# A before render that fails (e.g. a scenario new to this branch) yields a
# placeholder "not on main" left half rather than aborting.
set -euo pipefail

[ $# -ge 3 ] || { echo "usage: shoot.sh <pr_id> <screenshots_repo_dir> <variant>..." >&2; exit 2; }

pr_id=$1; repo=$2; shift 2
yas_root=$(git rev-parse --show-toplevel)
repo=$(cd "$repo" && pwd)
owner_repo='tmck-code/yas-pr-screenshots'
url_base="https://github.com/$owner_repo/blob/main/screenshots/$pr_id"

out_dir="$repo/screenshots/$pr_id"
before_dir="$out_dir/before"
after_dir="$out_dir/after"
mkdir -p "$before_dir" "$after_dir"

wt="$(mktemp -d)/yas-base"
git -C "$yas_root" worktree add -q "$wt" main
trap 'git -C "$yas_root" worktree remove --force "$wt" >/dev/null 2>&1 || true' EXIT

# Deterministic styling for headings / separator.
bg='#181818'; fg_txt='#c8c8c8'; sep='#444444'
title_h=28; sep_w=6

render() { # <tree-dir> <scenario> <env> <out-png>
  ( cd "$1" && env $3 DEMO_ONLY="$2" make demo/img >/dev/null 2>&1 ) || return 1
  if [ -f "$1/demo/$2.png" ]; then
    cp "$1/demo/$2.png" "$4"
  elif [ -f "$1/demo/subagents/$2.png" ]; then
    cp "$1/demo/subagents/$2.png" "$4"
  else
    return 1
  fi
}

titled() { # <in-png> <title> <out-png>  — stack a small heading bar on top
  local w; w=$(magick identify -format '%w' "$1")
  magick -size "${w}x${title_h}" "xc:$bg" -fill "$fg_txt" -pointsize 16 \
    -gravity center -annotate 0 "$2" "$1" -background "$bg" -append "$3"
}

combine() { # <before-png-or-empty> <after-png> <out-png>
  local tmp; tmp=$(mktemp -d)
  if [ -n "$1" ]; then
    titled "$1" "before" "$tmp/b.png"
  else
    local w h
    w=$(magick identify -format '%w' "$2"); h=$(magick identify -format '%h' "$2")
    magick -size "${w}x${h}" "xc:$bg" -fill "$fg_txt" -pointsize 16 \
      -gravity center -annotate 0 "(not on main)" "$tmp/raw.png"
    titled "$tmp/raw.png" "before" "$tmp/b.png"
  fi
  titled "$2" "after" "$tmp/a.png"
  local h; h=$(magick identify -format '%h' "$tmp/b.png")
  local h2; h2=$(magick identify -format '%h' "$tmp/a.png")
  [ "$h2" -gt "$h" ] && h=$h2
  magick "$tmp/b.png" -size "${sep_w}x${h}" "xc:$sep" "$tmp/a.png" \
    -background "$bg" -gravity north +append "$3"
  rm -rf "$tmp"
}

rows=()
for variant in "$@"; do
  IFS=: read -r label scenario env <<< "$variant"
  echo ">> $label  (scenario=$scenario${env:+ env=$env})" >&2

  render "$yas_root" "$scenario" "$env" "$after_dir/$label.png" \
    || { echo "   ERROR: after render failed for $label" >&2; exit 1; }

  before_png="$before_dir/$label.png"
  if ! render "$wt" "$scenario" "$env" "$before_png"; then
    echo "   note: before render failed on main (new to this branch?) — placeholder half" >&2
    rm -f "$before_png"
    before_png=""
  fi

  combine "$before_png" "$after_dir/$label.png" "$out_dir/$label.png"
  rows+=("| $label | ![${label}]($url_base/$label.png?raw=true) |")
done

# Markdown table on stdout — this is the handoff artifact.
echo
echo "|  | before / after |"
echo "|--|----------------|"
printf '%s\n' "${rows[@]}"
