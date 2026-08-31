# PHASE 05 — Capacity Model & Stage-0 Gate

| | |
|---|---|
| **Stage** | 0 — Foundation & Design (**exit gate**) |
| **Estimated effort** | 3 hours |
| **Depends on** | 01, 02, 03, 04 |
| **Blocks** | **All of Stage 1** — nothing is built until this gate passes |
| **Risk** | 🟠 Medium — this is a go/no-go decision with financial consequences |
| **Blast radius** | The project's budget and schedule |
| **Architecture refs** | `ARCHITECTURE.md#p--capacity--performance-models`, `ULTIMATE-PLAN.md#13-success-criteria`, G0, G1 |

---

## 🎯 MISSION

Consolidate every finding from Phases 01–04 into a **sizing model, a prioritized purchase list, and a documented go/no-go decision** on gates **G0** (complete machine-readable inventory) and **G1** (facility supports the load with ≥ 25 % headroom).

> 💡 **WHY a formal gate:** Stage 1 begins spending money and installing hardware. Every subsequent phase assumes the design is settled. This is the last cheap moment to discover that the network is under-specified, the room cannot cool the load, or there is no ECC hardware for the control plane. **A failed gate here costs three hours. A failed gate discovered in Phase 21 costs three months.**

---

## ✅ PREFLIGHT

```bash
# All four upstream phases must be complete with passing acceptance
for p in 01 02 03 04; do
  test -s "evidence/phase-$p/acceptance.md" || echo "MISSING: phase-$p acceptance"
  grep -q "Phase status: COMPLETE" "evidence/phase-$p/acceptance.md" || echo "INCOMPLETE: phase-$p"
done

task validate                        # everything schema-valid
python tools/power-budget.py --check
python tools/net-validate.py
```

**If any upstream phase is incomplete, this gate cannot be evaluated.** Write `evidence/phase-05/BLOCKED.md` naming the incomplete phases and stop.

---

## 📦 DELIVERABLES

```
docs/capacity/
  sizing-model.md               # the math: compute, storage, network, power
  growth-plan.md                # M1→M2→M3→M4 with per-milestone requirements
  purchase-list.md              # THE buy decision, prioritized, costed
  cost-model.md                 # TCO, $/GPU-hour, break-even vs. cloud
  gate-decision.md              # G0/G1 GO or NO-GO, signed and dated
tools/
  capacity-model.py             # computes everything above from inventory/
  fleet-report.py               # one-command fleet status
evidence/phase-05/
  g0-inventory-completeness.md
  g1-facility-headroom.md
  acceptance.md handoff.md deviations.md
```

---

## 📋 TASKS

### Task 1 — Consolidate the findings register

Pull every finding from Phases 01–04 into one prioritized table in `purchase-list.md`. Each row must be independently actionable.

| ID | Src | Sev | Finding | Blocks | Remediation | Qty | Unit $ | Total $ | Milestone |
|---|---|---|---|---|---|---|---|---|---|
| F-01 | 01 | 🔴 | 18 nodes at 1 GbE cannot join the training pool (R-01) | G4, Phase 22 | ConnectX-5 100 GbE NIC | 18 | $180 | $3,240 | M1 |
| F-02 | 01 | 🔴 | No PLP NVMe → Ceph WAL/DB has no valid home (R-04) | Phase 27 | Enterprise NVMe 1.6 TB | 6 | $400 | $2,400 | M2 |
| F-04 | 01 | 🟠 | No ECC hardware → no valid control-node candidate | G3, Phase 12 | Workstation-class control node | 3 | $1,800 | $5,400 | M1 |
| F-07 | 02 | 🔴 | Rack load at 98 % of derated breaker capacity | G1 | Second 30 A circuit per rack | 2 | $1,200 | $2,400 | M1 |
| F-08 | 02 | 🔴 | No switched PDUs → no remote hard power cycle (R-05) | Phase 08 | Switched 3-φ PDU 30 A | 4 | $900 | $3,600 | M1 |
| F-09 | 03 | 🟠 | Leaf switch buffer 16 MB < 3×BDP requirement | R-10, Phase 22 | Higher-buffer switch model | 2 | $3,500 | $7,000 | M2 |
| F-12 | 04 | 🟡 | No offline custody location for the age root key | Phase 10 | Safe / HSM / sealed envelope process | — | — | $0 | M1 |

**Classification rules:**

