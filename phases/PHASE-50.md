# PHASE 50 — Performance CI & Regression Gates

| | |
|---|---|
| **Stage** | 8 — Performance Engineering |
| **Estimated effort** | 3–4 hours |
| **Depends on** | 15, 48, 49 |
| **Blocks** | 52, 53 |
| **Risk** | 🟡 Medium — a gate that blocks legitimate changes is worse than none |
| **Blast radius** | The change pipeline |
| **Architecture refs** | `ULTIMATE-PLAN.md#8-performance-budget`, Law VIII, Phase 15's GitOps guardrails |

---

## 🎯 MISSION

**Lock in what Phase 49 just gained.** Wire the benchmark harness into the change pipeline so that any change which degrades performance is caught automatically — before it reaches the fleet, or immediately after — and make the response proportionate: block what should be blocked, alert on what should be investigated, and never turn the gate into an obstacle people learn to bypass.

> 💡 **WHY performance decays without a gate.** Every upgrade, every policy change, every new DaemonSet takes a small bite. A Kubernetes minor upgrade costs 0.5 % of B3. A new security agent costs 1 % of B1. A CNI update costs 2 % of B5. None is worth blocking on its own; **the cumulative effect over a year is the 30 % erosion that Phase 48's baseline-creep rule exists to prevent.** Continuous measurement is the only thing that makes each individual regression visible while it is still attributable to one change.

> ⚠️ **The failure mode of performance CI is being ignored.** A gate that is slow, flaky, or blocks on noise gets bypassed within a month — first with an override flag, then by default. **Speed, low false-positive rate, and proportionate response are not nice-to-haves; they are what determines whether the gate survives.**

---

## ✅ PREFLIGHT

```bash
# 📊 The post-tuning baseline (Phase 49's output)
jq '.metadata' benchmarks/baselines/baseline.json

# The harness, working and fast
time bash benchmarks/harness/run.sh --quick

# 📊 Measured spread per benchmark — the thresholds derive from this
jq '.[].spread_pct' benchmarks/baselines/baseline.json

# GitOps pipeline (Phase 15) and CI
cat .github/workflows/*.yaml | grep -c "name:"
argocd app list
```

---

## 📦 DELIVERABLES

```
.github/workflows/
  perf-pr.yaml                      # fast checks on every PR
  perf-nightly.yaml                 # --quick, every night
  perf-weekly.yaml                  # --standard
  perf-release.yaml                 # --full before a major change
benchmarks/gates/
  thresholds.yaml                   # 🎯 per-benchmark, derived from measured spread
  gate.sh                           # evaluate a run against the baseline
  triage.sh                         # ⚠️ regression → probable cause
observability/rules/
  performance-regression-alerts.yaml
dashboards/
  performance-trends.json           # 📊 12-month view per benchmark
docs/operations/
  performance-ci.md
  regression-response.md            # what to do when the gate fires
evidence/phase-50/{preflight,acceptance,handoff,deviations}.md
```

---

## 📋 TASKS

### Task 1 — 🎯 Thresholds derived from measured noise

A threshold tighter than the benchmark's natural variance produces constant false positives.

```
threshold = max(2.5 × measured_spread, minimum_meaningful_change)
```

| Benchmark | Measured spread | Warn at | **Block at** | Notes |
|---|---|---|---|---|
| B1 GEMM | ±0.4 % | -1 % | **-3 %** | Very stable |
| B2 PCIe | ±0.6 % | -2 % | -4 % | |
| B3 TCP | ±0.8 % | -2 % | -5 % | |
| B4 RDMA bw | ±0.5 % | -1.5 % | **-3 %** | Stable; a drop means a real fabric change |
| B4 RDMA lat | ±0.2 µs | +0.5 µs | +1 µs | |
| B5 NCCL busbw | ±1.2 % | -3 % | **-6 %** | |
| B6 fio | ±3 % | -8 % | -15 % | ⚠️ Noisiest — SLC cache, background I/O |
| B9 Training | ±1.5 % | -3 % | **-6 %** | The headline number |
| B10 TTFT p95 | ±5 % | +10 % | +20 % | Latency is inherently noisier |
| B11 Admission | ±8 % | +20 % | +50 % | Very noisy; depends on cluster state |

