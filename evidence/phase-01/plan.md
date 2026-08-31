# Phase 01 — Plan

Date: 2026-08-31
Operator: Claude Code (agent)

No physical fleet exists yet, so this run follows the phase file's "zero machines" path
(Tasks 1, 2, 6, 7, 8 now; Tasks 3–5 deferred to when hardware is racked).

1. Task 1 — GPU + disk capability matrices → `tools/discovery/capability-matrix.yaml`,
   `tools/discovery/disk-class-matrix.yaml`
2. Task 2 — discovery script + parser → `tools/discovery/collect.sh`,
   `tools/discovery/emit-nodespec.py`, `tools/discover-node.sh` (wrapper)
3. Task 3 — SKIPPED (no hardware to run it on) — recorded in `deviations.md`
4. Task 4 — SKIPPED (requires running hardware) — recorded in `deviations.md`
5. Task 5 — SKIPPED (requires physical BIOS access) — recorded in `deviations.md`
6. Task 6 — classification rules → `docs/hardware-classification.md`; apply to 8 planned
   NodeSpecs for the M1 pilot fleet (3 control + 4 compute-gpu + 1 storage,
   `ULTIMATE-PLAN.md §9`) → `inventory/nodes/nx-{m,c,s}-r01-*.yaml`, all `status: planned`
7. Task 7 — gap analysis → `inventory/fleet-summary.md` via extended `tools/inventory-stats.sh`
   + `tools/render-node-table.sh`
8. Task 8 — GPU EULA legal-review ticket → `evidence/phase-01/legal-review.md`

Decision: planned nodes use realistic spec-sheet values (AMD/Intel workstation boards, RTX 4090
GPUs, ConnectX-6 NICs, PM9A3 for storage) consistent with `ULTIMATE-PLAN.md §4`. They are markers
for procurement, not measured facts — every acceptance criterion requiring physical evidence
(A3, A5 under-load, A12 BIOS records) is reported as deferred, not fabricated.
