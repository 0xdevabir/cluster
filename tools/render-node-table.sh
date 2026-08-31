#!/usr/bin/env bash
# Render a human-readable node table from inventory/nodes/*.yaml
source "$(dirname "$0")/lib/common.sh"
need yq

nodes_dir="$ROOT/inventory/nodes"
shopt -s nullglob
files=("$nodes_dir"/*.yaml)

if [[ ${#files[@]} -eq 0 ]]; then
  log "no nodes in inventory/nodes/ yet"
  exit 0
fi

printf '%-16s %-14s %-10s %-6s %-8s %-6s\n' "NAME" "ARCHETYPE" "RACK/U" "CORES" "MEM(GB)" "GPUS"
for f in "${files[@]}"; do
  [[ "$(basename "$f")" == *.example ]] && continue
  yq -r '[.metadata.name, .spec.archetype, "\(.spec.location.rack)/\(.spec.location.u)", .spec.cpu.cores, .spec.memory.totalGB, (.spec.gpus | length)] | @tsv' "$f"
done | awk -F'\t' '{printf "%-16s %-14s %-10s %-6s %-8s %-6s\n", $1, $2, $3, $4, $5, $6}'
