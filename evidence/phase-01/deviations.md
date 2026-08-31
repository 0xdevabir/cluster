# Phase 01 — Deviations

Date: 2026-08-31
Operator: Claude Code (agent), for 0xdevabir

## D1 — Tasks 3, 4, 5 not performed: no physical hardware exists

**What the file said:** Task 3 ("Run discovery on every machine"), Task 4 (runtime
capability probes: P2P, PCIe link-under-load, GPUDirect RDMA), and Task 5 (BIOS/UEFI
standardization pass) all require physical or SSH access to a running machine.

**What I found:** This project has no physical fleet yet — it is a from-scratch
design/build exercise, and Phase 00 confirmed no hardware exists (only design docs and
tooling). The phase file's own preflight text anticipates exactly this case: *"If you have
zero machines available yet: you can still complete Tasks 1, 2, 7, and 8 ... using the
specification-sheet values for machines you intend to buy. Mark every such record
`spec.status: planned` and re-run discovery when hardware arrives."*

**What I did:** Completed Tasks 1, 2, 6, 7, 8 in full. Built 8 `status: planned` NodeSpecs
for the M1 pilot fleet (`ULTIMATE-PLAN.md §9`) using realistic spec-sheet values (AMD Ryzen
9 7950X + RTX 4090 for compute-gpu, Intel Xeon E-2488 + ECC for control/storage, ConnectX-6
NICs, Samsung PM9A3 for storage's enterprise NVMe). Did **not** fabricate `collect.sh`
output, probe results, or BIOS records — each node's `evidence/phase-01/raw/<node>/NOTE.md`
says explicitly that discovery has not run and gives the exact command to run it later.

**Why:** Rule 5 ("A phase with fabricated evidence is a catastrophic failure") outweighs
completing the phase's every task. Tasks 3–5 physically cannot be done without a machine;
inventing plausible-looking discovery output would poison every downstream phase's trust in
`inventory/`.

## D2 — `yq`'s jq-style `add` is not supported by mikefarah/yq

**What I found:** The Phase 00-authored `tools/inventory-stats.sh` used
`yq -r '[.spec.gpus[].vramGB] | add // 0'` to sum array values. This is jq syntax; the
pinned `yq` (`v4.53.6`, mikefarah/yq — a Go reimplementation, not jq) has no `add` operator
and throws `Error: 1:25: lexer: invalid input text "add // 0"`. It happened to go unnoticed
in Phase 00 because the only example node in inventory at the time contributed non-zero
GPU/storage totals through a different code path, or the error was silently swallowed.

**What I did:** Rewrote every `| add // 0` aggregation in `tools/inventory-stats.sh` to
extract the raw values with `yq` and sum them with `awk '{s+=$1} END{print s+0}'` instead.

**Why:** `add` genuinely does not exist in this yq dialect — this is a latent bug in
Phase 00's deliverable, not a Phase 01 hardware issue, but it blocked Task 7's fleet-summary
generation so it had to be fixed here.

## D3 — `tools/inventory-stats.sh` used bash associative arrays; macOS system bash is 3.2

**What I found:** My first draft of the extended `tools/inventory-stats.sh` used
`declare -A` for per-archetype and per-GPU-model counts. This workstation's `/bin/bash` is
the Apple-shipped 3.2.57 (no newer bash is installed via `mise`/Homebrew per Phase 00's
`handoff.md`), which does not support associative arrays (`declare: -A: invalid option`).

**What I did:** Rewrote the aggregation to use temp files + `sort`/`uniq`/`awk` instead of
associative arrays, matching the portable style already used by the other Phase 00 scripts
(`validate-inventory.sh`, `render-node-table.sh`).

**Why:** Every later phase's tooling must run on this same shell; silently requiring a
newer bash would be an undocumented environment assumption.

## D4 — Two nodes literally named `nx-c-r01-05`

**What I found:** `inventory/nodes/nx-c-r01-05.yaml.example` (Phase 00's seed example) and
the newly created `inventory/nodes/nx-c-r01-05.yaml` (a real planned M1 pilot node) share
the same base name. `validate-inventory.sh` skips `*.example` files, so there is no
collision for validation purposes, and the name is legitimately reused as intended by the
Phase 00 example (`ARCHITECTURE.md#x3` names this exact slot as its worked example).

**What I did:** Left the `.example` file untouched (Phase 00 deliverable) and used the same
node name for slot `r01-05`'s real planned record, since it is in fact the fourth GPU node
in this rack under the naming scheme.

**Why:** No conflict exists; noting it here only so a future reader isn't confused by two
similarly-named files in the same directory.
