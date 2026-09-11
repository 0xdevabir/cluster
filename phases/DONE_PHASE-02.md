# PHASE 02 — Facility, Power & Thermal Design

| | |
|---|---|
| **Stage** | 0 — Foundation & Design |
| **Estimated effort** | 3–4 hours (plus external contractor lead time) |
| **Depends on** | 01 |
| **Blocks** | 03, 05, 08 (PDU control), 32 (power-aware scheduling) |
| **Risk** | 🔴 **Highest real-world risk in the project** — electrical and thermal errors cause outages, hardware loss, and fire |
| **Blast radius** | The entire facility |
| **Architecture refs** | `ARCHITECTURE.md#l0--facility-layer`, `ULTIMATE-PLAN.md#45-power--thermal`, `#46-no-bmc`, R-02, R-05, R-14, R-17 |

---

## 🎯 MISSION

Produce a **defensible electrical load study, a cooling plan, a rack/airflow layout, and a remote power-control design** that together let 100 GPU machines run continuously without tripping a breaker, cooking themselves, or requiring a human to walk to a power button.

> ⚠️ **DANGER — this phase is not a paper exercise.** Every other phase in this project can be fixed with a `git revert`. This one cannot. An under-sized circuit fails under load, at night, when the cluster is finally busy. Get the numbers right, and have a licensed electrician sign off before energizing anything.

> 💡 **WHY at Phase 02 and not later:** power and cooling have the longest lead times (electrical work: weeks; cooling equipment: months) and the highest cost variance ($25k–$180k, `ULTIMATE-PLAN.md §6.3`). Starting the contractor conversation now means the facility is ready when the hardware is.

---

## ✅ PREFLIGHT

```bash
# 1. Phase 01 is complete with measured power data
task validate:inventory
yq -r '.spec.power.measuredPeakW' inventory/nodes/*.yaml | grep -v null | wc -l
# ↑ must equal the node count. If power was not measured, go back to Phase 01.

# 2. You know the facility's existing electrical service
#    - Service voltage and phase (e.g. 208V 3-phase, 240V single-phase)
#    - Main breaker rating (amps)
#    - Available panel capacity (spare breaker slots and amps)
#    - Whether the space is a purpose-built room, a garage, an office, or a closet

# 3. You have (or can get) a clamp meter or a metering PDU for validation
```

**If you cannot answer #2, this phase is BLOCKED.** Write `evidence/phase-02/BLOCKED.md` and get an electrician to survey the panel before continuing. Do not estimate your way past this.

---

## 📦 DELIVERABLES

```
docs/facility/
  load-study.md                     # THE deliverable — the electrical math
  cooling-plan.md
  rack-layout.md
  power-control-design.md           # PDU + WoL + KVM strategy
  acoustic-and-safety.md
  contractor-brief.md               # what you hand to the electrician/HVAC contractor
inventory/power/
  circuits.yaml                     # every breaker, its rating, what it feeds
  pdu-map.yaml                      # every PDU, its outlets, and the node on each
  power-budget.yaml                 # per-rack and per-node budgets used by Phase 32
inventory/racks.yaml                # rack IDs, U positions, thermal zones
inventory/schema/{rack,power}.schema.json   # completed (stubbed in Phase 00)
tools/power-budget.py               # computes the load study from inventory/
evidence/phase-02/
  acceptance.md handoff.md deviations.md
  electrician-signoff.md            # or a documented plan to obtain it
```

---

## 📋 TASKS

### Task 1 — Compute the electrical load

**`tools/power-budget.py`** must implement this model and emit `docs/facility/load-study.md`.

```
Per node:
  P_node_peak = P_gpu_peak_sum + P_cpu_tdp + P_platform + P_storage + P_nic
    P_gpu_peak_sum = Σ over GPUs of min(tdpWatts, powerCapWatts)
    P_cpu_tdp      = cpu.tdpWatts × 1.15          # PPT/PL2 can exceed nominal TDP
    P_platform     = 60 W                          # board, fans, RAM, VRM losses
    P_storage      = 8 W per NVMe, 6 W per SSD, 9 W per HDD (active)
    P_nic          = 15 W (100 GbE ConnectX), 8 W (25 GbE), 3 W (1 GbE)
  P_node_psu_draw = P_node_peak / PSU_efficiency   # 0.90 (80+ Gold) — 0.92 (Platinum)

Per rack:
  P_rack = Σ P_node_psu_draw + P_switches + P_pdu_overhead
  I_rack_per_phase = P_rack / (V_line_to_neutral × 3)      # 3-phase wye, balanced
  ⚠️ NEC 210.20(A) / continuous-load rule:
       Breaker rating must be ≥ 1.25 × continuous load
       ⇔ continuous load must be ≤ 0.80 × breaker rating

Facility:
  P_IT     = Σ P_rack
  P_total  = P_IT × PUE          # PUE 1.3 (good) – 1.6 (retrofit room)
  I_service = P_total / (V × √3 × PF)   for 3-phase; PF ≈ 0.98 with modern PSUs

Cooling:
  Q_BTU  = P_IT × 3.412
  Tons   = Q_BTU / 12000
  CFM    = P_IT × 3.412 / (1.08 × ΔT_°F)     # ΔT = 20 °F (11 °C) typical
```

