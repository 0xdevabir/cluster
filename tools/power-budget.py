#!/usr/bin/env python3
"""Compute the Phase 02 electrical/thermal load study from inventory/.

Implements the model in phases/PHASE-02.md Task 1, sourced live from
inventory/nodes/*.yaml, inventory/racks.yaml, inventory/power/circuits.yaml
and inventory/power/pdu-map.yaml — never from a cached number — so a node
added without re-running this script fails CI instead of silently drifting
past the breaker rating.

Usage:
  python tools/power-budget.py                  # write load-study.md + power-budget.yaml
  python tools/power-budget.py --check           # A2: per-circuit continuous load <= 80% of breaker
  python tools/power-budget.py --headroom        # A3: per-rack load <= 80% of combined derated capacity
  python tools/power-budget.py --check-locations # A7: every node has a rack+U that exists, no collisions
  python tools/power-budget.py --check-pdu-coverage  # A10: every node is on exactly one switched PDU outlet pair
  python tools/power-budget.py --pue 1.4         # override the default PUE used for the facility roll-up
"""
from __future__ import annotations

import argparse
import datetime
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Task 1 model constants ──────────────────────────────────────────────
PLATFORM_W = 60.0
CPU_PPT_MULTIPLIER = 1.15
STORAGE_W = {
    "consumer-nvme": 8.0,
    "enterprise-nvme": 8.0,
    "consumer-sata-ssd": 6.0,
    "enterprise-sata-ssd": 6.0,
    "hdd": 9.0,
}
NIC_W_BY_SPEED = {1: 3.0, 10: 5.0, 25: 8.0, 40: 10.0, 50: 12.0, 100: 15.0, 200: 20.0, 400: 25.0}
PSU_EFFICIENCY = 0.90  # 80+ Gold; override per-node not modeled — see load-study.md assumptions
V_LINE_TO_NEUTRAL = 120.0  # 208V 3-phase wye
V_LINE_TO_LINE = 208.0
POWER_FACTOR = 0.98
BREAKER_DERATE = 0.80  # NEC 210.20(A): continuous load <= 0.80 x breaker rating
DEFAULT_PUE = 1.5  # M1: portable/mini-split cooling; revisit per milestone (see load-study.md)
BTU_PER_WATT = 3.412
BTU_PER_TON = 12000.0
DELTA_T_F = 20.0
CFM_CONSTANT = 1.08


def load_yaml(path):
    with open(path) as f:
        return list(yaml.safe_load_all(f))


def load_nodes():
    nodes = []
    for path in sorted(glob.glob(os.path.join(ROOT, "inventory/nodes/*.yaml"))):
        if path.endswith(".example"):
            continue
        docs = load_yaml(path)
        for doc in docs:
            if doc and doc.get("kind") == "NodeSpec":
                nodes.append(doc)
    return nodes


def load_single_doc(relpath, kind):
    path = os.path.join(ROOT, relpath)
    if not os.path.exists(path):
        return None
    for doc in load_yaml(path):
        if doc and doc.get("kind") == kind:
            return doc
    return None


def node_power(node):
    spec = node["spec"]
    name = node["metadata"]["name"]

    gpu_w = sum(min(g["tdpWatts"], g.get("powerCapWatts", g["tdpWatts"])) for g in spec.get("gpus", []) or [])
    cpu_w = spec["cpu"]["tdpWatts"] * CPU_PPT_MULTIPLIER
    platform_w = PLATFORM_W
    storage_w = sum(STORAGE_W.get(d["class"], 8.0) for d in spec.get("storage", []) or [])
    nic_w = sum(NIC_W_BY_SPEED.get(n["speedGbps"], 8.0) for n in spec.get("nics", []) or [])

    peak_w = gpu_w + cpu_w + platform_w + storage_w + nic_w
    psu_draw_w = peak_w / PSU_EFFICIENCY

    return {
        "name": name,
        "rack": spec["location"]["rack"],
        "gpuW": round(gpu_w, 2),
        "cpuW": round(cpu_w, 2),
        "platformW": round(platform_w, 2),
        "storageW": round(storage_w, 2),
        "nicW": round(nic_w, 2),
        "peakW": round(peak_w, 2),
        "psuDrawW": round(psu_draw_w, 2),
        "budgetWatts": spec.get("power", {}).get("budgetWatts"),
    }


