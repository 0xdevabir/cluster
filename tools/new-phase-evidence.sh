#!/usr/bin/env bash
# Scaffold evidence for a phase: tools/new-phase-evidence.sh 07
source "$(dirname "$0")/lib/common.sh"

[[ $# -eq 1 ]] || die "usage: $(basename "$0") <phase-number>"

num="$(printf '%02d' "$((10#$1))")"
dest="$ROOT/evidence/phase-$num"

if [[ -d "$dest" ]]; then
  warn "$dest already exists — leaving it untouched"
else
  mkdir -p "$dest"
  for tmpl in "$ROOT"/evidence/_template/*.md; do
    name="$(basename "$tmpl")"
    sed "s/YYYY-MM-DD/$(date +%Y-%m-%d)/; s/Phase NN/Phase $num/" "$tmpl" > "$dest/$name"
  done
  ok "created $dest"
fi
