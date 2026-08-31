# PHASE 47 — SLOs, Alerting & On-Call (G12/G14)

| | |
|---|---|
| **Stage** | 7 — Platform Experience |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 34, 40, 43, 45, 46 |
| **Blocks** | 48 (Stage 8 entry), 53, 54 |
| **Risk** | 🟡 Medium — the risk is committing to SLOs you cannot meet |
| **Blast radius** | Operational expectations and human sustainability |
| **Architecture refs** | `ULTIMATE-PLAN.md#5-target-capability-model`, `#13-gates` (G12, G14), `ARCHITECTURE.md#l10` |

---

## 🎯 MISSION

Define what **"working" means** as measurable, published commitments; instrument them; build the alerting and on-call process that sustains them without burning out the people who run the platform. Then pass **gates G12 (operational readiness) and G14 (user experience)** and close Stage 7.

> 💡 **WHY SLOs matter more in a private cluster than a commercial one.** There is no contract, no credit, no customer to lose — which is exactly why expectations drift. Without published SLOs, "the cluster is slow" is an opinion, capacity requests are arguments, and the operations team is judged against an imaginary standard that rises every time something goes wrong. **An SLO converts a feeling into a number that both sides agreed to in advance.**

> ⚠️ **The trap: committing to SLOs you cannot meet.** This platform runs on consumer hardware with no BMC, no redundant power per node, and a single fabric. Promising 99.9 % availability for batch training is dishonest. **Set SLOs from the measured behavior of Phases 22–45, not from aspiration**, and state the reasoning. An SLO you miss every month is worse than a lower one you hold.

---

## ✅ PREFLIGHT

```bash
# 📊 The measured reality that SLOs must be derived from
cat benchmarks/baselines/b*.json | jq -s 'length'
bash tools/accounting/waterfall.sh --last-quarter
grep -A10 "SLO" evidence/phase-40/handoff.md      # inference numbers

# Observability able to compute SLIs over 13 months (Phase 45)
promtool query instant 'count(up)'

# Alert inventory across all phases
kubectl get prometheusrules -A -o json | jq '[.items[].spec.groups[].rules[]] | length'
```

---

## 📦 DELIVERABLES

```
observability/slo/
  slo-definitions.yaml              # 🎯 the commitments, as code
  sli-recording-rules.yaml
  error-budget-rules.yaml
  burn-rate-alerts.yaml             # ⚠️ multi-window, multi-burn-rate
dashboards/
  slo-overview.json
  error-budget.json
docs/operations/
  slo-document.md                   # 🎯 the published commitment
  oncall-handbook.md                # 🎯 the human process
  incident-response.md
  postmortem-template.md
  escalation-policy.md
tools/oncall/
  incident.sh                       # declare, track, resolve
  alert-audit.sh                    # which alerts fire, which get acted on
  slo-report.sh                     # monthly
gates/G12-operational.md
gates/G14-user-experience.md
evidence/phase-47/{preflight,acceptance,handoff,deviations,gate-g12,gate-g14}.md
```

---

## 📋 TASKS

### Task 1 — 🎯 Define the SLOs (from measurement, not aspiration)

**The service classes, because one SLO cannot cover a batch cluster and a serving platform:**

| Class | What | SLO philosophy |
|---|---|---|
| **Control plane** | API server, scheduler, etcd | High — everything depends on it |
| **Production inference** | Phase 40 services | High — user-facing |
| **Batch compute** | Training, HPC, data | ⚠️ Moderate — interruption is expected and cheap (Phase 33) |
| **Interactive** | Notebooks | Moderate — annoying, not damaging |
| **Storage** | T1/T2/T3 durability + availability | High for durability, moderate for latency |
| **Platform services** | Registry, tracking, portal | Moderate |

**`slo-definitions.yaml` — the actual commitments:**

