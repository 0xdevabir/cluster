# Phase 01B — Acceptance

Date: 2026-09-11
Operator: agent (session_01883KZe15yE4EHBCxZ8aNgj)
Commit: (pending)

| # | Criterion | Command | Result | Evidence |
|---|---|---|---|---|
| A1 | Every candidate lab has owner/timetable/uplink/ventilation record | `task validate:campus` | ❌ N/A — no real labs surveyed | see `BLOCKED.md` |
| A2 | ≥1 `probed` machine per lab config with raw evidence | `yq '...probeConfidence=="probed"...'` | ❌ N/A — no machines probed | — |
| A3 | Every machine has measured memory + MAC | `tools/campus/validate-campus.sh` | ❌ N/A — no machine records | — |
| A4 | Uplink ceilings computed, none exceeds policy | `tools/campus/validate-campus.sh` | ❌ N/A — no uplink records | — |
| A5 | `fleet-report.md` states GPU-h/week and core-h/week with assumptions | manual review | ⏳ Template only | `docs/campus/fleet-report.md` |
| A6 | Zero writes to any lab machine's internal disk | `evidence/phase-01B/acceptance.md` attestation | ✅ N/A — no machine touched | probe script has no write path (see `survey-method.md`) |
| A7 | No personal data beyond role/institutional contact stored | `grep -rniE 'phone\|nid\|personal\|@gmail' inventory/campus/` | ✅ PASS | `.example` stubs contain no personal data |

## Summary

- Criteria passed: 2 / 7 (A6, A7 — the two that don't require field data)
- Phase status: **INCOMPLETE**
- What remains and why: A1–A5 require a completed, authorized field survey
  (PREFLIGHT #2), which has not happened — see `BLOCKED.md`. All tooling,
  schemas, and validation this phase specifies are implemented and ready
  to run against real data once the survey is authorized and performed.