| Class | Meaning | Treatment |
|---|---|---|
| 🔴 **Blocker** | A gate cannot pass, or a designed capability is impossible | Must be resolved or the scope formally reduced |
| 🟠 **Degradation** | The platform works but misses a stated SLO | Resolve before the milestone that needs it |
| 🟡 **Debt** | Operational friction, no capability loss | Schedule, track, do not block |

> ⚠️ **A 🔴 finding cannot be "accepted."** Either fix it, or amend the plan to remove the capability it blocks — and write that amendment down. Silently proceeding with an unresolved blocker is how a project discovers in month 5 that distributed training was never possible.

---

### Task 2 — The sizing model

**`tools/capacity-model.py`** reads `inventory/` and emits `docs/capacity/sizing-model.md`. Implement each model from `ARCHITECTURE.md#P`.

**Compute (P1):**
```
Effective_GPU_hours = N_gpus × 8760 × Availability × Utilization
  Availability = (1 − MTTR/MTBF) × (1 − planned_maintenance)
               ≈ 0.97 for consumer hardware with auto-remediation
  Utilization  = target 0.70 (DCGM SM occupancy, NOT "allocated")

Aggregate_FP16_TFLOPS = Σ over GPUs of spec_tflops × 0.85   (realized fraction)
Aggregate_usable_VRAM = Σ vramGB × 0.85                      (activation headroom)

Largest trainable model (FSDP, bf16, Adam):
  bytes/param ≈ 2 (weights) + 2 (grads) + 8 (Adam m,v fp32) + 4 (fp32 master) = 16
  Max_params ≈ Aggregate_usable_VRAM_bytes / 16   ... minus activations
  ⚠️ Report this per homogeneous pool, not fleet-wide — a gang cannot span GPU models (R-15)
```

**Storage (P4):**
```
Raw_needed = Logical × Replication_overhead × (1 + Growth) / Target_fullness
  replicated 3× → 3.00   ·   EC 6+3 → 1.50   ·   target_fullness 0.70

Per tier, then check against the inventory's actual device capacity per tier:
  T0 available = Σ storage[].sizeGB where role=scratch
  T2 available = Σ storage[].sizeGB where role=osd-data
  ⚠️ If T2_available < T2_needed → 🔴 finding
```

**Network (P2):**
```
Bisection = N_spines × N_leaves × uplink_speed
Per-node fair share = Bisection / N_nodes
Assert: per-node fair share ≥ node NIC speed for the training pool (non-blocking)
```

**Power (P5):** already computed in Phase 02; assert it still holds against the final node count.

**Emit a single dashboard table:**

| Dimension | Have | Need (M1) | Need (M4) | Gap | Finding |
|---|---|---|---|---|---|
| GPUs | 42 | 8 | 88 | −46 @ M4 | procurement plan |
| Aggregate VRAM | 1,008 GB | 192 GB | 2,112 GB | −1,104 GB | " |
| Physical cores | 704 | 128 | 1,600 | −896 | " |
| RAM | 5.4 TB | 1.0 TB | 12.8 TB | −7.4 TB | " |
| T0 NVMe | 84 TB | 16 TB | 200 TB | −116 TB | " |
| T2 raw (Ceph) | 0 TB | 30 TB | 557 TB | **−30 TB @ M1** | 🔴 F-02 |
| PLP NVMe (WAL/DB) | 0 | 6 | 18 | **−6** | 🔴 F-02 |
| 100 GbE ports | 24 | 8 | 100 | −76 | procurement plan |
| Bisection BW | 1.6 Tb/s | 0.8 | 12.8 | −11.2 Tb/s | " |
| Electrical service | 100 A | 30 A | 400 A | **−300 A** | 🔴 F-07 |
| Cooling | 3 t | 1.5 t | 19.2 t | −16.2 t | staged |
| ECC nodes | 0 | 3 | 5 | **−3** | 🔴 F-04 |

---

### Task 3 — Growth plan

**`docs/capacity/growth-plan.md`** — per milestone, what must be true.

| | **M1 Pilot** | **M2 Squad** | **M3 Wing** | **M4 Fleet** |
|---|---|---|---|---|
| Nodes | 8 | 24 | 48 | 100+ |
| Control nodes | 3 (ECC) | 3 | 3 + 3 infra | **5** + 3 infra |
| Storage nodes | 1 (degraded, `min_size=1` for pilot only) | 3 | 5 | 9 |
| Racks | 1 | 2 | 4 | 8 |
| Switches | 1 leaf + 1 mgmt | 2 leaf (MLAG) + 1 mgmt | 4 leaf + 2 spine | 12 leaf + 4 spine |
| Electrical | 30 A 3-φ | 100 A 3-φ | 200 A 3-φ | 400 A 3-φ |
| Cooling | 1.5 t | 4.6 t | 9.2 t | 19.2 t |
| Ceph failure domain | host | **rack** | rack | rack |
| etcd | 3 members | 3 | 3 | **5 + separate events store** |
| BGP | not needed | not needed | **enabled** | enabled |
| Observability | Prometheus only | + Loki | + Mimir long-term | + per-AZ Prometheus |
| Phases required | 00–19, 25, 30, 31, 36 | +20–24, 26–29, 32–35 | +37–47 | +48–56 |
| **Exit gate** | G2, G3 | G4, G5, G7 | G6, G8, G9 | **G10–G14** |

