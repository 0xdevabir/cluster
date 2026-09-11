# Phase 01B — Deviations

Date: 2026-09-11
Operator: agent (session_01883KZe15yE4EHBCxZ8aNgj)

## D1 — Field survey not performed; scaffolding only

- **What the file said:** Task 1–5 describe surveying real campus rooms and
  probing real machines, producing `inventory/campus/{labs,machines,timetable,uplinks}.yaml`
  with actual field data.
- **What was found:** PREFLIGHT #2 (written survey authorization) is not
  satisfied — no department head has granted permission for any room, and
  an AI agent cannot obtain or fabricate that permission on the user's
  behalf.
- **What was done:** Implemented every piece of tooling, schema, validation,
  and documentation the phase specifies, using `.example` stub files (same
  convention as `inventory/racks.yaml.example`) instead of real survey data.
  Wrote `BLOCKED.md` per the phase's own instruction for this exact
  situation.
- **Why:** The phase file explicitly treats unauthorized surveying as the
  R-20 failure mode "only earlier," and instructs writing `BLOCKED.md`
  rather than proceeding. Fabricating authorization or survey data would
  violate both that instruction and the phase's data-honesty requirements
  (`probeConfidence` must not lie about what was actually measured).
