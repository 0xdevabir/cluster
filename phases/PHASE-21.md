# PHASE 21 — SR-IOV, RDMA & RoCE Fabric

| | |
|---|---|
| **Stage** | 3 — Acceleration Fabric |
| **Estimated effort** | 6–7 hours |
| **Depends on** | 13, 20 |
| **Blocks** | 22, 26, 36, 37, 39, 40 |
| **Risk** | 🔴 High — RoCE misconfiguration causes PFC storms that degrade **all** traffic on the fabric (R-10) |
| **Blast radius** | The entire data fabric, including storage |
| **Architecture refs** | `ARCHITECTURE.md#l23-the-rdma-path`, `#l24-roce-lossless-configuration-contract`, ADR-005, ADR-006, R-01, R-10 |

---

## 🎯 MISSION

Put **line-rate, kernel-bypass networking directly into pods**: SR-IOV virtual functions attached via Multus, RDMA verbs devices exposed to containers, and the RoCEv2 lossless contract from Phase 03 applied and verified on both the NIC and the switch — reaching ≥ 96 % of line rate with < 3 µs p99 latency (benchmark B4).

> 💡 **WHY this is the phase the whole project depends on.** From `ULTIMATE-PLAN.md §4.4`: a 16-node allreduce takes 240 s over TCP on 1 GbE and 2.3 s over 100 GbE RoCE. Every scaling-efficiency number in §8.2, every distributed-training SLO in §5, and gate G6 all rest on this phase producing a working RDMA path. **If RDMA does not work, NEXUS is a collection of single-node machines with a shared filesystem.**

> ⚠️ **DANGER — PFC storms (R-10).** Priority Flow Control makes Ethernet lossless by pausing the sender. Misconfigured — enabled on the wrong priority, without ECN, without a watchdog, or with mismatched settings between NIC and switch — it causes head-of-line blocking that degrades *every* traffic class, including storage and the Kubernetes control plane. **Enable PFC on priority 3 only, with ECN and a watchdog, and verify both ends agree before putting load on it.**

---

## ✅ PREFLIGHT

```bash
bash tools/net/cilium-verify.sh                     # Phase 13 green
bash tools/topology/verify-alignment.sh             # Phase 20 green

# RDMA-capable nodes
kubectl get nodes -l nexus.io/nic.rdma=true

# IOMMU is enabled (required for SR-IOV) — Phase 09/20
for n in $RDMA_NODES; do
  talosctl --nodes "$n" dmesg | grep -iE 'DMAR: IOMMU enabled|AMD-Vi: Interrupt remapping'
done

# The NIC is in the right firmware mode
# mlxconfig -d <dev> q | grep -E 'SRIOV_EN|NUM_OF_VFS|LINK_TYPE'
# Expect: SRIOV_EN=True, NUM_OF_VFS >= 4, LINK_TYPE_P1=ETH(2)

# ⚠️ Switch-side RoCE QoS from Phase 03 must be applied BEFORE enabling PFC on hosts
# ssh <leaf> 'show qos interface swp1'  → PFC on priority 3, ECN on TC3
test -f docs/network/roce-contract.md
```

**If the switch side is not configured, do not enable host-side PFC.** One-sided PFC is worse than none.

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/acceleration/network-operator/
  values.yaml                        # NVIDIA Network Operator
  nicclusterpolicy.yaml
  application.yaml                   # sync-wave 20
clusters/nexus-prod/acceleration/sriov/
  sriovnetworknodepolicy.yaml        # VF creation per node pool
  sriovnetwork-rdma.yaml             # the VLAN 400 network
  networkattachmentdefinition.yaml
clusters/nexus-prod/acceleration/rdma/
  rdma-shared-device-plugin.yaml     # for nodes where SR-IOV is unavailable
  roce-tuning-daemonset.yaml         # applies the lossless contract on the NIC
tools/net/
  rdma-verify.sh
  bench-rdma.sh                      # 📊 B4
  roce-health.sh                     # PFC/ECN counters, the R-10 tripwire
  full-mesh-test.sh                  # every node pair (gate G4)
