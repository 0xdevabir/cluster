# PHASE 33B — Harvest Efficiency Accounting (G15)

| | |
|---|---|
| **Stage** | C — Campus Harvest Plane |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 33, 31B, 45 |
| **Blocks** | 52B, and **all harvest scale-out past 120 nodes** |
| **Risk** | 🟠 R-21 — an unmeasured harvest fabric is an unfalsifiable claim |
| **Blast radius** | The credibility of the entire campus plane |
| **Architecture refs** | `CAMPUS-FABRIC.md#32-work-loss`, `#7-the-backend` (M7), `#11-harvest-slos--gates`, `ULTIMATE-PLAN.md#48-the-availability-boundary`, Law XII, Gate G15 |

---

## 🎯 MISSION

Classify **every borrowed node-second** as useful, wasted, overhead, or idle-unharvested; compute the work-loss ratio W continuously; and enforce **Gate G15 (W ≤ 5 %)** as a hard block on further scale-out.

> 💡 **WHY this is a gate and not a dashboard.** The central claim of the campus plane is that borrowed capacity can be harvested efficiently. That claim is either true and measurable, or it is marketing. A fabric running at W = 30 % consumes the department's electricity and its goodwill to produce two-thirds of the work it appears to. **Measuring this is what separates this plan from a well-intentioned screensaver**, and it is the number the ClusterOne comparison has no equivalent of.

> 🎯 **`idle-unharvested` is the growth signal.** It is the machine-hours the fleet was entitled to and did not take — because a lab was not enrolled, a node failed to wake, an image was cold, or the queue was empty. It tells you exactly where the next fifty machines are, and it is usually the largest bucket in the first months.

---

## ✅ PREFLIGHT

```bash
# 1. Eviction traces flowing from 31B, with the fields the classifier needs
grep -c 'eviction_id' evidence/phase-31B/eviction-traces/*.jsonl

# 2. Observability stack live (Phase 45) — Prometheus/Mimir with harvest metrics
kubectl get pods -n monitoring

# 3. Node state transitions from 08B's controller are being recorded
promtool query instant <url> 'harvest_node_state_transitions_total'

# 4. Cache accounting from 25B (bytes local vs. remote)
tools/campus/cache-report.sh --lab <any> --last-window

# 5. Phase 33 complete — the core accounting/showback machinery exists
test -f clusters/nexus-prod/observability/accounting.yaml
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/observability/campus/
  harvest-recording-rules.yaml      # the four classifications + W, as recording rules
  harvest-alerts.yaml               # W regression, over-promise rate, idle-unharvested spikes
  dashboards/
    harvest-efficiency.json         # THE dashboard — W, yield, per-lab breakdown
    harvest-capacity.json           # what we have, what we took, what we missed
    lab-health.json                 # per-lab: wake rate, evictions, thermals, uplink
tools/campus/
  harvest-report.sh                 # weekly report: W, yield, per-lab, per-tenant
  w-explain.sh                      # decompose W into its contributing causes
  gate-g15.sh                       # the CI/scale-out gate
docs/campus/
  efficiency-model.md               # the classification rules and their edge cases
evidence/phase-33B/
  preflight.md plan.md acceptance.md handoff.md deviations.md
  weekly-reports/                   # ≥1 full 7-day report at ≥100 nodes for G15
```

---

## 📋 TASKS

### Task 1 — Classify every node-second

Four buckets, mutually exclusive, jointly exhaustive. **Every second a machine could have been ours must land in exactly one.**

| Class | Definition | Healthy share |
|---|---|---|
| **useful** | Compute that produced a durable result — work whose output reached T-core, or whose checkpoint was durably flushed | **≥ 80 %** of harvested |
| **wasted** | Compute discarded because a node was reclaimed before the result was durable, plus attributable re-execution | **≤ 5 %** (G15) |
| **overhead** | Boot, join, image pull, dataset warm, drain, shutdown — real cost, no direct output | ≤ 10 % |
| **idle-unharvested** | The window was open and the machine was ours to take, and we did not use it | Reported, not gated |

```
harvested_node_seconds = useful + wasted + overhead
theoretical_node_seconds = harvested + idle-unharvested

W     = wasted / harvested                                    ← Gate G15, ≤ 5 %
yield = useful / theoretical_node_seconds                      ← Gate G19, ≥ 60 %
```