| # | SLI | SLO | Window | Derived from |
|---|---|---|---|---|
| **S1** | Kubernetes API availability | 99.9 % | 30 d | Phase 12 HA |
| **S2** | API p99 latency | < 1 s | 30 d | Phase 12 measurement |
| **S3** | **Inference availability** (per service) | **99.5 %** | 30 d | Phase 40 |
| **S4** | **Inference TTFT p95** | **< 500 ms** | 30 d | Phase 40's B10 |
| **S5** | Inference error rate | < 0.5 % | 30 d | Phase 40 |
| **S6** | **Job admission latency p95** (uncontended) | **< 60 s** | 30 d | Phase 30's B11 |
| **S7** | **Batch job completion rate** (excluding user error) | **≥ 98 %** | 30 d | Phase 41's retry classification |
| **S8** | Preemption recovery time p95 | < 10 min | 30 d | Phase 33's drill |
| **S9** | Notebook startup p95 | < 60 s | 30 d | Phase 43 |
| **S10** | **Storage durability (T1/T2)** | **no data loss** | ∞ | Phase 26/27 replication |
| **S11** | Storage availability (T2) | 99.9 % | 30 d | Phase 27 |
| **S12** | **Cluster GPU utilization** | **≥ 65 %** | 30 d | Phase 34's waterfall (G8.22 baseline) |
| **S13** | Registry availability | 99.5 % | 30 d | Phase 42 |
| **S14** | Time to detect a failed node | < 5 min | 30 d | Phase 23 |
| **S15** | Backup restore verification | 100 % pass | weekly | Phase 29 |

> ⚠️ **S7 and S12 are the two that will be argued about.** S7 must exclude user error (a Python bug is not a platform failure) — define the exclusion precisely and publish it, or the number becomes meaningless. S12 is an *aspiration expressed as an SLO*, which is unusual and deliberate: it makes the platform team accountable for the waterfall, not just for uptime.

> 💡 **Note what is deliberately absent: a batch-job availability SLO.** Batch compute is preemptible by design. Committing to "your job will not be interrupted" would undo Phases 30–33. Instead, S8 commits to *recovery* being fast — which is the honest promise this architecture can keep.

---

### Task 2 — Error budgets and burn-rate alerting

An SLO without an error budget is a wish.

```
99.5 % over 30 days  →  error budget = 0.5 % = 3 h 39 m of downtime
Burning 50 % of the budget in the first week is a signal, not an emergency.
Burning 100 % means: stop shipping changes, fix reliability.
```

**Multi-window, multi-burn-rate alerting** — the technique that gives both fast detection and low noise:

| Burn rate | Long window | Short window | Budget consumed | Action |
|---|---|---|---|---|
| **14.4×** | 1 h | 5 m | 2 % in 1 h | 🔴 **Page** — catastrophic |
| **6×** | 6 h | 30 m | 5 % in 6 h | 🔴 Page |
| **3×** | 1 d | 2 h | 10 % in 1 d | 🟠 Ticket |
| **1×** | 3 d | 6 h | 10 % in 3 d | 🟡 Ticket |

> 💡 **This replaces threshold alerting for anything with an SLO.** "p99 latency > 500 ms for 5 minutes" pages on every transient blip; "we are burning error budget 14× faster than sustainable" pages only when the SLO is genuinely at risk. The short window prevents alerting on an already-recovered incident.

**The error-budget policy — write it down before you need it:**
```
Budget remaining > 50 %:   Ship freely.
Budget 10–50 %:            Ship, but prioritize reliability work.
Budget < 10 %:             ⚠️ Feature freeze for that service. Reliability only.
Budget exhausted:          Freeze + a written plan before resuming changes.
```

> ⚠️ **The policy is only real if it is honored when inconvenient.** The first time a budget is exhausted during an important deadline is when the policy is tested. Agreeing to it in advance, in writing, with the people who will want to override it, is the entire point.

---

### Task 3 — Alert hygiene (the sustainability problem)

Forty-seven phases have each added alerts. Left unmanaged, that is an unsustainable page load.

**`tools/oncall/alert-audit.sh`** — run it, then act on it:

```
ALERT AUDIT — last 90 days
  Total alert rules:              214
  Alerts that fired:               47
  ⚠️ Alerts that NEVER fired:     167   ← untested. Do they work?
  Alerts fired > 20 times:          6   ← noise candidates
  Alerts with no runbook:           0   ✅ (Phase 45 A17)
  🔴 Alerts fired but never acted on: 11 ← DELETE OR FIX THESE
  Pages outside business hours:    14
  Pages that were false positives:  3   (21 %) ⚠️
```

**The three actions this produces:**

| Finding | Action |
|---|---|
| Fired but never acted on | **Delete it, or make it actionable.** An alert nobody acts on trains people to ignore alerts. |
| Never fired | **Test it.** An untested alert is an assumption. |
| High false-positive rate | Fix the threshold or the condition — 21 % is high enough to erode trust |

