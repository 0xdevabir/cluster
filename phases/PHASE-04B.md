# PHASE 04B — Consent, Governance & Acceptable Use

| | |
|---|---|
| **Stage** | C — Campus Harvest Plane |
| **Estimated effort** | 3–4 hours of writing, plus days-to-weeks of institutional lead time |
| **Depends on** | 04, 01B |
| **Blocks** | **03B, 08B, 14B, 19B, 25B, 31B, 33B, 36B, 52B — every phase that touches a machine** |
| **Risk** | 🔴 **The programme-ending risk.** R-19 and R-20 both live here |
| **Blast radius** | The entire campus plane, and the project's standing in the department |
| **Architecture refs** | `CAMPUS-FABRIC.md#9-consent-safety--the-human-contract`, `ULTIMATE-PLAN.md#121-harvest-plane-risks`, Laws XI–XII, Rule C1 |

---

## 🎯 MISSION

Obtain and record the **written agreements that make harvesting legitimate** — per-lab participation, network approval, data policy, and student notice — and encode the five promises in a form that later phases can check mechanically rather than remember culturally.

> ⚠️ **This is the hard barrier of Track C.** Until this phase passes for a given lab, that lab's machines may not be woken, netbooted, enrolled, or loaded. Not one, not "just for a test". `phases/README.md` Rule C1.

> 💡 **WHY a whole phase for paperwork.** The technical risk in this project is moderate; the political risk is fatal and asymmetric. A hundred flawless nights of harvesting buy nothing that one interrupted lecture cannot destroy. The agreement is not bureaucracy — it is the artifact that converts "some students are running something on our PCs" into "the department operates a sanctioned compute service." Those two sentences have very different outcomes when a professor finds a machine warm at 7am.

---

## ✅ PREFLIGHT

```bash
# 1. Phase 01B complete — you know which labs you are asking for, and what you would gain
task validate:campus
yq -r '.[] | select(.harvest_status=="surveyed") | .id' inventory/campus/labs.yaml

# 2. Phase 04 complete — the security architecture and trust zones exist,
#    so the data policy in this phase has something to reference
test -f docs/security/threat-model.md

# 3. You can identify, for each candidate lab, the person or role with the authority to say yes
#    (usually: lab in-charge → department head → IT director, in escalating order)

# 4. You have read CAMPUS-FABRIC.md §9 in full.
```

---

## 📦 DELIVERABLES

```
docs/campus/
  five-promises.md                  # the public contract, written for a non-technical reader
  participation-agreement.md        # THE template — one page, plain language
  agreements/
    <lab-id>.md                     # one signed record per participating lab
  network-approval.md               # campus IT sign-off: VLAN, ceilings, shaping
  data-policy.md                    # what may and may not run/store on shared machines
  student-notice.md                 # the sign posted in each participating lab
  withdrawal-procedure.md           # how a lab leaves, and how fast
  licensing-determination.md        # the GeForce EULA question, its status, and its owner
policies/campus/
  harvest-eligibility.yaml          # machine-readable: which labs are cleared, and for what
  kyverno-harvest-data.yaml         # blocks PII/regulated claims onto harvest nodes
tools/campus/
  check-consent.sh                  # CI gate: no enrolled lab lacks a signed agreement
evidence/phase-04B/
  preflight.md plan.md acceptance.md handoff.md deviations.md
  signed/                           # scans or signed emails, one per lab
```

---

## 📋 TASKS

### Task 1 — Write the five promises

**`docs/campus/five-promises.md`** — this is a public document. Write it for a professor, not for an engineer. Each promise names the mechanism that enforces it, so it can be audited rather than trusted.

| # | Promise | Enforced by | Verified by |
|---|---|---|---|
| 1 | **The human always wins.** Any sign of someone using the machine releases it within 10 seconds. No override, no "let this job finish". | Eviction path (31B), Law XI | Gate G16 — recorded eviction traces |
| 2 | **We leave no trace.** Nothing is installed on your Windows system, no disk space is used, no settings are changed except one BIOS boot-order entry — reversible in 30 seconds. | Diskless netboot (08B) | Gate: post-harvest disk hash unchanged |
| 3 | **We never degrade your network.** Our traffic is always shaped below teaching traffic, with a hard per-lab ceiling. | Uplink budget + shaping (03B) | Gate G18 — utilization reports |
| 4 | **We respect the hardware.** Power and thermal caps below manufacturer limits; no sustained load in rooms without adequate ventilation. | Power caps (19B), ventilation verdict (01B) | Per-lab thermal report |
| 5 | **You can leave instantly.** One request removes your lab. No notice period, no negotiation, no questions. | `harvest_status: withdrawn` → cordon within 5 min | Withdrawal drill (Task 6) |

> 💡 Promise 5 is the one that gets agreements signed. An owner who knows they can end it at any moment, unilaterally, has very little to lose by trying it. Make the exit genuinely trivial and say so first, not last.

### Task 2 — The participation agreement

