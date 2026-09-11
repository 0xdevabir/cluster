# Phase 01B — BLOCKED (field survey)

Date: 2026-09-11
Operator: agent (session_01883KZe15yE4EHBCxZ8aNgj)

PREFLIGHT #2 of `phases/PHASE-01B.md` requires written permission from each
candidate room's department head (or equivalent) to *survey* it, before any
physical fieldwork begins. No such authorization exists yet — see
`docs/campus/survey-authorization.md`, currently a template with no rooms
granted.

## What this means

- All schema, tooling, validation, and documentation scaffolding for this
  phase is implemented and ready (see `handoff.md`).
- **No physical survey has been performed.** `inventory/campus/*.yaml`
  contain only `.example` stub files, not real room or machine data.
- Nothing has been probed, photographed, or measured on campus equipment.

## To unblock

1. Obtain written authorization (a short email from each room's department
   head/lab in-charge naming the room is sufficient at this stage) and
   record it in `docs/campus/survey-authorization.md`.
2. Re-run preflight; proceed to Task 1 onward only for rooms with recorded
   authorization.

This file should be deleted once authorization is on file and real survey
data begins landing in `inventory/campus/`.
