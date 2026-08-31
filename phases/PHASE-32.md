# PHASE 32 — Power-Aware Scheduling & Thermal Management

| | |
|---|---|
| **Stage** | 5 — Scheduling & Orchestration |
| **Estimated effort** | 3–4 hours |
| **Depends on** | 02, 18, 23, 31 |
| **Blocks** | 34, 35, 52 |
| **Risk** | 🔴 High — the failure mode is a tripped breaker taking a rack offline |
| **Blast radius** | A rack, or the whole facility |
| **Architecture refs** | `ULTIMATE-PLAN.md#45-power-and-thermal-reality`, `ARCHITECTURE.md#l0-facility`, `#x2` (F1–F3) |

---

## 🎯 MISSION

Treat **electrical capacity as a first-class scheduling resource**. Enforce a per-rack and per-facility power budget that the scheduler respects, cap GPU power at the efficiency knee, respond to thermal events before they become throttling, and make it **impossible for a scheduling decision to trip a breaker**.

> 💡 **WHY this phase has no cloud equivalent.** AWS does not let you overload a rack; the constraint is invisible because someone else designed around it. You own the building. A 12-node GPU rack at full tilt draws 8.4 kW; a 20 A single-phase circuit delivers 3.8 kW continuous under NEC's 80 % derate. **The scheduler does not know this.** Left alone, Kueue will happily admit jobs that light up every GPU in a rack simultaneously and trip the feed — taking down not just those jobs but the storage OSDs and possibly a control-plane node in the same rack.

> ⚠️ **The compounding failure.** A breaker trip is not a clean shutdown: nodes lose power mid-write, Ceph OSDs come back needing recovery, consumer NVMe without PLP may have lost acknowledged writes (Phase 24 §4.7), and the recovery storm on restart draws *more* power via inrush. Power events cascade. This phase is about never having one.

---

## ✅ PREFLIGHT

```bash
# Phase 02 electrical model
cat facility/design/power-model.md
yq '.circuits' facility/pdu-map.yaml

# PDU telemetry flowing (Phase 11 SNMP scrape)
promtool query instant 'pdu_outlet_power_watts' | head

# GPU power caps applied (Phase 18)
kubectl get ds -n gpu-operator power-cap-daemonset

# Node → rack → circuit mapping complete
kubectl get nodes -L topology.kubernetes.io/rack,nexus.io/power.circuit
```

---

## 📦 DELIVERABLES

```
facility/power/
  power-budget.yaml                 # 🎯 per circuit, rack, facility
  node-power-profiles.yaml          # measured draw per archetype and state
clusters/nexus-prod/scheduling/power/
  power-capacity-exporter.yaml      # publishes power headroom as a metric
  power-admission-check.yaml        # Kueue AdmissionCheck gating on headroom
  thermal-response-controller.yaml
  power-cap-tiers.yaml              # normal / constrained / emergency
tools/power/
  measure-node-power.sh             # 📊 characterize real draw
  power-report.sh                   # live headroom per circuit
  emergency-shed.sh                 # ⚠️ load shedding, human-triggered
docs/operations/power-runbook.md
observability/rules/power-alerts.yaml
evidence/phase-32/{preflight,acceptance,handoff,deviations}.md
```

---

## 📋 TASKS

### Task 1 — 📊 Characterize real power draw (measure, do not estimate)

Nameplate ratings are useless. Measure.

**`tools/power/measure-node-power.sh`** — per node archetype, record PDU-measured draw in each state:

| State | Duration | What it tells you |
|---|---|---|
| Powered off (soft) | 1 min | Standby draw — real, and it adds up over 100 nodes |
| Idle, booted | 5 min | The floor |
| CPU stress, GPU idle | 5 min | CPU contribution |
| **GPU 100 % (all GPUs), CPU idle** | 10 min | **The number that matters** |
| **GPU + CPU + NVMe + NIC all loaded** | 10 min | **True peak — use this for budgeting** |
| Boot inrush | Capture peak | ⚠️ Can be 2–3× steady state for seconds |

