# PHASE 31 — Gang Scheduling & Topology-Aware Placement

| | |
|---|---|
| **Stage** | 5 — Scheduling & Orchestration |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 22, 30 |
| **Blocks** | 33, 35, 36, 37, 39, 41 |
| **Risk** | 🟠 Medium — a deadlocked gang wedges capacity; bad topology rules strand nodes |
| **Blast radius** | All distributed workloads |
| **Architecture refs** | `ARCHITECTURE.md#l71-the-scheduling-pipeline`, `#l73-topology-aware-scheduling`, `ULTIMATE-PLAN.md#82` |

---

## 🎯 MISSION

Make distributed jobs work correctly and *fast*: **all-or-nothing (gang) scheduling** so a 16-GPU job never runs 15 pods while waiting forever for the 16th, and **topology-aware placement** so those 16 GPUs land as close together on the fabric as possible — turning Phase 22's measured 8–14 % placement advantage into an automatic property of every job.

> 💡 **WHY gang scheduling is non-negotiable for distributed training.** A PyTorch DDP job with 16 ranks initializes with a collective barrier: rank 0 waits for all 16 before any compute starts. If the scheduler places 15 pods and the 16th cannot be placed, those 15 GPUs are **allocated, idle, and blocking other jobs indefinitely**. Two such jobs deadlock each other. Without gang scheduling, a busy cluster degrades into mutual starvation — and the symptom (jobs "running" at 0 % GPU utilization) is easy to misdiagnose for weeks.

> 💡 **WHY topology awareness is worth a phase.** Phase 22 measured it: ranks spread across leaf domains are 8–14 % slower at 16 nodes than ranks in one domain. On a 100-GPU cluster running continuously, 10 % is ten GPUs' worth of capacity, recovered by placement decisions that cost nothing. This is Law III — *Topology Awareness Is Not Optional*.

---

## ✅ PREFLIGHT

```bash
# Kueue admitting workloads (Phase 30)
kubectl get clusterqueues
bash tools/scheduling/quota-report.sh

# 📊 The placement delta measured in Phase 22 — the justification for this work
grep -A5 "topology-aware vs. random" evidence/phase-22/handoff.md

# Topology labels present on every node (Phase 14)
kubectl get nodes -L nexus.io/leaf-domain,topology.kubernetes.io/rack,nexus.io/rack-unit

# The fabric map from Phase 03
yq '.leaf_domains' network/design/ip-plan.yaml
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/scheduling/
  coscheduling/
    scheduler-plugins-values.yaml
    podgroup-defaults.yaml
  topology/
    topology.yaml                   # Kueue Topology CR: the fabric hierarchy
    resourceflavor-patches.yaml     # attach topology to flavors
policies/defaults/
  inject-podgroup.yaml              # auto-create PodGroups from job specs
  require-gang-for-distributed.yaml
tools/scheduling/
  placement-report.sh               # where did this job's ranks land?
  gang-deadlock-check.sh            # 🚨 detect partially-placed gangs
  bench-placement.sh                # 📊 topology benefit, re-measured
docs/user/distributed-placement.md
observability/rules/gang-alerts.yaml
evidence/phase-31/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Note |
|---|---|---|
| scheduler-plugins | `v0.30.6` | Coscheduling plugin; must match the K8s minor version |
| Kueue | `v0.10.1` | Phase 30 pin; provides TAS |

---

## 📋 TASKS

### Task 1 — Choose the gang mechanism

Three mechanisms exist and they overlap. Pick deliberately.

| Mechanism | How | Pros | Cons |
|---|---|---|---|
| **A. Kueue workload admission** | Kueue admits the whole Workload or none of it | ✅ Already deployed; quota-aware; no second scheduler | Admission ≠ placement — Kueue can admit a job kube-scheduler then cannot place |
| **B. scheduler-plugins Coscheduling** | A `PodGroup` with `minMember`; the scheduler holds pods until all can bind | ✅ True placement-level gang | Requires a second scheduler profile; more moving parts |
| **C. Operator-native (Volcano, Ray, Trainer)** | Each framework's own gang logic | Framework-integrated | Fragmented; different semantics per framework |

**The correct answer is A + B together**, and understanding why is the core insight of this phase:

```
Kueue answers:      "Does this tenant have QUOTA for 16 GPUs?"          → admission
Coscheduling answers: "Can 16 pods actually BIND to real nodes now?"    → placement