**`docs/campus/participation-agreement.md`** — **one page.** If it runs to three, nobody reads it and you have consent in form only. It must state:

```
Lab:                    <building / room>
Responsible person:     <name, role>
Harvest windows:        <the specific hours — e.g. weekdays 18:30–07:30, weekends all day>
Blackout periods:       <exam weeks, specific dates, "any time I say so">
Excluded machines:      <e.g. the instructor console — always offer this>
Mode:                   Scheduled only  [ ]     Scheduled + opportunistic  [ ]   ← default is Scheduled only
Ventilation confirmed:  adequate / marginal / inadequate
Contact for problems:   <phone/email of the on-call project member>
Withdrawal:             This lab may be removed at any time by contacting the above.
                        Removal takes effect within 5 minutes and requires no reason.

The five promises (attached) form part of this agreement.

Signed: ______________________   Date: __________
```

**Mode B (opportunistic, during class hours) is unchecked by default and must not be offered on a first agreement.** Offer it only after four clean weeks of Mode A in that specific lab (`CAMPUS-FABRIC.md §6.2`).

Store the completed record as `docs/campus/agreements/<lab-id>.md` with the signed scan or the signed email thread in `evidence/phase-04B/signed/`.

### Task 3 — Network approval

Campus IT owns the network. Harvesting hundreds of machines that all PXE-boot, all pull images, and all hold cluster connections is a change to their network whether or not they notice it. **Get it in writing before 03B, not after an incident.**

**`docs/campus/network-approval.md`** must record their agreement to:

- the VLAN or segment the harvest nodes will occupy (03B designs it; they approve it)
- DHCP/PXE behavior on that segment, and which server is authoritative
- directed-broadcast or per-segment WoL relay for wake packets
- the per-lab uplink ceilings (40 % class hours / 70 % off-hours) and the shaping mechanism
- a named contact and an escalation path for "NEXUS is doing something to my network"
- our commitment to shut the fabric down on their request, immediately, without discussion

> ⚠️ **If campus IT declines, Track C is blocked at 03B, not here.** Record it, escalate through the department, and in the meantime the Core Plane track (`00`–`56`) is entirely unblocked. Do not proceed by not asking.

### Task 4 — The data policy

**`docs/campus/data-policy.md`** — a harvest node is a machine hundreds of students can physically touch. Classify accordingly (`CAMPUS-FABRIC.md §8.3`):

| Data class | Harvest plane | Enforcement |
|---|---|---|
| Public / de-identified datasets | ✅ Allowed | Lab cache, read-only, TTL'd |
| Container images | ✅ Allowed | Ephemeral; wiped every power cycle |
| Job checkpoints | ✅ Transient only | Encrypted at rest, flushed to Core, wiped on shutdown |
| Tenant-private datasets | ⚠️ Opt-in per tenant, encrypted | Namespace annotation; default **deny** |
| PII, student records, health, financial, anything regulated | ❌ **Never** | Kyverno blocks the claim; job rejected at admission |
| Long-lived secrets, kubeconfigs, cloud credentials | ❌ **Never** | Short-lived scoped node identity only (08B) |

**`policies/campus/kyverno-harvest-data.yaml`** must implement the last two rows as an admission policy, not as a documented expectation. A rule that only exists in a Markdown file will be violated by a well-meaning user in month two.

### Task 5 — Student notice

**`docs/campus/student-notice.md`** — an A4 sign for each participating lab. Plain language, both English and Bangla:

> *These computers help run the university's research computing service outside class hours. When you use this machine, it is yours alone — anything we were running stops immediately. Nothing we run touches your files or the Windows system. Questions or problems: <contact>.*

Transparency here is not decoration. A student who finds a machine behaving oddly and knows why files a question; one who does not files a rumour.

### Task 6 — Withdrawal procedure and drill

**`docs/campus/withdrawal-procedure.md`** and a **live drill**:

```bash
# Withdrawal must be one operation, and it must be fast.
yq -i '(.[] | select(.id=="cse-402") | .harvest_status) = "withdrawn"' inventory/campus/labs.yaml
git commit -am "chore(campus): withdraw cse-402 at owner request" && git push
# Argo CD reconciles → the lab's nodes are cordoned and drained.
```

**Drill it before the first lab signs**, using a lab-id that does not exist yet if necessary, and record the measured time from commit to full drain. Target: **≤ 5 minutes**. You are going to promise this in writing to people who will remember it — know the real number first.

### Task 7 — The licensing determination

**`docs/campus/licensing-determination.md`** carries R-03 forward from Phase 01, restated for the campus context (`CAMPUS-FABRIC.md §9.3`):

- **The question:** whether NVIDIA's GeForce driver licence terms regarding "datacenter deployment" apply to university-owned lab workstations used for teaching and research on university premises.
- **The honest position:** lab PCs in classrooms are a materially different fact pattern from consumer cards racked in a datacenter. That is a factual distinction, **not legal advice**, and this project does not provide legal advice.
- **The action:** request a written institutional determination through the university's procurement or legal office, and record its status here with a named owner and a date.
- **What is unblocked meanwhile:** CPU-only and integrated-GPU harvesting, which the clause does not touch. Discrete-GPU harvesting at scale (19B, 52B) waits on the determination.

