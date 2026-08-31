# PHASE 30 — Kueue: Quotas, Cohorts & Fair Share

| | |
|---|---|
| **Stage** | 5 — Scheduling & Orchestration |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 16, 19, 29 |
| **Blocks** | 31, 32, 33, 34, 35, 36, 37, 41 |
| **Risk** | 🟠 Medium — a wrong quota model blocks everyone; a missing one lets one user take the cluster |
| **Blast radius** | Every workload's admission |
| **Architecture refs** | `ARCHITECTURE.md#l7-scheduling--orchestration`, `#l72-the-kueue-resource-model`, `ULTIMATE-PLAN.md#10-multi-tenancy` |

---

## 🎯 MISSION

Install **Kueue** and build the quota model that turns a pile of GPUs into a *fairly shared* pool: ResourceFlavors describing the real heterogeneity of your hardware, ClusterQueues per tenant with nominal quotas, a Cohort that lets idle capacity be borrowed, and preemption rules that reclaim it. The result: **the cluster runs at high utilization AND every tenant gets their guaranteed share back within minutes when they need it.**

> 💡 **WHY a queueing layer above the Kubernetes scheduler.** kube-scheduler places pods one at a time, first-come-first-served, with no notion of a job, a budget, or a fair share. Given 100 GPUs and one user submitting 500 pods, kube-scheduler will happily give them all 100 GPUs and leave everyone else pending forever. Kueue adds job-level admission: a workload waits in a queue until its *whole* resource request can be satisfied *within its tenant's quota*, then is released to the scheduler. This is the difference between a cluster and a free-for-all.

