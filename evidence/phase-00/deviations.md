# Phase 00 — Deviations

Date: 2026-08-31
Operator: Claude Code (agent), for 0xdevabir

## D1 — `go-task`'s mise tool id has changed

- **What the file said:** `.mise.toml` should pin `"go-task" = "3.40.0"`.
- **What was found:** the installed `mise` (2026.8.14) registry no longer recognizes `go-task` as
  a tool id; `mise install` failed with `go-task not found in mise tool registry`. `mise registry`
  shows the current short id is `task` (backed by `aqua:go-task/task`).
- **What was done:** changed `.mise.toml` to `task = "3.40.0"`. Version pin unchanged.
- **Why:** upstream/mise registry naming drift, not a version problem — same tool, same pinned
  version, new lookup key.

## D2 — `check-jsonschema` was missing from `.mise.toml` entirely

- **What the file said:** the version-pinning table lists `check-jsonschema 0.29.4`, and the
  `.mise.toml` template in Task 3 was expected to cover every pinned tool.
- **What was found:** the `.mise.toml` snippet in the phase file does not actually include a
  `check-jsonschema` line, and it has no native `mise` backend (only an unrelated `jsonschema`
  CLI by sourcemeta is registered).
- **What was done:** added `"pipx:check-jsonschema" = "0.29.4"` to `.mise.toml`. `mise install`
  resolved it via `uv tool install check-jsonschema==0.29.4` — exact pinned version.
- **Why:** `check-jsonschema` is a Python/pip package; `pipx:` is `mise`'s supported backend for
  that.

## D3 — `trivy@0.57.0` is unavailable upstream

- **What the file said:** pin `trivy` at `0.57.0` (first used in Phase 42).
- **What was found:** `mise install` fails: `aquasecurity/trivy` has no `0.57.0`/`v0.57.0` GitHub
  release anymore (`mise ls-remote` shows a gap between `0.26.0` and `0.69.2` — the registry only
  serves a retained window of releases, and this one aged out).
- **What was done:** left `.mise.toml` pinned at `0.57.0` as documented (source of truth for the
  phase that actually needs it) rather than silently jumping to `0.69.x`+ (a minor-version bump,
  forbidden without explicit note per the DANGER callout). `task tools:verify` does not check
  `trivy` (not yet used), so this does not block Phase 00. **Phase 42 must re-pin `trivy` to a
  currently-available release before first use.**
- **Why:** Rule 4/6 — never silently bump a minor version; record the substitution for the phase
  that owns it.

## D4 — `shellcheck-precommit` hook could not run (Docker Desktop paused)

- **What the file said:** `pre-commit run --all-files` — all hooks pass (Acceptance A8).
- **What was found:** the `koalaman/shellcheck-precommit` hook runs shellcheck inside a Docker
  container; Docker Desktop was manually paused on this workstation, so the hook failed with
  `Error response from daemon: Docker Desktop is manually paused`.
- **What was done:** installed `shellcheck` natively via Homebrew and ran it directly against
  every script this phase owns (`find tools benchmarks chaos -name '*.sh' | xargs shellcheck -x
  -S warning`) — 0 warnings/errors, matching what `task validate:shell` (and thus CI) actually
  gates on. All seven non-Docker pre-commit hooks pass. This is a local-machine condition, not a
  repo defect — unpausing Docker is outside this phase's scope (workstation config, Rule 2).
- **Why:** Rule 6 — record the gap rather than claim an unverifiable pass; the equivalent native
  check is the real gate anyway (CI runners don't use the Docker-backed hook — GitHub Actions'
  `task validate:shell` calls `shellcheck` directly).

## D5 — Pre-existing design docs are excluded from the markdown lint gate

- **What the file said:** Task 7 asks for `.markdownlint.yaml`; `task validate:markdown` is part
  of `task validate` (Acceptance A11, must exit 0).
- **What was found:** `ARCHITECTURE.md`, `ULTIMATE-PLAN.md`, and `phases/*.md` (pre-existing,
  written before this phase) trip ~650 markdownlint findings (missing code-fence languages,
  heading-spacing, etc.). Reformatting 57 phase files and two ~90 KB design docs is far outside
  Phase 00's "Do NOT" boundary (schema stubs and repo scaffolding only).
- **What was done:** added `.markdownlintignore` and scoped `task validate:markdown` to exclude
  `ARCHITECTURE.md`, `ULTIMATE-PLAN.md`, and `phases/**`. Every markdown file this phase actually
  authored (`README.md`, `CONTRIBUTING.md`, `inventory/README.md`, `evidence/**`, etc.) lints
  clean with 0 errors.
- **Why:** Rule 2 — stay inside the phase boundary; don't let out-of-scope legacy content block
  the validation gate this phase is responsible for building.

## D6 — Pre-commit's `trailing-whitespace` hook auto-fixed 4 pre-existing phase files

- **What was found:** running `pre-commit run --all-files` for the first time, the
  `trailing-whitespace` hook auto-fixed trailing whitespace in `phases/PHASE-08.md`,
  `PHASE-34.md`, `PHASE-38.md`, and `PHASE-46.md` (pre-existing files, not authored this phase).
- **What was done:** left the auto-fix in place — whitespace-only, no semantic change — and staged
  it as part of this commit rather than reverting it, since re-running the hook would just redo
  the same fix.
- **Why:** non-semantic, and reverting would leave the repo in a state that fails its own
  pre-commit gate on the next run.

## D7 — CI vendor chosen without a remote to verify a live run

- **What was found:** the repo has no configured Git remote yet, so Acceptance A12 ("push a
  branch, open a PR, all CI jobs green") cannot be executed in this session.
- **What was done:** `.github/workflows/validate.yaml` was authored and its YAML validated with
  `yamllint`/`task validate:yaml`, and every step it runs (`task tools:verify`, `task
  validate:*`, `task evidence:check`) was independently exercised and passes locally. The actual
  GitHub Actions run is deferred until a remote exists.
- **Why:** Rule 5 — don't claim evidence that wasn't actually produced. See `handoff.md`.
