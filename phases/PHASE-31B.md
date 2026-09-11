# PHASE 31B — Preemption-First Scheduling & Tiered Checkpointing

| | |
|---|---|
| **Stage** | C — Campus Harvest Plane |
| **Estimated effort** | 6–7 hours |
| **Depends on** | 31, 14B, 25B |
| **Blocks** | 19B, 33B, 36B, 52B |
| **Risk** | 🔴 R-19 (Law XI enforcement lives here), 🟠 R-21 (work loss) |
| **Blast radius** | Every running harvest workload, and every promise made to a lab owner |
| **Architecture refs** | `CAMPUS-FABRIC.md#7-the-backend` (M2, M3), `#63-the-node-state-machine`, `ULTIMATE-PLAN.md#48-the-availability-boundary`, Laws XI–XII, Gates G15/G16 |

---

## 🎯 MISSION

Build the **eviction path and the checkpoint hierarchy**: release a borrowed machine to a detected human in **under 10 seconds**, lose at most one checkpoint interval of work, and restart preferentially on a machine in the same lab where the data already is.

> ⚠️ **This phase is where Law XI stops being a sentence and becomes a mechanism.** Promise 1 in every signed lab agreement — *"the human always wins"* — is enforced by the code written here and by nothing else. Gate G16 is not a performance target; it is the audit of a commitment made in writing to people who trusted us.

> 💡 **WHY preemption-first rather than preemption-tolerant.** Most schedulers treat preemption as an exception path: rare, expensive, bolted on. On the harvest plane it is the *normal* case — several hundred nodes are reclaimed twice a day by design. A system that treats the common case as an exception spends its life in its slowest code path. Here, eviction is the hot path and it is engineered as one.

---

## ✅ PREFLIGHT

```bash
# 1. Oracle publishing tiers and enforcing deadline-aware admission
kubectl get nodes -l nexus.io/plane=harvest -L nexus.io/availability-tier,nexus.io/window-closes-at

# 2. The lab cache and checkpoint landing zone exist and are reachable from harvest nodes
kubectl get pods -n nexus-campus -l app=lab-cache -o wide

# 3. Phase 31 complete — the core preemption/checkpointing machinery for Plane A exists;
#    this phase specializes it rather than duplicating it
test -f clusters/nexus-prod/scheduling/preemption.yaml

# 4. Measured T-lab flush latency from 25B's handoff (the checkpoint budget depends on it)
grep -i 'flush latency' evidence/phase-25B/handoff.md
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/scheduling/campus/
  eviction-controller/              # the reclaim trigger → release path
    deployment.yaml rbac.yaml config.yaml
  checkpoint-sidecar/               # tiered flush, injected by webhook
    daemonset.yaml webhook.yaml
  harvest-pod-defaults.yaml         # terminationGracePeriod, lifecycle hooks, tolerations
  restore-affinity.yaml             # same-lab restart preference
tools/campus/
  evict-trace.sh                    # instrumented eviction: every stage timestamped (G16)
  checkpoint-verify.sh              # proves a checkpoint restores correctly, tier by tier
  chaos-reclaim.sh                  # random reclaim injection for W measurement
docs/campus/
  eviction-design.md                # the three-stage yield, the budgets, the failure modes
  checkpoint-contract.md            # what a workload must implement to be Gold-eligible
evidence/phase-31B/
  preflight.md plan.md acceptance.md handoff.md deviations.md
  eviction-traces/                  # ≥100 samples for the G16 p99
```

---

## 📋 TASKS

### Task 1 — The three-stage yield

The mechanism behind M2 (`CAMPUS-FABRIC.md §7`). **The machine is released before the flush completes** — this is the key design decision and the reason 10 seconds is achievable at all.

```
t+0.0s   RECLAIM TRIGGER
         (human input detected | class window closing | node health | lab withdrawn)

t+0.1s   Stage 1 — FREEZE
         SIGTERM to the workload; cgroup frozen if it does not respond in 500 ms.
         Node cordoned. No new admission. GPU released.

t+0.5s   Stage 2 — CHECKPOINT TO LOCAL
         In-process hook writes state to T-local (tmpfs/scratch).
         Budget: 2 s. If it overruns, the checkpoint is abandoned, not extended.
         ⚠️ We never make a human wait for our checkpoint. Ever.

t+3.0s   Stage 3 — RELEASE
         Containers terminated, cgroups torn down, machine handed back.
         ── THE HUMAN HAS THE MACHINE FROM HERE. Budget: t+10 s p99. (G16)

t+3.0s   ...asynchronously, on the node's remaining lifetime or the seed's:
         flush T-local → T-lab   (target < 60 s)
         flush T-lab   → T-core  (target < 10 min)
         If the machine dies before the T-lab flush, we lose one interval. Accepted.
```

> ⚠️ **Never block release on a flush.** The temptation to hold a machine "just five more seconds" to save a checkpoint is exactly how promise 1 dies. The design's answer is to make the local write fast (tmpfs, 2 s) and the durable write asynchronous. If the flush fails, we lose one interval — which is what the whole checkpointing scheme is budgeted for.