Kueue alone: admits a 16-GPU job when quota exists, but if the free GPUs are
fragmented across nodes in a way that cannot satisfy the pods' constraints,
kube-scheduler places some and leaves others Pending. THE GANG IS BROKEN.
```

> ⚠️ **This is the failure mode Phase 30's troubleshooting table ended on:** "A workload is admitted but pods stay Pending." Kueue's admission is necessary and not sufficient. Deploy coscheduling to close the gap.

**Deploy scheduler-plugins as a second scheduler**, not as a replacement for the default:
```yaml
# Pods opt in with:  schedulerName: nexus-scheduler
# Default workloads keep using kube-scheduler — smaller blast radius.
```

---

### Task 2 — PodGroups and automatic injection

```yaml
apiVersion: scheduling.x-k8s.io/v1alpha1
kind: PodGroup
metadata: { name: train-job-abc, namespace: tenant-research }
spec:
  minMember: 16                     # all-or-nothing threshold
  scheduleTimeoutSeconds: 300       # ⚠️ the deadlock escape hatch
```

**`inject-podgroup.yaml`** — users should never write a PodGroup by hand. Derive it:

| Source | `minMember` |
|---|---|
| `batch/v1 Job` with `completions`/`parallelism` | `parallelism` |
| `PyTorchJob` | Σ replicas across all replica specs |
| `RayJob` | head + `minReplicas` of each worker group |
| `MPIJob` | launcher + workers |
| `LeaderWorkerSet` | size of one group |

> ⚠️ **`scheduleTimeoutSeconds` is the anti-deadlock guard and it is mandatory.** Without it, a gang that can never be satisfied waits forever holding its queue position. With it, the gang is rejected back to the queue after 5 minutes and the capacity is released. Set it to ~2× your observed p99 placement time, never unbounded.

**`minMember` vs. elastic jobs:** for frameworks that support scaling (Ray, elastic PyTorch), set `minMember` to the *minimum viable* size, not the desired size. The job starts as soon as it can run at all and grows as capacity appears — far better utilization than waiting for the full allocation. Phase 33 builds on this.

---

### Task 3 — 🎯 The topology model

Define the fabric hierarchy Kueue's Topology-Aware Scheduling will optimize against.

```yaml
apiVersion: kueue.x-k8s.io/v1alpha1
kind: Topology
metadata: { name: nexus-fabric }
spec:
  levels:                           # ⚠️ ORDER MATTERS: broadest → narrowest
    - nodeLabel: "topology.kubernetes.io/zone"      # room / power domain
    - nodeLabel: "topology.kubernetes.io/rack"      # rack
    - nodeLabel: "nexus.io/leaf-domain"             # leaf switch — THE important one
    - nodeLabel: "kubernetes.io/hostname"           # node
```

**Why `leaf-domain` is the level that matters:** the Clos fabric from Phase 03 means two nodes on the same leaf switch communicate at full line rate with one hop. Two nodes on different leaves traverse leaf→spine→leaf, three hops, sharing oversubscribed uplinks with every other cross-leaf flow. **The leaf boundary is where the bandwidth cliff is**, and it is the level TAS should try hardest to respect.

**Attach it to flavors and let users request it:**
```yaml
# In the ResourceFlavor
spec:
  topologyName: nexus-fabric
---
# In the job — two levels of strictness
metadata:
  annotations:
    # PREFERRED: pack as tightly as possible, but run anywhere rather than wait
    kueue.x-k8s.io/podset-preferred-topology: "nexus.io/leaf-domain"
    # REQUIRED: do not run at all unless all ranks fit in one leaf domain
    kueue.x-k8s.io/podset-required-topology: "nexus.io/leaf-domain"