**The table you produce:**

| Archetype | Idle W | Peak W | Boot inrush W | Nodes per 20 A circuit |
|---|---|---|---|---|
| A: 4×4090 GPU node | 180 | 1,850 | ~2,600 | **2** (3,700 W of 3,840 W) |
| B: 2×4090 GPU node | 140 | 1,000 | ~1,500 | 3 |
| C: CPU node | 90 | 420 | 600 | 9 |
| D: Storage node | 120 | 380 | 700 | 10 |
| E: Control plane | 60 | 180 | 300 | 21 |

> ⚠️ **Boot inrush is the constraint people miss.** Ten nodes booting simultaneously can draw 2–3× their steady-state peak for several seconds — enough to trip a breaker that handles their running load fine. Phase 11 already staggers provisioning for this reason; here you formalize it as a rule: **never power on more than N nodes per circuit within a 30-second window**, where N comes from the inrush measurement.

> 💡 **Measure with the GPU power cap you actually deploy**, not uncapped. Phase 02's analysis found ~320 W as the efficiency knee for a 450 W 4090 — 1.29× better perf/W for ~7 % less throughput. If you run capped, budget capped.

---

### Task 2 — The power budget model

**`facility/power/power-budget.yaml`:**

```yaml
facility:
  service_amps: 400
  service_volts: 400            # 3-phase
  derate: 0.80                  # NEC continuous-load rule
  usable_watts: 221760          # 400 × 400 × √3 × 0.8
  reserved_watts: 20000         # cooling, lighting, network, headroom
  budget_watts: 201760

circuits:
  - id: PDU-1A-C1
    rack: R1
    amps: 20
    volts: 230
    derate: 0.80
    budget_watts: 3680          # 20 × 230 × 0.8
    nodes: [nexus-gpu-001, nexus-gpu-002]
    committed_watts: 3700       # ⚠️ Σ peak of assigned nodes — OVER BUDGET
    policy: "cap"               # cap | limit-concurrency | move-a-node
```

**Three ways to resolve an over-committed circuit — pick per circuit:**

| Policy | Mechanism | Trade |
|---|---|---|
| **`cap`** | Lower the GPU power cap on those nodes until Σpeak ≤ budget | ✅ Simple, always safe; costs a few % throughput |
| **`limit-concurrency`** | Scheduler admits work to at most K of the circuit's nodes at full power | Complex; better peak throughput |
| **`move-a-node`** | Physically rebalance | ✅ Best if you have spare circuits |

> 💡 **Default to `cap`.** It is enforced at the hardware level by `nvidia-smi -pl`, needs no scheduler cooperation, and cannot be defeated by a workload. Concurrency limiting is only safe if *every* path to running a GPU goes through the scheduler — and in practice, a debug pod or a DaemonSet eventually will not.

**The layered defense (each layer independently sufficient):**

| Layer | Mechanism | Fails how? |
|---|---|---|
| 1. Hardware cap | `nvidia-smi -pl` on every GPU | Cannot be exceeded by software |
| 2. Circuit budget | Σ capped peak ≤ circuit budget | Arithmetic, verified in CI |
| 3. Scheduler headroom | AdmissionCheck blocks admission when a circuit is near budget | Advisory; catches unusual draw |
| 4. Thermal response | Reduce caps when inlet temps rise | Protects against cooling failure |
| 5. PDU limits | Per-outlet high-current alarms on the PDU itself | Last-resort alerting |
| 6. Emergency shed | Human-triggered, ordered shutdown | Manual |

> ⚠️ **Layer 1 must be sufficient on its own.** Layers 2–6 are defense in depth, but a power design that only works when the scheduler cooperates is not a power design. Set the caps so that **every node in a rack at 100 % simultaneously stays under budget**, then treat layers 3–5 as monitoring.

---

### Task 3 — Publishing power headroom to the scheduler