**Worked example — one rack of 12 RTX 4090 nodes:**

| Term | Value |
|---|---|
| GPU (capped at 320 W, see Task 4) | 320 W |
| CPU (170 W TDP × 1.15) | 196 W |
| Platform | 60 W |
| Storage (2× NVMe) | 16 W |
| NIC (100 GbE) | 15 W |
| **P_node_peak** | **607 W** |
| **P_node_psu_draw** (90 % eff.) | **674 W** |
| × 12 nodes | 8,092 W |
| + 2 leaf switches (150 W ea.) + mgmt switch (50 W) | +350 W |
| **P_rack** | **8,442 W** |
| Per-phase current @ 208 V 3-phase wye (120 V L-N) | 8,442 / (120 × 3) = **23.5 A** |
| Required breaker (÷ 0.80) | **≥ 29.3 A → 30 A** |
| **Verdict** | ✅ One 30 A 3-phase circuit per rack — **at 98 % of the derated limit** |

> ⚠️ **That verdict is too tight.** 98 % of the derated limit leaves no headroom for a transient, a fan ramp, or a future node. **Design rule: target ≤ 80 % of the derated limit** (i.e. ≤ 64 % of the raw breaker rating). Fix by one of:
> - **A:** 10 nodes per rack instead of 12 → 19.6 A/phase = 82 % of derated. ✅
> - **B:** Two 30 A circuits per rack (A/B feeds, dual-corded where PSUs allow) → 11.8 A/phase each. ✅✅ **Recommended** — also gives PDU-level redundancy.
> - **C:** A 50 A 3-phase circuit per rack → 23.5/40 = 59 %. ✅
>
> Choose one, state which, and record the reasoning in `load-study.md`.

**Facility roll-up at M4 (8 racks, option B):**

| Metric | Value |
|---|---|
| P_IT | 8 × 8,442 W = **67.5 kW** |
| P_total @ PUE 1.4 | **94.6 kW** |
| Service current @ 208 V 3-φ | 94,600 / (208 × 1.732 × 0.98) = **268 A** |
| **Required service** | **400 A 3-phase** (nearest standard size with headroom) |
| Heat load | 67.5 kW × 3.412 = **230,300 BTU/h = 19.2 tons** |
| Airflow @ ΔT 20 °F | 230,300 / (1.08 × 20) = **10,660 CFM** |

**Staged buildout table** — because you will not install all 100 nodes at once:

| Milestone | Nodes | Racks | P_IT | Service needed | Cooling |
|---|---|---|---|---|---|
| M1 | 8 | 1 | 5.4 kW | 1× 30 A 3-φ (or 2× 30 A 1-φ) | 1.5 t — a good portable/mini-split |
| M2 | 24 | 2 | 16.2 kW | 100 A 3-φ | 4.6 t |
| M3 | 48 | 4 | 32.4 kW | 200 A 3-φ | 9.2 t |
| M4 | 100 | 8 | 67.5 kW | 400 A 3-φ | 19.2 t |

> 💡 **Design the electrical infrastructure for M4 even if you build M1.** Pulling conduit twice costs more than pulling it once oversized. Pulling *feeders* once and adding breakers later is the cheap path.

---

### Task 2 — Cooling design

**Choose an approach:**

| Approach | Capacity | Cost | Suits | Notes |
|---|---|---|---|---|
| Mini-split / ductless | 1–5 t | $2–6 k | M1 pilot | Cheap, easy, no containment. Watch for short-cycling. |
| Portable spot cooler | 1–4 t | $1–3 k | Emergency/M1 | Poor efficiency; needs condensate + exhaust path |
| CRAC / CRAH (room) | 5–30 t | $15–60 k | M2–M4 | Needs containment to be effective |
| **In-row cooler** | 10–40 t | $20–80 k | **M3–M4, recommended** | Cooling next to the load; the shortest air path; scales per row |
| Rear-door heat exchanger | 20–40 kW/rack | $8–15 k/rack | High density | Needs chilled water |
| Direct-to-chip liquid | 50–100 kW/rack | $$$ | Not applicable | Consumer GPUs are air-cooled |