> 🚫 **The single most damaging thing in operations is an alert people have learned to ignore**, because they cannot tell it apart from the one that matters. Ruthless deletion is a reliability practice, not a shortcut.

**The alert tiering, applied consistently across all phases' alerts:**

| Tier | Criteria | Destination | Response |
|---|---|---|---|
| **🔴 Page** | User-visible impact now, or imminent data loss | On-call, 24/7 | < 15 min |
| **🟠 Urgent ticket** | Will become user-visible within hours | Team queue, business hours | < 4 h |
| **🟡 Ticket** | Degradation or a capacity signal | Backlog | < 1 week |
| **ℹ️ Dashboard** | Informational | No notification | — |

---

### Task 4 — 🎯 The on-call handbook

**`docs/operations/oncall-handbook.md`** — the human process, written for a small team.

```markdown
# On-call

## Rotation
· Weekly, handover Monday 10:00
· ⚠️ Minimum 4 people in the rotation. With 3 or fewer, on-call is a
  retention problem — say so to management rather than absorbing it.
· Secondary escalates after 15 min of no primary ack
· Compensation/time-off-in-lieu policy: [state it explicitly]

## What being on-call means here
· You respond to PAGES within 15 minutes. That's it.
· 🟠 and 🟡 alerts are NOT your problem out of hours.
· You are not expected to fix everything — you are expected to
  stabilize, communicate, and escalate.
· ⚠️ If you are paged more than 2× in a night, that is an incident
  about the alerting, and it gets a postmortem.

## The first five minutes of any page
1. Acknowledge it.
2. Open the runbook linked in the alert.
3. Check the cluster overview dashboard: is this isolated or systemic?
4. `nexus correlate --time <now>` (Phase 45) — what else happened?
5. If user-visible: declare an incident (`incident.sh declare`) and post status.

## Declaring an incident
    nexus incident declare --severity 2 --summary "Inference latency degraded"
Creates: a channel, a timeline doc, a status page entry, and starts the clock.
⚠️ Declare EARLY and downgrade. Under-declaring costs more than over-declaring.

## Severity
| SEV1 | Data loss, or the whole cluster down          | Page everyone |
| SEV2 | A production service down or badly degraded  | Page on-call + service owner |
| SEV3 | Degraded, workaround exists                  | On-call handles |
| SEV4 | Minor, no user impact                        | Ticket |

## Handover checklist
· Open incidents and their state
· Nodes quarantined and why (Phase 23)
· Error budget status per service
· Anything deliberately silenced, and when the silence expires
· Planned maintenance in the coming week
```

> ⚠️ **The 4-person minimum is not a nicety.** A 2-person rotation means each person is on-call half the time, permanently. That is how platform teams lose people, and losing the person who understands Phases 20–23 costs more than any outage. **If the rotation is too small, that belongs in the handoff as a risk with an owner** — it is a real operational finding, not an HR aside.

---

### Task 5 — Incident response and postmortems

**`postmortem-template.md`** — blameless, and focused on the system.

```markdown
# Incident YYYY-MM-DD: <title>
Severity: · Duration: · User impact: · Error budget consumed:

## Timeline        (from `nexus correlate` — actual timestamps, not recollection)
## What happened   (mechanism, not blame)
## Why it wasn't caught earlier    ← ⚠️ the most valuable section
## Why it took N minutes to resolve
## Action items    (each with an OWNER and a DATE; tracked to completion)
## What went well
```

**The rules:**

| Rule | Why |
|---|---|
| **Blameless** | People give accurate accounts only when it is safe to |
| **SEV1/SEV2 always get a postmortem** | Within 5 working days |
| **Action items have owners and dates** | An action item without an owner is a wish |
| **Track action items to completion** | ⚠️ Unclosed action items are how the same incident recurs |
| Publish internally | The learning is the product |
| **Repeat incidents get a deeper look** | The second occurrence means the first postmortem missed something |

> 💡 **"Why wasn't this caught earlier?" is the section that improves the platform.** It converts every incident into either a new alert, a new test, or a documented gap — which is how the alert set becomes *earned* rather than guessed.

---

### Task 6 — 🚪 GATE G12 — Operational Readiness

**`gates/G12-operational.md`**