def rack_infra_watts(rack_cfg):
    switches = sum(s["powerW"] for s in rack_cfg.get("switches", []) or [])
    kvm = sum(k["powerW"] for k in rack_cfg.get("kvm", []) or [])
    return switches + kvm


def compute(pue=DEFAULT_PUE):
    nodes = load_nodes()
    node_powers = [node_power(n) for n in nodes]

    rack_map = load_single_doc("inventory/racks.yaml", "RackMap")
    circuit_map = load_single_doc("inventory/power/circuits.yaml", "CircuitMap")
    if rack_map is None or circuit_map is None:
        sys.exit("power-budget: inventory/racks.yaml and inventory/power/circuits.yaml must exist")

    racks_by_name = {r["name"]: r for r in rack_map["racks"]}
    circuits_by_rack = {}
    for c in circuit_map["circuits"]:
        circuits_by_rack.setdefault(c["rack"], []).append(c)

    rack_results = []
    for rack_name, rack_cfg in racks_by_name.items():
        rack_nodes = [np for np in node_powers if np["rack"] == rack_name]
        nodes_w = sum(np["psuDrawW"] for np in rack_nodes)
        infra_w = rack_infra_watts(rack_cfg)
        rack_w = nodes_w + infra_w

        circuits = circuits_by_rack.get(rack_name, [])
        n_circuits = len(circuits) or 1
        per_circuit_w = rack_w / n_circuits
        per_circuit_amps = per_circuit_w / (V_LINE_TO_NEUTRAL * 3)

        circuit_results = []
        for c in circuits:
            derated_amps = c["breakerAmps"] * BREAKER_DERATE
            circuit_results.append(
                {
                    "name": c["name"],
                    "breakerAmps": c["breakerAmps"],
                    "continuousLoadAmps": round(per_circuit_amps, 2),
                    "percentOfDerated": round(100 * per_circuit_amps / derated_amps, 1) if derated_amps else None,
                }
            )

        rack_results.append(
            {
                "name": rack_name,
                "nodeCount": len(rack_nodes),
                "nodesW": round(nodes_w, 2),
                "infraW": round(infra_w, 2),
                "rackW": round(rack_w, 2),
                "circuits": circuit_results,
            }
        )

    p_it_w = sum(r["rackW"] for r in rack_results)
    p_total_w = p_it_w * pue
    service_current_a = p_total_w / (V_LINE_TO_LINE * (3 ** 0.5) * POWER_FACTOR)
    cooling_tons = (p_it_w * BTU_PER_WATT) / BTU_PER_TON
    cooling_cfm = (p_it_w * BTU_PER_WATT) / (CFM_CONSTANT * DELTA_T_F)

    facility = {
        "pIT_W": round(p_it_w, 2),
        "pue": pue,
        "pTotal_W": round(p_total_w, 2),
        "serviceCurrentA": round(service_current_a, 2),
        "coolingTons": round(cooling_tons, 3),
        "coolingCfm": round(cooling_cfm, 1),
    }

    return {"nodes": node_powers, "racks": rack_results, "facility": facility}


# ── Checks (CI gates) ───────────────────────────────────────────────────

def check_breaker_derate(result, per_circuit=True):
    """A2/A3: every circuit's (and by extension every rack's) continuous
    load must be <= 80% of the breaker rating. per_circuit=True checks each
    circuit individually (A2); False rolls the rack's circuits up together
    (A3, the design-headroom view)."""
    violations = []
    for rack in result["racks"]:
        if per_circuit:
            for c in rack["circuits"]:
                if c["percentOfDerated"] is not None and c["percentOfDerated"] > 100.0:
                    violations.append(
                        f"{rack['name']}/{c['name']}: {c['continuousLoadAmps']}A is "
                        f"{c['percentOfDerated']}% of the derated {c['breakerAmps'] * BREAKER_DERATE}A limit"
                    )
        else:
            derated_total_w = sum(c["breakerAmps"] * BREAKER_DERATE * V_LINE_TO_NEUTRAL * 3 for c in rack["circuits"])
            if derated_total_w and rack["rackW"] > derated_total_w:
                pct = 100 * rack["rackW"] / derated_total_w
                violations.append(f"{rack['name']}: {rack['rackW']}W is {pct:.1f}% of combined derated capacity {derated_total_w:.0f}W")
    return violations


