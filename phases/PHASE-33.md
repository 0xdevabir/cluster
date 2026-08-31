# PHASE 33 — Preemption, Checkpointing & Elastic Jobs

| | |
|---|---|
| **Stage** | 5 — Scheduling & Orchestration |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 25, 28, 30, 31 |
| **Blocks** | 34, 35, 37, 41, 54 |
| **Risk** | 🟡 Medium — bad checkpoint logic loses work; good checkpoint logic is the platform's superpower |
| **Blast radius** | All long-running jobs |
| **Architecture refs** | `ARCHITECTURE.md#l74-failure-and-preemption-semantics`, `ULTIMATE-PLAN.md#9-scaling-model`, Law IX |

---

## 🎯 MISSION

Make **interruption cheap**. Every mechanism built so far — quota borrowing (30), preemption, gang rescheduling (31), load shedding (32), node remediation (23) — depends on being able to stop a job and resume it with minutes of lost work rather than days. Deliver a checkpointing contract, automated checkpoint sync, graceful preemption with warning, and elastic jobs that grow and shrink with available capacity.

> 💡 **WHY this is the keystone phase of Stage 5.** Every efficiency mechanism in this platform is a promise to take capacity away from someone. Borrowing only works if the lender can reclaim. Preemption only works if the victim survives. Load shedding only works if you can stop compute without destroying work. **A cluster where interruption is expensive must be run conservatively — low utilization, static quotas, nobody borrows.** A cluster where interruption costs 90 seconds can run at 90 % utilization with everyone borrowing everything. This phase is worth more utilization than any scheduler tuning.

> ⚠️ **The honest constraint.** Checkpointing is fundamentally the *application's* responsibility — the platform cannot serialize arbitrary GPU state. What the platform can do is: give warning, provide fast local storage to write to, sync it durably in the background, restore it automatically, and make the correct pattern the default in every template. Where an application refuses to checkpoint, it must be marked non-preemptible and pay for that in quota.

---

## ✅ PREFLIGHT

```bash
# Scheduling and gang semantics working
kubectl get clusterqueues,podgroups -A

# T0 scratch (fast checkpoint target) and T3 object (durable target)
kubectl get sc nexus-scratch
s5cmd ls s3://nexus-artifacts-*/

# 📊 T0 write throughput — determines checkpoint write time
jq '.seq_write_steady_gbps' benchmarks/baselines/b6-device-raw.json

# Preemption already functioning at the quota level (Phase 30 A9/A10)
grep -A3 "Reclaim works" evidence/phase-30/acceptance.md
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/scheduling/lifecycle/
  preemption-policy.yaml            # grace periods per priority class
  checkpoint-sync-sidecar.yaml      # T0 → T3 async replication
  suspend-resume-controller.yaml
  elastic-policy.yaml
policies/defaults/
  inject-checkpoint-sidecar.yaml
  require-preemption-declaration.yaml   # declare checkpointable or pay
  inject-termination-handler.yaml
tools/lifecycle/
  checkpoint-verify.sh              # 🧪 is this checkpoint restorable?
  preemption-drill.sh               # 🧪 measure real preemption cost
  resume-report.sh
docs/user/
  checkpointing-guide.md            # 🎯 the contract with users
  elastic-jobs.md
templates/                          # reference implementations
  pytorch-checkpoint-example/
  ray-checkpoint-example/
observability/rules/lifecycle-alerts.yaml
evidence/phase-33/{preflight,acceptance,handoff,deviations}.md
```

---

## 📋 TASKS

### Task 1 — 🎯 The checkpointing contract

Write it as a contract with two sides. Both sides must be explicit.

**What the platform guarantees:**

