# PHASE 35 — Slurm Interoperability & Scheduling Gate (G8)

| | |
|---|---|
| **Stage** | 5 — Scheduling & Orchestration |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 30, 31, 32, 33, 34 |
| **Blocks** | 36 (Stage 6 entry), 39 |
| **Risk** | 🟡 Medium — two schedulers sharing one pool of hardware is a classic double-allocation hazard |
| **Blast radius** | GPU allocation correctness |
| **Architecture refs** | `ARCHITECTURE.md#l7-scheduling--orchestration`, ADR-020, `ULTIMATE-PLAN.md#13-gates` (G8) |

---

## 🎯 MISSION

Give HPC users the **`sbatch`/`srun`/`squeue` interface they already know**, backed by the same Kubernetes resource pool — without ever letting Slurm and Kubernetes allocate the same GPU. Then close Stage 5 by passing **gate G8**.

> 💡 **WHY bother, when Kubernetes already schedules everything.** Because a decade of HPC muscle memory, job scripts, and MPI workflows exists, and telling a computational-physics group to rewrite everything as `PyTorchJob` YAML is how a platform gets bypassed. Slinky (Slurm-on-Kubernetes) runs Slurm as a workload *inside* the cluster, so `sbatch` works, while every GPU it touches was allocated to it by Kubernetes. The users keep their interface; the platform keeps one source of truth for allocation.

> ⚠️ **The one hazard that matters: double allocation.** If Slurm believes it owns GPU 3 on node 14 while Kubernetes also hands GPU 3 to a Kueue workload, both jobs corrupt each other and both users lose hours. **Slurm must never see a resource that Kubernetes has not exclusively allocated to it.** Everything else in this phase is convenience; this is correctness.

> 💡 **This phase is optional.** If nobody in your organization uses Slurm, skip Tasks 1–6, execute the G8 gate (Task 7), and record the decision in `deviations.md`. Do not build an interface nobody asked for — that is a violation of the efficiency ladder in CLAUDE.md and of Law X.

---

## ✅ PREFLIGHT

```bash
# Is there real demand? If not, skip to Task 7.
cat evidence/phase-01/findings.md | grep -i slurm

# Everything Stage 5 built
kubectl get clusterqueues,podgroups,admissionchecks -A
bash tools/scheduling/quota-report.sh
bash tools/accounting/waterfall.sh --last-week

# MPI/RDMA path works (Slurm's main users need it)
cat benchmarks/baselines/b4-rdma.json
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/scheduling/slurm/
  slinky-operator-values.yaml
  slurmcluster.yaml                 # the Slurm partition definition
  slurm-clusterqueue.yaml           # ⚠️ Slurm's OWN Kueue quota — the isolation boundary
  slurm-config/                     # slurm.conf, gres.conf, cgroup.conf as ConfigMaps
  login-node.yaml                   # sbatch entrypoint, SSH via Phase 17 identity
tools/slurm/
  allocation-audit.sh               # 🧪 THE safety check: no GPU in two places
  slurm-health.sh
docs/user/slurm-guide.md
gates/G8-scheduling.md
observability/rules/slurm-alerts.yaml
evidence/phase-35/{preflight,acceptance,handoff,deviations,gate-g8}.md
```

---

## 🔧 VERSION PINNING

| Component | Version |
|---|---|
| Slinky slurm-operator | `0.3.0` |
| Slurm | `24.11.x` |
| Kubernetes | `1.34.x` |

---

## 📋 TASKS

### Task 1 — 🎯 The isolation model (design before deploying)

Choose how Slurm gets hardware. Only one option is safe.

| Model | How | Verdict |
|---|---|---|
| **A. Static partition** | A fixed set of nodes tainted `nexus.io/scheduler=slurm`, invisible to Kueue | ✅ **Safest.** Capacity is walled off. |
| **B. Elastic via Kueue** | Slurm compute nodes are Kubernetes pods admitted through their own ClusterQueue; the partition grows and shrinks | ✅ **Best utilization.** Kubernetes remains the single allocator. |
| C. Shared nodes | Both schedulers see the same nodes | 🚫 **Never.** This is the double-allocation failure. |

**Implement B**, with A as the fallback if Slinky's elasticity proves unstable.

