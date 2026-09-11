# PHASE 14B — Availability Oracle & Harvest Node Onboarding

| | |
|---|---|
| **Stage** | C — Campus Harvest Plane |
| **Estimated effort** | 5–6 hours |
| **Depends on** | 14, 08B |
| **Blocks** | 19B, 25B, 31B, 33B |
| **Risk** | 🟠 R-21 (work loss), R-25 (stale timetable data) |
| **Blast radius** | Every scheduling decision on the harvest plane |
| **Architecture refs** | `CAMPUS-FABRIC.md#4-the-availability-model`, `#7-the-backend`, `ULTIMATE-PLAN.md#48-the-availability-boundary`, Law XII |

---

## 🎯 MISSION

Build the **Availability Oracle** — the controller that answers, per harvest node, continuously: *"how many seconds of uninterrupted compute can I promise on this machine, starting now, and at what confidence?"* — and publish that answer as node labels the scheduler consumes at admission time.

> 💡 **WHY this is the single highest-leverage phase in Track C.** Every other mechanism in the harvest backend reduces the *cost* of an eviction. This one reduces the *number* of evictions that should never have happened, by refusing to place a six-hour job on a machine that has fifty minutes left. It is the difference between W ≈ 20 % and W ≤ 5 % (`CAMPUS-FABRIC.md §3.2`), and without it the rest of the backend is patching damage the scheduler chose to cause.

> 🎯 **The distinguishing idea of this whole project.** ClusterOne schedules resources. NEXUS schedules *time*. The timetable is not tribal knowledge here — it is a first-class scheduling input, and it is *known future* rather than a statistical guess. Nothing else in the comparative review has an equivalent.

---

## ✅ PREFLIGHT

```bash
# 1. Phase 14 complete — NFD, the node labeler, taints, and pool membership exist
kubectl get nodes -L nexus.io/plane

# 2. Phase 08B complete — harvest nodes actually join, with lab/building labels and the taint
kubectl get nodes -l nexus.io/plane=harvest -L nexus.io/lab

# 3. Consent windows are machine-readable and validated
tools/campus/check-consent.sh

# 4. Timetable data exists with a stated confidence per lab
yq -r '.[] | [.lab, .confidence, (.weekly | length)] | @tsv' inventory/campus/timetable.yaml

# 5. Prometheus is receiving harvest node state transitions from 08B's controller
#    (Even a few days of history materially improves the model; zero history is acceptable
#     to start — the Oracle degrades to timetable-only, which is still the dominant signal.)
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/scheduling/campus/
  availability-oracle/
    deployment.yaml rbac.yaml config.yaml
  harvest-clusterqueue.yaml         # per-lab ClusterQueue in the harvest Cohort
  harvest-resourceflavors.yaml      # per-lab and per-GPU-model flavors
  workload-priority-classes.yaml    # harvest priority tiers
tools/campus/
  oracle-explain.sh                 # why does node X have tier Y right now — the debug tool
  timetable-lint.sh                 # validates timetable vs. consent windows
  backtest-oracle.py                # replays history: predicted vs. actual free windows
docs/campus/
  availability-model.md             # the model, its inputs, its failure modes
evidence/phase-14B/
  preflight.md plan.md acceptance.md handoff.md deviations.md
  backtest-report.md                # accuracy of the model against real reclaim history
```

---

## 📋 TASKS

### Task 1 — The prediction model

For each harvest node, compute `predictedFreeSeconds` at **p50** and **p10**. Inputs, in descending authority (`CAMPUS-FABRIC.md §4.1`):

| Signal | Source | Role |
|---|---|---|
| **Consent window** (04B) | `policies/campus/harvest-eligibility.yaml` | **Hard bound.** The Oracle may narrow a window, never widen it. A prediction that exceeds the signed window is a bug, not an optimization. |
| **Timetable** (01B) | `inventory/campus/timetable.yaml` | Dominant predictor — this is known future |
| **Reclaim history** | Prometheus, per lab per hour-of-week | Corrects the timetable for reality (rooms used off-schedule, clubs, project work) |
| **Live occupancy** | 08B controller heartbeat / input detection | Immediate override — overrides everything above, instantly |
| **Node health** | node-exporter, SMART, thermals, prior XIDs | Downgrades confidence; a flaky node gets a shorter promise |

```
window_end   = min(next timetable block start, consent window end, blackout start)
              − reserved_buffer_minutes                       # 01B, default 30 min

raw_free     = window_end − now

# Historical correction: what fraction of this lab-hour's windows actually ran to completion?
survival(lab, hour_of_week) = P(no unscheduled reclaim before window_end)   # from ≥4 weeks history

predictedFreeSeconds_p50 = raw_free × survival_p50
predictedFreeSeconds_p10 = raw_free × survival_p10            # ← the admission figure

# With no history: survival_p10 = 0.7 (conservative), survival_p50 = 0.9.
# Never assume 1.0. A room with no reclaim history is a room we do not understand yet.
```

