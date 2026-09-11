# Phase 03 — Plan

Date: 2026-09-11
Operator: agent (session_01883KZe15yE4EHBCxZ8aNgj), for 0xdevabir

1. Fabric class (Task 1) → `docs/network/fabric-design.md`, derived from real NIC inventory rather than assumed
2. Topology/oversubscription (Task 2) → same file; M1 uses the already-racked 2-leaf MLAG pair (deviation from the phase's own single-leaf M1 table — Rule 6, reality wins)
3. IP plan (Task 3) → `inventory/network/{vlans,ip-plan}.yaml` + `docs/network/ip-plan.md`; `inventory/schema/network.schema.json` completed (was a Phase 00 stub)
4. RoCE contract (Task 4) → `docs/network/roce-contract.md`, includes a real buffer-size finding against the actual SN2410 spec
5. BGP design (Task 5) → `docs/network/bgp-design.md`; ASNs assigned now, not operationally enabled until M3 (no spine exists)
6. Switch port map & cabling (Task 6) → `inventory/network/{switches,switch-ports}.yaml`, `docs/network/cabling-guide.md`, `tools/gen-cable-list.py`; 3 control nodes' `switchPort` fields added to `inventory/nodes/*.yaml` to match the convention the compute/storage nodes already used
7. Switch config templates (Task 7) → `docs/network/switch-config/{leaf,spine,mgmt}-template.conf` — written, not applied to hardware
8. Failure analysis (Task 8) → `docs/network/fabric-design.md`, with an honest M1-specific gap noted (leaf-a loss isolates all hosts since none are dual-homed yet)
9. Validation → `tools/net-validate.py` (N1-N8), wired as `task validate:network`; `inventory/schema/network.schema.json` already wired into `tools/validate-inventory.sh`