**`power-capacity-exporter.yaml`** — a small controller that:
1. Reads PDU telemetry (Phase 11 SNMP).
2. Computes per-circuit and per-rack headroom: `budget − current_draw`.
3. Computes *committed* headroom: `budget − Σ(peak of nodes with running GPU workloads)`.
4. Exports as Prometheus metrics and as node labels/annotations.

```
nexus_power_circuit_budget_watts{circuit="PDU-1A-C1"}
nexus_power_circuit_draw_watts{circuit="PDU-1A-C1"}
nexus_power_circuit_committed_watts{circuit="PDU-1A-C1"}
nexus_power_circuit_headroom_ratio{circuit="PDU-1A-C1"}
nexus_power_rack_headroom_ratio{rack="R1"}
nexus_power_facility_headroom_ratio{}
```

**`power-admission-check.yaml`** — a Kueue AdmissionCheck:
```
Before admitting a workload, verify:
  · The facility headroom after admission stays ≥ 10 %
  · No circuit the workload would land on exceeds 90 % committed
If not → hold the workload in the queue with a clear reason:
  "Held: rack R1 at 94 % power commitment. Waiting for capacity."
```

> 💡 **A clear "why" message matters more here than anywhere else**, because "waiting for power" is not a reason users expect. If `describe workload` says "insufficient quota" when the real reason is electrical, you will spend the next year explaining it.

---

### Task 4 — Thermal response

Power in equals heat out. Phase 02's cooling analysis is the other half of this.

**`thermal-response-controller.yaml`** — a three-tier response:

| Tier | Trigger | Response | Reversible |
|---|---|---|---|
| **Normal** | Inlet < 27 °C, GPU < 80 °C | Standard power cap | — |
| **Constrained** | Inlet 27–32 °C, or GPU > 83 °C sustained | Reduce GPU caps by 15 %; stop admitting new GPU work to affected racks | ✅ Auto-restores |
| **Emergency** | Inlet > 35 °C, or GPU > 90 °C, or cooling failure detected | Cap to minimum; drain non-critical GPU workloads; 🚨 page | Manual |

**Detection inputs:**
- DCGM `DCGM_FI_DEV_GPU_TEMP`, `DCGM_FI_DEV_THERMAL_VIOLATION` (Phase 23 H7/H8)
- Rack inlet/outlet temperature sensors (if fitted — if not, that is a Phase 02 gap to record)
- PDU temperature sensors where available
- **Rate of change** — a 5 °C rise in 10 minutes means cooling has failed, regardless of absolute value

> ⚠️ **Alert on the derivative, not just the threshold.** By the time the absolute temperature is critical, you have minutes. A rapid rise detected at 24 °C gives you time to act. This is the single most useful thermal alert.

> 🚫 **Never let the automation power off a node for thermal reasons without the Phase 23 guards** (rate limit, floor, storm detection). A cooling failure is inherently correlated — it will trip the storm guard, which is correct: this is a facility event that needs a human, not 40 independent node remediations.

---

### Task 5 — GPU power capping tiers

Extend Phase 18's power-cap DaemonSet to be dynamic:

```yaml
# power-cap-tiers.yaml
tiers:
  performance:   { watts: 450, when: "manual override only" }
  normal:        { watts: 320, when: "default — the efficiency knee" }
  constrained:   { watts: 270, when: "thermal tier 2 or circuit > 90%" }
  emergency:     { watts: 200, when: "thermal tier 3" }
  minimum:       { watts: 150, when: "load shed" }
```

📊 **Measure the throughput cost of each tier** so the trade is known, not guessed:

| Cap | Watts | Relative training throughput | Perf/W |
|---|---|---|---|
| 450 W | 100 % | 100 % | 1.00 |
| **320 W** | 71 % | ~93 % | **1.29** ✅ |
| 270 W | 60 % | ~86 % | 1.43 |
| 200 W | 44 % | ~68 % | 1.53 |

> 💡 **The 320 W knee is where this cluster should live by default.** You lose ~7 % throughput and gain 29 % more compute per watt — which, in a power-constrained facility, means you can run *more GPUs*. When power is the binding constraint, perf/W is the metric, not perf.