docs/operations/rdma-runbook.md
benchmarks/baselines/b4-rdma.json
evidence/phase-21/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Source |
|---|---|---|
| NVIDIA Network Operator | `24.10.0` | `oci://ghcr.io/nvidia/network-operator` |
| Multus CNI | `4.1.3` | via Network Operator |
| SR-IOV Network Operator | `1.4.0` | `oci://ghcr.io/k8snetworkplumbingwg/sriov-network-operator-chart` |
| SR-IOV CNI | `2.8.1` | via the operator |
| RDMA shared device plugin | `1.5.1` | `ghcr.io/mellanox/k8s-rdma-shared-dev-plugin` |
| Whereabouts IPAM | `0.8.0` | for VF address assignment |
| MLNX_OFED / DOCA | in-tree `mlx5` (Talos kernel) | ⚠️ do not install OFED on Talos |

> 💡 **Do not install MLNX_OFED on Talos.** The in-tree `mlx5_core`/`mlx5_ib` drivers in the Talos kernel are current and sufficient. OFED expects a mutable filesystem and a package manager, neither of which exists. Set `ofedDriver.deploy: false` in the Network Operator.

---

## 📋 TASKS

### Task 1 — Choose the RDMA exposure mode

Two mechanisms, different trade-offs. **Pick per node pool and record it.**

| | **SR-IOV VF** (recommended) | **RDMA shared device** |
|---|---|---|
| How | A hardware virtual function is bound into the pod's netns | The host's RDMA device is shared into pods |
| Isolation | Hardware-level; each pod gets its own VF | None; all pods share one device |
| Performance | Full line rate, own queues | Full line rate, contended queues |
| Pod count per node | ≤ number of VFs (typically 8–64) | Unlimited |
| Requires | IOMMU + SR-IOV firmware + PCIe capacity | Just the driver |
| Networking | Its own IP on VLAN 400 (Multus secondary interface) | Uses host networking or the primary CNI IP |
| **Use for** | **Training pool** — isolation and predictable performance | Nodes without SR-IOV; low-density RDMA users |

---

### Task 2 — Create the VFs

**`sriovnetworknodepolicy.yaml`**
```yaml
apiVersion: sriovnetwork.openshift.io/v1
kind: SriovNetworkNodePolicy
metadata: { name: rdma-vfs, namespace: network-operator }
spec:
  nodeSelector:
    nexus.io/nic.rdma: "true"
    nexus.io/pool: training
  resourceName: rdma_vf                # → pods request nvidia.com/rdma_vf: 1
  numVfs: 8                            # ⚠️ each VF consumes NIC resources; do not overshoot
  priority: 99
  nicSelector:
    vendor: "15b3"                     # Mellanox
    deviceID: "1017"                   # ConnectX-5 — adjust per your inventory
    pfNames: [ "enp2s0f0np0#0-7" ]     # VF index range on this PF
  deviceType: netdevice                # NOT vfio-pci — we want the kernel netdev + verbs
  isRdma: true                         # ⚠️ THE critical flag: exposes the verbs device
  linkType: eth
  needVhostNet: false
```

> ⚠️ **`isRdma: true` is the flag people forget.** Without it, the pod gets a fast netdev but **no `/dev/infiniband/uverbs*`**, so NCCL and UCX silently fall back to TCP over that interface. You get a working-looking cluster at a fraction of the performance, and the only symptom is a disappointing benchmark. **Verify by listing `/dev/infiniband` inside a pod.**

> ⚠️ **VF count is a real trade.** Each VF consumes queue pairs and MSI-X vectors on the NIC. 8 VFs is ample for a training node running 1–4 ranks. 64 VFs on a ConnectX-5 will exhaust resources and degrade PF performance. Start at 8.

**Applying this reboots the node** (VF creation requires a driver reload, and Talos handles it as a config change). Roll with drain, as in Phase 20.

---

### Task 3 — The pod network

**`sriovnetwork-rdma.yaml`** — VLAN 400 from Phase 03:
```yaml
apiVersion: sriovnetwork.openshift.io/v1
kind: SriovNetwork
metadata: { name: rdma-net, namespace: network-operator }
spec:
  networkNamespace: default            # NetworkAttachmentDefinition target ns
  resourceName: rdma_vf
  vlan: 400
  spoofChk: "off"                      # RDMA needs to send with its own addressing
  trust: "on"                          # required for the VF to set QoS/DSCP bits
  ipam: |
    {
      "type": "whereabouts",
      "range": "10.220.0.0/20",
      "exclude": ["10.220.0.0/24"],
      "routes": [ { "dst": "10.220.0.0/20" } ]
    }
```

