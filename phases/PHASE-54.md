# PHASE 54 — Chaos Engineering & Disaster Recovery (G10)

| | |
|---|---|
| **Stage** | 9 — Operations & Sustainment |
| **Estimated effort** | 5–6 hours |
| **Depends on** | 23, 29, 47, 52, 53 |
| **Blocks** | 56 |
| **Risk** | 🔴 High — you are deliberately breaking production |
| **Blast radius** | Whatever you choose; that is the point of controlling it |
| **Architecture refs** | `ARCHITECTURE.md#x2-failure-domains` (F1–F14), `ULTIMATE-PLAN.md#13-gates` (G10), Law V |

---

## 🎯 MISSION

Stop waiting for failures to teach you. **Cause them deliberately, on your schedule, with the blast radius you choose** — and prove that every fault in the F1–F14 catalog behaves the way the architecture says it should. Then execute a full disaster-recovery exercise and pass **gate G10**.

> 💡 **WHY chaos engineering rather than more testing.** Fifty-three phases have tested components in isolation and verified individual failure modes as they were built. What has never been tested is the *system* under a failure it was not expecting, while real work is running, with real people responding. **Every assumption in this architecture — "Ceph will rebalance," "the job will resume from checkpoint," "the storm guard will escalate" — is a hypothesis until it has been tested in production conditions.** Chaos engineering converts them into facts on a Tuesday morning rather than at 3 a.m. during an unplanned event.

> ⚠️ **This phase can cause a real outage. That is not a bug; it is the risk being managed deliberately.** Every experiment needs a hypothesis, a blast-radius limit, an abort condition, a stop button that works, and a human watching. **Never run a chaos experiment you cannot stop, and never run one during an active incident or an exhausted error budget.**

---

## ✅ PREFLIGHT

```bash
# 🔴 THE GO/NO-GO CHECKS — all must pass
bash tools/backup/verify-restore.sh --all        # Phase 29: restores verified
bash tools/oncall/slo-report.sh --current        # Phase 47: budget available?
kubectl get events -A --field-selector type=Warning | wc -l    # quiet?
bash benchmarks/harness/quiet-check.sh           # no critical work running?

# The remediation controller is ARMED (we are testing it)
kubectl get deploy remediation-controller -n acceleration -o jsonpath='{.spec.template.spec.containers[0].env}'

# The team is available and knows this is happening
```

---

## 📦 DELIVERABLES

```
chaos/
  experiments/                      # 🎯 one per fault, F1–F14
    F01-single-node-loss/
      hypothesis.md
      blast-radius.md               # ⚠️ what CAN and CANNOT be affected
      abort-conditions.md
      procedure.md
      results.md
  game-days/                        # scheduled team exercises
  chaos-mesh-values.yaml            # or a scripted approach
tools/chaos/
  inject.sh                         # ⚠️ guarded fault injection
  abort.sh                          # 🔴 THE STOP BUTTON — must always work
  blast-radius-check.sh             # pre-flight safety validation
docs/operations/
  chaos-engineering.md
  dr-exercise-report.md             # the full DR run
gates/G10-resilience.md
evidence/phase-54/{preflight,acceptance,handoff,deviations,gate-g10}.md
```

---

## 📋 TASKS

### Task 1 — 🎯 The experiment protocol and its guards

Every experiment, without exception:

```
1. HYPOTHESIS      "Killing one GPU node during a 16-GPU training job will:
                    · fail the job within 90 s (NCCL async error handling)
                    · trigger a restart from the last checkpoint within 5 min
                    · quarantine the node within 5 min (Phase 23)
                    · NOT affect any other tenant's jobs
                    · NOT affect inference SLOs"
                   ⚠️ Predict what should NOT happen, not just what should.
2. BLAST RADIUS    Explicitly: which nodes, which tenants, which services.
                   ⚠️ Never include: control plane, storage quorum, inference.
                      (Until you have graduated to those experiments deliberately.)
3. ABORT CONDITIONS  Any SLO burn-rate alert; any tenant outside the radius affected;
                     any unrecovered state after N minutes.
4. STOP BUTTON     `abort.sh` — tested BEFORE the experiment starts. Every time.
5. NOTIFY          Team + status page. ⚠️ Users must know it is an exercise,
                   or someone declares a real incident.
6. INJECT
7. OBSERVE         Record what actually happened, with timestamps.
8. RECOVER         Verify full recovery before ending.
9. COMPARE         Hypothesis vs. reality. ⚠️ Differences are the findings.
10. FIX            Every gap gets an owner and a date.
```

