#!/usr/bin/env bash
# Roll inventory/campus/{labs,machines,timetable}.yaml up into the capacity
# table that docs/campus/fleet-report.md is built around (PHASE-01B Task 5 /
# CAMPUS-FABRIC.md#10). Excludes labs with ventilation_verdict: inadequate
# from GPU totals, and machines with wolCapable: false from all totals.
source "$(dirname "$0")/../lib/common.sh"
need yq

labs="$ROOT/inventory/campus/labs.yaml"
machines="$ROOT/inventory/campus/machines.yaml"
timetable="$ROOT/inventory/campus/timetable.yaml"

for f in "$labs" "$machines" "$timetable"; do
  [[ -f "$f" ]] || die "missing $f — no surveyed campus data yet (see phases/PHASE-01B.md)"
done

# weekly_free_hours for a lab = 168 - sum(occupied weekly blocks, in hours)
free_hours_for() {
  local lab="$1"
  yq -r --arg lab "$lab" '
    .[] | select(.lab == $lab) | .weekly[]? |
    ( (.to | split(":") | (.[0]|tonumber) + (.[1]|tonumber)/60) -
      (.from | split(":") | (.[0]|tonumber) + (.[1]|tonumber)/60) )
  ' "$timetable" | awk -v total=168 '{sum+=$1} END{printf "%.2f", total-sum}'
}

echo "lab,machines_total,machines_gpu_discrete,machines_igpu,machines_cpu_only,cpu_cores_total,memory_bytes_total,weekly_free_hours,effective_hours,gpu_hours_per_week,core_hours_per_week"

total_gpu=0 total_core_hours=0 total_gpu_hours=0

for lab in $(yq -r '.[].id' "$labs"); do
  verdict="$(yq -r --arg lab "$lab" '.[] | select(.id==$lab) | .power.ventilation_verdict' "$labs")"

  m_total=$(yq -r --arg lab "$lab" '[.[] | select(.lab==$lab and .network.wolCapable==true)] | length' "$machines")
  m_gpu=$(yq -r --arg lab "$lab" '[.[] | select(.lab==$lab and .network.wolCapable==true and (.gpu[]?.class=="consumer-discrete"))] | length' "$machines")
  m_igpu=$(yq -r --arg lab "$lab" '[.[] | select(.lab==$lab and .network.wolCapable==true and (.gpu[]?.class=="igpu"))] | length' "$machines")
  m_cpu=$(( m_total - m_gpu - m_igpu ))
  cores=$(yq -r --arg lab "$lab" '[.[] | select(.lab==$lab and .network.wolCapable==true) | .cpu.cores] | add // 0' "$machines")
  mem=$(yq -r --arg lab "$lab" '[.[] | select(.lab==$lab and .network.wolCapable==true) | .memory.measuredBytes] | add // 0' "$machines")

  free_h=$(free_hours_for "$lab")
  eff_h=$(awk -v f="$free_h" 'BEGIN{printf "%.2f", f*0.94}')

  if [[ "$verdict" == "inadequate" ]]; then
    gpu_h=0
  else
    gpu_h=$(awk -v g="$m_gpu" -v e="$eff_h" 'BEGIN{printf "%.2f", g*e}')
  fi
  core_h=$(awk -v c="$cores" -v e="$eff_h" 'BEGIN{printf "%.2f", c*e}')

  echo "$lab,$m_total,$m_gpu,$m_igpu,$m_cpu,$cores,$mem,$free_h,$eff_h,$gpu_h,$core_h"

  total_gpu=$((total_gpu + m_gpu))
  total_gpu_hours=$(awk -v a="$total_gpu_hours" -v b="$gpu_h" 'BEGIN{printf "%.2f", a+b}')
  total_core_hours=$(awk -v a="$total_core_hours" -v b="$core_h" 'BEGIN{printf "%.2f", a+b}')
done

log ""
log "TOTAL gpu_hours_per_week=$total_gpu_hours core_hours_per_week=$total_core_hours"
log "(inadequate-ventilation labs excluded from GPU totals; wolCapable:false machines excluded from all totals)"
