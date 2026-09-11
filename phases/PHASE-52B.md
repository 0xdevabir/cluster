# PHASE 52B — Campus Scale-Out Validation (G18/G19)

| | |
|---|---|
| **Stage** | C — Campus Harvest Plane |
| **Estimated effort** | 6–8 hours of work spread across ≥ 6 weeks of staged expansion |
| **Depends on** | 52, 33B, 36B |
| **Blocks** | Campus GA |
| **Risk** | 🔴 R-19 — this is where the fabric meets the whole campus, and every promise is tested at once |
| **Blast radius** | Every participating lab, and the project's standing |
| **Architecture refs** | `CAMPUS-FABRIC.md#11-harvest-slos--gates`, `#12-what-this-answers`, `ULTIMATE-PLAN.md#9-scaling-model`, Gates G18/G19 |

---

## 🎯 MISSION

Expand the harvest fabric **lab by lab to 250+ machines**, proving at each step that the fabric holds — work loss stays under 5 %, no teaching time is disrupted, no uplink ceiling is breached — and produce the evidence corpus that makes the campus plane defensible to anyone who asks.

> ⚠️ **Expansion is staged, never bulk.** Adding twelve labs at once means that when something breaks you cannot tell which lab, which switch, or which change caused it — and you have twelve owners to apologize to instead of one. One lab, one week, one report. Slow is fast here.

> 💡 **WHY G18 is a 30-day zero-incident gate rather than a performance number.** Every other gate in this project measures whether the system works. G18 measures whether we kept our word. It is the only gate whose failure condition is another person's bad day, and it is the one that determines whether the fabric exists in a year.

---

## ✅ PREFLIGHT

```bash
# 1. G15 passing — this is a hard block on expansion past 120 nodes
tools/campus/gate-g15.sh --window 7d --min-nodes 100

# 2. G16 and G17 passing from 31B and 25B
grep -i 'G16' evidence/phase-31B/acceptance.md
grep -i 'G17' evidence/phase-25B/acceptance.md

# 3. Templates published and proven under eviction (36B)
ls charts/harvest-templates/

# 4. Real users actually running work — expansion without demand is waste
tools/campus/harvest-report.sh --last-week | grep -i 'idle-unharvested'

# 5. Signed agreements in hand for the labs to be added
tools/campus/check-consent.sh
```

> ⚠️ **If `idle-unharvested` is dominated by "queue empty", stop and do not expand.** More machines will not help. Per 33B's handoff, the binding constraint is demand, and the correct next action is recruiting users (36B's guide, Phase 46's portal), not signing more labs. **Enrolling machines nobody uses converts a good story into wasted electricity and a hollow number.**

---

## 📦 DELIVERABLES

```
docs/campus/
  scale-out-plan.md                 # the staged sequence, with per-stage exit criteria
  scale-out-report.md               # THE deliverable — what happened at each stage
  incident-log.md                   # every teaching-time issue, however small, with resolution
  capacity-model.md                 # measured, not surveyed: what this fabric actually yields
tools/campus/
  enroll-lab.sh                     # the staged enrolment procedure, idempotent
  gate-g18.sh                       # 30-day zero-incident + uplink compliance audit
  gate-g19.sh                       # yield ≥ 60 % of theoretical
evidence/phase-52B/
  preflight.md plan.md acceptance.md handoff.md deviations.md
  stages/                           # one report per enrolment stage
  weekly/                           # the continuous G18/G19 evidence
```

---

## 📋 TASKS

### Task 1 — The staged expansion plan

**One lab per stage. One week per stage. No exceptions for "easy" labs.**

| Stage | Adds | Cumulative | Exit criteria before the next stage |
|---|---|---|---|
| S0 | Baseline (Beachhead labs) | 40–120 | G15/G16/G17 passing; ≥ 2 weeks stable |
| S1 | +1 lab | ~150 | 7 days: W ≤ 5 %, zero incidents, uplink compliant, per-lab report sent to the owner |
| S2 | +1 lab | ~180 | Same |
| S3 | +1 lab (**different building** — new uplink topology) | ~210 | Same, plus uplink verification for the new distribution path |
| S4 | +1 lab (**worst-case room** — marginal ventilation or oversubscribed uplink) | ~240 | Same, plus thermal verification |
| S5 | +1–2 labs | 250+ | **G18 (30 consecutive days, zero incidents) and G19 (yield ≥ 60 %)** |

