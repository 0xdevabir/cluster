# PHASE 36B — Elastic & Preemption-Tolerant Workload Patterns

| | |
|---|---|
| **Stage** | C — Campus Harvest Plane |
| **Estimated effort** | 5–6 hours |
| **Depends on** | 36, 31B, 19B |
| **Blocks** | 52B |
| **Risk** | 🟠 A fabric users cannot figure out how to use has a work-loss ratio of 100 % |
| **Blast radius** | User experience and adoption |
| **Architecture refs** | `CAMPUS-FABRIC.md#51-the-honest-capability-table`, `#7-the-backend` (M6), `ULTIMATE-PLAN.md#5-target-capability-model` (Plane B table) |

---

## 🎯 MISSION

Give users **working templates** for every workload class the harvest plane supports, size their work units to the machine's confidence tier automatically, and state plainly — in the templates themselves — what the plane will not run.

> 💡 **WHY this phase decides whether the fabric gets used.** Everything before it makes borrowed capacity *available*. None of it makes borrowed capacity *usable* by a graduate student who wants to run a sweep and has never heard of a checkpoint interval. The gap between "the cluster works" and "people run things on it" is where most university clusters quietly die, and it is closed by templates and honest documentation, not by more scheduler features.

> 🎯 **M6 — right-sized work units** (`CAMPUS-FABRIC.md §7`). A 45-minute trial submitted against a Silver window is automatically split into three checkpointed 15-minute segments. **The work adapts to the machine, not the reverse** — and the user does not have to think about it.

---

## ✅ PREFLIGHT

```bash
# 1. The checkpoint contract exists and is satisfied by at least one framework
test -f docs/campus/checkpoint-contract.md
grep -i 'frameworks' evidence/phase-31B/handoff.md

# 2. GPU pools labeled, sharing modes configured
kubectl get nodes -l nexus.io/plane=harvest -L nexus.io/gpu-pool

# 3. Phase 36 complete — Ray on the Core Plane exists; this phase adapts it to churn
kubectl get raycluster -A

# 4. The Oracle's declared-runtime contract from 14B's handoff
grep -i 'declared-runtime contract' evidence/phase-14B/handoff.md
```

---

## 📦 DELIVERABLES

```
charts/harvest-templates/           # the user-facing golden paths
  sweep/                            # hyperparameter search across the fleet
  batch-inference/
  preprocess/                       # ETL / feature extraction shards
  single-node-train/                # Gold-tier, checkpointed
  local-sgd-train/                  # same-lab low-communication training
  ci-job/
clusters/nexus-prod/compute/campus/
  ray-harvest.yaml                  # Ray cluster tolerant of node churn
  work-unit-sizer.yaml              # M6: splits work to fit the confidence tier
docs/campus/
  user-guide.md                     # THE user-facing document
  what-does-not-run-here.md         # the honest exclusion list, written for users
  cost-of-interruption.md           # how to think about checkpoint intervals
evidence/phase-36B/
  preflight.md plan.md acceptance.md handoff.md deviations.md
  template-runs/                    # one real run per template, with its trace
```

---

## 📋 TASKS

### Task 1 — The six templates

Each ships as a chart with sensible defaults, a working example, and **its Oracle declarations pre-filled** so a user never has to compute a checkpoint interval by hand.

| Template | Tier | Declares | Notes |
|---|---|---|---|
| **sweep** | Bronze–Gold | `estimatedRuntime` per trial, `checkpointInterval: 600s` | Ray Tune or Argo Workflows fan-out. The flagship workload — zero inter-node traffic, scales to the whole campus. |
| **batch-inference** | Silver–Gold | `estimatedRuntime` from item count × measured rate | Shards by input partition; a lost shard re-runs, never the whole job |
| **preprocess** | Bronze–Gold | per-shard runtime | Read-once/write-once; idempotent by construction, so checkpointing is often unnecessary |
| **single-node-train** | Gold only | `checkpointInterval: 900s`, `estimatedRuntime` | Framework-native checkpointing (Lightning / HF Trainer / Ray Train) wired to `$NEXUS_CHECKPOINT_DIR` |
| **local-sgd-train** | Gold, same-lab, homogeneous | sync interval, ranks, pool id | The only synchronizing template. See Task 3. |
| **ci-job** | Bronze | `estimatedRuntime: <15m` | Lowest priority; the fabric's natural gap-filler |

