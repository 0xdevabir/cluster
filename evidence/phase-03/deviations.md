# Phase 03 — Deviations

Date: 2026-09-11
Operator: agent (session_01883KZe15yE4EHBCxZ8aNgj), for 0xdevabir

## D1 — M1 topology uses the already-racked MLAG pair, not a single leaf

- **What the file said:** `phases/PHASE-03.md` Task 2's staged topology table shows M1 (8 nodes) as "1× 32-port 100 G leaf + 1× 48-port 1 G mgmt — Single switch, no spine tier needed," with MLAG introduced at M2.
- **What was found:** `inventory/racks.yaml` and `evidence/phase-02/handoff.md` (item 3/5) already show **two** leaf switches racked at M1 — `r01-leaf-a` and `r01-leaf-b`, an MLAG pair — because Phase 02 designed the rack with dual-corded power redundancy in mind, ahead of the network phase's own staged assumption.
- **What was done:** Designed M1's network around the actual 2-leaf MLAG pair rather than a fictional single leaf. All 8 hosts remain single-homed to `r01-leaf-a` (no host is dual-connected yet); `r01-leaf-b` carries only the MLAG peer-link. Noted as an open gap in the Task 8 failure analysis: losing `r01-leaf-a` today isolates all 8 hosts, since MLAG protects the peer-link and future dual-homed hosts, not today's single-homed ones.
- **Why:** Rule 6 — reality wins. Designing around a switch that doesn't exist, or ignoring one that does, would both be less useful than documenting the actual M1 hardware and its actual (partial) redundancy.

## D2 — Upstream connectivity left as `<REPLACE-ME>`

- **What the file said:** Task 3's `ip-plan.yaml` example includes literal `upstream.gateway`/`dns`/`ntp` values.
- **What was found:** Phase 02's facility survey (electrician sign-off, existing service confirmation) is not yet complete — the actual site's upstream gateway, DNS, and NTP servers are unknown.
- **What was done:** Left `upstream.gateway`/`dns`/`ntp` as `<REPLACE-ME>` placeholders (the project's own convention for "generate/obtain a real value, do not commit a guess" — `phases/README.md` §4.2), and documented this as an open item rather than fabricating plausible-looking IPs.
- **Why:** Rule 5 — fabricated evidence is a catastrophic failure that every later phase would trust. A wrong upstream gateway is exactly the kind of silently-wrong assumption that surfaces as a mysterious outage in Phase 07 (DHCP/DNS bring-up).

## D3 — Buffer-size finding raised, not resolved

- **What the file said:** Task 4 flags "shared buffer ≥ 32 MB" as a purchasing criterion; Troubleshooting says a switch with < 32 MB should either be accepted with a documented risk or replaced.
- **What was found:** The already-racked SN2410 leaves have 16 MB total buffer — below the computed ≥ 36 MB target for a fully-populated 32-port leaf at single-hop BDP.
- **What was done:** Documented the finding in `docs/network/roce-contract.md` rather than silently passing A10, and noted it is not a current risk at M1's actual 8-port population. Recorded as a hard M2+ purchasing criterion.
- **Why:** The switches are already purchased and racked (a Phase 02 decision, out of this phase's scope to reverse); the honest path is to flag the constraint for future capacity planning rather than mark A10 an unqualified pass.
