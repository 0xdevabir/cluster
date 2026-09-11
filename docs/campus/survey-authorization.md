# Campus Survey Authorization

> **STATUS: TEMPLATE — NOT YET GRANTED.**
> PREFLIGHT #2 of `phases/PHASE-01B.md` requires written permission to
> *survey* (not harvest) each candidate room before any physical fieldwork
> begins. Nothing in this repository can substitute for that permission —
> it must come from the department head or equivalent authority for each
> room listed below. Do not fill this in with placeholder or assumed
> approval; an unauthorized survey is exactly the failure mode R-20
> describes, only earlier. See `evidence/phase-01B/BLOCKED.md` for the
> current gate status.

## Rooms requesting survey authorization

| Room ID | Building | Floor | Requested of | Status |
|---|---|---|---|---|
| `<REPLACE-ME>` | `<REPLACE-ME>` | `<REPLACE-ME>` | `<name/role>` | pending |

## Authorization record

Once granted, record per room:

- **Room:** `<lab id>`
- **Granted by:** `<name, role>` (role/title only — see R-20's data-minimization note)
- **Date:** `YYYY-MM-DD`
- **Scope:** survey only (read-only probing of one representative machine
  per configuration, room timetable, owner contact, uplink measurement)
  — **explicitly not** harvesting, enrollment, or netboot.
- **Evidence:** short email or written note, referenced (not pasted, if it
  contains anything beyond the above) — e.g. `docs/campus/agreements/<lab-id>.md`

## Next step once granted

1. Append the granting record above.
2. Update the corresponding `inventory/campus/labs.yaml` entry's
   `owner.contact_ref` to point at the agreement.
3. Proceed to Task 2 (`tools/campus/probe-harvest-node.sh`) only for rooms
   listed here as granted.