---

### Task 6 — Emergency load shedding

**`tools/power/emergency-shed.sh`** — human-triggered, never automatic.

**The shed order (write it down before you need it):**
```
1. Preemptible / low-priority batch workloads        → evict immediately
2. Interactive notebooks idle > 15 min               → evict
3. Normal-priority training                          → checkpoint (Phase 33), then evict
4. Cap all remaining GPUs to minimum                 → 150 W
5. High-priority training                            → checkpoint and evict
6. Inference services                                → scale down replicas, keep 1
7. ⚠️ NEVER shed: control plane, etcd, storage OSDs, monitoring
```

> ⚠️ **Step 7 is the rule that prevents a power incident becoming a data incident.** Shedding storage nodes to save power means Ceph loses OSDs and starts a recovery storm — which draws more power. Shed compute; protect state.

**Guards:** typed confirmation, a dry-run mode showing exactly what would be evicted and how many watts it saves, and an audit record.

---

### Task 7 — Alerts

| Alert | Threshold | Severity |
|---|---|---|
| `CircuitNearLimit` | Draw > 85 % of budget | 🟠 High |
| `CircuitOverLimit` | Draw > 95 % | 🔴 Page |
| `CircuitCommittedOverBudget` | Σ peak > budget | 🔴 **Design error** — fix the model |
| `RackPowerHigh` / `FacilityPowerHigh` | > 85 % | 🟠 |
| `PowerHeadroomLow` | Facility < 10 % | 🟠 |
| `InletTempHigh` / `InletTempRising` | > 30 °C / +5 °C in 10 min | 🟠 / 🔴 |
| `GPUThermalThrottling` | Sustained (Phase 23 H7) | 🟡 |
| `CoolingFailureSuspected` | Rapid rise across a rack | 🔴 Page |
| `PowerCapNotApplied` | A GPU running above its tier's cap | 🟠 |
| `PDUUnreachable` | SNMP timeout | 🟠 — you are blind |
| `ThermalTierActive` | Constrained or emergency active | Info → 🔴 |

> ⚠️ **`CircuitCommittedOverBudget` should never fire in production.** If it does, the budget model and reality have diverged — a node was moved, a GPU was added, or a cap was not applied. Treat it as a design defect, and add the check to CI so the YAML cannot merge in a bad state.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | 📊 **Real power draw measured per archetype in all six states** | `measure-node-power.sh` | Table complete |
| **A2** | 📊 Boot inrush captured | Measurement | Recorded |
| **A3** | Power budget model covers every circuit, rack, and the facility | `power-budget.yaml` | Complete |
| **A4** | **Σ capped peak ≤ budget for every circuit** | Arithmetic check | Holds |
| **A5** | The arithmetic check runs in CI | `.github/workflows` | Present |
| **A6** | Every node maps to a circuit | `kubectl get nodes -L` | All mapped |
| **A7** | GPU caps applied and verified on every GPU | `nvidia-smi -q -d POWER` | Applied |
| **A8** | **A rack at 100 % GPU load stays under budget** | 🧪 Load the whole rack, measure | Under |
| **A9** | 📊 Throughput cost of each cap tier measured | Benchmark | Table complete |
| **A10** | Power headroom metrics exported and accurate vs. PDU readings | Compare | Within 5 % |
| **A11** | Kueue AdmissionCheck holds workloads when a circuit is near budget | 🧪 Simulate | Held |
| **A12** | The hold reason is clearly stated to the user | `describe workload` | Clear |
| **A13** | Thermal tier 2 engages and reduces caps | 🧪 Simulate high temp | Engages |
| **A14** | Thermal tier 2 auto-restores when conditions normalize | Test | Restores |
| **A15** | Thermal tier 3 drains non-critical GPU work and pages | 🧪 Simulate | Correct |
| **A16** | **Rate-of-change thermal alert fires before the absolute threshold** | Simulate a ramp | Fires early |
| **A17** | Correlated thermal events trip the Phase 23 storm guard | Simulate | Escalates, no mass remediation |
| **A18** | Boot stagger enforced — no more than N nodes powering on per circuit | 🧪 Power on a rack | Staggered |
| **A19** | `emergency-shed.sh` dry run shows evictions and watts saved | Run dry | Accurate |
| **A20** | Shed order **never** targets control plane, storage, or monitoring | Read + test | Never |
| **A21** | Shed requires typed confirmation and audits | Try | Enforced |
| **A22** | All alerts fire correctly | Induce each | Fire |
| **A23** | Power runbook covers a breaker trip, a cooling failure, and a PDU failure | Read it | Complete |

