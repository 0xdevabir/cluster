# PHASE 19B — Campus GPU Harvesting

| | |
|---|---|
| **Stage** | C — Campus Harvest Plane |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 19, 14B, 31B |
| **Blocks** | 36B, 52B |
| **Risk** | 🟠 R-27 (heterogeneity fragments pools), R-28 (thermal/hardware life), R-03/licensing |
| **Blast radius** | Lab hardware condition and room temperature |
| **Architecture refs** | `ULTIMATE-PLAN.md#42-the-consumer-gpu-reality-table`, `#archetype-f--harvest`, `CAMPUS-FABRIC.md#51-the-honest-capability-table`, `#9.3` |

---

## 🎯 MISSION

Make the campus fleet's **wildly heterogeneous GPUs** — integrated, GTX 16xx, RTX 20/30/40-series, and none at all — into usable, correctly-labeled, fractionally-shareable pools, running at power and thermal settings that respect hardware we do not own.

> 💡 **WHY heterogeneity is mostly a non-problem here.** `ULTIMATE-PLAN.md` R-15 warns that mixed GPUs cause stragglers in collectives — and it is right, for Plane A. On the harvest plane the dominant workload is *independent trials*, where a slow GPU simply finishes its own trial more slowly and delays nobody. Heterogeneity only bites where ranks must synchronize, and the only synchronizing workload we permit here (same-lab Local-SGD, 36B) is restricted to homogeneous placement groups. **Do not over-engineer for a problem the workload mix does not have.**

> ⚠️ **The licensing question gates scale-out, not experimentation.** Per 04B Task 7, discrete-GPU harvesting at scale waits on the institutional determination. CPU-only and iGPU work is unaffected and proceeds now. Check the determination's status before enrolling discrete GPUs across many labs.

---

## ✅ PREFLIGHT

```bash
# 1. GPU inventory from the survey, with per-model counts
yq -r '.[] | select(.gpu != null) | [.lab, .gpu[].model, .gpu[].vramMiB] | @tsv' \
  inventory/campus/machines.yaml | sort | uniq -c

# 2. Ventilation verdicts — R-28 gate
yq -r '.[] | [.id, .power.ventilation_verdict] | @tsv' inventory/campus/labs.yaml

# 3. Licensing determination status recorded
grep -iE 'status:' docs/campus/licensing-determination.md

# 4. Phase 19 complete — DRA driver, DeviceClasses, sharing modes exist for Plane A
kubectl get deviceclasses.resource.k8s.io

# 5. Eviction path working — GPU release must be part of the yield (31B)
grep -i 'GPU released' evidence/phase-31B/handoff.md
```

**If any lab in scope has `ventilation_verdict: inadequate`, GPU work is BLOCKED for that lab.** Its CPU capacity remains available. Record it; do not negotiate with a hot room.

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/acceleration/campus/
  harvest-gpu-labels.yaml           # per-model pool labels from NFD + the survey
  harvest-deviceclasses.yaml        # DRA DeviceClasses per GPU model family
  harvest-sharing.yaml              # MPS / time-slicing config per model
  power-thermal-caps.yaml           # DaemonSet applying per-model caps
  gpu-exclusions.yaml               # models and labs excluded, with reasons
tools/campus/
  gpu-census.sh                     # what GPUs exist, where, in what quantity
  thermal-watch.sh                  # per-room temperature trend under load
  gpu-suitability.sh                # is this GPU worth harvesting at all?
docs/campus/
  gpu-pools.md                      # the pools, their capability flags, what runs where
  thermal-report.md                 # per-room evidence for promise 4
evidence/phase-19B/
  preflight.md plan.md acceptance.md handoff.md deviations.md
  thermal/                          # per-room load-test temperature traces
```

---

## 📋 TASKS

### Task 1 — Census and pool formation

Group by **capability, not by name**. A pool is a set of GPUs a workload can treat as interchangeable.

```yaml
# Illustrative pools from a typical campus census
pools:
  - id: gpu-ampere-8g          # RTX 3050/3060 Ti class, 8 GB
    vramMiB: 8192
    computeCapability: "8.6"
    fp8: false
    count: 34
  - id: gpu-turing-4g          # GTX 1650 class, 4 GB
    vramMiB: 4096
    computeCapability: "7.5"
    count: 41
    note: "4 GB is below the practical floor for most modern model work — see Task 2"
  - id: gpu-ada-12g
    vramMiB: 12288
    computeCapability: "8.9"
    fp8: true
    count: 9
  - id: igpu                   # Intel UHD / AMD Vega integrated
    usable: cpu-assist-only
    count: 118
  - id: none
    count: 58                  # CPU-only nodes — a large and genuinely useful pool