> ⚠️ **Admission uses p10, not p50.** This is the single most consequential parameter choice in the phase. p50 placement is right half the time, and "right half the time" on a six-hour job means three hours of wasted electricity per failure. Pessimism is cheap here and optimism is not.

### Task 2 — Confidence tiers as node labels

Publish the model's output where the scheduler can use it:

```yaml
nexus.io/availability-tier: gold        # gold | silver | bronze | blocked
nexus.io/predicted-free-seconds: "48600"
nexus.io/predicted-free-p10: "43740"
nexus.io/window-closes-at: "2026-09-15T07:30:00+06:00"
nexus.io/oracle-confidence: "historical"   # historical | timetable-only | degraded
```

| Tier | p10 window | Admits |
|---|---|---|
| **Gold** | > 8 h | Single-node training, long sweeps, anything checkpointed |
| **Silver** | 1–8 h | Batch inference, preprocessing, medium trials |
| **Bronze** | 5–60 min | CI, short trials, EP shards, rendering tiles |
| **Blocked** | < 5 min, lab in session, blackout, or withdrawn | Nothing — node is cordoned |

> ⚠️ **Publish on a fixed cadence (30–60 s), not per-event.** Several hundred nodes each emitting label updates on every telemetry change will produce an etcd write storm and a scheduler that spends its time reconciling labels (`ULTIMATE-PLAN.md §9`, harvest scaling cliffs). Batch the reconciliation.

### Task 3 — Deadline-aware admission

The admission rule from `CAMPUS-FABRIC.md §4.2`, implemented as a Kueue admission check plus a validating webhook:

```
admit(job, node) requires:
      job.estimatedRuntime          ≤ node.predictedFreeSeconds_p10
   OR job.checkpointInterval × 1.5  ≤ node.predictedFreeSeconds_p10

and always:
      job.checkpointInterval ≤ 900 s
      job tolerates nexus.io/harvest
      job.dataClass ∈ lab's permitted dataClasses          (04B)
      lab is not withdrawn / in blackout                    (04B)
```

**Jobs must declare `estimatedRuntime` and `checkpointInterval`.** Undeclared jobs get conservative defaults (`estimatedRuntime: unknown` → Bronze only, `checkpointInterval: 900 s` assumed and enforced by 31B). Reject rather than guess when the mismatch would waste hours.

> 💡 **The rejection is the feature.** A user whose 6-hour job is refused at 07:00 and queued for the 18:30 Gold window has lost nothing — it would have died at 08:00 anyway. Make the rejection message say exactly that, with the next available window, or users will experience the Oracle as an obstacle rather than as the thing that saved their run.

### Task 4 — Queue topology

**`harvest-clusterqueue.yaml`** — one `ClusterQueue` per lab, all in a shared harvest `Cohort` so idle labs lend capacity to busy ones:

```yaml
# Per lab: cpu, memory, gpu (per model), and nexus.io/uplink-mbps (03B)
# Cohort: harvest-campus  → borrowing enabled, lending enabled
# Preemption: harvest workloads are preemptible by definition; within the plane,
#             priority order is  interactive > sweep > batch > ci
# Cross-plane rule: a harvest job NEVER preempts or delays a Core Plane job,
#                   and a Core Plane job never lands on a harvest node.
```

**`workload-priority-classes.yaml`** extends `ULTIMATE-PLAN.md §10`'s ladder with harvest-only tiers below `spot(50)`, so nothing on the harvest plane can outrank Core Plane work.

### Task 5 — The explain tool

Operators and users will both ask "why did my job not land there?" Answer it in one command:

```bash
tools/campus/oracle-explain.sh --node hv-cse402-07
# node:            hv-cse402-07  (lab cse-402, building daffodil-tower)
# now:             2026-09-15 21:14 +06
# consent window:  18:30 → 07:30           (signed 2026-09-12)
# next class:      2026-09-16 08:30        (timetable, authoritative)
# buffer:          30 min → window_end 07:30
# raw_free:        37,000 s
# survival p10:    0.94  (4 weeks history, 2 unscheduled reclaims observed)
# → tier GOLD, predicted_free_p10 = 34,780 s
# admits: single-node training ≤ 9.6 h, or any job checkpointing ≤ 15 min
```

An Oracle nobody can interrogate becomes an Oracle nobody trusts, and an untrusted scheduler gets worked around.

