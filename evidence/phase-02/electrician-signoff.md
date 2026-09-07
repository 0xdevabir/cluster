# Phase 02 — Electrician Sign-off

Date: 2026-09-07
Operator: Claude Code (agent), for 0xdevabir
Status: **NOT OBTAINED — dated plan to obtain it, per A16.**

## Why this is a plan, not a sign-off

`phases/PHASE-02.md` PREFLIGHT requires knowing the facility's existing electrical service
before any circuit design proceeds, and its DANGER banner requires "a licensed electrician
sign off before energizing anything." This project has no physical facility in this
environment — there is nothing to survey and no one to sign off on real circuits yet. Per
Rule 5 (no fabricated evidence — the same standard Phase 01 applied to hardware discovery,
`evidence/phase-01/deviations.md` D1), this document is the plan to close that gap, not a
substitute for the real survey.

## What has been produced without the survey

Everything achievable on paper against the M1 pilot fleet and the M4 design target:

- `docs/facility/load-study.md` — full Task 1 electrical/thermal model, generated live from
  inventory by `tools/power-budget.py`.
- `docs/facility/cooling-plan.md`, `rack-layout.md`, `power-control-design.md`,
  `acoustic-and-safety.md` — Tasks 2, 3, 5, 6.
- `docs/facility/contractor-brief.md` — Task 7, the document meant to be handed to the
  electrician/HVAC contractor as the starting point for their survey and quote.
- `inventory/racks.yaml`, `inventory/power/{circuits,pdu-map,power-budget}.yaml` and their
  schemas — Task 8.

## What cannot be produced without the survey

- Confirmation that a 208 V 3-phase service (or any specific voltage/phase) is actually
  present at the site, and that spare panel capacity exists for the 2x30 A circuits designed
  in `load-study.md`.
- Floor-loading verification (`inventory/racks.yaml` `floorLoadRatingKg: null`).
- Panel location, conduit routing, EPO placement, arc-flash assessment.
- Any of the items in `docs/facility/acoustic-and-safety.md` marked "open."
- The three electrical and three HVAC quotes required by R-14.

## Plan to obtain sign-off

1. Hand `docs/facility/contractor-brief.md` to a licensed electrician and an HVAC contractor
   (Task 7's explicit ask: "please quote, and please tell us what we got wrong").
2. Electrician performs the panel/service survey named in PREFLIGHT #2 and confirms or
   revises the M1 circuit design (2x30 A 3-phase) and the M4 feeder sizing (400 A 3-phase).
3. Collect three quotes each for electrical and HVAC scope (R-14).
4. Electrician provides written sign-off before any circuit is energized — this file is
   replaced with that sign-off (or a reference to it) at that point.
5. Until step 4, `docs/facility/load-study.md`, `cooling-plan.md`, and
   `power-control-design.md` remain design targets only — no purchasing, conduit pulling, or
   energizing proceeds on their numbers alone.

**No target date is set** — this is gated on external contractor availability, which this
project cannot schedule from inside a design/build exercise. The next session picking up
this phase should treat "engage an electrician" as the literal next physical-world action
item, parallel to Phase 01's "rack the first machine" handoff item.