**Mandatory design elements regardless of approach:**

1. **Hot/cold aisle separation.** Every node's intake faces the cold aisle; every exhaust faces the hot aisle. **This alone is worth 20–30 % of cooling capacity.**
2. **Containment** (curtains at minimum, doors + roof ideally). Without it, hot exhaust recirculates into intakes and you cool the room instead of the machines.
3. **Blanking panels in every empty U.** Air takes the path of least resistance; an open U is a bypass.
4. **Brush grommets on every cable cutout.**
5. **ΔT target ≤ 12 °C (22 °F)** between cold-aisle intake and hot-aisle exhaust. A larger ΔT means insufficient airflow; a smaller one means you are over-ventilating and wasting fan power.
6. **Intake temperature 18–24 °C.** ASHRAE A2 allows up to 35 °C, but consumer GPUs boost-clock aggressively and will throttle. Cooler intake = higher sustained clocks = more compute per watt.
7. **Humidity 40–60 % RH.** Below 30 % risks static; above 70 % risks condensation.
8. **Redundancy: N+1** on cooling from M2 onward. A cooling failure at 67 kW raises room temperature by roughly **1 °C every 10–20 seconds** in a small room. You have minutes, not hours.
9. **Thermal shutdown interlock.** A room-temperature sensor that, above a threshold, triggers a graceful cluster shutdown via the PDUs. Wire this in Phase 32; specify it here.

**Instrumentation to specify now** (Phase 45 wires it into Prometheus):
- 2× intake + 2× exhaust temperature probes per rack
- 1× room temperature + humidity sensor per zone
- Per-outlet power metering on every PDU
- Differential pressure sensor across containment (if used)
- Water-leak detection (if chilled water is anywhere near)

---

### Task 3 — Rack layout & chassis strategy

Fill in `docs/facility/rack-layout.md` and `inventory/racks.yaml` following `ARCHITECTURE.md#L0.1`.

**The chassis decision** (from `ARCHITECTURE.md#L0.1`) — pick one and justify it:

| | Open-frame mining rack | **4U rackmount ATX** | Towers on shelves |
|---|---|---|---|
| Density | High | Medium | Low |
| Cost/node | ~$40 | ~$120–200 | $0 |
| Airflow | Needs a fan wall; front-to-back achievable | Native front-to-back | Chaotic |
| PCIe risk | ⚠️ **Cheap risers are PCIe 3.0 x1** — catastrophic | None (direct slot) | None |
| Serviceability | Excellent | Good | Poor at scale |
| **Verdict** | Only with x16→x16 shielded risers, verified in Phase 01/A5 | **Recommended for M2+** | M1 pilot only |

⚠️ **If you use risers, re-run Phase 01 Task 4/P2 (link width under load) after racking.** A riser that downtrains a 100 GbE NIC or a GPU to x1 destroys the entire performance thesis and is invisible until you measure.

**Layout rules:**
- Control nodes: one per rack, in **three different racks** (`ARCHITECTURE.md#L4.1` anti-affinity).
- Storage nodes: spread across ≥ 3 racks (Ceph rack-level CRUSH rule, `#L6.3`).
- Heaviest equipment at the bottom. A 42U rack full of 4U chassis is ~500 kg — **verify the floor loading**.
- Switches at the top (short DAC runs to nodes below) or middle-of-rack (shorter average run).
- Leave **≥ 1.2 m of clearance** in front and behind for service access.
- Label every node's physical position and match it exactly to `spec.location` in the inventory. A mismatch here means a technician power-cycles the wrong machine during an incident.

---

### Task 4 — GPU power capping (the free performance-per-watt win)

Consumer GPUs ship tuned for benchmark peaks, far past their efficiency knee. Capping power costs a few percent of throughput and saves a large fraction of watts and heat.

**Typical measured shape** (verify per model in Phase 49):

| Power cap | % of stock TDP | Throughput retained | Perf/Watt |
|---|---|---|---|
| 450 W (stock 4090) | 100 % | 100 % | 1.00× |
| 400 W | 89 % | ~98 % | 1.10× |
| **350 W** | **78 %** | **~95 %** | **1.22×** |
| **320 W** | **71 %** | **~92 %** | **1.29×** ← the knee |
| 280 W | 62 % | ~85 % | 1.36× |
| 250 W | 56 % | ~76 % | 1.37× (diminishing) |