| # | Check | Evidence | Pass |
|---|---|---|---|
| G12.1 | SLOs defined, derived from measurement, and published | `slo-document.md` | ☐ |
| G12.2 | SLIs instrumented and computing correctly | Dashboard | ☐ |
| G12.3 | Error budgets calculated per service | Dashboard | ☐ |
| G12.4 | Multi-burn-rate alerting configured for every SLO | Rules | ☐ |
| G12.5 | 🧪 Burn-rate alerts fire correctly on a simulated breach | Test | ☐ |
| G12.6 | Error-budget policy written and agreed | Doc + sign-off | ☐ |
| G12.7 | **Alert audit complete; non-actionable alerts deleted** | `alert-audit.sh` | ☐ |
| G12.8 | Every alert tiered; page-tier is user-visible impact only | Audit | ☐ |
| G12.9 | Every alert has a tested runbook | Link check + spot test | ☐ |
| G12.10 | On-call rotation staffed with ≥ 4 people (or the risk is recorded) | Rota | ☐ |
| G12.11 | Escalation policy configured and tested | 🧪 Test page | ☐ |
| G12.12 | Incident tooling works end to end | 🧪 Declare a test incident | ☐ |
| G12.13 | Postmortem template and process documented | Doc | ☐ |
| G12.14 | 🧪 **A real or simulated incident has been run end to end** | Record | ☐ |
| G12.15 | Handover checklist in use | Observe a handover | ☐ |
| G12.16 | Status page live and accurate | Browser | ☐ |
| G12.17 | 📊 30 days of SLI data collected; SLOs currently met | `slo-report.sh` | ☐ |

---

### Task 7 — 🚪 GATE G14 — User Experience

**`gates/G14-user-experience.md`** — the gate that asks whether the platform is *usable*, not merely working.

| # | Check | Evidence | Pass |
|---|---|---|---|
| G14.1 | 🧪 **A new user reaches a running GPU job in < 10 min, docs only** | Phase 46 A21 | ☐ |
| G14.2 | All eight golden paths execute as written, verified in CI | Phase 46 A14/A15 | ☐ |
| G14.3 | `nexus` CLI covers the common cases with zero flags | Phase 46 A3 | ☐ |
| G14.4 | 🎯 **Pending jobs explain WHY, with an estimate** | Phase 46 A7 | ☐ |
| G14.5 | Notebook startup within SLO | Phase 43 | ☐ |
| G14.6 | Users can see their own usage and cost | Phase 34 | ☐ |
| G14.7 | Model lineage answerable in one query | Phase 44 | ☐ |
| G14.8 | Tenant onboarding is one command | Phase 46 A20 | ☐ |
| G14.9 | Reference documentation is generated from the live cluster | Phase 46 A16 | ☐ |
| G14.10 | Status page shows live capacity and wait estimates | Phase 46 A17 | ☐ |
| G14.11 | 📊 **A user satisfaction signal is collected** (even a 3-question survey) | Results | ☐ |
| G14.12 | Support requests are categorized; the top 3 causes have owners | Ticket analysis | ☐ |
| G14.13 | Users are not writing raw YAML for common tasks | Ask them | ☐ |

> 📊 **G14.11 does not need to be sophisticated.** Three questions — "Can you get your work done? What is the most frustrating thing? What would you fix first?" — asked of ten users produces more actionable signal than any dashboard. Run it, publish the results, and act on the top item.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | All 15 SLOs defined with their measurement basis stated | `slo-definitions.yaml` | Complete |
| **A2** | **Every SLO traceable to a measured baseline, not aspiration** | Cross-reference benchmarks | Traceable |
| **A3** | S7's user-error exclusion is precisely defined | Read | Precise |
| **A4** | SLIs compute correctly over 30-day windows | Query | Correct |
| **A5** | Error budgets visible per service | Dashboard | Visible |
| **A6** | Multi-burn-rate alerts configured (4 rates per SLO) | Rules | Configured |
| **A7** | 🧪 A simulated breach fires the correct burn-rate alert | Inject | Fires |
| **A8** | 🧪 A brief blip does NOT page (short-window suppression works) | Inject | No page |
| **A9** | Error-budget policy documented and agreed by stakeholders | Sign-off | Agreed |
| **A10** | 📊 **Alert audit run; results actioned** | `alert-audit.sh` + changes | Actioned |
| **A11** | Non-actionable alerts deleted | Diff | Deleted |
| **A12** | Untested alerts identified and tested | Record | Tested |
| **A13** | All alerts tiered consistently | Audit | Consistent |
| **A14** | Escalation works: unacked page escalates in 15 min | 🧪 Test | Escalates |
| **A15** | `incident.sh` creates channel, timeline, and status entry | 🧪 Test | Works |
| **A16** | 🧪 **A full incident simulation run with the team** | Record | Run |
| **A17** | Postmortem produced for the simulation | Doc | Produced |
| **A18** | Action items tracked to completion | Tracker | Tracked |
| **A19** | On-call handbook reviewed by everyone in the rotation | Sign-off | Reviewed |
| **A20** | Rotation ≥ 4 people, or the risk is recorded with an owner | Rota | Either |
| **A21** | 📊 Monthly SLO report generates | `slo-report.sh` | Generates |
| **A22** | 📊 User satisfaction signal collected from ≥ 10 users | Survey | Collected |
| **A23** | 🚪 **Gate G12 passes** | `gates/G12-operational.md` | All ☑ |
| **A24** | 🚪 **Gate G14 passes** | `gates/G14-user-experience.md` | All ☑ |

