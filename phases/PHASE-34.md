# PHASE 34 — Accounting, Showback & Utilization Analytics

| | |
|---|---|
| **Stage** | 5 — Scheduling & Orchestration |
| **Estimated effort** | 3–4 hours |
| **Depends on** | 30, 32, 33 |
| **Blocks** | 35, 47, 56 |
| **Risk** | 🟢 Low — read-only analytics; the risk is publishing numbers you cannot defend |
| **Blast radius** | Reporting only |
| **Architecture refs** | `ULTIMATE-PLAN.md#11-cost-model`, `#10-multi-tenancy`, `ARCHITECTURE.md#l10-observability` |

---

## 🎯 MISSION

Answer, defensibly: **who used what, what did it cost, and where did the capacity actually go?** Build per-tenant GPU-hour accounting, a cost model that includes power and amortized hardware, a showback report, and — most valuable of all — a **utilization waterfall** that accounts for every GPU-hour the cluster did not deliver.

> 💡 **WHY accounting matters in a private cluster with no bills.** Showback is not about charging money; it is about **making consumption visible**. A tenant who cannot see that they held 40 GPUs at 12 % utilization for three weeks will keep doing it. The waterfall is the operator's equivalent: it converts "the cluster feels busy" into "we lost 340 GPU-hours last week: 120 to idle allocations, 90 to fragmentation, 70 to failed jobs, 40 to preemption, 20 to quarantine." Each of those has a different fix, and you cannot prioritize what you cannot separate.

> ⚠️ **The trap: `DCGM_FI_DEV_GPU_UTIL`.** It reports whether *any* kernel was resident, so a job stuck in a data-loading loop reads 95–100 %. Every number in this phase must use `DCGM_FI_PROF_SM_ACTIVE` (fraction of SMs actually issuing work) or you will report a fully-utilized cluster that is in fact half idle. This distinction was established in Phase 18; here it becomes load-bearing.

---

## ✅ PREFLIGHT

```bash
# Metrics with tenant/job attribution
promtool query instant 'DCGM_FI_PROF_SM_ACTIVE' | head
promtool query instant 'kube_pod_labels{label_nexus_io_tenant!=""}' | head

# Kueue workload metrics (Phase 30)
promtool query instant 'kueue_admitted_workloads_total'

# Power telemetry (Phase 32)
promtool query instant 'nexus_power_circuit_draw_watts'

# Long-term storage available — accounting needs months, not 15 days
kubectl get pods -n monitoring -l app.kubernetes.io/name=mimir
```

---

## 📦 DELIVERABLES

```
observability/accounting/
  recording-rules.yaml              # 🎯 the derived metrics, computed once
  cost-model.yaml                   # rates: GPU-hour, CPU-hour, TB-month
  waterfall-rules.yaml              # utilization loss attribution
dashboards/
  tenant-showback.json
  utilization-waterfall.json        # 🎯 the operator's most important dashboard
  capacity-planning.json
  idle-resources.json
tools/accounting/
  showback-report.sh                # monthly per-tenant report
  waterfall.sh                      # where did the GPU-hours go?
  idle-reclaim.sh                   # find and (optionally) reclaim idle allocations
  gpu-hour-audit.sh                 # 🧪 reconcile: do the numbers add up?
docs/
  user/understanding-your-usage.md
  operations/accounting-methodology.md   # ⚠️ how every number is computed
evidence/phase-34/{preflight,acceptance,handoff,deviations}.md
```

---

## 📋 TASKS

### Task 1 — Define the unit of account

Everything reduces to the **allocated GPU-hour**, and the definition must be unambiguous.

```
allocated_gpu_hours = Σ over (job, gpu) of  hours the GPU was ASSIGNED to the job
                      (not hours the GPU was BUSY)
```

> 💡 **Bill on allocation, report on utilization.** A GPU held by a job is unavailable to everyone else whether or not it is busy — so allocation is the fair unit of account. Utilization is then reported *alongside* it, which is exactly the signal that changes behavior: "you were allocated 2,400 GPU-hours and used 18 % of them."