**Set `spec.gpus[].powerCapWatts` in the inventory now** based on the model's expected knee; Phase 49 will replace the estimate with measurements, and Phase 18 will enforce the cap via the GPU Operator.

**Impact on this phase's math:** capping 100× 4090s from 450 W to 320 W removes **13 kW** of IT load and **~4.4 tons** of cooling. That is worth roughly $20k–$40k of avoided facility cost for a ~8 % throughput trade. Document this trade explicitly in `load-study.md`.

---

### Task 5 — Remote power control design (solving the no-BMC problem)

This is the design that makes the fleet operable. See `ULTIMATE-PLAN.md §4.6`.

**`docs/facility/power-control-design.md`** must specify all four layers:

| Layer | Mechanism | Covers | Spec |
|---|---|---|---|
| **1. Power on** | Wake-on-LAN magic packet | Normal boot from S5 | Requires: BIOS WoL on, ErP off (Phase 01/Task 5), mgmt VLAN reachable, `ethtool wol g` persisted (Phase 09) |
| **2. Graceful shutdown** | Talos API `talosctl shutdown` | Planned maintenance | Needs the node to be responsive |
| **3. Hard power cycle** | **Switched PDU outlet toggle** | Hung node, kernel panic, WoL failure | **Mandatory.** Choose a PDU with per-outlet switching + metering and an SNMPv3 or REST API |
| **4. Console / BIOS** | PiKVM v4 + HDMI/USB matrix switch | BIOS changes, boot-failure diagnosis | 1 PiKVM + 1× 16-port matrix per rack |

**PDU selection criteria** (this is a real purchasing decision):

| Requirement | Why |
|---|---|
| Per-outlet **switching** | Layer 3 above. A metered-only PDU cannot power-cycle. |
| Per-outlet **metering** | Feeds Phase 32's power-aware scheduler and Phase 45's dashboards |
| SNMPv3 **or** REST/JSON API | Automation. Avoid telnet-only legacy units. |
| 3-phase input, 30 A (or 2× per rack) | Matches Task 1 option B |
| Outlet count ≥ node count + switches + 2 spare | Do not run out |
| Outlet-level power-on **sequencing/delay** | Prevents an inrush-current trip when a rack powers up |
| Environmental sensor ports | Reuse for the temperature probes in Task 2 |

**Write the outlet map now** in `inventory/power/pdu-map.yaml`:
```yaml
apiVersion: nexus.io/v1
kind: PduMap
pdus:
  - name: r01-pdu-a
    rack: r01
    model: "<vendor/model>"
    mgmtIp: 10.100.1.11          # management VLAN only — NEVER routable from tenants
    phases: 3
    breakerAmps: 30
    api: { type: snmpv3, credentialRef: "vault://nexus/pdu/r01-pdu-a" }
    outlets:
      - { id: 1,  node: nx-c-r01-01, psu: primary }
      - { id: 2,  node: nx-c-r01-02, psu: primary }
      # ...
      - { id: 23, node: r01-leaf-a,  psu: primary }
      - { id: 24, node: null,        reserved: spare }
```

> ⚠️ **The PDU management network is a critical security boundary.** Anyone who can reach it can power off the cluster. It lives on VLAN 100, is not routable from tenant namespaces, and requires VPN + jump-host access (`ARCHITECTURE.md#X1.1`).

**Escalation ladder for an unresponsive node** — encode this in `runbooks/` later, specify it now:
```
1. Talos API responds?         → talosctl reboot
2. Node pings but API is dead? → PDU power-cycle (off, 10 s, on)
3. Node dark, PDU shows 0 A?   → hardware failure → PiKVM to inspect POST
4. PiKVM shows no POST?        → physical intervention required → ticket
```

---

### Task 6 — Safety, acoustics & environment

**`docs/facility/acoustic-and-safety.md`**

