#!/usr/bin/env bash
# Non-invasive live-USB probe of ONE campus harvest-candidate machine.
# Run FROM the live USB, booted on the target machine — never from the
# machine's internal OS. Collects identity/CPU/memory/GPU/disk/network/
# firmware/thermal facts and prints one inventory/campus/machines.yaml
# record fragment to stdout. Never mounts, images, or writes to the
# internal disk (PHASE-01B Task 2 / CAMPUS-FABRIC.md#9.1 leave-no-trace).
#
# Usage: tools/campus/probe-harvest-node.sh <lab-id> <machine-id> [iface]
set -euo pipefail

lab="${1:?usage: $(basename "$0") <lab-id> <machine-id> [iface]}"
id="${2:?usage: $(basename "$0") <lab-id> <machine-id> [iface]}"
iface="${3:-}"

have() { command -v "$1" >/dev/null 2>&1; }
run()  { have "$1" || { echo "  (missing: $1)"; return 0; }; shift; "$@" 2>&1 || true; }

if [[ -z "$iface" ]]; then
  iface="$(ip -o link show 2>/dev/null | awk -F': ' '$2 != "lo" {print $2; exit}')"
fi

echo "# probe: lab=$lab id=$id iface=$iface  ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
echo "## identity & chassis"
run dmidecode dmidecode -s system-manufacturer -s system-product-name -s baseboard-product-name

echo "## cpu"
run lscpu lscpu | grep -E 'Model name|^CPU\(s\)|Thread|Socket|NUMA' || true

echo "## memory (measured, never nameplate)"
run free free -b | awk '/Mem:/{print "measuredBytes:", $2}'
run dmidecode dmidecode -t memory | grep -E 'Size|Speed|Type:'

echo "## gpu"
run lspci lspci -nn | grep -Ei 'vga|3d|display' || echo "  (none found)"
run nvidia-smi nvidia-smi --query-gpu=name,memory.total,compute_cap,driver_version --format=csv 2>/dev/null || true

echo "## disk — READ ONLY, never mount/write"
run lsblk lsblk -o NAME,SIZE,TYPE,FSTYPE,PARTLABEL

echo "## network"
if [[ -n "$iface" ]]; then
  run ethtool ethtool "$iface" | grep -E 'Speed|Duplex|Link detected'
  run ethtool ethtool "$iface" | grep -i 'Wake-on'
  run ip ip -o link show "$iface" | awk '{print $2, $(NF-2)}'
else
  echo "  (no interface detected — pass one explicitly as \$3)"
fi

echo "## firmware/boot"
[[ -d /sys/firmware/efi ]] && echo "UEFI" || echo "BIOS"

echo "## thermal baseline (idle)"
run sensors sensors | grep -E 'Core|edge|temp1' || echo "  (sensors unavailable)"

cat <<'NOTE' >&2

Copy the fields above into an inventory/campus/machines.yaml record. Set
probeConfidence: probed and commit the raw output of this script, verbatim,
to evidence/phase-01B/raw/<id>.txt (required for validate-campus.sh's
"probed implies raw evidence" check).
NOTE
