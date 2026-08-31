#!/usr/bin/env bash
# Runs ON a target machine (or over SSH against it) and emits a schema-valid NodeSpec YAML
# on stdout. Read-only: writes nothing to the target machine.
#
# Usage:
#   sudo ./tools/discover-node.sh --name nx-c-r01-05 --archetype compute-gpu \
#       --site hq --rack r01 --u 22 --pdu r01-pdu-a:12 --pdu r01-pdu-b:12 \
#       > inventory/nodes/nx-c-r01-05.yaml
#
# Requires: bash, python3 + PyYAML, and (best-effort) dmidecode/lscpu/numactl/lspci/
# nvidia-smi/ethtool/lsblk/nvme-cli/smartctl — see tools/discovery/collect.sh for the
# full list. Missing tools degrade individual fields; they do not abort collection.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW="$(mktemp)"
trap 'rm -f "$RAW"' EXIT

bash "$ROOT/tools/discovery/collect.sh" > "$RAW"
python3 "$ROOT/tools/discovery/emit-nodespec.py" "$RAW" "$@"