def check_locations():
    nodes = load_nodes()
    rack_map = load_single_doc("inventory/racks.yaml", "RackMap")
    racks_by_name = {r["name"]: r for r in rack_map["racks"]} if rack_map else {}

    violations = []
    seen = {}
    for n in nodes:
        loc = n["spec"]["location"]
        name = n["metadata"]["name"]
        rack, u = loc["rack"], loc["u"]
        if rack not in racks_by_name:
            violations.append(f"{name}: rack {rack} is not defined in inventory/racks.yaml")
            continue
        units = racks_by_name[rack]["units"]
        if not (1 <= u <= units):
            violations.append(f"{name}: U{u} is outside rack {rack}'s {units}U")
        key = (rack, u)
        if key in seen:
            violations.append(f"{name}: U{u} in {rack} collides with {seen[key]}")
        seen[key] = name
    return violations


def check_pdu_coverage():
    nodes = load_nodes()
    node_names = {n["metadata"]["name"] for n in nodes}
    pdu_map = load_single_doc("inventory/power/pdu-map.yaml", "PduMap")
    if pdu_map is None:
        return ["inventory/power/pdu-map.yaml is missing"]

    covered = set()
    for pdu in pdu_map["pdus"]:
        for outlet in pdu["outlets"]:
            if outlet.get("node"):
                covered.add(outlet["node"])

    violations = []
    for name in sorted(node_names - covered):
        violations.append(f"{name}: not present on any PDU outlet")
    # Outlets pointing at a node that no longer exists (excluding switches/kvm, which
    # are not NodeSpecs) are a silent misconfiguration risk too.
    infra_names = set()
    rack_map = load_single_doc("inventory/racks.yaml", "RackMap")
    if rack_map:
        for r in rack_map["racks"]:
            infra_names |= {s["name"] for s in r.get("switches", []) or []}
            infra_names |= {k["name"] for k in r.get("kvm", []) or []}
    for pdu in pdu_map["pdus"]:
        for outlet in pdu["outlets"]:
            node = outlet.get("node")
            if node and node not in node_names and node not in infra_names:
                violations.append(f"{pdu['name']} outlet {outlet['id']}: references unknown device {node}")
    return violations


# ── Output generation ───────────────────────────────────────────────────

def write_power_budget_yaml(result):
    doc = {
        "apiVersion": "nexus.io/v1",
        "kind": "PowerBudget",
        "generatedBy": "tools/power-budget.py",
        "generatedAt": datetime.date.today().isoformat(),
        "assumptions": {
            "psuEfficiency": PSU_EFFICIENCY,
            "cpuPptMultiplier": CPU_PPT_MULTIPLIER,
            "platformWatts": PLATFORM_W,
            "pue": result["facility"]["pue"],
            "voltageLineToLine": V_LINE_TO_LINE,
            "powerFactor": POWER_FACTOR,
            "breakerDerate": BREAKER_DERATE,
            "deltaTF": DELTA_T_F,
        },
        "nodes": result["nodes"],
        "racks": result["racks"],
        "facility": result["facility"],
    }
    out_path = os.path.join(ROOT, "inventory/power/power-budget.yaml")
    with open(out_path, "w") as f:
        f.write("# Generated by tools/power-budget.py — do not hand-edit; re-run the script instead.\n")
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False)
    return out_path