> 💡 **Deliberately schedule the hardest lab at S4, not last.** The instinct is to save difficult rooms for the end; the correct move is to hit them while you still have attention and slack to fix what they reveal, and while the fabric is small enough to diagnose. A fabric that only works in easy rooms is not a campus fabric.

### Task 2 — The per-stage procedure

**`tools/campus/enroll-lab.sh`** — idempotent, and it refuses to proceed if any precondition fails:

```
1. Verify signed agreement + network approval        (04B, 03B)  → else refuse
2. Verify uplink budget computed for this lab        (03B)       → else refuse
3. Verify ventilation verdict permits the intended work (01B)    → else CPU-only
4. BIOS pass, with the technician, outside class hours (08B)
5. Verify no-trace on a representative machine        (08B)      → else STOP, do not enrol
6. First wake: half the room only. Observe one full window.
7. Second wake: full room. Observe one full window.
8. Warm the cache, measure cold start                 (25B, G17)
9. Run one real user workload end-to-end              (36B)
10. Generate the lab's first weekly report and send it to the owner  (33B)
11. Only then: mark harvest_status: active and proceed to the next stage
```

**Step 6 — half the room first — catches the room-specific failures cheaply**: breaker limits, switch uplink behavior under 30 simultaneous PXE boots, thermal response. Doing it at half scale means the failure is recoverable and invisible.

### Task 3 — G18: the zero-incident audit

**`tools/campus/gate-g18.sh --window 30d`** verifies two things over 30 consecutive days at full scale:

| Criterion | Source | Tolerance |
|---|---|---|
| Teaching-time incidents attributable to NEXUS | `incident-log.md` + owner reports | **Zero** |
| Per-lab uplink share never exceeded its ceiling | Switch counters + `HarvestUplinkCeiling` alerts | Zero breaches > 60 s |
| Machines available at class start | Drain audit per window | 100 % |
| Owner-reported complaints | Direct outreach, not just inbound | Zero unresolved |

**`docs/campus/incident-log.md` records everything, including near-misses and things nobody noticed.** A machine that was still draining at 08:05 with an empty classroom is not an incident, but it is a warning, and a log that only contains disasters teaches nothing.

> ⚠️ **Actively ask, do not wait to be told.** Contact each lab owner at least monthly and ask directly whether anything has been odd. Most people will not report a minor annoyance; they will simply become quietly less willing to renew. The outreach is part of the gate, not a courtesy.

### Task 4 — G19: yield validation

```
yield = useful_node_seconds / theoretical_node_seconds ≥ 60 %

where theoretical = Σ over enrolled machines of (consented window hours)
```

60 % is a deliberately achievable bar, because the remaining 40 % is largely **honest overhead and demand-limited idle**, not failure. Report the decomposition alongside it (33B Task 6) — a yield of 58 % that is 30 points demand-limited is a healthy fabric with a marketing problem, and a yield of 58 % that is 30 points wake-failure is a broken one. **The number alone is not the finding.**

### Task 5 — The measured capacity model

Replace `CAMPUS-FABRIC.md §10`'s illustrative arithmetic with **measured reality** in `docs/campus/capacity-model.md`:

| Quantity | Surveyed estimate (01B) | Measured (52B) | Delta and why |
|---|---|---|---|
| Machines enrolled | | | |
| Effective harvest hours/machine/week | 103 | | wake failures, drain overhead, blackouts |
| GPU-hours/week (useful) | | | |
| CPU-core-hours/week (useful) | | | |
| Cost per useful GPU-hour | $0.07 | | measured electricity |
| Work-loss ratio W | ≤ 5 % target | | |

**Where the measurement contradicts the survey, the measurement wins and the survey's assumption is named.** This table is the one people will quote; it should be the one number in the project that nobody can dispute.

### Task 6 — Close the comparative record

Update `CAMPUS-FABRIC.md §12` with measured outcomes against the 07 Sep 2026 review's findings:

| Their finding | Status at campus GA |
|---|---|
| "Multi-year; 1/56 phases done" | Beachhead delivered in N weeks; measured users, measured GPU-hours |
| "Assumes RDMA NICs / fabric not yet owned" | Plane B built on existing hardware; $0 capex — measured |
| "Needs switches, RDMA NICs, new racks" | Campus capex actual: $X |
| "High operational complexity" | Honest assessment: what actually took operator time over 30 days |
| "No test report/manual yet" | G15–G19 evidence corpus + user guide + runbooks |
| "GeForce EULA at scale" | Determination status |