```

Label nodes with pool id, VRAM, compute capability, and FP8 support. **Workloads request a pool, not a model** — this is what keeps the fleet usable as machines are replaced over the years.

### Task 2 — Decide what is worth harvesting

Not every GPU earns its electricity. **`tools/campus/gpu-suitability.sh`** applies an explicit test:

| GPU class | Verdict | Reasoning |
|---|---|---|
| ≥ 8 GB VRAM, CC ≥ 7.5 | ✅ Harvest | Genuinely useful for trials, batch inference, small-model training |
| 4–6 GB VRAM, CC ≥ 7.5 | ⚠️ Harvest for inference/CV only | Below the practical floor for modern LLM work (`ULTIMATE-PLAN.md §4.2`); excellent for classical CV, small models, preprocessing |
| < 4 GB VRAM | ❌ Do not harvest the GPU | Harvest the node's CPU instead. The GPU's marginal value does not justify the driver, power, and thermal cost |
| Integrated GPU | ❌ Not as a GPU resource | Harvest the CPU. iGPUs share system RAM and are not worth the scheduling complexity |
| CC < 7.0 (pre-Turing) | ❌ Do not harvest | Modern container images increasingly do not build for it; the maintenance cost exceeds the value |

> 💡 **A node whose GPU is not worth harvesting is still worth harvesting.** The CPU-only pool at DIU scale is on the order of 200,000 core-hours per week — that is a large, useful resource for preprocessing, ETL, CI, simulation, and classical ML. Do not let a disappointing GPU census read as a disappointing fleet.

Record every exclusion in `gpu-exclusions.yaml` with its reason. The census will be re-run as labs refresh hardware, and next year's engineer needs to know why the GTX 1050s were skipped.

### Task 3 — Fractional sharing on harvest nodes

Per `ULTIMATE-PLAN.md §4.2`, consumer GPUs have no MIG. Sharing is MPS or time-slicing, via DRA (Phase 19's machinery, specialized here):

| Mode | Use on harvest nodes | Caution |
|---|---|---|
| **Time-slicing** | ✅ Default for Bronze/Silver inference and short trials | No memory isolation — a workload that OOMs takes its co-tenants with it. Cap replicas by VRAM. |
| **MPS** | ✅ For concurrent small inference | Same VRAM caveat; MPS daemon must be torn down cleanly in the eviction path |
| **Exclusive** | ✅ Default for Gold-tier training | Simplest, and on a 4–8 GB card usually correct anyway |
| MIG | ❌ Unavailable | Consumer hardware |

**VRAM admission control is mandatory.** With no hardware partitioning, the only thing preventing an OOM cascade is refusing to admit more than the card can hold. Workloads declare VRAM; the sum of admitted claims never exceeds the card's capacity minus a 10 % headroom.

> ⚠️ **The eviction path must release the GPU cleanly.** An MPS daemon or a leaked CUDA context that survives teardown means the next boot — or the student's session — inherits a GPU in a bad state. Verify GPU release explicitly in the 31B trace (acceptance criterion 4).

### Task 4 — Power and thermal caps (promise 4)

We are running someone else's hardware, in a room that may have one air conditioner, at night, unattended. **Cap at the efficiency knee, not the frequency peak** (`ULTIMATE-PLAN.md §4.5`, Law I's cousin: the last 8 % of performance costs 30 % of the power and most of the heat).

```bash
# Applied per GPU model by DaemonSet at node join; illustrative values.
nvidia-smi -pl <efficiency_knee_watts>     # typically 70–80 % of the card's default TDP
nvidia-smi -pm 1                           # persistence mode
# Thermal ceiling BELOW the vendor's throttle point, so we never run the fans at maximum
nvidia-smi -gtt 78                         # target temp; card downclocks rather than screaming
```

**Fan noise is a real constraint.** A lab of 30 GPUs at full fan speed is loud enough to be noticed from the corridor, and an unexplained noise at 10pm generates exactly the kind of attention this project does not want. The thermal target is set for quiet, not for peak throughput.

### Task 5 — Room thermal validation

**`tools/campus/thermal-watch.sh`** — for each GPU-enabled lab, run a **sustained 2-hour load test** on a full room and record:

| Measurement | Threshold | Action if exceeded |
|---|---|---|
| Room ambient rise | ≤ +6 °C over 2 h | Reduce concurrency, or downgrade the lab to CPU-only |
| GPU temperature p95 | ≤ 78 °C | Lower the power cap |
| Any thermal throttle event | 0 preferred, < 2 % of samples tolerated | Lower the cap; investigate airflow |
| Fan noise (subjective, from the corridor) | Not noticeable | Lower the cap |

Commit the traces to `evidence/phase-19B/thermal/`. This is the evidence behind promise 4, and it is what you show a lab owner who asks whether this is damaging their equipment.

> 💡 Run the first thermal test with the lab technician present. Someone who has watched the test and seen the numbers becomes an advocate; someone who hears about it secondhand becomes a sceptic.

### Task 6 — Homogeneous placement groups

For the one synchronizing workload class permitted on this plane (same-lab Local-SGD, 36B), enforce what R-15 requires: **all ranks on identical GPUs, in one lab.** Kueue TAS with `nexus.io/lab` as the tightest domain (03B) plus a pool-id equality constraint.

For every other harvest workload class, **explicitly do not enforce homogeneity** — it would fragment the pools for no benefit.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Command |
|---|---|---|
| 1 | Every harvest node with a usable GPU carries pool, VRAM, and compute-capability labels | `kubectl get nodes -l nexus.io/plane=harvest -L nexus.io/gpu-pool` |
| 2 | GPUs failing the suitability test are excluded, with reasons recorded; their nodes remain in the CPU pool | `gpu-suitability.sh --report` + node listing |
| 3 | VRAM admission control refuses an over-subscription | deliberate over-request, capture the rejection |
| 4 | GPU is fully released within the 31B eviction budget; no leaked MPS daemon or CUDA context | `nvidia-smi` post-eviction, 20 samples |
| 5 | Power caps applied at node join and verified | `nvidia-smi -q -d POWER` across the fleet |
| 6 | 2-hour room thermal test passes every threshold in Task 5, per GPU lab | traces in `evidence/phase-19B/thermal/` |
| 7 | No GPU work is scheduled in a lab with `ventilation_verdict: inadequate` | scheduling policy test |
| 8 | Homogeneous placement enforced for synchronizing workloads only | placement test, both positive and negative |
| 9 | Licensing determination status is current and recorded before any multi-lab discrete-GPU enrolment | manual review |

---

## ↩️ ROLLBACK

Label all harvest GPUs unschedulable; the fleet reverts to a CPU-only harvest fabric. **This loses less than it sounds like** — the CPU pool is the larger resource by core-hours, and every embarrassingly-parallel workload class still runs. Use this immediately if a thermal or licensing question arises, rather than debating it while the GPUs are hot.

---

## 🧯 TROUBLESHOOTING

| Symptom | Cause | Action |
|---|---|---|
| Room temperature climbing past +6 °C | Concurrency too high for the room's cooling | Reduce concurrency first, then the power cap. Do not wait for a complaint. |
| Fans audible from the corridor at night | Thermal target too high | Lower `-gtt`. Quiet is a feature here, not a compromise. |
| OOM cascade on a time-sliced GPU | VRAM admission not enforced, or a workload under-declared | Enforce declared VRAM; move that workload to exclusive mode. |
| Driver version mismatch across labs | Different GPU generations need different branches | Pin per pool, not fleet-wide. Record in `gpu-pools.md`. |
| GPU in a bad state after eviction | MPS/context not torn down | Fix the 31B teardown. This one is user-visible — a student may find a broken GPU. |
| Very few usable GPUs found | Realistic outcome at many universities | Report it plainly and lead with the CPU pool's numbers. The fleet's value was never mostly GPU. |
| Pool fragmentation stalls scheduling | Over-strict homogeneity constraints | Confirm homogeneity is enforced only for synchronizing workloads. |

---

## 🚫 DO NOT

- Do not run GPU work in a lab with inadequate ventilation.
- Do not run GPUs at stock power limits on machines you do not own.
- Do not enforce homogeneous placement for independent workloads.
- Do not harvest GPUs below the suitability threshold — take the CPU and move on.
- Do not enrol discrete GPUs across many labs while the licensing determination is unresolved.
- Do not skip the thermal validation because the room "feels fine" during a 10-minute test.
- Do not attempt MIG, vGPU, or P2P workarounds on consumer cards (`ULTIMATE-PLAN.md §4.2`).

---

## 🤝 HANDOFF — write `evidence/phase-19B/handoff.md`

Must state:

- The GPU census: pools, counts, and total usable GPU-hours per week — with excluded models and reasons.
- Per-lab thermal results and the concurrency limit each room can sustain.
- The power cap in force per model, and the measured performance cost of capping (Law VIII: a number, not an assertion).
- Which labs are CPU-only and why.
- Licensing determination status at the time of the phase.
- The sharing mode default per pool, for 36B's templates to target.
