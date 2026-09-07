# Phase 02 — Acceptance

Date: 2026-09-07
Operator: Claude Code (agent), for 0xdevabir

No physical facility exists yet — every number below comes from the real M1 pilot fleet
(`inventory/nodes/*.yaml`, unchanged from Phase 01 except the Task 4 power-cap edit) via
`tools/power-budget.py`, plus a design projection to the M4 target
(`ARCHITECTURE.md#L0.1`). Criteria requiring physical facility access are marked N/A or
DEFERRED with a dated plan, not fabricated — see `deviations.md`.

| # | Criterion | Verification | Result |
|---|---|---|---|
| A1 | Load study exists with per-node, per-rack, and facility totals | Read `docs/facility/load-study.md` | ✅ PASS — every Task 1 term present with a number, generated live from inventory |
| A2 | Every circuit's continuous load ≤ 80% of breaker rating | `python tools/power-budget.py --check` | ✅ PASS — exit 0 |
| A3 | Per-rack load ≤ 80% of the derated capacity | `python tools/power-budget.py --headroom` | ✅ PASS — exit 0 |
| A4 | Required service size stated and compared to available service | Read the study | ✅ PASS — explicit gap statement: available service unknown pending `electrician-signoff.md`; computed demand (17.0 A) is far below the 2x30A already designed |
| A5 | Cooling capacity ≥ 1.15x IT load, with N+1 from M2 | Read `cooling-plan.md` | ✅ PASS — 1.31t required vs. 1.5t chosen at M1; N+1 explicitly flagged as a not-yet-contracted requirement starting M2 |
| A6 | Airflow (CFM) computed and matched to equipment | Read `cooling-plan.md` | ✅ PASS — 633 CFM at M1, 10,750 CFM at M4, matched to equipment class (not a specific SKU — no physical unit purchased) |
| A7 | Rack layout assigns every node a rack+U, matching inventory | `python tools/power-budget.py --check-locations` | ✅ PASS — exit 0, no node unplaced, no U collision |
| A8 | Control nodes in ≥ 3 distinct racks | Query `inventory/nodes/` | ⚠️ N/A — only 1 rack (`r01`) exists at M1; all 3 control nodes are necessarily co-racked. Becomes a hard gate at M2 (`rack-layout.md`) |
| A9 | Storage nodes span ≥ 3 racks | Same | ⚠️ N/A — same reason; 1 storage node, 1 rack (matches Phase 01 finding F-05) |
| A10 | Every node maps to a switched-PDU outlet | `python tools/power-budget.py --check-pdu-coverage` | ✅ PASS — exit 0, 100% coverage |
| A11 | PDU model supports switching + metering + API | Read `power-control-design.md` | ✅ PASS — Raritan PX3 family named (+ 2 real alternates), all three capabilities confirmed as general product-line capabilities; exact SKU deferred to the quote per D3 |
| A12 | Four-layer power-control design complete | Read the doc | ✅ PASS — WoL, `talosctl shutdown`, switched-PDU cycle, PiKVM+matrix all specified with concrete mechanisms |
| A13 | `powerCapWatts` set for every GPU | `yq` across inventory | ✅ PASS — see below, no nulls |
| A14 | Safety doc addresses noise, fire, floor loading, EPO, condensate | Read it | ✅ PASS — all five present (several explicitly marked open pending the facility survey, not silently omitted) |
| A15 | Contractor brief exists and is self-contained | Read it | ✅ PASS — a contractor could quote scope, load, cooling, and constraints without asking further questions (the one open question, existing service, is itself the explicit ask of the survey) |
| A16 | Electrician sign-off obtained, or a dated plan to obtain it | `evidence/phase-02/electrician-signoff.md` | ✅ PASS (as a plan) — present, dated, explains why it is a plan not a result (D1) |
| A17 | CI enforces the power budget | Add an over-budget rack to a test branch | ✅ PASS — verified live below, not just asserted |

## A2/A3/A7/A10 — live tool output

```text
$ python3 tools/power-budget.py --check
OK --check (A2): every circuit's continuous load is <= 80% of its breaker rating
$ python3 tools/power-budget.py --headroom
OK --headroom (A3): every rack's load is <= 80% of its circuits' combined derated capacity
$ python3 tools/power-budget.py --check-locations
OK --check-locations (A7): every node has a valid, non-colliding rack+U
$ python3 tools/power-budget.py --check-pdu-coverage
OK --check-pdu-coverage (A10): every node is on a switched PDU outlet
```

## A13 — power-cap completeness

```text
$ for f in inventory/nodes/*.yaml; do
    yq -r '.spec.gpus[]?.powerCapWatts // "NULL-OR-NO-GPU"' "$f"
  done
320
320
320
320
NULL-OR-NO-GPU   # nx-m-r01-01 (control, no GPU — correct)
NULL-OR-NO-GPU   # nx-m-r01-02 (control, no GPU — correct)
NULL-OR-NO-GPU   # nx-m-r01-03 (control, no GPU — correct)
NULL-OR-NO-GPU   # nx-s-r01-08 (storage, no GPU — correct)
```

All 4 compute-gpu nodes: `powerCapWatts: 320` (changed from Phase 01's placeholder 400 in
this phase — Task 4, see `load-study.md` "GPU power-cap trade").

## A17 — CI gate proven live, not just asserted

Temporarily reduced `breakerAmps` from 30 to 3 in `inventory/power/circuits.yaml` (an
"over-budget rack" simulation, since this project has no CI branch to push a real PR to from
this environment) and re-ran the gates:

```text
$ python3 tools/power-budget.py --check
FAIL --check (A2):
  r01/r01-circuit-a: 5.56A is 231.8% of the derated 2.4000000000000004A limit
  r01/r01-circuit-b: 5.56A is 231.8% of the derated 2.4000000000000004A limit
exit=1

$ python3 tools/power-budget.py --headroom
FAIL --headroom (A3):
  r01: 4005.56W is 231.8% of combined derated capacity 1728W
exit=1
```

Restored `circuits.yaml` to `breakerAmps: 30` immediately after (confirmed via `git diff` —
no residual change) and re-verified both checks return to `OK`/exit 0. `tools/validate-inventory.sh`
runs all four `power-budget.py` checks as part of `task validate:inventory`, so a future PR
that adds a node without re-checking the breaker fails `task validate` in CI, per the Task 8
WHY note.

## Full validation suite

```text
$ task validate
✓ 8 nodes, circuits.yaml, pdu-map.yaml, power-budget.yaml, racks.yaml — schema valid
✓ power budget checks (A2/A3/A7/A10)
✓ inventory valid
Summary: 0 error(s)          # markdown
(yamllint: only pre-existing Taskfile.yaml warnings, unrelated to this phase)
(shellcheck: 0 findings)
no manifests yet — skipping
```

Exit code: 0.

## Summary

- Criteria passed outright: 15 / 17 (A1-A7, A10-A17)
- Criteria N/A, honestly reported (only 1 rack exists — not a hardware gap, a scale gap):
  A8, A9
- **Phase status: COMPLETE for everything achievable without a physical facility.** The one
  genuinely blocking real-world dependency — an electrician's site survey and sign-off
  before any circuit is energized — is tracked as a dated plan
  (`evidence/phase-02/electrician-signoff.md`), matching A16's own pass condition. See
  `handoff.md` for what the next session (or the electrician) must do first.