| # | Guarantee |
|---|---|
| **P1** | You get **at least 120 seconds** of warning before preemption (configurable per priority) |
| **P2** | The warning arrives as `SIGTERM` and as a file at `/nexus/preemption-notice` with a deadline timestamp |
| **P3** | `/scratch` is fast local NVMe — writing 40 GB takes ~20 s (Phase 25's measured number) |
| **P4** | Anything you write to `/checkpoints` is **asynchronously replicated to durable object storage** |
| **P5** | On restart, your last synced checkpoint is **restored to `/checkpoints` before your container starts** |
| **P6** | Your job's identity (`NEXUS_JOB_ID`) is stable across preemption, so you can find your own checkpoints |
| **P7** | You will not be preempted within 10 minutes of starting (anti-thrash guarantee) |

**What the application must do:**

| # | Requirement |
|---|---|
| **A1** | Handle `SIGTERM`: checkpoint and exit cleanly within the grace period |
| **A2** | Write checkpoints to `/checkpoints/<step>/`, atomically (write to `.tmp`, then rename) |
| **A3** | On start, look for and resume from the latest valid checkpoint |
| **A4** | Checkpoint **at least every 30 minutes** of wall time, regardless of preemption |
| **A5** | Declare `nexus.io/preemptible: "true"` — or `"false"` and accept the quota cost |

> ⚠️ **P7 (the anti-thrash guarantee) is what makes preemption tolerable.** Without it, a heavily contended cluster can preempt a job seconds after it starts, repeatedly, so it never makes progress — burning GPU-hours on nothing but startup. A minimum runtime guarantee ensures every preemption cycle produces some forward progress. Enforce it in the preemption controller, not by convention.

> ⚠️ **A2's atomic write is not optional.** A checkpoint interrupted mid-write is worse than no checkpoint: it looks valid, restore reads garbage, and the job crashes on resume — often hours later. Write-then-rename, and write a `COMPLETE` marker with a checksum.

---

### Task 2 — Graceful preemption with warning

**`preemption-policy.yaml`:**

| Priority class | Grace period | Warning method | Minimum runtime (P7) |
|---|---|---|---|
| `nexus-critical` | Not preemptible | — | — |
| `nexus-high` | 600 s | SIGTERM + notice file | 30 min |
| `nexus-normal` | 300 s | SIGTERM + notice file | 15 min |
| `nexus-low` | 120 s | SIGTERM + notice file | 10 min |
| `nexus-preemptible` | 60 s | SIGTERM + notice file | 5 min |

**The preemption sequence:**
```
1. Scheduler/Kueue decides to preempt workload W
2. Controller writes /nexus/preemption-notice into every pod of W:
       { "deadline": "2026-08-31T14:23:00Z", "reason": "quota reclaim by tenant-x",
         "grace_seconds": 300 }
3. SIGTERM to every container
4. Application checkpoints and exits  ──────────┐
5. Controller waits for the grace period        │ whichever comes first
6. SIGKILL any remaining containers  ───────────┘
7. Checkpoint sync sidecar flushes remaining data to T3   ⚠️ MUST outlive the main container
8. Pod terminates; workload returns to the queue with its checkpoint recorded
```

> ⚠️ **Step 7 is the one that breaks in practice.** Kubernetes terminates all containers in a pod together. If the sync sidecar dies with the main container, the last checkpoint never reaches durable storage. Solutions: (a) sidecar as a **native sidecar** (`initContainer` with `restartPolicy: Always`, Kubernetes 1.29+), which shuts down after the main containers; (b) sync synchronously before exit; (c) a node-level DaemonSet that drains orphaned checkpoint directories. **Implement (a) and (c)** — (a) for the normal path, (c) for the node-failure path where nothing gets to run.

---

### Task 3 — The checkpoint sync sidecar

**`checkpoint-sync-sidecar.yaml`** — a native sidecar injected by mutation.

```
Watches /checkpoints for new completed checkpoints (COMPLETE marker present)
  → verifies the checksum
  → uploads to s3://nexus-artifacts-<tenant>/<job-id>/checkpoints/<step>/
  → records the manifest (step, size, checksum, timestamp)
  → prunes local copies beyond the last K (default 2) to protect scratch capacity
  → on SIGTERM: flush everything pending, THEN exit
```

**Why local-then-async rather than writing straight to object storage:**

| | Direct to S3 | **T0 → async sync** |
|---|---|---|
| Checkpoint write time (40 GB) | ~40 s at 1 GB/s, competing with NCCL | **~20 s local, network cost is off the critical path** |
| Impact on training step time | Stalls the job | Near zero |
| Fabric contention with gradients | ⚠️ Direct competition | None during the write |
| Durability if the node dies mid-sync | S3 has partial | Local copy lost; last synced copy safe |

> 💡 **This is Law VI applied to writes.** The same principle that says "stage datasets locally" says "checkpoint locally and replicate in the background." The GPU should never wait on the network for anything that can be deferred.

**Restore path** — an `initContainer` injected alongside:
```
On pod start:
  · Query the manifest for the latest complete checkpoint for NEXUS_JOB_ID
  · Download it to /checkpoints/
  · Verify the checksum
  · Export NEXUS_RESUME_FROM=/checkpoints/<step>
  · If nothing exists → fresh start, export NEXUS_RESUME_FROM=""
```

---

### Task 4 — 🧪 Measure real preemption cost

**`tools/lifecycle/preemption-drill.sh`** — the numbers that determine how aggressively the cluster can be scheduled.

| Metric | Target | Measured |
|---|---|---|
| Warning → checkpoint complete (7B model, 40 GB state) | < 60 s | |
| Checkpoint write throughput to `/scratch` | ≥ 1.5 GB/s | |
| Checkpoint sync to S3 (background) | Off critical path | |
| Pod termination → workload requeued | < 15 s | |
| Requeue → rescheduled (uncontended) | < 30 s | |
| Restore download + verify | < 60 s | |
| **Total: preemption → training resumed** | **< 5 min** | |
| **Lost work (wall time since last checkpoint)** | **< 15 min avg** | |
| GPU-hours lost per preemption event | Compute it | |

📊 **The derived number that matters:** *preemption efficiency* =
```
1 − (lost_work + restart_overhead) / time_between_preemptions
```
If preemption costs 5 minutes and happens hourly, you lose ~8 % — acceptable, and far cheaper than the utilization gained by allowing borrowing. If it costs 45 minutes, borrowing is not worth it and the quota model should be more static. **Compute this and let it drive Phase 30's policy.**

**`tools/lifecycle/checkpoint-verify.sh`** — proves a checkpoint is *restorable*, not merely present:
```
· COMPLETE marker exists
· Checksum matches
· The framework can load it (a short restore-and-one-step test job)
Run this on a sample of checkpoints nightly. A checkpoint that has never been
loaded is the same hypothesis problem as an untested backup (Phase 29).
```

---

### Task 5 — Elastic jobs

Jobs that run at a range of sizes convert queue wait into productive work.

| Framework | Elasticity | Mechanism |
|---|---|---|
| **Ray** | ✅ Native | Autoscaler adds/removes workers; tasks reschedule |
| **PyTorch Elastic (torchrun)** | ✅ | `--nnodes=MIN:MAX`; rendezvous handles membership changes |
| **Kubeflow Trainer** | Partial | Depends on the framework plugin |
| Spark | ✅ | Dynamic allocation |
| MPI | ❌ | Fixed world size; must restart |

**`elastic-policy.yaml`** — how the platform scales a job:
```
Grow:   when the tenant has free quota AND capacity exists AND the job is below max
        → add workers, one leaf-domain-aligned group at a time
        → ⚠️ rate-limit growth: a job that grows every 30 s thrashes the scheduler
Shrink: when capacity is reclaimed (preemption targets the newest workers first)
        → ⚠️ NEVER shrink below minReplicas — that breaks the gang (Phase 31)
        → prefer shrinking to preempting the whole job
```

> 💡 **Shrinking beats preempting.** Reclaiming 4 workers from a 16-worker elastic job costs that job 25 % throughput. Preempting the whole job costs it 100 % plus a restart. Configure Kueue's preemption to prefer partial reclaim from elastic workloads where the framework supports it.

> ⚠️ **Elastic training changes the math, not just the plumbing.** A PyTorch job whose world size changes mid-run has a different effective batch size, which changes the learning-rate schedule and can change convergence. Say this plainly in the user guide: **elastic training is a correctness concern, not just an ops feature.** Users must either use a batch-size-invariant setup or accept the variance.

---

### Task 6 — Declaring preemptibility and pricing it

**`require-preemption-declaration.yaml`** — every workload must state its position:

```yaml
metadata:
  labels:
    nexus.io/preemptible: "true"          # I checkpoint; take my GPUs when needed
    # or
    nexus.io/preemptible: "false"
  annotations:
    nexus.io/non-preemptible-reason: "Legacy simulation, no checkpoint support"
    nexus.io/non-preemptible-approved-by: "platform-team"   # required
```

**The incentive** (feeds Phase 34's showback):

| Declaration | Quota treatment | Showback rate |
|---|---|---|
| `preemptible: true` + low priority | Can borrow freely; wide access to idle capacity | **0.4× base rate** |
| `preemptible: true` + normal | Normal | 1.0× |
| `preemptible: false` | **Counts against nominal quota only — cannot borrow** | **1.5×** |

> 💡 **Make non-preemptible expensive rather than forbidden.** Some workloads genuinely cannot checkpoint. Banning them creates shadow IT; pricing them creates an incentive to fix the checkpointing, and meanwhile the cluster stays honest about what capacity is actually flexible.

---

### Task 7 — The user guide and reference templates

**`docs/user/checkpointing-guide.md`** — lead with the working example, not the theory.

```python
# The pattern the platform expects. Copy this.
import os, torch, signal

CKPT_DIR = "/checkpoints"
preempting = False

def on_sigterm(sig, frame):
    global preempting
    preempting = True                      # checkpoint at the next safe point
signal.signal(signal.SIGTERM, on_sigterm)

# ── Resume ────────────────────────────────────────────────
start_step = 0
resume = os.environ.get("NEXUS_RESUME_FROM")
if resume:
    state = torch.load(f"{resume}/state.pt", map_location="cpu")
    model.load_state_dict(state["model"]); opt.load_state_dict(state["opt"])
    start_step = state["step"]
    print(f"Resumed from step {start_step}")

# ── Train ─────────────────────────────────────────────────
for step in range(start_step, total_steps):
    train_one_step()
    if step % 500 == 0 or preempting:
        save_checkpoint(step)              # atomic: tmp dir → fsync → rename → COMPLETE
        if preempting:
            print("Preemption checkpoint written; exiting cleanly")
            sys.exit(0)                    # exit 0 — you were not a failure

def save_checkpoint(step):
    tmp = f"{CKPT_DIR}/.tmp-{step}"
    os.makedirs(tmp, exist_ok=True)
    if rank == 0:                          # ⚠️ ONE rank writes, not all of them
        torch.save({"model": model.state_dict(), "opt": opt.state_dict(),
                    "step": step}, f"{tmp}/state.pt")
        open(f"{tmp}/COMPLETE", "w").write(sha256_of(f"{tmp}/state.pt"))
    dist.barrier()
    if rank == 0:
        os.rename(tmp, f"{CKPT_DIR}/{step}")   # atomic
```

> ⚠️ **"One rank writes" matters at scale.** Sixteen ranks each writing a 40 GB checkpoint is 640 GB of simultaneous I/O and will saturate anything. Use rank 0 for the model state, or a sharded format (FSDP's `distributed_checkpoint`) that writes shards in parallel by design — never N full copies.

Also document: **exit 0 on preemption.** A job that exits non-zero when preempted will be counted as a failure, may trigger retry backoff, and will pollute the reliability metrics.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | Preemption notice file delivered before SIGTERM | 🧪 Preempt a test job | Present, correct deadline |
| **A2** | Grace period honored per priority class | Test each class | Honored |
| **A3** | **P7: no job preempted within its minimum runtime** | 🧪 Contend heavily | Never |
| **A4** | Checkpoint sidecar injected automatically | Submit a job with the annotation | Injected |
| **A5** | **Sidecar outlives the main container and flushes on SIGTERM** | 🧪 Preempt mid-write | Last checkpoint reaches S3 |
| **A6** | Node-level orphan drainer recovers checkpoints after a node failure | 🧪 Hard-kill a node | Recovered or explicitly reported lost |
| **A7** | Restore initContainer downloads the latest valid checkpoint | Restart a job | Restored |
| **A8** | Corrupt/incomplete checkpoints are rejected, older one used | 🧪 Corrupt one | Falls back correctly |
| **A9** | `NEXUS_JOB_ID` stable across preemption | Preempt and resume | Stable |
| **A10** | Atomic write pattern enforced in templates and documented | Read templates | Correct |
| **A11** | 🧪 **`preemption-drill.sh` full cycle < 5 min** | Run it | Met |
| **A12** | 📊 Lost work per preemption measured | Drill | < 15 min avg |
| **A13** | 📊 **Preemption efficiency computed** | Derived | Recorded |
| **A14** | `checkpoint-verify.sh` actually loads a checkpoint | Run it | Loads |
| **A15** | Nightly checkpoint verification scheduled | CronJob | Scheduled |
| **A16** | Elastic Ray job grows when capacity appears | 🧪 Free capacity | Grows |
| **A17** | Elastic job shrinks under reclaim instead of being killed | 🧪 Reclaim | Shrinks |
| **A18** | **Elastic job never shrinks below `minReplicas`** | Test | Never |
| **A19** | Growth is rate-limited | Observe | Limited |
| **A20** | `preemptible` declaration required on every workload | Submit without | Rejected |
| **A21** | Non-preemptible workloads cannot borrow quota | Test | Cannot |
| **A22** | Preempted jobs exit 0 and are not counted as failures | Check metrics | Correct |
| **A23** | Local checkpoint pruning keeps scratch from filling | Long-running test | Pruned |
| **A24** | Reference templates run end to end with a real preemption | 🧪 Run each | Both survive |

---

## ↩️ ROLLBACK

```bash
# Stop preemption entirely — jobs run to completion, utilization drops
kubectl patch clusterqueue --all --type merge \
  -p '{"spec":{"preemption":{"reclaimWithinCohort":"Never","withinClusterQueue":"Never"}}}'

# Remove the checkpoint sidecar injection (existing jobs keep theirs)
kubectl delete cpol inject-checkpoint-sidecar

# ⚠️ Do NOT delete checkpoint data in S3 as part of a rollback.
```

> 💡 **Disabling preemption is a safe, reversible pressure valve.** If checkpointing is not working reliably yet, turn preemption off, run with static quotas, fix checkpointing, turn it back on. Utilization suffers; nobody loses work.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Job killed without checkpointing | Application ignores SIGTERM | It is A1's contract — point them at the template |
| Checkpoint incomplete on restore | Non-atomic write | Enforce tmp→rename; A10 |
| Last checkpoint missing after preemption | Sidecar died with the main container | Use a native sidecar; A5 |
| Checkpoints lost on node failure | Only in `/scratch`, never synced | Node-level drainer (A6); reduce sync interval |
| Scratch fills with checkpoints | Pruning not working | Check the sidecar's retention (A23) |
| Job preempted immediately and repeatedly | Minimum runtime not enforced | A3 — fix the controller |
| Elastic job thrashing | Growth not rate-limited | A19 |
| Elastic training diverges | World-size change altered effective batch size | Documented risk — use invariant config or fix the LR schedule |
| Preempted jobs counted as failures | Exit code non-zero | Exit 0 on preemption; A22 |
| Restore downloads a stale checkpoint | Manifest not updated, or a sync lag | Check the sync sidecar's queue depth |
| Preemption cycle > 15 min | Slow checkpoint or slow restore | Profile which half; usually restore download — pre-warm to scratch |
| Nobody borrows despite idle capacity | Users disabled preemptibility | Check the showback incentive; check trust in the mechanism |

---

## 🚫 DO NOT

- **Do not** preempt a job without warning.
- **Do not** allow preemption within the minimum runtime.
- **Do not** let the checkpoint sync sidecar die with the main container.
- **Do not** write checkpoints from every rank.
- **Do not** write checkpoints non-atomically.
- **Do not** shrink an elastic job below its gang minimum.
- **Do not** treat a preempted job as a failed job.
- **Do not** ship a checkpoint mechanism you have not restored from (Phase 29's lesson).
- **Do not** implement accounting or showback here. Phase 34.

---

## 📤 HANDOFF

`evidence/phase-33/handoff.md` must state:

1. **📊 The full preemption cost breakdown** — warning, checkpoint, requeue, reschedule, restore, resume — and the total.
2. **📊 Preemption efficiency** — the number that tells Phase 30 how aggressive borrowing can safely be.
3. **The checkpointing contract as implemented**, both sides, and which guarantees are enforced by code vs. convention.
4. **How the sidecar-outlives-container problem is solved**, and the node-failure orphan path.
5. **Which frameworks support elasticity** in this cluster and the growth/shrink policy.
6. **The preemptible-vs-not incentive structure** — feeds Phase 34's rate card.
7. **🧪 Reference template results** — proof that both examples survive a real preemption.
8. **Workloads currently declared non-preemptible** and why — the list to shrink over time.

---

## ➡️ NEXT

**[PHASE-34 — Accounting, Showback & Utilization Analytics](PHASE-34.md)** — measure who used what, what it cost, and where the capacity actually went.