> ⚠️ **Test the abort mechanism before every experiment, not once at the start.** A stop button that has drifted out of working order turns a controlled experiment into an uncontrolled outage. Thirty seconds of verification; no exceptions.

**The graduated blast radius** — earn the right to bigger experiments:

| Level | Scope | Prerequisite |
|---|---|---|
| **L1** | One worker node, one tenant's test workload | — |
| **L2** | Multiple nodes, one leaf domain, real batch workloads | L1 clean 3× |
| **L3** | A full rack; storage nodes; network faults | L2 clean; backups verified |
| **L4** | ⚠️ Control plane; cluster-wide | L3 clean; a maintenance window; leadership sign-off |
| **L5** | 🔴 Full DR exercise (Task 4) | All above; scheduled well ahead |

---

### Task 2 — The fault catalog, tested

Work through `ARCHITECTURE.md#X2`'s F1–F14. Each has a predicted behavior; verify it.

| # | Fault | Predicted behavior | Level |
|---|---|---|---|
| **F1** | Single GPU node loss | Job fails fast, restarts from checkpoint; node quarantined | L1 |
| **F2** | GPU hardware failure (Xid) | Detected < 60 s; node quarantined; job restarts | L1 |
| **F3** | Node NotReady (kubelet) | Standard eviction; workloads reschedule | L1 |
| **F4** | Disk failure (scratch) | Job fails; node continues; scratch reclaimed | L1 |
| **F5** | Disk failure (OSD) | Ceph rebalances; no data loss; client impact bounded | L2 |
| **F6** | NIC failure | Node isolated; quarantined; jobs reschedule | L2 |
| **F7** | RDMA link down | Detected; node quarantined; NCCL jobs fail cleanly | L2 |
| **F8** | Leaf switch failure | ⚠️ Capacity loss, no partition; jobs on that leaf fail | L3 |
| **F9** | Rack power loss (12 nodes) | Storm guard escalates (no mass remediation); Ceph rebalances | L3 |
| **F10** | Storage node loss under load | Client I/O continues; measure the degradation | L3 |
| **F11** | Control-plane node loss | ⚠️ **Running jobs UNAFFECTED**; quorum holds | L4 |
| **F12** | etcd quorum loss (2 of 3) | API read-only/unavailable; **running jobs continue** | L4 |
| **F13** | Registry unavailable | Cached images still start; new pulls fail | L2 |
| **F14** | Observability outage | ⚠️ Alerts still fire (Phase 45's independent Prometheus) | L2 |

> ⚠️ **F11 and F12 test the architecture's single most important invariant:** *"a control-plane outage must never terminate a running training job."* This is stated in `ARCHITECTURE.md`'s Three Planes section and has been assumed by every phase since. **It has probably never been tested.** Test it. If a 16-GPU job dies when etcd loses quorum, that is a foundational finding that changes how the platform must be operated.

> 💡 **F14 is the meta-test.** Phase 45 kept the Phase 11 Prometheus alive specifically so that observability failure is survivable. Kill Mimir and verify that alerts still reach on-call. If they do not, every other experiment's monitoring is unreliable.

---

### Task 3 — Game days: the human half

Technical resilience is half the system. The other half is whether people can respond.

**A game day** — a scheduled 2–3 hour exercise, quarterly:

```
· A scenario is injected by a facilitator; the on-call rotation responds
· ⚠️ The responders do NOT know what was injected
· They use only the real tools: alerts, dashboards, runbooks, the CLI
· The facilitator observes and takes notes; they do not help
· Debrief immediately after
```

**What game days actually find** — and it is rarely what people expect:

| Finding type | Example |
|---|---|
| **Runbook errors** | A command that no longer works; a path that changed |
| **Missing runbooks** | An alert with no procedure |
| **Alert routing gaps** | The page went to someone who left |
| **Tool access problems** | The responder lacks the RBAC to run the fix |
| **Knowledge concentration** | ⚠️ Only one person knows how to do X |
| **Communication gaps** | Nobody told the users |
| **Slow diagnosis** | The signal existed but was not where they looked |

> 💡 **"Only one person knows how to do X" is the most valuable finding a game day produces**, and it never shows up in any technical test. It is a bus-factor risk that becomes an outage the week that person is on holiday. **Rotate the responder role so this surfaces.**

**Scenario library** — write 8–10, drawn from the fault catalog plus realistic compound events:
```
· "Three nodes in rack 2 went down and Ceph is degraded"
· "Inference latency doubled 20 minutes ago; nobody deployed anything"
· "A user says all their jobs are failing; nobody else is complaining"
· "The cluster is at 40 % utilization but the queue is full"     ← Phase 34/31
· "etcd is running out of space"                                 ← Phase 52
· "A certificate expired"                                        ← Phase 53
```

---

### Task 4 — 🔴 The full DR exercise

The one Phase 29 designed for and could only partially test. **Schedule it; do not improvise it.**

**Choose the scenario honestly:**

| Scenario | Realism | Cost | Recommendation |
|---|---|---|---|
| **D2: All control-plane nodes lost, workers intact** | High | 2–4 h downtime | ✅ **Do this one.** Recoverable, bounded, tests the critical path. |
| D4: Mayastor etcd lost | High | 2 h | ✅ Also do this — tests a hazard nobody expects |
| D3: Ceph unrecoverable | Moderate | 1–2 days | Tabletop unless you have a spare cluster |
| **D1: Total loss, rebuild from scratch** | Low frequency, high impact | Days | ⚠️ **Tabletop + partial**: rebuild a 3-node cluster from Git + offline keys |

**The D2 exercise:**
```
1. Snapshot everything; confirm backups (Phase 29)
2. Announce the window; drain nothing — ⚠️ leave batch jobs RUNNING
   (this tests the invariant from F11/F12 at full scale)
3. Destroy all 3 control-plane nodes
4. 🔴 START THE CLOCK
5. Rebuild control-plane nodes via the Phase 08/09 pipeline
6. Restore etcd from snapshot (Phase 12's recovery runbook)
7. Verify: nodes rejoin, workloads reconcile, Argo syncs
8. 🔴 STOP THE CLOCK  →  compare against the 2–4 h RTO
9. ⚠️ Did the running training jobs survive? THE key question.
```

> ⚠️ **The D1 partial rebuild is worth doing even at 3 nodes.** It is the only way to discover that the age key custody procedure has a gap, that the Git remote credentials expired, or that a step in the bootstrap chain (Phase 29's ordered list) is wrong. **Do it on spare hardware, with only the offline materials and the documentation** — no access to the running cluster. Whatever you cannot do is a real, unrecoverable gap.

---

### Task 5 — 🚪 GATE G10 — Resilience

**`gates/G10-resilience.md`**

| # | Check | Evidence | Pass |
|---|---|---|---|
| G10.1 | All 14 faults F1–F14 tested | `chaos/experiments/` | ☐ |
| G10.2 | Each fault's actual behavior recorded vs. predicted | Results | ☐ |
| G10.3 | 🧪 **F11: control-plane node loss does not affect running jobs** | Test | ☐ |
| G10.4 | 🧪 **F12: etcd quorum loss does not terminate running jobs** | Test | ☐ |
| G10.5 | 🧪 F9: rack power loss escalates via the storm guard | Test | ☐ |
| G10.6 | 🧪 F5/F10: storage failures cause no data loss | Test + checksums | ☐ |
| G10.7 | 🧪 F14: alerts fire during an observability outage | Test | ☐ |
| G10.8 | 🧪 F13: cached images still start when the registry is down | Test | ☐ |
| G10.9 | Jobs resume from checkpoint after every node-loss fault | Test | ☐ |
| G10.10 | No fault affected a tenant outside its declared blast radius | Records | ☐ |
| G10.11 | 🔴 **The abort mechanism worked every time it was tested** | Records | ☐ |
| G10.12 | 🧪 **A full D2 DR exercise executed and timed** | `dr-exercise-report.md` | ☐ |
| G10.13 | 📊 Actual RTO recorded against target | Report | ☐ |
| G10.14 | 🧪 D4 (Mayastor etcd) exercise executed | Report | ☐ |
| G10.15 | 🧪 **D1 partial rebuild from offline materials attempted** | Report | ☐ |
| G10.16 | Every gap found has an owner and a date | Tracker | ☐ |
| G10.17 | ≥ 2 game days run with the on-call rotation | Records | ☐ |
| G10.18 | Runbook errors found in game days are fixed | Diff | ☐ |
| G10.19 | Knowledge-concentration risks identified and addressed | Record | ☐ |
| G10.20 | No SLO permanently breached by chaos work | Phase 47 | ☐ |
| G10.21 | Chaos experiments scheduled to recur (not one-off) | Calendar | ☐ |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | Every experiment has a hypothesis with negative predictions | Read | All |
| **A2** | Every experiment declares its blast radius explicitly | Read | All |
| **A3** | 🔴 **`abort.sh` tested immediately before each experiment** | Records | Every time |
| **A4** | 🧪 **The abort actually stops an in-progress experiment** | Test it | Stops |
| **A5** | `blast-radius-check.sh` refuses an experiment touching the control plane at L1–L3 | 🧪 Try | Refused |
| **A6** | Experiments refuse to run during an incident or exhausted budget | 🧪 Try | Refused |
| **A7** | Users notified before each experiment | Records | Notified |
| **A8** | All 14 faults executed | Records | All |
| **A9** | 🧪 **F11/F12: the control-plane-independence invariant HOLDS** | Test | Holds |
| **A10** | Where a prediction was wrong, the gap is recorded and owned | Results | Recorded |
| **A11** | No fault escaped its declared blast radius | Records | None |
| **A12** | Recovery verified before ending each experiment | Records | Verified |
| **A13** | 🧪 **D2 DR exercise executed** | Report | Executed |
| **A14** | 📊 D2 actual RTO measured; compared to the Phase 29 target | Report | Compared |
| **A15** | 🧪 D4 executed | Report | Executed |
| **A16** | 🧪 **D1 partial rebuild attempted with offline materials only** | Report | Attempted |
| **A17** | D1 gaps recorded — anything that could not be rebuilt | Report | Recorded |
| **A18** | ≥ 2 game days run with real responders | Records | Run |
| **A19** | Responders used only real tools; the facilitator did not help | Records | Confirmed |
| **A20** | Every runbook error found is fixed | Diff | Fixed |
| **A21** | Bus-factor risks identified | Record | Identified |
| **A22** | Chaos runs on a recurring schedule | Calendar | Scheduled |
| **A23** | 🚪 **Gate G10 passes** | `gates/G10-resilience.md` | All ☑ |

---

## ↩️ ROLLBACK

```bash
# 🔴 THE STOP BUTTON — must work at all times, from any terminal
bash tools/chaos/abort.sh
#   · Stops all injected faults immediately
#   · Restores network rules, uncordons nodes, restarts killed processes
#   · Posts to the incident channel
#   · ⚠️ Does NOT auto-recover workloads — verify recovery manually

# Halt all chaos activity indefinitely
kubectl delete namespace chaos-mesh
```

> ⚠️ **If `abort.sh` ever fails to work, all chaos activity stops until it is fixed.** No exceptions, no "just this one more experiment." The stop button is the only thing separating a controlled experiment from an outage you caused.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Experiment affected an out-of-scope tenant | Blast radius not enforced, only declared | A5 — make the check mechanical |
| Abort did not fully restore state | Incomplete cleanup logic | Fix before any further experiments |
| A job did not resume from checkpoint | Phase 33's contract not met by that workload | Real finding — the platform or the user's code |
| Node not quarantined after an induced fault | Phase 23 detection gap | Real finding — fix the health signal |
| Alerts did not fire during the fault | Alert gap, or the observability path was affected | F14's purpose |
| Recovery took far longer than the RTO | Real finding | Revise the RTO honestly, or fix the procedure |
| **D2 exercise: running jobs died** | 🔴 **The core invariant does not hold** | Foundational finding — investigate immediately |
| D1 rebuild blocked | Missing offline material or a documentation gap | Exactly what D1 exists to find |
| Team could not diagnose during a game day | Signal exists but is not discoverable | Improve dashboards/runbooks, not the alert |
| Chaos experiments keep getting postponed | Organizational, not technical | Schedule them like maintenance; they degrade without recurrence |

---

## 🚫 DO NOT

- **Do not** run an experiment without testing the abort mechanism first.
- **Do not** exceed the declared blast radius.
- **Do not** run chaos during an active incident or an exhausted error budget.
- **Do not** run L4 experiments without a maintenance window and sign-off.
- **Do not** skip notifying users — a surprise experiment becomes a real incident.
- **Do not** help responders during a game day.
- **Do not** treat a gap found as a failure of the exercise — that is the exercise working.
- **Do not** make chaos a one-off; it must recur or it decays.
- **Do not** pass G10 without the D2 exercise actually executed.

---

## 📤 HANDOFF

`evidence/phase-54/handoff.md` must state:

1. **🚪 The G10 gate result** with every experiment's record.
2. **🧪 The F11/F12 result** — whether the control-plane-independence invariant actually holds. **The most important single finding in this phase.**
3. **📊 The D2 DR exercise report** — actual RTO vs. target, and what went wrong.
4. **🧪 The D1 partial rebuild result** — anything that could not be rebuilt from offline materials is an unresolved existential risk.
5. **Every prediction that was wrong**, and what it revealed.
6. **Game day findings** — runbook errors, alert gaps, and bus-factor risks.
7. **The chaos schedule** going forward.
8. **Gaps still open**, with owners and dates.

---

## ➡️ NEXT

**[PHASE-55 — Security Hardening & Supply Chain (G13)](PHASE-55.md)** — close the security posture that Phase 04 designed and every phase since has partially implemented.
