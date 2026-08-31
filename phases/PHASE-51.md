# PHASE 51 — Distributed Profiling & Bottleneck Analysis

| | |
|---|---|
| **Stage** | 8 — Performance Engineering |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 37, 45, 48 |
| **Blocks** | 52, 56 |
| **Risk** | 🟢 Low — diagnostic tooling; the risk is profiling overhead perturbing what it measures |
| **Blast radius** | None if correctly gated |
| **Architecture refs** | `ARCHITECTURE.md#l10-observability--control`, `ULTIMATE-PLAN.md#8`, Law VIII |

---

## 🎯 MISSION

Answer **"why is this slow?"** for any workload, in minutes rather than days. Deploy distributed profiling — PyTorch Profiler, Nsight Systems, Parca, and per-rank timing — behind a single command, and produce a **bottleneck taxonomy** that turns a vague complaint into a specific, actionable diagnosis.

> 💡 **WHY this phase exists separately from benchmarking.** Phase 48 tells you *that* something is slow relative to a baseline. Phase 50 tells you *when* it got slow. Neither tells you *why*. On a distributed system with GPUs, five storage tiers, a shared fabric, and a scheduler, the answer could be in any of a dozen places — and the single most common outcome in real clusters is a user concluding "the cluster is slow" when their data loader is single-threaded. **The tooling that closes that gap pays for itself in the first week.**

> ⚠️ **The profiler's observer effect is real and specific.** Nsight Systems tracing a 16-rank job produces gigabytes per rank and can slow the job enough to change its behavior; PyTorch Profiler with `record_shapes` and stack traces on can double step time. **Profiling must be opt-in, bounded, and never the default** — and every profiling result must record what overhead it carried.

---

## ✅ PREFLIGHT

```bash
# Observability stack with traces and profiles (Phase 45)
kubectl get pods -n observability -l app.kubernetes.io/name=parca
kubectl get pods -n observability -l app.kubernetes.io/name=tempo

# 📊 The baseline that "slow" is relative to
jq '.b09' benchmarks/baselines/baseline.json

# A GPU node with capacity for a profiling run
kubectl get nodes -l nexus.io/pool=training

# Storage for profile artifacts (they are large)
s5cmd ls s3://nexus-artifacts/profiles/ 2>/dev/null || echo "create it"
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/observability/profiling/
  nsight-job-template.yaml
  profiler-sidecar.yaml
  profile-storage-config.yaml
tools/profiling/
  profile.sh                        # 🎯 one command, any workload
  analyze.sh                        # 🎯 profile → bottleneck classification
  rank-timing.sh                    # per-rank timing: find the straggler
  timeline.sh                       # merged multi-rank timeline
  overhead-check.sh                 # ⚠️ how much did profiling cost?
docs/user/
  profiling-guide.md
  bottleneck-taxonomy.md            # 🎯 the diagnosis decision tree
images/profiling/
  Dockerfile                        # nsys, ncu, py-spy, torch profiler deps
evidence/phase-51/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Note |
|---|---|---|
| NVIDIA Nsight Systems | `2024.6.x` | System-wide timeline |
| NVIDIA Nsight Compute | `2024.3.x` | ⚠️ Kernel-level; very high overhead |
| PyTorch Profiler | Bundled with `2.6.0` | |
| py-spy | `0.4.x` | Python sampling, no code change |
| Parca | Phase 45 pin | Always-on, low overhead |
| HolisticTraceAnalysis | `0.3.x` | Multi-rank trace analysis |

---

## 📋 TASKS

### Task 1 — 🎯 The bottleneck taxonomy (the decision tree)

**`docs/user/bottleneck-taxonomy.md`** — the most valuable artifact of the phase. Every "it's slow" starts here.

```
START: A job is slower than expected.

Q1: What is SM_ACTIVE?  (⚠️ SM_ACTIVE, not GPU_UTIL — Phase 18)
├─ > 85 % ────────► GPU-BOUND. The GPU is genuinely working.
│                    → Is the work efficient? Nsight Compute on one kernel.
│                    → Are you using tensor cores? Mixed precision? torch.compile?
│                    → This is the GOOD case. Optimization is now in your model code.
│
├─ 40–85 % ───────► PARTIALLY STALLED. Go to Q2.
│
└─ < 40 % ────────► SEVERELY STALLED. Go to Q2.

