# PHASE 48 — Benchmark Harness & Baselines (B1–B12)

| | |
|---|---|
| **Stage** | 8 — Performance Engineering |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 37, 39, 45, 47 |
| **Blocks** | 49, 50, 52 |
| **Risk** | 🟢 Low — measurement only; the risk is measuring badly and trusting it |
| **Blast radius** | Every performance claim the platform makes |
| **Architecture refs** | `ULTIMATE-PLAN.md#83-the-twelve-golden-benchmarks`, `#8-performance-budget`, Law VIII |

---

## 🎯 MISSION

Consolidate the twelve golden benchmarks — scattered across twenty phases as one-off scripts — into **one harness** that runs reproducibly on demand, produces machine-readable results, and establishes the **authoritative baseline** that Phase 50's regression gates and Phase 52's scale-out validation are measured against.

> 💡 **WHY consolidate now rather than earlier.** Each benchmark was written when its subsystem was built, against the hardware and configuration of that moment. They have different output formats, different assumptions about a quiet cluster, and no shared notion of "the baseline." Stage 8 is where performance stops being a per-phase concern and becomes a *managed property* — and that requires one harness, one result schema, and one baseline file.

> ⚠️ **The measurement hazard that invalidates everything: benchmarking on a busy cluster.** Every number in `ULTIMATE-PLAN.md §8` assumes an uncontended measurement. A B5 run while someone's Spark shuffle saturates the fabric produces a number that looks like a regression and is not. **The harness must know whether the cluster was quiet, record it, and refuse to write a baseline from a contended run.**

---

## ✅ PREFLIGHT

```bash
# Every benchmark's one-off script from its originating phase
ls tools/*/bench-*.sh

# Existing baselines, in whatever format they were written
ls benchmarks/baselines/

# Observability able to answer "was the cluster quiet?"
promtool query instant 'sum(DCGM_FI_PROF_SM_ACTIVE)'

# A reservable window (Phase 30 quota, or a maintenance window)
kubectl get clusterqueue benchmark-queue 2>/dev/null || echo "create it"
```

---

## 📦 DELIVERABLES

```
benchmarks/
  harness/
    run.sh                          # 🎯 the single entry point
    schema.json                     # ⚠️ one result format for all 12
    quiet-check.sh                  # is the cluster contended right now?
    reserve.sh                      # take exclusive capacity for a run
  suites/
    b01-gemm.sh          b07-storage.sh
    b02-pcie.sh          b08-object.sh
    b03-tcp.sh           b09-training.sh
    b04-rdma.sh          b10-inference.sh
    b05-nccl.sh          b11-scheduling.sh
    b06-fio.sh           b12-e2e.sh
  baselines/
    baseline.json                   # 🎯 THE authoritative record
    baseline-history/               # every accepted baseline, versioned
  reports/
    compare.sh                      # baseline vs. a run
    report.sh                       # human-readable
clusters/nexus-prod/benchmarks/
  benchmark-clusterqueue.yaml       # dedicated, high-priority, small
  benchmark-cronjob.yaml            # weekly full suite
docs/operations/benchmarking.md
evidence/phase-48/{preflight,acceptance,handoff,deviations}.md
```

---

## 📋 TASKS

### Task 1 — 🎯 One result schema

Every benchmark emits the same shape. Without this, comparison and regression detection are impossible.

```json
{
  "benchmark": "b05-nccl",
  "version": "1.2.0",
  "timestamp": "2026-08-31T02:14:33Z",
  "git_commit": "a91f3e2",
  "environment": {
    "cluster_version": "1.34.3",
    "gpu_driver": "560.35.03",
    "nccl_version": "2.23.4",
    "node_count": 8,
    "nodes": ["nexus-gpu-001", "..."],
    "gpu_model": "NVIDIA-GeForce-RTX-4090",
    "leaf_domains": ["leaf-01"],
    "power_cap_watts": 320
  },
  "conditions": {
    "cluster_quiet": true,                  // ⚠️ THE field that validates the run
    "concurrent_gpu_utilization_pct": 2.1,
    "concurrent_fabric_utilization_pct": 0.8,
    "exclusive_reservation": true
  },
  "results": {
    "busbw_gbps": { "value": 10.4, "unit": "GB/s" },
    "algbw_gbps": { "value": 5.9,  "unit": "GB/s" },
    "pct_of_theoretical": { "value": 86.7, "unit": "%" }
  },
  "raw_output_uri": "s3://nexus-artifacts/benchmarks/b05/2026-08-31T02-14-33Z.log",
  "duration_seconds": 412,
  "status": "ok"
}
```

