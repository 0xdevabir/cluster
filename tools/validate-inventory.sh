#!/usr/bin/env bash
# Validate every inventory document against its JSON Schema.
# Exit non-zero on the FIRST failure — CI must be unambiguous.
source "$(dirname "$0")/lib/common.sh"
need check-jsonschema
need yq

fail=0
shopt -s nullglob

validate_dir() {
  local dir="$1" schema="$2" f
  [[ -d "$ROOT/$dir" ]] || return 0
  for f in "$ROOT/$dir"/*.yaml; do
    [[ "$(basename "$f")" == *.example ]] && continue
    if check-jsonschema --schemafile "$ROOT/inventory/schema/$schema" "$f" >/dev/null 2>&1; then
      ok "$(basename "$f")"
    else
      warn "FAILED: $f"
      check-jsonschema --schemafile "$ROOT/inventory/schema/$schema" "$f" || true
      fail=1
    fi
  done
}

validate_dir "inventory/nodes"   "nodespec.schema.json"
validate_dir "inventory/network" "network.schema.json"
validate_dir "inventory/power"   "power.schema.json"
validate_dir "inventory"         "rack.schema.json"

# Cross-document integrity checks — schema cannot express these
log ""; log "── cross-document checks ──"

# C1: every node name is unique (filename == metadata.name)
dupes=$(find "$ROOT/inventory/nodes" -name '*.yaml' -print0 2>/dev/null | xargs -0 -I{} yq -r '.metadata.name' {} 2>/dev/null | sort | uniq -d)
[[ -z "$dupes" ]] || { warn "duplicate node names: $dupes"; fail=1; }

# C2: filename matches metadata.name
while IFS= read -r -d '' f; do
  want="$(basename "$f" .yaml)"; got="$(yq -r '.metadata.name' "$f")"
  [[ "$want" == "$got" ]] || { warn "filename/name mismatch: $f (name=$got)"; fail=1; }
done < <(find "$ROOT/inventory/nodes" -name '*.yaml' -print0 2>/dev/null)

# C3: every MAC address is globally unique across the fleet
macdupes=$(find "$ROOT/inventory/nodes" -name '*.yaml' -print0 2>/dev/null | xargs -0 -I{} yq -r '.spec.nics[].macAddress' {} 2>/dev/null | sort | uniq -d)
[[ -z "$macdupes" ]] || { warn "duplicate MAC addresses: $macdupes"; fail=1; }

# C4: every (rack,u) location is occupied at most once
locdupes=$(find "$ROOT/inventory/nodes" -name '*.yaml' -print0 2>/dev/null \
  | xargs -0 -I{} yq -r '"\(.spec.location.rack)/\(.spec.location.u)"' {} 2>/dev/null | sort | uniq -d)
[[ -z "$locdupes" ]] || { warn "two nodes in the same rack unit: $locdupes"; fail=1; }

# C5: exactly one boot device per node
while IFS= read -r -d '' f; do
  n=$(yq -r '[.spec.storage[] | select(.role=="boot")] | length' "$f")
  [[ "$n" == "1" ]] || { warn "$(basename "$f"): expected exactly 1 boot device, found $n"; fail=1; }
done < <(find "$ROOT/inventory/nodes" -name '*.yaml' -print0 2>/dev/null)

# C6: Phase 02 power-budget checks — circuit derate (A2/A3), rack/U placement (A7),
# and PDU outlet coverage (A10). Kept in tools/power-budget.py (not duplicated here)
# because it needs the Task 1 wattage model, not just schema/uniqueness checks.
if [[ -f "$ROOT/tools/power-budget.py" ]]; then
  pb_fail=0
  python3 "$ROOT/tools/power-budget.py" --check >/dev/null 2>&1 || { warn "power-budget.py --check FAILED (A2)"; pb_fail=1; }
  python3 "$ROOT/tools/power-budget.py" --headroom >/dev/null 2>&1 || { warn "power-budget.py --headroom FAILED (A3)"; pb_fail=1; }
  python3 "$ROOT/tools/power-budget.py" --check-locations >/dev/null 2>&1 || { warn "power-budget.py --check-locations FAILED (A7)"; pb_fail=1; }
  python3 "$ROOT/tools/power-budget.py" --check-pdu-coverage >/dev/null 2>&1 || { warn "power-budget.py --check-pdu-coverage FAILED (A10)"; pb_fail=1; }
  [[ $pb_fail -eq 0 ]] && ok "power budget checks (A2/A3/A7/A10)" || fail=1
fi

[[ $fail -eq 0 ]] && ok "inventory valid" || die "inventory validation FAILED"
