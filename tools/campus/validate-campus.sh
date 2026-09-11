#!/usr/bin/env bash
# Validate campus survey inventory: schema + the cross-reference and policy
# checks a JSON Schema cannot express (PHASE-01B Task 6). Exits non-zero on
# the first class of failure found — mirrors tools/validate-inventory.sh.
source "$(dirname "$0")/../lib/common.sh"
need check-jsonschema
need yq

campus_dir="$ROOT/inventory/campus"
schema_dir="$ROOT/inventory/schema"
fail=0
shopt -s nullglob

validate_file() {
  local f="$1" schema="$2"
  [[ -f "$f" ]] || return 0
  [[ "$(basename "$f")" == *.example ]] && return 0
  if check-jsonschema --schemafile "$schema_dir/$schema" "$f" >/dev/null 2>&1; then
    ok "$(basename "$f")"
  else
    warn "FAILED: $f"
    check-jsonschema --schemafile "$schema_dir/$schema" "$f" || true
    fail=1
  fi
}

validate_file "$campus_dir/labs.yaml"      "lab.schema.json"
validate_file "$campus_dir/machines.yaml"  "harvestnode.schema.json"
validate_file "$campus_dir/timetable.yaml" "timetable.schema.json"

labs="$campus_dir/labs.yaml"
machines="$campus_dir/machines.yaml"
timetable="$campus_dir/timetable.yaml"
uplinks="$campus_dir/uplinks.yaml"

if [[ -f "$labs" && -f "$machines" && -f "$timetable" && -f "$uplinks" ]]; then
  log ""; log "── cross-document checks ──"

  # C1: every machine references a lab that exists
  bad=$(yq -r '.[].lab' "$machines" | sort -u | while read -r l; do
    yq -re --arg l "$l" '.[] | select(.id==$l)' "$labs" >/dev/null 2>&1 || echo "$l"
  done)
  [[ -z "$bad" ]] || { warn "machine(s) reference unknown lab: $bad"; fail=1; }

  # C2: every lab has a timetable_ref and uplink_ref
  missing=$(yq -r '.[] | select((.timetable_ref // "") == "" or (.uplink_ref // "") == "") | .id' "$labs")
  [[ -z "$missing" ]] || { warn "lab(s) missing timetable_ref/uplink_ref: $missing"; fail=1; }

  # C3: wolCapable:true machines must have a MAC
  nomac=$(yq -r '.[] | select(.network.wolCapable==true and ((.mac // "") == "" or .mac == "00:00:00:00:00:00")) | .id' "$machines")
  [[ -z "$nomac" ]] || { warn "wolCapable machine(s) missing a real MAC: $nomac"; fail=1; }

  # C4: lab machine count vs actual machine records, within 10%
  for lab in $(yq -r '.[].id' "$labs"); do
    declared=$(yq -r --arg l "$lab" '.[] | select(.id==$l) | .machines' "$labs")
    actual=$(yq -r --arg l "$lab" '[.[] | select(.lab==$l)] | length' "$machines")
    [[ "$declared" -gt 0 ]] || continue
    diff=$(( declared > actual ? declared - actual : actual - declared ))
    pct=$(( diff * 100 / declared ))
    [[ "$pct" -le 10 ]] || { warn "$lab: declared machines=$declared, records=$actual (${pct}% drift)"; fail=1; }
  done

  # C5: nexusCeilingMbps must not exceed policy (40% class-hours / 70% off-hours of uplinkSpeedMbps)
  while IFS=, read -r lab uplink_speed class_ceil off_ceil; do
    [[ -z "$lab" ]] && continue
    max_class=$(( uplink_speed * 40 / 100 ))
    max_off=$(( uplink_speed * 70 / 100 ))
    [[ "$class_ceil" -le "$max_class" ]] || { warn "$lab: classHours ceiling $class_ceil > policy max $max_class"; fail=1; }
    [[ "$off_ceil" -le "$max_off" ]] || { warn "$lab: offHours ceiling $off_ceil > policy max $max_off"; fail=1; }
  done < <(yq -r '.[] | [.lab, .uplinkSpeedMbps, .nexusCeilingMbps.classHours, .nexusCeilingMbps.offHours] | @csv' "$uplinks" | tr -d '"')

  # C6: probeConfidence:probed requires raw evidence on file
  raw_dir="$ROOT/evidence/phase-01B/raw"
  for id in $(yq -r '.[] | select(.probeConfidence=="probed") | .id' "$machines"); do
    found=$(find "$raw_dir" -iname "${id}*" 2>/dev/null | head -1)
    [[ -n "$found" ]] || { warn "probed machine $id has no raw evidence in evidence/phase-01B/raw/"; fail=1; }
  done
else
  log "campus inventory not yet populated (only .example stubs present) — skipping cross-document checks"
fi

[[ $fail -eq 0 ]] && ok "campus inventory valid" || die "campus validation FAILED"
