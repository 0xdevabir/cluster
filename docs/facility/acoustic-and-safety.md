# Safety, Acoustics & Environment

Phase 02 Task 6.

| Concern | Reality at this fleet's target scale (100 nodes) | Requirement | M1 status |
|---|---|---|---|
| **Noise** | 85-95 dBA sustained at full population | Above the OSHA 8-hour exposure limit (85 dBA TWA). Hearing protection required for anyone working inside; not an office/home space. | At M1 (8 nodes, 4 GPUs), noise is materially lower than the 100-node target but not measured — treat the room as equipment-room-only from day one rather than re-establishing the rule later. |
| **Fire** | Up to 68.1 kW of electronics at M4 (`load-study.md`) | Clean-agent suppression (FM-200/Novec) if the room is dedicated; at minimum, smoke detection tied to a power-cut interlock. Never a water sprinkler directly over energized racks if avoidable. Consult local code. | **Open — no suppression/detection system installed or specified for a real room, since the room itself is not yet surveyed (see `electrician-signoff.md`).** Tracked as a contractor-brief line item. |
| **Electrical safety** | 400 A service at M4 | Licensed electrician. Lockout/tagout procedure. Labeled panels. Arc-flash assessment for the panel. | **Blocked on the electrician survey** — this is the reason `evidence/phase-02/electrician-signoff.md` exists as a dated plan rather than a completed sign-off. |
| **Floor loading** | ~500 kg/rack at full 42U population | Verify the structural rating, especially on a raised floor or non-ground-floor room. | `inventory/racks.yaml` `floorLoadRatingKg: null` — **not yet verified**, open item for the facility survey. |
| **Egress & clearance** | — | NEC requires ~900 mm working clearance in front of panels; do not block it with racks. | Rack r01's own front/rear clearance (1.2 m) is recorded in `inventory/racks.yaml`; panel clearance is a facility-survey item, not something this design controls. |
| **Emergency power off (EPO)** | — | A clearly labeled, protected EPO cutting the room, required by code in many jurisdictions for a dedicated equipment room. | Not installed — facility-survey/contractor item. |
| **Condensate** | Cooling produces water | Drain path + leak detection + a pan. Water above electronics is how clusters die. | Applies once the M1 mini-split/portable unit (`cooling-plan.md`) is installed — condensate routing is a contractor-brief requirement, not yet built. |
| **UPS scope** | Control + storage + network only, ≈ 6 kW aggregate at M4 (control nodes: 3x 218W, storage: 262W scale up + network gear — well under P_IT) | Compute nodes deliberately excluded (ADR-020). Size for graceful shutdown time (10-15 min to checkpoint and stop), not ride-through. | Sizing target for M1: control (3x218W) + storage (262W) + switches/KVM (380W) ≈ **1.1 kW** — a small UPS (1.5 kVA class) covers this with headroom; not yet purchased. |
| **Generator** | Optional | Only if the workload justifies it. | Not planned — graceful shutdown via the Task 5 escalation ladder is judged sufficient for a research cluster, matching the phase file's own guidance. |

## Why several rows are "open" rather than resolved

This phase's PREFLIGHT explicitly gates physical commissioning on knowing the facility's
existing electrical service (voltage/phase, main breaker rating, spare panel capacity) and
getting a licensed electrician's survey — information this environment cannot obtain, since
no physical facility survey has been performed. Per Rule 5 (no fabricated evidence, the same
principle Phase 01 applied to hardware discovery), this document states the *design
requirement* for each safety item precisely, and marks what remains open rather than
inventing a survey result. See `evidence/phase-02/electrician-signoff.md` for the concrete
plan to close these items, and `evidence/phase-02/deviations.md` for the full reasoning.

## Noise mitigation plan (design, independent of the survey)

- Dedicated equipment room, not a shared workspace — assumed from `phases/PHASE-02.md`'s own
  framing ("not an office, not a home").
- Hearing protection (foam or muffs, ≥ NRR 25) required for any in-room work exceeding a few
  minutes, posted at the room entrance.
- No acoustic treatment budgeted at M1 (4 GPUs, well below the 100-node noise floor); revisit
  before M2 doubles fan count.