Every template **fails fast at submit time** if its declarations are missing or inconsistent, with a message that says what to set and why — not a webhook rejection the user has to decode.

### Task 2 — The work-unit sizer (M6)

The user says "run 200 trials, each about 45 minutes." The sizer decides how that meets the fleet:

```
Given: trial_runtime ≈ 45 min, available tiers across the fleet
  Gold   window (> 8 h)  → run trials whole, 10 per node sequentially, checkpoint every 15 min
  Silver window (2 h)    → run trials whole, 2 per node, checkpoint every 15 min
  Bronze window (30 min) → SPLIT: each trial becomes 3 × 15-min checkpointed segments,
                            resumable on any node in the same lab
  Blocked                → do not place
```

Splitting requires the workload to be resumable — the checkpoint contract (31B). **A workload that cannot resume is never split**; it is simply restricted to tiers where it fits whole, and the user is told so at submit time rather than discovering it after four failed attempts.

> 💡 **The sizer is where the Oracle's prediction turns into throughput.** Without it, a fleet whose windows are mostly Silver runs only work that happens to be Silver-shaped. With it, the same fleet runs everything, because the work is reshaped to fit the time available.

### Task 3 — Low-communication distributed training

The one genuinely surprising capability of this plane, and the one to document most carefully (`CAMPUS-FABRIC.md §5.1`).

```
Standard DDP/FSDP:   sync every step      → 7B model = ~240 s per step over 1 GbE   ❌ dead
Local-SGD / DiLoCo:  sync every 100–500   → the same communication amortized over
                                            hundreds of steps ≈ ~1 s/step equivalent  ⚠️ viable
```

**Constraints, enforced by the template and not left to the user:**

- **Same lab only.** Cross-lab ranks would push synchronization traffic over shared uplinks (R-22). Kueue TAS with `nexus.io/lab` as the required domain.
- **Homogeneous GPUs only.** The slowest rank sets the pace of every synchronization (R-15, 19B Task 6).
- **Gold tier only.** A rank lost mid-round costs the whole round; short windows are not worth it.
- **Rank churn tolerated** — the template uses elastic rank counts and treats a lost rank as a reduced-participation round, not a failure.
- **Convergence is the user's problem, and we say so.** Local-SGD changes optimization behavior. The template documents this honestly and links to the literature rather than implying it is free.

> ⚠️ **Do not oversell this.** It is a real technique with real caveats, and it is the single easiest thing in this plan to overclaim. The honest framing: *"multi-node training on 1 GbE is possible for algorithms designed for low communication, at some cost in convergence behavior, within a single lab."* Anything stronger than that is not supportable and will be caught by anyone who reads §4.4.

### Task 4 — Ray under churn

**`ray-harvest.yaml`** — a Ray cluster whose workers are borrowed machines:

| Setting | Value | Why |
|---|---|---|
| Worker replacement | Aggressive, automatic | Losing workers is normal, not exceptional |
| Object spilling | To T-local, never across the network | Cross-node spilling over 1 GbE is a performance cliff |
| Object size guidance | Keep objects < 10 MB | Beyond that, the plasma store starts moving real bytes over the uplink |
| Task retries | Enabled, bounded (default 3) | Idempotent tasks are the plane's natural unit |
| Head node | **Core Plane only** | The head is state; state never lives on a borrowed machine (§3.3) |
| Autoscaling | Follows the Oracle's tier, not just pending work | Do not scale into a window that is about to close |

### Task 5 — The user guide, and the honest exclusion list

**`docs/campus/user-guide.md`** — how to pick a template, what to declare, how to read a rejection, where results go.

**`docs/campus/what-does-not-run-here.md`** — equally important, and written for users rather than engineers:

| You want to... | On the harvest plane | Where it goes instead |
|---|---|---|
| Train a large model with DDP/FSDP across nodes | ❌ Not possible over 1 GbE | Core Plane (Plane A) |
| Run tightly-coupled MPI | ❌ Not possible | Core Plane |
| Run a shuffle-heavy Spark job | ❌ Will be extremely slow | Core Plane, or restructure to partitioned |
| Run a long job with no checkpointing | ❌ Rejected at admission | Add checkpointing, or use the Core Plane |
| Store a dataset permanently | ❌ Harvest storage is ephemeral by design | Core Plane object store |
| Run something on private/regulated data | ❌ Blocked by policy | Core Plane (04B data policy) |
| Run 500 independent trials | ✅ **This is what the plane is for** | — |

