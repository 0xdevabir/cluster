# PHASE 56 — Capacity Planning, Documentation & Handover

| | |
|---|---|
| **Stage** | 9 — Operations & Sustainment |
| **Estimated effort** | 5–6 hours |
| **Depends on** | 46, 47, 48, 52, 53, 54, 55 |
| **Blocks** | — (final phase) |
| **Risk** | 🟢 Low — no production changes |
| **Blast radius** | None technically; **high organizationally** — this is what survives you |
| **Architecture refs** | `ULTIMATE-PLAN.md` (all), `ARCHITECTURE.md` (all), every gate G0–G14 |

---

## 🎯 MISSION

Close the project. Produce the **capacity plan** that says what the cluster can do and when it runs out; complete the **documentation set** so someone who was not here can operate it; and run a **real handover** — not a document drop, but a tested transfer of the ability to run this platform.

> 💡 **WHY this phase exists and is not optional.** A cluster that only its builder can operate is a liability with a bus factor of one. Every prior phase produced evidence, runbooks, and tools; none of them produced the *index* that turns 56 phases of artifacts into an operable system. **The test of this phase is not that documents exist — it is that a different person, given only the documents, successfully operates the cluster.** That test is Task 5, and it is the one that matters.

> ⚠️ **Do not write new documentation from scratch here.** Fifty-five phases produced runbooks, evidence, decision records, and gate results. This phase **organizes, indexes, verifies, and fills gaps** in what exists. Rewriting produces a second, divergent set of documents that immediately goes stale.

---

## ✅ PREFLIGHT

```bash
# 📊 All gates passed?
ls gates/ && grep -L "PASS" gates/*.md   # ⚠️ any output here is an unclosed gate

# What documentation exists across all phases
find docs/ evidence/ runbooks/ -name '*.md' | wc -l

# The baseline record (Phase 48) and the current trend (Phase 50)
cat benchmarks/baselines/current.json | jq '.suites | keys'

# Current utilization — the input to every capacity decision
promtool query instant $PROM 'avg_over_time(nexus:gpu_allocated_ratio[30d])'
promtool query instant $PROM 'avg_over_time(nexus:gpu_sm_active_ratio[30d])'
```

---

## 📦 DELIVERABLES

```
capacity/
  capacity-plan.md                  # 🎯 what we have, what we use, when we run out
  growth-model.md                   # demand projection with stated assumptions
  expansion-playbook.md             # how to add nodes / racks / power
  decommission-plan.md              # ⚠️ end of life, data destruction
docs/
  README.md                         # 🎯 THE index — the one entry point
  operations/                       # runbooks (from all phases, indexed)
  architecture/                     # ARCHITECTURE.md + ADRs 001–027
  users/                            # golden paths (Phase 46)
  onboarding/
    operator-day-1.md               # 🎯 first day as an operator
    operator-week-1.md
    known-quirks.md                 # ⚠️ the tribal knowledge, written down
handover/
  handover-checklist.md
  bus-factor.md                     # ⚠️ what only one person knows
  credentials-custody.md            # 🔒 who holds what, physically
  handover-test-results.md          # 🧪 the proof
  open-items.md                     # honest list of what is unfinished
reports/
  final-report.md                   # 🎯 what was built, measured, learned
evidence/phase-56/{preflight,acceptance,handoff,deviations}.md
```

---

## 📋 TASKS

### Task 1 — 🎯 The capacity plan

Three questions, answered with measurement, not estimation.

**1. What do we have?**

| Resource | Raw | Allocatable | Usable after overhead | Source |
|---|---|---|---|---|
| GPUs | N × count | minus platform | minus quarantined (Phase 23) | DRA ResourceSlices |
| GPU-hours/month | | | × availability SLO (Phase 47) | Measured |
| CPU cores | | minus system-reserved | | Phase 20 |
| RAM | | minus reserved | ⚠️ **per node — not a pool** | Phase 20 |
| Storage T1 | Raw NVMe | ÷ replica factor | minus reserve | Phase 26 |
| Storage T2 | Raw HDD/NVMe | ÷ replication | ⚠️ **minus the ~20 % Ceph must keep free** | Phase 27 |
| Network | Port speed × N | ÷ oversubscription | Measured busbw (Phase 22) | B4/B5 |
| **Power** | Circuit capacity | × 0.8 NEC derate | minus non-compute draw | Phase 32 |

