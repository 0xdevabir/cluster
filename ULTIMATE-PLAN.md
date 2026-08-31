# ULTIMATE PLAN — Project **NEXUS**

### A Private, Bare-Metal, Composable Supercomputer Built From Ordinary GPU PCs

> **Codename:** NEXUS
> **Class:** Private HPC/AI Cloud — Tier-1 performance on Tier-3 hardware
> **Scale target:** 8 nodes (Day 1) → 24 (Quarter 2) → 48 (Quarter 3) → **100+ nodes** (Year 1)
> **Prime directive:** *Never pay a performance tax for the privilege of being distributed.*

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [What We Are Building — And What We Are Not](#2-what-we-are-building--and-what-we-are-not)
3. [The Ten Laws of NEXUS](#3-the-ten-laws-of-nexus)
4. [Physics & Honest Constraints](#4-physics--honest-constraints-read-this-twice)
5. [Target Capability Model](#5-target-capability-model)
6. [Reference Hardware Specification](#6-reference-hardware-specification)
7. [Technology Stack Selection](#7-technology-stack-selection)
8. [The Performance Budget](#8-the-performance-budget)
9. [Scaling Model](#9-scaling-model-8--100-nodes)
10. [Multi-Tenancy & Organizational Model](#10-multi-tenancy--organizational-model)
11. [Cost Model](#11-cost-model)
12. [Risk Register](#12-risk-register)
13. [Success Criteria & Acceptance Gates](#13-success-criteria--acceptance-gates)
14. [Roadmap & Phase Map](#14-roadmap--phase-map)
15. [Glossary](#15-glossary)

---

## 1. Executive Summary

NEXUS turns a heterogeneous fleet of commodity GPU workstations into a **single, centrally-managed, self-healing, composable computing substrate**. A user submits a declarative workload description — *"I need 64 CPU cores, 8 GPUs with ≥20 GB VRAM each, 4 TB of fast scratch, and RDMA between all ranks"* — and the platform finds, reserves, wires, isolates, monitors, and (on failure) rebuilds that allocation across whatever physical machines can satisfy it.

The system is built on four load-bearing ideas:

| # | Idea | Consequence |
|---|------|-------------|
| **1** | **Bare metal, never virtualized** | Containers on an immutable, API-driven Linux. Zero hypervisor tax. 99–100 % of native CPU/GPU throughput. |
| **2** | **The network *is* the computer** | RDMA (RoCEv2 or InfiniBand) on a non-blocking leaf-spine fabric. The interconnect is the single largest determinant of scaling efficiency, so it gets the largest share of the budget and the deepest engineering. |
| **3** | **Declarative everything, GitOps always** | Every byte of cluster state — machine configs, network policy, quotas, dashboards, drivers — is a versioned artifact reconciled by a controller. No SSH. No snowflakes. 100 nodes is as easy as 8. |
| **4** | **Topology is a first-class scheduling input** | The scheduler knows NUMA domains, PCIe root complexes, network rails, rack failure domains and cache locality. Placement is never random. |

**What this buys you:** measured **$0.18–$0.40 per GPU-hour** amortized against **$2.20–$4.00** for equivalent public-cloud GPU capacity, with zero egress fees, complete data sovereignty, and hardware you can profile down to the PCIe lane.

**What it costs you:** real electrical and thermal engineering, a serious interconnect investment, and the discipline to keep every change in Git.

---

## 2. What We Are Building — And What We Are Not

### ✅ We ARE building

- A **distributed resource pool** that dynamically composes CPU cores, GPUs, GPU fractions, local NVMe, distributed storage, and RDMA network paths into per-workload allocations.
- A **centralized control plane** (Kubernetes + Kueue + Argo CD) that continuously reconciles desired state against physical reality.
- A **fault-tolerant fabric**: any node can die at any time; the platform detects, cordons, drains, remediates, and re-queues affected work automatically.
- A **multi-tenant** platform with hierarchical quotas, fair-share borrowing, preemption, and showback accounting.
- A **multi-node distributed compute layer** (Ray, PyTorch FSDP/DDP, DeepSpeed, MPI, Spark, Dask, vLLM) so that workloads *designed* for distribution transparently span machines.
- A **shared storage fabric** built from the local disks of every node — replicated, tiered, and cached.
- A **performance engineering discipline**: continuous benchmarking, regression gates, and roofline analysis baked into CI.

### ❌ We are NOT building

- **A single-system image (SSI).** We are not merging N computers into one giant machine. That path (Kerrighed, OpenSSI, ScaleMP, TidalScale) is dead for good reasons: cache-coherence over Ethernet is catastrophically slow.
- **A unified RAM pool.** See §4.1. RAM stays with its motherboard. Full stop.
- **A unified VRAM pool for arbitrary applications.** Unmodified CUDA programs will not magically see 100 GPUs' worth of VRAM. Programs must use model/tensor/pipeline parallelism or sharding to span devices.
- **API-transparent GPU remoting for production** (rCUDA / bitfusion-style). Interesting for dev ergonomics, disastrous for training throughput. We provide it as an *optional developer convenience tier only*, clearly labeled as slow.
- **A public cloud.** No untrusted tenants, no hostile multi-tenancy assumptions, no billing engine. Trusted-org multi-tenancy only.

---

## 3. The Ten Laws of NEXUS

These are the tie-breakers. When two designs conflict, the one that better satisfies the lower-numbered law wins.

| Law | Statement | Enforcement |
|-----|-----------|-------------|
| **I. No Hidden Tax** | Every abstraction layer must justify its overhead with a measurement. If it costs >2 % and buys nothing, it is deleted. | Phase 47–49 benchmark gates |
| **II. Declarative or Dead** | If it isn't in Git and reconciled by a controller, it does not exist. Manual `kubectl apply` is an incident. | Argo CD auto-sync + drift alerts |
| **III. Topology Awareness Is Not Optional** | Every allocation is NUMA-aligned, PCIe-aware, and rail-aware, or it is rejected. | Topology Manager `single-numa-node`, Kueue TAS |
| **IV. Immutable Infrastructure** | Nodes are cattle. There is no `ssh` and no `apt install`. A broken node is reimaged, not repaired. | Talos Linux (no shell, no SSH, API-only) |
| **V. Fail Loudly, Recover Silently** | Every failure emits a signal; the platform's control loops handle it without a human. Humans read post-hoc reports. | NPD + DCGM + auto-remediation + Kueue requeue |
| **VI. Data Gravity Wins** | Move compute to data before moving data to compute. Cache aggressively at every tier. | Locality-aware scheduling, JuiceFS/Alluxio, spegel |
| **VII. Security Is a Substrate, Not a Feature** | mTLS everywhere, signed images, default-deny network, no root, no privileged pods (except the 6 audited exceptions). | Cilium NetworkPolicy default-deny, Kyverno, cosign |
| **VIII. Measure Before You Tune, Prove After** | No tuning parameter enters `main` without a before/after benchmark in the PR. | Performance CI (Phase 49) |
| **IX. Degrade Gracefully** | Losing the control plane must not kill running jobs. Losing a rack must not lose data. Losing the internet must not stop the cluster. | Static pods, 3-way replication across racks, local registry mirror |
| **X. The Boring Choice Wins** | Prefer the component with the largest operational surface area of prior art. Novelty is a liability at 100 nodes. | Documented in each ADR |

---

## 4. Physics & Honest Constraints (READ THIS TWICE)

This section exists because most "build a supercomputer from PCs" plans fail here. We name the walls before we run into them.

### 4.1 The Memory Boundary — Immovable

**RAM and VRAM are physically attached to their node.** There is no software that changes this without unacceptable cost.

| Approach | Latency | vs. Local DRAM | Verdict |
|---|---|---|---|
| Local DDR5 | ~80 ns | 1× | ✅ |
| Local HBM/GDDR (VRAM) | ~250–400 ns | — | ✅ |
| NVLink peer GPU | ~1.5–2 µs | ~20× | ✅ (where available) |
| CXL 3.0 pooled memory | ~250–400 ns | ~4× | ⚠️ Requires CXL-capable server CPUs + switches. **Not available on consumer boards.** Track for the 2027 refresh. |
| RDMA remote DRAM (one-sided READ) | ~1.5–3 µs | ~25× | ⚠️ Usable *only* for explicitly designed apps (Ray object store, FSDP shards, KV-cache offload) |
| TCP/IP remote memory | ~30–80 µs | ~500× | ❌ Never for hot paths |
| Swap-over-network (Infiniswap-class) | ~10–40 µs, page-granular | ~200× | ❌ Research toy. Not deployed. |

**What we build instead of a fake memory pool — the four legitimate techniques:**

1. **Sharding the state** — ZeRO-3 / FSDP / tensor parallelism shards parameters, gradients, and optimizer state across N GPUs. Aggregate *usable* VRAM ≈ N × per-GPU VRAM, minus activation and communication buffers. This is the real "VRAM pooling" and it works.
2. **Pipeline parallelism** — different layers on different nodes; only activations cross the wire. Best cross-node technique because the message volume is small relative to compute.
3. **Hierarchical offload** — DeepSpeed ZeRO-Infinity / vLLM+LMCache: VRAM → host DRAM → local NVMe → distributed object store. Each tier ~10× slower and ~10× bigger.
4. **Explicit distributed object stores** — Ray's plasma store, Spark/Dask partitions: the application knows data is remote and prefetches accordingly.

> **Rule for users:** *If your workload cannot express itself as sharded, pipelined, offloaded, or partitioned, it runs on one node. Buy a bigger node.* NEXUS makes single-fat-node allocation a first-class scheduling outcome, not a failure.

### 4.2 The Consumer GPU Reality Table

Most "ordinary GPU PCs" hold GeForce cards. These have hard capability gaps versus datacenter GPUs. **This is the single most important table in this document.**

| Capability | GeForce (RTX 30/40/50) | RTX PRO / Quadro | Datacenter (A100/H100/H200/B200) | NEXUS mitigation |
|---|---|---|---|---|
| **NVLink peer-to-peer** | 3090 only (2-way). Removed on 40/50-series. | Some SKUs | ✅ 900 GB/s (NVLink 4/5) | Prefer intra-node PCIe P2P; treat every GPU as an isolated island; use pipeline parallelism across nodes |
| **PCIe P2P (GPU↔GPU DMA)** | ❌ Disabled in driver on 4090/5090 | ✅ | ✅ | Staging through pinned host memory; optional community `open-gpu-kernel-modules` P2P patch (⚠️ unsupported, see Phase 17) |
| **GPUDirect RDMA (GPU↔NIC DMA)** | ❌ Not officially supported | ✅ | ✅ | Host-bounce path + pinned/registered buffers + `NCCL_NET_GDR_LEVEL` tuning; budget +8–15 µs per collective |
| **MIG (hardware partitioning)** | ❌ | ❌ | ✅ (A30/A100/H100+) | **Time-slicing + MPS + DRA** for fractional GPU sharing (Phase 18) |
| **vGPU / SR-IOV on GPU** | ❌ | Licensed | ✅ | Not used. Container-level isolation only. |
| **ECC VRAM** | ❌ | ✅ | ✅ | Checkpoint frequently; DCGM XID monitoring; treat silent corruption as a real risk for long runs |
| **FP8 / FP4 tensor cores** | ✅ (Ada FP8, Blackwell FP4) | ✅ | ✅ | Fully exploited — this is where consumer cards shine |
| **Sustained clocks / cooling** | Aggressive boost, thermal-limited | Blower, sustained | Passive, chassis-cooled | **Power/thermal governance (Phase 48)** — cap at the efficiency knee, not the frequency peak |
| **Driver EULA — datacenter use** | ⚠️ NVIDIA's GeForce driver licence restricts "datacenter deployment". | ✅ | ✅ | **Action: legal review before scale-out (Phase 01 RISK-LEGAL-01).** Options: use RTX PRO/datacenter SKUs for the production pool, or obtain written clarification. This plan does not provide legal advice. |
| **VRAM per card** | 8–32 GB | 16–96 GB | 40–192 GB | Sharding (§4.1) + honest per-workload VRAM admission control |

**Mixed-fleet strategy.** NEXUS explicitly supports heterogeneity: nodes are labeled with exact GPU model, VRAM, compute capability, NVLink presence, and GDR support. The scheduler forms **homogeneous placement groups** — a distributed training job gets 8 identical GPUs or it does not run, because the slowest rank sets the pace of every collective.

### 4.3 PCIe Lane Budget — The Silent Killer

A consumer CPU has a small, fixed lane budget. You cannot have everything.

| Platform | Usable CPU PCIe lanes | Realistic config |
|---|---|---|
| AMD AM5 (Ryzen 7000/9000) | 24 usable (28 total) | 1× GPU @ x16 + 1× NVMe @ x4 + **NIC @ x4 (chipset — bottleneck!)** |
| Intel LGA1700/1851 | 20 usable | 1× GPU @ x16 + 1× NVMe @ x4, **no clean NIC slot** |
| AMD Threadripper (sTR5) | 48–88 | 2× GPU @ x16 + NIC @ x16 + 4× NVMe ✅ |
| Intel Xeon W / Sapphire Rapids-WS | 64–112 | Same ✅ |
| AMD EPYC (SP5) | 128 | 4× GPU @ x16 + 2× NIC @ x16 + 8× NVMe ✅✅ |

**Consequence:** a 100GbE NIC needs PCIe 4.0 x16 (or 5.0 x8) to reach line rate. On a consumer board you will run the GPU at x8 and the NIC at x8 — **which is fine** (PCIe 4.0 x8 = 16 GB/s ≫ any single GPU's host transfer need) but must be a *deliberate, documented* choice, verified with `lspci -vv` in Phase 01 and Phase 48.

**Design rule:** *Compute nodes are consumer-class. Storage nodes and control nodes are workstation/server-class.* Never put a 100GbE NIC behind a chipset link.

### 4.4 The Network Is the Budget

Distributed training scaling efficiency is governed by the ratio of compute time to communication time. For data-parallel training:

```
Efficiency ≈ T_compute / (T_compute + T_allreduce)
T_allreduce(ring) ≈ 2 × (N-1)/N × ModelBytes / BusBandwidth
```

For a 7 B-parameter model in bf16 (14 GB of gradients) across 16 nodes:

| Fabric | Effective bus BW | AllReduce time | Verdict |
|---|---|---|---|
| 1 GbE | ~0.11 GB/s | **~240 s** | ❌ Absurd — communication is 100× compute |
| 10 GbE (TCP) | ~1.1 GB/s | ~24 s | ❌ Still dominant |
| 25 GbE (TCP) | ~2.8 GB/s | ~9.4 s | ⚠️ Marginal |
| 25 GbE RoCEv2 | ~3.0 GB/s | ~8.8 s | ⚠️ Viable for large-batch / gradient-accumulation |
| 100 GbE RoCEv2 | ~11.5 GB/s | **~2.3 s** | ✅ Target |
| 200 GbE / HDR IB | ~23 GB/s | ~1.1 s | ✅✅ Aspirational |

> **Hard requirement:** **≥25 GbE with RDMA on every compute node; 100 GbE on the training pool.** A 1 GbE cluster is not a cluster — it is 100 separate computers that occasionally exchange postcards. Second-hand ConnectX-5/6 and Mellanox/NVIDIA SN2000-series switches make 100 GbE achievable at roughly the cost of the GPUs' power bill.

### 4.5 Power & Thermal — The Constraint Everyone Forgets

**100 nodes × 1× 350 W GPU + 150 W host + 60 W overhead ≈ 56 kW sustained, ~75 kW peak.**

| Domain | Requirement at 100 nodes | Notes |
|---|---|---|
| Electrical service | ~75 kW → **3-phase, ~230 A @ 208 V** (or 110 A @ 400 V) | Well beyond any residential service. Requires a commercial feed and an electrician. |
| Distribution | 8–12 × 3-phase switched rack PDUs (30 A) | **Switched PDUs double as remote power control** — this is how we solve the no-BMC problem (§4.6) |
| Cooling | 75 kW ≈ 256,000 BTU/h ≈ **21 tons** of cooling | Hot/cold aisle containment; in-row or CRAC; ΔT budget ≤ 12 °C |
| Airflow | ~ 6,000–9,000 CFM | Consumer towers have poor front-to-back airflow — see Phase 02 chassis strategy |
| UPS | Control plane + storage only (≈ 6 kW) | Compute nodes are *not* on UPS. They are cattle; jobs checkpoint. |
| Circuit safety | Never exceed 80 % continuous load per breaker | Enforced by PDU alerting (Phase 45) |

**Power is a scheduling resource.** NEXUS models Watts as a schedulable dimension (Phase 31): the scheduler will refuse to start a job that would push a rack past its breaker envelope, and will power down idle nodes via WoL + PDU control.

### 4.6 No BMC — The Remote-Hands Problem

Consumer motherboards have **no IPMI/Redfish BMC**. At 100 nodes you cannot walk to a power button.

| Function | Server answer | **NEXUS answer on consumer boards** |
|---|---|---|
| Remote power on | IPMI `chassis power on` | **Wake-on-LAN** (BIOS: WoL enabled, ErP/deep-sleep disabled) |
| Remote power off (graceful) | IPMI soft | Talos API `shutdown` |
| Remote power cycle (hard) | IPMI `power cycle` | **Switched PDU outlet toggle** (SNMP/REST) — mandatory |
| Console / BIOS access | iKVM | **PiKVM v4 / JetKVM on a per-rack HDMI+USB matrix switch** (1 per 8–16 nodes) |
| BIOS configuration at scale | Redfish | **Scripted flashing + `conrep`-style vendor tools, or a one-time manual pass captured as a signed checklist** |
| Hardware sensors | IPMI SDR | `node-exporter` + `nvme-cli` + `nvidia-smi` + PDU per-outlet metering |
| Boot device selection | Redfish | **PXE-first boot order set once; iPXE chainload decides everything after** |

**Alternative worth pricing:** Intel vPro/AMT boards (business-class Intel) give real out-of-band control for ~$40/board more. At 100 nodes that is $4,000 to eliminate an entire class of operational pain. **Strongly recommended for any node added after the pilot.**

### 4.7 Storage Reality: Consumer NVMe Has No Power-Loss Protection

Consumer SSDs (Samsung 990 Pro, WD SN850X, etc.) lack a supercapacitor. On `fsync`, they must actually flush to NAND rather than acknowledging from a protected DRAM buffer.

| Workload | Enterprise SSD (w/ PLP) | Consumer SSD | Ratio |
|---|---|---|---|
| 4K random write, QD1, `fsync` | ~50,000 IOPS | ~800–3,000 IOPS | **~20–60× worse** |
| Sequential read | ~7 GB/s | ~7 GB/s | 1× ✅ |
| Sequential write (sustained, past SLC cache) | ~5 GB/s | ~1.5 GB/s | ~3× worse |

Ceph's BlueStore issues `fsync` on the write path. **Putting Ceph OSD WAL/DB on consumer NVMe produces a slow, disappointing cluster.** NEXUS therefore uses a **tiered storage design** (Phase 23):

- **Tier 0 — Scratch:** raw local NVMe, no replication, ephemeral. Full 7 GB/s. For datasets, shuffle, and activation offload.
- **Tier 1 — Fast replicated block:** Mayastor/NVMe-oF, 2-way replicated, **on nodes with enterprise SSDs only**.
- **Tier 2 — Bulk distributed:** Rook-Ceph with **enterprise SSD WAL/DB devices** on 5–9 dedicated storage nodes (never on GPU compute nodes).
- **Tier 3 — Object/archive:** Ceph RGW or MinIO on HDD + SSD metadata, erasure-coded.
- **Tier 4 — Cache:** JuiceFS/Alluxio distributed cache over Tier 3, materializing hot datasets into Tier 0.

---

## 5. Target Capability Model

What NEXUS must be able to run on day one of general availability, with published SLOs.

| Workload class | Shape | Resource pattern | Platform mechanism | SLO |
|---|---|---|---|---|
| **Interactive notebook** | 1 GPU fraction, 8 cores | Long-lived, bursty, low priority | JupyterHub + DRA time-sliced GPU | Start < 45 s; preemptible |
| **Single-node training** | 1–4 GPUs, 1 node | Hours–days | Kueue `Job`, NUMA-pinned | Start < 2 min when quota available |
| **Multi-node distributed training** | 8–64 GPUs, 2–16 nodes | Gang, all-or-nothing, RDMA-hot | Kubeflow Trainer / KubeRay + gang scheduling + Topology-Aware Scheduling | ≥ 85 % scaling efficiency at 16 nodes |
| **Elastic training** | 4–32 GPUs, shrink/grow | Tolerates rank churn | `torchrun --standalone --nnodes=2:8` + Ray | Survives 1 node loss with < 90 s stall |
| **Hyperparameter sweep** | 100s of 1-GPU trials | Embarrassingly parallel | Ray Tune / Argo Workflows | ≥ 95 % cluster GPU utilization during sweep |
| **Large-model inference** | Model > 1 GPU VRAM | TP within node, PP across nodes | vLLM + LeaderWorkerSet + KServe | p99 TTFT < 500 ms; ≥ 99.5 % availability |
| **High-throughput inference** | Many small models | Fractional GPU, autoscaled | DRA MPS + KEDA + KV-cache-aware routing | Scale 0→N in < 60 s |
| **Classic HPC / MPI** | 100s of CPU ranks | Tightly coupled, latency-bound | MPI Operator + UCX over RDMA, or Slurm via Slinky | ≥ 70 % HPL efficiency |
| **Big-data ETL** | 1000s of CPU cores | Shuffle-heavy, storage-bound | Spark / Dask on K8s + Tier-0 shuffle | Shuffle at ≥ 60 % of aggregate NIC BW |
| **CI / build farm** | Short, many, CPU-only | Seconds–minutes | BuildKit + Kueue low-priority | Queue time < 15 s |
| **Long-running services** | Platform itself | HA, always-on | Deployments + PDBs + anti-affinity | 99.9 % control-plane availability |

---

## 6. Reference Hardware Specification

### 6.1 Node Archetypes

NEXUS defines five archetypes. Every physical machine is classified into exactly one, and the classification drives its Talos machine config, K8s labels/taints, and scheduling eligibility.

#### **Archetype A — `control`** (3 or 5 nodes, odd count, HA)
| Component | Spec | Rationale |
|---|---|---|
| CPU | 8–16 cores, high single-thread (Ryzen 7/9, Core i7/i9) | etcd is latency-sensitive and single-thread bound |
| RAM | 64 GB ECC **strongly preferred** | Control-plane corruption is catastrophic |
| Boot | 2× 480 GB **enterprise** SATA/NVMe, mirrored | Must survive a disk death |
| etcd disk | **Dedicated enterprise NVMe with PLP** — non-negotiable | etcd `fsync` p99 must stay < 10 ms |
| NIC | 2× 25 GbE (bonded LACP) | Control traffic; RDMA not required |
| GPU | None | Keeps them out of the compute pool |
| Power | **On UPS** | Law IX |

#### **Archetype B — `compute-gpu`** (the bulk of the fleet)
| Component | Spec (minimum → target) | Rationale |
|---|---|---|
| CPU | 8 cores/16 threads → 16 cores/32 threads | ~4 cores per GPU for data loading; 2 cores reserved for system |
| RAM | 64 GB → **128 GB** (≥ 2× total VRAM) | Host staging, page cache, pinned buffers, ZeRO-Offload |
| GPU | 1–4× ≥ 12 GB VRAM → 24–32 GB | 24 GB is the practical floor for modern model work |
| Boot | 256 GB NVMe | Talos is tiny; this is mostly image cache |
| Scratch | **1–4 TB NVMe (Tier 0)** | Dataset staging — the difference between 40 % and 95 % GPU utilization |
| NIC | 25 GbE RoCE → **100 GbE ConnectX-5/6** | §4.4 |
| BIOS | WoL on, deep-sleep off, IOMMU on, Above-4G Decoding on, Resizable BAR on, C-states tuned, PXE-first | Phase 08 checklist |

#### **Archetype C — `storage`** (5–9 nodes, dedicated)
| Component | Spec | Rationale |
|---|---|---|
| CPU | 16+ cores | ~2 cores + 5 GB RAM per Ceph OSD |
| RAM | 128 GB ECC | BlueStore cache; ECC mandatory for a data-durability tier |
| Data disks | 6–12× SSD/HDD | Capacity tier |
| WAL/DB | 1–2× **enterprise NVMe with PLP** | §4.7 — this is the single most important storage purchase |
| NIC | **2× 100 GbE** (separate public/cluster Ceph networks) | Recovery traffic must not starve client traffic |
| Chassis | Rackmount server-class | These are pets, not cattle |

#### **Archetype D — `compute-cpu`** (optional, cheap capacity)
GPU-less machines, older CPUs. Serve CI, ETL, Spark/Dask, preprocessing. Same networking floor (25 GbE) — a slow node poisons a shuffle.

#### **Archetype E — `infra`** (2–3 nodes)
Hosts the platform itself: registry, Argo CD, Prometheus/Mimir, Grafana, Vault, Keycloak, Harbor. Tainted so user workloads never land here. Kept off the compute pool so a runaway training job cannot take down observability.

### 6.2 Fabric Specification

| Layer | Spec at 100 nodes | Notes |
|---|---|---|
| **Leaf switches** | 6–8× 32-port 100 GbE (NVIDIA SN2410 / SN3420, Arista 7050X, Celestica) | 1 leaf pair per rack; MLAG or EVPN-MH |
| **Spine switches** | 2–4× 32-port 100/200 GbE | Non-blocking or ≤ 2:1 oversubscription |
| **Topology** | 2-tier leaf-spine (Clos) | 3-tier only past ~500 nodes |
| **Oversubscription** | **1:1 for the training pool**, ≤ 3:1 elsewhere | Ring/tree allreduce is bisection-hungry |
| **L3 design** | BGP unnumbered to every host (Cilium BGP Control Plane); ECMP everywhere | No spanning tree, no large L2 domains |
| **RDMA** | RoCEv2 with **PFC on a dedicated priority + ECN/DCQCN** | Or InfiniBand HDR if buying used — simpler, better collectives, but a separate fabric |
| **MTU** | 9000 (jumbo) end-to-end, verified per-link | A single 1500-MTU link silently halves throughput |
| **Management network** | Separate 1 GbE VLAN: PXE, IPMI/PiKVM, PDUs, out-of-band | Must work when the data fabric is down |
| **Storage network** | Logical VLAN on the 100 GbE fabric, QoS-isolated | Physical separation only if budget allows |

### 6.3 Indicative Bill of Materials (100-node build)

| Item | Qty | Unit (USD, incl. used-market) | Subtotal |
|---|---|---|---|
| Compute node (16C / 128 GB / 1× 24 GB GPU / 2 TB NVMe) | 88 | $2,400 | $211,200 |
| Storage node (server-class, 128 GB ECC, 10× SSD, 2× NVMe PLP) | 6 | $6,500 | $39,000 |
| Control node | 3 | $1,800 | $5,400 |
| Infra node | 3 | $2,200 | $6,600 |
| ConnectX-5 100 GbE NIC (used) | 100 | $180 | $18,000 |
| 100 GbE DAC/AOC cable | 120 | $45 | $5,400 |
| Leaf switch 32×100 GbE (used SN2410) | 8 | $3,500 | $28,000 |
| Spine switch 32×100 GbE | 2 | $5,000 | $10,000 |
| 1 GbE mgmt switch 48-port | 4 | $400 | $1,600 |
| Switched 3-phase PDU 30 A | 12 | $900 | $10,800 |
| PiKVM v4 + HDMI/USB matrix | 8 | $600 | $4,800 |
| Open-frame / 4U rack chassis | 100 | $120 | $12,000 |
| 42U rack | 8 | $900 | $7,200 |
| UPS (6 kW, control+storage) | 2 | $3,000 | $6,000 |
| Cooling (in-row, 25 t capacity) | — | — | $60,000–$120,000 |
| Electrical work (3-phase, panels, runs) | — | — | $25,000–$60,000 |
| **Hardware subtotal** | | | **≈ $385,000** |
| **Facility subtotal** | | | **≈ $85,000–$180,000** |
| **TOTAL CAPEX** | | | **≈ $470,000–$565,000** |

> **Compare:** 88 GPUs of comparable class from a public cloud at $2.20/GPU-hr ≈ **$1.7 M/year**. NEXUS pays for itself in **4–7 months** at 60 % utilization, then costs only power (§11).

---

## 7. Technology Stack Selection

Every choice below is an ADR in `ARCHITECTURE.md`. Summary table:

| Layer | **Choice** | Why | Rejected alternatives (and why) |
|---|---|---|---|
| **Node OS** | **Talos Linux** | Immutable, API-only, no SSH/shell, declarative machine config, 12-second boot, minimal attack surface. Perfect embodiment of Law IV. | *Ubuntu+kubeadm* (config drift at scale); *Flatcar* (good, but less declarative); *Fedora CoreOS* (Ignition is less complete than Talos machine config); *Bottlerocket* (AWS-centric) |
| **Bare-metal provisioning** | **Tinkerbell** (+ Talos Image Factory) | Netboot-native, workflow-driven, handles no-BMC via WoL hooks, k8s-native | *MAAS* (heavy, Ubuntu-centric, needs BMC); *Metal³* (needs Redfish/BMC — we have none); *Foreman* (dated); *manual USB* (impossible at 100) |
| **Orchestrator** | **Kubernetes 1.34+** | Only substrate with DRA, Topology Manager, a full device/CNI/CSI ecosystem, and a 10-year operational corpus | *Slurm alone* (poor service story, weak containers, no reconciliation); *Nomad* (smaller ecosystem, no DRA); *Ray alone* (no infra layer); *Docker Swarm* (dead) |
| **Batch/HPC compatibility** | **Slinky (Slurm-on-K8s)** as an *optional overlay* | Gives classic `sbatch` users their UX without a second cluster | Running two schedulers on split hardware (fragments the pool — the exact thing we're solving) |
| **CNI** | **Cilium** (eBPF, kube-proxy replacement, native routing, BGP, BIG TCP, Hubble) | Highest-throughput CNI; no overlay encap tax; L7 policy; best observability | *Calico* (good, but weaker eBPF/observability story); *Flannel* (VXLAN tax); *Antrea*; *kube-proxy iptables* (O(n) rule scaling collapses at scale) |
| **Secondary/RDMA net** | **Multus + SR-IOV Device Plugin + RDMA Shared Device Plugin + NVIDIA Network Operator** | Gives pods a VF directly on the NIC — kernel bypass, line rate | Host networking for everything (breaks isolation and policy) |
| **GPU management** | **NVIDIA GPU Operator + NVIDIA DRA Driver** | Automates driver/toolkit/device-plugin/DCGM; DRA enables real fractional and topology-aware GPU allocation | *Manual driver installs* (config drift, Law IV violation); *device-plugin only* (no sharing semantics, no topology) |
| **GPU sharing** | **DRA + time-slicing + MPS** (MIG where hardware allows) | Only viable fractional path on consumer GPUs | *vGPU* (unlicensed on GeForce); *rCUDA-style remoting* (throughput disaster) |
| **Batch queueing** | **Kueue** (+ `scheduler-plugins` coscheduling) | K8s-native quota, cohorts, fair-share borrowing, preemption, **Topology-Aware Scheduling**, all-or-nothing admission | *Volcano* (strong gang scheduling, but a fork of the scheduler and a heavier operational surface — kept as fallback, see ADR-014); *YuniKorn*; *raw K8s Jobs* (no quota/gang) |
| **Distributed AI runtime** | **Ray (KubeRay)** + **Kubeflow Trainer v2** | Ray for elastic Python/RL/tuning/serving; Trainer for canonical PyTorch DDP/FSDP/DeepSpeed | *Bare torchrun* (no orchestration); *Horovod* (declining) |
| **Inference** | **vLLM** + **KServe** + **LeaderWorkerSet** (+ llm-d patterns) | PagedAttention, continuous batching, TP/PP, multi-node LWS grouping, KV-cache-aware routing | *Triton alone* (heavier); *TGI* (narrower); *raw FastAPI* (no batching) |
| **Storage — bulk** | **Rook-Ceph** (RBD + CephFS + RGW) | The only self-healing, multi-protocol, rack-aware OSS storage that survives node loss at this scale | *Longhorn* (simpler, but weak at 100 nodes / no object / no CephFS); *GlusterFS* (deprecated); *MinIO* (object only — used as an alternative for Tier 3); *NFS* (single point of failure) |
| **Storage — fast block** | **OpenEBS Replicated PV (Mayastor)** over NVMe-oF | Near-local NVMe latency with replication; SPDK user-space path | *Ceph RBD for hot data* (fsync tax, §4.7) |
| **Storage — scratch** | **local-path-provisioner / LVM LocalPV + `emptyDir` on NVMe** | Zero-overhead, full device bandwidth | Anything replicated (waste for ephemeral data) |
| **Dataset cache** | **JuiceFS** (metadata in Redis/TiKV, data in S3) or **Alluxio** | POSIX view over object storage with distributed local-NVMe caching — turns cold S3 reads into local NVMe reads | *Reading S3 directly per epoch* (network amplification × epochs) |
| **GitOps** | **Argo CD** (app-of-apps) + **Kustomize** + **Helm** | Declarative, drift-detecting, self-healing, multi-env, best UI | *Flux* (excellent alternative — chosen against only for UI and app-of-apps ergonomics); *Ansible push* (imperative, no reconciliation) |
| **Secrets** | **OpenBao/Vault** + **External Secrets Operator**; **SOPS+age** for bootstrap | Dynamic credentials, PKI engine, audit trail | *Sealed Secrets* (no dynamic secrets); *plain K8s Secrets* (base64 ≠ encryption) |
| **Identity** | **Keycloak** (OIDC) → K8s OIDC + Argo/Grafana/Harbor SSO | One identity, everywhere, with groups → RBAC mapping | *Static kubeconfigs* (unrevocable, unauditable) |
| **Registry** | **Harbor** + **Spegel** (P2P in-cluster image mirror) | Local pulls, vuln scanning, signing, replication. Spegel makes a 100-node rollout pull once, not 100 times. | *Docker Hub direct* (rate limits, egress, outage-coupled) |
| **Observability** | **Prometheus + Mimir** (long-term), **Grafana**, **Loki**, **Tempo**, **OpenTelemetry**, **DCGM-exporter**, **Parca** (continuous profiling), **Kepler** (power) | Full-stack: metrics, logs, traces, profiles, power | *Datadog/NewRelic* (cost, egress, sovereignty) |
| **Policy** | **Kyverno** + Pod Security Admission + Cilium NetworkPolicy (default-deny) | YAML-native policy, mutation + validation + generation | *OPA/Gatekeeper* (Rego learning cliff) |
| **Runtime security** | **Tetragon** (eBPF) + **Trivy Operator** + **cosign/sigstore** | Kernel-level enforcement + supply chain integrity | *Falco* (fine alternative; Tetragon chosen for Cilium integration) |
| **Workflows** | **Argo Workflows** (+ Argo Events) | DAG/pipeline engine, artifact passing, K8s-native | *Airflow* (weak K8s-native execution); *Flyte* (strong — see ADR-021) |
| **Experiment tracking** | **MLflow** + object storage backend | Open, self-hostable, model registry included | *W&B* (SaaS, egress) |
| **Developer portal** | **Backstage** + software templates | Self-service golden paths; the "small private cloud" UX | Wiki + tribal knowledge (does not scale past ~10 users) |
| **Container runtime** | **containerd** + **NVIDIA Container Toolkit** (CDI mode) | Standard, CDI is the modern device-injection path | *Docker* (deprecated in K8s); *CRI-O* (fine alternative) |
| **Load balancing** | **Cilium L2 announcements / BGP** + **Gateway API (Envoy)** | No extra component; BGP ECMP for real HA | *MetalLB* (superseded by Cilium's built-in); *nginx-ingress* (Gateway API is the future) |

---

## 8. The Performance Budget

Law I demands we account for every lost percent. This is the ledger.

### 8.1 Single-Node Efficiency Target: ≥ 98 % of bare metal

| Source of loss | Naive cost | NEXUS cost | How |
|---|---|---|---|
| Hypervisor | 5–15 % | **0 %** | No hypervisor. Containers only. |
| Container runtime | 0.5–2 % | **< 0.3 %** | cgroup v2, no seccomp on the hot path for trusted HPC workloads (audited exception), CDI device injection |
| CPU scheduler jitter | 2–8 % | **< 0.5 %** | `static` CPU Manager policy → exclusive cores; `isolcpus`/`nohz_full` on the RDMA-polling cores |
| NUMA misplacement | **10–40 %** (memory bandwidth) | **0 %** | Topology Manager `single-numa-node` + NUMA-aware device plugin |
| Page faults / TLB misses | 2–5 % | **< 1 %** | 2 MB + 1 GB hugepages preallocated |
| CNI overlay encapsulation | 15–30 % of net BW | **0 %** | Cilium **native routing** (no VXLAN/Geneve), eBPF host-routing, BIG TCP |
| kube-proxy iptables | O(n) latency growth | **0 %** | Cilium kube-proxy replacement (eBPF hash maps, O(1)) |
| Kernel network stack | 30–60 % of 100 G | **0 % on the RDMA path** | SR-IOV VF + RoCEv2 kernel bypass into the pod |
| Storage abstraction | 20–50 % IOPS | **< 3 %** on Tier 0 | Raw local NVMe via LVM LocalPV; no network hop |
| CPU frequency governor | 3–10 % | **0 %** | `performance` governor; C-state floor tuned in Phase 48 |
| Thermal throttling | 5–25 % | **< 2 %** | Power cap at the efficiency knee + airflow engineering (Phase 02/48) |

### 8.2 Multi-Node Scaling Efficiency Targets

| Nodes | Data-parallel (FSDP, 7 B model) | Pipeline-parallel | Embarrassingly parallel |
|---|---|---|---|
| 2 | ≥ 96 % | ≥ 97 % | ≥ 99.5 % |
| 4 | ≥ 93 % | ≥ 95 % | ≥ 99.5 % |
| 8 | ≥ 90 % | ≥ 93 % | ≥ 99 % |
| 16 | ≥ 85 % | ≥ 90 % | ≥ 99 % |
| 32 | ≥ 78 % | ≥ 86 % | ≥ 98 % |
| 64 | ≥ 70 % | ≥ 82 % | ≥ 97 % |

*Measured with `nccl-tests` busbw and end-to-end tokens/sec. Gates enforced in Phase 49 CI. Numbers assume 100 GbE RoCEv2 and no GPUDirect (consumer GPU host-bounce path); GDR-capable hardware adds ~4–7 points at 16+ nodes.*

### 8.3 The Golden Benchmarks (run in every phase gate)

| # | Benchmark | Tool | Gate |
|---|---|---|---|
| B1 | Single-GPU FP16 GEMM | `cublasLt` / `nvbench` | ≥ 97 % of vendor spec |
| B2 | Host↔Device bandwidth | `bandwidthTest` | ≥ 90 % of PCIe theoretical |
| B3 | Node-to-node TCP | `iperf3 -P 8` | ≥ 92 % of line rate |
| B4 | Node-to-node RDMA | `ib_write_bw`, `ib_send_lat` | ≥ 96 % line rate; < 3 µs p99 |
| B5 | AllReduce busbw | `nccl-tests all_reduce_perf` | ≥ 85 % of theoretical at 8 nodes |
| B6 | Local NVMe | `fio` 4K randread QD32 | ≥ 95 % of device spec |
| B7 | Distributed FS | `fio` + `mdtest` on CephFS | ≥ 60 % aggregate device BW |
| B8 | HPL (Linpack) | `hpl-cuda` | ≥ 70 % of Rpeak |
| B9 | End-to-end training | ResNet-50 + Llama-7B LoRA | Within 5 % of published reference |
| B10 | Inference | vLLM throughput + TTFT | Within 5 % of upstream reference |
| B11 | Scheduling latency | Custom harness | p99 pod-start < 5 s (warm image) |
| B12 | Failure recovery | Chaos harness | Node kill → job requeued < 90 s |

---

## 9. Scaling Model: 8 → 100+ Nodes

The architecture is chosen so that **nothing structural changes** between these milestones. What changes is replica counts and switch tiers.

| Milestone | Nodes | Control plane | Network | Storage | Notable additions |
|---|---|---|---|---|---|
| **M0 — Lab** | 1 (all-in-one) | Single Talos node | Existing LAN | local-path | Validate the whole stack in a VM/1 box |
| **M1 — Pilot** | 8 (3 ctrl + 4 GPU + 1 storage) | 3-node etcd | 1× 25/100 GbE switch | Ceph 3-way, 1 failure domain | Prove B1–B6, GitOps, first training job |
| **M2 — Squad** | 24 | 3-node etcd, dedicated | 2 leaves + MLAG | Ceph across 3 racks | Kueue quotas, multi-tenancy, Ray autoscaling |
| **M3 — Wing** | 48 | 3-node etcd + 3 infra | 4 leaves + 2 spines, BGP | 5 storage nodes, RGW | Slinky, JuiceFS cache, Mimir long-term metrics |
| **M4 — Fleet** | 100+ | **5-node etcd**, separated events store | 8 leaves + 4 spines, 1:1 for training pool | 9 storage nodes, EC pools | Power-aware scheduling, per-rack availability zones, chaos program |
| **M5 — Federation** | 250+ | Multi-cluster (Cluster API + Karmada/Admiralty) | Multi-fabric | Cross-cluster object replication | Only if a single cluster's etcd or blast radius becomes limiting |

**Known scaling cliffs and pre-planned mitigations:**

| Cliff | Appears around | Mitigation (pre-designed) |
|---|---|---|
| etcd write amplification | ~5,000 pods / high event churn | Separate etcd for events; `--event-ttl=30m`; reduce `kubelet` status frequency |
| kube-proxy iptables O(n) | ~1,000 services | Already eliminated — Cilium eBPF from day one |
| Image pull storm | ~50 nodes | Spegel P2P mirror + Harbor proxy cache + pre-pull DaemonSet |
| Prometheus cardinality | ~40 nodes with DCGM | Mimir + recording rules + label dropping (Phase 45) |
| Scheduler throughput | ~5,000 pending pods | `percentageOfNodesToScore`, Kueue admission batching |
| L2 broadcast domain | ~256 hosts | L3-to-the-host BGP from M3 onward |
| ARP/ND table exhaustion | ~1,000 endpoints | Native routing + per-rack subnets |
| Ceph OSD map churn | ~200 OSDs | `osd_map_cache_size`, stretch-mode discipline, staged rebalancing |

---

## 10. Multi-Tenancy & Organizational Model

| Construct | Implementation | Purpose |
|---|---|---|
| **Tenant** | Capsule `Tenant` → owns N namespaces | Org unit (team, lab, project) |
| **Identity** | Keycloak group → K8s Group → RBAC RoleBinding | Single sign-on, revocable |
| **Quota — fair share** | Kueue `ClusterQueue` in a `Cohort` with `borrowingLimit` | Guaranteed floor + opportunistic borrowing of idle capacity |
| **Quota — hard cap** | `ResourceQuota` + `LimitRange` | Prevents a runaway namespace |
| **Priority** | `WorkloadPriorityClass`: `critical`(1000) > `production`(800) > `research`(500) > `batch`(200) > `spot`(50) | Preemption ordering |
| **Isolation — network** | Default-deny `CiliumNetworkPolicy` per namespace; explicit allow-lists | Blast-radius containment |
| **Isolation — compute** | Node taints + `nodeSelector` + Kyverno-enforced `runAsNonRoot` | No cross-tenant node sharing for `critical` tier |
| **Isolation — data** | Per-tenant Ceph pools + RGW buckets + Vault paths | Data sovereignty within the org |
| **Accounting** | Kubecost/OpenCost + DCGM GPU-seconds + Kepler Watt-hours → per-tenant showback report | Behavioral incentive without real billing |
| **Self-service** | Backstage templates: "New training job", "New model endpoint", "New notebook" | Golden paths (Law X) |

**Default quota policy (tunable):** every tenant gets a guaranteed floor of 10 % of its historical p50 usage, may borrow up to 300 % from the cohort when idle capacity exists, and borrowed capacity is preemptible with a 120-second graceful-termination window (enough for a checkpoint).

---

## 11. Cost Model

### 11.1 Operating Cost (100 nodes, steady state)

| Line item | Calculation | Annual |
|---|---|---|
| Compute power | 56 kW avg × 8,760 h × $0.12/kWh | $58,900 |
| Cooling power (PUE 1.4) | +40 % of IT load | $23,500 |
| Hardware refresh reserve | 15 % of $385 k capex/yr (≈6.5 yr life) | $57,750 |
| Network/internet | Business fiber | $6,000 |
| Spares & failures (5 %/yr) | Nodes, disks, cables, PSUs | $19,000 |
| Ops labor | 0.5 FTE platform engineer | $75,000 |
| **Total OPEX** | | **≈ $240,000/yr** |

### 11.2 Effective Cost per GPU-Hour

```
88 GPUs × 8,760 h × 60 % utilization = 462,528 GPU-hours/year
$240,000 / 462,528                    = $0.52 /GPU-hour  (OPEX only)
+ amortized capex ($470k / 5 yr)      = $0.72 /GPU-hour  (fully loaded)
```

At 85 % utilization: **$0.51/GPU-hour fully loaded.**
Public-cloud equivalent (24 GB-class GPU, on-demand): **$1.10–$2.60/GPU-hour**, plus egress.

**Break-even utilization vs. cloud @ $1.50/GPU-hr: ~20 %.** Above that, NEXUS wins. This is the economic thesis.

### 11.3 The Non-Financial Return

Data sovereignty · zero egress · full hardware observability (you can read PCIe counters) · no capacity queues · no instance-type roulette · a team that actually understands its infrastructure.

---

## 12. Risk Register

| ID | Risk | P | I | Score | Mitigation | Owner phase |
|---|---|---|---|---|---|---|
| **R-01** | **Network is under-specified (1/10 GbE)** — the #1 cause of failure for this class of project | H | **Critical** | 🔴 | Hard gate: no node joins the training pool without ≥25 GbE RDMA validated by B4 | 03, 20, 21 |
| **R-02** | **Power/thermal exceeds facility capacity** → breakers trip, thermal throttling, fire risk | H | **Critical** | 🔴 | Phase 02 load study *before* purchase; per-rack power caps enforced in the scheduler; PDU alerting | 02, 31, 48 |
| **R-03** | **GeForce driver EULA / datacenter clause** | M | High | 🟠 | Legal review at Phase 01; procurement pivot to RTX PRO / datacenter SKUs for the production pool | 01 |
| **R-04** | **Ceph on consumer NVMe performs terribly** | H | High | 🔴 | Tiered design (§4.7); enterprise PLP NVMe mandatory for WAL/DB; Tier-0 scratch for hot paths | 23, 25 |
| **R-05** | **No BMC → hands-on-hardware ops at 100 nodes** | H | High | 🔴 | Switched PDUs + WoL + PiKVM from day one; prefer vPro/AMT boards for new purchases | 02, 07 |
| **R-06** | **Consumer hardware failure rate** (PSU, fans, consumer NVMe wear) | H | Medium | 🟠 | 5 % spares pool; automated cordon+drain; SMART/NVMe wear alerting; treat nodes as cattle | 22, 45 |
| **R-07** | **Silent data corruption (no ECC on compute nodes)** | M | High | 🟠 | ECC mandatory on control/storage; frequent checkpointing; Ceph end-to-end checksums; periodic scrub | 06, 25, 32 |
| **R-08** | **Complexity exceeds the team's operational capacity** | M | High | 🟠 | Strict phase gating; nothing enters the platform without a runbook and a dashboard; Backstage golden paths | all |
| **R-09** | **etcd degradation at scale** | M | Critical | 🟠 | Dedicated PLP NVMe for etcd; separate events etcd; continuous `fsync` p99 alerting; tested restore drills | 11, 28 |
| **R-10** | **RoCE congestion collapse** (PFC storms, head-of-line blocking) | M | High | 🟠 | ECN/DCQCN over PFC; PFC watchdog; per-priority buffers; incast testing in Phase 21 | 20, 21 |
| **R-11** | **Split-brain / partition during a switch failure** | L | Critical | 🟡 | Odd etcd count, dual-homed control nodes, Ceph `min_size=2`, MLAG/EVPN redundancy | 03, 11, 25 |
| **R-12** | **Supply-chain compromise via container images** | M | High | 🟠 | Harbor + cosign signature enforcement via Kyverno; SBOM on every image; Trivy gates | 43, 54 |
| **R-13** | **GitOps repo becomes the single point of failure** | L | High | 🟡 | Mirrored Git (local Gitea + upstream), Argo CD can run from a local mirror, disaster bundle in Vault | 14, 28 |
| **R-14** | **Cost overrun on cooling/electrical retrofit** | M | High | 🟠 | Get 3 contractor quotes before hardware purchase; phase the buildout to match facility capacity | 02 |
| **R-15** | **Heterogeneity causes stragglers in collectives** | H | Medium | 🟠 | Homogeneous placement groups enforced by Kueue TAS + node labels; per-model GPU pools | 13, 30 |
| **R-16** | **Kubernetes/driver upgrade breaks GPU stack** | M | High | 🟠 | Canary node pool; pinned driver/toolkit versions; staged rollout with automatic rollback | 52 |
| **R-17** | **Noise / physical environment unsuitable** | M | Medium | 🟡 | 100 GPU PCs ≈ 85–95 dBA. Dedicated room with acoustic treatment; never an office space. | 02 |
| **R-18** | **Storage rebuild storm saturates the fabric** | M | High | 🟠 | Ceph `osd_max_backfills` throttles, separate cluster network, QoS class for recovery traffic | 25, 20 |

---

## 13. Success Criteria & Acceptance Gates

**A stage is not complete until every gate passes and the evidence is committed to `/evidence/<phase>/`.**

| Gate | Criterion | Verification | Blocks |
|---|---|---|---|
| **G0** | Every node's hardware, PCIe topology, and thermal envelope is inventoried and machine-readable | `inventory/nodes.yaml` validates against the schema; 100 % coverage | Phase 05+ |
| **G1** | Facility supports the planned load with ≥ 25 % headroom | Signed electrical/thermal study | Any hardware purchase |
| **G2** | A node reimages from bare metal to `Ready` with zero human touch in < 15 min | Timed, recorded, repeated 3× | Phase 11+ |
| **G3** | Control plane survives the loss of any single control node with < 30 s API disruption | Chaos test recorded | Phase 17+ |
| **G4** | B3/B4 pass on every node pair in the training pool | Automated full-mesh benchmark report | Phase 29+ |
| **G5** | GPU allocation is NUMA- and PCIe-aligned 100 % of the time | `numactl`/`nvidia-smi topo -m` assertion in CI | Phase 29+ |
| **G6** | B5 (NCCL allreduce) ≥ 85 % of theoretical at 8 nodes | `nccl-tests` report committed | Phase 35+ |
| **G7** | Ceph survives the loss of an entire rack with zero data loss and no client I/O errors | Chaos test recorded | GA |
| **G8** | A gang-scheduled 16-node job is admitted, placed topology-optimally, and starts in < 3 min | Recorded trace | GA |
| **G9** | Killing a node mid-training results in automatic requeue and resume from checkpoint in < 5 min | Chaos test recorded | GA |
| **G10** | Full cluster rebuild from Git + backups completes in < 4 h | Full DR drill, recorded | GA |
| **G11** | Every workload class in §5 meets its SLO | Benchmark suite green | GA |
| **G12** | Zero manual `kubectl apply` in the preceding 30 days | Argo CD drift report + audit log | GA |
| **G13** | 100 % of running images are signed and SBOM-attested | Kyverno policy report | GA |
| **G14** | On-call runbook exists for every alert that can fire | Alert-to-runbook coverage report = 100 % | GA |

---

## 14. Roadmap & Phase Map

**57 phases across 10 stages.** Each phase is a self-contained work order in `phases/`, sized for a single focused session. See `phases/README.md` for the execution protocol.

| Stage | Phases | Theme | Outcome |
|---|---|---|---|
| **0 — Foundation & Design** | `00`–`05` | Repo, inventory, facility, network design, security architecture, capacity model | A buildable, costed, risk-assessed design |
| **1 — Bootstrap Infrastructure** | `06`–`11` | Seed node, DHCP/DNS/PXE/NTP, PKI, provisioning, Talos OS, secrets, bootstrap observability | Bare metal → booted, addressable, declaratively-configured nodes |
| **2 — Kubernetes Substrate** | `12`–`17` | HA control plane, Cilium, node onboarding, GitOps, policy/tenancy, ingress | A production-grade, self-reconciling Kubernetes cluster |
| **3 — Acceleration Fabric** | `18`–`23` | GPU Operator, DRA, NUMA/topology, SR-IOV/RDMA/RoCE, NCCL tuning, GPU health | GPUs and RDMA are first-class, topology-aware, shareable resources |
| **4 — Storage Fabric** | `24`–`29` | Tiering, local NVMe, Mayastor, Rook-Ceph, object, dataset cache, backup/DR | A tiered, self-healing, benchmarked storage layer |
| **5 — Scheduling & Resource Mgmt** | `30`–`35` | Kueue, gang + topology-aware scheduling, power-aware autoscaling, preemption/checkpointing, accounting, Slurm interop | The "unified pool" behavior users actually experience |
| **6 — Distributed Compute** | `36`–`41` | Ray, PyTorch Trainer, Dask/Spark, MPI/UCX, vLLM inference, Argo Workflows | Workloads genuinely span machines at high efficiency |
| **7 — Platform Experience** | `42`–`47` | Backstage, notebooks/IDE, registry + build farm, MLflow, full observability, SLOs/on-call | A private cloud people *want* to use |
| **8 — Performance Engineering** | `48`–`52` | Benchmark harness, systematic tuning, performance CI, distributed profiling, 100-node scale validation | Proven, defended, regression-gated performance |
| **9 — Operations & Evolution** | `53`–`56` | Day-2 upgrades, chaos/DR, security hardening, capacity planning & handover | A platform that outlives its builders |

**Indicative timeline** (1 engineer full-time, or 2–3 part-time):

```
Month 1  ████████░░░░░░░░░░░░  Stage 0–1   Design + bootstrap (8-node pilot)
Month 2  ░░░░████████░░░░░░░░  Stage 2–3   K8s + GPU/RDMA fabric
Month 3  ░░░░░░░░████████░░░░  Stage 4–5   Storage + scheduling
Month 4  ░░░░░░░░░░░░████████  Stage 6     Distributed compute frameworks
Month 5  ░░░░░░░░░░░░░░██████  Stage 7     Platform UX + observability
Month 6  ░░░░░░░░░░░░░░░░████  Stage 8     Performance engineering → M2 (24 nodes)
Month 7+ ░░░░░░░░░░░░░░░░░░██  Stage 9     Ops maturity → M3 (48) → M4 (100+)
```

---

## 15. Glossary

| Term | Meaning |
|---|---|
| **ADR** | Architecture Decision Record — a dated, immutable record of a technical choice and its rationale |
| **AllReduce** | Collective operation summing tensors across all ranks and distributing the result; dominates data-parallel training communication |
| **busbw** | "Bus bandwidth" — NCCL's algorithm-normalized bandwidth metric, the correct way to compare collective performance |
| **CDI** | Container Device Interface — the vendor-neutral standard for injecting devices into containers |
| **Clos / leaf-spine** | Non-blocking multi-stage switch topology; every leaf connects to every spine |
| **Cohort** | Kueue construct: a set of ClusterQueues that may lend/borrow unused quota |
| **DCGM** | NVIDIA Data Center GPU Manager — health, telemetry, diagnostics |
| **DRA** | Dynamic Resource Allocation — Kubernetes' modern API for requesting structured, shareable, topology-aware devices |
| **DCQCN** | Data Center QCN — the congestion-control algorithm for RoCEv2 (ECN-driven rate control) |
| **FSDP** | Fully Sharded Data Parallel — PyTorch's parameter/gradient/optimizer sharding across ranks |
| **Gang scheduling** | All-or-nothing placement: either every rank of a job starts, or none do |
| **GPUDirect RDMA** | DMA directly between GPU memory and the NIC, bypassing host memory |
| **MIG** | Multi-Instance GPU — hardware partitioning of a datacenter GPU into isolated instances |
| **MPS** | Multi-Process Service — NVIDIA's mechanism for concurrent kernel execution from multiple processes on one GPU |
| **NUMA** | Non-Uniform Memory Access — memory is faster from its local socket/die |
| **PFC** | Priority Flow Control — per-priority link-level pause; makes Ethernet lossless for RoCE |
| **PLP** | Power-Loss Protection — supercapacitors in enterprise SSDs that make `fsync` fast and safe |
| **RoCEv2** | RDMA over Converged Ethernet v2 — routable RDMA over UDP/IP |
| **SR-IOV** | Single-Root I/O Virtualization — a physical NIC presents multiple virtual functions (VFs) |
| **TAS** | Topology-Aware Scheduling — Kueue's placement of a gang within the tightest network domain |
| **XID** | NVIDIA GPU error code reported by the driver; the primary GPU health signal |

---

## Appendix A — The One-Page Summary

> **NEXUS** is a bare-metal Kubernetes cluster on Talos Linux, provisioned by Tinkerbell, networked by Cilium over a 100 GbE RoCEv2 Clos fabric, accelerated by the NVIDIA GPU Operator with DRA-based fractional GPU allocation, stored on a four-tier fabric (local NVMe → Mayastor → Rook-Ceph → object + JuiceFS cache), scheduled by Kueue with gang and topology-aware placement, running Ray / PyTorch / MPI / Spark / vLLM workloads, observed by Prometheus-Mimir-Grafana-Loki-Tempo-Parca-Kepler, governed by Argo CD GitOps, and defended by continuous benchmarking with hard regression gates.
>
> **It does not pool RAM or VRAM.** It pools *cores, devices, bandwidth, and bytes* — and it makes distribution cheap enough that sharded workloads behave as if it did.

---

*Next: read `ARCHITECTURE.md` for the component-level design, then `phases/README.md` for the execution protocol.*
