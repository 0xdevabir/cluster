# PHASE 03 — Network Fabric Design

| | |
|---|---|
| **Stage** | 0 — Foundation & Design |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 01, 02 |
| **Blocks** | 05, 07, 08, 13, 21 |
| **Risk** | 🔴 High — the interconnect determines whether this is a cluster or 100 separate computers (R-01) |
| **Blast radius** | Design only in this phase; the decisions bind the entire build |
| **Architecture refs** | `ARCHITECTURE.md#l2--network-fabric`, `ULTIMATE-PLAN.md#44-the-network-is-the-budget`, R-01, R-10, R-11, R-18 |

---

## 🎯 MISSION

Design the complete network: **physical topology, addressing, VLANs, routing, the RoCEv2 lossless contract, and the port-by-port cable map** — such that any two nodes in the training pool can exchange data at ≥ 96 % of line rate with < 3 µs p99 latency, and no single switch failure removes more than one rack.

> 💡 **WHY this is the highest-leverage design phase.** From `ULTIMATE-PLAN.md §4.4`: the same 16-node training job takes **240 seconds** of allreduce on 1 GbE and **2.3 seconds** on 100 GbE RoCE. Nothing else in this project has a 100× lever. Every hour spent here is repaid many times in Phase 22 and Phase 52.

---

## ✅ PREFLIGHT

```bash
# 1. Phases 01 and 02 complete
task validate
test -f inventory/racks.yaml && test -f docs/facility/rack-layout.md && echo OK

# 2. Every node's NIC inventory is populated with speed, RDMA capability, and PCIe width
yq -r '.spec.nics[] | [.name,.speedGbps,.rdma.capable,.pcieWidth] | @csv' inventory/nodes/*.yaml
# ↑ no nulls in speedGbps or rdma.capable

# 3. Rack assignments are final (switch ports are assigned per rack)
yq -r '.spec.location.rack' inventory/nodes/*.yaml | sort | uniq -c

# 4. You know what upstream connectivity exists (internet, corporate LAN, existing VLANs)
```

---

## 📦 DELIVERABLES

```
docs/network/
  fabric-design.md              # topology, oversubscription math, failure analysis
  ip-plan.md                    # the addressing bible
  roce-contract.md              # the lossless configuration contract (both ends)
  switch-config/                # per-switch configuration templates
    leaf-template.conf
    spine-template.conf
    mgmt-template.conf
  cabling-guide.md              # colors, lengths, labeling scheme, run list
  bgp-design.md
inventory/network/
  vlans.yaml
  ip-plan.yaml                  # every subnet, every reservation
  switch-ports.yaml             # port-by-port: switch:port ↔ node:nic
  switches.yaml                 # every switch: model, role, mgmt IP, ASN
inventory/schema/network.schema.json    # completed (stubbed in Phase 00)
tools/
  net-validate.py               # IP overlap, port collision, MTU consistency checks
  gen-cable-list.py             # produces the physical run list for the installer
evidence/phase-03/{acceptance,handoff,deviations}.md
```

---

## 📋 TASKS

### Task 1 — Choose the fabric class

Decide, with the numbers, then record the decision in `fabric-design.md`.

| Option | Cost @100 nodes | Bus BW | Latency | Complexity | Verdict |
|---|---|---|---|---|---|
| **1 GbE only** | $2 k | 0.11 GB/s | ~50 µs | Trivial | ❌ **Disqualified.** R-01. Not a cluster. |
| 10 GbE TCP | $12 k | 1.1 GB/s | ~20 µs | Low | ❌ Communication dominates compute |
| 25 GbE RoCEv2 | $28 k | 3.0 GB/s | ~3 µs | Medium | ⚠️ **Minimum viable.** Works for large-batch / grad-accum training |
| **100 GbE RoCEv2** | $60 k | 11.5 GB/s | ~2 µs | Medium-High | ✅ **Target** (used-market ConnectX-5 + SN2410) |
| 200 GbE RoCE | $140 k | 23 GB/s | ~1.8 µs | High | ✅✅ If budget allows |
| HDR/EDR InfiniBand | $45 k (used) | 12–24 GB/s | ~1.3 µs | Medium | ✅ **Strong alternative** — simpler congestion control, SHARP in-network reduction, but a second fabric to operate (ADR-006) |