Q2: Where is the time going? (PyTorch Profiler or Nsight Systems timeline)
├─ Waiting on DATA ──────────► ⚠️ THE MOST COMMON CAUSE
│    Symptoms: gaps before each forward pass; DataLoader threads at 100 % CPU
│    → num_workers ≈ 4 × GPUs?   pin_memory=True?  prefetch_factor≥2?
│    → Is the dataset on /scratch, or being read from S3 every epoch? (Phase 28)
│    → Millions of small files? (Phase 27's metadata numbers)
│    → Decoding JPEGs on CPU? Move to DALI/nvJPEG.
│
├─ Waiting on COMMUNICATION ─► Go to Q3.
│
├─ Waiting on the CPU ───────► Python overhead, or a synchronous host operation
│    → py-spy / Parca to find the hot Python
│    → .item(), .cpu(), print() in the training loop force a device sync
│    → CPU-side preprocessing that should be on GPU
│
├─ Waiting on STORAGE (checkpoint writes) ─► Phase 33's async sync not in use
│
└─ Kernel launch overhead ───► Many tiny kernels
     → CUDA graphs, torch.compile, larger batch

Q3: Communication-bound. Which kind?
├─ EXPOSED (not overlapped with compute)
│    → DDP: gradient_as_bucket_view, tune bucket_cap_mb
│    → FSDP: forward_prefetch, backward_prefetch=BACKWARD_PRE
│    → ⚠️ The wrong parallelism strategy (Phase 37's table)
├─ Overlapped but the fabric is saturated
│    → Compare against B5. If B5 is fine and you're slower, it's your pattern.
│    → Are your ranks in one leaf domain? (placement-report.sh)
│    → Is someone else's Spark shuffle competing? (Phase 38)
└─ ONE RANK is slow (a straggler) ─► rank-timing.sh
     → Thermal throttling? Degraded PCIe? (Phase 23 H7/H10)
     → Different GPU model in the job? (should be impossible — R-15)
     → NUMA misalignment on that node? (Phase 20's validator)
```

> 💡 **Publish this as a flowchart in the portal and reference it from every "my job is slow" support response.** It converts an open-ended investigation into a bounded one, and — critically — it teaches users to answer the first two questions themselves.

---

### Task 2 — One command to profile anything

**`tools/profiling/profile.sh`:**

```bash
nexus profile <job> --duration 60s --level basic
```

| Level | Tools | Overhead | Use when |
|---|---|---|---|
| **`--level basic`** | DCGM counters + rank timing + Parca | **< 2 %** | ✅ **Always start here.** Answers Q1 and often Q2. |
| `--level torch` | PyTorch Profiler, limited steps | 10–30 % | Q2/Q3 detail |
| `--level nsys` | Nsight Systems, short window | 20–50 % | Full timeline, kernel + NCCL + CPU |
| `--level ncu` | Nsight Compute, one kernel | ⚠️ **10–100×** | Only for a specific kernel, on one rank |

> ⚠️ **`--level ncu` must never run on more than one rank, and never on a production job.** Nsight Compute serializes kernel execution to collect hardware counters; a distributed job under `ncu` will hit collective timeouts and fail. Enforce a rank limit in the tool.

> 💡 **`--level basic` answering the question is the goal.** Most performance complaints are resolved by "your SM_ACTIVE is 31 % and your data loader threads are pegged." That requires no invasive profiling at all — just the always-on signals from Phase 45, presented well.

**Profiling on a *running* job** without restarting it:
```
py-spy dump --pid <pid>            # Python stack, zero setup, no restart
py-spy top --pid <pid>             # live sampling
nsys profile --attach <pid>        # attach to a running process
```
This matters because the user's complaint usually arrives mid-run and restarting to profile loses hours of work.

---

### Task 3 — Multi-rank analysis (the part single-node tools do not do)

A distributed job's bottleneck is often the *relationship* between ranks, not any single rank.

**`tools/profiling/rank-timing.sh`** — the straggler finder:
```
STEP TIME PER RANK (last 100 steps)
  rank  0: 0.341 s  ████████████████░  median
  rank  1: 0.339 s  ████████████████░
  ...
  rank 11: 0.338 s  ████████████████░
  rank 12: 0.412 s  ███████████████████░  ⚠️ +21 %  ← THE STRAGGLER
  rank 13: 0.340 s  ████████████████░
  ...
  ⚠️ EVERY rank waits for rank 12 at each collective.
     The job runs at rank 12's speed: effective throughput -21 %.

  rank 12 → nexus-gpu-041, GPU 2
     GPU temp:        87 °C  (cohort median 71 °C)  ⚠️
     Throttle reason: SW_THERMAL_SLOWDOWN            ⚠️ FOUND IT
     → Phase 23 H7. Check airflow / fan on this node.
```

> 💡 **Straggler analysis is the highest-value multi-rank tool and the least commonly available.** In a synchronizing job, one slow rank sets the pace for all of them — so a single thermally-throttled GPU silently costs 21 % of a 16-GPU job, and no single-node profiler will ever show it. **Correlating per-rank timing with DCGM state is what turns that into a two-minute diagnosis.**

**`tools/profiling/timeline.sh`** — merge per-rank traces into one timeline (HolisticTraceAnalysis or a custom merge), showing:
- Where ranks diverge and re-synchronize
- Total time spent in collectives vs. compute, per rank
- **Idle time attributable to waiting for other ranks** — the load-imbalance number

---

### Task 4 — ⚠️ Overhead measurement and safety

**`tools/profiling/overhead-check.sh`** — every profiling run reports its own cost.

```
Profile run complete.
  Baseline step time (10 steps before profiling): 0.341 s
  Step time during profiling:                     0.398 s
  ⚠️ PROFILING OVERHEAD: 16.7 %
  Conclusions about absolute timing should account for this.
  Relative proportions (data vs. compute vs. comm) remain valid.
```

**Safety guards:**

| Guard | Rule |
|---|---|
| Profiling is **opt-in**, never default | |
| **Duration is bounded** (default 60 s, max 10 min) | An unbounded nsys trace fills the disk |
| Artifacts go to `/scratch`, then upload to S3 | ⚠️ Never the container overlay FS |
| **Artifact size cap** | nsys can produce GB per rank; cap and warn |
| `--level ncu` limited to 1 rank | A2's rule |
| Profiling a production inference service requires approval | Latency impact is user-visible |
| Auto-cleanup of profile artifacts after 14 days | Phase 45's retention |

> ⚠️ **Nsight Systems output size surprises people.** A 60-second trace of a 16-rank job can exceed 20 GB. Without a cap, it fills `/scratch`, evicts the pod (Phase 25's `sizeLimit`), and the user loses both the profile and the job. **Cap it, warn early, and default to a short window.**

---

### Task 5 — The user guide

**`docs/user/profiling-guide.md`**

````markdown
# Why is my job slow?

## Step 1 — Look before you profile (30 seconds, no overhead)
    nexus profile <job> --level basic
    → SM_ACTIVE: 34 %   Data wait: 61 %   Comm: 4 %   Compute: 34 %
      ⚠️ You are DATA-BOUND. See: data-loading-guide.md
This resolves most cases. Do not skip it.

## Step 2 — If you need detail
    nexus profile <job> --level torch --duration 60s
Produces a trace you can open in chrome://tracing or TensorBoard.

## Step 3 — Read the timeline
Look for GAPS. A gap before every forward pass = data loading.
Gaps during backward = communication not overlapped.
No gaps but slow = your kernels; go to --level ncu on ONE rank.

## The five things that cause 80 % of slow jobs here
1. Data loader too slow          → num_workers = 4 × GPUs, data on /scratch
2. Dataset read from S3/CephFS every epoch → stage it once (Phase 28)
3. A straggler rank              → `nexus profile --level basic` shows per-rank timing
4. Wrong parallelism strategy    → parallelism-strategies.md
5. `.item()` or `print()` in the training loop → forces a GPU sync every step

## ⚠️ Profiling changes what you measure
Every profile reports its overhead. Trust the PROPORTIONS
(60 % data, 30 % compute, 10 % comm), not the absolute times.

## Comparing against the cluster's capability
Your job's collective throughput vs. the cluster's B5 baseline:
    nexus profile <job> --compare-baseline
If you're at 40 % of B5, the problem is your pattern, not the fabric.
````

---

### Task 6 — Continuous profiling and the fleet view

Parca (Phase 45) runs always-on. Use it for what per-job profiling cannot see:

| Question | Answered by |
|---|---|
| Which workload is burning the most CPU cluster-wide? | Parca, fleet view |
| Did this platform component regress after an upgrade? | Parca, time comparison |
| Where does a *platform* component spend its time? | Parca (no code change needed) |
| Is a specific job slow? | Per-job profiling (Tasks 2–3) |

> 💡 **Parca's ability to profile a process that is already running, with no restart and no instrumentation, is what makes it useful during an incident.** When Cilium's agent starts eating a core at 3 a.m., there is no opportunity to add a profiler — but Parca already has the last 14 days of stacks.

📊 **Add a fleet-level view to the portal:** the top 10 CPU consumers cluster-wide, and the top 10 jobs by *wasted* GPU-hours (allocated but low SM_ACTIVE, from Phase 34). The second list is a work queue for platform-team outreach: each entry is a user who would benefit from a five-minute conversation about their data loader.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | `nexus profile --level basic` works on any running job | Test on 3 job types | Works |
| **A2** | 📊 **`--level basic` overhead < 2 %** | Measure | Met |
| **A3** | Basic level reports SM_ACTIVE, data wait, comm, compute split | Run | Reported |
| **A4** | `--level torch` produces a loadable trace | Open in TensorBoard | Loads |
| **A5** | `--level nsys` produces a loadable timeline | Open in Nsight | Loads |
| **A6** | **`--level ncu` refuses to run on > 1 rank** | 🧪 Try | Refused |
| **A7** | `--level ncu` refuses on a production inference service | 🧪 Try | Refused |
| **A8** | Profiling can attach to a running job without restart | 🧪 Test | Attaches |
| **A9** | 📊 **Every profile reports its own overhead** | Run each level | Reported |
| **A10** | Duration bounded; default 60 s, max enforced | 🧪 Try 1 h | Capped |
| **A11** | Artifacts written to `/scratch`, then uploaded | Inspect | Correct |
| **A12** | **Artifact size cap enforced with an early warning** | 🧪 Large trace | Capped |
| **A13** | Artifacts auto-cleaned after 14 days | Verify policy | Set |
| **A14** | 🎯 **`rank-timing.sh` identifies an injected straggler** | 🧪 Throttle one GPU | Identified |
| **A15** | 🎯 **Straggler output correlates with DCGM state (temp, throttle)** | Same test | Correlated |
| **A16** | `timeline.sh` merges multi-rank traces | Test 8 ranks | Merged |
| **A17** | Load-imbalance time quantified | Inspect output | Quantified |
| **A18** | `--compare-baseline` compares a job's collectives to B5 | Test | Compares |
| **A19** | Bottleneck taxonomy published as a flowchart | Portal | Published |
| **A20** | 🧪 **The taxonomy correctly diagnoses 5 synthetic bottlenecks** | Inject: slow loader, straggler, bad strategy, sync-in-loop, tiny kernels | 5/5 |
| **A21** | Parca fleet view shows top CPU consumers | Dashboard | Shows |
| **A22** | 📊 Top-10 wasted-GPU-hours list generated | Dashboard | Generated |
| **A23** | Profiling guide published and accurate | Review | Accurate |

---

## ↩️ ROLLBACK

```bash
# Profiling is opt-in and diagnostic — removing it loses capability, breaks nothing
kubectl delete -f clusters/nexus-prod/observability/profiling/

# Parca can be disabled independently if its overhead becomes a concern
kubectl scale ds/parca-agent -n observability --replicas=0
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Profiling makes the job fail | Overhead caused a collective timeout | Shorter duration; lower level; raise the NCCL timeout for the run |
| nsys output is huge | Tracing everything | Restrict with `--trace=cuda,nvtx,osrt`; shorten the window |
| `/scratch` fills during profiling | No size cap | A12 |
| Cannot attach to a running process | Missing `SYS_PTRACE` capability | Add it to the profiling sidecar (with an ADR — Phase 04) |
| Profile shows no GPU activity | Wrong PID, or the profiler attached to the launcher | Attach to the worker process |
| Per-rank timing unavailable | The framework does not emit step timing | Use the profiler's step markers, or NVTX ranges |
| Straggler identified but no obvious cause | Check NUMA alignment, PCIe width, co-tenant on the node | Phase 20 validator; Phase 23 H10 |
| Everything looks fine but the job is slow | Compare against B5/B9 — maybe it is not slow, expectations are wrong | A18 |
| Parca overhead noticeable on GPU nodes | Sampling frequency too high | Reduce; or exclude the GPU pool (Phase 45 A20) |

---

## 🚫 DO NOT

- **Do not** make profiling the default for any workload.
- **Do not** run Nsight Compute on more than one rank.
- **Do not** profile a production inference service without approval.
- **Do not** run an unbounded trace.
- **Do not** write profile artifacts to the container overlay filesystem.
- **Do not** quote absolute times from a profiled run without its overhead figure.
- **Do not** skip `--level basic` before reaching for heavier tools.
- **Do not** conclude "the cluster is slow" before comparing against the baselines.

---

## 📤 HANDOFF

`evidence/phase-51/handoff.md` must state:

1. **🎯 The bottleneck taxonomy** as published, and the 5/5 synthetic-diagnosis result.
2. **📊 Measured overhead per profiling level.**
3. **🧪 The straggler-detection result** — the injected case and how quickly it was found.
4. **What `--level basic` can and cannot answer** — the boundary that determines how often heavier tools are needed.
5. **The artifact size and retention policy.**
6. **📊 The top-10 wasted-GPU-hours list** — the outreach work queue, feeding Phase 34's idle bucket.
7. **Any workload type that cannot be profiled** with the current tooling.
8. **Common bottlenecks found** while validating the tooling — these become documentation.

---

## ➡️ NEXT

**[PHASE-52 — Scale-Out Validation to 100+ Nodes (G11)](PHASE-52.md)** — prove the whole platform holds at the scale it was designed for, and close Stage 8.
