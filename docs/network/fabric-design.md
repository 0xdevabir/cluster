# Network Fabric Design

Phase 03 Task 1, 2, 8. Canonical machine-readable source: `inventory/network/{vlans,switches,switch-ports}.yaml`. This document explains the design and the math; it does not duplicate the data.

## Task 1 — Fabric class

**Chosen: RoCEv2 over Ethernet (ADR-006), single fabric.** This was effectively already decided by the NICs Phase 01 inventoried:

| Pool | Real hardware (from `inventory/nodes/*.yaml`) | Class |
|---|---|---|
| `pool=training` (4× compute-gpu) | Mellanox ConnectX-6 EN, 100 Gbps, RoCEv2-capable, SR-IOV (8 VFs) | **100 GbE RoCEv2** |
| `pool=control` (3× control) | Mellanox ConnectX-6 Lx, 25 Gbps, RoCEv2-capable | 25 GbE RoCEv2 |
| `pool=storage` (1× storage) | Mellanox ConnectX-6 EN, 25 Gbps, RoCEv2-capable | 25 GbE RoCEv2 |

Per `phases/PHASE-03.md` Task 1's cost/BW/latency table, 100 GbE RoCEv2 is the target class and 25 GbE RoCEv2 is minimum-viable — both are represented here because this fleet mixes a training pool (needs the bus bandwidth) with control/storage pools (do not run collectives and are fine at 25 GbE). No node in this inventory is 1 GbE-only or lacks RDMA, so no node is disqualified by R-01 at the fabric-class level (see Task 8/A17 for the one thing that *does* exclude a node: single-homing, not link speed).

**RoCEv2 vs. InfiniBand:** RoCEv2 chosen, matching the standing ADR-006. This project's control plane is Kubernetes-native (Cilium, SR-IOV) and a second physical fabric (IB) was judged not worth the operational specialization cost for a single-site, single-institution build. See `phases/PHASE-03.md` Task 1 for the full comparison table.

## Task 2 — Topology and oversubscription

**Two-tier leaf-spine (Clos)** is the target design, per `ARCHITECTURE.md#L2.1`. **At M1, only the leaf tier exists** — this reflects real inventory, not a simplification:

- `inventory/racks.yaml` and `evidence/phase-02/handoff.md` already show **two leaf switches racked** (`r01-leaf-a`, `r01-leaf-b`, NVIDIA SN2410, MLAG pair) at M1, ahead of the phase file's own staged table (which shows a single leaf at M1, MLAG introduced at M2). **Reality wins (Rule 6):** the M1 design here uses the already-racked MLAG pair rather than a single switch — see `evidence/phase-03/deviations.md` D1.
- **No spine tier yet.** Only rack `r01` is physically populated; a spine has no purpose until a second rack exists. Two ports per leaf (`eth29`, `eth30` in `inventory/network/switch-ports.yaml`) are reserved, uncabled, for the M3 spine uplink.
- **All 8 hosts are single-homed to `r01-leaf-a`.** `r01-leaf-b` currently carries only the MLAG peer-link — it exists for redundancy capacity ahead of M2 dual-homing, not because any host is dual-connected today.

**Oversubscription at M1: not applicable.** With no spine, every host talks to every other host through a single leaf pair over the MLAG fabric — there is no tiered link to oversubscribe. **A2 (training pool ≤ 1:1) is trivially satisfied**: the 4 training-pool nodes share `r01-leaf-a`'s internal switching fabric, not a shared uplink.

**M2+ oversubscription plan** (from `phases/PHASE-03.md` Task 2, unchanged — a design projection, not inventory):

| Milestone | Switches | Topology | Training-pool oversubscription |
|---|---|---|---|
| **M1 (8 nodes, actual)** | 2× leaf (racked) + 1× mgmt (racked) | Single rack, no spine | N/A — no tiered link |
| M2 (24) | 2 leaf (MLAG) + 1 mgmt | Still no spine; MLAG peer-link carries inter-leaf | Design target: 1:1 |
| M3 (48) | 4 leaf + 2 spine | Spine tier introduced; BGP enabled | 1:1 |
| M4 (100) | 12 leaf + 4 spine | Full Clos | 1:1 |

At M2+, the rule from the phase file holds: **the training pool (`pool=training` label, Phase 14) gets 1:1 non-blocking uplinks; everything else may run up to 3:1.** Encode the split in leaf uplink counts when a second rack is actually built — do not pre-provision uplink ports for racks that do not exist.

## Task 8 — Failure analysis

| Failure | Expected behavior | Design element that provides it | M1 status |
|---|---|---|---|
| One host NIC port dies | Node loses its fabric link entirely (single-homed) → node NotReady → jobs requeue | Accepted — nodes are cattle; dual-homing doubles NIC cost | Applies to all 8 M1 nodes (all single-homed) |
| One leaf switch (`r01-leaf-a`) dies | Its MLAG peer (`r01-leaf-b`) would carry rack traffic — **but no host is dual-homed at M1** | MLAG pair exists, but host dual-homing does not yet | ⚠️ **At M1, losing `r01-leaf-a` isolates all 8 hosts** — MLAG protects the peer-link and future dual-homed hosts, not today's single-homed ones. Tracked as an open gap, not silently accepted; revisit when M2 dual-homes hosts. |
| **Both leaves in a rack die** | The rack is isolated | Accepted: rack is the designed failure domain (`ARCHITECTURE.md#X2.1`) | Applies |
| One spine dies | ECMP redistributes; bisection drops by 1/N_spines | ≥ 2 spines, ECMP | N/A — no spine at M1 |
| One uplink dies | ECMP redistributes | ≥ 4 uplinks per leaf | N/A — no uplinks cabled at M1 (reserved only) |
| MLAG peer-link dies | Split-brain risk → dual-active detection needed | MLAG backup-IP over mgmt network + 2-port peer-link | Peer-link is 2× `eth31`/`eth32`; backup-IP over VLAN 100 — specify in switch config templates (Task 7) |
| Management switch (`r01-mgmt`) dies | Cannot PXE or power-cycle; running cluster unaffected | Management plane deliberately separate | Applies — data-plane traffic does not transit `r01-mgmt` |
| PFC storm | PFC watchdog drops the paused queue after 200 ms rather than deadlocking | PFC watchdog — must be enabled | Specified in `docs/network/roce-contract.md`; not yet applied to hardware (Phase 21) |
| Ceph rebuild saturates the fabric | Storage-cluster VLAN is rate-limited; recovery throttles in Ceph | VLAN 301 QoS + `osd_max_backfills` (Phase 27) | N/A until Ceph is deployed |

## A17 — Nodes excluded from the training pool on network grounds

None. All 4 `pool=training` nodes (`nx-c-r01-04..07`) have 100 Gbps RoCEv2-capable NICs with SR-IOV. No node in the current inventory needs a network-driven exclusion from its assigned pool. This finding should be re-run (`tools/net-validate.py --check-pool-nics`) whenever a node is added.