```

**The decision table users need:**

| Job shape | Annotation | Rationale |
|---|---|---|
| ≤ 8 ranks, communication-heavy | **required** leaf-domain | Fits easily; the win is large |
| 16–32 ranks, communication-heavy | **preferred** leaf-domain, **required** rack | May not fit one leaf; do not block on it |
| > 32 ranks | **preferred** rack | Will span leaves regardless; minimize spine crossings |
| Embarrassingly parallel (no collectives) | None | Placement does not matter; spreading may even help I/O |
| Inference replicas | **anti**-affinity across racks | Availability beats locality here |

> ⚠️ **`required` topology can make a job unschedulable forever.** If a tenant asks for 16 GPUs required-in-one-leaf-domain, and no leaf domain has 16 free, the job waits — correctly, but invisibly. Pair every `required` annotation with the `KueueInadmissibleWorkload` alert from Phase 30 and a clear message in `describe workload`. **Default users to `preferred`.**

---

### Task 4 — Default the right behavior

Users should get good placement without asking. Mutation rules:

```
IF a workload is distributed (has a PodGroup, or is a PyTorchJob/RayJob/MPIJob)
   AND has no topology annotation:
     → inject podset-preferred-topology: nexus.io/leaf-domain   (≤ 32 ranks)
     → inject podset-preferred-topology: topology.kubernetes.io/rack  (> 32 ranks)

IF a workload is single-pod:
     → no topology annotation (nothing to co-locate)
```

Combined with Phase 22's NCCL injection and Phase 30's queue injection, a user's minimal job spec now automatically gets: the right queue, the right quota, gang semantics, tight placement, and correct NCCL configuration. **That composition is the platform.**

---

### Task 5 — 📊 Verify the benefit (re-measure, don't assume)

**`tools/scheduling/bench-placement.sh`** — repeat Phase 22's experiment, now through the real scheduling path.

| Configuration | 16-rank NCCL busbw | Training step time | GPU-hours per epoch |
|---|---|---|---|
| Random placement (topology off) | | | |
| **`preferred` leaf-domain** | | | |
| **`required` leaf-domain** | | | |
| Delta | | | |

📊 **Also measure the cost of topology constraints:**

| Metric | Random | Preferred | Required |
|---|---|---|---|
| Median time-to-admission | | | |
| p99 time-to-admission | | | |
| Jobs that never scheduled in 30 min | 0 | | ⚠️ expect > 0 |
| Cluster GPU utilization during the test | | | |

> 💡 **This table is the honest trade.** `required` gives the best runtime performance and the worst schedulability. `preferred` captures most of the benefit at almost no scheduling cost. Publish both numbers so users can choose knowingly — and so you can defend the default.

---

### Task 6 — 🚨 Deadlock detection and alerts

**`tools/scheduling/gang-deadlock-check.sh`** — the safety net.

```
Detect: a PodGroup where 0 < scheduled_pods < minMember, persisting > scheduleTimeoutSeconds
Report: which pods are placed, which are Pending, and WHY the pending ones cannot bind
Action: alert; the coscheduling plugin should have released them — if it did not, that is a bug
```

| Alert | Threshold | Severity |
|---|---|---|
| `GangPartiallyScheduled` | 0 < placed < minMember for > 5 min | 🔴 Critical — capacity is wedged |
| `GangScheduleTimeout` | PodGroup timed out | 🟡 Info — working as designed, but track the rate |
| `GangTimeoutRateHigh` | > 20 % of gangs timing out | 🟠 The cluster is too fragmented or quotas are wrong |
| `TopologyRequiredUnschedulable` | A `required` job pending > 30 min | 🟠 Tell the user to relax it |
| `FragmentationHigh` | Free GPUs exist but no leaf domain has ≥ 8 contiguous | 🟡 Defrag signal |
| `SchedulerPluginsDown` | Second scheduler unavailable | 🔴 Gang jobs cannot schedule |

> ⚠️ **`GangPartiallyScheduled` is the alert that saves the cluster.** If it ever fires for more than a few minutes, the gang mechanism is not working, and every minute it persists is idle GPUs blocking other tenants. Page on it.

**Fragmentation** is the second-order problem: after hours of mixed workloads, free GPUs are scattered one-per-node and no gang can be placed even though "40 GPUs are free." Track it:
```
fragmentation_index = 1 − (largest_placeable_gang / total_free_gpus)
```
Alert when it stays high; Phase 33's preemption/defragmentation work is the response.

---

### Task 7 — The user guide

**`docs/user/distributed-placement.md`**

```markdown
# How your distributed job gets placed