> ⚠️ **The M1 storage compromise:** a single storage node cannot satisfy Ceph's 3-replica rack-aware rule. For the pilot only, use `failureDomain: host` with 2 replicas across compute nodes, or accept `local-path` and no distributed storage. **Write this down as a known-degraded state with an expiry (M2)**, or it becomes permanent.

---

### Task 4 — Cost model

**`docs/capacity/cost-model.md`** — reproduce `ULTIMATE-PLAN.md §11` with *your* numbers.

```
CAPEX  = hardware + network + facility + labor(install)
OPEX/y = power_IT + power_cooling + refresh_reserve + network_service
       + spares(5 % of hw/y) + ops_labor

Effective_GPU_hours/y = N_gpus × 8760 × availability × utilization

Fully_loaded_$/GPU-hour = (OPEX/y + CAPEX/amortization_years) / Effective_GPU_hours

Break_even_utilization = (OPEX/y + CAPEX/years) / (N_gpus × 8760 × cloud_$/GPU-hr)
```

**Sensitivity table — this is the part that changes decisions:**

| Utilization | $/GPU-hr (fully loaded) | vs. cloud @ $1.50 | vs. cloud @ $2.50 |
|---|---|---|---|
| 20 % | $1.55 | ≈ break-even | 1.6× cheaper |
| 40 % | $0.78 | 1.9× cheaper | 3.2× cheaper |
| 60 % | $0.52 | 2.9× cheaper | 4.8× cheaper |
| **80 %** | **$0.39** | **3.8× cheaper** | **6.4× cheaper** |

> 💡 **The dominant variable is utilization, not hardware cost.** A cluster at 20 % utilization is not cheaper than the cloud. This is why Phases 30–34 (quotas, fair-share borrowing, preemption, idle detection, showback) are not "nice to have" — they are the mechanism that moves you from 20 % to 80 %, and therefore the mechanism that makes the whole project economically sound. Say so explicitly in the cost model.

Also record what the money *does not* buy: no elastic burst beyond 100 nodes, no instant capacity for a new project, and a hardware refresh obligation every ~5 years.

---

### Task 5 — Evaluate Gate G0

**G0: every node's hardware, PCIe topology, and thermal envelope is inventoried and machine-readable.**

`evidence/phase-05/g0-inventory-completeness.md`:

```bash
TOTAL=$(ls inventory/nodes/*.yaml | wc -l)
echo "Nodes in inventory: $TOTAL"

# G0.1 — all schema-valid
task validate:inventory && echo "G0.1 PASS"

# G0.2 — no unresolved manual-review flags
grep -rl "_requiresManualReview" inventory/nodes/ && echo "G0.2 FAIL" || echo "G0.2 PASS"

# G0.3 — every node has raw discovery evidence
MISSING=0
for f in inventory/nodes/*.yaml; do
  n=$(basename "$f" .yaml)
  [ -d "evidence/phase-01/raw/$n" ] || { echo "no evidence: $n"; MISSING=1; }
done
[ $MISSING -eq 0 ] && echo "G0.3 PASS"

# G0.4 — every GPU has pcieWidth, numaAffinity, and capability flags
yq -r '.spec.gpus[]? | select(.pcieWidth == null or .numaAffinity == null or .gpudirectRdma == null) | .model' \
  inventory/nodes/*.yaml | grep . && echo "G0.4 FAIL" || echo "G0.4 PASS"

# G0.5 — every node has measured power
yq -r 'select(.spec.power.measuredPeakW == null) | .metadata.name' inventory/nodes/*.yaml \
  | grep . && echo "G0.5 FAIL" || echo "G0.5 PASS"

# G0.6 — every node maps to a rack, a PDU outlet, and a switch port
python tools/power-budget.py --check-pdu-coverage
python tools/net-validate.py --check-ports
```

**G0 verdict: PASS only if all six sub-checks pass.**