> ⚠️ **Report usable capacity, not raw.** "112 GPUs" is a purchase order; "≈ 68 000 schedulable GPU-hours per month at the current availability SLO, after platform reservation and quarantine rate" is a capacity plan. The difference between the two numbers is where every capacity argument goes wrong.

**2. What do we use?** — from 30+ days of the Phase 34 accounting data:
- Allocation ratio and **utilization ratio** (they differ; the gap is the Phase 34 waterfall)
- Peak vs mean, and the **queue-wait distribution** (Phase 47's S4) — the real signal of scarcity
- Per-tenant consumption vs quota; who borrows, who lends
- Growth rate over the observation window

**3. When do we run out?**

| Resource | Current | Growth/month | Headroom | ⚠️ Exhaustion |
|---|---|---|---|---|
| GPU-hours | | | | month |
| T2 storage | | | | month |
| **Power** | | | | month |
| **Rack space** | | | | month |
| etcd/control plane | | | Phase 52's ceiling | |
| Network fabric | | | Oversubscription ratio | |

> 💡 **Power and rack space almost always exhaust before GPU-hours.** Phase 32 measured the real draw; this is where that measurement pays off. A capacity plan that projects only compute demand and discovers the power ceiling three weeks before the purchase arrives has failed at its one job. **State the binding constraint explicitly** — for most builds of this shape it is power, then cooling, then network ports, then compute.

> 📊 **The forecast must state its assumptions and its confidence.** "At the observed 6 %/month growth, GPU-hours exhaust in month 11 — but that growth is driven by three teams, and one new team of the same size moves it to month 7." A single number with no assumptions is not a plan.

---

### Task 2 — Expansion and decommission playbooks

**`capacity/expansion-playbook.md`** — adding capacity, by increment:

| Increment | What changes | Phases to re-run | Watch out |
|---|---|---|---|
| **+1 node, existing rack** | Provisioning only | 05, 06, part of 20 | Trivial — this must be routine |
| **+8 nodes, new rack** | Rack, switch uplink, PDU, **CRUSH topology** | 03, 05–07, 27, 31 | ⚠️ CRUSH map + topology labels + power budget |
| **+ storage only** | OSDs or DiskPools | 26 or 27 | Rebalance impact (Phase 27's C10) |
| **+ new GPU model** | New ResourceFlavor + pool | 19, 21, 30 | ⚠️ **Heterogeneity — do not mix models in one job** |
| **Crossing 100 nodes** | Control plane | 52 | Re-run G11 |

> ⚠️ **Adding a node must be a documented routine, not a project.** If adding one node takes a day of expert work, the platform has failed Phase 46's automation goal. Time it: the target is **a new node joins and takes work in under 60 minutes, mostly unattended.**

**`capacity/decommission-plan.md`** — the phase everyone skips:
- Draining a node without losing storage replicas (⚠️ **Ceph rebalance before removal, not after**)
- 🔒 **Data destruction**: NVMe secure erase / crypto-erase, verified — and recorded
- Returning the DRA/ResourceSlice, CRUSH, DNS, DHCP, and inventory entries
- Warranty, asset tracking, and the **failed-hardware pile** that accumulates in every cluster
- End-of-life for the whole cluster: what gets archived, what gets destroyed, who signs off

---

### Task 3 — 🎯 The documentation index

**`docs/README.md`** — one entry point, organized by *what the reader is trying to do*, not by system:

```
I want to...
  RUN A JOB                    → users/golden-paths/    (Phase 46)
  UNDERSTAND THE ARCHITECTURE  → architecture/          (ARCHITECTURE.md, ADRs)
  FIX SOMETHING BROKEN         → operations/runbooks/   (indexed by symptom)
  RESPOND TO A PAGE            → operations/on-call.md  (Phase 47)
  ADD OR REMOVE HARDWARE       → capacity/              (Phase 56)
  UPGRADE SOMETHING            → operations/upgrades.md (Phase 53)
  RECOVER FROM A DISASTER      → operations/dr/         (Phases 29, 54)
  UNDERSTAND A PERF NUMBER     → benchmarks/            (Phases 48–51)
  ONBOARD AS AN OPERATOR       → onboarding/            (this phase)
  KNOW WHY A DECISION WAS MADE → architecture/adr/      (ADRs 001–027)
```

**Documentation verification** — every runbook must be checked, not assumed:

| Check | Method |
|---|---|
| Does the command still work? | 🧪 **Run it** |
| Do referenced files exist? | Link check in CI |
| Is the version current? | Compare to Phase 53's version matrix |
| Does it match reality? | Spot-check against the live cluster |
| Did the last incident change it? | Cross-check postmortems (Phase 47) |

> 🚫 **Documentation not verified is documentation not trusted.** A runbook whose first command fails teaches the on-call to ignore runbooks, and from then on every runbook is worthless regardless of quality. **Put the link check and a sample of command checks in CI** so rot is caught by the pipeline, not by someone at 3 a.m.

**`docs/onboarding/known-quirks.md`** — ⚠️ the highest-value document in the set. The things that are not in any upstream documentation because they are specific to *this* build:
- "Node X's NIC needs a cold boot, not a warm reboot, after a firmware update"
- "Pool B GPUs throttle at 71 °C, not 83 °C, because of the case airflow"
- "The registry pre-warm must finish before the 08:00 batch or the thundering herd (Phase 42) delays everything"
- "Job type Y always looks stuck for 90 s at start — it is the dataset cache, not a hang"

This is the tribal knowledge. **Collect it by asking everyone who has operated the cluster: "what do you know that isn't written down?"**

---

### Task 4 — The final report

**`reports/final-report.md`** — the honest account, for whoever funds and inherits this.

| Section | Content |
|---|---|
| **What was built** | Architecture summary, node counts, capabilities |
| **📊 What it achieves** | B1–B12 results vs targets — **measured, with conditions** |
| **What it cost** | Hardware, power, and staff time (Phase 34's model) |
| **⚠️ What it does not do** | The memory boundary, no NVLink/GDR, FP64, RoCE vs IB, no BMC |
| **What was learned** | The surprises — where predictions were wrong |
| **Performance vs the §8.1 budget** | Phase 49's closure: where the loss is, and why |
| **Risks** | R-01–R-18 final status |
| **⚠️ Open items** | Honest, with owners |
| **Recommendations** | Next investments, ranked by measured bottleneck |

> 💡 **The "what it does not do" section is the most valuable one in the report**, and the one under the most pressure to soften. A user who expects 100 nodes to behave like one machine with pooled VRAM will design a workload that cannot run here, waste weeks, and conclude the platform is broken. **State the boundary in the report exactly as the architecture states it:** RAM and VRAM stay attached to their node; workloads that need distributed memory must be written for it; bandwidth and latency are first-class design constraints.

> 📊 **Every performance claim carries its conditions.** Phase 48's schema exists for this: node count, leaf domain, power cap, thermal state, cluster quiet or loaded. A number without conditions is a marketing figure and will be quoted back at you in a capacity meeting.

---

### Task 5 — 🧪 The handover test (this is the phase's real deliverable)

**`handover/handover-test-results.md`**

Documentation is not verified by review. It is verified by **someone who did not build the cluster successfully operating it, using only the documents.**

| # | Test | Given only the docs, they must… | Pass |
|---|---|---|---|
| **H1** | Run a job | Submit a training job and get results | ☐ |
| **H2** | Respond to a page | Take a real (injected) alert to resolution | ☐ |
| **H3** | Add a node | Provision and join a node | ☐ |
| **H4** | Diagnose a slow job | Use Phase 51's tree to find the bottleneck | ☐ |
| **H5** | Perform an upgrade | Upgrade one component per Phase 53 | ☐ |
| **H6** | 🔒 Recover a deleted PVC | Restore from backup (Phase 29) | ☐ |
| **H7** | Onboard a tenant | End to end (Phase 46) | ☐ |
| **H8** | Explain a bill | Reconstruct a month's charges (Phase 34) | ☐ |
| **H9** | Find why a decision was made | Locate the relevant ADR | ☐ |
| **H10** | ⚠️ **Recover from a DR scenario** | Execute Phase 54's D2 | ☐ |

**The rules of the test:**
```
· The tester has NOT built this cluster
· The builder may NOT help — not a hint, not "check the other file"
· The builder observes and takes notes silently
· EVERY question the tester asks aloud = a documentation gap → fix it
· Every place they get stuck = a gap → fix it
· Re-test the fixed sections with a DIFFERENT person
```

> ⚠️ **The urge to help is overwhelming and must be resisted.** The moment the builder says "oh, it's in the other file," the test stops measuring the documentation and starts measuring the builder's availability — which is exactly the thing being eliminated. Sit on your hands and write down the question.

> 💡 **Expect H10 and H3 to fail first.** DR runbooks are written once and rarely executed; node addition accumulates undocumented manual steps. These are the two that most often reveal that the platform is operable only by its author.

**`handover/bus-factor.md`** — ⚠️ what only one person knows:

| Knowledge | Who | Documented? | Mitigation |
|---|---|---|---|
| 🔒 Physical key/safe custody | | | ⚠️ **Must be ≥ 2 people** |
| 🔒 OpenBao unseal shares | | | Split custody; test recovery |
| 🔒 SOPS/age private key | | | Offline copies, 2 locations |
| Vendor/support contacts | | | Documented |
| The wiring and rack layout | | | Photos + diagrams |
| "Why we did X" | | | ADR or it is lost |

> 🔒 **A single-person custody of any credential is an outage waiting for a vacation.** Phase 29's state inventory (S2, S3, S4, S17) listed what must be recoverable; this table names the humans. Any row with one name and no mitigation is an open item, and it goes in the final report.

---

### Task 6 — The operating rhythm

The platform is built. Now it must be *run*. Define the recurring calendar so the disciplines from prior phases do not decay:

| Cadence | Activity | From |
|---|---|---|
| **Daily** | Alert review; queue-wait check; failed-job triage | 47 |
| **Weekly** | Utilization review; top wasted-GPU-hours outreach; capacity trend | 34, 51 |
| **Monthly** | Patching; benchmark `--standard`; exception-register review; postmortem review | 50, 53, 55 |
| **Quarterly** | 🧪 **Game day / chaos experiment**; DR drill; CVE exception review; capacity plan refresh | 54, 55, 56 |
| **Annually** | 🧪 **Full DR from offline materials**; architecture review; hardware refresh planning | 29, 54 |

> ⚠️ **The quarterly DR drill is the one that gets skipped**, because nothing is visibly broken and everyone is busy. Put it on a calendar with a named owner. Phase 54 proved the procedure works *once*; only the recurring drill proves it still works after a year of drift.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | 🚪 **All gates G0–G14 passed and recorded** | `gates/` | All |
| **A2** | Capacity plan states **usable**, not raw, capacity | Read | Usable |
| **A3** | 📊 Utilization based on ≥ 30 days of real data | Data | ≥ 30 d |
| **A4** | Exhaustion projected for every resource incl. **power and rack space** | Table | Complete |
| **A5** | ⚠️ **The binding constraint is named explicitly** | Read | Named |
| **A6** | Forecast states its assumptions and confidence | Read | Stated |
| **A7** | 🧪 Adding a node timed; under 60 min, mostly unattended | Timed | < 60 min |
| **A8** | Decommission plan includes **verified data destruction** | Read | Included |
| **A9** | `docs/README.md` organized by task, not by system | Read | Task-based |
| **A10** | 🧪 **Runbook commands sampled and actually run** | Test | Work |
| **A11** | Link check in CI | CI | Passing |
| **A12** | `known-quirks.md` populated from every operator | Read | Populated |
| **A13** | Final report includes the honest **"what it does not do"** | Read | Included |
| **A14** | Every performance claim carries its conditions | Read | All |
| **A15** | Performance vs §8.1 budget closed with structural causes | Read | Closed |
| **A16** | R-01–R-18 final status recorded | Register | Complete |
| **A17** | 🧪 **All 10 handover tests attempted by a non-builder** | Results | All 10 |
| **A18** | ⚠️ **The builder did not help during the test** | Observed | Confirmed |
| **A19** | Every question asked aloud recorded as a doc gap and fixed | Log | Fixed |
| **A20** | Fixed sections re-tested with a different person | Results | Re-tested |
| **A21** | 🔒 No credential has single-person custody without a mitigation | `bus-factor.md` | None |
| **A22** | 🔒 Offline key custody physically verified by two people | Check | Verified |
| **A23** | Operating rhythm on a calendar with named owners | Calendar | Scheduled |
| **A24** | ⚠️ **`open-items.md` is honest** — nothing quietly dropped | Read | Honest |
| **A25** | Handover accepted in writing by the receiving operator(s) | Signed | Accepted |

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Handover tester stuck constantly | Documentation assumes builder knowledge | That is the finding — fix and re-test |
| Builder keeps helping | Human nature | Leave the room; observe by recording |
| Capacity numbers disputed | Raw vs usable confusion | Show the derivation table |
| Growth projection wildly off | Too short an observation window | State the confidence; re-forecast quarterly |
| Runbooks fail when run | Drift since writing | Fix; add to CI |
| Nobody wants to own the rhythm | No named owner | Assign or record as a bus-factor risk |
| Open items list is empty | ⚠️ It is not honest | Every project has open items |
| Gate not passed but project "done" | Scope pressure | Record as an open item with an owner — do not mark the gate passed |

---

## 🚫 DO NOT

- **Do not** mark a gate passed that did not pass. Record it as an open item.
- **Do not** report raw capacity as usable capacity.
- **Do not** project compute demand without projecting power and space.
- **Do not** help during the handover test.
- **Do not** write new documentation that duplicates existing phase artifacts.
- **Do not** ship runbooks whose commands were never run.
- **Do not** soften the "what it does not do" section.
- **Do not** leave any credential in single-person custody.
- **Do not** publish an empty open-items list.

---

## 📤 HANDOFF — PROJECT CLOSE

`evidence/phase-56/handoff.md` — the final artifact of the project:

1. **🚪 Gate summary G0–G14** — every gate, its status, and any that did not pass.
2. **📊 The capacity plan** — usable capacity, current use, exhaustion dates, and **the binding constraint**.
3. **🧪 The handover test results** — all 10 tests, what failed, what was fixed, who re-tested.
4. **🔒 The bus-factor table** — every credential and every piece of unique knowledge, with custody.
5. **The final report** — built, measured, cost, limits, learned, recommended.
6. **⚠️ `open-items.md`** — the honest, complete list with owners and dates.
7. **The operating rhythm** — on a calendar, with names.
8. **Written acceptance** from the receiving operators.

---

## 🏁 PROJECT COMPLETE

The platform is built, measured, hardened, documented, and handed over.

What exists now: ordinary GPU PCs, unchanged in their physics, operating as one scheduled, observable, fault-tolerant pool of compute, storage, and network — with the boundaries of that pooling stated honestly, measured explicitly, and designed around rather than hidden.

> **Law X — Nothing Is True Until It Is Measured.** Every claim this platform makes about itself is backed by a benchmark with recorded conditions, a gate with recorded evidence, or a failure test that was actually run. That is the difference between a cluster and a collection of computers.

**⬅️ [Back to the phase index](README.md)** · **[ULTIMATE-PLAN.md](../ULTIMATE-PLAN.md)** · **[ARCHITECTURE.md](../ARCHITECTURE.md)**