### Task 2 — Reclaim triggers

| Trigger | Detection | Notice | Path |
|---|---|---|---|
| **Human input detected** (Mode B) | Agent heartbeat: keyboard/mouse/session state | 0 s | Full three-stage yield, immediately |
| **Window closing** (Mode A) | Oracle `window-closes-at` − buffer | Minutes | Graceful: stop admitting → let short work finish → checkpoint → drain → shutdown |
| **Class starting early** | Timetable + occupancy override | Seconds | Full three-stage yield |
| **Lab withdrawn** (04B) | GitOps reconcile | Seconds | Full three-stage yield across the whole lab |
| **Node unhealthy** (thermal, SMART, XID) | node-exporter / DCGM | Seconds | Yield + quarantine |
| **Power loss** | None | **0 s, no notice** | Nothing to do. Requeue from last durable checkpoint. This is the case the tiering exists for. |

**The graceful path (Mode A window close) is different from the abrupt path and must be implemented separately.** When we have minutes of notice, we should use them: stop admitting new work, let anything with less than the remaining window finish naturally, and only checkpoint what genuinely cannot complete. A fabric that hard-evicts everything at 07:00 wastes work it had time to finish.

### Task 3 — Tiered checkpointing

M3 (`CAMPUS-FABRIC.md §7`), built on 25B's data path:

```
WRITE   T-local (tmpfs)         < 2 s      always, synchronous, in the yield path
        T-lab   (cache seed)    < 60 s     async, survives node loss
        T-core  (object store)  < 10 min   async, survives lab loss — the durable copy

READ    same-lab node           ~1 s       ← strongly preferred (Law VI, data gravity)
        T-core                  seconds    fallback
```

**`docs/campus/checkpoint-contract.md`** — what a workload must provide to be admitted above Bronze:

| Requirement | Detail |
|---|---|
| Checkpoint hook | Responds to `SIGTERM`, writes state to `$NEXUS_CHECKPOINT_DIR` within 2 s |
| Interval | `checkpointInterval ≤ 900 s`, declared in the job spec |
| Idempotent restart | Restarting from a checkpoint yields the same result as an uninterrupted run |
| Atomicity | Write to a temp path, then rename. A truncated checkpoint must never be restorable. |
| Size discipline | Checkpoint ≤ 25 % of node RAM; larger states go to T-lab incrementally, not in the yield path |

For frameworks that provide this natively (PyTorch Lightning, Ray Train, HF Trainer), 36B ships templates. For those that do not, the fallback is Bronze-tier only: short work that is cheap to redo.

> 💡 **CRIU-style transparent process checkpointing is deliberately not the default.** It is attractive and it does not survive contact with GPU state, open sockets, or heterogeneous restore targets. Application-level checkpointing is boring, portable, and works — Law X. Revisit CRIU only for CPU-only workloads where it demonstrably beats the alternative.

### Task 4 — Local-first restore

A restart that re-reads its inputs over the uplink converts a cheap eviction into an expensive one — and charges the lab's uplink quota for the privilege.

**`restore-affinity.yaml`**:

```yaml
# Preferred, not required — a lab in session must never hold a job hostage.
preferredDuringSchedulingIgnoredDuringExecution:
  - weight: 100
    preference: { matchExpressions: [{ key: nexus.io/lab, operator: In, values: ["<origin-lab>"] }] }
  - weight: 50
    preference: { matchExpressions: [{ key: nexus.io/building, operator: In, values: ["<origin-building>"] }] }
```

The restart also carries the checkpoint's tier location, so the scheduler prefers a node that can read it from T-lab rather than T-core.

**Bound the retry.** A job that has been evicted more than N times (default 5) is escalated: routed to the Core Plane, or queued for the next Gold window, or failed with a clear message. **A job that keeps getting evicted and restarted is the purest form of the waste this phase exists to eliminate** — cap it and report it.

### Task 5 — Instrument the eviction (G16)

**`tools/campus/evict-trace.sh`** must emit, per eviction, a complete timeline:

```
eviction_id: ev-8842
node: hv-cse402-07   lab: cse-402   trigger: human-input
  t+0.000  trigger observed
  t+0.043  SIGTERM sent
  t+0.610  workload checkpoint hook returned          (bytes: 412 MiB → T-local)
  t+1.980  containers terminated
  t+2.310  cgroups released, node cordoned            ← MACHINE RELEASED (2.31 s)
  t+48.10  T-local → T-lab flush complete
  t+340.2  T-lab → T-core flush complete
  requeued: 2.4 s later on hv-cse402-19 (same lab, T-lab restore, 1.1 s)
  work lost: 214 s  (time since last checkpoint)
```

**Collect ≥ 100 traces before claiming G16.** The p99 is the gate, and a p99 measured from ten samples is not a p99. Include the ugly cases: large checkpoints, unresponsive workloads, simultaneous whole-lab eviction.