**Why B is safe:** a Slurm compute node is a *pod*. That pod holds a DRA ResourceClaim for its GPUs, admitted by Kueue against `slurm-cluster-queue`. Kubernetes has exclusively allocated those GPUs to that pod; Slurm then schedules jobs *within* the pod's allocation. **Slurm never allocates a GPU — it subdivides one Kubernetes already gave it.**

```
Kubernetes/Kueue  ──allocates──►  slurmd pod (4 GPUs via DRA)
                                        │
                                        └──subdivides──►  Slurm jobs (1–4 GPUs each)
```

**`slurm-clusterqueue.yaml`** — Slurm is a tenant like any other:
```yaml
spec:
  cohort: nexus-main               # participates in borrowing like everyone else
  resourceGroups:
    - flavors:
        - name: gpu-rtx4090
          resources:
            - name: "nvidia.com/gpu"
              nominalQuota: 24     # Slurm's guaranteed share
              borrowingLimit: 16
              lendingLimit: 12     # ⚠️ Slurm lends back when idle
```

> 💡 **Making Slurm a cohort member is the whole point.** A statically-walled-off Slurm partition idles at night while Kubernetes users queue. As a cohort member, its idle capacity flows to others and returns when needed — the same mechanism as every other tenant, with no special cases.

---

### Task 2 — 🧪 The double-allocation audit (build this before the first job)

**`tools/slurm/allocation-audit.sh`** — the safety check that must run continuously.

```
For every GPU in the cluster (by UUID):
  k8s_holder   = the pod holding a DRA ResourceClaim for it
  slurm_holder = the Slurm job Slurm believes owns it (scontrol show node / gres)

  ASSERT: slurm_holder is non-empty  ⟹  k8s_holder is the slurmd pod on that node
  ASSERT: no GPU has two distinct non-slurmd k8s holders
  ASSERT: Slurm's total GRES count == GPUs allocated to slurmd pods
```

| Failure | Severity | Response |
|---|---|---|
| Slurm claims a GPU no slurmd pod holds | 🔴 **Critical** | Stop Slurm scheduling immediately; drain; investigate |
| Slurm's GRES count > allocated | 🔴 Critical | Same |
| Slurm's GRES count < allocated | 🟡 Capacity wasted | Reconcile the gres.conf generation |

> ⚠️ **Run this as a CronJob every 5 minutes with a paging alert**, and once before declaring the phase complete. It is cheap, and it is the only thing standing between a configuration mistake and two users silently corrupting each other's runs.

**`gres.conf` must be generated, never hand-written.** Generate it inside the slurmd pod at startup from the GPUs actually visible in that container (`nvidia-smi -L` / CDI device list) — never from a static file listing what you *think* is there. A hand-maintained `gres.conf` that drifts from reality is exactly how Slurm ends up claiming a GPU it does not have.

---

### Task 3 — The Slurm cluster definition

```yaml
# slurmcluster.yaml (Slinky)
spec:
  controller: { replicas: 1 }            # slurmctld — see the HA note
  accounting: { enabled: true }          # slurmdbd → Postgres on T1
  login:
    replicas: 2
    auth: oidc                           # ⚠️ Phase 17 identity, NOT local accounts
  nodesets:
    - name: gpu
      replicas: 6                        # elastic: min 2, max 12
      partition: gpu
      resources:
        claims: [{ name: gpus }]         # 4 GPUs each via DRA
      # Kueue gates admission of these pods
      labels: { kueue.x-k8s.io/queue-name: slurm-queue }
```

**Configuration that must match the rest of the platform:**

| Setting | Value | Why |
|---|---|---|
| `SelectType` | `select/cons_tres` | Per-GPU, per-core allocation |
| `GresTypes` | `gpu` | |
| `ProctrackType` | `proctrack/cgroup` | Contain runaway jobs |
| `TaskPlugin` | `task/cgroup,task/affinity` | Honor Phase 20's NUMA work |
| `ConstrainDevices` | `yes` (cgroup.conf) | ⚠️ A job must not see GPUs it was not given |
| `AccountingStorageType` | `accounting_storage/slurmdbd` | Feeds Phase 34 |
| `PreemptType` | `preempt/partition_prio` | Consistent with Phase 33 |
| MaxTime / DefaultTime | Set both | Unbounded jobs are how clusters get stuck |