## What happens automatically
· Your job is treated as a GANG: all N pods start together, or none do.
· Your ranks are packed onto nodes sharing a leaf switch when possible.
· NCCL is configured for the fabric you landed on.
You do not configure any of this.

## When to override
    # I need all ranks on one leaf switch or the job is not worth running:
    annotations: { kueue.x-k8s.io/podset-required-topology: "nexus.io/leaf-domain" }
⚠️ This can mean waiting a long time — or forever, if your job is bigger than
   any single leaf domain. Check the capacity table below before using it.

    # My job doesn't do collectives; place it anywhere:
    annotations: { nexus.io/topology: "none" }

## Leaf-domain capacity (update per cluster)
| Leaf domain | GPUs | Largest single-domain job |
|---|---|---|
| leaf-01 | 24 | 24 |
| leaf-02 | 24 | 24 |
Asking for `required` with more ranks than any domain holds = never scheduled.

## My gang job is stuck
    kubectl get podgroup -n <ns>
    kubectl describe workload <name> -n <ns>
Look for: "0/16 pods scheduled". Causes:
 · Not enough free GPUs anywhere → wait
 · Enough free GPUs but scattered (fragmentation) → wait, or relax topology
 · `required` topology that cannot be satisfied → relax it
 · Your quota does not cover the whole gang → Phase 30's guide

## Elastic jobs get capacity sooner
If your framework supports it (Ray, elastic PyTorch), set a minimum size.
You start as soon as the minimum is available and grow from there.
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | scheduler-plugins running as a second scheduler | `kubectl get pods -n scheduler-plugins` | Running |
| **A2** | Default kube-scheduler still handles non-gang workloads | Submit a plain pod | Scheduled by default |
| **A3** | PodGroup auto-injected for every distributed job type | Submit each type | Injected, correct `minMember` |
| **A4** | **A 16-pod gang schedules all-or-nothing** | Submit when only 15 GPUs are free | 0 pods placed |
| **A5** | The same gang schedules fully when capacity appears | Free a GPU | All 16 placed |
| **A6** | **Two competing gangs do not deadlock** | Submit 2× (N/2+1)-GPU gangs | One runs, one queues; neither wedges |
| **A7** | `scheduleTimeoutSeconds` releases an unsatisfiable gang | Submit an impossible gang | Released after the timeout |
| **A8** | Topology CR levels match the physical fabric | Compare to Phase 03 | Matches |
| **A9** | `preferred` topology packs ranks into one leaf domain when possible | `placement-report.sh` | Packed |
| **A10** | `preferred` still schedules when packing is impossible | Fragment the cluster, submit | Schedules |
| **A11** | `required` refuses to spread | Test | Waits instead |
| **A12** | 📊 **Topology benefit re-measured through the real path** | `bench-placement.sh` | Table complete |
| **A13** | 📊 The schedulability cost of `required` is quantified | Same bench | Quantified |
| **A14** | Topology annotations auto-injected by rank count | Submit without | Correct injection |
| **A15** | Single-pod workloads get no topology constraint | Submit | None |
| **A16** | 🚨 `GangPartiallyScheduled` fires on an induced partial placement | Induce | Fires |
| **A17** | `placement-report.sh` shows rank→node→leaf mapping | Run on a live job | Clear |
| **A18** | Fragmentation index computed and exported | Query Prometheus | Present |
| **A19** | Elastic minimum works (job starts below full size) | Submit an elastic Ray job | Starts |
| **A20** | Gang + Kueue quota interact correctly (no double counting) | Submit | Correct |
| **A21** | 📊 Admission latency did not regress from Phase 30's B11 | Re-run `bench-admission.sh` | Within 20 % |
| **A22** | User guide includes the real leaf-domain capacity table | Read it | Accurate |

