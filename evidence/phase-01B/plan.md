# Phase 01B — Plan

Date: 2026-09-11
Operator: agent (session_01883KZe15yE4EHBCxZ8aNgj)

Scaffolding-only pass (field survey blocked — see `BLOCKED.md`).

1. Define lab/machine/timetable records → `inventory/schema/{lab,harvestnode,timetable}.schema.json`
2. Non-invasive probe script → `tools/campus/probe-harvest-node.sh`
3. Timetable schema → covered by step 1
4. Uplink record shape (no dedicated schema per phase file; validated in step 6) → `inventory/campus/uplinks.yaml.example`
5. Capacity rollup → `tools/campus/survey-stats.sh`, wired as `task campus:stats`
6. Validation gate → `tools/campus/validate-campus.sh`, wired as `task validate:campus` (added to `task validate` deps)
7. Doc templates → `docs/campus/{survey-authorization,survey-method,fleet-report}.md`
8. `.example` stub data for all four `inventory/campus/*.yaml` files (real data requires the field survey, gated on P2)
