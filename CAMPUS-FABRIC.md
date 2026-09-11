# CAMPUS FABRIC — The Harvest Plane

### Turning every PC in every DIU classroom and lab into schedulable capacity, without buying a single machine and without a student ever noticing

> **Companion to:** `ULTIMATE-PLAN.md` (why) · `ARCHITECTURE.md` (how the pieces fit) · `phases/README.md` (execution)
> **Status:** Design of record for Plane B. Supersedes any earlier assumption that every NEXUS node is racked, dedicated, and RDMA-attached.

---

## Table of Contents

1. [Why This Document Exists](#1-why-this-document-exists)
2. [The Two-Plane Architecture](#2-the-two-plane-architecture)
3. [Physics of a Borrowed Computer](#3-physics-of-a-borrowed-computer)
4. [The Availability Model](#4-the-availability-model)
5. [The 1 GbE Reality — What Runs and What Never Will](#5-the-1-gbe-reality--what-runs-and-what-never-will)
6. [Harvest Modes & the Node Lifecycle](#6-harvest-modes--the-node-lifecycle)
7. [The Backend — Seven Mechanisms That Eliminate Waste](#7-the-backend--seven-mechanisms-that-eliminate-waste)
8. [Locality Domains & the Data Path](#8-locality-domains--the-data-path)
9. [Consent, Safety & the Human Contract](#9-consent-safety--the-human-contract)
10. [Capacity Math for DIU](#10-capacity-math-for-diu)
11. [Harvest SLOs & Gates](#11-harvest-slos--gates)
12. [What This Answers in the ClusterOne Review](#12-what-this-answers-in-the-clusterone-review)

---

## 1. Why This Document Exists

The original NEXUS plan optimizes for one shape of machine: a node we own outright, that lives in a rack, that has a 25–100 GbE RDMA NIC, and that runs until we decide to stop it.

DIU already owns several hundred machines that are **none of those things**. They sit in classrooms and labs. They have integrated or consumer GPUs. They are on the campus 1 GbE access network. They belong to a lab whose scheduled classes outrank us absolutely. And for roughly **two-thirds of every week they are powered off or sitting at a login screen doing nothing.**

Treating those machines as "small NEXUS nodes" is the mistake that kills this class of project. They are a *different resource with different physics*, and they need a scheduler that reasons about **time and churn**, not just cores and bytes. That is what the Harvest Plane is.

The prize is not subtle. A pilot built on 6 dedicated GPU nodes has a hard ceiling of 6 GPUs. A campus harvest fabric across ~20 labs reaches **150–400 machines with zero procurement**, and it reaches them in weeks rather than quarters — because the hardware is already bought, already powered, already cooled, and already cabled.

The cost is honesty about what such a fabric cannot do. It cannot run tightly-coupled multi-node training. It never will, over 1 GbE. Everything in this document is built around routing that one class of work to the Core Plane and routing the other nine classes — which are the overwhelming majority of what a university actually runs — to the free capacity.

---

## 2. The Two-Plane Architecture

NEXUS is now **one control plane governing two compute planes**. A single Kubernetes cluster, one GitOps repo, one identity system, one queue system — two radically different pools underneath.

```
                        ┌──────────────────────────────────────┐
                        │      CONTROL PLANE (3 nodes)         │
                        │  etcd · Kueue · Argo CD · Keycloak   │
                        │  Availability Oracle · Harvest Ctrl  │
                        └───────────────┬──────────────────────┘
                                        │
                 ┌──────────────────────┴────────────────────────┐
                 ▼                                               ▼
  ┌──────────────────────────────┐            ┌────────────────────────────────────┐
  │   PLANE A — CORE             │            │   PLANE B — CAMPUS HARVEST         │
  │   (Phases 01–56, unchanged)  │            │   (Phases *B, this document)       │
  ├──────────────────────────────┤            ├────────────────────────────────────┤
  │ 4–8 racked nodes             │            │ 150–400 classroom / lab PCs        │
  │ Dedicated, 24/7              │            │ Borrowed, timetable-bounded        │
  │ 25–100 GbE RoCEv2            │            │ Campus 1 GbE, shared uplink        │
  │ Ceph / Mayastor / NVMe       │            │ RAM-disk + lab-local cache only    │
  │ Owned failure domain         │            │ Human-owned; the human always wins │
  │ Node lifetime: months        │            │ Node lifetime: hours               │
  ├──────────────────────────────┤            ├────────────────────────────────────┤
  │ RUNS: multi-node training,   │            │ RUNS: HPO sweeps, batch inference, │
  │ RDMA collectives, all state, │            │ preprocessing, CI, rendering,      │
  │ storage, registry, DBs,      │            │ single-node training, simulation,  │
  │ long-lived services          │            │ low-comms distributed training     │
  └──────────────────────────────┘            └────────────────────────────────────┘
```

**The load-bearing rule:** *state lives in the Core, work happens wherever it is cheapest.* No harvest node ever holds the only copy of anything. A harvest node dying is not an incident; it is Tuesday morning at 8am, forty times over.

### 2.1 Why one cluster and not two

| Option | Verdict |
|---|---|
| Two separate clusters (core K8s + campus BOINC/HTCondor) | ❌ Two identity systems, two quota systems, two sets of runbooks, and no way to burst one workload across both. Doubles ops burden — the exact thing the ClusterOne review flagged. |
| One cluster, one pool | ❌ A gang-scheduled RDMA job would land on a classroom PC and hang. Unacceptable. |
| **One cluster, two labeled/tainted pools, one queue system** | ✅ Kueue already models exactly this: separate `ClusterQueue`s in a shared `Cohort`, with per-pool `ResourceFlavor`s. One `kubectl`, one dashboard, one on-call. |

Harvest nodes carry `nexus.io/plane=harvest` and the taint `nexus.io/harvest=true:NoSchedule`. **Nothing lands there without explicitly tolerating churn.** That single taint is the safety interlock that keeps the two planes from poisoning each other.

---

## 3. Physics of a Borrowed Computer

The same treatment §4 of `ULTIMATE-PLAN.md` gives to memory and network, applied to the new constraint: **we do not own the machine's time.**

### 3.1 The Eviction Boundary

| Event | Notice we get | Frequency per node | Our required response |
|---|---|---|---|
| Timetable class begins | **Hours** (known in advance) | 2–5 × weekday | Drain gracefully, finish or checkpoint, reboot to Windows before the bell |
| Unscheduled human sits down | **0–15 s** (input detected) | Unpredictable | Yield the machine in < 10 s, checkpoint what we can, lose at most one interval |
| Power cut / someone hits the switch | **None** | Weekly across a fleet this size | Lose the node's in-flight interval; requeue from last checkpoint |
| Lab-wide shutdown policy | Minutes | Nightly in some labs | Drain the whole locality domain together |
| Network/uplink saturation by teaching traffic | Seconds | Class hours | Shed our own I/O first; we are always the lower priority |

**The derived law: no unit of work may assume it will finish.** Every harvest workload is either (a) shorter than the predicted window, (b) checkpointed at an interval shorter than the predicted window, or (c) idempotent and cheap enough to re-run. There is no fourth option, and the admission controller enforces this rather than trusting users.

### 3.2 Work Loss — the metric that governs the whole design

```
W = wasted_node_seconds / harvested_node_seconds

wasted = compute spent on work that was discarded because a node
         was reclaimed before the result was durable
```

A naive volunteer-computing setup on a campus timetable runs at **W ≈ 35–60 %** — over a third of the electricity and a third of the wall-clock produces nothing. That is the "resource loss" this design exists to kill.

| Design state | Expected W | Why |
|---|---|---|
| No checkpointing, no availability model | 40–60 % | Every eviction discards the whole job |
| Periodic checkpoint only | 15–25 % | Loses one interval, plus full re-read of inputs over 1 GbE |
| Checkpoint + local-first restore | 8–12 % | Restart is same-lab, warm cache |
| **+ Availability Oracle (deadline-aware placement)** | **< 5 % (target)** | Jobs are simply not placed where they cannot finish |

**Gate G15 sets W ≤ 5 % measured over a rolling 7 days.** This is the single number that proves the backend works, and it is the number the ClusterOne comparison has no equivalent of.

### 3.3 What a harvest node is *not* allowed to be

- ❌ A storage replica (Ceph OSD, Mayastor target). It vanishes; three-way replication over a shared 1 GbE uplink would be a self-inflicted denial of service.
- ❌ A control-plane or etcd member.
- ❌ A rank in a gang-scheduled RDMA job.
- ❌ A holder of secrets, tenant datasets at rest, or PII. It is physically accessible by hundreds of students; assume the disk is readable and the console is reachable.
- ❌ A host for anything with a Persistent Volume that matters.

---

## 4. The Availability Model

This is the component that does not exist in any competing plan, and it is where the performance comes from.

### 4.1 The Availability Oracle

A controller in the Core Plane that answers one question, per node, continuously:

> **"How many seconds of uninterrupted compute can I promise on this machine, starting now, at what confidence?"**

Inputs:

| Signal | Source | Weight |
|---|---|---|
| Room timetable (class start/end, exam weeks, holidays) | Registrar export → `inventory/campus/timetable.yaml` | Dominant — this is *known future*, not a guess |
| Lab-owner declared harvest windows | Per-lab agreement (Phase 04B) | Hard bound; never exceeded |
| Historical reclaim events per room, per hour-of-week | Prometheus, 4+ weeks | Corrects the timetable for reality (labs used off-schedule) |
| Live occupancy (input activity, session state) | Harvest agent heartbeat | Immediate override |
| Node health (thermals, SMART, prior XIDs) | node-exporter / DCGM | Downgrades confidence |

Output, per node: `predictedFreeSeconds` at p50 and p10, published as a node label and consumed at admission time.

### 4.2 Deadline-aware admission

The scheduler's placement question changes from *"does this fit?"* to **"does this fit, and will it finish?"**

```
admit(job, node) requires:
    job.estimatedRuntime           ≤ node.predictedFreeSeconds(p10)
  OR
    job.checkpointInterval × 1.5   ≤ node.predictedFreeSeconds(p10)

and always:
    job.checkpointInterval ≤ 900 s        # 15-min ceiling, no exceptions
    job.tolerations includes nexus.io/harvest
```

A 6-hour training run submitted at 07:00 on a Monday is **not** placed on a lab PC that loses its machine at 08:00 — it queues for the Core Plane or for the 18:00 window. This one rule is responsible for most of the drop from W≈20 % to W<5 %.

### 4.3 Confidence tiers

| Tier | `predictedFreeSeconds` p10 | Workload admitted |
|---|---|---|
| **Gold** | > 8 h (nights, weekends, holidays) | Single-node training, long sweeps, anything checkpointed |
| **Silver** | 1–8 h (scheduled gaps between classes) | Batch inference, preprocessing, medium sweep trials |
| **Bronze** | 5–60 min (opportunistic idle during class hours) | CI jobs, short trials, embarrassingly-parallel shards, rendering tiles |
| **Blocked** | < 5 min, or lab in session, or exam week | Nothing. Node is cordoned. |

---

## 5. The 1 GbE Reality — What Runs and What Never Will

`ULTIMATE-PLAN.md §4.4` already proves the point with numbers: a 7 B model AllReduce over 1 GbE takes **~240 seconds per step**. That verdict stands and is not negotiable. What follows is the constructive half of it.

### 5.1 The honest capability table

| Workload class | On harvest nodes over 1 GbE | Why |
|---|---|---|
| Hyperparameter sweep (100s of 1-GPU trials) | ✅ **Ideal** | Zero inter-node traffic. Scales linearly to the whole campus. |
| Batch / offline inference | ✅ **Ideal** | Stream in, stream out; bandwidth per item is tiny |
| Dataset preprocessing, feature extraction, ETL shards | ✅ **Ideal** | Read-once, write-once, partitioned |
| Single-node training (fits one GPU) | ✅ Excellent | Checkpoint to lab cache; no cross-node traffic |
| CI / build farm | ✅ Excellent | Seconds-to-minutes, perfectly preemptible |
| Rendering, simulation, Monte Carlo | ✅ Excellent | Embarrassingly parallel by nature |
| Ray tasks with small objects (< 10 MB) | ✅ Good | Plasma store stays local; watch object spilling |
| **Low-communication distributed training** (Local-SGD / DiLoCo-style, sync every 100–500 steps) | ⚠️ **Viable — and strategically important** | Communication drops by 100–500×, which is exactly what turns a 240 s AllReduce into ~1 s of amortized cost per step. Restricted to same-lab placement. |
| Federated / branch-train-merge | ⚠️ Viable | Same reasoning; merge on the Core Plane |
| Spark/Dask **shuffle-heavy** ETL | ❌ No | Shuffle is bisection-bound; 1 GbE collapses |
| Data-parallel DDP/FSDP training | ❌ **Never** | §4.4. Core Plane only. |
| Multi-node MPI / tightly-coupled HPC | ❌ **Never** | Latency-bound; 1 GbE p99 is ~100× too slow |
| Distributed storage (Ceph OSD) | ❌ **Never** | §3.3 |

> **The design consequence:** we do not pretend the campus fleet is a supercomputer. We route to it the ~70 % of real university AI/ML work that is throughput-bound and independent, and we keep the ~30 % that is latency-bound on a small, correctly-built Core. Both planes stay honest.

### 5.2 The uplink is the real constraint, not the port

Every PC has its own 1 Gb access port. That is not where you die. You die at the **lab's uplink**, which is shared:

```
Lab of 30 PCs behind a 1 GbE uplink:
    33 Mb/s ≈ 4 MB/s per PC of sustained cross-lab bandwidth

A 2 GB container image × 30 PCs pulled from a central registry:
    60 GB / 1 Gb/s = 8 minutes of a fully saturated uplink
    ...during which the lab's own teaching traffic is degraded.
    Do this once and the lab revokes consent. Permanently.
```

**Therefore, three hard rules, enforced in code, not in documentation:**

1. **Never fetch the same bytes twice into a lab.** P2P image distribution (Spegel) and a lab-local dataset cache mean N PCs cost ~1× the bytes, not N×.
2. **Uplink admission control.** The scheduler models each lab's uplink as a schedulable resource and will not admit work that pushes NEXUS's share past **40 % of uplink capacity during class hours / 70 % outside them**. Teaching traffic is never our peer; it is our superior.
3. **Egress shaping on every harvest node.** A per-node traffic class caps NEXUS at its allocation and yields instantly under contention. If we are ever the reason a lecture's video stutters, the whole programme is over.

---

## 6. Harvest Modes & the Node Lifecycle

### 6.1 Mode A — Scheduled Harvest (primary, ~90 % of yield)

Outside declared class hours, the room's PCs are woken and netbooted into an **ephemeral, diskless NEXUS node**.

```
 18:05  Availability Oracle: room CSE-402 enters its Gold window
        │
 18:05  Harvest Controller → Wake-on-LAN magic packet to 30 MACs
        │
 18:06  BIOS boot order: PXE first → iPXE chainload → Talos over HTTP
        │   ⚠️ The Windows install on the internal disk is never mounted,
        │      never modified, never even enumerated as a boot candidate.
        │
 18:07  Node boots into RAM (Talos, ~12 s), joins cluster, NFD labels it,
        │   Availability Oracle stamps predictedFreeSeconds=48600 (13.5 h)
        │
 18:08  Images warm from the lab-local Spegel peer, not the central registry
        │
 18:09  First workload pod running.  Cold-start budget: < 5 min room-wide.
        │
 ...    ~13 hours of Gold-tier compute
        │
 07:00  Oracle: window closes in 60 min → cordon, stop admitting
 07:30  Drain: SIGTERM → checkpoint → flush to lab cache → pods terminated
 07:45  Talos `shutdown`; next boot falls through PXE to Windows
 08:00  Student walks in, presses power, gets Windows. Nothing to notice.
```

**The critical property: we leave no trace.** No agent installed in Windows, no partition consumed, no registry key, no persistent change of any kind. Reverting the whole programme means changing one BIOS boot-order setting back. That reversibility is what makes lab owners say yes.

### 6.2 Mode B — Opportunistic Harvest (secondary, opt-in per lab)

During class hours, a PC sitting at a login screen with no input for **> 20 minutes** may be harvested at Bronze tier. Two implementations, and the choice is per-lab:

| Implementation | GPU access | Yield-to-human latency | Use when |
|---|---|---|---|
| **Reboot-to-harvest** (same netboot path, returns on WoL/power button) | Full, bare metal | ~90 s (a reboot) | Labs that are genuinely empty for long stretches |
| **In-Windows agent** (low-priority service, containerized runtime) | Reduced (WSL2/driver overhead ~10–25 %) | **< 5 s** (process suspend) | Labs where someone may walk in at any moment |

Mode B is **disabled by default**. It is enabled only per-lab, in writing, after Mode A has run cleanly for four weeks in that lab. Do not be clever here — the reputational cost of one interrupted class exceeds the value of every Bronze-tier hour we would ever harvest.

### 6.3 The node state machine

```
   OFFLINE ──WoL──► BOOTING ──join──► READY ──admit──► RUNNING
      ▲                │                │                 │
      │                │ (PXE fail)     │ oracle window    │ human detected
      │                ▼                │ closing          │ or class starts
      │            QUARANTINE           ▼                  ▼
      │           (report, skip)     CORDONED ────────► DRAINING
      │                                                    │
      └────────────────── shutdown ◄───────────────────────┘
                                          (checkpoint flushed, < 10 s from trigger)
```

Every transition emits a metric. `DRAINING → OFFLINE` p99 latency is Gate **G16** and its budget is **10 seconds** from the reclaim trigger to the machine being the human's again.

---

## 7. The Backend — Seven Mechanisms That Eliminate Waste

This section is the answer to "focus on the backend, make it fast, lose less." Each mechanism has an owning phase and a measured gate.

### M1 — Availability Oracle (Phase 14B)
Turns the timetable from tribal knowledge into a scheduling input. **Impact: W from ~20 % → ~8 %.** Without it, every other mechanism is patching damage the scheduler chose to cause.

### M2 — Fast eviction path (Phase 31B)
A three-stage yield: `SIGTERM` → in-process checkpoint hook → async flush. The pod does not wait for the flush to complete; a sidecar owns it and the node releases as soon as bytes are on local disk. **Budget: < 10 s to release, < 60 s to durable.**

### M3 — Tiered checkpointing with local-first restore (Phase 31B)
```
  checkpoint → tmpfs/local NVMe          (< 2 s, always)
             → lab-local cache peer      (async, < 60 s, survives node loss)
             → Core object store         (async, < 10 min, survives lab loss)

  restore    ← prefer same lab (LAN, ~1 s)
             ← then Core (uplink, seconds)
```
Restart is scheduled back into the **same locality domain** by preference (Law VI — data gravity). A restart that re-reads its inputs over the uplink has converted a cheap eviction into an expensive one.

### M4 — P2P content distribution (Phase 25B)
Spegel peer-to-peer image mirror per locality domain + torrent-style dataset seeding. **Turns an O(N) uplink cost into O(1).** This is the difference between a lab coming online in 4 minutes and in 40. Cold-start p95 < 5 min for a 30-PC room is Gate **G17**.

### M5 — Uplink-aware admission (Phase 03B)
Each lab's uplink is a first-class schedulable resource with a hard ceiling, modeled exactly like the power budget in Phase 02. Prevents the single failure mode most likely to get the project shut down by the network team.

### M6 — Right-sized work units (Phase 36B)
Sweep trials, inference batches, and ETL shards are automatically sized so their expected runtime lands inside the node's confidence tier. A 45-minute trial is split into three 15-minute checkpointed segments when the window is Silver. **The work adapts to the machine, not the reverse.**

### M7 — Harvest efficiency accounting (Phase 33B)
Every node-second is classified: `useful` / `wasted` / `overhead` / `idle-unharvested`. Published per-lab and per-tenant. What is not measured regresses silently — and `idle-unharvested` is the line item that tells us where the next 50 machines are.

---

## 8. Locality Domains & the Data Path

### 8.1 The domain hierarchy

```
campus
  └── building        (e.g. "Daffodil Tower")
       └── floor
            └── lab / classroom        ◄── THE scheduling unit for harvest work
                 └── node
```

A **lab is the harvest fabric's failure domain, locality domain, and consent domain simultaneously.** It shares an uplink, a timetable, a power circuit, a physical door, and an owner. Kueue Topology-Aware Scheduling is configured with `lab` as the tightest domain; any job with inter-node traffic is placed entirely within one lab or it is not placed at all.

### 8.2 The cache seed

Each lab designates **one machine as its cache seed** — ideally the most stable one (a lab-technician workstation or the instructor console) with the largest disk:

| Role | Detail |
|---|---|
| Spegel peer priority | Seeds container images to the rest of the room |
| Dataset cache | JuiceFS/local cache of hot datasets, pinned by the dataset warmer |
| Checkpoint landing zone | M3's tier-2 target — survives the loss of any single harvest node |
| Metrics relay | Aggregates the room's telemetry into one uplink stream instead of 30 |

If the seed is unavailable the lab still works; it just pays uplink cost. The seed is an optimization, never a dependency — **no single borrowed machine may be load-bearing.**

### 8.3 Data classification on the harvest plane

| Data | Allowed on a harvest node? | Mechanism |
|---|---|---|
| Container images (public/internal) | ✅ | Spegel, cleared on reboot (RAM/ephemeral) |
| Public or de-identified training datasets | ✅ | Lab cache, read-only, TTL'd |
| Job checkpoints | ✅ transiently | Encrypted at rest, flushed to Core, wiped on shutdown |
| Tenant private datasets | ⚠️ Only with tenant opt-in + encryption | Per-tenant policy flag; default **deny** |
| PII, student records, anything regulated | ❌ **Never** | Kyverno policy blocks the PVC/claim outright |
| Long-term secrets, tokens, kubeconfigs | ❌ **Never** | Node identity is short-lived and scoped (Phase 04B) |

Because the OS is diskless and ephemeral, **every power cycle is a guaranteed wipe.** That property does most of the security work for free, and it is why the netboot design was chosen over an installed agent.

---

## 9. Consent, Safety & the Human Contract

The technical risk in this project is moderate. The **political risk is the one that kills it.** A single professor who finds their lab PC busy, hot, or rebooted mid-lecture can end the programme by email.

### 9.1 The five promises (published, and enforced in code)

1. **The human always wins.** Any sign of human presence releases the machine within 10 seconds. There is no override, no "just let this job finish," no priority class that outranks a person standing at the keyboard.
2. **We leave no trace.** No software installed on the lab's OS, no disk partition consumed, no configuration changed except one BIOS boot-order entry that any technician can revert in 30 seconds.
3. **We never degrade the network.** NEXUS traffic is shaped below teaching traffic at all times, with a hard ceiling per lab uplink.
4. **We respect the machine.** GPU and CPU power caps at the efficiency knee, fan/thermal ceilings below the manufacturer's limit, and no sustained load in rooms without adequate ventilation. Nothing we run shortens a lab PC's life more than a student playing a game on it would.
5. **Any lab can leave, instantly.** One label change removes a lab from the fabric. No negotiation, no notice period, no questions.

### 9.2 What must be signed before a single machine is harvested

| Artifact | Signed by | Phase |
|---|---|---|
| Lab participation agreement (windows, opt-out, contact) | Lab owner / department head | 04B |
| Network integration approval (VLAN, uplink ceilings, shaping) | Campus IT / network team | 03B |
| Electrical sanity check per room (existing circuits, sustained load) | Facilities | 01B |
| Acceptable-use & data policy (what may run on shared machines) | Department + project lead | 04B |
| Student notice (posted in each participating lab) | Project lead | 04B |

> **Phase 01B is BLOCKED until at least one signed lab agreement exists.** Do not harvest a machine you were not given. This is not bureaucracy; it is the difference between a sanctioned campus service and an incident report.

### 9.3 The licensing question, restated

`ULTIMATE-PLAN.md` R-03 flags NVIDIA's GeForce driver EULA and its "datacenter deployment" restriction. The harvest plane **changes the shape of this risk but does not remove it**: lab PCs are workstations being used for university teaching and research on university premises, which is a materially different fact pattern from racking consumer cards in a datacenter. That is a factual distinction, **not legal advice.** The action remains as written: obtain a written institutional determination before scale-out (Phase 04B carries it now, jointly with Phase 01). Do not let the ambiguity block Mode A pilot work on CPU-only and integrated-GPU nodes, which the clause does not touch.

---

## 10. Capacity Math for DIU

Illustrative, to be replaced with surveyed reality in Phase 01B.

### 10.1 Weekly availability

```
Hours in a week                                        168
Less declared class hours (08:00–18:00, Sun–Thu)      −50
Less exam/holiday blackout amortized                   −8
                                                      ────
Gold + Silver window per machine                       110 h/week  (65 %)
× realistic wake/boot/drain overhead (−6 %)            103 h/week
```

### 10.2 Fleet yield at three survey outcomes

| Scenario | Machines | GPU-equipped | Effective GPU-h/week | Effective CPU-core-h/week |
|---|---|---|---|---|
| **Conservative** (5 labs, pilot) | 120 | 40 | 40 × 103 ≈ **4,100** | 120 × 8 × 103 ≈ **98,000** |
| **Expected** (12 labs) | 260 | 90 | ≈ **9,270** | ≈ **214,000** |
| **Full campus** (20+ labs) | 400 | 150 | ≈ **15,450** | ≈ **330,000** |

For comparison, the 6-GPU dedicated pilot described in the ClusterOne proposal yields `6 × 168 = 1,008` GPU-h/week at a theoretical 100 % utilization, and realistically ~600.

> **The conservative campus scenario delivers roughly 4–7× the GPU-hours of the dedicated pilot, with zero hardware purchase.** The GPU-hours are individually less capable and interruptible — which is precisely why every mechanism in §7 exists.

### 10.3 Cost

| Line item | Cost |
|---|---|
| Hardware | **$0** — already owned |
| Facility / cooling / electrical | **$0** — already provisioned for these machines |
| Network | **$0** — existing campus LAN |
| Incremental electricity (260 machines × 200 W avg × 103 h/wk × 52 × $0.12/kWh) | ≈ **$33,400/yr** at full expected scale |
| Core Plane (3 control + 2–4 GPU + 1 storage, Plane A) | Per `ULTIMATE-PLAN.md §6.3`, pilot subset ≈ **$18–28 k** |
| PXE/DHCP/WoL infrastructure (1 seed server, per-lab config) | ≈ **$1,500** |

**Effective cost per harvested GPU-hour: ≈ $0.06–0.10** (electricity + amortized seed infra). This is below every number in `ULTIMATE-PLAN.md §11.2`, because the capital cost was paid years ago by someone else.

---

## 11. Harvest SLOs & Gates

| Gate | Criterion | Verified in |
|---|---|---|
| **G15** | Work-loss ratio **W ≤ 5 %** over a rolling 7-day window at ≥ 100 harvest nodes | Phase 33B |
| **G16** | Human-presence → machine released: **p99 ≤ 10 s** (Mode B) / drain complete before window close 100 % of the time (Mode A) | Phase 31B |
| **G17** | Cold start of a 30-PC room: **p95 ≤ 5 min** from WoL to first workload pod running | Phase 25B |
| **G18** | **Zero** teaching-time incidents attributable to NEXUS over 30 consecutive days; NEXUS uplink share never exceeds its ceiling | Phase 52B |
| **G19** | Harvest yield ≥ **60 %** of theoretical availability (measured `useful / (useful+wasted+overhead+idle-unharvested)`) | Phase 52B |

Harvest-plane workload SLOs, extending `ULTIMATE-PLAN.md §5`:

| Workload class | SLO on the harvest plane |
|---|---|
| Sweep trial (Bronze) | Start < 90 s; ≥ 95 % of trials complete without re-execution |
| Batch inference | Throughput within 15 % of the same GPU on the Core Plane |
| Single-node training (Gold) | Progress loss per eviction ≤ 1 checkpoint interval (≤ 15 min) |
| CI job | Queue time < 30 s; retry rate < 2 % |
| Any harvest job | **Never** blocks or delays a Core Plane job of equal priority |

---

## 12. What This Answers in the ClusterOne Review

The 07 Sep 2026 comparative review scored NEXUS down on six dimensions. Five of them were fair, and the harvest plane is the direct response. The record, honestly kept:

| Their finding | Fair? | What changes |
|---|---|---|
| "Multi-year; 1/56 phases done" | ✅ Fair | The **Beachhead path** (`phases/README.md §8`) reaches real users on real campus hardware in ~8 weeks and ~11 phases. The 56-phase plan remains the long-horizon target, not the critical path. |
| "Assumes RDMA NICs / fabric not yet owned" | ✅ Fair | Plane B needs **no new hardware at all**. RDMA is now required only for the 4–8-node Core, not for the fleet. The 1 GbE constraint is designed *for*, not designed around. |
| "Needs switches, RDMA NICs, possibly new racks" | ✅ Fair | Campus capex → **$0**. Core pilot capex → ~$18–28 k, and it is deferrable past first users. |
| "High operational complexity" | ⚠️ Partly | Still higher than Slurm — that is the honest price of self-healing, quotas, and preemption. Mitigated by: one cluster not two, GitOps from day one, and the harvest agent being *stateless netboot* rather than fleet-managed software. |
| "No test report/manual yet" | ✅ Fair | Gates G15–G19 are machine-checkable and land in Stage C, not "far downstream." Each `*B` phase ships evidence in the same format as every other phase. |
| "GeForce EULA at scale" | ✅ Fair | §9.3. Unresolved, tracked, now owned by Phase 04B with a defined institutional-determination path — and the pilot's CPU/iGPU work proceeds unblocked meanwhile. |
| "Single anchor node = SPOF" (their own risk) | — | NEXUS has no anchor node. 3-node etcd, and the harvest plane assumes node death as normal operation rather than as an incident. |
| "Ceiling on growth beyond ~6 GPU nodes" (their own risk) | — | The harvest plane's ceiling is *the campus*, and it grows by signing a lab agreement, not by raising a purchase order. |

**The strategic statement:** ClusterOne is right that a 12-week path to real users matters more than an elegant 56-phase architecture. It is wrong that the only such path is 6 dedicated nodes. The campus already contains more idle compute than the department could afford to buy — the engineering problem is not acquiring capacity, it is **harvesting it without waste and without ever inconveniencing a human.** That problem is worth solving well, it is solvable in a semester, and this document is the design for it.

---

*Next: `phases/README.md §6.5` for the Track C phase index, then `phases/PHASE-01B.md` to begin the campus survey.*