> ⚠️ **The tension this phase resolves.** Static quotas guarantee fairness but waste capacity (a tenant's idle GPUs sit unused). No quotas maximize utilization but destroy fairness. Kueue's **cohort borrowing + preemption** gives both: borrow freely when others are idle, give it back when they return. Getting the preemption policy right is the whole game.

---

## ✅ PREFLIGHT

```bash
# DRA GPU allocation works (Phase 19)
kubectl get deviceclasses
kubectl get resourceslices | head

# Tenancy exists (Phase 16 Capsule tenants / namespaces)
kubectl get tenants

# Node labels for flavors (Phase 14)
kubectl get nodes -L nexus.io/gpu.model,nexus.io/pool,nexus.io/leaf-domain

# Storage gate passed
grep -c "☑" gates/G7-storage.md
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/scheduling/kueue/
  kueue-values.yaml
  resourceflavors.yaml              # 🎯 your real hardware heterogeneity
  cohort.yaml
  clusterqueues/                    # one per tenant
  localqueues/                      # one per namespace
  workloadpriorityclasses.yaml
  admission-checks.yaml
policies/defaults/
  require-queue-label.yaml          # Kyverno: no job escapes the queue
  inject-default-queue.yaml         # mutation: usability
tools/scheduling/
  quota-report.sh                   # who has what, who is borrowing
  queue-simulate.sh                 # 🧪 dry-run a quota model against real history
  bench-admission.sh                # 📊 B11 scheduling latency
docs/user/submitting-jobs.md
observability/rules/kueue-alerts.yaml
evidence/phase-30/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Note |
|---|---|---|
| Kueue | `v0.10.1` | Must support DRA-aware quota if you rely on it |
| Kubernetes | `1.34.x` | Phase 12 pin |

> ⚠️ **Check Kueue's DRA support level for your version.** DRA resource accounting in Kueue has been evolving. If your version cannot count `ResourceClaims` toward quota, you must **additionally** account GPUs via an extended resource or a per-flavor node count, or a tenant can bypass GPU quota entirely by using DRA claims. Determine this empirically in Task 2 and record the answer — it is the single most important compatibility fact in this phase.

---

## 📋 TASKS

### Task 1 — ResourceFlavors: describe your actual heterogeneity

A ResourceFlavor is "a kind of capacity." Users request a flavor; Kueue counts quota per flavor.

```yaml
apiVersion: kueue.x-k8s.io/v1beta1
kind: ResourceFlavor
metadata: { name: gpu-rtx4090 }
spec:
  nodeLabels:
    nexus.io/gpu.model: "NVIDIA-GeForce-RTX-4090"
    nexus.io/pool: "training"
  # Tolerate the pool taint so admitted work can land here
  tolerations:
    - key: nexus.io/pool
      value: training
      effect: NoSchedule
  topologyName: "leaf-domain-topology"     # for Phase 31 TAS
```

**Design the flavor set from the Phase 01 archetypes:**

| Flavor | Selector | Why it is separate |
|---|---|---|
| `gpu-rtx4090` | GPU model + training pool | 24 GB VRAM, no GDR, no P2P |
| `gpu-rtx4090-gdr` | Same model, GDR-capable nodes | Different NCCL config, better multi-node |
| `gpu-rtx3090` | Older generation | Different perf; users must be able to avoid it |
| `gpu-a6000` | Pro card | 48 GB VRAM, ECC, different EULA position |
| `cpu-standard` | Non-GPU nodes | CPU-only work should never consume a GPU node |
| `cpu-highmem` | Large-RAM nodes | Data preprocessing |
| `spot` / `preemptible` | Any node, low priority | Cheap capacity for interruptible work |

> 💡 **Do not create a flavor per node.** Flavors are for *meaningful* differences that a user would choose between. If two node groups are interchangeable for a workload, they belong in one flavor — otherwise the quota model fragments and capacity strands.

> ⚠️ **Flavor order matters.** Kueue tries flavors in the order listed in the ClusterQueue. Put the flavor you *want* used first (e.g. the older GPUs first, so the newest are preserved for jobs that specify them), or the newest first if you optimize for throughput. Decide deliberately and document it.

---

### Task 2 — ⚠️ Determine how GPUs are counted (do this empirically)

Before writing a single quota number, answer: **what resource name does Kueue count when a job requests a GPU via DRA?**

```bash
# Submit a job with a ResourceClaimTemplate, then:
kubectl get workload -n <ns> -o yaml | yq '.status.admission.podSetAssignments'
# Look at what resources appear. If GPUs do not appear, quota does not constrain them.
```

| Outcome | Consequence | Response |
|---|---|---|
| DRA devices counted in quota | ✅ Ideal | Set quota on the DRA resource name |
| DRA devices **not** counted | 🚫 **GPU quota is bypassable** | Add an extended resource or a Kyverno rule that requires a matching `nvidia.com/gpu`-style request alongside the claim, purely for accounting |
| Partially counted | Verify per DeviceClass | Test each of the five DeviceClasses from Phase 19 |

> 🚫 **Do not proceed to Task 3 until this is settled and tested.** A quota model that does not actually constrain GPUs is worse than none, because it creates false confidence. Write the finding into `deviations.md` and the handoff regardless of the outcome.

---

### Task 3 — The quota model

```yaml
apiVersion: kueue.x-k8s.io/v1beta1
kind: ClusterQueue
metadata: { name: tenant-research }
spec:
  cohort: nexus-main                      # ← membership enables borrowing
  namespaceSelector:
    matchLabels: { nexus.io/tenant: research }
  queueingStrategy: BestEffortFIFO        # see the table below
  preemption:
    reclaimWithinCohort: Any              # take back what was borrowed from us
    borrowWithinCohort: { policy: LowerPriority, maxPriorityThreshold: 100 }
    withinClusterQueue: LowerPriority     # our own low-pri jobs yield to our high-pri
  resourceGroups:
    - coveredResources: ["cpu", "memory", "nvidia.com/gpu"]
      flavors:
        - name: gpu-rtx4090
          resources:
            - name: "nvidia.com/gpu"
              nominalQuota: 32            # guaranteed share
              borrowingLimit: 24          # may borrow up to 24 more from the cohort
              lendingLimit: 16            # will lend at most 16 of ours when idle
            - name: "cpu"
              nominalQuota: "512"
            - name: "memory"
              nominalQuota: "2Ti"
```

**The four numbers and what each controls:**

| Field | Meaning | Set it wrong and… |
|---|---|---|
| `nominalQuota` | Your guaranteed floor | Too high across tenants (over-subscribed) → guarantees are fiction |
| `borrowingLimit` | How much idle capacity you may take | Unset (unlimited) → one tenant can hold the whole cluster, then preemption thrash |
| `lendingLimit` | How much of yours you will give away | Unset → your own burst may wait behind reclaim |
| `preemption.*` | Who yields to whom | Too aggressive → jobs die constantly; too passive → reclaim never happens |

> ⚠️ **Sum of `nominalQuota` across all ClusterQueues must not exceed physical capacity** (minus platform reservations from Phases 26/13/18). If it does, your "guarantees" cannot all be honored simultaneously and the first contention event exposes it. Write the arithmetic down:
> ```
> Total GPUs                    128
> − platform/system reserved      4
> − quarantined (Phase 23)        2
> = allocatable                 122
> Σ nominalQuota                120   ✅ (2 headroom)
> ```

**Queueing strategy:**

| Strategy | Behavior | Use when |
|---|---|---|
| `StrictFIFO` | Head-of-line blocking — a large job blocks smaller ones behind it | Fairness by arrival matters most |
| **`BestEffortFIFO`** | A blocked head lets others through | ✅ **Default** — much better utilization |

**WorkloadPriorityClasses** — priority is per *workload*, not per pod:

| Name | Value | For |
|---|---|---|
| `nexus-critical` | 10000 | Production inference, platform jobs |
| `nexus-high` | 1000 | Deadline-driven research |
| `nexus-normal` | 100 | Default |
| `nexus-low` | 10 | Batch, sweeps |
| `nexus-preemptible` | 1 | Opportunistic; expects to be killed |

---

### Task 4 — 🧪 Simulate before enforcing

**`tools/scheduling/queue-simulate.sh`** — replay the last N weeks of actual job submissions (from Phase 11 metrics, or a synthetic workload if you have no history) against the proposed quota model and report:

| Output | What it tells you |
|---|---|
| Jobs that would have been rejected/queued longer | Whether the model starves anyone |
| Peak borrowing per tenant | Whether `borrowingLimit` is realistic |
| Preemption count and victims | Whether the policy causes thrash |
| Time-to-admission distribution per priority | Whether priorities behave as intended |
| Idle-capacity-while-jobs-pending | Wasted capacity the model creates |

> 💡 **This step is what separates a quota model that works from one that gets reverted in week two.** If the simulation shows a tenant would have waited 6 hours for capacity that sat idle in another queue, fix `lendingLimit` now, not after they complain.

**If you have no history:** run the model in **observe-only mode** — Kyverno's `require-queue-label` in Audit, and Kueue admitting everything — for one week while collecting the same metrics. Then enforce.

---

### Task 5 — Make queue use mandatory and easy

**Mandatory** (`require-queue-label.yaml`, Kyverno):
```
Every Job / RayJob / PyTorchJob / MPIJob in a tenant namespace MUST carry:
    kueue.x-k8s.io/queue-name: <a LocalQueue in this namespace>
Reject otherwise.
Exempt: platform namespaces (kube-system, monitoring, storage, ...) — enumerate them.
```

**Easy** (`inject-default-queue.yaml`, mutation):
```
If the label is absent in a tenant namespace, inject the namespace's default LocalQueue.
```

> 💡 **Mutate first, then validate.** With both rules, the common case (a user submits a normal job) works with zero extra YAML, and only a user actively doing something unusual sees an error. This is the same pattern as Phase 22's NCCL injection: the platform makes the correct behavior the default.

> ⚠️ **Suspend semantics.** Kueue admits a Job by flipping `spec.suspend: false`. Any workload type that does not honor suspension cannot be gated by Kueue. Verify per workload type — batch/v1 Job, RayJob, PyTorchJob (via Trainer/Training Operator), MPIJob all support it; a bare Deployment does **not**. Document what Kueue can and cannot gate, because that gap is where quota leaks.

---

### Task 6 — 📊 Benchmark admission (B11) and observability

**`tools/scheduling/bench-admission.sh`:**

| Metric | Target | Measured |
|---|---|---|
| Time from job submit → admitted (uncontended, small) | < 2 s | |
| Time from job submit → admitted (uncontended, 16-GPU) | < 10 s | |
| Admission throughput | ≥ 100 workloads/min | |
| Time to reclaim borrowed capacity (preemption) | < 60 s | |
| Kueue controller CPU/memory at 1000 pending workloads | Record | |
| Queue depth handling: 5000 pending workloads | No degradation | |

**Alerts:**

| Alert | Threshold |
|---|---|
| `KueueControllerDown` | Any |
| `KueueQueueStarved` | A ClusterQueue with pending workloads and 0 admitted for 30 min |
| `KueueQuotaExhausted` | Nominal quota fully used for 1 h (capacity planning signal) |
| `KueueBorrowingHigh` | A tenant borrowing > 80 % of its limit for 2 h |
| `KueuePreemptionRateHigh` | > 10 preemptions/hour (thrash) |
| `KueueWorkloadPendingLong` | Any workload pending > 4 h |
| `KueueInadmissibleWorkload` | A workload that can never be admitted (asks for more than nominal+borrow) |

> 💡 **`KueueInadmissibleWorkload` is the highest-value alert here.** A user asking for 64 GPUs in a queue whose maximum is 56 will wait forever with no error. Detect it and tell them.

---

### Task 7 — The user guide

**`docs/user/submitting-jobs.md`**

```markdown
# Submitting a job

## The minimum
    apiVersion: batch/v1
    kind: Job
    metadata:
      namespace: my-tenant
      labels:
        kueue.x-k8s.io/queue-name: my-tenant-default    # auto-injected if omitted
    spec:
      suspend: true                                      # ⚠️ Kueue requires this
      template: { ... }

## What happens next
1. Your job is created SUSPENDED. Nothing runs yet.
2. Kueue checks: does your tenant have quota for the whole job?
3. If yes → admitted, unsuspended, pods scheduled.
4. If no → it waits in the queue. `kubectl get workloads -n my-tenant` shows why.

## Why is my job pending?
    kubectl describe workload <name> -n <ns>
    # The events say exactly which resource is short and in which flavor.
Common causes:
 · Your tenant's quota is fully used → wait, or ask for more
 · You asked for more than your queue's maximum → it will NEVER admit. Reduce or ask.
 · You requested a flavor with no free nodes → try another flavor
 · Cluster is full and you cannot borrow → wait

## Priority
    priorityClassName is for POD priority. For JOB priority use:
        labels: { kueue.x-k8s.io/priority-class: nexus-high }
Use nexus-preemptible for sweeps and experiments — you get capacity faster,
and you will be killed when someone with a guarantee needs it back.

## Borrowing
If other tenants are idle, you can exceed your quota automatically — no action needed.
⚠️ Borrowed capacity CAN BE TAKEN BACK. Checkpoint your work (Phase 33's guide).
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | Kueue controller healthy | `kubectl get pods -n kueue-system` | Running |
| **A2** | **GPU counting mechanism determined and tested for all 5 DeviceClasses** | Task 2 tests | Documented |
| **A3** | GPU quota cannot be bypassed via DRA | Attempt it | Blocked |
| **A4** | ResourceFlavors match real node labels; each selects ≥ 1 node | `quota-report.sh` | All non-empty |
| **A5** | Σ `nominalQuota` ≤ allocatable capacity | Arithmetic in the handoff | Holds |
| **A6** | A job over quota queues rather than failing | Submit one | Queued |
| **A7** | A job within quota admits promptly | Submit | Admitted |
| **A8** | **Borrowing works: an idle tenant's capacity is usable by another** | Two-tenant test | Borrowed |
| **A9** | **Reclaim works: the owner gets their capacity back** | Owner submits | Reclaimed < 60 s |
| **A10** | Preemption targets the correct victim (lowest priority first) | Test | Correct |
| **A11** | `withinClusterQueue` preemption works | Test | Works |
| **A12** | `borrowingLimit` is enforced | Try to exceed | Blocked |
| **A13** | `lendingLimit` protects the owner's burst | Test | Protected |
| **A14** | Priority classes order admission correctly | Submit mixed priorities | Correct order |
| **A15** | `BestEffortFIFO` lets small jobs past a blocked large one | Test | Passes |
| **A16** | Queue label required in tenant namespaces | Submit without | Rejected |
| **A17** | Default queue injected when the label is absent | Submit without | Injected |
| **A18** | Platform namespaces are exempt | Verify | Exempt |
| **A19** | Workload types that cannot be gated are documented | Read the doc | Documented |
| **A20** | 🧪 Simulation run against real or synthetic history | `queue-simulate.sh` | Report exists |
| **A21** | 📊 **B11 admission latency within targets** | `bench-admission.sh` | Met |
| **A22** | 5000 pending workloads does not degrade the controller | Load test | Stable |
| **A23** | All alerts fire correctly | Induce | Fire |
| **A24** | `KueueInadmissibleWorkload` detects an impossible request | Submit one | Alerts |
| **A25** | `quota-report.sh` shows usage, borrowing, and pending per tenant | Run it | Clear |

---

## ↩️ ROLLBACK

```bash
# Stop enforcing the queue requirement — jobs go direct to kube-scheduler
kubectl delete cpol require-queue-label

# Effectively disable quota without deleting the model: raise limits
kubectl patch clusterqueue <name> --type merge \
  -p '{"spec":{"resourceGroups":[{"flavors":[{"resources":[{"nominalQuota":"999999"}]}]}]}}'

# Full removal — ⚠️ suspended workloads must be released first, or they hang forever
kubectl get workloads -A          # note anything suspended
helm uninstall kueue -n kueue-system
# Then manually unsuspend any Job left with spec.suspend: true
```

> ⚠️ **The rollback hazard is orphaned suspended Jobs.** If Kueue is removed while workloads are queued, nothing will ever unsuspend them. Always drain the queue or unsuspend manually before uninstalling.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Job stays Suspended forever | Not admitted; check `kubectl describe workload` | The events name the missing resource |
| Job never admits despite free GPUs | Flavor selector matches no node; or the node is tainted and the flavor lacks the toleration | Fix the flavor |
| GPU quota appears not to apply | DRA not counted (Task 2) | Add the accounting mechanism |
| Preemption kills the wrong job | Priority classes not applied, or `reclaimWithinCohort: Never` | Fix priorities and the policy |
| Constant preemption thrash | Over-subscribed nominal quota, or borrowing with aggressive reclaim | Reduce nominal sums; raise `maxPriorityThreshold` |
| One tenant holds everything | No `borrowingLimit` | Set it |
| Utilization low, jobs pending | `StrictFIFO` head-of-line blocking, or lending limits too tight | Switch to `BestEffortFIFO`; relax lending |
| Deployments bypass quota | Kueue cannot gate them | Expected (A19). Use a separate mechanism (ResourceQuota) for those. |
| Admission slow at scale | Controller resources | Increase limits; check the queue-depth metric |
| A workload is admitted but pods stay Pending | Kueue admitted it; kube-scheduler cannot place it | This is a **topology/DRA** problem, not a quota one — Phase 31 |

---

## 🚫 DO NOT

- **Do not** write quota numbers before completing Task 2's GPU-counting test.
- **Do not** let Σ `nominalQuota` exceed allocatable capacity.
- **Do not** leave `borrowingLimit` unset.
- **Do not** enforce a new quota model without simulating or observing it first.
- **Do not** create a ResourceFlavor per node.
- **Do not** uninstall Kueue with workloads suspended.
- **Do not** implement gang scheduling or topology-aware placement here. Phase 31.
- **Do not** implement showback or accounting here. Phase 34.

---

## 📤 HANDOFF

`evidence/phase-30/handoff.md` must state:

1. **⚠️ How GPUs are counted toward quota** and whether DRA claims are constrained. If not, the compensating mechanism.
2. **The full quota model** — every ClusterQueue with nominal/borrowing/lending, and the capacity arithmetic proving it sums correctly.
3. **The ResourceFlavor set** and the flavor ordering decision.
4. **🧪 The simulation report** — projected wait times, preemption rate, and stranded capacity.
5. **📊 B11 admission latency** — feeds gate G8.
6. **Which workload types Kueue can and cannot gate.**
7. **Preemption policy in force** and the observed reclaim time.
8. **Platform reservations** subtracted from allocatable (Mayastor cores, Cilium, GPU operator, quarantine).

---

## ➡️ NEXT

**[PHASE-31 — Gang Scheduling & Topology-Aware Placement](PHASE-31.md)** — admission is solved; now make sure a 16-GPU job gets all 16 at once, on nodes that are close together.