---

### Task 6 — Evaluate Gate G1

**G1: the facility supports the planned load with ≥ 25 % headroom.**

`evidence/phase-05/g1-facility-headroom.md`:

| Check | Requirement | Actual | Verdict |
|---|---|---|---|
| G1.1 Electrical, per circuit | continuous ≤ 0.80 × breaker | | |
| G1.2 Electrical, design headroom | continuous ≤ 0.60 × breaker (i.e. ≥ 25 % below the derated limit) | | |
| G1.3 Service capacity | required ≤ available service | | |
| G1.4 Cooling capacity | capacity ≥ 1.25 × IT load | | |
| G1.5 Cooling redundancy | N+1 from M2 onward | | |
| G1.6 Airflow | CFM available ≥ CFM required at ΔT ≤ 12 °C | | |
| G1.7 Floor loading | rack weight ≤ rated capacity | | |
| G1.8 Remote power | 100 % of nodes on a switched PDU outlet | | |
| G1.9 Electrician sign-off | obtained or scheduled | | |
| G1.10 Safety | EPO, fire detection, noise plan documented | | |

**Evaluate G1 for the milestone you are about to build, not for M4.** It is legitimate to pass G1 for M1 and record that M3 requires a service upgrade — as long as that upgrade is in `growth-plan.md` with a lead time.

---

### Task 7 — The gate decision

**`docs/capacity/gate-decision.md`** — the go/no-go, written plainly.

```markdown
# Stage 0 Gate Decision

Date: YYYY-MM-DD
Decided by: <name/role>
Milestone under evaluation: M1 (8 nodes)

## G0 — Inventory Completeness
Verdict: PASS / FAIL
Evidence: evidence/phase-05/g0-inventory-completeness.md
Notes: <any conditional passes>

## G1 — Facility Headroom
Verdict: PASS / FAIL / CONDITIONAL
Evidence: evidence/phase-05/g1-facility-headroom.md
Conditions: <e.g. "PASS for M1; M3 requires a 200 A service upgrade, 8-week lead time,
             quote obtained YYYY-MM-DD">

## Blockers (🔴) — resolution status
| ID | Finding | Status | Owner | Due |
| F-01 | ... | ORDERED / RESOLVED / ACCEPTED-WITH-SCOPE-CHANGE | | |

## Scope changes accepted
<Any capability removed from the plan because a blocker could not be resolved.
 e.g. "Multi-node distributed training is deferred to M2 because the NIC upgrade
 has a 6-week lead time. M1 will support single-node training only.">

## DECISION
[ ] GO — proceed to Stage 1
[ ] GO WITH CONDITIONS — proceed, conditions listed above with due dates
[ ] NO-GO — reasons and what must change

## Purchase authorization
Total authorized this milestone: $X
Purchase list: docs/capacity/purchase-list.md rows <ids>
```

> ⚠️ **"GO WITH CONDITIONS" is the honest and usual answer.** It is not a failure. What matters is that every condition has an owner and a date, and that the scope changes are written down rather than silently absorbed.

---

### Task 8 — Fleet report tooling

**`tools/fleet-report.py`** — one command that prints current fleet status against the model. Later phases extend it with live cluster data; for now it reads `inventory/` only.