> ⚠️ **B6's noise is real, not sloppiness.** Consumer NVMe performance depends on SLC cache state, prior write history, and FTL garbage collection. A 15 % block threshold on storage is honest; a 3 % one would fire weekly for no reason. **Set the threshold to the benchmark's actual behavior, not to what you wish it were.**

**Improvements need attention too:**
```
⚠️ A benchmark improving > 10 % unexpectedly is a red flag, not a win.
   Usual causes: the benchmark broke and is measuring less work; a safety
   check was disabled; the run was not actually quiet.
   Investigate improvements with the same seriousness as regressions.
```

---

### Task 2 — Where to gate, and how hard

Match the check's cost to the change's risk.

| Trigger | Suite | Duration | Response |
|---|---|---|---|
| **PR touching tuning/, talos/, CNI, GPU operator, storage** | `--quick` on 2 canary nodes | ~20 min | 🚫 **Block merge** on a block-level regression |
| PR touching anything else in the cluster config | Config lint only | seconds | Advisory |
| **Post-merge to main** | `--quick` | ~20 min | Alert; auto-revert offered |
| **Nightly** | `--quick`, full fleet sample | ~20 min | Alert on regression |
| Weekly | `--standard` | ~2 h | Alert; trend update |
| **Before a Kubernetes / driver / CNI upgrade** | `--full` | ~8 h | Record pre-state |
| **After that upgrade** | `--full` | ~8 h | ⚠️ **Compare; this is where upgrades get caught** |
| Monthly | `--full` | ~8 h | Trend, baseline candidate |

> 💡 **The pre/post-upgrade pair is the highest-value use of this harness.** Kubernetes minor upgrades, GPU driver updates, and CNI upgrades are exactly the changes that cost 1–2 % silently. Running `--full` before and after makes the cost explicit and attributable, so it becomes a decision ("driver 575 costs 1.8 % and fixes Xid 79 — accept") rather than an unexplained drift.

**Path-based triggering keeps the gate fast.** A PR that changes a Grafana dashboard should not run a 20-minute GPU benchmark. Only paths that can plausibly affect performance trigger the expensive check.

---

### Task 3 — ⚠️ Proportionate response

Blocking everything makes the gate the enemy. Three tiers:

| Level | Condition | Response |
|---|---|---|
| **ℹ️ Info** | Within spread | Record; update the trend |
| **🟡 Warn** | Beyond warn threshold, within block | Comment on the PR / open a ticket. **Do not block.** |
| **🔴 Block** | Beyond block threshold, **confirmed by a re-run** | Block merge; require an override with a written justification |

> ⚠️ **"Confirmed by a re-run" is essential.** A single benchmark run that trips the block threshold must be re-run before blocking anything. Roughly one in twenty runs will be anomalous for reasons unrelated to the change, and blocking on those is exactly how the gate loses credibility.

**The override path must exist and must be visible:**
```yaml
# In the PR:
performance-override:
  benchmark: B5
  regression: -7.2 %
  justification: "NCCL 2.24 has a known regression on RoCE; upgrading anyway
                  because it fixes the hang in issue #412. Tracked as PERF-88.
                  Revisit at NCCL 2.25."
  approver: <name>
  expires: 2026-11-01
```

> 💡 **An override with an expiry and a tracking item is a decision; a silent bypass is a leak.** Make overriding easy but *visible* — every override appears in a monthly report, and expired overrides alert. Making it hard just pushes people to disable the check.

---

### Task 4 — 🎯 Triage: from "B5 regressed" to "here's why"

**`benchmarks/gates/triage.sh`** — uses Phase 48's dependency graph plus the change context.

```
🔴 REGRESSION DETECTED — B9 training scaling @32 GPUs: 88.6 % → 81.4 % (-8.1 %)

DEPENDENCY CHECK (upstream first):
  B3 TCP        95.8 → 95.7   ✅ unchanged
  B4 RDMA       97.2 → 97.1   ✅ unchanged
  B5 NCCL       87.1 → 80.2   🔴 REGRESSED (-7.9 %)
  → B9's regression is EXPLAINED by B5. Investigate B5, not B9.

B5 CONTEXT:
  Changed since the last good run:
    · NCCL 2.23.4 → 2.24.1                    ⚠️ likely cause
    · Cilium 1.17.1 → 1.17.2
    · 2 nodes replaced (nexus-gpu-041, -042)
  Placement: same leaf domain ✅
  Conditions: cluster_quiet=true ✅
  Per-node timing: nexus-gpu-041 is 12 % slower than the cohort  ⚠️ ALSO investigate

SUGGESTED NEXT STEPS:
  1. Re-run B5 excluding the 2 replaced nodes → isolates hardware from software
  2. Pin NCCL back to 2.23.4 on 2 nodes → isolates the library
```