> ⚠️ **`slurmctld` is a single point of failure** in most deployments. Slinky can run a backup controller with shared state, but the simpler and honest position for this platform is: **slurmctld failure stops Slurm scheduling, but does not stop running Slurm jobs or anything in Kubernetes.** State that blast radius in the runbook, back up `StateSaveLocation` (add to Phase 29's inventory), and do not pretend it is HA if it is not.

**Identity:** login nodes authenticate via OIDC (Phase 17), and the Slurm account maps to the same tenant as the Kubernetes namespace. One user, one identity, two interfaces. Never create local Unix accounts.

---

### Task 4 — Accounting continuity

Slurm has its own accounting database. Phase 34 must not lose visibility.

```
slurmdbd (Postgres on T1)
   → exporter → Prometheus
      → Phase 34 recording rules treat Slurm jobs as workloads of the "slurm" tenant
         → the waterfall accounts Slurm GPU-hours in the same buckets
```

> ⚠️ **Do not double-count.** The slurmd *pod* holds GPUs from Kubernetes' perspective for its whole lifetime; the Slurm *jobs* inside hold them for shorter intervals. Charge at exactly one level.
>
> **Decide: charge at the pod level** (Slurm's allocation from the cluster) and report Slurm's internal utilization as a sub-breakdown. That keeps the waterfall's reconciliation intact and correctly surfaces a Slurm partition holding idle GPUs — which is the same "allocated but idle" signal as any other tenant.

---

### Task 5 — The user guide

**`docs/user/slurm-guide.md`** — short, and honest about the differences.

```bash
# Your familiar workflow, unchanged
ssh login.nexus.internal            # OIDC-backed; your normal credentials
sbatch train.sh
squeue -u $USER
scancel <jobid>
srun --gres=gpu:2 --pty bash

# train.sh
#SBATCH --partition=gpu
#SBATCH --gres=gpu:4
#SBATCH --nodes=2
#SBATCH --time=08:00:00            # ⚠️ REQUIRED — there is a max
#SBATCH --account=research-physics
srun python train.py
```

**What is different from a traditional Slurm cluster — say it plainly:**

| | Traditional Slurm | Here |
|---|---|---|
| Node availability | Fixed | **Elastic** — the partition grows/shrinks with cluster demand |
| Your job may wait because… | Slurm queue | Slurm queue **or** the partition is currently small |
| Home directory | NFS | CephFS (`nexus-home`) — same POSIX semantics |
| Scratch | `/scratch` on the node | `/scratch` — same, node-local NVMe, non-durable |
| Modules | `module load` | Containers. See the container guide; `module` is not available. |
| Preemption | Depends on site | Yes — checkpoint your work (Phase 33's guide applies) |
| MPI | Native | Native, over the same RDMA fabric (Phase 39) |

> 💡 **The "modules vs. containers" gap is the real friction**, not the scheduler. Address it directly: provide a curated set of container images matching the software stacks users expect, and document the mapping. This is where most Slurm-interop efforts actually fail.

---

### Task 6 — Alerts

| Alert | Threshold | Severity |
|---|---|---|
| `SlurmAllocationMismatch` | Audit failure (Task 2) | 🔴 **Page** |
| `SlurmctldDown` | Controller unavailable | 🟠 High |
| `SlurmdbdDown` | Accounting unavailable | 🟡 |
| `SlurmNodeDrained` | Compute pods failing to admit | 🟡 |
| `SlurmPartitionEmpty` | Zero compute nodes with jobs queued | 🟠 |
| `SlurmJobPendingLong` | Job pending > 4 h | 🟡 |
| `SlurmPartitionIdle` | Slurm holds GPUs at < 5 % SM_ACTIVE > 1 h | 🟡 — lend them back |

---

### Task 7 — 🚪 GATE G8 — Scheduling

**`gates/G8-scheduling.md`** — Stage 5's gate. Runs whether or not Slurm was deployed.

| # | Check | Evidence | Pass |
|---|---|---|---|
| G8.1 | Quota model enforced; Σ nominal ≤ allocatable | Phase 30 | ☐ |
| G8.2 | **GPU quota cannot be bypassed via DRA** | Phase 30 A3 | ☐ |
| G8.3 | Borrowing and reclaim work end to end | Phase 30 A8/A9 | ☐ |
| G8.4 | 📊 **B11: admission latency within targets** | `bench-admission.sh` | ☐ |
| G8.5 | Gang scheduling: all-or-nothing verified | Phase 31 A4/A5 | ☐ |
| G8.6 | 🧪 **Two competing gangs cannot deadlock** | Phase 31 A6 | ☐ |
| G8.7 | 📊 Topology-aware placement benefit measured through the real path | Phase 31 A12 | ☐ |
| G8.8 | Fragmentation index exported and trending | Phase 31 A18 | ☐ |
| G8.9 | 🧪 **A full rack at 100 % load stays under power budget** | Phase 32 A8 | ☐ |
| G8.10 | Power AdmissionCheck holds workloads with a clear reason | Phase 32 A11/A12 | ☐ |
| G8.11 | Thermal response engages and auto-restores | Phase 32 A13/A14 | ☐ |
| G8.12 | 🧪 **Preemption → resumed training < 5 min** | Phase 33 A11 | ☐ |
| G8.13 | Checkpoints survive preemption **and** node failure | Phase 33 A5/A6 | ☐ |
| G8.14 | Minimum-runtime guarantee prevents thrash | Phase 33 A3 | ☐ |
| G8.15 | Elastic jobs grow and shrink without breaking the gang | Phase 33 A16–A18 | ☐ |
| G8.16 | 🧪 **Utilization waterfall reconciles to 100 %** | Phase 34 A9 | ☐ |
| G8.17 | 📊 Cost model published with its inclusions stated | Phase 34 A7 | ☐ |
| G8.18 | Every GPU-hour attributes to a tenant | Phase 34 A1 | ☐ |
| G8.19 | 🧪 **No GPU is allocated to both Slurm and Kubernetes** | `allocation-audit.sh` | ☐ or N/A |
| G8.20 | `sbatch`/`squeue`/`scancel` work for a real user | Manual test | ☐ or N/A |
| G8.21 | Slurm participates in cohort borrowing | Test | ☐ or N/A |
| G8.22 | 📊 **Cluster utilization ≥ 60 % over a 7-day window** | Waterfall | ☐ |
| G8.23 | All Stage 5 alerts fire correctly | Test each | ☐ |
| G8.24 | User docs published: submitting, placement, checkpointing, usage, Slurm | Review | ☐ |

> 📊 **G8.22 is the outcome check.** Everything else verifies a mechanism; this verifies the mechanisms together produce the result. If utilization is below 60 % with demand present, the waterfall says exactly why — fix the largest bucket before proceeding, or record it as a dated finding with an owner.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | Slurm isolation model is B (or A), never C | Read the design | Correct |
| **A2** | slurmd pods hold GPUs via DRA, admitted by Kueue | `kubectl get resourceclaims` | Confirmed |
| **A3** | `gres.conf` is generated from actually-visible devices | Read the startup logic | Generated |
| **A4** | 🧪 **`allocation-audit.sh` passes with zero mismatches** | Run it | Zero |
| **A5** | Audit runs every 5 min with a paging alert | CronJob + alert | Scheduled |
| **A6** | 🧪 A Slurm job cannot see a GPU it was not granted | `nvidia-smi` inside a 1-GPU job on a 4-GPU node | Sees 1 |
| **A7** | `ConstrainDevices=yes` in cgroup.conf | Read | Set |
| **A8** | Slurm partition grows when the cluster has capacity | 🧪 Free capacity | Grows |
| **A9** | Slurm partition shrinks when capacity is reclaimed | 🧪 Reclaim | Shrinks |
| **A10** | Slurm lends idle capacity back to the cohort | Test | Lends |
| **A11** | Login node authenticates via OIDC, no local accounts | Test | OIDC |
| **A12** | `sbatch`, `squeue`, `scancel`, `srun --pty` all work | Manual | Work |
| **A13** | A multi-node MPI job runs over RDMA from Slurm | 🧪 Run one | Works, RDMA confirmed |
| **A14** | MaxTime enforced; jobs cannot run unbounded | Submit without `--time` | Rejected or defaulted |
| **A15** | Slurm accounting flows into Phase 34 | Query | Present |
| **A16** | **GPU-hours are not double-counted** | 🧪 Reconcile the waterfall with Slurm running | Reconciles |
| **A17** | `StateSaveLocation` added to Phase 29's backup inventory | Check | Added |
| **A18** | slurmctld failure does not affect Kubernetes workloads | 🧪 Kill it | No impact |
| **A19** | All Slurm alerts fire | Induce | Fire |
| **A20** | Slurm guide is accurate, including the modules→containers gap | Review with a user | Accurate |
| **A21** | 🚪 **Gate G8 passes with all evidence** | `gates/G8-scheduling.md` | All ☑ |

---

## ↩️ ROLLBACK

```bash
# Drain Slurm gracefully: stop new jobs, let running ones finish
kubectl exec -n slurm deploy/slurmctld -- scontrol update PartitionName=gpu State=DRAIN

# Then scale the partition to zero — GPUs return to the Kubernetes pool
kubectl patch slurmcluster nexus -n slurm --type merge \
  -p '{"spec":{"nodesets":[{"name":"gpu","replicas":0}]}}'

# Full removal
helm uninstall slurm-operator -n slurm
kubectl delete clusterqueue slurm-queue    # returns its quota to the cohort
```

> 💡 **Slurm is the most cleanly removable component in the platform**, precisely because of the isolation model: it holds GPUs the same way any tenant does, and releasing them is a scale-to-zero. That property is the payoff for choosing model B.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| 🔴 Allocation audit mismatch | `gres.conf` drift, or a slurmd pod restarted without Slurm noticing | **Stop Slurm scheduling.** Regenerate gres; reconcile; investigate how it drifted. |
| Slurm job sees all GPUs on the node | `ConstrainDevices` not set | A7 |
| Slurm partition stuck at zero nodes | Kueue not admitting slurmd pods | Check `slurm-queue` quota and `describe workload` |
| Jobs pending though nodes look idle | Slurm's view of GRES is stale | `scontrol reconfigure`; check the audit |
| Login node auth fails | OIDC config, or group claim missing (Phase 17's known trap) | Check the `groups` mapper |
| MPI job fails to launch | PMIx version mismatch, or RDMA not visible in the pod | Phase 39's troubleshooting applies |
| Accounting shows double the GPU-hours | Charging at both pod and job level | Task 4 — charge at the pod level only |
| slurmctld restart loses queue | `StateSaveLocation` not persistent | Put it on T1; back it up |
| Users ask for `module load` | Not available | Provide curated container images; document the mapping |
| Gate G8.22 fails (utilization < 60 %) | Read the waterfall | Fix the largest bucket; do not proceed blind |

---

## 🚫 DO NOT

- **Do not** let Slurm and Kubernetes both schedule onto the same node (model C).
- **Do not** hand-write `gres.conf`.
- **Do not** deploy Slurm without the allocation audit running first.
- **Do not** create local Unix accounts on login nodes.
- **Do not** allow unbounded job time limits.
- **Do not** charge GPU-hours at both the pod and Slurm-job level.
- **Do not** claim slurmctld is HA if it is a single replica.
- **Do not** build Slurm at all if nobody needs it — record the decision and pass G8.
- **Do not** pass G8 with a failing check and no dated finding.

---

## 📤 HANDOFF

`evidence/phase-35/handoff.md` must state:

1. **🚪 The G8 gate result** with links to every piece of evidence across Phases 30–35.
2. **📊 Cluster utilization over the last 7 days**, and the waterfall behind it.
3. **Whether Slurm was deployed**, and if not, the recorded reason.
4. **🧪 The allocation audit result** and the fact that it runs continuously.
5. **Slurm's quota, borrowing, and lending position** in the cohort.
6. **The blast radius of a slurmctld failure**, as documented.
7. **The modules→containers mapping** provided to HPC users.
8. **Stage 5 declaration** — the cluster now admits work fairly, gangs it correctly, places it topologically, respects electrical limits, survives interruption cheaply, accounts for every GPU-hour, and speaks both `kubectl` and `sbatch`. Stage 6 (distributed compute frameworks) may begin.

---

## ➡️ NEXT

**[PHASE-36 — Ray & KubeRay](PHASE-36.md)** — begin Stage 6. The scheduler is solved; now give users the frameworks that turn a resource pool into distributed computation.