**Handle the sharing modes from Phase 19 explicitly:**

| Mode | Accounting |
|---|---|
| Exclusive GPU | 1.0 GPU-hour per hour |
| MPS quarter (25 % VRAM budget) | 0.25 GPU-hour per hour |
| Time-sliced (N-way) | 1/N GPU-hour per hour |
| Multi-GPU claim | Sum per device |

**And the non-obvious cases — decide and document each:**

| Case | Decision |
|---|---|
| Pod pending in queue | Not charged |
| Pod scheduled, container image pulling | ⚠️ **Charged** — the GPU is reserved and unavailable |
| Job preempted mid-run | Charged for time held; **the lost work is reported separately** |
| Node fails under a job | Charged? **No** — platform fault, credited back |
| GPU quarantined (Phase 23) | Not charged to any tenant; counted as platform loss |
| Idle notebook holding a GPU | ⚠️ **Charged in full** — this is the behavior showback exists to surface |

---

### Task 2 — The cost model

**`cost-model.yaml`** — rates derived from `ULTIMATE-PLAN.md §11`, recomputed with *your* measured numbers.

```yaml
hardware:
  gpu_node_capex: 12000            # per node, actual purchase
  amortization_months: 36
  gpus_per_node: 4
  # → capex per GPU-hour = 12000 / 36 / 730 / 4 = $0.114

power:
  measured_watts_per_gpu: 320      # ⚠️ from Phase 32's MEASURED table, capped
  pue: 1.35                        # facility overhead (cooling); measure it
  electricity_usd_per_kwh: 0.14
  # → power per GPU-hour = 320 × 1.35 / 1000 × 0.14 = $0.060

shared:
  network_capex_amortized: ...
  storage_capex_amortized: ...
  facility_monthly: ...            # rent, cooling capex, UPS
  staff_monthly: ...               # ⚠️ include it or the number is fiction
  # → allocated per GPU-hour by share of capacity

overhead_multiplier: 1.0           # applied to the sum

rates:
  gpu_hour_rtx4090: 0.62           # capex + power + shared
  gpu_hour_a6000: 0.94
  cpu_core_hour: 0.008
  memory_gb_hour: 0.001
  storage_t1_tb_month: 42.00
  storage_t2_tb_month: 18.00
  storage_t3_tb_month: 6.00
  egress_gb: 0.0                   # private cluster
```

> ⚠️ **Include staff cost or label the number "infrastructure cost only."** A GPU-hour rate that omits the people running the cluster will be compared against a cloud price that includes them, and the comparison will be wrong in a way that eventually embarrasses you. State the inclusion explicitly at the top of every report.

**Apply the Phase 33 preemptibility multipliers** (0.4× / 1.0× / 1.5×) so the rate card creates the right incentive.

📊 **Publish the honest comparison** — the cluster's actual rate vs. cloud on-demand and spot, with the caveats stated:
```
NEXUS RTX 4090       $0.62/GPU-hr   (at 65 % utilization; includes power + amortized capex + staff)
Cloud L4 on-demand   $0.80/GPU-hr
Cloud A10G spot      $0.35/GPU-hr
⚠️ Different GPUs, different reliability, no egress fees here, no elasticity here.
   At 30 % utilization our rate becomes $1.34 — UTILIZATION IS THE DOMINANT VARIABLE.
```

---

### Task 3 — 🎯 The utilization waterfall

This is the single most valuable artifact of the phase. **Every GPU-hour the cluster physically could have delivered must land in exactly one bucket.**