> 💡 **A clear "no" is a feature.** Users who understand the boundary route themselves correctly and stop filing tickets. Users who discover it by failure conclude the cluster is broken. Put this table in front of them before their first submission, not after their first failure.

### Task 6 — Prove each template with a real run

For each of the six templates, execute a real workload on real harvest nodes and commit the trace to `evidence/phase-36B/template-runs/`:

- the submitted spec, including its declarations
- placement decisions (which lab, which tier, why)
- at least one induced eviction and its recovery
- wall-clock, useful compute time, and work lost
- the result, verified correct after interruption

**A template with no evidence of surviving an eviction is not a harvest template.** It is a job spec that happens to have run once on a quiet night.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Command |
|---|---|---|
| 1 | All six templates exist, install cleanly, and run their example workload | `helm test` per chart |
| 2 | Each template has a committed real run **including a survived eviction** | `evidence/phase-36B/template-runs/` |
| 3 | Submitting without required declarations fails at submit time with an actionable message | deliberate bad submission, capture output |
| 4 | The sizer splits a 45-min unit into segments when only Bronze windows are available | tier-forced test |
| 5 | The sizer refuses to split a non-resumable workload and says why | negative test |
| 6 | `local-sgd-train` is placed same-lab, homogeneous, Gold-only; cross-lab placement is refused | positive and negative placement tests |
| 7 | Ray head never lands on a harvest node | scheduling policy test |
| 8 | Ray survives losing 30 % of its workers mid-job and completes correctly | chaos run |
| 9 | `what-does-not-run-here.md` is linked from the submission error path | manual review |
| 10 | A new user can run a sweep from the guide alone, unaided | have someone outside the project do it, and record where they got stuck |

---

## ↩️ ROLLBACK

Withdraw a template by unpublishing its chart; running jobs finish. **Never leave a broken template published** — a user whose first experience is a template that does not work does not come back for the second, and the fabric's value is entirely in its adoption.

---

## 🧯 TROUBLESHOOTING

| Symptom | Cause | Action |
|---|---|---|
| Users submit without declarations | Template defaults missing or docs unclear | Fix the template, not the user. Pre-fill everything that can be pre-filled. |
| Sweeps slow despite idle nodes | Trials too large for available tiers, or pool constraints too strict | Check the sizer's decisions; check whether GPU pool constraints are over-narrow (19B). |
| Local-SGD converges poorly | Sync interval too long for the model/optimizer | It is a real trade-off, not a bug. Document it; suggest a shorter interval and the resulting bandwidth cost. |
| Ray object spilling over the network | Objects too large | Restructure the workload. This is a 1 GbE cliff, not a tuning parameter. |
| Jobs succeed but results are wrong after an eviction | Non-idempotent restart | The checkpoint contract's idempotency requirement is being violated. Fix the template; add a verification step to its run evidence. |
| Nobody is using the fabric | The guide, not the fabric | Watch a real user attempt it. The failure is almost always in the first ten minutes of the experience. |

---

## 🚫 DO NOT

- Do not publish a template that has not survived a real eviction.
- Do not allow cross-lab placement for any synchronizing workload.
- Do not overclaim low-communication training.
- Do not put the Ray head, or any stateful component, on a harvest node.
- Do not split a workload that cannot resume.
- Do not hide the exclusion list — surface it at submission time.
- Do not build the accounting dashboards here; that is 33B.

---

## 🤝 HANDOFF — write `evidence/phase-36B/handoff.md`

Must state:

- The six templates, their tier eligibility, and their measured overhead versus an uninterrupted run on the same hardware.
- Sizer behavior per tier, and the workload classes that cannot be split.
- Local-SGD's measured throughput versus single-node × N, and its observed convergence behavior — with the caveats stated plainly.
- Ray's measured survival characteristics under worker churn.
- **Where the first real user got stuck.** That observation is worth more than any of the numbers above.
