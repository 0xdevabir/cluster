# Contributing to NEXUS

## Branch strategy

`main` is protected. All work happens on `phase/NN-slug` branches (e.g.
`phase/01-hardware-inventory`), merged via pull request.

## Commit conventions

Conventional commits, scoped to the phase:

```text
<type>(phase-NN): <imperative summary>

<what changed and why, 1–3 lines>

Evidence: evidence/phase-NN/<file>
Refs: ARCHITECTURE.md#<section>
```

Types: `feat` · `fix` · `docs` · `chore` · `perf` · `refactor` · `test` · `bench`

## Pull requests

Every PR must:

1. Link its evidence directory (`evidence/phase-NN/`) in the description.
2. Pass `task validate` and all CI checks.
3. Include an ADR in `ARCHITECTURE.md` (Architecture Decision Records section)
   for any deviation from the documented architecture — this is Law II
   (Declarative or Dead) and Rule 6 (reality wins, but you write it down)
   from `phases/README.md`.

## Evidence discipline

`evidence/phase-NN/` is append-only proof-of-work. Fabricated evidence is the
single worst failure mode in this project (`phases/README.md` Rule 5) —
every later phase trusts that earlier phases' evidence is real.

## Local validation

```bash
mise install
task tools:verify
task validate
pre-commit install
```