```
$ python tools/fleet-report.py

NEXUS FLEET REPORT — 2026-08-30
════════════════════════════════════════════════════════════
NODES       8 total  ·  3 control  ·  4 compute-gpu  ·  1 storage
STATUS      8 planned · 0 racked · 0 active
COMPUTE     64 cores · 512 GB RAM · 4 GPUs · 96 GB VRAM
            Aggregate FP16: 330 TFLOPS (realized est.)
POOLS       training: 4 nodes (RTX 4090 ×4, 100 GbE RDMA ✅)
NETWORK     4× 100 GbE  ·  bisection 0.4 Tb/s  ·  1:1 ✅
STORAGE     T0 8 TB  ·  T2 raw 0 TB ⚠️  ·  PLP devices 0 ⚠️
POWER       peak 5.4 kW  ·  rack r01 at 62 % of derated ✅
GATES       G0 PASS · G1 CONDITIONAL
BLOCKERS    2 open (F-02, F-04)
════════════════════════════════════════════════════════════
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Findings register consolidates all findings from Phases 01–04 | Cross-reference each phase's handoff | Every finding appears exactly once |
| **A2** | Every finding has severity, blocker linkage, remediation, quantity, and cost | Read `purchase-list.md` | All columns filled |
| **A3** | Every 🔴 blocker is resolved, ordered, or has an explicit accepted scope change | Read `gate-decision.md` | No unaddressed blockers |
| **A4** | Sizing model computes all four dimensions from inventory | `python tools/capacity-model.py` | Runs, output matches `sizing-model.md` |
| **A5** | Gap table exists for M1 and M4 | Read `sizing-model.md` | Both columns present |
| **A6** | Largest-trainable-model figure is reported **per homogeneous pool** | Read it | Per-pool, not fleet-wide |
| **A7** | Growth plan covers M1–M4 with per-milestone requirements | Read `growth-plan.md` | All four columns complete |
| **A8** | Known-degraded states (e.g. M1 storage) have a documented expiry milestone | Read it | Present |
| **A9** | Cost model includes the utilization sensitivity table | Read `cost-model.md` | Table present |
| **A10** | Cost model states that utilization is the dominant variable and links it to Phases 30–34 | Read it | Explicit |
| **A11** | G0 evaluated with all six sub-checks and real command output | `evidence/phase-05/g0-inventory-completeness.md` | Verdict recorded |
| **A12** | G1 evaluated with all ten sub-checks | `evidence/phase-05/g1-facility-headroom.md` | Verdict recorded |
| **A13** | Gate decision document is complete and dated | Read `gate-decision.md` | GO / GO-WITH-CONDITIONS / NO-GO with reasoning |
| **A14** | Every condition on a conditional GO has an owner and a due date | Read it | All conditions bounded |
| **A15** | `fleet-report.py` runs and matches the inventory | Run it | Output consistent |
| **A16** | Purchase authorization amount is stated | Read the decision | Present |

---

## ↩️ ROLLBACK

Documents only. **But note:** if this gate reaches NO-GO after hardware has been ordered, that is a real financial loss. The gate exists to happen *before* the order.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Too many blockers to resolve at once | Ambitious scope | **Reduce the milestone, not the standards.** Build M1 with 4 nodes that fully meet the spec rather than 8 that half-meet it. A correct small cluster teaches you everything; a broken large one teaches you nothing. |
| G1 fails on electrical capacity | The room is too small for the ambition | Options: (1) upgrade the service; (2) cap the cluster at what the room supports; (3) find a different room. Record which, in `growth-plan.md`. |
| Cost model shows the cloud is cheaper | Utilization assumption is too low | Two honest responses: either commit to the scheduling work (Phases 30–34) that raises utilization, or accept that the non-financial returns (sovereignty, latency, learning) are the actual justification — and say so. |
| Cannot resolve a 🔴 blocker | Budget or lead time | Write the scope change. "Distributed training deferred to M2" is a legitimate, honest outcome. Silently proceeding is not. |
| Sizing model output disagrees with intuition | Usually a units error (GB vs GiB, Gb vs GB) | Add unit assertions to `capacity-model.py`. Bit/byte confusion is an 8× error and it is common. |
| Stakeholders want to skip the gate | Schedule pressure | The gate is three hours. Point at the cost of discovering F-01 in Phase 22. |

---

## 🚫 DO NOT

- **Do not** pass a gate with an unresolved 🔴 blocker. Either fix it or change the scope in writing.
- **Do not** evaluate G1 against M4 if you are building M1. Evaluate the milestone in front of you, and record M4's requirements in the growth plan.
- **Do not** buy hardware before the gate decision is recorded.
- **Do not** install anything. This phase produces documents and a decision.
- **Do not** compute a fleet-wide "largest trainable model." Gangs cannot span GPU models (R-15); the number would be fiction.
- **Do not** assume high utilization in the cost model without the scheduling work planned to achieve it.

---

## 📤 HANDOFF

`evidence/phase-05/handoff.md` must state:

1. **The gate decision** — GO / conditional / NO-GO, with conditions and due dates.
2. **The milestone being built** (M1/M2/…), which determines every "how many" in Stage 1.
3. **The final node list for this milestone** — exact node names entering the build, by archetype.
4. **Purchase list status** — ordered, delivered, or pending, with lead times.
5. **Accepted degraded states with expiry milestones** — e.g. M1 storage.
6. **Scope changes** — capabilities removed or deferred, and to which milestone.
7. **The capacity targets each later phase must hit** — Phase 27 needs the T2 sizing; Phase 30 needs the quota totals; Phase 32 needs the power budgets; Phase 52 needs the M4 targets.

---

## ➡️ NEXT

**[PHASE-06 — Seed Node & Workstation Toolchain](PHASE-06.md)** — build the single machine from which the entire cluster is provisioned. Stage 1 begins.
