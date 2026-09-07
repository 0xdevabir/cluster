# Remote Power-Control Design

Phase 02 Task 5 — solving the no-BMC problem (`ULTIMATE-PLAN.md §4.6`). Consumer boards
carry no IPMI/Redfish BMC, so operability depends entirely on the four layers below.

## The four layers

| Layer | Mechanism | Covers | Spec (this fleet) |
|---|---|---|---|
| **1. Power on** | Wake-on-LAN magic packet | Normal boot from S5 | Requires BIOS WoL on + ErP off (Phase 01 Task 5), mgmt VLAN (100) reachability, `ethtool -s <if> wol g` persisted via Talos machine config (Phase 09). Every node's `spec.firmware.wol: true` is already set in `inventory/nodes/*.yaml`. |
| **2. Graceful shutdown** | Talos API `talosctl shutdown` | Planned maintenance | Requires the node to be responsive; wired in Phase 09/12, not this phase. |
| **3. Hard power cycle** | Switched PDU outlet toggle | Hung node, kernel panic, WoL failure | **Mandatory — implemented in `inventory/power/pdu-map.yaml`.** Every node has a `primary` outlet on one PDU and a `failover` outlet on the other (see "Single-PSU load balancing" below). |
| **4. Console / BIOS** | PiKVM v4 + HDMI/USB matrix switch | BIOS changes, boot-failure diagnosis | 1x PiKVM (`r01-pikvm`) + 1x 16-port matrix (`r01-kvm-matrix`) per rack, per `inventory/racks.yaml`. |

## PDU selection

**Chosen model family: Raritan PX3 series** (switched + metered, 3-phase 208V/30A input).
Alternates meeting the same criteria: Vertiv Geist rPDU, APC Rack PDU 2G. Exact SKU (outlet
count, receptacle type) is confirmed during the three-quote process
(`contractor-brief.md`, R-14) — not committed here, since it depends on the receptacle the
electrician installs.

| Requirement | Met by Raritan PX3 (and the listed alternates) |
|---|---|
| Per-outlet switching | Yes — required for Layer 3 above |
| Per-outlet metering | Yes — feeds Phase 32's power-aware scheduler and Phase 45's dashboards via `inventory/power/power-budget.yaml` |
| SNMPv3 or REST/JSON API | Yes — `inventory/power/pdu-map.yaml` records `api: { type: snmpv3, credentialRef: vault://... }` per PDU |
| 3-phase input, 30A (2 per rack) | Yes — matches Task 1 Option B |
| Outlet count ≥ node count + switches + spares | 16 outlets used of the model's typical 24-36; ≥8 spare per PDU for M2 growth |
| Outlet-level power-on sequencing/delay | Yes — required before any circuit above is energized (prevents an inrush trip on rack power-up) |
| Environmental sensor ports | Yes (Raritan DX2/DPX2) — reused for the temperature/humidity probes in `cooling-plan.md` |

**Never metered-only.** A metered-only PDU cannot power-cycle (Layer 3), which is a hard
requirement — see `phases/PHASE-02.md` DO NOT list and R-05.

## Single-PSU load balancing

M1 nodes carry one consumer ATX PSU each (`inventory/nodes/*.yaml` `spec.power.psuWatts` —
a single value, not a redundant pair). This is different from typical enterprise dual-corded
gear, and it changes what "two PDUs per rack" actually buys:

- **Not simultaneous dual-feed.** A single-PSU node has exactly one live cord at a time.
  `inventory/power/pdu-map.yaml` marks that cord's outlet `feed: primary` on whichever PDU it
  is plugged into, and reserves the same-numbered outlet on the *other* PDU as `feed:
  failover` — an empty, pre-labeled slot a technician moves the cord to during a PDU-A (or
  -B) outage, per the Task 5 escalation ladder below.
- **Load balancing is achieved by alternating which PDU is primary, node by node** (odd
  U slots primary-on-A, even U slots primary-on-B — see the outlet list in
  `pdu-map.yaml`), not by any single node splitting its draw across both feeds. This keeps
  `docs/facility/load-study.md`'s per-circuit continuous-load numbers close to a 50/50 split
  of `P_rack`, which is what the Task 1 Option B design ("11.8 A/phase each") assumes.
  Verified: `tools/power-budget.py` computed circuits within 0% of each other at M1 (both
  circuits carry exactly half of `P_rack` since node count splits evenly 4/4 here).
- **Full A/B failover at 100% of rack load is explicitly out of scope for M1.** If PDU-A
  fails, only the nodes whose *primary* was on A go dark until their cords are manually
  moved to their `failover` outlets on PDU-B — that PDU was never sized to carry both halves
  simultaneously without a cord-swap. This is an accepted operational tradeoff for
  consumer-PSU hardware, not a gap; enterprise dual-PSU nodes (if introduced later, e.g. for
  storage/control at M2+) should instead be genuinely dual-corded and split 50/50 at the PSU.
- Outlet power-on sequencing (2 s stagger, per the PDU's built-in feature) prevents an
  inrush trip when a rack — or a set of failover cords — powers up together.

## Management network

The PDU management IPs (`10.100.1.11`/`.12`, recorded in `inventory/power/pdu-map.yaml`) and
PiKVM/matrix live on VLAN 100, **not routable from tenant zones**
(`ARCHITECTURE.md#X1.1`) — admin VPN + jump host only. Anyone who can reach this network can
power off the cluster; it is a critical security boundary. Credentials are referenced by
Vault path (`vault://nexus/pdu/r01-pdu-a`) — never stored in the outlet map itself.

## Escalation ladder for an unresponsive node

Encoded here per Task 5; wired into `runbooks/` in a later phase.

```text
1. Talos API responds?         -> talosctl reboot
2. Node pings but API is dead? -> PDU power-cycle the node's `primary` outlet (off, 10s, on)
3. Node dark, PDU shows 0A?    -> check the `failover` outlet's PDU is up; if so, move the
                                   cord to `failover` and retry; if not -> hardware failure,
                                   PiKVM to inspect POST
4. PiKVM shows no POST?        -> physical intervention required -> ticket
```