**The InfiniBand vs. RoCE decision** — make it here, it is expensive to reverse:

| | RoCEv2 (chosen, ADR-006) | InfiniBand |
|---|---|---|
| Fabric count | **One** (Ethernet carries everything) | Two (IB + Ethernet for management/K8s) |
| Congestion control | PFC + ECN/DCQCN — **you must tune it** (R-10) | Credit-based, lossless by design |
| Collective offload | None | SHARP (in-network reduction, big win at scale) |
| Switch cost (used) | Lower | Comparable |
| Kubernetes integration | Native (Cilium, SR-IOV, standard CNI) | Needs IB-specific plugins; K8s still needs Ethernet |
| Operational familiarity | High | Specialist |
| **Failure mode if mistuned** | **PFC storms, head-of-line blocking** | Rare |

> If your team has no RoCE experience and the budget allows a second fabric, InfiniBand is the lower-risk choice and you should record a superseding ADR. This plan proceeds with RoCEv2 for single-fabric simplicity.

---

### Task 2 — Topology and oversubscription

**Two-tier leaf-spine (Clos).** Per `ARCHITECTURE.md#L2.1`.

```
Sizing formulas:
  N_leaves     = ceil(N_nodes / ports_per_leaf_for_hosts)
  uplinks/leaf = ports_per_leaf − hosts_per_leaf
  Bisection    = N_spines × N_leaves × uplink_speed   (each leaf → each spine)
  Oversub      = (hosts × host_speed) / (uplinks × uplink_speed)
```

**Worked design at M4 (100 nodes, 32-port 100 GbE switches):**

| Parameter | General pool | **Training pool** |
|---|---|---|
| Hosts per leaf pair | 24 (12 per switch, dual-homed via MLAG) | 16 |
| Uplinks per leaf | 8 (4 to each of 2 spines) | 16 |
| Oversubscription | 24×100 / 8×100 = **3:1** | 16×100 / 16×100 = **1:1 non-blocking** |
| Leaves required | 4 pairs for 96 general nodes | 2 pairs for 32 training nodes |
| Spines | 4× 32-port 100 GbE | shared |
| Bisection | 4 × 6 leaf-pairs × 100 G ≈ **12.8 Tb/s** | — |

**Why the training pool gets 1:1:** ring and tree allreduce move `2×(N−1)/N × ModelBytes` across the bisection every step. At 3:1 oversubscription, that traffic contends and your effective bus bandwidth drops by roughly the oversubscription ratio during the collective. **Non-blocking for the pool that does collectives; oversubscribed is fine for everything else.**