> ⚠️ **`trust: on` and `spoofChk: off` are required for RoCE**, because the VF must set its own DSCP/priority bits for the lossless class. They also weaken isolation — a VF can spoof addresses on VLAN 400. **This is an accepted risk given the trusted-org tenancy model (ADR-023); document it.**

**Pod usage:**
```yaml
metadata:
  annotations:
    k8s.v1.cni.cncf.io/networks: rdma-net       # Multus attaches net1
spec:
  containers:
    - name: trainer
      resources:
        limits:
          nvidia.com/rdma_vf: "1"                # the VF
          nvidia.com/gpu: "1"                    # (or a DRA claim)
      securityContext:
        capabilities: { add: [ IPC_LOCK ] }      # ⚠️ required to pin memory for RDMA
```

> 💡 **`IPC_LOCK` is mandatory.** RDMA registers and pins user memory pages. Without `IPC_LOCK`, `ibv_reg_mr` fails and every RDMA operation errors out with a confusing message. Add it via a Kyverno mutation for pods annotated for RDMA so users never hit this.

---

### Task 4 — Apply the RoCE lossless contract

Implements `docs/network/roce-contract.md` (Phase 03) on the NIC side. A DaemonSet on RDMA nodes.

```bash
# ── The contract, NIC side. Runs per node, idempotent, re-applied every 5 min. ──
DEV=mlx5_0
NETDEV=enp2s0f0np0

# 1. Trust DSCP (not PCP) — must match the switch's classification
mlnx_qos -i "$NETDEV" --trust dscp

# 2. PFC on priority 3 ONLY
mlnx_qos -i "$NETDEV" --pfc 0,0,0,1,0,0,0,0

# 3. ⚠️ Disable global (802.3x) pause — PFC and global pause must never coexist
ethtool -A "$NETDEV" rx off tx off

# 4. RoCE traffic class: DSCP 26 → ToS 106 (26 << 2)
cma_roce_tos -d "$DEV" -t 106
echo 106 > "/sys/class/infiniband/$DEV/tc/1/traffic_class"

# 5. ECN on the RoCE priority (both notification point and reaction point)
echo 1 > "/sys/class/net/$NETDEV/ecn/roce_np/enable/3"
echo 1 > "/sys/class/net/$NETDEV/ecn/roce_rp/enable/3"
echo 48 > "/sys/class/net/$NETDEV/ecn/roce_np/cnp_dscp"      # CNP on DSCP 48

# 6. MTU
ip link set "$NETDEV" mtu 9000

# 7. RoCE mode v2 and GID index
#    ⚠️ GID index 3 is typically RoCEv2/IPv4. VERIFY per NIC — a wrong index means
#    a silent fallback or a failure to connect.
show_gids "$DEV"
```

**Verify both ends agree:**
```bash
# NIC side
mlnx_qos -i "$NETDEV"
#   Expect: PFC enabled on prio 3 only; trust=dscp; no global pause

# Switch side (Phase 03 template)
# show qos interface swp1
#   Expect: PFC prio 3, ECN/WRED on TC3, watchdog enabled, buffer profile applied
```

> ⚠️ **A one-sided PFC configuration is the classic R-10 failure.** If the host pauses but the switch does not (or vice versa), you get buffer overrun and drops on a "lossless" class — which RoCE handles very badly, since it assumes losslessness. **Verify both ends before load testing.**

---

### Task 5 — 📊 Benchmark B4

**`tools/net/bench-rdma.sh`** — the number the whole project depends on.

```bash
# ── Bandwidth ──
# Server pod on node A:
ib_write_bw -d mlx5_0 -x 3 -F --report_gbits -D 30
# Client pod on node B:
ib_write_bw -d mlx5_0 -x 3 -F --report_gbits -D 30 <server-vf-ip>
#   -x 3  : GID index (verify with show_gids)
#   -F    : ignore CPU frequency changes
#   -D 30 : run for 30 seconds

# ── Latency ──
ib_send_lat  -d mlx5_0 -x 3 -F -n 10000
ib_write_lat -d mlx5_0 -x 3 -F -n 10000

# ── Message-rate (small messages — where latency dominates) ──
ib_send_bw -d mlx5_0 -x 3 -F -s 64 -D 30

# ── Bidirectional (catches half-duplex issues) ──
ib_write_bw -d mlx5_0 -x 3 -F -b --report_gbits -D 30
```