**Be as honest about what did not work as about what did.** An evidence corpus that reports only successes is worth nothing to the next person who reads it, and a comparative review that was fair to us deserves a response that is fair to it.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Command |
|---|---|---|
| 1 | ≥ 250 machines enrolled across ≥ 8 labs and ≥ 2 buildings, all with signed agreements | `tools/campus/check-consent.sh --report` |
| 2 | Each stage has a committed report with its exit criteria met before the next began | `evidence/phase-52B/stages/` |
| 3 | **G18: zero teaching-time incidents over 30 consecutive days**, with proactive owner outreach documented | `gate-g18.sh --window 30d` |
| 4 | **G18: no lab exceeded its uplink ceiling for > 60 s** in the window | switch counters + alert history |
| 5 | **G19: yield ≥ 60 %**, reported with its full decomposition | `gate-g19.sh --window 7d` |
| 6 | G15 (W ≤ 5 %) still passing at full scale, not just at Beachhead scale | `gate-g15.sh --window 7d --min-nodes 250` |
| 7 | G16 and G17 re-verified at full scale | re-run both harnesses |
| 8 | Machines available at class start, 100 % of observed windows across all labs | drain audit |
| 9 | `capacity-model.md` reports measured figures with surveyed estimates alongside and deltas explained | manual review |
| 10 | The comparative record is updated honestly, including failures | manual review of `CAMPUS-FABRIC.md §12` |

---

## ↩️ ROLLBACK

**Per stage:** the newest lab is withdrawn (04B procedure), and the fabric returns to its last known-good size. Because expansion is staged, rollback is always one lab.
**Fleet-wide:** the harvest controller stops; every machine falls through to Windows on next boot.

> **If G18 fails — if a class is disrupted — stop expanding immediately, regardless of every other metric.** Fix the cause, apologize to the owner in person, and restart the 30-day clock. There is no metric that compensates for a broken promise, and the fabric's continued existence depends far more on this response than on any number in this document.

---

## 🧯 TROUBLESHOOTING

| Symptom | Cause | Action |
|---|---|---|
| W degrades as labs are added | New labs' Oracle models have no history | Expected for ~4 weeks per lab. Use conservative defaults; do not add the next lab until it converges. |
| A new building's uplink behaves unlike the first | Different distribution topology | Why S3 is a different building. Re-measure; never extrapolate uplink behavior across buildings. |
| Yield below 60 %, mostly idle | Demand-limited | Stop enrolling. Recruit users. This is a good problem reported honestly. |
| Yield below 60 %, mostly wake failures | R-26 at scale | BIOS pass quality, or genuinely incapable hardware. Adjust the capacity model downward and say so. |
| An owner reports "the PCs seem slower" | Could be us, could be Windows updates, could be perception | Investigate seriously and report back with data either way. Being the team that investigates carefully is worth more than being right. |
| Scheduler or etcd strain at 250 nodes | Churn-driven, per `ULTIMATE-PLAN.md §9` cliffs | Batched reconciliation, longer node status intervals, separate events etcd. Anticipated — verify the mitigations engaged. |
| A lab withdraws | It happens, and the design permits it | Execute cleanly, thank them, ask why, record it. A clean exit protects every other agreement. |

---

## 🚫 DO NOT

- Do not add more than one lab per stage.
- Do not expand while G15 is failing.
- Do not expand when `idle-unharvested` is demand-dominated.
- Do not skip the half-room first wake.
- Do not report yield without its decomposition.
- Do not omit near-misses from the incident log.
- Do not restart the G18 clock quietly after an incident — record it, and say so in the report.
- Do not present the measured capacity model with the surveyed numbers removed. The delta is the most instructive part.

---

## 🤝 HANDOFF — write `evidence/phase-52B/handoff.md`

Must state:

- Final enrolled scale: machines, labs, buildings, GPU pools.
- The G15/G16/G17/G18/G19 verdicts at full scale, each with its evidence path.
- The measured capacity model, and every place it diverged from the survey.
- Total operator time spent over the 30-day window — this is the honest answer to the "operational complexity" criticism, and it should be a real number.
- Every incident and near-miss, with its resolution.
- The remaining growth headroom: unenrolled labs, and whether demand or supply now binds.
- What you would do differently. The next person to extend this fabric will read this line first.
