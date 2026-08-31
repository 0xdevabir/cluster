#!/usr/bin/env bash
# Aggregate fleet totals (cores, VRAM, storage, power) from inventory/nodes/*.yaml
source "$(dirname "$0")/lib/common.sh"
need yq
need jq

nodes_dir="$ROOT/inventory/nodes"
shopt -s nullglob
files=("$nodes_dir"/*.yaml)

if [[ ${#files[@]} -eq 0 ]]; then
  log "no nodes in inventory/nodes/ yet"
  exit 0
fi

total_nodes=0 total_cores=0 total_mem=0 total_gpus=0 total_vram=0 total_storage=0

for f in "${files[@]}"; do
  [[ "$(basename "$f")" == *.example ]] && continue
  total_nodes=$((total_nodes + 1))
  total_cores=$((total_cores + $(yq -r '.spec.cpu.cores' "$f")))
  total_mem=$((total_mem + $(yq -r '.spec.memory.totalGB' "$f")))
  total_gpus=$((total_gpus + $(yq -r '.spec.gpus | length' "$f")))
  total_vram=$((total_vram + $(yq -r '[.spec.gpus[].vramGB] | add // 0' "$f")))
  total_storage=$((total_storage + $(yq -r '[.spec.storage[].sizeGB] | add // 0' "$f")))
done

log "Fleet totals across $total_nodes node(s):"
log "  CPU cores:    $total_cores"
log "  Memory (GB):  $total_mem"
log "  GPUs:         $total_gpus"
log "  VRAM (GB):    $total_vram"
log "  Storage (GB): $total_storage"