---

## ↩️ ROLLBACK

```bash
# SLOs are commitments, not code — "rolling back" means renegotiating publicly
# If an SLO proves unachievable:
#   1. Do NOT quietly lower it. Publish the change and the reason.
#   2. Record the measured basis for the new number.
#   3. Note what would be required to meet the original.

# Alerting rollback: revert to threshold alerts if burn-rate alerting misfires
kubectl apply -f observability/rules/threshold-alerts-legacy.yaml
```

> ⚠️ **Quietly lowering an SLO destroys the mechanism.** The value of an SLO is that both sides agreed in advance; changing it unilaterally after a bad month makes it a post-hoc justification instead of a commitment. Renegotiate openly or not at all.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| SLO consistently missed | Set from aspiration, not measurement | A2 — renegotiate openly with data |
| Burn-rate alerts too noisy | Windows too short; or the SLI includes non-platform failures | Tune windows; check the SLI's exclusions |
| Burn-rate alerts never fire during real incidents | SLI does not capture the user-visible symptom | The SLI is measuring the wrong thing — redefine it |
| On-call burnout | Too few people, or too many pages | A20 + A10; both are the platform team's problem to escalate |
| Same incident recurring | Action items not completed | A18 |
| Postmortems become blame sessions | Culture, or leadership presence in the room | Reinforce blamelessness explicitly; facilitate |
| Users unaware of SLOs | Published but not surfaced | Put them on the status page (Phase 46) |
| Error-budget policy ignored under deadline pressure | It was never really agreed | A9 — get real agreement, or drop the pretence |
| Alert audit shows most alerts never fired | Normal for rare conditions — but test them | A12 |

---

## 🚫 DO NOT

- **Do not** set an SLO you have not measured.
- **Do not** commit to batch job non-interruption — it contradicts the architecture.
- **Do not** page for anything that is not user-visible or imminent data loss.
- **Do not** keep an alert that fires and is never acted on.
- **Do not** run a rotation smaller than 4 without recording it as a risk.
- **Do not** hold a postmortem that assigns blame to a person.
- **Do not** let action items go untracked.
- **Do not** quietly lower an SLO.
- **Do not** pass G12 or G14 with an unmet check and no dated finding.

---

## 📤 HANDOFF

`evidence/phase-47/handoff.md` must state:

1. **🚪 The G12 and G14 gate results**, with evidence links.
2. **The 15 SLOs as committed**, each with the measurement it was derived from.
3. **📊 Current SLO attainment** over the first 30 days.
4. **📊 The alert audit results** — how many alerts were deleted, how many were untested, and the false-positive rate.
5. **🧪 The incident simulation record** and its postmortem.
6. **The on-call rotation size** and, if under 4, the recorded risk with an owner.
7. **📊 The user satisfaction signal** and the top issue it surfaced.
8. **The error-budget policy** and who agreed to it.
9. **Stage 7 declaration** — the platform is now usable by people who did not build it, observable enough to diagnose, and operable by a team that can sustain it. Stage 8 (performance engineering) may begin.

---

## ➡️ NEXT

**[PHASE-48 — Benchmark Harness & Baselines (B1–B12)](PHASE-48.md)** — begin Stage 8. Consolidate every benchmark from every phase into one harness that runs on demand and in CI.