### Task 6 — Backtest before you trust it

**`tools/campus/backtest-oracle.py`** replays recorded history: for every past window, what would the Oracle have predicted, and what actually happened?

Report in `evidence/phase-14B/backtest-report.md`:

| Metric | Target | Meaning |
|---|---|---|
| **Over-promise rate** (actual < predicted p10) | **≤ 10 %** | The dangerous error — it causes work loss |
| Under-promise rate (actual ≫ predicted p10) | ≤ 40 % | Wasteful but safe; tighten later, once history is deep |
| Mean absolute error, p50 | < 25 % of window | General model quality |
| Labs with insufficient history | listed explicitly | These run timetable-only; say so |

> 💡 **Asymmetric errors deserve asymmetric targets.** Over-promising wastes compute and breaks the design's central claim; under-promising only leaves capacity on the table, which 33B will show you and you can tune back. Start conservative, earn the tighter bound with data.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Command |
|---|---|---|
| 1 | Every harvest node carries all five availability labels, refreshed within 60 s | `kubectl get nodes -l nexus.io/plane=harvest -L nexus.io/availability-tier` |
| 2 | No published window exceeds the lab's signed consent window | `tools/campus/timetable-lint.sh --strict` |
| 3 | A job whose `estimatedRuntime` exceeds p10 is rejected, with the next viable window named | submit a deliberate 6 h job at 07:00; capture the rejection message |
| 4 | A job with `checkpointInterval ≤ 900 s` is admitted to the same node | submit and observe |
| 5 | Tier transitions at window boundaries occur within 60 s and cordon on `blocked` | observe a real window close |
| 6 | Label updates are batched — measured etcd write rate stays flat as node count grows | Prometheus `etcd_writes` vs. harvest node count |
| 7 | Backtest over-promise rate ≤ 10 % (or, with < 4 weeks of history, the conservative defaults are in force and documented) | `backtest-oracle.py --report` |
| 8 | `oracle-explain.sh` produces a complete, human-readable derivation for any node | run against 3 nodes in different tiers |
| 9 | A withdrawn lab moves to `blocked` and cordons within 5 min | withdrawal drill, timed |
| 10 | No harvest workload preempts or delays a Core Plane workload | Kueue priority test |

---

## ↩️ ROLLBACK

Set the Oracle to `degraded` mode: every node reports `tier: bronze` with a 15-minute p10. The fabric keeps running with short, cheap work only, and nothing is lost but throughput. **The degraded state must be the automatic response to Oracle failure**, not a manual one — a scheduler that keeps placing six-hour jobs because the Oracle stopped answering is worse than one that stops.

---

## 🧯 TROUBLESHOOTING

| Symptom | Cause | Action |
|---|---|---|
| All nodes stuck at Bronze | No timetable data, or `confidence: assumed` everywhere | Correct — that is the design being honest. Improve timetable data quality to unlock Gold. |
| Over-promise rate high in one lab | Room used off-schedule (clubs, project work) | The historical correction will learn it within ~4 weeks. Meanwhile, manually narrow the window and tell the owner what you observed. |
| etcd write rate climbing with node count | Per-event label updates | Batch reconciliation (Task 2). This is a known scaling cliff. |
| Users complain jobs are rejected "for no reason" | Rejection message is unhelpful | Fix the message, not the threshold. Include the derivation and the next available window. |
| Oracle predicts a window past a class start | Timetable stale, or buffer not applied | `timetable-lint.sh --strict` in CI. This is an R-19 precursor — treat it as urgent. |
| A lab's history contradicts its signed window | The owner's stated window is optimistic | Trust the history, narrow the prediction, and mention it at the next conversation. Never widen past the signature. |

---

## 🚫 DO NOT

- **Do not let the Oracle widen a window beyond the signed consent window, ever, for any reason.**
- Do not use p50 for admission.
- Do not default `survival` to 1.0 for labs with no history.
- Do not publish labels per-event.
- Do not implement the eviction or checkpointing path here — that is 31B. This phase decides *where work goes*; 31B handles *what happens when it must leave*.
- Do not implement harvest accounting here — that is 33B.
- Do not let harvest priority classes outrank any Core Plane class.

---

## 🤝 HANDOFF — write `evidence/phase-14B/handoff.md`

Must state:

- Per lab: current tier distribution across the week, and the confidence level of its prediction (`historical` vs `timetable-only`).
- The backtest over-promise rate, and which labs are driving it.
- The `survival` defaults in force and when they should be revisited.
- The declared-runtime contract handed to 36B: what fields users must set, and what happens when they do not.
- Any lab whose signed window and observed reality disagree — this is a conversation for the next owner check-in, not a parameter to quietly adjust.