> 💡 **Triage that names the probable cause is what makes the gate useful rather than merely blocking.** Without it, "B9 regressed" starts a multi-day investigation. With it, the first hypothesis is on the screen with the experiment to test it — which is exactly Phase 49's protocol applied to an unplanned change.

---

### Task 5 — 📊 Trends: catching what no single comparison can

**`dashboards/performance-trends.json`** — 12 months, per benchmark, with annotations.

```
B5 NCCL busbw (GB/s) — 12 months
 11.0│                    ╭─╮
 10.5│  ╭──────╮    ╭─────╯ ╰──╮
 10.0│──╯      ╰────╯          ╰────────╮
  9.5│                                   ╰────
     └─────────────────────────────────────────
      Sep  Nov  Jan  Mar  May  Jul  Sep
      ▲         ▲              ▲       ▲
      │         │              │       └ NCCL 2.24 (-7.9 %) ← PERF-88
      │         │              └ K8s 1.33→1.34 (-0.6 %)
      │         └ EXP-021 NCCL tuning (+3.1 %)
      └ baseline established

  ⚠️ Net 12-month change: -4.1 %.  Attributed: -8.5 %, unexplained: +4.4 %
```

> ⚠️ **The "unexplained" line is the point of the trend chart.** If a benchmark has drifted more than the sum of attributed changes, something is happening that no gate caught — aging hardware, thermal changes, an unrecorded configuration drift. **Review it quarterly.** This is the mechanism that catches slow decay that per-change gating structurally cannot.

**Annotate the chart automatically** from the change log: every upgrade, every accepted tuning experiment, every override. A chart without annotations is a mystery; with them it is an explanation.

---

### Task 6 — Alerts and the monthly report

| Alert | Threshold |
|---|---|
| `PerformanceRegressionBlocking` | A block-level regression confirmed |
| `PerformanceRegressionWarning` | Warn-level, 3 consecutive runs |
| `PerformanceUnexpectedImprovement` | > 10 % better — investigate |
| `BenchmarkFailed` | The benchmark itself errored |
| `BenchmarkSkippedNotQuiet` | 3 consecutive runs skipped for contention |
| `PerformanceOverrideExpired` | An override past its date |
| `BaselineStale` | No accepted baseline in 90 days |
| `UnexplainedDrift` | Trend drift exceeds attributed changes by > 3 % |

📊 **The monthly performance report:**
```
PERFORMANCE REPORT — 2026-08
  Benchmarks run: 31 quick, 4 standard, 1 full
  🔴 Blocking regressions:  1  (B5, NCCL 2.24 — overridden, PERF-88)
  🟡 Warnings:              3  (B6 ×2 — noise; B11 ×1 — queue depth)
  Skipped (not quiet):      2
  Overrides active:         1  (expires 2026-11-01)
  ⚠️ Unexplained drift:     B2 -1.4 % over 3 months — INVESTIGATE
  Baseline age:            42 days ✅
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | **Thresholds derived from measured spread, per benchmark** | `thresholds.yaml` vs. baseline spreads | Derived |
| **A2** | PR gate triggers only on performance-relevant paths | 🧪 PR touching docs | Not triggered |
| **A3** | PR gate triggers on a tuning/CNI/storage change | 🧪 PR touching `talos/` | Triggered |
| **A4** | `--quick` gate completes in < 25 min | Time it | Met |
| **A5** | 🧪 **A block-level regression blocks the merge** | Inject one | Blocked |
| **A6** | **Blocking requires a confirming re-run** | Read the workflow | Required |
| **A7** | 🧪 A single anomalous run does NOT block | Inject one-off noise | Not blocked |
| **A8** | Warn-level produces a comment, not a block | 🧪 Inject | Comment only |
| **A9** | Override path works and requires a justification + expiry | 🧪 Use it | Enforced |
| **A10** | Overrides appear in the monthly report | Check | Listed |
| **A11** | `PerformanceOverrideExpired` fires | 🧪 Backdate one | Fires |
| **A12** | 🎯 **`triage.sh` correctly identifies an upstream cause** | 🧪 Induce a B4 regression, check the B9 report | Points upstream |
| **A13** | Triage lists changes since the last good run | Inspect output | Listed |
| **A14** | Triage flags per-node outliers | 🧪 With a slow node | Flagged |
| **A15** | Nightly, weekly, monthly schedules all run | Observe 30 days | Run |
| **A16** | Pre/post-upgrade comparison workflow exists and is used | 🧪 Run an upgrade through it | Used |
| **A17** | 📊 12-month trend dashboard renders with annotations | Open | Renders |
| **A18** | Annotations generated automatically from the change log | Check | Automatic |
| **A19** | 📊 **The "unexplained drift" figure is computed** | Dashboard | Computed |
| **A20** | `PerformanceUnexpectedImprovement` fires on a 10 % gain | 🧪 Inject | Fires |
| **A21** | `BenchmarkSkippedNotQuiet` fires after 3 skips | 🧪 Simulate | Fires |
| **A22** | 📊 Monthly report generates | Run | Generates |
| **A23** | False-positive rate over 30 days < 10 % | Measure | Met |
| **A24** | The gate has not been bypassed without an override | Audit | Clean |

---

## ↩️ ROLLBACK

```bash
# Downgrade blocking to advisory — keeps the signal, removes the obstacle
yq -i '.gates[].mode = "warn"' benchmarks/gates/thresholds.yaml

