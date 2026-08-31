# Phase 00 — Plan

Date: 2026-08-31
Operator: Claude Code (agent), for 0xdevabir

1. Task 1 — directory skeleton → `inventory/`, `talos/`, `bootstrap/`, `clusters/nexus-prod/*`, `charts/`, `images/`, `benchmarks/*`, `chaos/`, `runbooks/`, `policies/`, `docs/`, `tools/lib/`, `evidence/_template/`, `.github/workflows/` (+ `.gitkeep` in empty dirs)
2. Task 2 — `.gitignore`, `.gitattributes`, `.editorconfig`
3. Task 3 — `.mise.toml` pinned toolchain
4. Task 4 — `Taskfile.yaml` with `validate`, `tools:*`, `evidence:*`, `inventory:*` namespaces
5. Task 5 — `inventory/schema/{nodespec,rack,network,power}.schema.json` (rack/network/power stubbed with `TODO(phase-NN)`)
6. Task 6 — `tools/{lib/common.sh,validate-inventory.sh,verify-tools.sh,new-phase-evidence.sh,check-evidence.sh,render-node-table.sh,inventory-stats.sh,check-no-latest-tag.sh}`
7. Task 7 — `.yamllint.yaml`, `.markdownlint.yaml`, `.pre-commit-config.yaml`
8. Task 8 — `.github/workflows/validate.yaml`
9. Task 9 — `evidence/README.md`, `evidence/_template/*.md`, `CONTRIBUTING.md`, `CODEOWNERS`
10. Task 10 — seed `inventory/nodes/nx-c-r01-05.yaml.example` + rack/network/power examples; run `task validate`

Decision: local workstation lacks `mise`/`task`/`yq`/etc. pre-installed. Installed `mise` via
Homebrew (already present) and used it to install the pinned toolchain per `.mise.toml`, rather
than substituting unpinned Homebrew-latest versions for validation. See `deviations.md` for the
two tool-registry fixes this required.