> ⚠️ **Classification honesty is the whole phase.** There will be a temptation to book ambiguous seconds as `overhead` rather than `wasted`, because overhead is not gated. Resist it and write the rule down: *if the compute produced no durable result and the cause was a reclaim, it is wasted — regardless of how briefly.* A gate you can pass by reclassification is not a gate.

### Task 2 — Decompose W

**`tools/campus/w-explain.sh`** must attribute every wasted second to a cause, because "W is 12 %" is not actionable and "W is 12 %, of which 9 points are checkpoint intervals in three labs whose Oracle over-promises" is:

| Cause | Typical share | Owning fix |
|---|---|---|
| Time since last checkpoint at eviction | Largest, by design | Shorter intervals (31B), or better Oracle placement (14B) |
| Re-read / re-setup after restart | Should be small | T-lab misses (25B), restore affinity (31B) |
| Repeated eviction of the same job | Should be ~0 | Oracle over-promise (14B), retry cap (31B) |
| Abandoned checkpoints (2 s budget overrun) | < 2 % | Checkpoint size discipline (31B contract) |
| Work completed but flush failed | Should be ~0 | T-core flush reliability (25B) — **investigate any occurrence** |
| Non-idempotent restart producing wrong results | **Must be 0** | A correctness bug, not an efficiency one. Escalate immediately. |

### Task 3 — Per-lab and per-tenant reporting

**Per lab** — this is what you show a lab owner, and it is how you keep consent:

```
cse-402, week of 2026-09-14
  machines enrolled:        32  (1 excluded by agreement, 2 quarantined)
  window hours available:   103.0 h/machine
  harvested:                 96.1 h/machine  (93.3 %)
  useful:                    86.4 h/machine  (89.9 % of harvested)
  wasted:                     3.4 h/machine  (3.5 %)   ✅ under G15
  overhead:                   6.3 h/machine  (6.6 %)
  idle-unharvested:           6.9 h/machine  — queue empty on Fri/Sat nights
  evictions:                 41  (39 window-close, 2 human-detected)
  human-detected release p99: 4.2 s          ✅ under G16
  teaching-time incidents:    0              ✅
  uplink peak share:         52 % of ceiling ✅
  room temp rise (max):      +4.1 °C         ✅
```

**Per tenant** — extends Phase 33's showback with a harvest column, so a research group can see it received 4,000 GPU-hours it did not pay for and what fraction of its own work it wasted through poor checkpointing.

> 💡 **Send the per-lab report to the lab owner monthly, unprompted.** It converts an abstract arrangement into a visible contribution, it demonstrates the promises are being kept with numbers rather than assurances, and it makes withdrawal much less likely. This is the cheapest retention mechanism in the project.

### Task 4 — The G15 gate

**`tools/campus/gate-g15.sh`** — blocks scale-out past 120 harvest nodes:

```bash
tools/campus/gate-g15.sh --window 7d --min-nodes 100
# PASS  W = 4.1 %  over 7 days, 118 nodes, 12,400 harvested node-hours
# ...or...
# FAIL  W = 11.3 % over 7 days, 118 nodes
#       largest contributor: checkpoint interval in cse-402/cse-403 (7.2 pts)
#       → see w-explain.sh; do not enrol further labs until resolved
```

**Requirements for a valid G15 measurement:** ≥ 7 consecutive days, ≥ 100 harvest nodes, and a workload mix that includes at least one Gold-tier long job. Measuring W on a week of nothing but 5-minute CI jobs proves nothing — short work is trivially cheap to lose.

Wire it into the enrolment path: adding a lab to `harvest-eligibility.yaml` beyond the 120-node threshold fails CI unless the most recent G15 report passes.

### Task 5 — Alerts that matter

| Alert | Condition | Why |
|---|---|---|
| `HarvestWorkLossRegression` | W > 7 % over 24 h | Early warning before the gate fails |
| `HarvestOverPromise` | Oracle over-promise rate > 15 % in any lab | 14B's model degrading; usually a room's usage pattern changed |
| `HarvestFlushBacklog` | T-lab → T-core queue depth rising for 30 min | Checkpoints not durable — a correctness risk, not just efficiency |
| `HarvestIdleSpike` | idle-unharvested > 40 % for 3 consecutive windows | Queue empty, wake failures, or cold caches — free capacity going unused |
| `HarvestUplinkCeiling` | Any lab exceeds its ceiling for > 60 s | **R-19 precursor. Page, do not just log.** |
| `HarvestTeachingIncident` | Any manually-flagged incident | Zero tolerance; G18 counts from the last one |