**Targets:**

| Metric | 25 GbE | 100 GbE | Gate |
|---|---|---|---|
| `ib_write_bw` unidirectional | ≥ 24 Gb/s | ≥ 96 Gb/s | **≥ 96 % of line rate** |
| `ib_write_bw` bidirectional | ≥ 46 Gb/s | ≥ 185 Gb/s | ≥ 92 % |
| `ib_send_lat` p50 | < 2 µs | < 1.8 µs | — |
| `ib_send_lat` **p99** | < 4 µs | **< 3 µs** | **Gate** |
| Message rate (64 B) | ≥ 10 M msg/s | ≥ 15 M msg/s | — |

📊 **Compare against TCP over the same link** (Phase 13's B3). RDMA should be ~4 % faster in bandwidth and **10–20× better in latency**. If RDMA is not dramatically better in latency, it is almost certainly falling back to TCP — check `isRdma`, the verbs device, and the GID index.

**`tools/net/full-mesh-test.sh`** — **gate G4** requires B4 to pass on *every node pair* in the training pool, not just one:
```bash
# For all pairs (i,j) in the training pool:
#   run a 10-second ib_write_bw, record bandwidth and latency
# Emit a heatmap. Any pair below threshold is a finding:
#   · a whole row/column low → that node's NIC, cable, or slot
#   · a specific pair low    → the path between them (a spine/leaf link)
# Commit the heatmap to evidence/phase-21/.
```

---

### Task 6 — RoCE health monitoring (the R-10 tripwire)

**`tools/net/roce-health.sh`** — and the corresponding Prometheus rules (wired up in Phase 45).

```bash
# ── Pause frames: should be ~0 in steady state ──
ethtool -S "$NETDEV" | grep -E 'rx_pause|tx_pause|rx_prio3_pause|tx_prio3_pause'

# ── ECN marking: nonzero is HEALTHY (congestion control working) ──
cat /sys/class/infiniband/$DEV/ports/1/hw_counters/np_ecn_marked_roce_packets
cat /sys/class/infiniband/$DEV/ports/1/hw_counters/rp_cnp_handled

# ── Errors: any nonzero is a problem ──
cat /sys/class/infiniband/$DEV/ports/1/hw_counters/out_of_sequence
cat /sys/class/infiniband/$DEV/ports/1/hw_counters/packet_seq_err
cat /sys/class/infiniband/$DEV/ports/1/hw_counters/local_ack_timeout_err
ethtool -S "$NETDEV" | grep -E 'rx_crc_errors|rx_discards|tx_discards'
```

**Interpretation table — put this in the runbook:**

| Signal | Healthy | Warning | Meaning |
|---|---|---|---|
| `tx_pause` rate | ~0 | > 100/s | **The node is being paused. Head-of-line blocking is likely. R-10.** |
| `rx_pause` rate | ~0 | > 100/s | The node is pausing others — it cannot drain fast enough |
| `np_ecn_marked` | > 0 under load | — | Good: ECN is doing its job instead of PFC |
| `rp_cnp_handled` | > 0 under load | — | Good: the sender is reacting to congestion |
| `out_of_sequence` | 0 | any | Packet loss on a "lossless" class — the contract is broken |
| `packet_seq_err` | 0 | any | Same |
| `rx_crc_errors` | 0 | any | Physical layer: cable, transceiver, or bend radius |

> 💡 **The healthy signature is: ECN marks present, pause frames near zero.** If pause frames dominate and ECN marks do not, ECN is not configured and you are relying on PFC alone — which is exactly the configuration that storms.

**Incast test** — deliberately create the condition that breaks fabrics:
```bash
# N-to-1: have 8 nodes write to 1 node simultaneously at line rate.
# Watch: tx_pause on the receiver, ECN marks, and whether OTHER traffic
#        (storage, ping to the API) degrades.
# PASS: ECN throttles the senders; other traffic classes are unaffected;
#       no PFC watchdog trips.
# FAIL: pause frames flood; storage latency spikes; the control plane hiccups.
#       → ECN thresholds are wrong, or buffers are too small (Phase 03 F-09).
```

---

### Task 7 — GPUDirect RDMA (where the hardware allows it)

`ULTIMATE-PLAN.md §4.2`: GeForce does not officially support GPUDirect RDMA. Handle both cases.

```bash
# Is it available?
lsmod | grep nvidia_peermem
ls /sys/kernel/mm/memory_peers/                 # registered peer clients

# Definitive test (Phase 22 uses this too):
NCCL_DEBUG=INFO <nccl test>  2>&1 | grep -i gdr
#   "NCCL INFO ... [GDR] " → enabled
#   "GDRDMA disabled"       → host-bounce path
```

| | GDR available (RTX PRO / datacenter) | GDR unavailable (GeForce) |
|---|---|---|
| Path | NIC ↔ GPU VRAM directly | NIC ↔ host DRAM ↔ GPU VRAM |
| Extra latency | 0 | **+8–15 µs per message** |
| Extra memory bandwidth | 0 | One full round trip through DRAM |
| `NCCL_NET_GDR_LEVEL` | `PIX` | `LOC` (disabled) |
| Node label | `nexus.io/gpu.gpudirect-rdma=true` | `=false` |

**Set the label correctly** (Phase 14 measures it). Phase 22 injects the right NCCL configuration per pool based on it. **Do not set `NCCL_NET_GDR_LEVEL=PIX` on GeForce** — NCCL will attempt GDR, fail, and fall back with wasted initialization time and confusing logs.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Network Operator running, OFED **not** deployed | `kubectl -n network-operator get pods`; grep values | `ofedDriver.deploy: false` |
| **A2** | VFs created on every training-pool node | `cat /sys/class/net/*/device/sriov_numvfs` | Matches `numVfs` |
| **A3** | VF resources advertised | `kubectl describe node \| grep rdma_vf` | Count matches |
| **A4** | **`/dev/infiniband/uverbs*` present inside a pod** | Exec into a test pod, `ls /dev/infiniband` | Present — this proves `isRdma` worked |
| **A5** | `ibv_devinfo` works inside a pod | Exec | Device listed, `PORT_ACTIVE` |
| **A6** | Multus attaches `net1` on VLAN 400 with an IP | `ip addr` in the pod | Present, correct subnet |
| **A7** | VF MTU is 9000 | `ip link` in the pod | 9000 |
| **A8** | `IPC_LOCK` is present (mutated in, not user-supplied) | Inspect a pod created without it | Capability added |
| **A9** | PFC enabled on **priority 3 only** | `mlnx_qos -i <dev>` | Only prio 3 |
| **A10** | **Global pause (802.3x) is disabled** | `ethtool -a <dev>` | rx/tx off |
| **A11** | Trust mode is DSCP | `mlnx_qos` | dscp |
| **A12** | ECN enabled on the RoCE priority, both np and rp | Read sysfs | Both `1` |
| **A13** | Switch-side config matches the contract | `show qos interface` on a leaf | Matches all 10 contract rows |
| **A14** | 📊 **B4 bandwidth ≥ 96 % of line rate** | `bench-rdma.sh` | Recorded |
| **A15** | 📊 **B4 p99 latency < 3 µs (100 G) / < 4 µs (25 G)** | Same | Recorded |
| **A16** | 📊 RDMA latency is 10–20× better than TCP on the same link | Compare with B3 | Confirmed — proves no TCP fallback |
| **A17** | 📊 **Full-mesh test passes on every training-pool node pair (G4)** | `full-mesh-test.sh` heatmap | No pair below threshold |
| **A18** | Any failing pair is diagnosed to a node, cable, or path | Read the heatmap | Root cause identified |
| **A19** | Incast test: ECN throttles, no PFC storm, other classes unaffected | Run it; watch counters and storage latency | PASS |
| **A20** | `out_of_sequence` and `packet_seq_err` are zero after load | Read counters | Zero |
| **A21** | `rx_crc_errors` zero on every link | Fleet-wide | Zero; any nonzero → replace the cable |
| **A22** | GDR status determined and labeled correctly per node | Compare label with the NCCL probe | Matches |
| **A23** | RoCE config survives a node reboot | Reboot; re-check `mlnx_qos` | Reapplied by the DaemonSet |

---

## ↩️ ROLLBACK

```bash
# Remove the SR-IOV policy (VFs are destroyed; node reboots)
kubectl -n network-operator delete sriovnetworknodepolicy rdma-vfs

# ⚠️ EMERGENCY: disable PFC if a storm is degrading the fabric
#    Do this on BOTH ends, or you make it worse.
mlnx_qos -i <netdev> --pfc 0,0,0,0,0,0,0,0     # every node
# ssh <leaf> 'no priority-flow-control priority 3'
# Workloads fall back to TCP: slower, but the fabric recovers.
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| No `/dev/infiniband` in the pod | `isRdma: true` missing on the node policy | Set it and re-apply. **This is the #1 silent failure.** |
| `ibv_reg_mr` fails / "cannot allocate memory" | `IPC_LOCK` missing, or `memlock` ulimit too low | Add the capability; set `memlock: unlimited` |
| RDMA connects but is slow (~TCP speed) | Wrong GID index → not actually RoCEv2 | `show_gids`; pick the RoCEv2/IPv4 index; set `NCCL_IB_GID_INDEX` accordingly |
| `ib_write_bw` cannot connect | VLAN 400 not reachable, or the switch port is not trunked | Verify with a plain `ping` over `net1` first |
| Bandwidth is ~50 % of line rate | PCIe link too narrow for the NIC (Phase 01 §4.3) | `lspci -vv \| grep LnkSta`. A 100 G NIC needs PCIe 4.0 x8 minimum. |
| High `tx_pause`, low throughput | PFC head-of-line blocking; ECN not working | Verify ECN on both ends; check switch buffer allocation (Phase 03 F-09) |
| `out_of_sequence` climbing | Packet loss on the lossless class | The contract is broken somewhere. Compare NIC and switch config line by line. |
| PFC watchdog trips | A receiver is stuck paused | Usually a slow or overloaded receiver. Watchdog dropping the queue is the *correct* behavior — find the receiver. |
| VFs disappear after reboot | `sriov_numvfs` not persisted | The operator recreates them; if not, check the Talos config and the operator's node state |
| `rx_crc_errors` on one link | Physical: cable, transceiver, or bend radius | Replace the DAC. Check bend radius (Phase 03 cabling guide). |
| GDR "works" on GeForce | It does not; NCCL fell back | Read the full `NCCL_DEBUG=INFO` output; set `NCCL_NET_GDR_LEVEL=LOC` |
| Storage latency spikes during training | RoCE and storage share a link without QoS isolation | Verify VLAN 301's QoS class and rate limits (Phase 03) |

---

## 🚫 DO NOT

- **Do not** enable host-side PFC before the switch side is configured and verified.
- **Do not** enable global pause (802.3x) anywhere. PFC on priority 3 only.
- **Do not** omit `isRdma: true`. The failure is silent and expensive.
- **Do not** install MLNX_OFED on Talos.
- **Do not** create more VFs than you need. Each consumes NIC resources.
- **Do not** set `NCCL_NET_GDR_LEVEL=PIX` on GeForce nodes.
- **Do not** claim B4 passed from a single node pair. G4 requires the full mesh.
- **Do not** skip the incast test. It is the only test that reproduces the R-10 failure mode.
- **Do not** tune NCCL here. Phase 22 — this phase provides the fabric NCCL will use.
- **Do not** accept nonzero `rx_crc_errors`. Replace the cable.

---

## 📤 HANDOFF

`evidence/phase-21/handoff.md` must state:

1. **📊 B4 results** — bandwidth, latency (p50/p99), and message rate, per node pair. Phase 22 and Phase 48 build on these.
2. **📊 The full-mesh heatmap** and gate G4's verdict.
3. **Which nodes have working RDMA** and which do not, with the reason. Phase 30's `pool=training` ResourceFlavor must exclude the failures.
4. **The exposure mode per pool** (SR-IOV VF vs. shared device) and the VF count.
5. **The GID index** that works — Phase 22 sets `NCCL_IB_GID_INDEX` to it.
6. **GDR availability per node pool** — Phase 22 branches its NCCL configuration on this.
7. **The applied RoCE contract**, and any deviation from Phase 03's specification.
8. **The incast test result** and the ECN thresholds that worked.
9. **The RDMA network CIDR and VLAN** — Phase 26 (NVMe-oF) and Phase 39 (UCX) both use them.
10. **Any physical remediation still outstanding** (cables, transceivers, slots).

---

## ➡️ NEXT

**[PHASE-22 — NCCL & Collective Communication Tuning](PHASE-22.md)** — turn a working RDMA fabric into fast collectives, and make the configuration automatic so users never set an NCCL variable by hand.