> ⚠️ **The `environment` block is what makes a comparison meaningful.** A B9 result from before a driver upgrade is not comparable to one after — unless you recorded the driver version, in which case the difference *is the finding*. Capture everything that could plausibly change a result.

> 💡 **`conditions.cluster_quiet` is the gate on baseline acceptance.** `quiet-check.sh` samples cluster-wide GPU and fabric utilization before and during the run; if either exceeds a threshold, the result is recorded with `cluster_quiet: false` and **refused as a baseline**, though it is still useful as a spot check.

---

### Task 2 — The twelve suites, consolidated

Each `bXX-*.sh` wraps the originating phase's script and emits the schema. Do not rewrite the benchmarks; wrap them.

| # | Benchmark | Measures | From | Target |
|---|---|---|---|---|
| **B1** | GEMM | Single-GPU compute | 18 | ≥ 98 % of bare metal |
| **B2** | PCIe H2D/D2H | Host↔device bandwidth | 18 | ≥ 90 % theoretical |
| **B3** | TCP throughput | Pod-to-pod network | 13 | ≥ 94 % line rate |
| **B4** | RDMA | `ib_write_bw` / `ib_send_lat` | 21 | ≥ 96 % line rate, p99 < 3 µs |
| **B5** | NCCL busbw | Collectives | 22 | ≥ 85 % theoretical @ 8 nodes |
| **B6** | fio | Storage tiers T0–T2 | 24–27 | Per-tier table |
| **B7** | CephFS + HPL | Shared FS metadata; CPU peak | 27, 39 | ≥ 75 % of peak |
| **B8** | Object + cache | S3 throughput, cache hit | 28 | Per-tier |
| **B9** | **Training scaling** | End-to-end, 1→64 GPUs | 37 | §8.2 targets |
| **B10** | Inference | TTFT/TPOT/throughput curve | 40 | SLO targets |
| **B11** | Scheduling latency | Admission, placement | 30, 31 | < 60 s p95 |
| **B12** | **End-to-end pipeline** | The whole platform | 41 | Recorded |

**Run modes:**

| Mode | Suites | Duration | When |
|---|---|---|---|
| `--quick` | B1, B3, B5, B6, B11 | ~20 min | Post-change smoke test |
| `--standard` | All except B9 at > 16 GPUs and B12 | ~2 h | Weekly |
| `--full` | Everything, including B9 to max scale | ~8 h | Monthly, and before/after major changes |
| `--single BXX` | One | Varies | Investigation |

---

### Task 3 — ⚠️ Reproducibility: the things that silently change results

**`reserve.sh`** — takes exclusive capacity so the run is valid:
```
· Submit to benchmark-queue at nexus-critical priority
· Cordon nothing — instead, request the nodes and let Kueue preempt
· ⚠️ Record which physical nodes were used; pin them for future runs where possible
· Release immediately on completion
```

**The variables that must be held constant or recorded:**