Every alert needs a runbook before it ships (`ULTIMATE-PLAN.md` G14 applies here too).

### Task 6 — Close the loop on capacity

`idle-unharvested`, decomposed, is the growth roadmap:

| Reason idle | What it means | Action |
|---|---|---|
| Queue empty | We have more capacity than demand | Good problem. Recruit users (36B's guide), not machines. |
| Wake failure | R-26 — machines that will not come up | The BIOS pass, or genuinely WoL-incapable hardware |
| Cold cache / slow start | 25B underperforming in that lab | Warming schedule, seed placement |
| Lab not enrolled | The largest bucket early on | 04B — go sign another agreement |
| Blocked by tier | Windows too short for the queued work | 36B's sizer, or better Oracle data |

Report this decomposition every week. **It is the honest answer to "how big can this get?"** — a question the department will ask, and one that deserves a number derived from measurement rather than from the survey's optimistic ceiling.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Command |
|---|---|---|
| 1 | Every harvested node-second is classified into exactly one of the four buckets; the four sum to the theoretical total within 1 % | `harvest-report.sh --reconcile` |
| 2 | **G15: W ≤ 5 % over a rolling 7 days at ≥ 100 harvest nodes**, with a Gold-tier job in the mix | `gate-g15.sh --window 7d`, report committed |
| 3 | `w-explain.sh` attributes ≥ 95 % of wasted seconds to a named cause | run against the G15 window |
| 4 | Per-lab reports generate for every enrolled lab and include the promise-keeping metrics (G16 release time, incidents, uplink, temperature) | `harvest-report.sh --all-labs` |
| 5 | Per-tenant showback includes harvest GPU-hours and each tenant's own waste | dashboard review |
| 6 | The G15 gate blocks a simulated enrolment past 120 nodes when W is failing | inject a failing W, attempt enrolment, show the red build |
| 7 | Every alert has a runbook | alert-to-runbook coverage report = 100 % |
| 8 | `idle-unharvested` is decomposed by reason | weekly report review |
| 9 | Non-idempotent-restart count is zero; any occurrence is escalated as a correctness bug | dashboard + incident log |

---

## ↩️ ROLLBACK

The accounting is read-only; there is nothing to roll back except the gate. **Do not disable the gate to unblock enrolment.** If W is failing, the correct action is to stop enrolling and fix the cause — that is precisely what the gate is for, and disabling it converts a measured system back into an unfalsifiable claim.

---

## 🧯 TROUBLESHOOTING

| Symptom | Cause | Action |
|---|---|---|
| Buckets do not reconcile to the total | Missing state transitions, or double-counted seconds | Fix the classifier before trusting any number it produces. A W computed from an unreconciled model is noise. |
| W dominated by one lab | That lab's Oracle model is over-promising, or its cache is cold | `w-explain.sh --lab`; usually 14B's historical correction has not converged yet |
| W looks suspiciously good | Workload mix is all short jobs | Check the mix. G15 requires Gold-tier work in the window. |
| High overhead share | Cold starts, or excessive wake/drain cycling | 25B warming; consider longer windows where the agreement allows |
| idle-unharvested very high | Usually: not enough users | This is a demand problem, and it is solved in 36B and 46, not here |
| Flush backlog alert firing | T-core write path degraded | Treat as urgent — checkpoints that never become durable mean W is about to spike |
| A lab's report shows an incident | Something reached a human | Stop. Investigate. Talk to the owner before they talk to you. G18 resets. |

---

## 🚫 DO NOT

- Do not book ambiguous seconds as `overhead` to protect the gate.
- Do not measure W on a workload mix of only short jobs.
- Do not disable or weaken the G15 gate to unblock enrolment.
- Do not report yield without also reporting `idle-unharvested` — the flattering half of the number is not the number.
- Do not ship an alert without a runbook.
- Do not treat a non-idempotent-restart occurrence as an efficiency issue. It is a correctness bug.
- Do not enrol beyond 120 nodes on a passing W measured over less than 7 days.

---

## 🤝 HANDOFF — write `evidence/phase-33B/handoff.md`

Must state:

- The G15 verdict, with the measurement window, node count, workload mix, and the committed report.
- W's decomposition and the single largest remaining lever.
- Per-lab efficiency, and which labs are dragging the average and why.
- `idle-unharvested` decomposed — the concrete growth roadmap handed to 52B.
- Whether demand or supply is currently the binding constraint. **52B's staged expansion plan depends entirely on this answer**, and getting it wrong means enrolling machines nobody will use.