---

## ↩️ ROLLBACK

```bash
# Remove scheduler power gating — capping (layer 1) remains, which is the load-bearing layer
kubectl delete admissioncheck power-headroom

# Disable thermal automation, keep alerting
kubectl scale deploy/thermal-response-controller -n facility --replicas=0

# ⚠️ DO NOT remove GPU power caps as part of a rollback.
# The caps are what keep circuits within budget. Removing them can trip a breaker.
```

> ⚠️ **The power caps are the one thing in this phase that must never be rolled back casually.** Everything else is monitoring and scheduling refinement; the caps are physics enforcement.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Measured draw exceeds the model | Model used nameplate or omitted a component | Re-measure; update the model; re-check the budget |
| Breaker trips despite the model | Inrush, or a cap not applied, or an unaccounted device | Check `PowerCapNotApplied`; audit what is on the circuit |
| Caps revert after a node reboot | DaemonSet ordering — caps applied before the driver is ready | Add a readiness gate; verify after every reboot |
| Workloads held for power with obvious headroom | Exporter reading stale PDU data | Check the scrape; check PDU reachability |
| Thermal tier flapping | Thresholds too close; no hysteresis | Add hysteresis (engage at 32 °C, release at 28 °C) |
| Cooling failure not detected | Only absolute thresholds, no derivative | This is A16 |
| PDU telemetry unavailable | SNMP creds, network, or a dead PDU | You are flying blind — treat as an incident |
| GPU throttling despite caps | Airflow blocked, or ambient too high | Physical inspection; this is a cooling problem, not a power one |
| One node draws far more than its cohort | Failing PSU, or a stuck fan | Investigate; Phase 23 H9/H13 |
| Load shed did not free enough power | Shed order too conservative | Revise; but never move storage above compute |

---

## 🚫 DO NOT

- **Do not** use nameplate ratings for budgeting.
- **Do not** rely on the scheduler as the only thing preventing an overload.
- **Do not** remove GPU power caps as a rollback step.
- **Do not** let automation power off nodes for thermal reasons without Phase 23's storm guard.
- **Do not** shed storage, control plane, or monitoring.
- **Do not** power on a rack's nodes simultaneously.
- **Do not** ignore `CircuitCommittedOverBudget` — it means the model is wrong.
- **Do not** implement checkpointing here (the shed order depends on it). Phase 33.

---

## 📤 HANDOFF

`evidence/phase-32/handoff.md` must state:

1. **📊 The measured power table** per archetype, including boot inrush — the foundation of every capacity decision from here on.
2. **The power budget per circuit/rack/facility**, and the committed vs. available headroom today.
3. **📊 The cap-tier throughput table** — what each watt costs in performance.
4. **The default cap in force** and the perf/W justification.
5. **Circuits at or over budget**, with the resolution policy for each.
6. **🧪 The full-rack load test result** — the proof that A4's arithmetic holds in reality.
7. **Thermal thresholds and hysteresis** in force.
8. **How many more nodes the facility can accept** at the current cap — the hard limit on Milestone M4/M5 growth, feeding Phase 56.
9. **Any missing instrumentation** (no inlet temp sensors, PDUs without per-outlet metering) as a dated finding.

---

## ➡️ NEXT

**[PHASE-33 — Preemption, Checkpointing & Elastic Jobs](PHASE-33.md)** — make interruption cheap, so borrowing, preemption, and load shedding cost minutes instead of days.