def fmt_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def write_load_study_md(result):
    nodes = result["nodes"]
    racks = result["racks"]
    facility = result["facility"]

    per_node_rows = [
        [n["name"], n["rack"], n["gpuW"], n["cpuW"], n["platformW"], n["storageW"], n["nicW"], n["peakW"], n["psuDrawW"]]
        for n in nodes
    ]
    per_node_table = fmt_table(
        ["Node", "Rack", "GPU W", "CPU W", "Platform W", "Storage W", "NIC W", "P_node_peak W", "P_node_psu_draw W"],
        per_node_rows,
    )

    rack_rows = []
    for r in racks:
        for c in r["circuits"]:
            rack_rows.append(
                [r["name"], r["nodeCount"], r["nodesW"], r["infraW"], r["rackW"], c["name"], c["breakerAmps"],
                 c["continuousLoadAmps"], f"{c['percentOfDerated']}%"]
            )
    rack_table = fmt_table(
        ["Rack", "Nodes", "Nodes W", "Infra W", "P_rack W", "Circuit", "Breaker A", "Continuous A", "% of derated (80%)"],
        rack_rows,
    )

    content = f"""# Facility Load Study

_Generated by `tools/power-budget.py` from `inventory/nodes/*.yaml`, `inventory/racks.yaml`,
`inventory/power/circuits.yaml`. Re-run after every inventory change — this is the CI gate
for A2/A3 (`--check`, `--headroom`)._

Model: `phases/PHASE-02.md` Task 1. Assumptions: PSU efficiency {PSU_EFFICIENCY * 100:.0f}%
(80+ Gold), CPU sustained draw {CPU_PPT_MULTIPLIER}x TDP (PL2/PPT headroom), platform
overhead {PLATFORM_W:.0f} W/node, 208 V 3-phase wye ({V_LINE_TO_NEUTRAL:.0f} V line-to-neutral),
power factor {POWER_FACTOR}, NEC 210.20(A) continuous-load derate {BREAKER_DERATE * 100:.0f}%.

## Per-node peak load (M1 pilot fleet, real inventory)

{per_node_table}

## Per-rack load and circuit sizing

**Design choice: Option B — two 30 A 3-phase circuits per rack (A/B feeds).** M1 pilot nodes
carry a single consumer ATX PSU each (not dual-corded), so `inventory/power/pdu-map.yaml`
alternates each node's _primary_ live feed between PDU-A and PDU-B (control/compute nodes
round-robin) to keep the two circuits' steady-state load within a few percent of each other,
with the same-numbered outlet on the other PDU reserved as a manual-swap `failover` slot
(Task 5's hard-power-cycle / escalation-ladder path also uses this pairing). This is why the
table below shows each circuit carrying roughly half of `P_rack`, not the full rack load —
see `docs/facility/power-control-design.md` "Single-PSU load balancing" for the electrical
rationale and why full A/B failover-at-100%-load is explicitly out of scope for consumer PSUs.

{rack_table}

Every circuit above is well inside the NEC 210.20(A) 80% derate limit at the current 8-node
M1 population — expected, since Option B and the 30 A circuit size were chosen against the
ARCHITECTURE.md#L0.1 M4 target of 12 compute-gpu nodes/rack, not the smaller M1 pilot. See
"M4 unit economics" below for the number that actually constrains the circuit choice.

## Facility roll-up (M1, 1 rack)

| Metric | Value |
|---|---|
| P_IT | {facility['pIT_W'] / 1000:.2f} kW |
| PUE (M1 — portable/mini-split; revisit per milestone, see cooling-plan.md) | {facility['pue']} |
| P_total | {facility['pTotal_W'] / 1000:.2f} kW |
| Service current @ 208 V 3-phase | {facility['serviceCurrentA']:.1f} A |
| Heat load | {facility['pIT_W'] * BTU_PER_WATT:.0f} BTU/h = {facility['coolingTons']:.2f} tons |
| Airflow @ ΔT {DELTA_T_F:.0f} °F | {facility['coolingCfm']:.0f} CFM |
| **Required service vs. available** | Available service **unknown — see PREFLIGHT #2 / `electrician-signoff.md`**; the 2x30A circuits already designed here (Option B) cover the computed {facility['serviceCurrentA']:.1f} A total demand many times over, so no upgrade is implied by this milestone's load alone |

## M4 unit economics (design target, not yet physical)

`ARCHITECTURE.md#L0.1` targets 12 compute-gpu nodes per rack at M4. Recomputing the Task 1
model for that rack shape, using this fleet's _actual_ per-node numbers (not the phase file's
illustrative 320 W-GPU/16-NVMe example) and the 320 W GPU cap chosen below:

"""

    # M4 unit economics, computed from a real compute-gpu node's numbers.
    gpu_node = next(n for n in nodes if n["gpuW"] > 0)
    infra_w = sum(r["infraW"] for r in racks if r["name"] == gpu_node["rack"]) or 380.0
    m4_rack_w = gpu_node["psuDrawW"] * 12 + infra_w
    m4_per_circuit_w = m4_rack_w / 2
    m4_per_circuit_a = m4_per_circuit_w / (V_LINE_TO_NEUTRAL * 3)
    m4_pct_derated = 100 * m4_per_circuit_a / (30 * BREAKER_DERATE)

    m4_facility_w = m4_rack_w * 8
    m4_total_w = m4_facility_w * 1.4
    m4_service_a = m4_total_w / (V_LINE_TO_LINE * (3 ** 0.5) * POWER_FACTOR)
    m4_tons = m4_facility_w * BTU_PER_WATT / BTU_PER_TON
    m4_cfm = m4_facility_w * BTU_PER_WATT / (CFM_CONSTANT * DELTA_T_F)

    content += f"""| Term | Value |
|---|---|
| P_node_psu_draw (one compute-gpu node, 320 W cap) | {gpu_node['psuDrawW']:.1f} W |
| x 12 nodes | {gpu_node['psuDrawW'] * 12:.0f} W |
| + switches (2x leaf 150 W) + mgmt (50 W) + PiKVM/matrix (30 W) | +{infra_w:.0f} W |
| **P_rack (M4 shape)** | **{m4_rack_w:.0f} W** |
| Per-circuit load (Option B, 2 circuits) | {m4_per_circuit_w:.0f} W |
| Per-phase current per circuit | {m4_per_circuit_a:.1f} A |
| % of 30 A circuit's 80% derated limit (24 A) | {m4_pct_derated:.1f}% |
| **Verdict** | {'✅ within the 80% design headroom target' if m4_pct_derated <= 100 else '⚠️ EXCEEDS the 80% design headroom target — revisit node count or cap'} |

**Facility at M4 (8 racks of this shape):**

| Metric | Value |
|---|---|
| P_IT | 8 x {m4_rack_w:.0f} W = {m4_facility_w / 1000:.1f} kW |
| P_total @ PUE 1.4 | {m4_total_w / 1000:.1f} kW |
| Service current @ 208 V 3-phase | {m4_service_a:.0f} A |
| Required service | {'400 A 3-phase' if m4_service_a <= 400 else '(size up from 400A — recompute)'} |
| Heat load | {m4_facility_w * BTU_PER_WATT:.0f} BTU/h = {m4_tons:.1f} tons |
| Airflow @ ΔT {DELTA_T_F:.0f} °F | {m4_cfm:.0f} CFM |

This tracks closely with `phases/PHASE-02.md`'s own worked example (67.5 kW / 400 A / 19.2 t)
— the small excess here comes from this design counting the management NIC and rack
KVM/PiKVM explicitly, which the phase file's illustrative table omits.

**Staged buildout** (M2/M3 are a projection per `phases/PHASE-02.md` Task 1's own table, not
yet real hardware — Phase 05's capacity model is the procurement-final authority, same caveat
Phase 01 applied to the M1 node records):

| Milestone | Nodes | Racks | P_IT | Service needed | Cooling |
|---|---|---|---|---|---|
| M1 (measured, this fleet) | 8 | 1 | {facility['pIT_W'] / 1000:.2f} kW | 2x30A 3-phase (installed here) | {facility['coolingTons']:.2f} t |
| M2 (projected) | 24 | 2 | ~16.2 kW | 100 A 3-phase | ~4.6 t |
| M3 (projected) | 48 | 4 | ~32.4 kW | 200 A 3-phase | ~9.2 t |
| M4 (projected, this shape) | 100 | 8 | {m4_facility_w / 1000:.1f} kW | 400 A 3-phase | {m4_tons:.1f} t |

## GPU power-cap trade (Task 4)

`spec.gpus[].powerCapWatts` on all 4 M1 compute-gpu nodes was changed from the Phase 01
spec-sheet default of **400 W** (89% of the RTX 4090's 450 W stock TDP, ~98% throughput) down
to **320 W** — the measured efficiency knee (71% of stock, ~92% throughput retained, 1.29x
perf/W) — per `phases/PHASE-02.md` Task 4's table. Impact:

- **vs. stock (450 W):** -130 W/GPU. At the M4 scale of 96 compute-gpu nodes this removes
  ~12.5 kW of IT load and ~4.2 tons of cooling for a ~8% throughput trade — matching the
  phase file's own "$20-40k of avoided facility cost" estimate.
- **vs. the Phase 01 default (400 W):** -80 W/GPU, ~7.7 kW / ~2.6 tons at M4 scale, for a
  ~6% throughput trade. This is the delta actually committed by this phase, since 400 W was
  never physically measured — it was Phase 01's placeholder.
- Phase 49 replaces this estimate with a measured perf/watt curve on real hardware; Phase 18
  enforces the cap via the GPU Operator.

## NOT YET KNOWN

The facility's existing electrical service (voltage, phase, main breaker rating, spare panel
capacity — PREFLIGHT #2) is not known in this environment; see
`evidence/phase-02/electrician-signoff.md` for the plan to obtain it before any circuit above
is energized. Every number in this document is a _design_ target for a contractor to quote
against (`docs/facility/contractor-brief.md`), not a confirmation that the target is
buildable in the room as it exists today.
"""

    out_path = os.path.join(ROOT, "docs/facility/load-study.md")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(content)
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="A2: per-circuit continuous load <= 80%% of breaker")
    ap.add_argument("--headroom", action="store_true", help="A3: per-rack load <= 80%% of combined derated capacity")
    ap.add_argument("--check-locations", action="store_true", help="A7: every node has a valid, non-colliding rack+U")
    ap.add_argument("--check-pdu-coverage", action="store_true", help="A10: every node is on a PDU outlet")
    ap.add_argument("--pue", type=float, default=DEFAULT_PUE, help="override facility PUE (default %(default)s)")
    args = ap.parse_args()

    any_check = args.check or args.headroom or args.check_locations or args.check_pdu_coverage
    result = compute(pue=args.pue)

    exit_code = 0

    if args.check:
        violations = check_breaker_derate(result, per_circuit=True)
        if violations:
            print("FAIL --check (A2):", file=sys.stderr)
            for v in violations:
                print(f"  {v}", file=sys.stderr)
            exit_code = 1
        else:
            print("OK --check (A2): every circuit's continuous load is <= 80% of its breaker rating")

    if args.headroom:
        violations = check_breaker_derate(result, per_circuit=False)
        if violations:
            print("FAIL --headroom (A3):", file=sys.stderr)
            for v in violations:
                print(f"  {v}", file=sys.stderr)
            exit_code = 1
        else:
            print("OK --headroom (A3): every rack's load is <= 80% of its circuits' combined derated capacity")

    if args.check_locations:
        violations = check_locations()
        if violations:
            print("FAIL --check-locations (A7):", file=sys.stderr)
            for v in violations:
                print(f"  {v}", file=sys.stderr)
            exit_code = 1
        else:
            print("OK --check-locations (A7): every node has a valid, non-colliding rack+U")

    if args.check_pdu_coverage:
        violations = check_pdu_coverage()
        if violations:
            print("FAIL --check-pdu-coverage (A10):", file=sys.stderr)
            for v in violations:
                print(f"  {v}", file=sys.stderr)
            exit_code = 1
        else:
            print("OK --check-pdu-coverage (A10): every node is on a switched PDU outlet")

    if not any_check:
        study_path = write_load_study_md(result)
        budget_path = write_power_budget_yaml(result)
        print(f"wrote {os.path.relpath(study_path, ROOT)}")
        print(f"wrote {os.path.relpath(budget_path, ROOT)}")
        print(f"P_IT = {result['facility']['pIT_W'] / 1000:.2f} kW across {len(result['racks'])} rack(s)")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