```
Theoretical capacity          = GPUs × hours in period          10,000 GPU-hr  100.0 %
 ├─ Unavailable: quarantined/failed hardware (Phase 23)            −180          1.8 %
 ├─ Unavailable: maintenance windows, upgrades (Phase 53)          −220          2.2 %
 ├─ Unavailable: power/thermal constrained (Phase 32)               −40          0.4 %
 ├─ Idle: no demand (queue was empty)                             −600          6.0 %
 ├─ Idle: demand existed but FRAGMENTATION prevented placement    −430          4.3 %  ⚠️
 ├─ Idle: demand existed but blocked by QUOTA (no borrowing)      −150          1.5 %  ⚠️
 ├─ Allocated but idle: jobs holding GPUs at <5 % SM_ACTIVE       −890          8.9 %  ⚠️⚠️
 ├─ Lost: preemption restart overhead (Phase 33)                  −120          1.2 %
 ├─ Lost: failed jobs (crashed, OOM, user error)                  −310          3.1 %
 └─ ✅ PRODUCTIVE: allocated and actively computing              7,060         70.6 %
```

**How to compute each bucket** — write this into `accounting-methodology.md`, because every number will eventually be challenged:

| Bucket | Query basis |
|---|---|
| Quarantined | Node taint duration × GPUs on node |
| Maintenance | Cordon duration during declared windows |
| Power constrained | Time the Phase 32 AdmissionCheck held workloads |
| Idle, no demand | GPU unallocated **and** Kueue pending queue empty |
| **Idle, fragmentation** | GPU unallocated **and** pending gang workloads exist **and** free GPUs ≥ requested but not placeable |
| Idle, quota-blocked | GPU unallocated **and** pending workloads exist **and** their queue is at its borrowing limit |
| **Allocated but idle** | Allocated **and** `SM_ACTIVE < 0.05` for > 15 min |
| Preemption overhead | Phase 33's per-event cost × event count |
| Failed jobs | Allocated time of jobs that exited non-zero (excluding preemption exit-0) |
| Productive | Remainder — **and it must reconcile** |

> 🧪 **`gpu-hour-audit.sh` must prove the buckets sum to 100 %** within a small tolerance. If they do not, a bucket is double-counting or a case is unhandled — find it. A waterfall that does not reconcile is worse than none, because it will be quoted anyway.

**The three ⚠️ buckets are the ones with actionable fixes:**

| Bucket | Fix | Owner phase |
|---|---|---|
| Allocated but idle | Idle reclaim, notebook culling, showback pressure | This phase + 43 |
| Fragmentation | Defragmentation, better bin-packing, elastic jobs | 31, 33 |
| Quota-blocked with idle capacity | Raise borrowing limits; re-tune the cohort | 30 |

---

### Task 4 — Idle detection and reclaim

**`tools/accounting/idle-reclaim.sh`** — the direct response to the largest waterfall bucket.

| Signal | Threshold | Action |
|---|---|---|
| Interactive notebook, no kernel activity | 30 min | Warn the user (Slack/email) |
| Interactive notebook, still idle | 60 min | Suspend; state preserved (Phase 43) |
| Batch job, `SM_ACTIVE < 5 %` | 30 min | 🚨 Alert the owner with a link to the profiling guide |
| Batch job, `SM_ACTIVE < 5 %` | 2 h | Flag for review; charge continues |
| Allocated GPU, **no process on the GPU at all** | 15 min | Reclaim — this is a leak, not a workload |
| Job past its declared `activeDeadlineSeconds` | Immediate | Terminate |

> ⚠️ **Reclaim only the unambiguous cases automatically.** "No process on the GPU" is a leak. "Low SM_ACTIVE" might be a legitimate workload — a job doing heavy CPU preprocessing, an inference server awaiting requests, a debugging session. **Killing someone's job because it looked idle destroys trust in the platform permanently.** Warn, report, charge — and let humans decide, except for true leaks.

> 💡 **Notebooks are the single biggest source of idle allocation in every research cluster.** A suspend-with-state-preserved policy (Phase 43) is far better received than termination, and recovers nearly the same capacity.

---

### Task 5 — Reports and dashboards

**`tools/accounting/showback-report.sh`** — per tenant, monthly:

```
TENANT: research-vision                          2026-08-01 → 2026-08-31
═══════════════════════════════════════════════════════════════════════
ALLOCATION
  GPU-hours allocated (RTX 4090)        2,412        $1,495
  CPU core-hours                       18,300        $  146
  Storage T2 (avg 12.4 TB)                           $  223
  ───────────────────────────────────────────────────────────
  TOTAL                                              $1,864
  (Infrastructure + power + amortized capex + staff. See methodology.)

UTILIZATION                                    ⚠️ ACTION
  Mean SM_ACTIVE while allocated          31 %   ← low
  GPU-hours at <5 % SM_ACTIVE            688     ← $426 of allocated-but-idle
  Top idle workload: notebook-jhw-3      412 hr
  Suggestion: run `nexus profile <job>` — most low-utilization jobs are
              data-loading bound (see the data-loading guide).

QUEUE
  Jobs submitted                          247
  Median time to admission              4m 12s
  p95 time to admission                 41m 08s
  Jobs preempted                           18   (avg 6 min lost each)
  Borrowed from cohort                    340 GPU-hr   (you benefited)
  Lent to cohort                          180 GPU-hr

QUOTA
  Nominal                                  32 GPUs
  Peak used                                49 GPUs (borrowed 17)
  Hours at quota ceiling                   88     ← consider requesting more
```

**Dashboards:**

| Dashboard | Audience | Key panels |
|---|---|---|
| `utilization-waterfall` | **Operators** | The waterfall, trended weekly; each bucket clickable to the underlying jobs |
| `tenant-showback` | Tenant leads | Their own allocation, utilization, cost, queue experience |
| `capacity-planning` | Platform + finance | Utilization trend, quota ceiling hits, growth projection (feeds Phase 56) |
| `idle-resources` | Operators | Live view of allocated-but-idle, ranked by GPU-hours wasted |

---

### Task 6 — The user-facing guide

**`docs/user/understanding-your-usage.md`** — framed as help, not blame.

```markdown
# Understanding your usage report

## "Allocated" vs. "used"
You are accounted for GPUs ASSIGNED to you, because while you hold them
nobody else can. Your utilization percentage shows how much of that you used.
Low utilization is not a penalty — it is a hint that something is slowing you down.

## Mean SM_ACTIVE below 40 %? You are probably I/O bound.
The most common cause by far is data loading. Check:
 1. Is your dataset staged to /scratch? (see the data-loading guide)
 2. num_workers ≈ 4 per GPU?
 3. Are you decoding JPEGs on the CPU?
Fixing this usually doubles throughput — it makes YOUR jobs finish sooner.
That is why we show you the number.

## Reducing what you're accounted for
· Use `nexus.io/preemptible: "true"` → 0.4× rate, and you get capacity faster
· Release notebooks when you stop using them (they are suspended after 60 min anyway)
· Use MPS fractional GPUs for development — 0.25 GPU-hour instead of 1.0
· Right-size: asking for 8 GPUs and using 2 is accounted as 8

## Being at your quota ceiling for many hours
That is the signal to request more quota. Bring your report — the hours-at-ceiling
number is exactly what the capacity discussion needs.
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | Every GPU-hour attributes to a tenant, job, and user | Query | 100 % attributed |
| **A2** | **All utilization figures use `SM_ACTIVE`, never `GPU_UTIL`** | Read the rules | Confirmed |
| **A3** | Sharing modes accounted at the correct fraction | Test each mode | Correct |
| **A4** | Edge cases (image pull, preemption, node failure, quarantine) handled per the table | Test each | Per spec |
| **A5** | Node-failure time is credited back, not charged | 🧪 Kill a node under a job | Credited |
| **A6** | Cost model uses **measured** power from Phase 32 | Compare | Measured |
| **A7** | Cost model states whether staff cost is included | Read the report header | Stated |
| **A8** | Preemptibility multipliers applied | Compare two jobs | Applied |
| **A9** | 🧪 **Waterfall buckets sum to 100 % ±1 %** | `gpu-hour-audit.sh` | Reconciles |
| **A10** | Each waterfall bucket's query is documented | `accounting-methodology.md` | Documented |
| **A11** | Fragmentation loss is separated from no-demand idle | Inspect | Separated |
| **A12** | Allocated-but-idle is separated from unallocated | Inspect | Separated |
| **A13** | Waterfall reproduces a known period correctly | 🧪 Cross-check a week by hand | Matches |
| **A14** | Idle detection identifies a truly idle notebook | Test | Detected |
| **A15** | **Automatic reclaim only fires on true leaks (no GPU process)** | Test both cases | Only leaks |
| **A16** | Users are warned before any suspension | Test | Warned |
| **A17** | Showback report generates per tenant | Run it | Generates |
| **A18** | Report includes queue experience, not just cost | Read it | Included |
| **A19** | All four dashboards render with real data | Open them | Render |
| **A20** | Waterfall dashboard drills down to specific jobs | Click through | Works |
| **A21** | Historical data retained ≥ 13 months | Check Mimir retention | ≥ 13 mo |
| **A22** | 📊 Cluster-wide utilization figure published with its definition | Report | Published |
| **A23** | Tenants can see only their own data | Test cross-tenant | Isolated |

---

## ↩️ ROLLBACK

```bash
# Accounting is read-only — rollback is just removing dashboards and rules
kubectl delete prometheusrule accounting-rules waterfall-rules -n monitoring

