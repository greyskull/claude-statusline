#!/bin/bash

DEMO="${1:-kitchen-sink}"

mkdir -p demos demo
rm -rf demos/* demo/*

function demo {
  local demo="$1"
  local i="$2"
  echo -e "------\n$i" >&2
  DEMO_ONLY="$demo" SKIP_PNG=1 COLUMNS=800 YAS_JUSTIFY=1 YAS_MAX_WIDTH="$i" make demo/img
  cat "demo/${demo}.txt" > "demos/${i}.txt"
}
export -f demo

for i in $(seq -w 1 400); do
  echo "$i"
done | parallel -n1 -P12 -I{} demo "$DEMO" {}

clear -x
echo -ne "\e[?25l"

for i in demos/*; do
  printf '\033[H'
  echo -e "${i}\033[K\n"
  while IFS= read -r line; do
    echo -e "${line}\033[K"
  done < "$i"
  sleep 0.05
done
echo -ne "\e[?25h"
