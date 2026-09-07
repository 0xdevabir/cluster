# Phase 02 — Plan

Date: 2026-09-07
Operator: Claude Code (agent)

No physical facility exists yet, so this run completes every task the paper design allows
(Tasks 1-8 in full) and gates the one physical dependency (electrician sign-off) behind a
dated plan rather than a fabricated survey — mirroring Phase 01's "zero machines" path.

1. Task 1 — electrical load model → `tools/power-budget.py` (computes live from inventory) →
   `docs/facility/load-study.md`, `inventory/power/power-budget.yaml`
2. Task 2 — cooling design → `docs/facility/cooling-plan.md`
3. Task 3 — rack layout & chassis strategy → `docs/facility/rack-layout.md`,
   `inventory/racks.yaml`
4. Task 4 — GPU power capping decision → `inventory/nodes/nx-c-r01-0{4-7}.yaml`
   `powerCapWatts: 400 -> 320`, documented in `load-study.md`
5. Task 5 — remote power-control design → `docs/facility/power-control-design.md`,
   `inventory/power/pdu-map.yaml`
6. Task 6 — safety, acoustics & environment → `docs/facility/acoustic-and-safety.md`
7. Task 7 — contractor brief → `docs/facility/contractor-brief.md`
8. Task 8 — rack/power schemas + CI checks → `inventory/schema/{rack,power}.schema.json`,
   `tools/power-budget.py --check/--headroom/--check-locations/--check-pdu-coverage` wired
   into `tools/validate-inventory.sh`

Decision: circuit sizing uses Option B (two 30 A 3-phase circuits per rack, A/B feeds) per
the phase file's own recommendation, adapted for this fleet's single-PSU consumer nodes
(alternating primary feed + manual-swap failover outlet, not simultaneous dual-cording — see
`deviations.md` D4). Every load number is computed live from `inventory/nodes/*.yaml` by
`tools/power-budget.py`, not hand-typed, so it cannot silently drift from the fleet it
describes.