Do not let this question stall the whole track, and do not quietly ignore it either. Record status, owner, date; revisit at 19B.

### Task 8 — Make consent machine-checkable

**`policies/campus/harvest-eligibility.yaml`**:

```yaml
labs:
  - id: cse-402
    agreementRef: docs/campus/agreements/cse-402.md
    signedOn: 2026-09-12
    windows:                       # authoritative; the Oracle may narrow these, never widen them
      - { day: [sun,mon,tue,wed,thu], from: "18:30", to: "07:30" }
      - { day: [fri,sat], from: "00:00", to: "23:59" }
    blackouts: [{ from: "2026-12-10", to: "2026-12-24" }]
    excludedMachines: [hv-cse402-01]     # instructor console
    modeB: false
    dataClasses: [public, checkpoint]
```

**`tools/campus/check-consent.sh`** — a CI gate that fails if:

- any lab with `harvest_status: enrolled|active` has no entry here, or its `agreementRef` file is missing
- any harvest window in `harvest-eligibility.yaml` extends beyond what the signed agreement states
- any machine in `excludedMachines` appears in an enrolled node list
- `modeB: true` for a lab with fewer than 28 days of clean Mode A history

This script is the mechanical embodiment of Rule C1. Wire it into CI and into 08B's preflight.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Command |
|---|---|---|
| 1 | ≥ 1 lab has a signed participation agreement with a scan/email in `evidence/phase-04B/signed/` | `ls evidence/phase-04B/signed/ \| wc -l` |
| 2 | Every signed agreement has a matching entry in `policies/campus/harvest-eligibility.yaml` | `tools/campus/check-consent.sh` |
| 3 | Network approval recorded, with a named contact and escalation path | manual review of `docs/campus/network-approval.md` |
| 4 | Kyverno policy blocking PII/regulated claims onto harvest nodes is written and unit-tested | `kyverno test policies/campus/` |
| 5 | Withdrawal drill executed and timed at ≤ 5 min, output recorded | `evidence/phase-04B/acceptance.md` |
| 6 | Student notice exists in English and Bangla | manual review |
| 7 | Licensing determination has a status, an owner, and a date — even if that status is "requested" | manual review |
| 8 | `check-consent.sh` runs in CI and fails a deliberately-introduced violation | commit a test violation, show the red build, revert |

---

## ↩️ ROLLBACK

There is nothing technical to roll back. If an agreement is withdrawn, execute Task 6's procedure and record it. **Never treat a withdrawn agreement as recoverable by persuasion** — the moment consent becomes something to be argued with, promise 5 is worthless and so is every other one.

---

## 🧯 TROUBLESHOOTING

| Symptom | Cause | Action |
|---|---|---|
| Lab owner hesitant about "someone else's programs on my PCs" | Reasonable | Lead with promise 5 (instant exit) and promise 2 (no trace). Offer a two-week trial on a subset of machines. |
| Owner wants to exclude the instructor console | Very common, always reasonable | `excludedMachines`. Offer it before they ask. |
| Campus IT wants a separate VLAN we did not plan for | Good outcome, actually | Take it. 03B is designed around whatever they approve; a dedicated segment simplifies shaping. |
| Department head defers to a committee | Institutional reality | Proceed with the labs that can approve directly; keep the committee informed. Do not stall the whole track on the slowest approver. |
| Someone proposes "let's just try it on a few PCs first, quietly" | The single most dangerous suggestion in this project | Refuse. Cite R-20 and Rule C1. Getting caught harvesting without consent costs more than every hour it would have gained. |
| Legal question stalls with no owner | Common | Assign a named owner and a review date. Proceed with CPU/iGPU work, which is unaffected. |

---

## 🚫 DO NOT

- **Do not enrol, wake, or netboot any machine.** That is 08B, and only after this phase passes for that specific lab.
- Do not offer Mode B on a first agreement.
- Do not write an agreement longer than one page.
- Do not promise anything you have not built the enforcement for. Every promise here is a gate later; if you cannot yet prove promise 1, say "we will demonstrate this before we start" rather than asserting it.
- Do not store personal contact details in Git — the agreement scan in `evidence/` is the record; `labs.yaml` carries roles and references.
- Do not treat a verbal yes as an agreement.
- Do not extend harvest windows beyond what was signed, even when the lab is visibly empty.

---

## 🤝 HANDOFF — write `evidence/phase-04B/handoff.md`

Must state:

- Which labs are cleared, for what windows, with what exclusions and what mode.
- The measured withdrawal time from the drill.
- Campus IT's named contact, the approved VLAN/segment, and any conditions they attached — 03B builds directly on this.
- The licensing determination's status, owner, and review date.
- Which labs declined or deferred, and whether re-approach is appropriate later.
- The date of the first agreement, since Mode B eligibility is counted from it.
