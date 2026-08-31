# Phase 01 — Acceptance

Date: 2026-08-31
Operator: Claude Code (agent), for 0xdevabir

No physical fleet exists yet — every NodeSpec in this run is `status: planned`, built from
spec-sheet values for the M1 pilot (`ULTIMATE-PLAN.md §9`: 3 control + 4 compute-gpu + 1
storage). Criteria that require physical evidence (a real discovery run, a runtime probe, a
BIOS change) are marked DEFERRED, not fabricated — see `deviations.md`.

| # | Criterion | Verification | Result |
|---|---|---|---|
| A1 | Every machine has a schema-valid NodeSpec | `task validate:inventory` | ✅ PASS — exit 0, 8/8 nodes valid |
| A2 | Node count matches physical reality | `ls inventory/nodes/*.yaml \| wc -l` vs. physical count | ⚠️ N/A — 8 planned records match the M1 pilot plan; 0 physical machines exist to compare against |
| A3 | Every node has raw discovery evidence | `test -d evidence/phase-01/raw/<node>` for each | ⚠️ PARTIAL — a directory exists for all 8 nodes, but each holds a `NOTE.md` explaining deferred status, not real `collect.sh` output (no hardware to run it on) |
| A4 | No duplicate MACs, names, or rack units | `task validate:inventory` cross-checks | ✅ PASS — see below |
| A5 | Every GPU has `pcieWidth` from `max`, verified under load | Inspect probes.txt | ❌ DEFERRED — no hardware to load-test; `pcieWidth: x16` is the spec-sheet max for RTX 4090 in a x16 slot, unverified |
| A6 | Every GPU capability flag is matrix-sourced or probe-verified | `grep -r _requiresManualReview inventory/nodes/` | ✅ PASS — zero matches; RTX 4090 is a known model in `capability-matrix.yaml` |
| A7 | Every NIC has `numaAffinity` and `rdma.capable` populated | `yq` query across all nodes | ✅ PASS — see below |
| A8 | Every disk has `class`, `plp`, `tier`, `role` | `yq` query | ✅ PASS — see below |
| A9 | Archetype post-conditions hold | Manual check against `docs/hardware-classification.md` | ✅ PASS — control=3, storage=1 (M1 target), no rack collisions (single rack at M1) |
| A10 | `fleet-summary.md` generates and is committed | `task inventory:stats` | ✅ PASS — file produced, totals plausible |
| A11 | Findings table exists with severity, remediation, cost | Read `inventory/fleet-summary.md` | ✅ PASS — 5 findings (F-01..F-05) |
| A12 | BIOS baseline recorded per machine | `ls evidence/phase-01/raw/*/bios.md` | ❌ DEFERRED — no physical BIOS access yet |
| A13 | Every `pool=training` node has `topology.aligned == true` | Cross-check GPU/NIC `numaAffinity` | ✅ PASS — all 4 training nodes: GPU `numaAffinity: 0` == NIC `numaAffinity: 0` (single-NUMA board) |
| A14 | Legal review item opened and referenced | `evidence/phase-01/legal-review.md` | ✅ PASS — R-03 tracked, status OPEN |

## A1 — Schema validation

```text
$ task validate:inventory
✓ nx-c-r01-04.yaml
✓ nx-c-r01-05.yaml
✓ nx-c-r01-06.yaml
✓ nx-c-r01-07.yaml
✓ nx-m-r01-01.yaml
✓ nx-m-r01-02.yaml
✓ nx-m-r01-03.yaml
✓ nx-s-r01-08.yaml

── cross-document checks ──
✓ inventory valid
```

Exit code: 0.

## A4 — Cross-document integrity

`tools/validate-inventory.sh` checks C1–C5 (duplicate names, filename/name mismatch,
duplicate MACs, rack-unit collisions, exactly one boot device per node) all pass silently
as part of the A1 run above — no warnings were printed for any of the four checks.

## A6 — Capability-flag provenance

```text
$ grep -rn "_requiresManualReview" inventory/nodes/*.yaml
(no output)
```

## A7 — NIC completeness

```text
$ for f in inventory/nodes/*.yaml; do
    yq -r '.spec.nics[] | select(.numaAffinity == null or .rdma.capable == null) | .name' "$f"
  done
(no output)
```

## A8 — Disk completeness

```text
$ for f in inventory/nodes/*.yaml; do
    yq -r '.spec.storage[] | select(.class==null or .plp==null or .tier==null or .role==null) | .device' "$f"
  done
(no output)
```

## A10/A11 — Fleet summary and findings

`tools/inventory-stats.sh` (rewritten this phase to also emit the heterogeneity report and
findings table) generated `inventory/fleet-summary.md`:

```text
$ ./tools/inventory-stats.sh
✓ wrote inventory/fleet-summary.md

Fleet totals across 8 node(s):
  CPU cores:    96
  Memory (GB):  832
  GPUs:         4
  VRAM (GB):    96
  Storage (GB): 25472
```

Full report in `inventory/fleet-summary.md` — Fleet Totals, Heterogeneity Report (single
GPU model, no mixing), and 5 findings (F-01..F-05).

## Full validation suite

```text
$ task validate
✓ inventory valid
Summary: 0 error(s)          # markdown
(yamllint: only pre-existing Taskfile.yaml warnings, unrelated to this phase)
(shellcheck: 0 findings on tools/discovery/*.sh, tools/discover-node.sh, tools/inventory-stats.sh)
no manifests yet — skipping
```

Exit code: 0.

## Summary

- Criteria passed outright: 10 / 14 (A1, A4, A6, A7, A8, A9, A10, A11, A13, A14)
- Criteria N/A or deferred, honestly reported (no hardware exists): A2, A3, A5, A12
- **Phase status: INCOMPLETE — blocked on physical hardware.** Everything achievable without
  a physical machine (Tasks 1, 2, 6, 7, 8) is done and passes validation. Tasks 3–5 (run
  discovery, runtime capability probes, BIOS standardization) cannot be completed until at
  least one machine is racked. See `handoff.md` for what the next session must do first.
