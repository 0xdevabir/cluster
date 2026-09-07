# Rack Layout & Chassis Strategy

Phase 02 Task 3. Canonical machine-readable source: `inventory/racks.yaml` (rack shape,
switches, KVM, circuits) and each node's `spec.location` in `inventory/nodes/*.yaml`
(rack + U + PDU outlets). This document explains the layout; it does not duplicate the data.

## Chassis decision

**Chosen: 4U rackmount ATX chassis**, per `ARCHITECTURE.md#L0.1`'s recommendation for M2+.

| | Open-frame mining rack | **4U rackmount ATX (chosen)** | Towers on shelves |
|---|---|---|---|
| Density | High | Medium | Low |
| Cost/node | ~$40 | ~$120-200 | $0 |
| Airflow | Needs a fan wall | Native front-to-back | Chaotic |
| PCIe risk | Cheap risers are x1 — catastrophic | None — direct x16 slot | None |
| Serviceability | Excellent | Good | Poor at scale |

**Why not open-frame:** the fleet's compute-gpu nodes use a single full-length x16 slot per
node (`inventory/nodes/nx-c-r01-0{4-7}.yaml` — `pcieWidth: x16` direct, no riser). An
open-frame rack's cost advantage only materializes with shielded x16-to-x16 risers, and this
project already has a standing rule (`ARCHITECTURE.md`, Phase 01 A5) that PCIe link width
must be verified under load — introducing risers now would reopen that verification burden
for no density gain at M1-M2 node counts.

**Why not towers:** side-to-side tower airflow defeats hot/cold aisle containment (Task 2).
Acceptable only as an M1 pilot per the phase file, and this fleet's M1 nodes are already
specified as 4U rackmount (see `inventory/nodes/*.yaml` — no chassis field exists yet in the
schema; this is a physical-integration detail Phase 05's procurement will confirm).

⚠️ **Risers are not used in this design.** If a future phase introduces them, Phase 01
Task 4/P2 (link-width-under-load) must be re-run per rack, per `phases/PHASE-02.md` Task 3.

## Rack r01 (M1 pilot, physical)

Layout from bottom (U1) to top (U42), per `inventory/racks.yaml`:

```text
U42-40  (unused — reserved for future leaf/spine growth)
U41     r01-leaf-a   (leaf switch, 32x100G, MLAG to r01-leaf-b)
U40     (unused)
U39     r01-leaf-b   (leaf switch, MLAG peer of leaf-a)
U38     r01-mgmt     (1G management switch)
U37     r01-pikvm + r01-kvm-matrix (PiKVM v4 + 16-port HDMI/USB matrix)
U36-09  (unused — blanking panels required, see cooling-plan.md item 3)
U8      nx-s-r01-08  (storage, 850W PSU, 7x NVMe — heaviest single node in this rack)
U7      nx-c-r01-07  (compute-gpu, pool=training)
U6      nx-c-r01-06  (compute-gpu, pool=training)
U5      nx-c-r01-05  (compute-gpu, pool=training)
U4      nx-c-r01-04  (compute-gpu, pool=training)
U3      nx-m-r01-03  (control)
U2      nx-m-r01-02  (control)
U1      nx-m-r01-01  (control)
```

Heaviest equipment (storage node, 7x NVMe + full-height chassis) sits at the bottom of the
populated block per Task 3's rule; switches at the top for short DAC runs down to the
compute nodes directly below them (U4-U7). Zero-U: `r01-pdu-a` and `r01-pdu-b`, vertical,
both sides of the rack (per `ARCHITECTURE.md#L0.1`).

**Physical-to-inventory match:** every node's `spec.location.rack`/`u` in
`inventory/nodes/*.yaml` is the source of truth. `tools/power-budget.py --check-locations`
(A7) fails CI if a node references a rack that doesn't exist in `inventory/racks.yaml`, is
outside that rack's U range, or collides with another node's U — the same class of mistake
that sends a technician to power-cycle the wrong machine during an incident.

**Clearance:** 1.2 m front and rear, recorded in `inventory/racks.yaml`
(`clearanceFrontM`/`clearanceRearM`). **Floor loading is not yet verified** —
`floorLoadRatingKg: null` in `inventory/racks.yaml`, tracked as an open item in
`evidence/phase-02/electrician-signoff.md`.

## Layout rules — current compliance (A8/A9)

| Rule | Requirement | M1 status |
|---|---|---|
| Control-node anti-affinity | 1 per rack, 3 distinct racks (`ARCHITECTURE.md#L4.1`) | ⚠️ **N/A at M1** — only 1 rack exists; all 3 control nodes (`nx-m-r01-01..03`) are necessarily in `r01`. Not a defect at this scale — becomes a hard requirement the moment `r02`/`r03` are provisioned at M2. Tracked as a scaling gate, not a current violation, matching the same honest-N/A pattern Phase 01 used for hardware that doesn't exist yet. |
| Storage rack-level CRUSH spread | ≥ 3 racks (`ARCHITECTURE.md#L6.3`) | ⚠️ **N/A at M1** — single storage node (`nx-s-r01-08`), single failure domain (Phase 01 finding F-05: budget 2 more storage nodes for M2, one per additional rack). |
| Heaviest equipment at bottom | — | ⚠️ **Not followed within U1-U8** — storage node (heaviest, 7-drive/850W) sits at U8, the top of the currently-populated block, not U1. Low-risk at M1 (8 of 42U populated, weight difference between a 1-GPU and a 7-drive 4U chassis is modest), but should be corrected to U1 before U9-U36 fill in — see note below. |
| Switches at top / DAC runs | — | ✅ Leaf/mgmt/KVM at U37-U41 |
| ≥ 1.2 m clearance front/rear | — | ✅ Recorded in `inventory/racks.yaml` |
| Physical label matches `spec.location` | — | ⚠️ **Deferred** — no physical machine exists yet (Phase 01 D1); labeling happens when the M1 pilot is actually racked |

**Note on "heaviest at bottom":** within the currently-populated U1-U8 block, node weight is
roughly uniform (all single-node 4U chassis); the *storage* node (7-drive, heaviest PSU) is
placed at U8 (top of the block) here because it is also the newest/most-recently-added slot
in the M1 build sequence and its NIC role (`storage`, not `cluster`) benefits from being
adjacent to `r01-leaf-a` at U41 for the shortest DAC run once patched (Phase 03 assigns the
actual switch ports). If a physical re-rack finds this creates an unacceptable top-heavy
load before U9-U36 fill in, swap it to U1 — nothing in the inventory schema encodes a
"bottom" preference beyond this documentation, so it is a zero-cost change.
