# ARCHITECTURE — Project NEXUS

### The Component-Level Design of a Composable Bare-Metal Supercomputer

> Companion to `ULTIMATE-PLAN.md`. That document says *what and why*. This one says *how the pieces fit*.
> Every implementation phase in `phases/` references a section here.

---

## Table of Contents

- [0. Architectural Overview](#0-architectural-overview)
- [L0 — Facility Layer](#l0--facility-layer)
- [L1 — Hardware Layer](#l1--hardware-layer)
- [L2 — Network Fabric](#l2--network-fabric)
- [L3 — Provisioning & OS Layer](#l3--provisioning--os-layer)
- [L4 — Cluster Substrate](#l4--cluster-substrate)
- [L5 — Resource Abstraction Layer](#l5--resource-abstraction-layer)
- [L6 — Storage Fabric](#l6--storage-fabric)
- [L7 — Scheduling & Orchestration](#l7--scheduling--orchestration)
- [L8 — Distributed Compute Runtimes](#l8--distributed-compute-runtimes)
- [L9 — Platform Services](#l9--platform-services)
- [L10 — Observability & Control Loops](#l10--observability--control-loops)
- [X1 — Security Architecture](#x1--security-architecture)
- [X2 — Failure Domains & Fault Model](#x2--failure-domains--fault-model)
- [X3 — Naming, Labeling & Namespace Taxonomy](#x3--naming-labeling--namespace-taxonomy)
- [X4 — Repository Architecture](#x4--repository-architecture)
- [W — Reference Walkthroughs](#w--reference-walkthroughs)
- [C — Component Catalog](#c--component-catalog)
- [ADR — Architecture Decision Records](#adr--architecture-decision-records)
- [P — Capacity & Performance Models](#p--capacity--performance-models)

---

## 0. Architectural Overview

### 0.1 The Eleven-Layer Stack

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  L10  OBSERVABILITY & CONTROL LOOPS                                          ║
║  Prometheus·Mimir │ Grafana │ Loki │ Tempo │ OTel │ Parca │ Kepler │ Hubble  ║
║  DCGM-exporter │ node-exporter │ NPD │ Alertmanager │ auto-remediation       ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  L9   PLATFORM SERVICES                                                      ║
║  Backstage │ JupyterHub │ Harbor+Spegel │ MLflow │ Keycloak │ OpenBao        ║
║  Argo CD │ Argo Workflows │ Gitea mirror │ Gateway API                       ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  L8   DISTRIBUTED COMPUTE RUNTIMES                                           ║
║  Ray (KubeRay) │ Kubeflow Trainer v2 │ MPI Operator+UCX │ Spark │ Dask       ║
║  vLLM + KServe + LeaderWorkerSet │ DeepSpeed │ Slurm (Slinky, optional)      ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  L7   SCHEDULING & ORCHESTRATION                                             ║
║  Kueue (quota·cohort·preempt·TAS) │ coscheduling (gang) │ KEDA │ Descheduler ║
║  WorkloadPriorityClass │ power-aware admission │ checkpoint/requeue          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  L6   STORAGE FABRIC                                                         ║
║  T0 local NVMe │ T1 Mayastor/NVMe-oF │ T2 Rook-Ceph RBD+FS │ T3 RGW/MinIO    ║
║  T4 JuiceFS/Alluxio cache │ Velero │ CSI snapshots                           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  L5   RESOURCE ABSTRACTION                                                   ║
║  DRA (ResourceSlice·DeviceClass·ResourceClaim) │ NVIDIA DRA driver           ║
║  GPU Operator │ Topology Manager │ CPU Manager │ Memory Manager │ NFD        ║
║  SR-IOV DP │ RDMA DP │ Multus │ CDI                                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  L4   CLUSTER SUBSTRATE                                                      ║
║  Kubernetes 1.34+ (HA: 3–5 ctrl) │ etcd (dedicated PLP NVMe) │ containerd    ║
║  Cilium eBPF (kube-proxy replacement, native routing, BGP) │ CoreDNS         ║
║  Kyverno │ cert-manager │ Capsule                                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  L3   PROVISIONING & OS                                                      ║
║  Talos Linux (immutable, API-only) │ Talos Image Factory (+NVIDIA ext)       ║
║  Tinkerbell (Boots/Smee·Tink·Hegel) │ iPXE │ WoL │ PDU control │ PiKVM       ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  L2   NETWORK FABRIC                                                         ║
║  Leaf-spine Clos │ BGP-to-the-host + ECMP │ RoCEv2 (PFC+ECN/DCQCN)           ║
║  MTU 9000 │ VLANs: mgmt·k8s·storage·rdma │ SR-IOV VFs │ LACP/MLAG            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  L1   HARDWARE                                                               ║
║  Archetypes: control·compute-gpu·compute-cpu·storage·infra                   ║
║  NVIDIA GPUs │ NVMe tiers │ ConnectX NICs │ NUMA/PCIe topology               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  L0   FACILITY                                                               ║
║  3-phase power │ switched PDUs │ hot/cold aisle │ CRAC/in-row │ UPS │ racks  ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### 0.2 The Three Planes

NEXUS separates concerns into three planes that fail independently (Law IX).

| Plane | Carries | Components | Failure behavior |
|---|---|---|---|
| **Management plane** | Provisioning, out-of-band, power | 1 GbE mgmt VLAN, Tinkerbell, PDUs, PiKVM, DHCP/TFTP | Loss ⇒ cannot provision new nodes; **running cluster unaffected** |
| **Control plane** | Desired-state reconciliation | kube-apiserver, etcd, controllers, schedulers, Argo CD | Loss ⇒ no new scheduling; **running pods keep running** (kubelet is autonomous) |
| **Data plane** | Actual work + its I/O | Pods, NCCL traffic, Ceph I/O, RDMA | Loss ⇒ workload failure; recovered via requeue + checkpoint |

**Design invariant:** *a control-plane outage must never terminate a running training job.* This is achieved by kubelet's static-pod autonomy, `--node-status-update-frequency` tuning, generous `pod-eviction-timeout`, and Ray/torchrun keeping their own membership state.

### 0.3 The Composability Model — how a "unified pool" actually works

```
       USER INTENT                          PLATFORM MECHANISM                    PHYSICAL REALITY
┌────────────────────────┐          ┌──────────────────────────────┐      ┌──────────────────────────┐
│ "64 CPU cores"         │  ──────► │ Kueue quota admission →      │ ───► │ 4 nodes × 16 exclusive   │
│                        │          │ coscheduling gang →          │      │ cores, NUMA-pinned       │
│                        │          │ Topology Manager alignment   │      │                          │
├────────────────────────┤          ├──────────────────────────────┤      ├──────────────────────────┤
│ "8 GPUs ≥24 GB, same   │  ──────► │ DRA ResourceClaim +          │ ───► │ 8 identical GPUs on 4    │
│  model, RDMA-connected"│          │ DeviceClass selector +       │      │ nodes in the same leaf   │
│                        │          │ Kueue TAS (topology domain)  │      │ domain                   │
├────────────────────────┤          ├──────────────────────────────┤      ├──────────────────────────┤
│ "0.25 of a GPU"        │  ──────► │ DRA shared claim +           │ ───► │ 1 physical GPU running   │
│                        │          │ MPS/time-slice config        │      │ 4 tenants concurrently   │
├────────────────────────┤          ├──────────────────────────────┤      ├──────────────────────────┤
│ "4 TB fast scratch"    │  ──────► │ LocalPV / generic ephemeral  │ ───► │ Raw NVMe on each of the  │
│                        │          │ volume, node-local           │      │ 4 assigned nodes         │
├────────────────────────┤          ├──────────────────────────────┤      ├──────────────────────────┤
│ "shared dataset, 40 TB"│  ──────► │ CephFS RWX PVC / JuiceFS     │ ───► │ Striped across 6 storage │
│                        │          │ mount w/ local NVMe cache    │      │ nodes; hot blocks cached │
├────────────────────────┤          ├──────────────────────────────┤      ├──────────────────────────┤
│ "line-rate RDMA"       │  ──────► │ Multus NetworkAttachment +   │ ───► │ SR-IOV VF bound into the │
│                        │          │ SR-IOV VF + RDMA DP          │      │ pod netns, kernel bypass │
└────────────────────────┘          └──────────────────────────────┘      └──────────────────────────┘
```

The "pool" is not an illusion layer — it is a **matchmaking and wiring service**. Nothing pretends to be local that isn't.

---

## L0 — Facility Layer

### L0.1 Physical layout (per rack, at M4 scale)

```
                     ┌──── COLD AISLE (18–22 °C intake) ────┐
  ╔══════════════════╧═══════════════════════════════════════╧═════════════════╗
  ║ 42U RACK  ×8                                                                ║
  ║ ┌─────────────────────────────────────────────────────────────────────────┐ ║
  ║ │ U42-41  Leaf switch A (32×100G)   ── to Spine 1,2  (2×100G uplinks each)│ ║
  ║ │ U40-39  Leaf switch B (32×100G)   ── MLAG peer of A                     │ ║
  ║ │ U38     Mgmt switch (48×1G)                                             │ ║
  ║ │ U37     PiKVM v4 + 16-port HDMI/USB matrix                              │ ║
  ║ ├─────────────────────────────────────────────────────────────────────────┤ ║
  ║ │ U36-05  12 × compute nodes (open-frame 2U sleds or 4U chassis)          │ ║
  ║ │         each: GPU(s) + ConnectX NIC + NVMe scratch                      │ ║
  ║ ├─────────────────────────────────────────────────────────────────────────┤ ║
  ║ │ U04-01  Cable management / blanking panels                              │ ║
  ║ └─────────────────────────────────────────────────────────────────────────┘ ║
  ║ ZERO-U: 2 × switched 3-phase PDU (30 A) — vertical, both sides             ║
  ╚═══════════════════════════════╤═══════════════════════════════════════════╝
                     └──── HOT AISLE (≤ 35 °C exhaust) ────┘
```

**Per-rack budget:** 12 compute nodes × ~560 W = 6.7 kW + 0.6 kW switching ≈ **7.3 kW/rack**, well inside a 30 A 3-phase 208 V PDU pair (≈ 10.8 kW usable at 80 % derate). This deliberately leaves headroom (Law IX) and lets the scheduler burst.

**Chassis strategy for consumer towers.** Tower cases waste 3–4× the volume of a rack sled and have side-to-side airflow that destroys hot/cold aisle containment. Two acceptable options:

| Option | Density | Cost/node | Airflow | Use when |
|---|---|---|---|---|
| **Open-frame mining rack** (8–12 GPUs per frame, risers) | High | ~$40 | Front-to-back with a fan wall | GPU is the only card; riser bandwidth (PCIe 3.0 x1 on cheap risers) is **unacceptable** — must use x16-to-x16 shielded risers |
| **4U rackmount ATX chassis** | Medium | ~$120–200 | Native front-to-back | **Recommended** — standard ATX board fits, full x16 slots, hot-swap bays |
| Keep towers on shelves | Low | $0 | Chaotic | Pilot (M1) only. Never past 24 nodes. |

**Instrumentation.** Every rack gets: 2× intake temp probe, 2× exhaust temp probe, per-outlet PDU power metering, and a differential pressure sensor if containment is used. All scraped into Prometheus (Phase 45) and used by the power-aware scheduler (Phase 31).

---

## L1 — Hardware Layer

### L1.1 Node topology model

Every node publishes a machine-readable topology document consumed by NFD, the Topology Manager, and the scheduler.

```yaml
# inventory/nodes/nx-c-r01-05.yaml  (canonical per-node record, Phase 01)
apiVersion: nexus.io/v1
kind: NodeSpec
metadata:
  name: nx-c-r01-05                 # <cluster>-<archetype>-<rack>-<slot>
spec:
  archetype: compute-gpu
  location: { site: hq, room: dc1, rack: r01, u: 22, pdu: [r01-pdu-a:12, r01-pdu-b:12] }
  cpu:
    model: "AMD Ryzen 9 7950X"
    sockets: 1
    cores: 16
    threads: 32
    numaNodes: 1                    # AM5 = 1 NUMA node (NPS1); EPYC may be 1/2/4
    l3CacheMB: 64
    ccxCount: 2                     # CCD boundary matters for cache-sensitive work
  memory: { totalGB: 128, type: DDR5-5600, ecc: false, channels: 2 }
  gpus:
    - index: 0
      model: "NVIDIA GeForce RTX 4090"
      uuid: GPU-xxxxxxxx-....
      vramGB: 24
      computeCapability: "8.9"
      pcieBusId: "0000:01:00.0"
      pcieGen: 4
      pcieWidth: x8                 # ← DEGRADED from x16, documented deliberately
      numaAffinity: 0
      nvlink: false
      p2pCapable: false
      gpudirectRdma: false
      tdpWatts: 450
      powerCapWatts: 320            # efficiency-knee cap, Phase 48
  nics:
    - name: enp2s0f0np0
      model: "Mellanox ConnectX-5 EN"
      speedGbps: 100
      pcieBusId: "0000:02:00.0"
      pcieWidth: x8
      numaAffinity: 0
      rdma: { capable: true, protocol: RoCEv2, device: mlx5_0 }
      sriov: { capable: true, maxVfs: 8, configuredVfs: 4 }
      switchPort: "r01-leaf-a:eth5"
    - name: eno1
      speedGbps: 1
      role: management
  storage:
    - device: /dev/nvme0n1
      model: "Samsung 990 PRO 2TB"
      sizeGB: 2000
      class: consumer-nvme          # no PLP → tier0 only
      plp: false
      role: scratch
      tier: 0
    - device: /dev/nvme1n1
      sizeGB: 256
      role: boot
  firmware: { bios: "3.10", agesa: "1.2.0.2", secureBoot: false, iommu: on, resizableBar: on, wol: enabled }
  benchmarks:                        # populated by Phase 47, refreshed monthly
    b1_gemm_tflops: 82.4
    b2_h2d_gbs: 24.9
    b4_rdma_gbs: 11.7
    b6_nvme_randread_iops: 1_020_000
```

### L1.2 PCIe topology rules (enforced by Phase 19 validation)

| Rule | Check | Failure action |
|---|---|---|
| GPU and its assigned NIC share a NUMA node | `nvidia-smi topo -m` shows `PHB` or better; `/sys/class/net/*/device/numa_node` matches | Taint node `nexus.io/topology=misaligned:NoSchedule` |
| GPU link runs at negotiated max width/speed | `lspci -vv \| grep LnkSta` | Alert `GPULinkDegraded`; possible riser/reseat issue |
| NIC has ≥ PCIe 4.0 x8 for 100 GbE | Same | Node excluded from the training pool label |
| ACS is **disabled** for P2P-capable topologies, **enabled** where SR-IOV isolation is required | `lspci -vvv \| grep ACSCtl` | Documented per-archetype in Phase 19 |
| IOMMU enabled (required for SR-IOV) | `dmesg \| grep -i -e DMAR -e IOMMU` | Node cannot join RDMA pool |

---

## L2 — Network Fabric

### L2.1 Physical topology (M4, 100+ nodes)

```
                        ┌──────────┐        ┌──────────┐
                        │ SPINE-1  │        │ SPINE-2  │      (32×100G each)
                        └────┬─────┘        └─────┬────┘
              ┌──────────────┼────────────────────┼──────────────┐
              │  ┌───────────┼──────────┬─────────┼───────────┐  │
         2×100G each        ...        ...       ...         2×100G each
              │              │          │         │              │
        ┌─────┴────┐   ┌─────┴────┐  ┌──┴───┐  ┌──┴───┐   ┌──────┴───┐
        │ LEAF-R01 │   │ LEAF-R02 │  │ ...  │  │ ...  │   │ LEAF-R08 │
        │  (MLAG)  │   │  (MLAG)  │  │      │  │      │   │  (MLAG)  │
        └─────┬────┘   └─────┬────┘  └──────┘  └──────┘   └──────┬───┘
              │ 1×100G/node  │                                    │
        ┌─────┴──────────────┴────────────────────────────────────┴─────┐
        │  12 compute nodes per rack  ·  BGP unnumbered to each host    │
        └────────────────────────────────────────────────────────────────┘

  Oversubscription: 12 hosts × 100G down : 4 × 100G up  =  3:1   (general pool)
  Training pool racks:  8 hosts × 100G down : 8 × 100G up  =  1:1 (non-blocking)
```

### L2.2 Addressing & VLAN plan

| Network | VLAN | CIDR | Purpose | MTU | QoS |
|---|---|---|---|---|---|
| Management / OOB | 100 | `10.100.0.0/22` | PXE, DHCP, PDU, PiKVM, Talos API fallback | 1500 | best-effort |
| Kubernetes node | 200 | `10.200.0.0/20` | Node IPs, kubelet, etcd, API | 9000 | PCP 0 |
| Pod network | — | `10.244.0.0/14` | Cilium native routing; **/24 per node** | 9000 | PCP 0 |
| Service network | — | `10.96.0.0/16` | ClusterIP | — | — |
| Storage (Ceph public) | 300 | `10.300.0.0/22`* | Client ↔ OSD | 9000 | PCP 2 |
| Storage (Ceph cluster) | 301 | `10.301.0.0/22`* | OSD ↔ OSD replication/backfill | 9000 | PCP 2, rate-limited |
| **RDMA / RoCEv2** | 400 | `10.400.0.0/20`* | NCCL, UCX, NVMe-oF | 9000 | **PCP 3 — lossless (PFC), ECN-marked** |
| Load balancer | — | `10.10.0.0/24` | Cilium BGP-advertised service VIPs | 1500 | — |

\* *Illustrative; real CIDRs assigned in Phase 03. VLAN IDs > 4094 are invalid — the plan uses 300/301/400 as VLAN IDs and separate /22s; adjust to your IPAM.*

**Why L3-to-the-host (BGP unnumbered):** eliminates spanning tree, gives ECMP multipathing across both leaf uplinks, keeps broadcast domains at ~12 hosts, and makes rack loss a routing event rather than an L2 storm. Cilium's BGP Control Plane peers directly with the leaf pair and advertises PodCIDRs and LoadBalancer VIPs.

### L2.3 The RDMA path (the most performance-critical design in NEXUS)

```
  ┌───────────────────────── COMPUTE NODE ──────────────────────────┐
  │                                                                 │
  │  ┌─────────── POD (training rank) ──────────┐                    │
  │  │  PyTorch → NCCL → libibverbs             │                    │
  │  │            │                             │                    │
  │  │            ├─ net_ib plugin              │                    │
  │  │            ▼                             │                    │
  │  │  /dev/infiniband/uverbs0  (RDMA device plugin)                │
  │  │  net1 = SR-IOV VF        (Multus + SR-IOV DP)                 │
  │  └────────────┬─────────────────────────────┘                    │
  │               │  ── kernel BYPASS ──                              │
  │               ▼                                                  │
  │        ┌─────────────┐   PCIe 4.0 x8                              │
  │        │ ConnectX-5  │◄──────────────► host DRAM (pinned/registered)
  │        │   VF 0..7   │                        ▲                   │
  │        └──────┬──────┘                        │ cudaMemcpy (no GDR
  │               │ 100 GbE                       │  on GeForce)      │
  │               │                        ┌──────┴──────┐            │
  │               │                        │  GPU VRAM   │            │
  │               │                        └─────────────┘            │
  └───────────────┼─────────────────────────────────────────────────┘
                  ▼
          RoCEv2 (UDP:4791) on VLAN 400, PCP 3, PFC-lossless, ECN-marked
```

**On GDR-capable hardware** (RTX PRO / datacenter GPUs) the dashed host-DRAM hop disappears: the NIC DMAs straight into VRAM via the PCIe root complex, saving ~8–15 µs and one full memory-bandwidth round trip per message. Nodes are labeled `nexus.io/gpudirect-rdma=true|false`, and NCCL is configured per-pool accordingly.

### L2.4 RoCEv2 lossless configuration contract

Every switch port and every NIC must agree, or RoCE degrades silently into pause storms.

| Parameter | Value | Set on |
|---|---|---|
| RoCE traffic class / DSCP | DSCP 26 → PCP 3 | NIC (`mlnx_qos -i <dev> --trust dscp`), switch ingress classification |
| PFC | Enabled **on priority 3 only** | Both ends of every link |
| PFC watchdog | Enabled, 200 ms | Switch |
| ECN (WRED) | Min 150 KB, Max 1.5 MB, prob 100 % on priority 3 | Switch egress queue |
| DCQCN | Enabled (`mlnx_qos`, `cnp_dscp=48`) | NIC |
| Buffer allocation | Dedicated lossless pool ≥ 3× BDP per port | Switch |
| MTU | 9000 (L2) / 9000 (RoCE MTU 4096) | Both |
| Congestion telemetry | `rx_pause`, `tx_pause`, `np_ecn_marked_roce_packets` scraped every 15 s | Phase 45 |

**Failure signature to watch for:** rising `tx_pause` counters plus falling application throughput = PFC head-of-line blocking. Remedy path documented in Phase 21 troubleshooting.

### L2.5 CNI datapath decisions

| Setting | Value | Reason |
|---|---|---|
| Routing mode | `native` (no encapsulation) | Removes 50-byte VXLAN header + encap/decap CPU cost; requires L3 reachability between PodCIDRs — which BGP-to-the-host gives us |
| `kubeProxyReplacement` | `true` | eBPF hash-map service lookup, O(1) vs iptables O(n) |
| `bpf.masquerade` | `true` | eBPF NAT instead of iptables |
| `bpf.hostLegacyRouting` | `false` | eBPF host routing — skips the host netfilter stack entirely |
| `enableBIGTCP` (IPv4+IPv6) | `true` | 192 KB GSO/GRO super-packets; measurably lowers CPU per Gb at 100 G |
| `loadBalancer.mode` | `dsr` | Direct Server Return — reply bypasses the LB node |
| `loadBalancer.algorithm` | `maglev` | Consistent hashing; connection stability across backend churn |
| `bandwidthManager.enabled` | `true` (EDT/BBR) | Fair pacing; avoids bufferbloat on shared links |
| `bgpControlPlane.enabled` | `true` | Advertise PodCIDR + LB VIPs to leaves |
| `hubble.enabled` | `true`, flows exported to Loki/Tempo | Flow-level observability without tcpdump |
| Datapath for RDMA pods | **Bypassed entirely** — Multus secondary interface | Cilium governs the default (control/TCP) interface; RDMA rides its own VF |

---

## L3 — Provisioning & OS Layer

### L3.1 Zero-touch provisioning flow

```
 [1] Node powers on (WoL magic packet from Tinkerbell, or PDU outlet toggle)
        │
 [2] NIC PXE ROM → DHCP on VLAN 100 → Smee (Tinkerbell DHCP/PXE) responds
        │           options: next-server, filename=ipxe.efi (UEFI) / undionly.kpxe
 [3] iPXE chainloads → HTTP GET  http://smee/auto.ipxe?mac=${net0/mac}
        │
 [4] Tinkerbell looks up the Hardware CR by MAC
        │   ├─ UNKNOWN MAC  → boot the DISCOVERY image (hardware inventory agent)
        │   │                 agent POSTs full inventory → creates a draft
        │   │                 Hardware CR → human/automated classification (Phase 07)
        │   └─ KNOWN MAC    → boot the Talos kernel+initramfs from Image Factory
        │                     with the schematic ID for this node's extensions
 [5] Talos boots into MAINTENANCE mode, no config, listens on :50000
        │
 [6] Tinkerbell workflow / talosctl applies the machine config for this node
        │   (rendered from Git by Argo CD → talos-config-controller)
        │
 [7] Talos installs itself to /dev/nvme1n1, reboots into the installed system
        │
 [8] Node joins the cluster (control-plane bootstrap OR worker join via kubeadm-free
        │   Talos join token from the Secrets store)
        │
 [9] NFD labels the node from real hardware; GPU Operator rolls the driver;
        │   Cilium starts BGP peering; Ceph OSDs prepared if archetype=storage
        │
[10] Node passes the readiness gate (Phase 13 validation job): B1–B4 smoke tests
        │   PASS → labels removed from `nexus.io/quarantine`, node enters the pool
        │   FAIL → node cordoned + alert
```

**Target: < 15 minutes, zero human touch, fully repeatable (Gate G2).**

### L3.2 Why Talos Linux

| Property | Consequence for NEXUS |
|---|---|
| No shell, no SSH, no package manager | Law IV is enforced by the OS itself; config drift is *impossible*, not just discouraged |
| Single declarative `machineconfig` YAML | The entire OS is one Git-managed artifact per node/group |
| gRPC API (`talosctl`) with mTLS | Automatable everything: upgrade, reboot, reset, logs, dmesg, `pcap` |
| Read-only squashfs root + ephemeral partition | Reboot restores a known-good state |
| System Extensions (via Image Factory) | NVIDIA driver + container toolkit, `mlx5` tools, `iscsi-tools`, `nvme-cli` baked into a signed image with a schematic ID |
| A/B image upgrades with automatic rollback | Safe fleet-wide kernel/driver upgrades (Phase 52) |
| KubeSpan / SideroLink (optional) | WireGuard mesh for nodes across sites — not used at M1–M4, kept for M5 |

**The trade-off (documented honestly):** debugging is different. There is no `ssh` + `strace`. You use `talosctl logs`, `talosctl dmesg`, `talosctl pcap`, and privileged debug pods with `hostPID`. Phase 08 includes a full debugging runbook to compensate.

### L3.3 Machine config structure (Git-managed)

```
talos/
├── secrets/                      # SOPS-encrypted; the cluster CA + tokens
│   └── secrets.enc.yaml
├── patches/
│   ├── _base.yaml                # every node: NTP, DNS, registry mirrors, kubelet flags
│   ├── archetype-control.yaml    # etcd on dedicated disk, control-plane scheduling off
│   ├── archetype-compute-gpu.yaml# NVIDIA ext, hugepages, CPU manager static, sysctls
│   ├── archetype-storage.yaml    # disk wipe policy, extra mounts, Ceph sysctls
│   ├── net-bond-lacp.yaml
│   ├── net-sriov-8vf.yaml
│   ├── kernel-perf.yaml          # isolcpus, nohz_full, iommu=pt, mitigations policy
│   └── node-overrides/
│       └── nx-c-r01-05.yaml      # only truly per-node facts (disk serials, static IP)
└── rendered/                     # CI-generated, never hand-edited
```

Key `machine.sysctls` and kernel args set on `compute-gpu` (full list and rationale in Phase 08 and Phase 48):

| Setting | Value | Why |
|---|---|---|
| `iommu` / `amd_iommu` | `on`, `iommu=pt` | SR-IOV requires IOMMU; passthrough mode removes DMA translation cost |
| `hugepagesz=1G hugepages=N` | sized per node | TLB pressure elimination for large allocations |
| `isolcpus=<rdma-poll-cores>` `nohz_full=` `rcu_nocbs=` | on 2–4 cores | Jitter-free cores for RDMA polling / MPI ranks |
| `transparent_hugepage` | `madvise` | `always` causes stalls in allocation-heavy trainers |
| `net.core.rmem_max/wmem_max` | 268435456 | 100 GbE TCP fallback path |
| `net.ipv4.tcp_congestion_control` | `bbr` | Better behavior on the fat, occasionally-lossy fabric |
| `vm.max_map_count` | 1048576 | Ray/JVM/Ceph need it |
| `kernel.numa_balancing` | `0` | We pin explicitly; autonuma fights the Topology Manager |
| `nvidia.NVreg_EnableGpuFirmware` / persistence mode | on | Avoids driver re-init latency |
| `mitigations` | **Documented decision** — default on; `off` only for an isolated, air-gapped pool with sign-off | 5–25 % on syscall-heavy work; never silently disabled |

---

## L4 — Cluster Substrate

### L4.1 Control plane

```
        ┌──────────── kube-vip / Cilium L2+BGP VIP: 10.200.0.10:6443 ────────────┐
        │                                                                         │
  ┌─────┴──────┐              ┌────────────┐              ┌────────────┐
  │ ctrl-01    │              │ ctrl-02    │              │ ctrl-03    │
  │ apiserver  │◄────raft────►│ apiserver  │◄────raft────►│ apiserver  │
  │ etcd (PLP  │              │ etcd       │              │ etcd       │
  │  NVMe)     │              │            │              │            │
  │ scheduler  │              │ scheduler  │              │ scheduler  │  (leader-elected)
  │ ctrl-mgr   │              │ ctrl-mgr   │              │ ctrl-mgr   │
  └────────────┘              └────────────┘              └────────────┘
     rack r01                     rack r02                    rack r03      ← anti-affinity
```

**etcd is the single most fragile component.** Its contract:

| Metric | Target | Alert |
|---|---|---|
| `etcd_disk_wal_fsync_duration_seconds` p99 | < 10 ms | > 25 ms = page |
| `etcd_disk_backend_commit_duration_seconds` p99 | < 25 ms | > 50 ms = page |
| `etcd_server_leader_changes_seen_total` | 0/hour | any = warn |
| DB size | < 4 GB (quota 8 GB) | > 6 GB = warn |
| Peer RTT | < 2 ms | > 10 ms = warn |

Hard requirements: dedicated enterprise NVMe with PLP; `--quota-backend-bytes=8589934592`; auto-compaction every 5 min; defrag weekly via CronJob; **separate etcd cluster for `events`** from M3 onward; hourly snapshot to object storage (Phase 28).

### L4.2 kubelet configuration contract (compute-gpu)

```yaml
cpuManagerPolicy: static
cpuManagerPolicyOptions:
  full-pcpus-only: "true"          # never split a physical core across containers
  distribute-cpus-across-numa: "false"
memoryManagerPolicy: Static
topologyManagerPolicy: single-numa-node    # ← Law III, enforced here
topologyManagerScope: pod                  # align the whole pod, not each container
reservedSystemCPUs: "0-1"                  # system + kubelet never steal a workload core
systemReserved:  { cpu: "1",   memory: "2Gi", ephemeral-storage: "10Gi" }
kubeReserved:    { cpu: "1",   memory: "2Gi", ephemeral-storage: "10Gi" }
evictionHard:    { memory.available: "1Gi", nodefs.available: "10%", imagefs.available: "10%" }
maxPods: 64                                 # low: these are fat workload nodes
serializeImagePulls: false
registryPullQPS: 20
nodeStatusUpdateFrequency: 10s
featureGates:
  DynamicResourceAllocation: true
  MemoryQoS: true
```

> **`topologyManagerPolicy: single-numa-node` is the single highest-value line in this file.** On a 2-NUMA node it is the difference between full and half memory bandwidth for the data-loading path.

---

## L5 — Resource Abstraction Layer

### L5.1 DRA — how GPUs actually get allocated

Classic device plugins expose a GPU as an opaque integer (`nvidia.com/gpu: 1`). That cannot express "24 GB, Ada-class, NVLink-paired, same PCIe root as `mlx5_0`, shared with 3 other pods." **DRA can.**

```
  ┌──────────────────────────── NODE ───────────────────────────┐
  │  nvidia-dra-driver-kubelet-plugin (DaemonSet)               │
  │        │ publishes                                          │
  │        ▼                                                    │
  │  ResourceSlice  (one per node, per driver)                  │
  │    devices:                                                 │
  │      - name: gpu-0                                          │
  │        attributes: {productName: "RTX 4090", cc: "8.9",      │
  │                     numaNode: 0, pcieRoot: "0000:00:01.1"}   │
  │        capacity:   {memory: 24Gi}                            │
  └──────────────────────────────┬──────────────────────────────┘
                                 │ scheduler reads all ResourceSlices
  ┌──────────────────────────────▼──────────────────────────────┐
  │  DeviceClass: nexus-gpu-24g                                 │
  │    selectors: device.attributes["gpu.nvidia.com"].memory     │
  │               >= 24Gi                                        │
  ├─────────────────────────────────────────────────────────────┤
  │  ResourceClaimTemplate: training-gpu                        │
  │    devices.requests:                                        │
  │      - name: gpu                                            │
  │        deviceClassName: nexus-gpu-24g                       │
  │        count: 1                                             │
  │        allocationMode: ExactCount                           │
  ├─────────────────────────────────────────────────────────────┤
  │  Pod.spec.resourceClaims: [{name: gpu, template: ...}]      │
  └─────────────────────────────────────────────────────────────┘
```

**Sharing modes NEXUS exposes** (Phase 18 defines one `DeviceClass` per mode):

| DeviceClass | Mechanism | Isolation | Use case | Overhead |
|---|---|---|---|---|
| `nexus-gpu-exclusive` | Whole device | Full | Training, benchmarking | 0 % |
| `nexus-gpu-mps-quarter` | MPS, 25 % thread quota + memory limit | Soft (shared address space, no fault isolation) | Many small inference replicas | ~2–4 % |
| `nexus-gpu-timeslice-4` | Driver time-slicing, 4 shares | None (context-switch thrash possible) | Notebooks, dev, CI | 5–15 % under contention |
| `nexus-gpu-mig-*` | MIG profiles | **Hardware** | Only on MIG-capable GPUs, if present | ~0 % |

**Critical honesty:** time-slicing does **not** partition VRAM. Four pods on a 24 GB card each see 24 GB and will OOM each other. NEXUS enforces per-pod VRAM budgets via `CUDA_MPS_PINNED_DEVICE_MEM_LIMIT` (MPS mode) and a Kyverno policy requiring an explicit `nexus.io/vram-request` annotation on any shared-GPU pod, reconciled by an admission webhook (Phase 18).

### L5.2 Node Feature Discovery label taxonomy

NFD + a custom NEXUS hardware-labeler DaemonSet produce:

```
nexus.io/archetype                  = compute-gpu | compute-cpu | storage | control | infra
nexus.io/rack                       = r01..r08
nexus.io/pdu                        = r01-pdu-a
nexus.io/leaf-domain                = r01            # network topology key for Kueue TAS
nexus.io/spine-domain               = pod-a
nexus.io/gpu.model                  = rtx-4090
nexus.io/gpu.count                  = "1"
nexus.io/gpu.vram-gb                = "24"
nexus.io/gpu.compute-capability     = "8.9"
nexus.io/gpu.nvlink                 = "false"
nexus.io/gpu.p2p                    = "false"
nexus.io/gpu.gpudirect-rdma         = "false"
nexus.io/nic.speed-gbps             = "100"
nexus.io/nic.rdma                   = "true"
nexus.io/nic.sriov-vfs              = "4"
nexus.io/numa.nodes                 = "1"
nexus.io/topology.aligned           = "true"         # GPU+NIC same NUMA/PCIe root
nexus.io/storage.tier0-gb           = "2000"
nexus.io/power.cap-watts            = "600"
nexus.io/pool                       = training | inference | general | ci | storage
nexus.io/quarantine                 = "true"         # set until readiness gate passes
```

Taints:
```
nexus.io/archetype=storage:NoSchedule           # only Ceph tolerates
nexus.io/archetype=infra:NoSchedule             # only platform services tolerate
nexus.io/quarantine=true:NoSchedule             # new/failed nodes
nvidia.com/gpu=present:NoSchedule               # CPU jobs never squat on GPU nodes
node.nexus.io/unhealthy=<reason>:NoExecute      # auto-remediation
```

---

## L6 — Storage Fabric

### L6.1 The five tiers

```
LATENCY   CAPACITY   ┌──────────────────────────────────────────────────────────┐
  ~80 µs    2–4 TB   │ T0  LOCAL NVMe SCRATCH                                   │
    │      /node     │     LVM LocalPV · generic ephemeral volumes · emptyDir   │
    │                │     NO replication · dies with the node · 7 GB/s         │
    │                │     ▸ dataset staging, shuffle, activation offload, /tmp │
    │                ├──────────────────────────────────────────────────────────┤
  ~150 µs   20–60 TB │ T1  REPLICATED FAST BLOCK                                │
    │                │     OpenEBS Mayastor (SPDK) over NVMe-oF/RDMA · 2-way    │
    │                │     ▸ databases, MLflow backend, etcd-adjacent state,    │
    │                │       high-IOPS RWO volumes                              │
    │                ├──────────────────────────────────────────────────────────┤
  ~700 µs   200 TB+  │ T2  DISTRIBUTED BULK  (Rook-Ceph)                        │
    │                │     RBD (RWO block) + CephFS (RWX POSIX)                 │
    │                │     3-way replica across RACK failure domains            │
    │                │     WAL/DB on enterprise PLP NVMe — MANDATORY            │
    │                │     ▸ home dirs, shared datasets, model artifacts        │
    │                ├──────────────────────────────────────────────────────────┤
  ~2–10 ms  500 TB+  │ T3  OBJECT / ARCHIVE                                     │
    │                │     Ceph RGW (S3) or MinIO · erasure-coded 6+3           │
    │                │     ▸ raw datasets, checkpoints, backups, model registry │
    ▼                ├──────────────────────────────────────────────────────────┤
  ~T0 when warm      │ T4  DISTRIBUTED CACHE  (JuiceFS or Alluxio)              │
                     │     POSIX/HDFS view of T3, blocks cached on T0 NVMe      │
                     │     ▸ THE tier that makes epoch-2..N read at local speed │
                     └──────────────────────────────────────────────────────────┘
```

### L6.2 Storage decision matrix (what users are told)

| I need... | StorageClass | Access | Durability | Speed |
|---|---|---|---|---|
| Temp space that dies with my pod | `nexus-scratch` (ephemeral) | RWO | **None** | ★★★★★ |
| A fast DB volume | `nexus-fast-block` (Mayastor) | RWO | 2 replicas | ★★★★☆ |
| A home directory | `nexus-home` (CephFS) | RWX | 3 replicas, rack-aware | ★★☆☆☆ |
| A shared dataset, read many times | `nexus-dataset` (JuiceFS over RGW) | RWX-RO | EC 6+3 | ★★★★☆ (after warm) |
| Checkpoints during training | S3 to RGW via `s3://nexus-ckpt/` | — | EC 6+3 | ★★★☆☆ |
| A model artifact for serving | OCI artifact in Harbor (ORAS) | — | Harbor replication | ★★★★☆ (Spegel P2P) |
| An archive I touch yearly | `s3://nexus-archive/` (HDD pool) | — | EC 8+3 | ★☆☆☆☆ |

### L6.3 Ceph CRUSH design (rack-aware)

```
root default
 └── datacenter dc1
      ├── rack r01 ── host nx-s-r01-01 ── osd.0 .. osd.9
      ├── rack r02 ── host nx-s-r02-01 ── osd.10 .. osd.19
      ├── rack r03 ── host nx-s-r03-01 ── osd.20 .. osd.29
      ├── rack r04 ── host nx-s-r04-01 ── ...
      └── ...

  replicated_rack_rule:  step chooseleaf firstn 0 type rack     ← 3 copies, 3 racks
  ec_rack_rule (6+3):    step choose indep 9 type rack          ← survives 3 rack losses
```

`min_size=2` on replicated pools (accept writes with 2 of 3 copies; **never** set `min_size=1` — that is how data is lost). Recovery throttles: `osd_max_backfills=1`, `osd_recovery_max_active=3`, `osd_recovery_sleep_ssd=0` — tuned in Phase 25 so a rebuild does not starve training I/O (R-18).

### L6.4 The data-loading pipeline (why T4 exists)

```
  Epoch 1:  GPU ◄── T0 NVMe ◄── [JuiceFS cache MISS] ◄── T3 RGW ◄── OSDs
            (network-bound, ~2–5 GB/s aggregate)

  Epoch 2+: GPU ◄── T0 NVMe ◄── [JuiceFS cache HIT]
            (local NVMe-bound, ~7 GB/s per node — 100 % GPU utilization)
```

Supporting mechanisms: a `dataset-warmer` Job that pre-populates the cache on the exact nodes a gang was placed on (Phase 27); `NVIDIA DALI` / `torchdata` for GPU-side decode; `pin_memory=True` + `num_workers = 4 × GPUs`; and a Kyverno mutation that injects the JuiceFS cache mount automatically for any pod with `nexus.io/dataset` annotation.

---

## L7 — Scheduling & Orchestration

### L7.1 The scheduling pipeline

```
  ┌─ user submits Job/RayCluster/TrainJob with a Kueue queue label ─┐
  │                                                                  │
  ▼
 [1] Kueue Workload created (suspended)
      │
 [2] ADMISSION: does the ClusterQueue have quota?
      │   ├─ nominal quota available            → admit
      │   ├─ cohort has idle borrowable quota   → admit (borrowed, preemptible)
      │   ├─ lower-priority workload occupying  → PREEMPT it, then admit
      │   └─ none of the above                  → stay queued (fair-share ordered)
      │
 [3] TOPOLOGY-AWARE ASSIGNMENT (Kueue TAS)
      │   find the tightest domain (leaf → spine → cluster) that fits all N pods
      │   annotate pods with the chosen domain
      │
 [4] GANG GATE (scheduler-plugins coscheduling / Kueue all-or-nothing)
      │   PodGroup minMember = N; nothing binds until all N can bind
      │
 [5] kube-scheduler per-pod: filter + score
      │   filters:  nodeSelector, taints, DRA ResourceClaim satisfiable,
      │             topology domain match, power budget headroom
      │   scores:   NodeResourcesFit(LeastAllocated for training /
      │             MostAllocated for inference bin-packing),
      │             InterPodAffinity, ImageLocality, custom NexusTopologyScore
      │
 [6] BIND → kubelet
      │   Topology Manager: single-numa-node hint convergence across
      │   CPU Manager + Memory Manager + DRA + device plugins
      │   ├─ hints converge  → admit pod, pin CPUs, allocate hugepages, inject devices
      │   └─ no convergence  → REJECT with TopologyAffinityError → reschedule
      │
 [7] RUNNING
      │   Descheduler watches for topology drift / node imbalance
      │   NPD + DCGM watch for failure → taint NoExecute → pods evicted
      │   Kueue observes failure → requeue the whole gang (with backoff)
```

### L7.2 Kueue resource model

```
                       ┌──────────── Cohort: "nexus-main" ────────────┐
                       │  (idle quota flows freely between queues)     │
  ┌────────────────────┼──────────────────────────────────────────────┼───────┐
  │ ClusterQueue: cq-research      │ ClusterQueue: cq-production      │ cq-ci │
  │  nominal: 32 GPU, 512 CPU      │  nominal: 40 GPU, 640 CPU        │ 0 GPU │
  │  borrowLimit: 48 GPU           │  borrowLimit: 24 GPU             │ 256CPU│
  │  lendingLimit: 32 GPU          │  lendingLimit: 8 GPU             │       │
  │  preemption:                   │  preemption:                     │       │
  │    withinCQ: LowerPriority     │    reclaimWithinCohort: Any      │       │
  │    reclaimWithinCohort: Any    │    borrowWithinCohort: never     │       │
  │  flavorFungibility: Borrow     │                                  │       │
  └────────────────────────────────┴──────────────────────────────────┴───────┘
            │                                 │
      LocalQueue(s) in                  LocalQueue(s) in
      ns: team-vision, team-nlp         ns: prod-serving

  ResourceFlavors (bind quota to real hardware):
    rf-gpu-4090-100g   : nodeLabels {gpu.model: rtx-4090, nic.speed-gbps: "100"}
    rf-gpu-3090-25g    : nodeLabels {gpu.model: rtx-3090, nic.speed-gbps: "25"}
    rf-cpu-only        : nodeLabels {archetype: compute-cpu}
```

**Why flavors matter (R-15):** a job requesting `rf-gpu-4090-100g` can never be placed on a mixed set of 3090s and 4090s. Homogeneity is a *quota-level* guarantee, not a hope.

### L7.3 Topology-Aware Scheduling (TAS)

```yaml
# The topology the scheduler reasons about
apiVersion: kueue.x-k8s.io/v1beta1
kind: Topology
metadata: { name: nexus-network }
spec:
  levels:
    - nodeLabel: nexus.io/spine-domain   # widest  (2 hops)
    - nodeLabel: nexus.io/leaf-domain    # tighter (1 hop)  ← preferred
    - nodeLabel: kubernetes.io/hostname  # tightest (0 hops)
```

A 16-pod job annotated `kueue.x-k8s.io/podset-preferred-topology: nexus.io/leaf-domain` will be packed into a single rack if 16 slots exist there, falling back to a spine domain, then to anywhere. **Measured impact: 8–14 % end-to-end training throughput at 16 nodes** versus random placement, because every allreduce packet stays inside one leaf's switching ASIC.

### L7.4 Failure & preemption semantics

| Event | Detection | Reaction | User impact |
|---|---|---|---|
| Node NotReady > 40 s | node-lifecycle-controller | Taint `NoExecute`, evict pods after `tolerationSeconds` | Gang fails → Kueue requeue |
| GPU XID 48/63/74/79 (ECC/fallen-off-bus) | DCGM health + NPD | Taint `node.nexus.io/unhealthy=gpu:NoExecute`, cordon, open incident | Job requeued to healthy nodes |
| Disk pressure / NVMe wear > 90 % | node-exporter + NPD | Taint `NoSchedule`, drain, alert | New pods avoid the node |
| Thermal throttle sustained > 5 min | DCGM `SM clock reduced` | Reduce power cap 10 %, alert; taint if persists | Slight slowdown, no failure |
| PSU/power loss (whole rack) | PDU + node absence | Ceph rack-aware CRUSH keeps data available; jobs on that rack requeue | ≤ 1 rack of jobs restart |
| Preemption by higher priority | Kueue | `SIGTERM` → `terminationGracePeriodSeconds: 120` | **Checkpoint hook fires**, job requeued with `preserveOnPreempt` |
| Job exceeds `activeDeadlineSeconds` | Job controller | Terminate, mark failed | Reported to owner |
| Repeated crash (backoff) | Kueue `backoffLimitCount` | Deactivate the workload, alert the owner | Requires human triage |

**The checkpoint contract:** any job wanting preemption safety declares `nexus.io/checkpoint-hook: /app/checkpoint.sh`; a mutating webhook injects a `preStop` hook and sets a 120 s grace period. Frameworks with native support (PyTorch `torch.distributed.checkpoint`, Ray Train, DeepSpeed) get a library shim in Phase 32.

---

## L8 — Distributed Compute Runtimes

### L8.1 Runtime selection guide

| If the workload is... | Use | Why |
|---|---|---|
| PyTorch DDP/FSDP training, fixed world size | **Kubeflow Trainer v2 (`TrainJob`)** | Canonical, gang-scheduled, `torchrun` rendezvous handled |
| Python, dynamic parallelism, RL, tuning, pipelines | **Ray (KubeRay)** | Actor model, elastic, object store, unified train+tune+serve |
| Tightly-coupled MPI (CFD, MD, HPL) | **MPI Operator + UCX over RDMA** | Real MPI semantics, PMIx, RDMA-native |
| SQL / DataFrame ETL at TB scale | **Spark on K8s** | Mature shuffle, catalyst optimizer |
| Pythonic array/DataFrame parallelism | **Dask** | Numpy/Pandas-native, low friction |
| LLM inference | **vLLM + KServe + LeaderWorkerSet** | PagedAttention, continuous batching, multi-node TP/PP grouping |
| Classic `sbatch` HPC users | **Slinky (Slurm on K8s)** | Familiar UX without a second physical cluster |
| A DAG of heterogeneous steps | **Argo Workflows** | K8s-native, artifact passing, retries |

### L8.2 Ray topology on NEXUS

```
  ┌────────────────────── RayCluster (KubeRay) ──────────────────────┐
  │                                                                   │
  │  HEAD POD (infra node, no GPU)                                    │
  │   ├─ GCS (Global Control Store) ──► Redis/Valkey on T1 Mayastor   │
  │   │     ↑ GCS fault tolerance: head can restart, cluster survives │
  │   ├─ Ray Dashboard  ──► exposed via Gateway API + Keycloak OIDC   │
  │   └─ Autoscaler ──► creates/destroys worker groups via K8s API    │
  │                                                                   │
  │  WORKER GROUP "gpu-4090"    (min 0, max 64)                       │
  │   ├─ raylet + object store (plasma, /dev/shm sized to 30 % RAM)   │
  │   ├─ DRA ResourceClaim → 1× RTX 4090                              │
  │   ├─ Multus RDMA VF for object transfer                           │
  │   └─ spill directory → T0 local NVMe                              │
  │                                                                   │
  │  WORKER GROUP "cpu-bulk"    (min 0, max 512)                      │
  └───────────────────────────────────────────────────────────────────┘
```

Critical settings: `RAY_object_store_memory` sized explicitly (never let it default to a fraction that collides with the container limit); object spilling to T0 NVMe; `RAY_grpc_enable_http_proxy=0`; and `--num-cpus` set from the *cgroup* limit, not the host's core count (a classic 100-node footgun).

### L8.3 Multi-node LLM inference (the "VRAM pooling" that actually works)

```
   Model: 70B params, bf16 = 140 GB  ·  Available: RTX 4090 = 24 GB each
   Required GPUs ≈ 140 GB / (24 GB × 0.85 usable) ≈ 7 → round to 8

   ┌──────────── LeaderWorkerSet (size=2 nodes × 4 GPUs) ────────────┐
   │  LEADER pod (node A)                 WORKER pod (node B)         │
   │   vLLM engine, TP=4  ─────RDMA──────► vLLM engine, TP=4          │
   │   pipeline stage 0                    pipeline stage 1           │
   │   layers 0..39                        layers 40..79              │
   │   ▲ HTTP :8000                                                   │
   └───┼──────────────────────────────────────────────────────────────┘
       │
   KServe InferenceService ──► Gateway API ──► KV-cache-aware router
                                                (routes a follow-up turn to the
                                                 replica already holding its prefix)
```

**Why TP within a node and PP across nodes:** tensor parallelism requires an allreduce *per layer* (huge message volume, latency-critical) so it must stay on the fastest link available; pipeline parallelism sends only the activation tensor at the stage boundary (small, latency-tolerant) so it survives a 100 GbE hop. Getting this backwards is the most common multi-node inference mistake.

### L8.4 NCCL configuration contract

| Variable | Value (RoCE, no GDR) | Value (GDR-capable) | Purpose |
|---|---|---|---|
| `NCCL_IB_HCA` | `mlx5_0` | `mlx5_0,mlx5_1` | Pin to the right HCA; never let it auto-pick the mgmt NIC |
| `NCCL_IB_GID_INDEX` | 3 (RoCEv2 IPv4) | 3 | Wrong GID = silent TCP fallback |
| `NCCL_IB_TC` | 106 (DSCP 26 ≪2) | 106 | Must match the switch's lossless class |
| `NCCL_IB_QPS_PER_CONNECTION` | 4 | 4 | ECMP entropy across leaf uplinks |
| `NCCL_NET_GDR_LEVEL` | `LOC` (disabled) | `PIX` | Enable GDR only where the hardware supports it |
| `NCCL_SOCKET_IFNAME` | `net1` (the VF) | `net1` | Bootstrap must not use the pod's default iface |
| `NCCL_ALGO` | auto (`Ring,Tree`) | auto | Let NCCL pick; override only with benchmark evidence |
| `NCCL_P2P_DISABLE` | `1` on 4090 | `0` | 4090 has no working P2P; forcing it wastes time |
| `NCCL_DEBUG` | `WARN` (prod) / `INFO` (bring-up) | same | `INFO` prints the chosen rings — essential in Phase 21 |
| `NCCL_CROSS_NIC` | 1 | 1 | Allows rings to cross NICs on multi-NIC nodes |

These are injected by a Kyverno policy based on the node's `nexus.io/gpu.gpudirect-rdma` label — **users never set them by hand** (Phase 21).

---

## L9 — Platform Services

| Service | Namespace | Purpose | HA | Data tier |
|---|---|---|---|---|
| Argo CD | `argocd` | GitOps reconciliation of everything | 2 replicas, HA Redis | T1 |
| Gitea (mirror) | `platform-git` | Local Git mirror so the cluster survives GitHub outages (R-13) | 2 replicas | T2 |
| Harbor | `platform-registry` | Image registry, vuln scan, cosign signing, proxy cache | 2 replicas | T3 (RGW) |
| Spegel | `kube-system` | P2P image mirror across nodes — one pull, N nodes | DaemonSet | node-local |
| Keycloak | `platform-identity` | OIDC for K8s, Argo, Grafana, Harbor, Backstage, Ray | 2 replicas | T1 (Postgres) |
| OpenBao (Vault) | `platform-secrets` | Dynamic secrets, PKI, transit encryption | 3 replicas, Raft | T1 |
| External Secrets Operator | `platform-secrets` | Syncs Vault → K8s Secrets | 2 replicas | — |
| cert-manager | `cert-manager` | Internal PKI + Let's Encrypt for external endpoints | 1 (leader-elected) | — |
| Backstage | `platform-portal` | Developer portal, golden-path templates, service catalog | 2 replicas | T1 (Postgres) |
| JupyterHub | `platform-notebooks` | Per-user notebook servers with DRA GPU claims | 1 hub + N singleuser | T2 (home on CephFS) |
| MLflow | `platform-mlops` | Experiment tracking + model registry | 2 replicas | T1 meta + T3 artifacts |
| Argo Workflows | `platform-workflows` | DAG execution | 2 replicas | T3 artifacts |
| Gateway API (Envoy) | `platform-gateway` | North-south ingress, TLS, OIDC auth | 3 replicas | — |

---

## L10 — Observability & Control Loops

### L10.1 The signal map

```
 METRICS                LOGS                TRACES              PROFILES        POWER
 ───────                ────                ──────              ────────        ─────
 node-exporter          kubelet/containerd  OpenTelemetry       Parca (eBPF)    Kepler
 cAdvisor               app stdout          Collector           pprof           PDU SNMP
 DCGM-exporter          Talos kernel        ├─ Ray traces       CUDA (Nsight)   nvidia-smi
 kube-state-metrics     audit log           ├─ vLLM spans                       IPMI/hwmon
 Cilium/Hubble          Ceph                └─ HTTP/gRPC
 Ceph mgr               switch syslog
 etcd                   PDU events
 Kueue                       │
 switch gNMI/SNMP            │
 smartctl/nvme-cli           │
     │                       │                    │                 │            │
     ▼                       ▼                    ▼                 ▼            ▼
 Prometheus (per-AZ)  ──► Alloy/Promtail ──►  Tempo          ──► Parca      ──► Prometheus
     │                       │                    │                 │
     └──► Mimir (long-term, 13 mo) ◄─────────────┘                 │
              │                                                     │
              ▼                                                     ▼
        ┌──────────────────────── GRAFANA ────────────────────────────┐
        │  Fleet Overview · GPU Fleet · Network/RoCE · Storage/Ceph    │
        │  Kueue Queues · Job Explorer · Power & Thermal · Cost        │
        │  Per-tenant showback · Benchmark trends · SLO burn-down      │
        └───────────────────────────────────────────────────────────────┘
```

### L10.2 The control loops (this is what "self-healing" means concretely)

| Loop | Sensor | Controller | Actuator | Period |
|---|---|---|---|---|
| **Desired-state** | Git commit | Argo CD | K8s API | 3 min / webhook |
| **Node health** | NPD + DCGM + smartctl | node-problem-detector + Medik8s NHC | Taint / cordon / reboot / reimage | 30 s |
| **Workload placement** | Pending workloads | Kueue + kube-scheduler | Pod binding | continuous |
| **Capacity** | Queue depth, node idle | Cluster autoscaler analog (power controller) | WoL power-on / PDU power-off | 5 min |
| **Storage health** | Ceph PG state | Rook operator + Ceph mgr | OSD recreate, rebalance | continuous |
| **Thermal** | Intake/exhaust/GPU temp | Custom power controller | `nvidia-smi -pl` cap adjust, taint | 60 s |
| **Cert rotation** | Expiry | cert-manager + Talos | Reissue, rolling restart | daily |
| **Image freshness** | Registry digest | Renovate + Argo Image Updater | PR → merge → sync | daily |
| **Performance regression** | Benchmark CI | Phase 49 harness | Block merge, open issue | per-PR + nightly |
| **Drift** | Live vs Git | Argo CD | Auto-sync + `selfHeal: true`, alert on manual change | 3 min |

### L10.3 Golden signals per subsystem

| Subsystem | Signal | Target | Alert threshold |
|---|---|---|---|
| Cluster | API server p99 request latency | < 300 ms | > 1 s for 5 min |
| Cluster | Scheduling latency p99 (pending→bound) | < 5 s | > 30 s |
| etcd | `wal_fsync` p99 | < 10 ms | > 25 ms |
| GPU | Fleet utilization (SM occupancy) | > 70 % | < 40 % for 1 h (waste) |
| GPU | XID errors | 0 | any critical XID |
| GPU | Temperature | < 83 °C | > 88 °C |
| Network | RoCE `tx_pause` rate | ~0 | > 100/s |
| Network | Link errors / CRC | 0 | any |
| Network | Fabric utilization (leaf uplink) | < 70 % | > 90 % for 10 min |
| Storage | Ceph PG state | `active+clean` | any `degraded` > 30 min |
| Storage | Ceph client p99 latency | < 20 ms | > 100 ms |
| Storage | NVMe wear-leveling | < 80 % | > 90 % |
| Scheduling | Queue wait p95 by priority class | per-SLO | 2× SLO |
| Power | Per-rack draw | < 80 % breaker | > 85 % |
| Platform | Argo CD out-of-sync apps | 0 | > 0 for 30 min |

---

## X1 — Security Architecture

### X1.1 Trust boundaries

```
  ┌─ INTERNET ─────────────────────────────────────────────────────────────┐
  │   only: Gateway API (443) · WireGuard admin VPN · outbound registry pull│
  └──────────────────────────────┬─────────────────────────────────────────┘
                                 │ TLS 1.3, OIDC-authenticated
  ┌──────────────────────────────▼─────────────────────────────────────────┐
  │  PLATFORM ZONE  (infra nodes)  — Argo, Harbor, Keycloak, Vault, Grafana │
  │  ─ no user workloads (tainted) ─ default-deny netpol ─ audited          │
  └──────────────────────────────┬─────────────────────────────────────────┘
                                 │ mTLS (SPIFFE-style Cilium identities)
  ┌──────────────────────────────▼─────────────────────────────────────────┐
  │  TENANT ZONES (one per Capsule Tenant)                                  │
  │   ns: team-a-*        ns: team-b-*        ns: prod-serving              │
  │   default-deny between tenants; explicit CiliumNetworkPolicy allow-lists│
  └──────────────────────────────┬─────────────────────────────────────────┘
                                 │
  ┌──────────────────────────────▼─────────────────────────────────────────┐
  │  DATA ZONE (storage nodes) — Ceph, per-tenant pools + RGW buckets       │
  │  reachable only from tenant namespaces holding the right CSI secret     │
  └────────────────────────────────────────────────────────────────────────┘
  ┌────────────────────────────────────────────────────────────────────────┐
  │  MANAGEMENT ZONE (VLAN 100) — PXE, PDU, PiKVM, Talos API               │
  │  NOT routable from tenant zones. Admin VPN + jump host only.            │
  └────────────────────────────────────────────────────────────────────────┘
```

### X1.2 Controls

| Domain | Control | Implementation |
|---|---|---|
| **Identity** | SSO everywhere, no static creds | Keycloak OIDC → K8s `--oidc-issuer-url`; groups map to RBAC |
| **Node identity** | mTLS with per-node certs | Talos PKI; certs rotate automatically |
| **Workload identity** | SPIFFE-like, cryptographic | Cilium identities + optional SPIRE for cross-service auth |
| **Network** | Default-deny, L3/L4/L7 | `CiliumClusterwideNetworkPolicy` baseline deny + per-namespace allow |
| **Admission** | Signed images only | Kyverno `verifyImages` with cosign keyless (Fulcio/Rekor) or a local key |
| **Pod security** | Restricted PSA baseline | `pod-security.kubernetes.io/enforce=restricted` on all tenant namespaces |
| **Privileged exceptions** | Exactly 6, each with an ADR | GPU Operator, Cilium, Rook OSD, SR-IOV DP, NPD, Spegel — audited quarterly |
| **Secrets** | Never in Git, never plaintext | Vault + ESO; SOPS+age for the bootstrap bundle only |
| **Supply chain** | SBOM + scan + sign | Trivy Operator, Syft SBOM attached as OCI attestation, cosign |
| **Runtime** | eBPF detection + enforcement | Tetragon policies: block `ptrace` cross-container, unexpected `execve` in prod ns |
| **Audit** | Full API audit log | Talos-managed apiserver audit policy → Loki, 400-day retention |
| **Data at rest** | Encrypted | Talos `STATE`/`EPHEMERAL` LUKS2 (TPM-sealed where available); Ceph `dmcrypt` OSDs |
| **Data in transit** | Encrypted | TLS everywhere; **RDMA traffic is NOT encrypted** — accepted risk, mitigated by physical/VLAN isolation (documented in ADR-023) |
| **Backup integrity** | Immutable, tested | Velero → object lock bucket; quarterly restore drill (G10) |

---

## X2 — Failure Domains & Fault Model

### X2.1 Domain hierarchy

| Domain | Population | Simultaneous-loss tolerance | Enforced by |
|---|---|---|---|
| Device (GPU/NVMe/NIC) | 1 | Any | DCGM/SMART detection → taint |
| Node | 1 | Any 5 % of the fleet | Ceph replication, Kueue requeue |
| PDU / power circuit | ~6 nodes | 1 | Nodes dual-corded across PDU A and B where PSU allows |
| Rack | ~12 nodes | **1 full rack** | Ceph CRUSH rack rule, control-plane anti-affinity, TAS spread for HA services |
| Leaf switch | ~12 nodes | 1 of a MLAG pair | MLAG + dual uplinks |
| Spine switch | fabric | 1 of N | ECMP |
| Room / site | all | 0 (single site at M1–M4) | Off-site backup only; M5 adds a second site |

### X2.2 Fault injection catalog (Phase 53 runs these on a schedule)

| # | Fault | Expected behavior | Max acceptable impact |
|---|---|---|---|
| F1 | Kill a random compute node (`talosctl reset`) | Pods evicted, gang requeued, Ceph self-heals | 1 job restarts; < 90 s |
| F2 | Kill 1 control-plane node | API VIP fails over; etcd keeps quorum | < 30 s API blip; **0 running pods affected** |
| F3 | Kill 2 control-plane nodes (of 3) | Quorum lost, API read-only/down | Running pods keep running; documented recovery |
| F4 | Power off a full rack (PDU) | Ceph degraded-but-available; jobs on that rack requeue | No data loss; < 5 min recovery |
| F5 | Sever one leaf uplink | ECMP reroutes | < 1 s convergence, ~50 % BW for that rack |
| F6 | Saturate the fabric with an incast pattern | ECN throttles, PFC does not storm | RoCE stays up; no PFC deadlock |
| F7 | Fill a node's T0 NVMe to 100 % | Eviction manager evicts the offender | Node stays Ready |
| F8 | Corrupt an OSD | Ceph scrub detects, repairs from replica | 0 data loss |
| F9 | Inject GPU XID 79 (fallen off bus) | DCGM detects, node tainted, job requeued | < 2 min |
| F10 | Delete a random platform namespace | Argo CD self-heals from Git | < 3 min |
| F11 | Expire a certificate | cert-manager/Talos rotates | 0 impact |
| F12 | Network-partition etcd (one member) | Member rejoins, no split-brain | 0 impact |
| F13 | Registry (Harbor) down | Spegel serves cached layers | New images fail; running/cached unaffected |
| F14 | Git remote unreachable | Argo CD uses the last synced state; Gitea mirror serves | 0 impact for 24 h |

---

## X3 — Naming, Labeling & Namespace Taxonomy

### X3.1 Node naming

```
  nx  -  c  -  r01  -  05
  │      │     │       └─ slot within rack (01–36)
  │      │     └───────── rack id
  │      └─────────────── archetype: c=compute-gpu, u=compute-cpu, s=storage,
  │                                   m=control(master), i=infra
  └────────────────────── cluster prefix
```

### X3.2 Namespace convention

| Prefix | Owner | Examples |
|---|---|---|
| `kube-*` | Kubernetes itself | `kube-system` |
| `platform-*` | Platform team, tainted to infra nodes | `platform-registry`, `platform-secrets` |
| `obs-*` | Observability | `obs-metrics`, `obs-logs`, `obs-traces` |
| `storage-*` | Storage fabric | `storage-ceph`, `storage-mayastor` |
| `sched-*` | Scheduling layer | `sched-kueue`, `sched-plugins` |
| `team-<name>-<env>` | Tenants | `team-vision-dev`, `team-nlp-prod` |
| `prod-*` | Production services | `prod-serving` |
| `ci-*` | Build/test | `ci-runners` |

### X3.3 Mandatory labels on every workload (Kyverno-enforced)

```yaml
nexus.io/owner: "<keycloak-username>"      # who to page
nexus.io/team: "<tenant>"                  # for showback
nexus.io/cost-center: "<code>"
nexus.io/workload-class: training|inference|interactive|batch|service
nexus.io/checkpointable: "true"|"false"    # determines preemption eligibility
app.kubernetes.io/name / version / part-of # standard recommended labels
```

---

## X4 — Repository Architecture

```
cluster/
├── ULTIMATE-PLAN.md
├── ARCHITECTURE.md
├── phases/                       # 57 executable work orders
│   ├── README.md                 # execution protocol — READ FIRST
│   └── PHASE-00 … PHASE-56.md
│
├── inventory/                    # source of truth for physical reality
│   ├── schema/nodespec.schema.json
│   ├── nodes/*.yaml
│   ├── racks.yaml
│   ├── network/{ip-plan,vlans,switch-ports}.yaml
│   └── power/{pdu-map,circuits}.yaml
│
├── talos/                        # L3: OS layer
│   ├── secrets/  patches/  rendered/
│   └── Taskfile.yaml
│
├── bootstrap/                    # things that must exist before GitOps
│   ├── tinkerbell/  dnsmasq/  step-ca/  argocd-install/
│
├── clusters/                     # L4+: GitOps root
│   └── nexus-prod/
│       ├── root-app.yaml         # app-of-apps entry point
│       ├── infra/                # cilium, coredns, cert-manager, kyverno, capsule
│       ├── acceleration/         # gpu-operator, dra, sriov, network-operator, nfd
│       ├── storage/              # rook-ceph, mayastor, localpv, juicefs, velero
│       ├── scheduling/           # kueue, coscheduling, keda, descheduler
│       ├── runtimes/             # kuberay, trainer, mpi-operator, spark, kserve
│       ├── platform/             # argocd, harbor, keycloak, vault, backstage, mlflow
│       ├── observability/        # prometheus, mimir, grafana, loki, tempo, parca, kepler
│       └── tenants/              # per-tenant Capsule + Kueue + namespace definitions
│
├── charts/                       # first-party Helm charts
├── images/                       # Dockerfiles for base images (cuda, ray, vllm, bench)
├── benchmarks/                   # B1–B12 harness + historical results
│   ├── suites/  runners/  baselines/  reports/
├── chaos/                        # F1–F14 experiment definitions
├── runbooks/                     # one per alert
├── policies/                     # kyverno, cilium netpol, tetragon
├── docs/                         # user-facing: how to submit a job, quotas, examples
├── evidence/                     # per-phase acceptance artifacts (Gate proof)
│   └── phase-XX/
└── tools/                        # CLI helpers, scripts, Taskfile
```

---

## W — Reference Walkthroughs

### W1 — Life of a 16-GPU distributed training job

```
 t+0.0s   User: `nexus submit train.yaml`  (Backstage template or kubectl)
          → creates a Kubeflow `TrainJob`, labelled queue=lq-team-nlp

 t+0.2s   Kyverno mutating webhook:
            + injects NCCL env from node-pool labels
            + injects JuiceFS dataset mount (annotation nexus.io/dataset=imagenet)
            + injects checkpoint preStop hook, grace=120s
            + validates: image is cosign-signed, owner label present, VRAM declared

 t+0.5s   Kueue creates a suspended Workload. cq-research has 12/32 GPUs used.
          16 requested > 20 free? No → nominal quota suffices → ADMIT.

 t+0.8s   Kueue TAS: needs 16 GPUs in the tightest domain.
            leaf-domain r03 has 12 free → insufficient
            leaf-domain r04 has 10 free → insufficient
            spine-domain pod-a (r03+r04) has 22 free → CHOSEN
          Pods annotated with the assigned topology domain.

 t+1.0s   Coscheduling PodGroup (minMember=16) created. Job unsuspended.

 t+1.2s   kube-scheduler filters/scores 16 pods.
            DRA: each pod's ResourceClaim (DeviceClass nexus-gpu-24g) matched
                 against ResourceSlices → concrete GPU UUIDs reserved
            All 16 bindable → gang permitted → BIND

 t+2.0s   kubelet on each node:
            Topology Manager: CPU(8 exclusive) + memory + GPU + SR-IOV VF hints
                              all converge on NUMA node 0 → ADMIT
            CDI injects /dev/nvidia*, /dev/infiniband/uverbs0
            Multus attaches net1 = SR-IOV VF on VLAN 400

 t+3.0s   Image pull: Spegel finds the layers on a peer node in the same rack
                      → 40 GB CUDA image pulls at ~5 GB/s from a neighbour,
                        not 16× over the internet

 t+18s    Containers start. `torchrun` rendezvous via the Trainer-provided
          headless service. NCCL initializes:
            NCCL INFO NET/IB : Using [0]mlx5_0:1/RoCE
            NCCL INFO Ring 00 : 0 -> 1 -> 2 ... -> 15 -> 0
          Rings are built inside the spine domain — no cross-pod hops.

 t+25s    Dataset: JuiceFS cache warm on these nodes (warmer job ran at admission)
          → first epoch reads from local NVMe, GPU util hits 94 % immediately

 t+1h13m  Node nx-c-r04-07 throws XID 79 (GPU fell off the bus).
            DCGM → NPD → taint node.nexus.io/unhealthy=gpu:NoExecute
            Pod evicted → PodGroup broken → all 16 pods terminated (gang semantics)
            preStop hook fires on the survivors → torch.distributed.checkpoint saved to
            s3://nexus-ckpt/job-abc/step-4200
            Kueue: workload requeued with backoff, retry 1/3

 t+1h15m  Re-admitted on 16 healthy GPUs; job resumes from step 4200.
          Total lost work: 2 minutes.

 t+9h     Job completes. Artifacts → MLflow. GPU-seconds → showback report.
          Node nx-c-r04-07 remains cordoned; an incident is open with the XID trace.
```

### W2 — Life of an inference request (70B model, 8 GPUs, 2 nodes)

```
 client → Gateway API (TLS, OIDC) → KServe InferenceService
        → KV-cache-aware router: hashes the prompt prefix, finds replica-2
          already holding 3,100 tokens of that conversation's KV cache
        → vLLM LEADER pod (node A, TP=4)
             ├─ scheduler adds the request to the continuous batch
             ├─ prefill: layers 0-39 on node A
             ├─ activation tensor (≈ 2 MB) → RDMA → node B  [~180 µs]
             ├─ prefill: layers 40-79 on node B
             └─ decode loop: token-by-token, 1 RDMA hop per token per stage
        → SSE stream back to the client

  p50 TTFT  ~180 ms   ·  p99 TTFT ~430 ms  ·  ~38 tok/s/stream
  The 2 MB pipeline hop costs ~0.2 ms of a ~26 ms/token budget = < 1 % overhead.
  This is why pipeline parallelism crosses nodes and tensor parallelism does not.
```

### W3 — Life of a node failure (power supply death)

```
 t+0      PSU fails. Node stops. PDU reports outlet current → 0 A.
 t+10s    kubelet stops reporting. `NodeLease` not renewed.
 t+40s    node-lifecycle-controller: NodeReady=Unknown → taint node.kubernetes.io/
          unreachable:NoExecute
 t+45s    Pods with tolerationSeconds=30 begin eviction.
            - Training gang → Kueue requeues the whole workload
            - Ray worker    → Ray autoscaler replaces it; the job's lineage
                              reconstruction re-executes lost tasks
            - Ceph OSDs (if storage node) → marked down at 600 s (osd_down_out_interval
                              deliberately long to avoid rebalancing on a reboot)
 t+60s    Alert: NodeDown + PDUOutletZeroCurrent correlated → the runbook says
          "hardware failure, not a soft crash"
 t+2min   Auto-remediation attempts 1 PDU power-cycle. No current draw returns.
          → escalate: node marked `nexus.io/status=hardware-failed`, ticket created,
            node removed from all Kueue ResourceFlavor capacity counts
 t+10min  Ceph (if applicable) begins backfill, throttled to 1 concurrent backfill so
          training I/O is unaffected.
 Next day A human swaps the PSU. Node PXE-boots, re-provisions in 12 minutes,
          passes the readiness gate, rejoins the pool automatically.
```

---

## C — Component Catalog

| # | Component | Layer | Namespace | Criticality | Failure impact |
|---|---|---|---|---|---|
| 1 | Talos Linux | L3 | — | 🔴 Critical | Node unbootable |
| 2 | Tinkerbell | L3 | `bootstrap` | 🟡 Low | Cannot provision new nodes |
| 3 | etcd | L4 | `kube-system` | 🔴 Critical | Cluster state loss |
| 4 | kube-apiserver | L4 | `kube-system` | 🔴 Critical | No new scheduling |
| 5 | Cilium | L4 | `kube-system` | 🔴 Critical | Pod networking down |
| 6 | CoreDNS | L4 | `kube-system` | 🔴 Critical | Service discovery down |
| 7 | containerd | L4 | — | 🔴 Critical | No containers on that node |
| 8 | NVIDIA GPU Operator | L5 | `gpu-operator` | 🟠 High | GPUs unusable |
| 9 | NVIDIA DRA driver | L5 | `gpu-operator` | 🟠 High | GPU claims unsatisfiable |
| 10 | Node Feature Discovery | L5 | `nfd` | 🟡 Low | Stale labels |
| 11 | Multus + SR-IOV DP | L5 | `network-operator` | 🟠 High | No RDMA in pods |
| 12 | RDMA shared DP | L5 | `network-operator` | 🟠 High | No verbs device in pods |
| 13 | Rook-Ceph | L6 | `storage-ceph` | 🔴 Critical | PVC unavailability |
| 14 | OpenEBS Mayastor | L6 | `storage-mayastor` | 🟠 High | Fast block PVCs down |
| 15 | LocalPV / local-path | L6 | `storage-local` | 🟡 Low | No new scratch volumes |
| 16 | JuiceFS | L6 | `storage-cache` | 🟡 Medium | Slow dataset reads (still correct) |
| 17 | Velero | L6 | `storage-backup` | 🟡 Medium | No new backups |
| 18 | Kueue | L7 | `sched-kueue` | 🟠 High | No new job admission |
| 19 | coscheduling plugin | L7 | `sched-plugins` | 🟠 High | Gangs may deadlock |
| 20 | KEDA | L7 | `sched-keda` | 🟡 Low | No event autoscaling |
| 21 | Descheduler | L7 | `sched-kueue` | 🟢 Info | Gradual topology drift |
| 22 | KubeRay | L8 | `runtimes-ray` | 🟠 High | Ray clusters unmanaged |
| 23 | Kubeflow Trainer | L8 | `runtimes-training` | 🟠 High | No new TrainJobs |
| 24 | MPI Operator | L8 | `runtimes-mpi` | 🟡 Medium | No new MPIJobs |
| 25 | KServe + vLLM | L8 | `prod-serving` | 🔴 Critical (if prod) | Inference outage |
| 26 | LeaderWorkerSet | L8 | `runtimes-lws` | 🟠 High | Multi-node inference broken |
| 27 | Argo CD | L9 | `argocd` | 🟠 High | No reconciliation (cluster keeps running) |
| 28 | Harbor | L9 | `platform-registry` | 🟠 High | No image pulls (Spegel mitigates) |
| 29 | Spegel | L9 | `kube-system` | 🟡 Medium | Slow pulls |
| 30 | Keycloak | L9 | `platform-identity` | 🟠 High | No new logins |
| 31 | OpenBao/Vault | L9 | `platform-secrets` | 🟠 High | No new secret issuance |
| 32 | cert-manager | L9 | `cert-manager` | 🟠 High | Certs expire eventually |
| 33 | Backstage | L9 | `platform-portal` | 🟢 Low | Self-service down; kubectl works |
| 34 | JupyterHub | L9 | `platform-notebooks` | 🟡 Medium | No new notebooks |
| 35 | MLflow | L9 | `platform-mlops` | 🟡 Medium | No experiment logging |
| 36 | Argo Workflows | L9 | `platform-workflows` | 🟡 Medium | Pipelines stall |
| 37 | Prometheus/Mimir | L10 | `obs-metrics` | 🟠 High | Blind |
| 38 | Grafana | L10 | `obs-metrics` | 🟡 Medium | No dashboards |
| 39 | Loki | L10 | `obs-logs` | 🟡 Medium | No log search |
| 40 | Tempo/OTel | L10 | `obs-traces` | 🟢 Low | No traces |
| 41 | DCGM exporter | L10 | `gpu-operator` | 🟠 High | No GPU health signal |
| 42 | NPD + Medik8s NHC | L10 | `obs-health` | 🟠 High | No auto-remediation |
| 43 | Parca | L10 | `obs-profiles` | 🟢 Low | No continuous profiling |
| 44 | Kepler | L10 | `obs-power` | 🟢 Low | No power attribution |
| 45 | Kyverno | X1 | `kyverno` | 🟠 High | Policy unenforced (fail-closed configured) |
| 46 | Tetragon | X1 | `kube-system` | 🟡 Medium | No runtime enforcement |
| 47 | Capsule | X1 | `capsule-system` | 🟡 Medium | Tenant boundaries unmanaged |
| 48 | Gateway API / Envoy | L9 | `platform-gateway` | 🔴 Critical | North-south traffic down |

---

## ADR — Architecture Decision Records

> Format: **decision** · *status* · context → decision → consequences.
> Superseded ADRs are never deleted, only marked.

| ID | Decision | Status | Key consequence |
|---|---|---|---|
| **ADR-001** | Kubernetes is the single substrate; Slurm is an overlay, not a peer | Accepted | One pool, one scheduler of record. Slurm users get UX, not a hardware carve-out. |
| **ADR-002** | Talos Linux, not a general-purpose distro | Accepted | Config drift becomes structurally impossible. Debugging workflow must change (runbook in Phase 08). |
| **ADR-003** | Bare metal + containers; no hypervisor anywhere | Accepted | 0 % virtualization tax. Weaker isolation than VMs — acceptable for a trusted-org tenancy model. |
| **ADR-004** | Cilium in native routing mode with BGP-to-the-host | Accepted | No encapsulation tax; requires an L3 fabric and switch BGP config. Rules out flat-L2 shortcuts. |
| **ADR-005** | RDMA is mandatory for the training pool | Accepted | Sets a hardware floor (25 GbE + RoCE NIC). Nodes that fail B4 cannot join the training pool. |
| **ADR-006** | RoCEv2 over Ethernet, not InfiniBand | Accepted (revisit at M4) | One fabric, cheaper switches, but requires careful PFC/ECN engineering. IB remains a documented alternative if RoCE tuning proves fragile. |
| **ADR-007** | DRA (not just device plugins) for GPU allocation | Accepted | Enables attribute-based selection and sharing. Requires K8s ≥ 1.34 and the NVIDIA DRA driver. |
| **ADR-008** | Time-slicing/MPS for GPU sharing; no vGPU | Accepted | Only option on consumer GPUs. VRAM isolation must be enforced in policy, not hardware — documented risk. |
| **ADR-009** | Topology Manager `single-numa-node`, scope `pod` | Accepted | Some pods will be rejected with `TopologyAffinityError`. That is correct behavior, not a bug. |
| **ADR-010** | Five storage tiers, not one | Accepted | More concepts for users, but avoids the "Ceph is slow" failure mode (R-04). Documented decision matrix (L6.2). |
| **ADR-011** | Ceph WAL/DB on enterprise PLP NVMe is mandatory | Accepted | Hard procurement requirement. Without it, Tier 2 is unusable for anything latency-sensitive. |
| **ADR-012** | Storage runs on dedicated nodes, never colocated with GPUs | Accepted | Costs ~6 nodes. Prevents Ceph recovery from stealing GPU-node CPU/PCIe/network during a rebuild. |
| **ADR-013** | Kueue is the batch admission layer | Accepted | Quota/fairness/preemption come free; adds a suspension step to every job's lifecycle. |
| **ADR-014** | `scheduler-plugins` coscheduling, not Volcano, for gang | Accepted (revisit) | Keeps the stock kube-scheduler (smaller operational surface, Law X). Volcano is the documented fallback if gang semantics prove insufficient. |
| **ADR-015** | Argo CD app-of-apps, not Flux | Accepted | Better UI/UX for a small team; Flux is a valid alternative — the repo structure is portable between them. |
| **ADR-016** | Harbor + Spegel, not direct upstream pulls | Accepted | Survives internet outages; a 40 GB CUDA image is pulled once per rack, not once per node. |
| **ADR-017** | Consumer GPUs are supported but pool-segregated by exact model | Accepted | Heterogeneity is embraced at the fleet level and forbidden within a single gang (R-15). |
| **ADR-018** | Switched PDUs are mandatory infrastructure, not a nice-to-have | Accepted | Solves remote hard-power-cycle on BMC-less consumer boards (R-05). |
| **ADR-019** | Power is a schedulable resource | Accepted | The scheduler can refuse a job on breaker grounds. Requires per-rack power telemetry. |
| **ADR-020** | Compute nodes are NOT on UPS | Accepted | Saves a large UPS. Requires that all long jobs checkpoint (enforced by policy for `training` class). |
| **ADR-021** | Argo Workflows for DAGs; Flyte deferred | Accepted (revisit at M3) | Simpler, K8s-native. Flyte's typed/cached DAGs are compelling if pipeline complexity grows. |
| **ADR-022** | Ray is the default for Python distributed work | Accepted | Best elasticity + fault tolerance story. Kubeflow Trainer remains for canonical `torchrun` jobs. |
| **ADR-023** | RDMA traffic is not encrypted | Accepted (risk) | Encryption would cost line rate. Mitigated by VLAN isolation, physical security, and a trusted-org tenancy model. Revisit if the trust model changes. |
| **ADR-024** | Single Kubernetes cluster up to ~250 nodes | Accepted | Avoids federation complexity. M5 multi-cluster plan exists if etcd or blast radius forces it. |
| **ADR-025** | Every performance claim requires a committed benchmark artifact | Accepted | Slows down merges; makes performance a defended property rather than a hope (Law VIII). |
| **ADR-026** | Kyverno fails **closed** for image signature verification | Accepted | A Kyverno outage blocks new pods. Chosen deliberately: an unsigned image running is worse than a stalled deploy. HA Kyverno (3 replicas) is therefore mandatory. |
| **ADR-027** | `mitigations=off` is never a default | Accepted | Speculative-execution mitigations stay on. An isolated pool may disable them with explicit written sign-off recorded in `evidence/`. |

---

## P — Capacity & Performance Models

### P1 — GPU capacity planning

```
Effective_GPU_hours = N_gpus × 8760 × Availability × Utilization

  Availability  = (1 − MTTR/MTBF) × (1 − planned_maintenance_fraction)
                ≈ 0.97 for consumer hardware with auto-remediation
  Utilization   = fraction of available GPU-hours actually consumed by jobs
                  target ≥ 0.70 (measured as DCGM SM occupancy, not "allocated")
```

**Allocated ≠ utilized.** A notebook holding a GPU at 3 % SM occupancy is the #1 source of waste. Countermeasures: idle-culling in JupyterHub (30 min), `nexus-gpu-timeslice-4` DeviceClass for interactive work, and a weekly "GPU hoarders" showback report.

### P2 — Network capacity model

```
Bisection_BW      = N_spine_ports × 100 Gb/s
Per-node fair share = Bisection_BW / N_nodes

At M4: 4 spines × 32 ports × 100 G = 12.8 Tb/s bisection
        ÷ 100 nodes                = 128 Gb/s per node  (≥ node NIC → non-blocking ✅)

Oversubscription per leaf = (hosts × host_speed) / (uplinks × uplink_speed)
  general pool: (12 × 100) / (4 × 100) = 3:1
  training pool: (8 × 100) / (8 × 100) = 1:1
```

### P3 — Distributed training time model

```
T_step = max(T_compute, T_comm_overlapped) + T_comm_exposed + T_dataload_exposed

T_compute      = 6 × P × B / (N × FLOPS_eff)          [fwd+bwd ≈ 6·params·tokens]
T_allreduce    = 2 × (N−1)/N × G / BW_bus              [ring]
T_comm_exposed = max(0, T_allreduce − T_compute_overlap_window)

Scaling_efficiency(N) = T_step(1) / (N × T_step(N)) × N = T_step(1)/T_step(N)
```

**Design lever ranking (highest first):** bucket size & overlap → interconnect bandwidth → gradient compression/precision (bf16→fp8) → topology-aware placement → NCCL algorithm → CPU/dataloader.

### P4 — Storage capacity model

```
Raw_needed = Logical × Replication_overhead × (1 + Growth) / Target_fullness

  Replicated 3×  : overhead 3.00
  EC 6+3         : overhead 1.50
  Target_fullness: 0.70  (Ceph degrades sharply past ~80 %; NEVER exceed 85 %)

Example: 200 TB logical on EC 6+3, 30 % growth headroom
  200 × 1.5 × 1.3 / 0.70 = 557 TB raw
```

### P5 — Power model

```
P_node   = P_idle + Σ(GPU_cap) × U_gpu + P_cpu_max × U_cpu
P_rack   = Σ P_node + P_switch
P_total  = Σ P_rack × PUE

Scheduler constraint (Phase 31):
  ∀ rack:  Σ (P_node_projected of scheduled pods) ≤ 0.80 × Breaker_capacity
```

---

## Reading Order

1. `ULTIMATE-PLAN.md` — vision, constraints, risks, economics
2. **This document** — component design
3. `phases/README.md` — execution protocol and conventions
4. `phases/PHASE-00.md` onward — the actual work

*Every phase file cross-references the section of this document it implements. If an implementation ever diverges from this architecture, the divergence must be recorded as a new ADR before the code is merged (Law II).*