# Disable the PR gate entirely (⚠️ regressions become invisible until nightly)
gh workflow disable perf-pr.yaml
```

> 💡 **Downgrading to advisory is the right first response if the gate is misbehaving** — it preserves the measurement and the trend while removing the friction, so you can fix the thresholds without anyone learning to work around the check.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Gate fires constantly on one benchmark | Threshold tighter than the noise | A1 — re-derive from the measured spread |
| Gate never fires despite real regressions | Thresholds too loose; or the wrong benchmarks in `--quick` | Review what `--quick` covers |
| PR gate too slow, people complain | `--quick` is not quick | A4 — trim the suite |
| Runs skipped for contention constantly | No reserved capacity for benchmarks | Phase 48's `benchmark-queue` |
| Triage points at the wrong thing | Dependency graph incomplete | Extend it |
| Trend chart shows drift nobody caught | Per-change gating cannot see slow decay | A19 — that is what the trend is for |
| Overrides accumulating | Real regressions not being fixed | Review in the monthly report; escalate |
| Someone disabled the workflow | The gate is causing pain | Find out why; downgrade to advisory rather than removing |
| Benchmarks fail after an upgrade | The harness itself depends on something that changed | Version-pin the harness's own dependencies |
| Post-upgrade comparison missing | Pre-upgrade run not taken | A16 — make it part of the upgrade runbook (Phase 53) |

---

## 🚫 DO NOT

- **Do not** set a threshold tighter than the benchmark's measured spread.
- **Do not** block on a single run.
- **Do not** make the override path hard — make it visible.
- **Do not** run expensive benchmarks on every PR.
- **Do not** ignore unexpected improvements.
- **Do not** let overrides expire unreviewed.
- **Do not** upgrade Kubernetes, drivers, or the CNI without a pre/post `--full` pair.
- **Do not** treat a green gate as proof of no decay — that is what the trend is for.

---

## 📤 HANDOFF

`evidence/phase-50/handoff.md` must state:

1. **The thresholds per benchmark** and the measured spread each was derived from.
2. **📊 The false-positive rate** over the first 30 days — the number that predicts whether the gate survives.
3. **🎯 Triage effectiveness** — how often it named the correct cause.
4. **What `--quick` covers** and what it therefore cannot catch between weekly runs.
5. **The override policy** and any active overrides with expiry dates.
6. **📊 The 12-month trend baseline**, including the unexplained-drift figure.
7. **The pre/post-upgrade procedure**, and its handoff into Phase 53's upgrade runbook.
8. **Benchmarks too noisy to gate on**, and how they are monitored instead.

---

## ➡️ NEXT

**[PHASE-51 — Distributed Profiling & Bottleneck Analysis](PHASE-51.md)** — give users and operators the tools to find out *why* something is slow, not just that it is.