**Staged topology** (matches Phase 02's buildout):

| Milestone | Switches | Topology |
|---|---|---|
| M1 (8 nodes) | 1× 32-port 100 G leaf + 1× 48-port 1 G mgmt | Single switch — no spine tier needed |
| M2 (24) | 2 leaf (MLAG pair) + 1 mgmt | Still no spine; MLAG peer-link carries inter-leaf |
| M3 (48) | 4 leaf + 2 spine | Spine tier introduced; **BGP enabled here** |
| M4 (100) | 12 leaf + 4 spine | Full Clos |

> ⚠️ **Design the addressing and BGP scheme at M1 even though you do not need it yet.** Renumbering a live cluster is a multi-day outage. Phase 13's Cilium BGP config must be written once and never changed.

---

### Task 3 — The IP plan

**`inventory/network/ip-plan.yaml`** — the single source of truth. Every later phase reads from it.

```yaml
apiVersion: nexus.io/v1
kind: IpPlan
metadata: { cluster: nexus-prod, site: hq }
spec:
  # ── Underlay: physical, routed, per-rack ─────────────────────────────────
  supernet: 10.0.0.0/8

  networks:
    management:
      vlan: 100
      cidr: 10.100.0.0/22          # 1022 hosts — PXE, PDU, PiKVM, switch mgmt
      gateway: 10.100.0.1
      mtu: 1500                    # PXE ROMs are unreliable with jumbo frames
      dhcp: { enabled: true, range: [10.100.2.1, 10.100.3.254] }   # discovery pool
      reservations:
        - { name: seed-node,   ip: 10.100.0.10 }
        - { name: r01-leaf-a,  ip: 10.100.1.1 }
        - { name: r01-leaf-b,  ip: 10.100.1.2 }
        - { name: r01-pdu-a,   ip: 10.100.1.11 }
        - { name: r01-pikvm,   ip: 10.100.1.21 }
      # static node mgmt IPs: 10.100.0.100 + node_index

    cluster:                        # Kubernetes node network — one /26 per rack
      vlan: 200
      cidr: 10.200.0.0/20
      mtu: 9000
      perRack:
        r01: 10.200.1.0/26
        r02: 10.200.2.0/26
        # ... one /26 (62 usable) per rack; rack index = third octet
      reservations:
        - { name: k8s-api-vip, ip: 10.200.0.10 }

    storage:                        # Ceph public network
      vlan: 300
      cidr: 10.210.0.0/22
      mtu: 9000
    storageCluster:                 # Ceph replication/backfill — QoS-throttled
      vlan: 301
      cidr: 10.211.0.0/22
      mtu: 9000

    rdma:                           # RoCEv2 — SR-IOV VFs live here
      vlan: 400
      cidr: 10.220.0.0/20
      mtu: 9000
      perRack:
        r01: 10.220.1.0/24          # /24 per rack, VF IP = .{node_slot}{vf_index}
      dscp: 26
      pcp: 3
      lossless: true

  # ── Overlay: Kubernetes-internal ─────────────────────────────────────────
  kubernetes:
    podCidr: 10.244.0.0/14          # 1024 × /24 — one /24 per node, 262k pods max
    podCidrPerNode: /24
    serviceCidr: 10.96.0.0/16
    loadBalancerPool: 10.10.0.0/24  # advertised to leaves via BGP
    clusterDns: 10.96.0.10

  # ── BGP ──────────────────────────────────────────────────────────────────
  bgp:
    spineAsn: 65000
    leafAsnBase: 65100              # r01 leaves = 65101, r02 = 65102, ...
    hostAsnBase: 65200              # per-rack host ASN (all hosts in a rack share)
    unnumbered: true                # RFC 5549 — IPv6 link-local, no /31s to manage
    ecmpMaxPaths: 8
    gracefulRestart: true
    bfd: { enabled: true, intervalMs: 300, multiplier: 3 }

  # ── Upstream ─────────────────────────────────────────────────────────────
  upstream:
    gateway: <REPLACE-ME>
    dns: [<REPLACE-ME>, <REPLACE-ME>]
    ntp: [<REPLACE-ME>]
    natEgress: true                 # nodes need outbound for image pulls (until Phase 42)
```

**Addressing rules to encode in `tools/net-validate.py`:**

| # | Rule | Why |
|---|---|---|
| N1 | No two networks' CIDRs overlap | Silent routing failures |
| N2 | `podCidr` does not overlap the underlay, the service CIDR, or the upstream LAN | Cilium native routing requires globally-unique pod addresses |
| N3 | Every node has exactly one IP per network it participates in | Duplicate IPs are the hardest fault to debug |
| N4 | Rack /26 supports the node count + 20 % growth | Renumbering is an outage |
| N5 | MTU is consistent within a network | **One 1500-MTU link silently halves throughput** |
| N6 | Management network MTU is 1500 | PXE ROM compatibility |
| N7 | Every reservation is unique and outside the DHCP range | Address conflicts during provisioning |
| N8 | `loadBalancerPool` is advertised by BGP and not in a rack subnet | It must be reachable from anywhere |

---

### Task 4 — The RoCEv2 lossless contract

**`docs/network/roce-contract.md`.** Both ends must agree exactly, or RoCE degrades silently. This is R-10.

**The contract table** (from `ARCHITECTURE.md#L2.4`, expanded with configuration):

| Parameter | Value | NIC side | Switch side |
|---|---|---|---|
| Traffic class | DSCP 26 → PCP 3 | `mlnx_qos -i <dev> --trust dscp`<br>`cma_roce_tos -d mlx5_0 -t 106` | Ingress: `qos map dscp 26 to traffic-class 3` |
| PFC | **Priority 3 only** | `mlnx_qos -i <dev> --pfc 0,0,0,1,0,0,0,0` | `priority-flow-control priority 3 enable` on every fabric port |
| PFC watchdog | Enabled, 200 ms | — | `priority-flow-control watchdog` |
| ECN / WRED | min 150 KB, max 1500 KB, prob 100 % | `echo 1 > /sys/class/net/<dev>/ecn/roce_np/enable/3`<br>`echo 1 > .../roce_rp/enable/3` | `random-detect ecn minimum-threshold 150 kbytes maximum-threshold 1500 kbytes` on TC3 |
| CNP (congestion notification) | DSCP 48, priority 6 | `echo 48 > /sys/class/net/<dev>/ecn/roce_np/cnp_dscp` | Ensure priority 6 is **lossless too** or CNPs get dropped under congestion |
| DCQCN | Enabled (default rate params) | `mlx5` defaults are usually correct; tune only with evidence | — |
| Buffer | Dedicated lossless pool ≥ 3 × BDP | — | Per-vendor buffer profile; **this is the #1 thing vendors get wrong by default** |
| MTU | 9000 L2 / RoCE MTU 4096 | `ip link set <dev> mtu 9000` | `mtu 9216` (switch overhead allowance) |
| Trust mode | DSCP (not PCP) | `mlnx_qos --trust dscp` | `qos trust dscp` |
| Link-level flow control (global pause) | **DISABLED** | `ethtool -A <dev> rx off tx off` | `no flowcontrol receive/send` |

> ⚠️ **DANGER — global pause vs. PFC.** Enabling both, or enabling global pause instead of PFC, produces a fabric that appears to work and then collapses under load with head-of-line blocking across *all* traffic classes. **Global pause off. PFC on priority 3 only.**

**BDP sizing** (buffer must hold at least one bandwidth-delay product per port):
```
BDP = link_rate × RTT
    = 100 Gb/s × 10 µs = 125 KB  (single hop)
    = 100 Gb/s × 30 µs = 375 KB  (through a spine)
Recommended lossless headroom ≥ 3 × BDP ≈ 1.1 MB per port
→ A 32-port leaf needs ≥ 36 MB of buffer dedicated to the lossless pool.
  Verify your switch model has it. Many cheap 100 G switches have 16 MB total.
```

📊 **This is a purchasing criterion.** Add "shared buffer ≥ 32 MB" to the switch requirements in Phase 05.

**Validation commands to run in Phase 21** (specify them now so the switch config is written to satisfy them):
```bash
# NIC side
mlnx_qos -i <netdev>                          # confirm PFC on prio 3 only, trust dscp
ethtool -S <netdev> | grep -E 'pause|prio3'   # rx_pause/tx_pause should be ~0 at idle
cat /sys/class/infiniband/mlx5_0/ports/1/hw_counters/np_ecn_marked_roce_packets

# End-to-end
ib_write_bw -d mlx5_0 -x 3 -F --report_gbits -D 30 <peer>   # ≥ 96 % line rate
ib_send_lat -d mlx5_0 -x 3 -F                                # p99 < 3 µs
```

---

### Task 5 — BGP design

**`docs/network/bgp-design.md`.** Per `ARCHITECTURE.md#L2.2`, L3-to-the-host eliminates spanning tree and large L2 domains.

```
                    ┌──────────┐  ┌──────────┐
                    │ SPINE-1  │  │ SPINE-2  │     AS 65000 (shared, or unique per spine)
                    └────┬─────┘  └─────┬────┘
                  eBGP unnumbered (RFC 5549, IPv6 link-local next-hop)
                    ┌────┴─────┐  ┌─────┴────┐
                    │ LEAF-R01 │  │ LEAF-R02 │     AS 65101, 65102
                    └────┬─────┘  └─────┬────┘
                  eBGP unnumbered to each host
                    ┌────┴─────────────────────┐
                    │ hosts in r01: AS 65201    │  ← Cilium BGP Control Plane
                    │  advertise: PodCIDR /24   │
                    │             LB VIPs /32   │
                    └───────────────────────────┘

Rules:
  · All hosts in a rack share one ASN (allowas-in not needed; they never transit)
  · Leaves advertise a rack summary upward; spines carry the full table (small)
  · ECMP across both uplinks and both leaves — maxPaths ≥ 8
  · BFD for sub-second failure detection (300 ms × 3 = 900 ms convergence)
  · Graceful restart so a Cilium agent restart does not blackhole the node
  · Route filtering: hosts may advertise ONLY their own PodCIDR and LB VIPs
    (prefix-list on the leaf — a compromised node must not be able to blackhole
     the cluster)
```

**Route-map / prefix-list on every leaf's host-facing session** — this is a security control, not just hygiene:
```
ip prefix-list HOST-IN seq 5  permit 10.244.0.0/14 ge 24 le 24
ip prefix-list HOST-IN seq 10 permit 10.10.0.0/24 ge 32 le 32
ip prefix-list HOST-IN seq 99 deny 0.0.0.0/0 le 32
```

---

### Task 6 — Switch port map & cabling

**`inventory/network/switch-ports.yaml`** — port-by-port. Generated into a physical run list by `tools/gen-cable-list.py`.

```yaml
apiVersion: nexus.io/v1
kind: SwitchPortMap
switches:
  - name: r01-leaf-a
    model: "NVIDIA SN2410"
    role: leaf
    rack: r01
    asn: 65101
    mgmtIp: 10.100.1.1
    ports:
      - { port: swp1,  peer: nx-c-r01-01, peerIf: enp2s0f0np0, speed: 100G, media: DAC-1m,  type: host }
      - { port: swp2,  peer: nx-c-r01-02, peerIf: enp2s0f0np0, speed: 100G, media: DAC-1m,  type: host }
      # ...
      - { port: swp29, peer: spine-1, peerIf: swp1, speed: 100G, media: AOC-10m, type: uplink }
      - { port: swp30, peer: spine-2, peerIf: swp1, speed: 100G, media: AOC-10m, type: uplink }
      - { port: swp31, peer: r01-leaf-b, peerIf: swp31, speed: 100G, media: DAC-1m, type: mlag-peer }
      - { port: swp32, peer: r01-leaf-b, peerIf: swp32, speed: 100G, media: DAC-1m, type: mlag-peer }
```

**Cabling standards** — `docs/network/cabling-guide.md`:

| Aspect | Standard |
|---|---|
| Media choice | **DAC** ≤ 3 m (cheap, low power, low latency); **AOC** 3–30 m; optics + fiber > 30 m |
| Color code | 🟦 Blue = cluster (VLAN 200) · 🟥 Red = RDMA (VLAN 400) · 🟩 Green = storage · ⬜ Grey = management · 🟨 Yellow = uplink |
| Labeling | **Both ends**, every cable: `<switch>:<port> ↔ <node>:<iface>` |
| Bend radius | ≥ 10× cable diameter for DAC; ≥ 30 mm for fiber. Violating this on DAC causes intermittent CRC errors that look like a bad transceiver. |
| Slack | 300 mm service loop at the node end |
| Routing | Never across the front of a chassis (blocks airflow and service access) |
| Verification | After install: `ethtool <if>` shows the expected speed; LLDP neighbor matches `switch-ports.yaml` |

📊 **LLDP is your ground truth.** Phase 14 will run an automated check comparing each node's LLDP neighbor to `switch-ports.yaml` and flag any mismatch. Miscabling at 100 nodes is not a possibility, it is a certainty; automate the detection.

---

### Task 7 — Switch configuration templates

Write vendor-appropriate templates for `leaf`, `spine`, and `mgmt`. Structure (Cumulus/SONiC syntax shown; adapt to your vendor):

```
docs/network/switch-config/leaf-template.conf
  1. Hostname, mgmt VRF, mgmt IP
  2. NTP + syslog + SNMP/gNMI (to the seed node → later, to Prometheus)
  3. Interface config: MTU 9216 on all fabric ports
  4. VLAN definitions (100/200/300/301/400) and per-port tagging
  5. MLAG peering with the rack's other leaf
  6. QoS: DSCP trust, PFC on priority 3, ECN/WRED thresholds, buffer profile
  7. BGP: ASN, unnumbered peers (hosts + spines), ECMP, BFD, prefix-lists
  8. Storm control on the management VLAN only
  9. AAA: TACACS/RADIUS or local + SSH keys; disable telnet
 10. Save + a config-backup hook
```

> 🚫 **Do not apply these to hardware in this phase.** Phase 07 stages the management network; Phase 21 applies the RoCE QoS after the NICs exist to test against. Write the templates now while the design is fresh.

---

### Task 8 — Failure analysis

Document in `fabric-design.md` what happens for each failure, and verify the design survives it:

| Failure | Expected behavior | Design element that provides it |
|---|---|---|
| One host NIC port dies | Node loses its fabric link entirely (single-homed) → node NotReady → jobs requeue | Accepted. Dual-homing every node doubles NIC cost; nodes are cattle. |
| One leaf switch dies | Its MLAG peer carries all rack traffic at 50 % capacity | MLAG pair per rack |
| **Both leaves in a rack die** | The rack is isolated → 12 nodes NotReady | Accepted: rack is the designed failure domain (`ARCHITECTURE.md#X2.1`) |
| One spine dies | ECMP redistributes; bisection drops by 1/N_spines | ≥ 2 spines, ECMP |
| One uplink dies | ECMP redistributes; that leaf's uplink capacity drops | ≥ 4 uplinks per leaf |
| MLAG peer-link dies | Split-brain risk → dual-active detection needed | MLAG backup-IP over the mgmt network + `peer-link` on 2 ports |
| Management switch dies | Cannot PXE or power-cycle; **running cluster unaffected** | Management plane is deliberately separate (`ARCHITECTURE.md#0.2`) |
| PFC storm | PFC watchdog drops the paused queue after 200 ms rather than deadlocking | PFC watchdog — **must be enabled** |
| Ceph rebuild saturates the fabric | Storage-cluster VLAN is rate-limited; recovery throttles in Ceph | VLAN 301 QoS + `osd_max_backfills` (Phase 27), R-18 |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Fabric class chosen with documented math | Read `fabric-design.md` | Decision + the §4.4 table applied to *your* model size |
| **A2** | Training pool is ≤ 1:1 oversubscribed | Oversubscription calculation | ≤ 1:1 for `pool=training`, ≤ 3:1 elsewhere |
| **A3** | No CIDR overlaps anywhere | `python tools/net-validate.py` | Exit 0 |
| **A4** | Pod CIDR does not collide with underlay or upstream LAN | Same tool | Exit 0 |
| **A5** | Every node has an IP on every network it participates in | Same tool | 100 % coverage, no duplicates |
| **A6** | MTU consistent per network; mgmt = 1500; data = 9000 | Same tool | Pass |
| **A7** | Every node NIC maps to exactly one switch port | `--check-ports` | No unmapped NICs, no double-assigned ports |
| **A8** | Switch port count is sufficient with ≥ 10 % spare | Count from `switch-ports.yaml` | Spare ports ≥ 10 % |
| **A9** | RoCE contract specifies both NIC and switch side for all 10 parameters | Read `roce-contract.md` | All rows complete |
| **A10** | Switch buffer ≥ 3 × BDP × port count | Datasheet check | Documented; if insufficient, a finding is raised |
| **A11** | Global pause is explicitly disabled in the contract | Grep the doc | Present and explicit |
| **A12** | BGP design specifies ASNs, ECMP, BFD, and host prefix filtering | Read `bgp-design.md` | All four present |
| **A13** | Host prefix-list restricts advertisements to PodCIDR + LB VIPs | Read the config template | Present |
| **A14** | Cable run list generates with lengths and media types | `python tools/gen-cable-list.py` | CSV produced; every run has a length and media |
| **A15** | Failure analysis covers all nine scenarios in Task 8 | Read `fabric-design.md` | All present with a mitigation |
| **A16** | Switch config templates exist for leaf, spine, and mgmt | `ls docs/network/switch-config/` | Three files, all ten sections present |
| **A17** | Nodes that cannot join the training pool are identified | Cross-reference NIC speed/RDMA with `pool` labels | Finding raised for each (R-01) |

---

## ↩️ ROLLBACK

Design artifacts only — `git revert`. No hardware is configured in this phase.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Not enough switch ports for the plan | Under-counted uplinks or MLAG peer links | Recount: hosts + uplinks + MLAG + spare. Uplinks are usually the forgotten term. |
| Pod CIDR overlaps the corporate LAN | `10.244.0.0/16` is a common default that collides | Pick from a range confirmed unused; `10.244.0.0/14` is chosen here for a /24-per-node at 1024 nodes |
| Cannot decide oversubscription | Unclear which nodes do collectives | Split the fleet: `pool=training` gets 1:1; everything else 3:1. Encode it in node labels (Phase 14). |
| Vendor documentation contradicts the RoCE contract | Vendor-specific buffer/QoS model | Reality wins (Rule 6). Preserve the *intent* — lossless prio 3, ECN-marked, watchdog on — and record the syntax in `deviations.md` |
| Switch has < 32 MB buffer | Cheap merchant silicon | Either accept higher loss under incast (and expect worse tail latency) or change the switch model. Raise it as a Phase 05 finding. |
| BGP unnumbered not supported by the switch | Older NOS | Fall back to /31 point-to-point links; add them to `ip-plan.yaml`. More addresses to manage but functionally equivalent. |
| Uncertain whether the NIC does RoCEv1 or v2 | Firmware/config | `mlxconfig -d <dev> q \| grep ROCE`; v2 requires `ROCE_NEXT_PROTOCOL` set and GID index 3 |

---

## 🚫 DO NOT

- **Do not** configure any switch or NIC in this phase. Design only. Phase 07 does management-network bring-up; Phase 21 applies RoCE QoS.
- **Do not** use a flat L2 network "for simplicity." It works at 8 nodes and fails at 100 (broadcast, ARP tables, spanning-tree convergence).
- **Do not** enable global pause (802.3x) anywhere. PFC on priority 3 only.
- **Do not** put the RDMA network on the same VLAN as Kubernetes traffic. Congestion on one must not pause the other.
- **Do not** make the management VLAN routable from tenant workloads. It controls power.
- **Do not** skip the buffer-size check. It is invisible until the fabric is under real load, and then it is the fault.
- **Do not** assign IP addresses to pods here. Cilium IPAM handles that (Phase 13).

---

## 📤 HANDOFF

`evidence/phase-03/handoff.md` must state:

1. **Fabric class chosen** and the per-node link speed by pool.
2. **The full IP plan** — Phase 07 (DHCP/DNS), Phase 09 (Talos static IPs), Phase 13 (Cilium), Phase 21 (SR-IOV VF addressing), and Phase 27 (Ceph networks) all read it.
3. **VLAN IDs** — needed by Phase 09 (Talos interface config) and Phase 21 (Multus NetworkAttachmentDefinitions).
4. **BGP ASNs and peer addresses** — Phase 13's `CiliumBGPClusterConfig` uses these verbatim.
5. **The RoCE contract** — Phase 21 implements it on both ends and Phase 22 validates it.
6. **Switch models and their buffer sizes** — determines how aggressively Phase 22 can push incast patterns.
7. **Nodes excluded from the training pool** on network grounds, with the upgrade needed and its cost (feeds Phase 05).
8. **The cable run list** — hand it to whoever racks the hardware.

---

## ➡️ NEXT

**[PHASE-04 — Security Architecture & Threat Model](PHASE-04.md)** — define trust zones, the PKI hierarchy, the identity model, and the policy baseline before any of it is implemented.