| Concern | Reality at 100 nodes | Requirement |
|---|---|---|
| **Noise** | 85–95 dBA sustained | **This is above the OSHA 8-hour exposure limit (85 dBA TWA).** Not an office. Not a home. Hearing protection for anyone working inside, or a dedicated room with acoustic treatment (R-17). |
| **Fire** | 67 kW of electronics | Clean-agent suppression (FM-200/Novec) if the room is dedicated; at minimum, smoke detection tied to a power-cut interlock. **Never** a water sprinkler directly over energized racks if avoidable. Consult local code. |
| **Electrical safety** | 400 A service | Licensed electrician. Lockout/tagout procedure. Labeled panels. Arc-flash assessment for the panel. |
| **Floor loading** | ~500 kg/rack | Verify the structural rating, especially on a raised floor or a non-ground-floor room. |
| **Egress & clearance** | — | NEC requires working clearance in front of panels (typically 900 mm). Do not block it with racks. |
| **Emergency power off (EPO)** | — | A clearly labeled, protected EPO that cuts the room. Required by code in many jurisdictions for a dedicated equipment room. |
| **Condensate** | Cooling produces water | Drain path + leak detection + a pan. Water above electronics is how clusters die. |
| **UPS scope** | Control + storage + network only (≈ 6 kW) | Compute nodes deliberately excluded (ADR-020). Size for **graceful shutdown time**, not ride-through: 10–15 minutes is enough to checkpoint and stop cleanly. |
| **Generator** | Optional | Only if the workload justifies it. For a research cluster, graceful shutdown is usually sufficient. |

---

### Task 7 — The contractor brief

**`docs/facility/contractor-brief.md`** — a one-page document you can hand to an electrician and an HVAC contractor. Include:

1. **Electrical scope:** required service size, number and rating of branch circuits, receptacle types (e.g. IEC 60309 or NEMA L21-30R), panel location, conduit runs, EPO, and the staged buildout schedule.
2. **Load table:** per-rack continuous load in kW and amps per phase, with the 80 % continuous-load derate shown explicitly.
3. **Cooling scope:** heat load in BTU/h and tons, ΔT target, required CFM, containment expectations, N+1 requirement, sensor and interlock requirements.
4. **Constraints:** floor loading, ceiling height, existing service, room dimensions, noise expectations.
5. **Explicit ask:** "Please quote, and please tell us what we got wrong." Contractors catch things a spreadsheet does not.

📊 **Get three quotes** (R-14). Cost variance on this scope is routinely 2–3×.

---

### Task 8 — Complete the power & rack schemas

Fill in `inventory/schema/rack.schema.json` and `inventory/schema/power.schema.json` (stubbed in Phase 00). Required validations:

- Every `circuits.yaml` entry: `breakerAmps`, `voltage`, `phases`, `feeds[]`, and a computed `continuousLoadAmps` that must be ≤ `0.8 × breakerAmps` — **make this a schema-adjacent check in `tools/power-budget.py` that fails CI.**
- Every PDU outlet references a node that exists in `inventory/nodes/`, or is explicitly `null`/`reserved`.
- Every node in `inventory/nodes/` appears on at least one PDU outlet.
- Sum of `power.budgetWatts` per rack ≤ the rack's derated capacity.

> 💡 **WHY encode this in CI:** in six months someone will add nodes to a rack without re-checking the breaker. The pipeline should stop them.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Load study exists with per-node, per-rack, and facility totals | Read `docs/facility/load-study.md` | Every term in the Task 1 model is present with a number |
| **A2** | Every circuit's continuous load ≤ 80 % of breaker rating | `python tools/power-budget.py --check` | Exit 0 |
| **A3** | Per-rack load ≤ 80 % of the *derated* capacity (design headroom) | Same tool, `--headroom` check | Exit 0 or an explicit documented exception |
| **A4** | Required service size is stated and compared to available service | Read the study | Explicit gap statement: sufficient / requires upgrade of X A |
| **A5** | Cooling capacity ≥ 1.15 × IT load, with N+1 from M2 | Read `cooling-plan.md` | Stated with the calculation |
| **A6** | Airflow (CFM) computed and matched to the chosen equipment | Read `cooling-plan.md` | Number present, equipment spec meets it |
| **A7** | Rack layout assigns every node a rack + U, matching inventory | `python tools/power-budget.py --check-locations` | No node unplaced, no U collision |
| **A8** | Control nodes are in ≥ 3 distinct racks | Query `inventory/nodes/` | True (once ≥ 3 racks exist) |
| **A9** | Storage nodes span ≥ 3 racks | Same | True |
| **A10** | Every node maps to a switched-PDU outlet | `--check-pdu-coverage` | 100 % coverage |
| **A11** | PDU model chosen supports per-outlet switching + metering + API | Read `power-control-design.md` | Model named, three capabilities confirmed from the datasheet |
| **A12** | The four-layer power-control design is complete | Read the doc | All four layers specified with concrete mechanisms |
| **A13** | `powerCapWatts` set for every GPU | `yq` across inventory | No nulls |
| **A14** | Safety document addresses noise, fire, floor loading, EPO, condensate | Read it | All five present |
| **A15** | Contractor brief exists and is self-contained | Read it | A contractor could quote from it without asking you questions |
| **A16** | Electrician sign-off obtained, or a dated plan to obtain it | `evidence/phase-02/electrician-signoff.md` | Present |
| **A17** | CI enforces the power budget | Add an over-budget rack to a test branch | Pipeline fails |