# ⚠️ The one thing with side effects: idle reclaim. Disable it first if in doubt.
kubectl patch cronjob idle-reclaim -n accounting -p '{"spec":{"suspend":true}}'
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Waterfall does not sum to 100 % | Double-counting or an unhandled case | Find it — A9 is not optional |
| Utilization looks impossibly high | Using `GPU_UTIL` | A2 |
| GPU-hours unattributed | Pods without tenant labels | Enforce labels in Phase 16's policy |
| Costs look wildly off vs. the electric bill | PUE guessed, not measured | Measure PUE: facility draw ÷ IT draw |
| Fragmentation bucket is zero but jobs wait | The query does not detect the case | Cross-check against Phase 31's fragmentation index |
| Tenants dispute their numbers | Methodology not published | Publish it; walk through one job by hand |
| Idle reclaim killed a legitimate job | Threshold too aggressive | This is a trust incident. Apologize, restore, tighten to leaks only. |
| Historical data missing | Prometheus retention, not Mimir | Ensure long-term storage is receiving |
| Report says 100 % utilization, users say the cluster is slow | Accounting allocation, not utilization — check both are shown | Show both side by side |

---

## 🚫 DO NOT

- **Do not** use `DCGM_FI_DEV_GPU_UTIL` for any reported number.
- **Do not** publish a waterfall that does not reconcile.
- **Do not** auto-kill jobs for low utilization — only for true leaks.
- **Do not** publish a GPU-hour rate without stating what is included.
- **Do not** compare your rate to cloud without stating utilization sensitivity.
- **Do not** charge tenants for platform faults.
- **Do not** let one tenant see another's data.
- **Do not** implement notebook suspension here. Phase 43 owns the mechanism; this phase supplies the signal.

---

## 📤 HANDOFF

`evidence/phase-34/handoff.md` must state:

1. **📊 The first full utilization waterfall**, reconciled — the baseline every later improvement is measured against.
2. **The three largest addressable loss buckets** with their owning phase, ranked by GPU-hours.
3. **The cost model and its inputs**, including measured power, PUE, and whether staff cost is included.
4. **📊 The current effective GPU-hour rate** and its utilization sensitivity curve.
5. **The accounting methodology document** — what is charged, what is not, and every edge case decision.
6. **Idle allocation totals** per tenant — the input to Phase 43's notebook policy.
7. **Tenants persistently at their quota ceiling** — feeds Phase 56's capacity plan.
8. **Data retention configured** for accounting history.

---

## ➡️ NEXT

**[PHASE-35 — Slurm Interoperability & Scheduling Gate (G8)](PHASE-35.md)** — meet HPC users where they are, then close Stage 5.
