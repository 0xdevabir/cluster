# Phase 01B — Handoff

Date: 2026-09-11
Operator: agent (session_01883KZe15yE4EHBCxZ8aNgj)

## What's implemented (ready to use)

- Schemas: `inventory/schema/lab.schema.json`, `harvestnode.schema.json`, `timetable.schema.json`
- `.example` stub records: `inventory/campus/{labs,machines,timetable,uplinks}.yaml.example`
- `tools/campus/probe-harvest-node.sh` — run on the live-USB-booted target machine
- `tools/campus/survey-stats.sh` — capacity rollup, wired as `task campus:stats`
- `tools/campus/validate-campus.sh` — schema + cross-reference gate, wired as `task validate:campus` (now a dependency of `task validate`)
- `docs/campus/{survey-authorization,survey-method,fleet-report}.md` — templates

## What's NOT done (needs a human, on-site)

- **No survey authorization exists.** `docs/campus/survey-authorization.md`
  has zero granted rooms. Nothing further can proceed until a department
  head grants written permission per room — see `BLOCKED.md`.
- No real `inventory/campus/*.yaml` (non-`.example`) files exist yet — only
  stubs, so `task validate:campus` currently validates nothing but passes.
- No machine has been probed; `evidence/phase-01B/raw/` does not exist yet.

## Next steps for whoever picks this up

1. Get written authorization for each candidate room; record it in
   `docs/campus/survey-authorization.md`.
2. Rename `inventory/campus/*.yaml.example` → `*.yaml` and replace stub
   content with real surveyed records as rooms are visited.
3. Run `tools/campus/probe-harvest-node.sh <lab> <machine>` on-site per
   Task 2; commit raw output to `evidence/phase-01B/raw/<machine-id>.txt`.
4. `task validate:campus` and `task campus:stats` once real data lands.
5. Fill in `docs/campus/fleet-report.md` with the real rollup and its
   assumptions clause (acceptance criterion 5 — mandatory).
6. Delete `evidence/phase-01B/BLOCKED.md` once P2 is satisfied.
