# Evidence

`evidence/phase-NN/` is the proof-of-work directory for each phase. Its
contents are **append-only history** — never edit a past phase's evidence
to make it look better after the fact. If new information changes a
conclusion, add it, dated, rather than rewriting the old entry.

Fabricated evidence is the single worst failure mode in this project
(`phases/README.md` Rule 5): every later phase trusts that earlier phases'
evidence is real. A phase with fabricated evidence is a catastrophic
failure — it silently poisons everything built on top of it.

Each phase directory contains:

| File | Purpose |
|---|---|
| `preflight.md` | Output of every preflight command, captured verbatim |
| `plan.md` | The ordered task list before execution (~10 lines) |
| `acceptance.md` | Every acceptance criterion, with the exact command and real output |
| `deviations.md` | Anywhere reality contradicted the phase file, and what was done about it |
| `handoff.md` | What the next phase needs to know that isn't already written down |

Scaffold a new phase's evidence directory with `task evidence:new -- NN`.
Verify every completed phase has complete evidence with `task evidence:check`.
