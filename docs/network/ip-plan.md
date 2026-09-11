# IP Plan

Phase 03 Task 3. Canonical machine-readable source: `inventory/network/{vlans,ip-plan}.yaml`. This document explains the addressing scheme; it does not duplicate the data.

## Networks

| VLAN | Name | CIDR | MTU | Purpose |
|---|---|---|---|---|
| 100 | management | `10.100.0.0/22` | 1500 | PXE, PDU, PiKVM, switch mgmt — N6: 1500 for PXE ROM compatibility |
| 200 | cluster | `10.200.0.0/20` | 9000 | Kubernetes node network, one /26 per rack |
| 300 | storage | `10.210.0.0/22` | 9000 | Ceph public network |
| 301 | storage-cluster | `10.211.0.0/22` | 9000 | Ceph replication/backfill, QoS-throttled |
| 400 | rdma | `10.220.0.0/20` | 9000 | RoCEv2 SR-IOV VFs, one /24 per rack, DSCP 26 / PCP 3 |

## Static addressing scheme

- **Node management IPs:** `10.100.0.100 + node_index`, where `node_index` is the last two digits of the node name (e.g. `nx-m-r01-01` → `10.100.0.101`, `nx-s-r01-08` → `10.100.0.108`). Formula-based, not enumerated per node, so it survives node additions without a file edit.
- **Infrastructure reservations** (`inventory/network/ip-plan.yaml` → `networks.management.reservations`): switches, PDUs (real values from `evidence/phase-02/handoff.md` item 4 — `r01-pdu-a` = `10.100.1.11`, `r01-pdu-b` = `10.100.1.12`), PiKVM, KVM matrix.
- **Cluster network:** rack `r01` gets `10.200.1.0/26` (62 usable). At 8 nodes with room for the M1 rack's remaining U1-U36 to fill, this leaves > 20% headroom (N4) for the current population; re-evaluate before M2 fills the rack past ~48 hosts.
- **RDMA network:** rack `r01` gets `10.220.1.0/24`. Per-node VF addressing (`.{node_slot}{vf_index}`) is assigned when SR-IOV VFs are actually created — **Phase 21's job**, not this one (DO NOT list: "do not assign IP addresses to pods here" extends to not pre-assigning VF addresses that don't exist yet).
- **Storage / storage-cluster:** flat `/22` each at M1 (no `perRack` needed — single rack). Add `perRack` entries when a second storage rack is built.

## Kubernetes overlay

- **Pod CIDR:** `10.244.0.0/14` — one `/24` per node, up to 1024 nodes. Chosen specifically because it does **not** collide with the `10.0.0.0/8` underlay supernet's other allocations (management/cluster/storage/rdma all sit in `10.100.0.0/16`–`10.220.0.0/16`) nor with a typical corporate `10.0.0.0/8` — see Troubleshooting in `phases/PHASE-03.md`.
- **Service CIDR:** `10.96.0.0/16`. **Load-balancer pool:** `10.10.0.0/24`, advertised via BGP (N8) — deliberately outside every rack subnet so it is reachable regardless of which rack backs a given Service.

## BGP addressing

- `spineAsn: 65000` (reserved, unused until M3 — no spine exists yet)
- `leafAsnBase: 65100` → `r01` leaves share **AS 65101**
- `hostAsnBase: 65200` → `r01` hosts share **AS 65201**
- RFC 5549 unnumbered — no `/31` point-to-point addressing to manage, IPv6 link-local next-hops only

Full design and rationale in `docs/network/bgp-design.md`.

## Upstream — open item

`spec.upstream` in `inventory/network/ip-plan.yaml` (`gateway`, `dns`, `ntp`) is still `<REPLACE-ME>`. The facility's existing electrical/network service is not yet confirmed (`evidence/phase-02/electrician-signoff.md` is a dated plan, not a completed survey) — filling in a real upstream gateway now would be a fabrication of exactly the kind Rule 5 forbids. See `evidence/phase-03/deviations.md` D1.

## Validation

`tools/net-validate.py` encodes rules N1–N8 from `phases/PHASE-03.md` Task 3 and is wired as `task validate:network`.