### Task 6 — Measure work loss (feeds G15)

**`tools/campus/chaos-reclaim.sh`** injects random reclaims at realistic rates and measures W:

```
W = wasted_node_seconds / harvested_node_seconds

wasted = Σ over evictions of (time since last durable checkpoint)
       + Σ over restarts of (re-read/re-setup time attributable to the eviction)
```

Run against a representative workload mix. This is not yet Gate G15 — that is 33B's continuous, fleet-scale measurement — but **if W is not already trending under ~8 % here, on a controlled test, it will not reach 5 % in production.** Find out now, while the variables are still controllable.

Expected contributions, for debugging a high W:

| Contribution | Healthy | If high, look at |
|---|---|---|
| Time since last checkpoint | ≤ 450 s avg (half of a 900 s interval) | Interval too long, or hook failing silently |
| Restore re-read | < 30 s | Local-first restore not working; T-lab misses (25B) |
| Repeated eviction of the same job | ~0 | Oracle over-promising (14B), or retry cap missing |
| Abandoned checkpoints (2 s overrun) | < 2 % | Checkpoint too large for the yield path |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Command |
|---|---|---|
| 1 | **G16: human-input trigger → machine released, p99 ≤ 10 s over ≥ 100 traces** | `evict-trace.sh --report`, traces committed |
| 2 | Mode A drain completes before the window closes, 100 % of observed windows | window-close audit over ≥ 10 windows |
| 3 | Release never waits on a T-lab or T-core flush | trace inspection: release timestamp precedes flush completion |
| 4 | A checkpoint written to T-local restores correctly from T-lab and from T-core | `checkpoint-verify.sh --tier lab --tier core` |
| 5 | A truncated/partial checkpoint is never restored | fault injection: kill mid-write, confirm rejection |
| 6 | Restart prefers a same-lab node when one is available | 20 induced evictions; ≥ 80 % same-lab placement |
| 7 | A job evicted > 5 times is escalated, not endlessly retried | chaos run + escalation log |
| 8 | Measured W under chaos ≤ 8 % on the representative mix | `chaos-reclaim.sh --report` |
| 9 | Whole-lab simultaneous eviction (withdrawal drill) releases all 30 machines within 10 s p99 | timed drill |
| 10 | A workload with no checkpoint hook is admitted at Bronze only, and is never placed on Gold work | admission test |

---

## ↩️ ROLLBACK

Set the harvest plane to **Bronze-only**: nothing longer than 15 minutes is admitted anywhere. Eviction then costs at most 15 minutes of work regardless of whether the checkpoint path functions. Throughput drops sharply; correctness and Law XI do not. **This is the correct response to any doubt about the eviction path** — never the response of admitting longer jobs and hoping.

---

## 🧯 TROUBLESHOOTING

| Symptom | Cause | Action |
|---|---|---|
| Release p99 above 10 s | Checkpoint hook overrunning, or container teardown slow | Enforce the 2 s abandon rule harder. **Never extend the budget** — abandon the checkpoint instead. |
| Workload ignores SIGTERM | No hook, or a framework that traps it | Freeze the cgroup and kill. Then fix the workload's template (36B) so it does not recur. |
| High W, checkpoints look healthy | Restore re-reading over the uplink | T-lab misses. Check 25B's hit rate and the restore affinity weights. |
| Same job evicted repeatedly | Oracle over-promising, or the job's `estimatedRuntime` is wrong | Check 14B's backtest for that lab; check whether the user's declared runtime matches reality. |
| Checkpoints exceed node RAM | Large-state workload on a small machine | Admission must reject this. Route to Core Plane or to larger harvest nodes. |
| T-lab flush queue growing | Seed saturated or flush shaped too hard | Alert on queue depth. A backlog means checkpoints are not durable — treat as urgent (§3.3). |
| Whole-lab eviction slower than single-node | Serialized teardown | Parallelize per-node; the drill in criterion 9 exists to catch exactly this. |

---

## 🚫 DO NOT

- **Do not block machine release on any flush, for any reason, ever.**
- Do not extend the 2-second local checkpoint budget to save a large checkpoint.
- Do not restore from a checkpoint that was not atomically committed.
- Do not retry an evicted job indefinitely.
- Do not use CRIU for GPU workloads.
- Do not implement the accounting/reporting here — that is 33B. This phase must *emit* the metrics; 33B classifies and gates on them.
- Do not implement workload templates here — that is 36B. This phase defines the contract they must satisfy.

---

## 🤝 HANDOFF — write `evidence/phase-31B/handoff.md`

Must state:

- The G16 p99 release time, the sample size, and the worst observed case with its cause.
- Measured W under chaos, broken down by contribution, and the largest lever remaining.
- The checkpoint contract as shipped, and which frameworks satisfy it natively — 36B builds templates from this list.
- Observed T-lab and T-core flush latencies, and the flush failure rate.
- The retry cap in force and how escalation behaves.
- Any workload class that cannot meet the contract and is therefore Bronze-only — users need this list before they plan work.