---

## ↩️ ROLLBACK

```bash
# Remove gang semantics — jobs schedule pod-by-pod again (degraded, but functional)
kubectl delete cpol inject-podgroup require-gang-for-distributed
# Existing PodGroups become inert.

# Remove topology preferences — placement becomes arbitrary
kubectl delete cpol inject-topology-annotation

# Full removal of the second scheduler — ⚠️ pods with schedulerName: nexus-scheduler
# will stay Pending forever. Drain them first.
kubectl get pods -A -o json | jq -r '.items[] | select(.spec.schedulerName=="nexus-scheduler") | ...'
helm uninstall scheduler-plugins -n scheduler-plugins
```

> ⚠️ **The same hazard as Phase 30's rollback:** removing a scheduler while pods reference it strands them. Always enumerate and drain first.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Gang never schedules despite free GPUs | Fragmentation — free GPUs scattered | Check the fragmentation index; relax topology; Phase 33 defrag |
| Some pods placed, rest Pending, indefinitely | Coscheduling not active for these pods (`schedulerName` missing) | Verify the injection covers this workload type |
| `minMember` wrong | Injection rule does not understand this job type | Add a rule for it; test |
| Job admitted by Kueue, gang times out repeatedly | Quota exists but nodes cannot satisfy the pod constraints | Mismatch between flavor selectors and real node labels |
| `required` topology job never runs | Larger than any domain | This is A11 working. Tell the user; publish the capacity table. |
| Placement ignores topology | Flavor missing `topologyName`; or labels missing on nodes | Check both |
| Ranks land in one domain but performance is unchanged | The bottleneck is not the fabric | Profile it (Phase 51) — do not tune blind (Law VIII) |
| Admission latency regressed badly | TAS computation cost at scale | Reduce topology levels; measure again |
| Second scheduler down, gang jobs stuck | `SchedulerPluginsDown` | Restart; gang jobs cannot proceed without it |
| Gang timeout rate climbing over weeks | Growing fragmentation | Track it; it is a capacity/defrag signal, not a scheduler bug |

---

## 🚫 DO NOT

- **Do not** rely on Kueue admission alone for gang semantics.
- **Do not** set `scheduleTimeoutSeconds` to unbounded.
- **Do not** default users to `required` topology.
- **Do not** replace the default scheduler; run a second profile.
- **Do not** uninstall the second scheduler with pods referencing it.
- **Do not** set `minMember` to the desired size for elastic jobs — use the minimum viable size.
- **Do not** claim a topology benefit you have not re-measured through the real scheduling path.
- **Do not** implement preemption, checkpointing, or defragmentation here. Phase 33.

---

## 📤 HANDOFF

`evidence/phase-31/handoff.md` must state:

1. **📊 The re-measured topology benefit** (busbw and step time) and the schedulability cost — the two-sided table.
2. **The topology hierarchy in force** and the leaf-domain capacity table users need.
3. **Which mechanism gangs which workload type**, and any type not covered.
4. **The `scheduleTimeoutSeconds` value** and the observed gang timeout rate.
5. **🧪 The two-competing-gangs deadlock test result** — proof the cluster cannot wedge.
6. **The current fragmentation index** and its trend — Phase 33's input.
7. **📊 Whether admission latency regressed** from B11.
8. **Default annotations injected**, so Phase 46's golden paths document them accurately.

---

## ➡️ NEXT

**[PHASE-32 — Power-Aware Scheduling & Thermal Management](PHASE-32.md)** — the constraint no cloud has to think about: your building's electrical capacity is a scheduling dimension.
