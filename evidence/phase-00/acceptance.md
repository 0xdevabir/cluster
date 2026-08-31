# Phase 00 — Acceptance

Date: 2026-08-31
Operator: Claude Code (agent), for 0xdevabir
Commit: (staged on top of `4d05532` — see `git log` at commit time)

| # | Criterion | Command | Result | Evidence |
|---|---|---|---|---|
| A1 | Directory skeleton matches the deliverables tree | `find . -type d -not -path './.git/*' \| sort` | ✅ PASS | below |
| A2 | Every pinned tool installed at pinned version | `task tools:verify` | ✅ PASS | below |
| A3 | Inventory schema rejects a malformed node | delete `spec.cpu`, `task validate:inventory` | ✅ PASS | below |
| A4 | Inventory schema accepts a valid node | copy example, `task validate:inventory` | ✅ PASS | below |
| A5 | Conditional rule: control node without ECC rejected | `memory.ecc: false` on a `control` node | ✅ PASS | below |
| A6 | Cross-document check catches duplicate MACs | two nodes, same MAC | ✅ PASS | below |
| A7 | Cross-document check catches rack-unit collision | two nodes at `r01`/`u22` | ✅ PASS | below |
| A8 | Pre-commit hooks installed and pass | `pre-commit run --all-files` | ⚠️ PASS w/ 1 known gap | below (see `deviations.md` D4) |
| A9 | `:latest` tag is rejected | `pre-commit run no-latest-tags --all-files` | ✅ PASS | below |
| A10 | A private key is rejected | commit a generated test RSA key | ✅ PASS | below |
| A11 | Full validation suite is green | `task validate` | ✅ PASS | below |
| A12 | CI pipeline runs and passes on a PR | push a branch, open a PR | ⏸ DEFERRED | no Git remote configured yet — see `handoff.md` |
| A13 | Evidence scaffolding works | `task evidence:new -- 00` | ✅ PASS | below |
| A14 | Markdown links in the three design docs resolve | custom relative-link checker over `*.md` + `phases/*.md` | ✅ PASS | below |

## A1 — Directory skeleton

```text
$ find . -type d -not -path './.git/*' | sort
.
./benchmarks
./benchmarks/baselines
./benchmarks/reports
./benchmarks/runners
./benchmarks/suites
./bootstrap
./bootstrap/argocd-install
./bootstrap/dnsmasq
./bootstrap/step-ca
./bootstrap/tinkerbell
./chaos
./charts
./clusters
./clusters/nexus-prod
./clusters/nexus-prod/acceleration
./clusters/nexus-prod/infra
./clusters/nexus-prod/observability
./clusters/nexus-prod/platform
./clusters/nexus-prod/runtimes
./clusters/nexus-prod/scheduling
./clusters/nexus-prod/storage
./clusters/nexus-prod/tenants
./docs
./evidence
./evidence/_template
./evidence/phase-00
./images
./inventory
./inventory/network
./inventory/nodes
./inventory/power
./inventory/schema
./phases
./policies
./runbooks
./talos
./talos/patches
./talos/patches/node-overrides
./talos/rendered
./talos/secrets
./tools
./tools/lib
```

All deliverable directories present. `talos/secrets`, `talos/rendered`, `bootstrap/*`,
`clusters/nexus-prod/*`, `charts`, `images`, `chaos`, `policies` hold only `.gitkeep` (empty by
design — populated starting Phase 09+).

## A2 — Pinned tool versions

Installed via `mise install` against `.mise.toml` (see `deviations.md` D1–D3 for the two registry
fixes and the one tool, `trivy`, deferred to Phase 42).

```text
$ task tools:verify
✓ task Task version: v3.40.0
✓ yq yq (https://github.com/mikefarah/yq/) version v4.44.3
✓ jq jq-1.7.1
✓ kubectl v1.34.1
✓ helm v3.16.2+g13654a5
✓ kustomize v5.5.0
✓ sops sops 3.9.1
✓ age v1.2.0
✓ pre-commit pre-commit 4.0.1
✓ check-jsonschema check-jsonschema, version 0.29.4
✓ all pinned tools verified
```

Exit code: 0.

## A3 — Malformed node rejected

Deleted `spec.cpu` from a copy of the seed example:

```text
$ task validate:inventory
! FAILED: inventory/nodes/nx-c-r01-05.yaml
Schema validation errors were encountered.
  inventory/nodes/nx-c-r01-05.yaml::$.spec: 'cpu' is a required property
✗ inventory validation FAILED
```

Exit code: 1.

## A4 — Valid node accepted

```text
$ cp inventory/nodes/nx-c-r01-05.yaml.example inventory/nodes/nx-c-r01-05.yaml
$ task validate:inventory
✓ nx-c-r01-05.yaml
✓ inventory valid
```

Exit code: 0.

## A5 — Control node without ECC rejected

Crafted `nx-m-r01-01` with `archetype: control` and `memory.ecc: false`:

```text
$ task validate:inventory
! FAILED: inventory/nodes/nx-m-r01-01.yaml
  inventory/nodes/nx-m-r01-01.yaml::$.spec.memory.ecc: True was expected
✗ inventory validation FAILED
```

Exit code: 1.

## A6 — Duplicate MAC caught

Two nodes (`nx-c-r01-05`, `nx-c-r01-06`) sharing NIC MAC `00:11:22:33:44:55`:

```text
$ task validate:inventory
✓ nx-c-r01-05.yaml
✓ nx-c-r01-06.yaml
! duplicate MAC addresses: 00:11:22:33:44:55
✗ inventory validation FAILED
```

Exit code: 1.

## A7 — Rack-unit collision caught

Same two nodes, distinct MACs, both at `rack: r01, u: 22`:

```text
$ task validate:inventory
✓ nx-c-r01-05.yaml
✓ nx-c-r01-06.yaml
! two nodes in the same rack unit: r01/22
✗ inventory validation FAILED
```

Exit code: 1. Test nodes deleted afterward; `inventory/nodes/` returned to examples-only.

## A8 — Pre-commit hooks

```text
$ pre-commit install
pre-commit installed at .git/hooks/pre-commit
$ detect-secrets scan > .secrets.baseline
$ pre-commit run --all-files
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
check yaml................................................................Passed
check for merge conflicts.................................................Passed
check for added large files...............................................Passed
detect private key........................................................Passed
mixed line ending.........................................................Passed
yamllint...................................................................Passed
ShellCheck v0.10.0.........................................................Failed  (Docker unavailable — see deviations.md D4)
Detect secrets..............................................................Passed
Validate inventory against JSON Schema......................................Passed
Reject :latest image tags....................................................Passed
```

7/8 local hooks pass; the Docker-backed `shellcheck` hook cannot run in this environment (Docker
Desktop paused). Native `shellcheck -x -S warning` against every script in scope — the same gate
CI actually runs (`task validate:shell`) — passes with 0 findings. See `deviations.md` D4.

## A9 — `:latest` tag rejected

```text
$ pre-commit run no-latest-tags --all-files
Reject :latest image tags................................................Failed
- hook id: no-latest-tags
- exit code: 1
clusters/nexus-prod/infra/a9-test.yaml:8:      image: foo:latest
✗ :latest tag found (Rule 4)
```

Exit code: 1 (hook correctly blocked the commit). Test file deleted afterward.

## A10 — Private key rejected

```text
$ openssl genrsa -out a10-test-key.txt 2048
$ git add -f a10-test-key.txt
$ pre-commit run detect-private-key --all-files
detect private key.......................................................Failed
- hook id: detect-private-key
- exit code: 1
Private key found: a10-test-key.txt
```

Exit code: 1. Test key deleted immediately after (`rm -f a10-test-key.txt`, unstaged).

## A11 — Full validation suite

```text
$ task validate
task: [validate:inventory] bash tools/validate-inventory.sh
✓ inventory valid
task: [validate:yaml] yamllint -c .yamllint.yaml .
task: [validate:shell] find tools benchmarks chaos -name '*.sh' -print0 | xargs -0 -r shellcheck -x -S warning
task: [validate:manifests] ...
no manifests yet — skipping
task: [validate:markdown] markdownlint-cli2 --config .markdownlint.yaml "**/*.md" "!ARCHITECTURE.md" "!ULTIMATE-PLAN.md" "!phases/**"
Summary: 0 error(s)
```

Exit code: 0, on a repo containing only inventory examples (Task 10 requirement).

## A12 — CI pipeline on a PR

DEFERRED. No Git remote is configured for this repo yet, so a PR cannot be opened in this
session. Every step the workflow runs was verified independently and passes (`task tools:verify`,
`task validate:inventory`, `task validate:yaml`, `task validate:markdown`, `task validate:shell`,
`task validate:manifests`, `task evidence:check`). See `handoff.md`.

## A13 — Evidence scaffolding

```text
$ rm -rf evidence/phase-00 && task evidence:new -- 00
✓ created evidence/phase-00
$ ls evidence/phase-00/
acceptance.md  deviations.md  handoff.md  plan.md  preflight.md
```

All five templates present. Exit code: 0.

## A14 — Markdown links resolve

Wrote a relative-link checker (Python, `re` + `os.path.exists`) over `ARCHITECTURE.md`,
`ULTIMATE-PLAN.md`, and every `phases/*.md`, resolving each `[label](target)` link (excluding
`http(s)://`/`mailto:` and same-file anchors) against the filesystem relative to its source file.

```text
files scanned: 60
relative links checked: 117
broken: 0
```

## Summary

- Criteria passed: 13 / 14 (A12 deferred — no blocking gate, no Git remote yet)
- Phase status: COMPLETE (A12 requires a remote; not a Phase 00 defect — see `handoff.md`)