| Variable | Why it matters | Handling |
|---|---|---|
| **Which physical nodes** | Node-to-node variance is real (Phase 21's mesh heatmap) | Pin the same nodes; record them |
| **Leaf domain placement** | 8–14 % difference (Phase 22) | Pin to one leaf; record it |
| **GPU power cap** | Directly scales B1/B9 (Phase 32's table) | Record; never benchmark uncapped if you run capped |
| **Thermal state** | A warm GPU throttles | ⚠️ Warm-up period, then measure; record temps |
| **Driver/CUDA/NCCL versions** | | In `environment` |
| **Concurrent load** | | `quiet-check.sh` |
| **Time of day** | Ambient temperature varies | Record; prefer a consistent window |
| **Kernel/BIOS settings** | Phase 20's isolation | Record the relevant subset |

> ⚠️ **Thermal state is the most commonly missed variable.** A GPU benchmarked cold reads several percent faster than the same GPU after 20 minutes of sustained load — and sustained load is the real operating condition. **Every compute benchmark must include a warm-up phase and report the temperature at measurement time.** Otherwise B1 drifts by season.

> 💡 **Run each benchmark at least 3 times and report the median with the spread.** A single number hides variance, and variance is itself a finding: a B4 that ranges 8.9–11.2 GB/s across runs is telling you something about the fabric that the median conceals.

---

### Task 4 — 🎯 The baseline, and what it means to accept one

`baseline.json` is the authoritative claim about this cluster's performance. Changing it is a decision, not a side effect.

**Acceptance rules:**

| Rule | Enforcement |
|---|---|
| A baseline may only be written from a `cluster_quiet: true` run | `run.sh --write-baseline` refuses otherwise |
| A baseline requires 3 consecutive consistent runs (spread < 5 %) | Refuses otherwise |
| **Accepting a baseline is an explicit, committed action** | `--write-baseline` + a Git commit with a reason |
| **A worse baseline requires a written justification** | ⚠️ The most important rule |
| Every accepted baseline is archived in `baseline-history/` | Never overwritten |

> 🚫 **The failure this prevents: baseline creep.** Without the "worse baseline requires justification" rule, every regression eventually gets absorbed by re-baselining, and eighteen months later the cluster is 30 % slower than at commissioning with no single moment where anyone decided that was acceptable. **Each individual 2 % is defensible; the aggregate is not.**

**When accepting a worse baseline IS correct:**
```
✅ "Driver 560→575 costs 1.8 % on B1 but fixes the Xid 79 crashes. Accepted;
    tracked as NVIDIA bug #xyz; revisit at the next driver release."
✅ "Power cap lowered 320W→290W for the R3 circuit constraint (Phase 32).
    B1 drops 4 %. This is a deliberate power/performance trade."
❌ "B5 dropped 6 %, we're not sure why, re-baselining to make CI green."
```

---

### Task 5 — Reporting and comparison

**`reports/compare.sh`:**
```
BENCHMARK COMPARISON — baseline (2026-07-15) vs. run 2026-08-31
                                 baseline    current    delta    status
  B1  GEMM (TFLOPS)                 82.4       82.1     -0.4 %   ✅
  B2  PCIe H2D (GB/s)               24.8       24.7     -0.4 %   ✅
  B3  TCP (Gb/s)                    94.2       94.0     -0.2 %   ✅
  B4  RDMA bw (Gb/s)                96.8       96.9     +0.1 %   ✅
  B5  NCCL busbw @8 (GB/s)          10.4        9.6     -7.7 %   🔴 REGRESSION
  B6  T0 seq read (GB/s)             6.5        6.5      0.0 %   ✅
  ...
  B9  Scaling eff @32 GPU (%)       88.1       81.4     -7.6 %   🔴 REGRESSION
  ...
  ⚠️ B5 and B9 both regressed by ~7.7 %. These are related — B9 depends on B5.
     Investigate the fabric first (B5 is upstream of B9).

  Conditions: cluster_quiet=true, same nodes, same leaf domain.
  Changed since baseline: NCCL 2.23.4 → 2.24.1
```

> 💡 **The correlation hint is what turns a report into a diagnosis.** B9 depends on B5, which depends on B4, which depends on B3. Encode that dependency graph so the report says "investigate the upstream benchmark first" rather than presenting twelve independent numbers. This is the same layering the whole project is built on.

**The benchmark dependency graph:**
```
B1 (GEMM) ──────────────────┐
B2 (PCIe) ──────────────────┤
B3 (TCP) → B4 (RDMA) → B5 (NCCL) ─┼→ B9 (training) → B12 (e2e)
B6 (fio) → B7/B8 (storage) ───────┤
B11 (scheduling) ─────────────────┘
                              B10 (inference) ← B1, B2
```

---

### Task 6 — Scheduled runs and history

**`benchmark-cronjob.yaml`:**

| Schedule | Mode | Purpose |
|---|---|---|
| Nightly 02:00 | `--quick` | Catch fast regressions |
| Weekly Sunday 01:00 | `--standard` | Trend tracking |
| Monthly | `--full` | Full picture including scaling |
| On demand | Any | Investigation, pre/post change |

📊 **Trend, not just comparison.** Store every run (not just baselines) and chart each benchmark over time. A benchmark that drifts 0.5 % per month is invisible in any single comparison and obvious in a 12-month chart — and it is exactly the kind of decay this phase exists to catch.

> ⚠️ **A nightly benchmark competes with real work.** Give it a small dedicated quota at high priority so it can run, but keep `--quick` genuinely quick (~20 min) and schedule the heavier modes for low-demand windows. If benchmarking consumes 3 % of cluster capacity, it has become the problem it was meant to detect.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | All twelve suites run from one entry point | `run.sh --full` | All run |
| **A2** | **Every result conforms to the schema** | Validate against `schema.json` | Valid |
| **A3** | `environment` block captures versions, nodes, GPU model, power cap | Inspect a result | Complete |
| **A4** | **`quiet-check.sh` correctly detects a contended cluster** | 🧪 Run under load | Detects |
| **A5** | **A contended run cannot become a baseline** | 🧪 Try | Refused |
| **A6** | 3-run consistency required for a baseline | 🧪 Try with 1 run | Refused |
| **A7** | Baseline acceptance is explicit and committed | Test | Explicit |
| **A8** | **A worse baseline requires a written justification** | 🧪 Try without | Refused |
| **A9** | Every accepted baseline archived in history | Check | Archived |
| **A10** | Warm-up phase implemented for compute benchmarks | Read the code | Present |
| **A11** | GPU temperature recorded at measurement time | Inspect results | Recorded |
| **A12** | Node selection pinned and recorded | Inspect | Pinned |
| **A13** | Leaf-domain placement recorded | Inspect | Recorded |
| **A14** | Median of ≥ 3 runs reported with spread | Inspect | Reported |
| **A15** | `reserve.sh` obtains exclusive capacity | 🧪 Run on a loaded cluster | Exclusive |
| **A16** | `compare.sh` produces the comparison table | Run | Produces |
| **A17** | 🎯 **The dependency graph produces correlation hints** | 🧪 Induce a B4 regression | Hints at upstream |
| **A18** | `--quick` completes in < 25 min | Time it | Met |
| **A19** | `--full` completes in < 10 h | Time it | Met |
| **A20** | Scheduled runs execute without disrupting user work | Observe a week | No complaints/impact |
| **A21** | 📊 Historical trend chart per benchmark | Dashboard | Renders |
| **A22** | 📊 **The authoritative baseline is established and committed** | `baseline.json` | Committed |
| **A23** | Baseline values match or explain differences from each phase's original | Cross-reference | Reconciled |
| **A24** | Raw output archived to object storage | Check URIs | Archived |

---

## ↩️ ROLLBACK

```bash
# Benchmarking is read-only against the cluster — disable the schedule if it disrupts
kubectl patch cronjob benchmark-nightly -n benchmarks -p '{"spec":{"suspend":true}}'

# Revert a baseline acceptance
git revert <the baseline commit>
# baseline-history/ retains every previous baseline regardless
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Results vary wildly between runs | Cluster not quiet; or thermal state differs | A4/A10 — check `conditions` |
| A benchmark regresses only at night | Ambient temperature; or a nightly job competing | Record time-of-day; check `quiet` |
| B5 regressed but B4 did not | Above the fabric: NCCL config or placement | A17's hint should say this |
| All benchmarks regressed uniformly | Power cap changed, or a driver/BIOS change | Check `environment` diff |
| One node consistently slower | Real hardware variance | Phase 23 H10/H7; consider excluding and RMA |
| Benchmark cannot get capacity | Queue too small or priority too low | Adjust `benchmark-clusterqueue` |
| Baseline write refused | Working as designed | Read the refusal reason — usually `cluster_quiet: false` |
| Historical comparison meaningless | `environment` fields missing from old results | Backfill what you can; note the discontinuity |
| `--full` takes 14 hours | B9 at max scale dominates | Split B9 into its own schedule |

---

## 🚫 DO NOT

- **Do not** write a baseline from a contended run.
- **Do not** benchmark a cold GPU and call it steady-state.
- **Do not** accept a worse baseline without a written reason.
- **Do not** compare results across different node sets without saying so.
- **Do not** report a single run as a result.
- **Do not** rewrite the phase-specific benchmarks — wrap them.
- **Do not** let the benchmark schedule consume meaningful capacity.
- **Do not** perform tuning in this phase. Phase 49.

---

## 📤 HANDOFF

`evidence/phase-48/handoff.md` must state:

1. **📊 The authoritative `baseline.json`** — all twelve benchmarks, the conditions under which each was measured, and the nodes used.
2. **Any discrepancy** between this baseline and the original per-phase numbers, with the explanation.
3. **The variance observed** per benchmark across the 3-run sets — which benchmarks are stable and which are noisy.
4. **Nodes with anomalous performance** relative to their cohort.
5. **The benchmark dependency graph** as implemented.
6. **The schedule** and its measured capacity cost.
7. **What is NOT covered** by the twelve — gaps a future benchmark should fill.

---

## ➡️ NEXT

**[PHASE-49 — Systematic Tuning: BIOS → Kernel → Runtime](PHASE-49.md)** — with a trustworthy baseline in place, change one thing at a time and keep only what measurably helps.