---

## ↩️ ROLLBACK

Documents and schemas only — `git revert`. **Physical electrical work cannot be rolled back by this project**; that is precisely why the sign-off gate (A16) exists before any work is commissioned.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Load study says you need more service than the building has | Reality | Options: (1) upgrade the service; (2) reduce node count per milestone; (3) lower power caps; (4) find a different room. **Do not** proceed by hoping the peak never happens. |
| Breaker trips under load despite a "passing" calculation | Inrush current at power-on, or an unbalanced 3-phase load | Enable PDU outlet power-on sequencing (2 s stagger). Rebalance node-to-phase assignment. |
| One phase runs much hotter than the others | Unbalanced assignment | Distribute nodes round-robin across phases in `pdu-map.yaml`; most PDUs report per-phase current |
| GPUs throttle despite adequate room cooling | Recirculation — hot exhaust reaching intakes | Containment, blanking panels, brush grommets. Measure intake temperature *at the node*, not at the room thermostat. |
| ΔT across the rack is > 15 °C | Insufficient airflow | More CFM, or fewer nodes per rack |
| ΔT is < 6 °C | Over-ventilating | Reduce fan speed; you are wasting energy |
| WoL fails on a node that has it enabled in BIOS | ErP/EuP is still on, or the NIC lost the WoL flag across a reboot | Disable ErP. Persist `ethtool -s <if> wol g` via the Talos machine config (Phase 09). |
| PDU API returns stale outlet state | Firmware caching | Poll interval ≥ 5 s; do not use the PDU as a real-time sensor. For real-time, use node-level telemetry. |
| The room is unbearably loud | 100 GPU fans | Expected. This is an equipment room, not a workspace. See `acoustic-and-safety.md`. |

---

## 🚫 DO NOT

- **Do not** energize new circuits without an electrician's sign-off.
- **Do not** size to the *average* load. Size to the *continuous peak*, with derate.
- **Do not** put compute nodes on the UPS (ADR-020). It multiplies UPS cost for no benefit if jobs checkpoint.
- **Do not** buy metered-only PDUs. Without per-outlet switching, layer 3 of the power-control design does not exist, and R-05 materializes.
- **Do not** run the management VLAN (PDUs, PiKVM) anywhere reachable from tenant workloads.
- **Do not** design cooling for the *pilot* size. Design for M4 and install in stages.
- **Do not** configure any switch or VLAN here. That is Phase 03 (this phase only reserves VLAN 100 conceptually).
- **Do not** implement the power-aware scheduler. That is Phase 32; this phase produces the budget data it consumes.
- **Do not** install anything on the nodes. No OS work happens before Phase 09.

---

## 📤 HANDOFF

`evidence/phase-02/handoff.md` must state:

1. **Final electrical design** — service size, circuit count/rating, the chosen per-rack option (A/B/C from Task 1), and current status (designed / quoted / installed).
2. **Cooling design** — approach, capacity, redundancy level, and status.
3. **PDU model and API type** — Phase 08 writes the power-control integration against this exact API; Phase 32 reads its metering.
4. **PDU management IPs and credential locations** (by Vault path reference — never the credentials themselves).
5. **Final rack layout** — which nodes are in which rack, since Phase 03 assigns switch ports by rack and Phase 27 builds the Ceph CRUSH map from it.
6. **Per-rack power budgets** — the exact numbers Phase 32's admission controller will enforce.
7. **GPU power caps chosen** — Phase 18 applies them; Phase 49 validates and refines them.
8. **Anything the facility cannot support** — e.g. "the room supports 4 racks; M4 requires a second room." This is a scaling constraint the roadmap must absorb.

---

## ➡️ NEXT

**[PHASE-03 — Network Fabric Design](PHASE-03.md)** — design the leaf-spine topology, IP/VLAN plan, BGP peering, and the RoCEv2 lossless contract. The rack layout you just finalized determines the switch port map.
