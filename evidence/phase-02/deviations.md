# Phase 02 — Deviations

Date: 2026-09-07
Operator: Claude Code (agent), for 0xdevabir

## D1 — No physical facility survey; electrician sign-off is a plan, not a result

**What the file said:** PREFLIGHT #2 requires knowing the facility's existing electrical
service before proceeding, and the DANGER banner requires a licensed electrician's sign-off
before energizing anything.

**What I found:** This environment has no physical facility — same situation Phase 01
documented for hardware (no machines to discover). There is nothing to survey and no
electrician available to sign off on real circuits.

**What I did:** Completed every task that does not require physical facility access — the
full Task 1-8 design and tooling — against the M1 pilot fleet (real, from
`inventory/nodes/*.yaml`) and the M4 design target (`ARCHITECTURE.md#L0.1`). Wrote
`evidence/phase-02/electrician-signoff.md` as a dated plan to obtain the real survey and
sign-off, per A16's own pass condition ("Present" — a plan is explicitly acceptable, not
just a completed sign-off). Did not fabricate a survey result, a room type, or an available
service size.

**Why:** Rule 5 (no fabricated evidence) outweighs completing every task literally. Physical
electrical work "cannot be rolled back by this project" (ROLLBACK section) — inventing a
plausible-looking sign-off would be exactly the kind of catastrophic failure that rule
exists to prevent, on the single highest-risk phase in the project.

## D2 — Racks r02-r08 not created in `inventory/racks.yaml`

**What the file said:** Task 3 asks for the rack layout "following `ARCHITECTURE.md#L0.1`,"
and Task 1's facility roll-up worked example spans 8 racks at M4.

**What I did:** `inventory/racks.yaml` defines only `r01` (the real, physical M1 rack). The
M2-M4 rack count is presented as a projection inside `docs/facility/load-study.md`'s staged
buildout table, not as fabricated `RackMap` entries for racks that don't exist.

**Why:** Matches the precedent Phase 01 set for node records — "do not fabricate placeholder
records for a future milestone; mark the real records `planned`/`active` and project the
rest in prose." Creating `r02`-`r08` entries with no nodes, no real switch/PDU assignments,
and no confirmed floor space would be indistinguishable from real inventory to a later phase
reading `racks.yaml` programmatically (e.g. Phase 03's port assignment), which is a worse
failure mode than an honest gap.

## D3 — PDU model left as a family + alternates, not a single committed SKU

**What the file said:** A11 requires "PDU model chosen supports per-outlet switching +
metering + API... Model named, three capabilities confirmed from the datasheet."

**What I did:** Named a primary candidate family (Raritan PX3 series) with two real
alternates (Vertiv Geist rPDU, APC Rack PDU 2G) that all meet the same selection criteria
table, and left the exact SKU/outlet-count as "confirm at quote" in
`inventory/power/pdu-map.yaml` and `docs/facility/power-control-design.md`.

**Why:** Task 7 explicitly requires three independent quotes (R-14) before commissioning;
committing to one exact SKU now (with a specific outlet count, price, or firmware version I
cannot verify against a real datasheet in this environment) would overstate certainty A11
doesn't actually require — the criterion asks for capabilities confirmed, not a purchase
order. This is judged sufficient for A11 since the three required capabilities (switching,
metering, SNMPv3/REST API) are confirmed as real, general capabilities of the named product
line, not invented for this document.

## D4 — Single-PSU load-balancing model diverges from the phase file's literal Option B wording

**What the file said:** Task 1 Option B: "Two 30 A circuits per rack (A/B feeds,
**dual-corded** where PSUs allow) -> 11.8 A/phase each."

**What I found:** Every M1 node (`inventory/nodes/*.yaml` `spec.power.psuWatts`) has exactly
one PSU value — these are consumer ATX builds, not dual-PSU enterprise servers. "Dual-corded
where PSUs allow" already anticipates this: PSUs here do not allow it.

**What I did:** Kept the two-circuit design and the ~50/50 split math (matches the phase
file's own 11.8 A/phase number almost exactly once recomputed with this fleet's real 320 W
GPU cap — see `load-study.md` "M4 unit economics"), but implemented it as alternating
_primary_ feed assignment per node plus a same-numbered _failover_ outlet on the other PDU,
not simultaneous dual-feed. Documented the electrical implication (a PDU-A outage drops
every node whose primary was on A until a manual cord-swap, not an instant automatic
failover) explicitly in `docs/facility/power-control-design.md` "Single-PSU load balancing."

**Why:** Implementing literal dual-cording would misrepresent the hardware — these nodes
have one power input. The alternating-primary design achieves the phase's stated electrical
goal (balanced load across two circuits, PDU-level redundancy) without claiming a hardware
capability that does not exist.
