# PHASE 00 — Repository Foundation & Toolchain

| | |
|---|---|
| **Stage** | 0 — Foundation & Design |
| **Estimated effort** | 2–3 hours |
| **Depends on** | Nothing |
| **Blocks** | Every other phase |
| **Risk** | 🟢 Low — no hardware, no cluster, no destructive operations |
| **Blast radius** | Local repository only |
| **Architecture refs** | `ARCHITECTURE.md#x4--repository-architecture`, `ARCHITECTURE.md#x3--naming-labeling--namespace-taxonomy` |

---

## 🎯 MISSION

Create the repository skeleton, the pinned toolchain, the validation harness, and the conventions that **every one of the following 56 phases depends on**. When this phase is done, any later phase can drop a file into a known place, run `task validate`, and get a trustworthy pass/fail.

> 💡 **WHY this is Phase 00 and not an afterthought:** at 100 nodes the difference between a maintainable platform and an unmaintainable one is decided here. A repo without schema validation accumulates malformed inventory. A repo without pinned tools produces "works on my machine" cluster configs. A repo without an evidence convention produces phases that *claim* to be done. Spend the three hours.

---

## ✅ PREFLIGHT

Run every command. All must pass. Record output in `evidence/phase-00/preflight.md`.

```bash
# 1. Git is available and the repo exists
git --version                                    # expect >= 2.40
git rev-parse --show-toplevel                    # expect the repo root path

# 2. The two design documents exist and are readable
test -f ULTIMATE-PLAN.md  && echo "OK: ULTIMATE-PLAN.md"
test -f ARCHITECTURE.md   && echo "OK: ARCHITECTURE.md"
test -f phases/README.md  && echo "OK: phases/README.md"

# 3. A POSIX shell and core utilities
command -v bash sed awk grep find xargs tar

# 4. Internet access (needed to fetch tools; note if air-gapped)
curl -sSfI https://github.com >/dev/null && echo "OK: network"

# 5. Working directory is clean
git status --porcelain            # expect empty or only the three docs above
```

**If any check fails:** write `evidence/phase-00/BLOCKED.md` and stop.

---

## 📦 DELIVERABLES

```
.editorconfig
.gitignore
.gitattributes
.mise.toml                              # pinned tool versions
Taskfile.yaml                           # the single task runner entry point
.pre-commit-config.yaml
.markdownlint.yaml
.yamllint.yaml
CONTRIBUTING.md
CODEOWNERS
.github/workflows/validate.yaml         # (or .gitlab-ci.yml — pick one, note it)
.github/pull_request_template.md

inventory/
  README.md
  schema/
    nodespec.schema.json
    rack.schema.json
    network.schema.json
    power.schema.json
  nodes/.gitkeep
  racks.yaml.example
  network/
    ip-plan.yaml.example
    vlans.yaml.example
    switch-ports.yaml.example
  power/
    pdu-map.yaml.example
    circuits.yaml.example

talos/.gitkeep
bootstrap/.gitkeep
clusters/nexus-prod/.gitkeep
charts/.gitkeep
images/.gitkeep
benchmarks/{suites,runners,baselines,reports}/.gitkeep
chaos/.gitkeep
runbooks/README.md
policies/.gitkeep
docs/README.md
evidence/README.md
evidence/_template/{preflight.md,plan.md,acceptance.md,deviations.md,handoff.md}
tools/
  validate-inventory.sh
  render-node-table.sh
  new-phase-evidence.sh
  lib/common.sh
```

---

## 🔧 VERSION PINNING

These are the tools every later phase assumes. Pin them in `.mise.toml`.

| Tool | Version | Purpose | First used in |
|---|---|---|---|
| `task` (go-task) | `3.40.0` | Task runner | 00 |
| `yq` | `4.44.3` | YAML processing | 00 |
| `jq` | `1.7.1` | JSON processing | 00 |
| `kubectl` | `1.34.1` | Kubernetes CLI | 12 |
| `helm` | `3.16.2` | Chart rendering | 15 |
| `kustomize` | `5.5.0` | Manifest overlays | 15 |
| `talosctl` | `1.9.5` | Talos control | 09 |
| `argocd` | `2.13.2` | GitOps CLI | 15 |
| `cilium-cli` | `0.16.20` | Cilium ops | 13 |
| `sops` | `3.9.1` | Secret encryption | 10 |
| `age` | `1.2.0` | Encryption keys | 10 |
| `kubeconform` | `0.6.7` | Manifest schema validation | 00 |
| `kyverno-cli` | `1.13.1` | Policy testing | 16 |
| `pre-commit` | `4.0.1` | Git hooks | 00 |
| `check-jsonschema` | `0.29.4` | Inventory validation | 00 |
| `hadolint` | `2.12.0` | Dockerfile lint | 42 |
| `trivy` | `0.57.0` | Vulnerability scanning | 42 |
| `cosign` | `2.4.1` | Image signing | 42 |

> ⚠️ **DANGER — version drift:** if a listed version is gone from upstream, take the nearest patch of the same minor and record it in `deviations.md`. Never bump a minor version silently; Kubernetes-adjacent tooling breaks across minors.

---

## 📋 TASKS

### Task 1 — Create the directory skeleton

```bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

mkdir -p \
  inventory/{schema,nodes,network,power} \
  talos/{secrets,patches/node-overrides,rendered} \
  bootstrap/{tinkerbell,dnsmasq,step-ca,argocd-install} \
  clusters/nexus-prod/{infra,acceleration,storage,scheduling,runtimes,platform,observability,tenants} \
  charts images \
  benchmarks/{suites,runners,baselines,reports} \
  chaos runbooks policies docs tools/lib \
  evidence/_template \
  .github/workflows

# Git does not track empty directories
find inventory talos bootstrap clusters charts images benchmarks chaos policies tools \
  -type d -empty -exec touch {}/.gitkeep \;
```

🧪 **VERIFY:** `find . -type d -not -path './.git/*' | sort` matches the deliverables tree.

---

### Task 2 — `.gitignore`, `.gitattributes`, `.editorconfig`

**`.gitignore`**
```gitignore
# Secrets — NEVER commit these
*.key
*.pem
!**/ca.pem
*.decrypted.*
*-secret.yaml
!*-secret.enc.yaml
.env
.env.*
!.env.example
age.key
talosconfig
kubeconfig
*.kubeconfig

# Rendered artifacts (regenerate, never edit)
talos/rendered/*
!talos/rendered/.gitkeep

# Tooling
.task/
.venv/
__pycache__/
node_modules/
*.log
*.tmp
.DS_Store

# Large benchmark payloads — commit summaries, not raw traces
benchmarks/reports/**/*.nsys-rep
benchmarks/reports/**/*.qdrep
benchmarks/reports/**/raw/
```

**`.gitattributes`**
```gitattributes
* text=auto eol=lf
*.sh   text eol=lf
*.yaml text eol=lf
*.yml  text eol=lf
*.json text eol=lf
*.md   text eol=lf
*.png  binary
*.enc.yaml  diff=sops
```

**`.editorconfig`**
```ini
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 2

[*.md]
trim_trailing_whitespace = false
max_line_length = off

[*.{sh,bash}]
indent_size = 2

[Makefile]
indent_style = tab
```

🔒 **SECRET:** the `.gitignore` above is a *safety net*, not a policy. Rule 9 still applies: never write a plaintext secret to disk inside the repo, even temporarily.

---

### Task 3 — `.mise.toml` (pinned toolchain)

```toml
# .mise.toml — the single source of truth for tool versions.
# Install everything:  mise install
# Verify:              task tools:verify
[tools]
"go-task"                        = "3.40.0"
yq                               = "4.44.3"
jq                               = "1.7.1"
kubectl                          = "1.34.1"
helm                             = "3.16.2"
kustomize                        = "5.5.0"
sops                             = "3.9.1"
age                              = "1.2.0"
"aqua:siderolabs/talos"          = "1.9.5"     # talosctl
"aqua:argoproj/argo-cd"          = "2.13.2"
"aqua:cilium/cilium-cli"         = "0.16.20"
"aqua:yannh/kubeconform"         = "0.6.7"
"aqua:kyverno/kyverno"           = "1.13.1"
"aqua:hadolint/hadolint"         = "2.12.0"
"aqua:aquasecurity/trivy"        = "0.57.0"
"aqua:sigstore/cosign"           = "2.4.1"
python                           = "3.12"
pre-commit                       = "4.0.1"

[env]
NEXUS_CLUSTER   = "nexus-prod"
NEXUS_ROOT      = "{{config_root}}"
KUBECONFIG      = "{{config_root}}/.secrets/kubeconfig"
TALOSCONFIG     = "{{config_root}}/.secrets/talosconfig"
```

> 💡 **WHY mise and not asdf/nix:** mise is a single static binary, reads one declarative file, and supports both language runtimes and arbitrary release binaries via aqua/ubi backends. If your environment mandates Nix, a `flake.nix` with the same pins is an acceptable substitution — record it in `deviations.md`.

---

### Task 4 — `Taskfile.yaml` (the single entry point)

Every later phase adds tasks here. Establish the namespaces now.

```yaml
version: '3'

vars:
  ROOT:
    sh: git rev-parse --show-toplevel
  CLUSTER: '{{.NEXUS_CLUSTER | default "nexus-prod"}}'

tasks:
  default:
    desc: List available tasks
    cmds: [task --list]

  # ─────────────────────────── validation ───────────────────────────
  validate:
    desc: Run every validation gate (this is what CI runs)
    deps: [validate:inventory, validate:yaml, validate:markdown, validate:shell, validate:manifests]

  validate:inventory:
    desc: Validate all inventory YAML against JSON Schema
    cmds:
      - bash tools/validate-inventory.sh

  validate:yaml:
    desc: Lint all YAML
    cmds:
      - yamllint -c .yamllint.yaml .

  validate:markdown:
    desc: Lint all Markdown
    cmds:
      - markdownlint-cli2 --config .markdownlint.yaml "**/*.md"

  validate:shell:
    desc: Lint all shell scripts
    cmds:
      - find tools benchmarks chaos -name '*.sh' -print0 | xargs -0 -r shellcheck -x

  validate:manifests:
    desc: Validate Kubernetes manifests against the API schema
    cmds:
      - |
        if [ -d clusters ] && find clusters -name '*.yaml' -type f | grep -q .; then
          find clusters -name '*.yaml' -type f -print0 \
            | xargs -0 kubeconform -strict -summary -ignore-missing-schemas \
                -kubernetes-version 1.34.1 \
                -schema-location default \
                -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{`{{.Group}}`}}/{{`{{.ResourceKind}}`}}_{{`{{.ResourceAPIVersion}}`}}.json'
        else
          echo "no manifests yet — skipping"
        fi

  # ─────────────────────────── tooling ───────────────────────────
  tools:verify:
    desc: Assert every pinned tool is installed at the pinned version
    cmds:
      - bash tools/verify-tools.sh

  # ─────────────────────────── evidence ───────────────────────────
  evidence:new:
    desc: 'Scaffold evidence for a phase: task evidence:new -- 07'
    cmds:
      - bash tools/new-phase-evidence.sh {{.CLI_ARGS}}

  evidence:check:
    desc: Assert every completed phase has complete evidence
    cmds:
      - bash tools/check-evidence.sh

  # ─────────────────────────── inventory ───────────────────────────
  inventory:table:
    desc: Render a human-readable node table from inventory/
    cmds:
      - bash tools/render-node-table.sh

  inventory:stats:
    desc: Aggregate fleet totals (cores, VRAM, storage, power)
    cmds:
      - bash tools/inventory-stats.sh

  # ─────────────── namespaces reserved for later phases ───────────────
  # talos:*      (phase 09)    cluster:*   (phase 12)
  # gitops:*     (phase 15)    gpu:*       (phase 18)
  # net:*        (phase 21)    storage:*   (phase 24)
  # sched:*      (phase 30)    bench:*     (phase 48)
  # chaos:*      (phase 54)
```

🚫 **DO NOT** add tasks for later phases now. Each phase adds its own.

---

### Task 5 — Inventory JSON Schemas

This is the highest-leverage artifact in the phase: it makes malformed hardware data **impossible to commit**.

**`inventory/schema/nodespec.schema.json`**
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://nexus.io/schema/nodespec.json",
  "title": "NEXUS NodeSpec",
  "type": "object",
  "required": ["apiVersion", "kind", "metadata", "spec"],
  "additionalProperties": false,
  "properties": {
    "apiVersion": { "const": "nexus.io/v1" },
    "kind": { "const": "NodeSpec" },
    "metadata": {
      "type": "object",
      "required": ["name"],
      "additionalProperties": false,
      "properties": {
        "name": {
          "type": "string",
          "pattern": "^nx-[cusmi]-r[0-9]{2}-[0-9]{2}$",
          "description": "nx-<archetype letter>-<rack>-<slot>; see ARCHITECTURE.md#x3"
        },
        "labels": { "type": "object", "additionalProperties": { "type": "string" } }
      }
    },
    "spec": {
      "type": "object",
      "required": ["archetype", "location", "cpu", "memory", "nics", "storage", "firmware"],
      "additionalProperties": false,
      "properties": {
        "archetype": { "enum": ["control", "compute-gpu", "compute-cpu", "storage", "infra"] },
        "status":    { "enum": ["planned", "racked", "provisioning", "active", "quarantine", "failed", "retired"], "default": "planned" },
        "location": {
          "type": "object",
          "required": ["site", "rack", "u", "pdu"],
          "additionalProperties": false,
          "properties": {
            "site": { "type": "string" },
            "room": { "type": "string" },
            "rack": { "type": "string", "pattern": "^r[0-9]{2}$" },
            "u":    { "type": "integer", "minimum": 1, "maximum": 48 },
            "pdu":  { "type": "array", "minItems": 1, "items": { "type": "string", "pattern": "^r[0-9]{2}-pdu-[ab]:[0-9]{1,2}$" } }
          }
        },
        "cpu": {
          "type": "object",
          "required": ["model", "sockets", "cores", "threads", "numaNodes"],
          "additionalProperties": false,
          "properties": {
            "model":     { "type": "string" },
            "sockets":   { "type": "integer", "minimum": 1 },
            "cores":     { "type": "integer", "minimum": 1 },
            "threads":   { "type": "integer", "minimum": 1 },
            "numaNodes": { "type": "integer", "minimum": 1 },
            "l3CacheMB": { "type": "integer" },
            "ccxCount":  { "type": "integer" },
            "baseClockGHz": { "type": "number" },
            "tdpWatts":  { "type": "integer" }
          }
        },
        "memory": {
          "type": "object",
          "required": ["totalGB", "ecc"],
          "additionalProperties": false,
          "properties": {
            "totalGB":  { "type": "integer", "minimum": 8 },
            "type":     { "type": "string" },
            "ecc":      { "type": "boolean" },
            "channels": { "type": "integer" },
            "speedMTs": { "type": "integer" }
          }
        },
        "gpus": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["index", "model", "vramGB", "pcieBusId", "pcieGen", "pcieWidth", "numaAffinity", "tdpWatts"],
            "additionalProperties": false,
            "properties": {
              "index":             { "type": "integer", "minimum": 0 },
              "model":             { "type": "string" },
              "uuid":              { "type": "string" },
              "vramGB":            { "type": "integer", "minimum": 1 },
              "computeCapability": { "type": "string", "pattern": "^[0-9]+\\.[0-9]+$" },
              "pcieBusId":         { "type": "string", "pattern": "^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\\.[0-9]$" },
              "pcieGen":           { "type": "integer", "minimum": 1, "maximum": 6 },
              "pcieWidth":         { "enum": ["x1", "x2", "x4", "x8", "x16"] },
              "numaAffinity":      { "type": "integer", "minimum": -1 },
              "nvlink":            { "type": "boolean", "default": false },
              "p2pCapable":        { "type": "boolean", "default": false },
              "gpudirectRdma":     { "type": "boolean", "default": false },
              "migCapable":        { "type": "boolean", "default": false },
              "eccSupported":      { "type": "boolean", "default": false },
              "tdpWatts":          { "type": "integer" },
              "powerCapWatts":     { "type": "integer" }
            }
          }
        },
        "nics": {
          "type": "array",
          "minItems": 1,
          "items": {
            "type": "object",
            "required": ["name", "speedGbps", "macAddress", "role"],
            "additionalProperties": false,
            "properties": {
              "name":       { "type": "string" },
              "model":      { "type": "string" },
              "macAddress": { "type": "string", "pattern": "^([0-9a-f]{2}:){5}[0-9a-f]{2}$" },
              "speedGbps":  { "type": "integer", "enum": [1, 2, 10, 25, 40, 50, 100, 200, 400] },
              "role":       { "enum": ["management", "cluster", "storage", "rdma", "unused"] },
              "pcieBusId":  { "type": "string" },
              "pcieWidth":  { "enum": ["x1", "x2", "x4", "x8", "x16"] },
              "numaAffinity": { "type": "integer", "minimum": -1 },
              "rdma": {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "capable":  { "type": "boolean" },
                  "protocol": { "enum": ["RoCEv1", "RoCEv2", "InfiniBand", "iWARP"] },
                  "device":   { "type": "string", "pattern": "^mlx[0-9]_[0-9]+$" }
                }
              },
              "sriov": {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "capable":        { "type": "boolean" },
                  "maxVfs":         { "type": "integer" },
                  "configuredVfs":  { "type": "integer" }
                }
              },
              "switchPort": { "type": "string", "pattern": "^r[0-9]{2}-(leaf|mgmt)-[ab]:[a-zA-Z0-9/]+$" }
            }
          }
        },
        "storage": {
          "type": "array",
          "minItems": 1,
          "items": {
            "type": "object",
            "required": ["device", "sizeGB", "class", "plp", "role", "tier"],
            "additionalProperties": false,
            "properties": {
              "device": { "type": "string", "pattern": "^/dev/(nvme[0-9]+n[0-9]+|sd[a-z]+)$" },
              "model":  { "type": "string" },
              "serial": { "type": "string" },
              "sizeGB": { "type": "integer", "minimum": 1 },
              "class":  { "enum": ["consumer-nvme", "enterprise-nvme", "consumer-sata-ssd", "enterprise-sata-ssd", "hdd"] },
              "plp":    { "type": "boolean" },
              "role":   { "enum": ["boot", "scratch", "osd-data", "osd-waldb", "mayastor", "etcd", "unused"] },
              "tier":   { "type": "integer", "minimum": 0, "maximum": 4 },
              "dwpd":   { "type": "number" }
            }
          }
        },
        "firmware": {
          "type": "object",
          "required": ["bios", "iommu", "wol"],
          "additionalProperties": false,
          "properties": {
            "bios":          { "type": "string" },
            "agesa":         { "type": "string" },
            "me":            { "type": "string" },
            "secureBoot":    { "type": "boolean" },
            "iommu":         { "type": "boolean" },
            "resizableBar":  { "type": "boolean" },
            "above4gDecode": { "type": "boolean" },
            "wol":           { "type": "boolean" },
            "vpro":          { "type": "boolean", "default": false },
            "pxeFirst":      { "type": "boolean" }
          }
        },
        "power": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "psuWatts":       { "type": "integer" },
            "measuredIdleW":  { "type": "integer" },
            "measuredPeakW":  { "type": "integer" },
            "budgetWatts":    { "type": "integer" }
          }
        },
        "benchmarks": { "type": "object", "additionalProperties": { "type": "number" } },
        "notes": { "type": "string" }
      },
      "allOf": [
        {
          "if":   { "properties": { "archetype": { "const": "compute-gpu" } } },
          "then": { "required": ["gpus"], "properties": { "gpus": { "minItems": 1 } } }
        },
        {
          "if":   { "properties": { "archetype": { "const": "control" } } },
          "then": { "properties": { "memory": { "properties": { "ecc": { "const": true } } } } }
        }
      ]
    }
  }
}
```

> 💡 **WHY the conditional `allOf` rules:** they encode architecture decisions as *validation*. A `control` node without ECC now fails CI instead of being discovered during an incident (`ULTIMATE-PLAN.md §6.1`, R-07).

Create `rack.schema.json`, `network.schema.json`, and `power.schema.json` with the same rigor — the fields are defined in Phases 02 and 03; for now stub them with `additionalProperties: true` and a `TODO(phase-02)` / `TODO(phase-03)` comment so validation does not block.

---

### Task 6 — Validation tooling

**`tools/lib/common.sh`**
```bash
#!/usr/bin/env bash
# Shared helpers. Source this; do not execute it.
set -euo pipefail

readonly C_RED='\033[0;31m' C_GRN='\033[0;32m' C_YEL='\033[0;33m' C_RST='\033[0m'

ROOT="$(git rev-parse --show-toplevel)"
readonly ROOT

log()  { printf '%s\n' "$*" >&2; }
ok()   { printf "${C_GRN}✓${C_RST} %s\n" "$*" >&2; }
warn() { printf "${C_YEL}!${C_RST} %s\n" "$*" >&2; }
die()  { printf "${C_RED}✗${C_RST} %s\n" "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "required tool not found: $1"; }
```

**`tools/validate-inventory.sh`**
```bash
#!/usr/bin/env bash
# Validate every inventory document against its JSON Schema.
# Exit non-zero on the FIRST failure — CI must be unambiguous.
source "$(dirname "$0")/lib/common.sh"
need check-jsonschema
need yq

fail=0
shopt -s nullglob

validate_dir() {
  local dir="$1" schema="$2" f
  [[ -d "$ROOT/$dir" ]] || return 0
  for f in "$ROOT/$dir"/*.yaml; do
    [[ "$(basename "$f")" == *.example ]] && continue
    if check-jsonschema --schemafile "$ROOT/inventory/schema/$schema" "$f" >/dev/null 2>&1; then
      ok "$(basename "$f")"
    else
      warn "FAILED: $f"
      check-jsonschema --schemafile "$ROOT/inventory/schema/$schema" "$f" || true
      fail=1
    fi
  done
}

validate_dir "inventory/nodes"   "nodespec.schema.json"
validate_dir "inventory/network" "network.schema.json"
validate_dir "inventory/power"   "power.schema.json"

# Cross-document integrity checks — schema cannot express these
log ""; log "── cross-document checks ──"

# C1: every node name is unique (filename == metadata.name)
dupes=$(find "$ROOT/inventory/nodes" -name '*.yaml' -exec yq -r '.metadata.name' {} \; 2>/dev/null | sort | uniq -d)
[[ -z "$dupes" ]] || { warn "duplicate node names: $dupes"; fail=1; }

# C2: filename matches metadata.name
while IFS= read -r -d '' f; do
  want="$(basename "$f" .yaml)"; got="$(yq -r '.metadata.name' "$f")"
  [[ "$want" == "$got" ]] || { warn "filename/name mismatch: $f (name=$got)"; fail=1; }
done < <(find "$ROOT/inventory/nodes" -name '*.yaml' -print0 2>/dev/null)

# C3: every MAC address is globally unique across the fleet
macdupes=$(find "$ROOT/inventory/nodes" -name '*.yaml' -exec yq -r '.spec.nics[].macAddress' {} \; 2>/dev/null | sort | uniq -d)
[[ -z "$macdupes" ]] || { warn "duplicate MAC addresses: $macdupes"; fail=1; }

# C4: every (rack,u) location is occupied at most once
locdupes=$(find "$ROOT/inventory/nodes" -name '*.yaml' \
  -exec yq -r '"\(.spec.location.rack)/\(.spec.location.u)"' {} \; 2>/dev/null | sort | uniq -d)
[[ -z "$locdupes" ]] || { warn "two nodes in the same rack unit: $locdupes"; fail=1; }

# C5: exactly one boot device per node
while IFS= read -r -d '' f; do
  n=$(yq -r '[.spec.storage[] | select(.role=="boot")] | length' "$f")
  [[ "$n" == "1" ]] || { warn "$(basename "$f"): expected exactly 1 boot device, found $n"; fail=1; }
done < <(find "$ROOT/inventory/nodes" -name '*.yaml' -print0 2>/dev/null)

[[ $fail -eq 0 ]] && ok "inventory valid" || die "inventory validation FAILED"
```

**`tools/verify-tools.sh`** — asserts each pinned tool resolves to the pinned version; fails loudly with the mismatch. **`tools/new-phase-evidence.sh`** — copies `evidence/_template/` to `evidence/phase-NN/` and stamps the date. **`tools/check-evidence.sh`** — for every `evidence/phase-NN/` that exists, assert `preflight.md`, `acceptance.md`, and `handoff.md` are present and non-empty.

Make all scripts executable: `chmod +x tools/*.sh`.

---

### Task 7 — Lint configuration

**`.yamllint.yaml`**
```yaml
extends: default
rules:
  line-length: { max: 160, level: warning }
  comments: { min-spaces-from-content: 1 }
  document-start: disable
  truthy: { allowed-values: ['true', 'false', 'on', 'off'], check-keys: false }
  indentation: { spaces: 2, indent-sequences: consistent }
  braces: { max-spaces-inside: 1 }
  brackets: { max-spaces-inside: 1 }
ignore: |
  talos/rendered/
  charts/**/templates/
  .task/
```

**`.markdownlint.yaml`**
```yaml
default: true
MD013: false      # line length — tables and diagrams need width
MD024: { siblings_only: true }
MD033: false      # inline HTML is allowed in docs
MD041: false      # first line need not be a top-level heading
MD046: { style: fenced }
```

**`.pre-commit-config.yaml`**
```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
        args: [--allow-multiple-documents]
        exclude: '^(charts/.*/templates/|talos/rendered/)'
      - id: check-merge-conflict
      - id: check-added-large-files
        args: ['--maxkb=1024']
      - id: detect-private-key
      - id: mixed-line-ending
        args: ['--fix=lf']

  - repo: https://github.com/adrienverge/yamllint
    rev: v1.35.1
    hooks: [{ id: yamllint, args: ['-c', '.yamllint.yaml'] }]

  - repo: https://github.com/koalaman/shellcheck-precommit
    rev: v0.10.0
    hooks: [{ id: shellcheck, args: ['-x'] }]

  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets
        args: ['--baseline', '.secrets.baseline']

  - repo: local
    hooks:
      - id: inventory-schema
        name: Validate inventory against JSON Schema
        entry: bash tools/validate-inventory.sh
        language: system
        pass_filenames: false
        files: '^inventory/'
      - id: no-latest-tags
        name: Reject :latest image tags
        entry: bash -c 'if grep -rnE "image:.*:latest" --include="*.yaml" clusters/ charts/ 2>/dev/null; then echo "ERROR: :latest tag found (Rule 4)"; exit 1; fi'
        language: system
        pass_filenames: false
```

Then: `pre-commit install && detect-secrets scan > .secrets.baseline && pre-commit run --all-files`.

> 💡 **WHY the `no-latest-tags` hook:** Rule 4 is unenforceable by convention alone. At 100 nodes, one `:latest` tag means an unreproducible cluster the moment upstream publishes a new digest.

---

### Task 8 — CI pipeline

**`.github/workflows/validate.yaml`**
```yaml
name: validate
on:
  push:    { branches: [main] }
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: jdx/mise-action@v2
        with: { version: 2024.11.8 }
      - name: Install tools
        run: mise install
      - name: Verify pinned versions
        run: task tools:verify
      - name: Validate inventory
        run: task validate:inventory
      - name: Lint YAML
        run: task validate:yaml
      - name: Lint Markdown
        run: task validate:markdown
      - name: Lint shell
        run: task validate:shell
      - name: Validate Kubernetes manifests
        run: task validate:manifests
      - name: Check evidence completeness
        run: task evidence:check

  secrets:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - name: Scan for leaked secrets
        uses: gitleaks/gitleaks-action@v2
        env: { GITHUB_TOKEN: '${{ secrets.GITHUB_TOKEN }}' }
```

> ⚠️ If you use GitLab/Gitea instead, port this to the equivalent pipeline file and record the substitution in `deviations.md`. The *gates* are what matter, not the CI vendor.

---

### Task 9 — Evidence templates & contributor docs

**`evidence/README.md`** — explain that `evidence/phase-NN/` is the proof-of-work directory, that its contents are append-only history, and that fabricated evidence is the single worst failure mode in this project.

**`evidence/_template/acceptance.md`**
```markdown
# Phase NN — Acceptance

Date: YYYY-MM-DD
Operator: <name or agent id>
Commit: <sha>

| # | Criterion | Command | Result | Evidence |
|---|---|---|---|---|
| A1 | <criterion text from the phase file> | `<exact command run>` | ✅ PASS / ❌ FAIL | <paste output below> |

## A1 — <criterion>
```
<verbatim command output>
```

## Summary
- Criteria passed: N / M
- Phase status: COMPLETE / INCOMPLETE
- If INCOMPLETE, what remains and why:
```

Create the same shape for `preflight.md`, `plan.md`, `deviations.md`, `handoff.md`.

**`CONTRIBUTING.md`** — commit conventions, branch strategy (`main` protected; work on `phase/NN-slug` branches), the PR template requirement that every PR link its evidence directory, and the rule that any deviation from `ARCHITECTURE.md` requires a new ADR in the same PR (Law II).

**`CODEOWNERS`** — at minimum, protect `inventory/`, `talos/`, `policies/`, and `ARCHITECTURE.md`.

---

### Task 10 — Seed the inventory examples

Write one fully-populated `inventory/nodes/nx-c-r01-05.yaml.example` matching `ARCHITECTURE.md#L1.1`, plus `racks.yaml.example` and the three network/power examples. These are the templates Phase 01 fills in for real.

Then run:
```bash
task validate
```
Everything must pass on a repo containing only examples.

---

## 🧪 ACCEPTANCE CRITERIA

Record each in `evidence/phase-00/acceptance.md` with real output.

| # | Criterion | Verification command | Pass condition |
|---|---|---|---|
| **A1** | Directory skeleton matches the deliverables tree | `find . -type d -not -path './.git/*' \| sort` | All listed directories exist |
| **A2** | Every pinned tool is installed at the pinned version | `task tools:verify` | Exit 0, no mismatches |
| **A3** | Inventory schema rejects a malformed node | Copy an example, delete `spec.cpu`, run `task validate:inventory` | Exits non-zero with a clear message |
| **A4** | Inventory schema accepts a valid node | `cp inventory/nodes/*.example inventory/nodes/nx-c-r01-05.yaml && task validate:inventory` | Exit 0 |
| **A5** | Conditional rule works: control node without ECC is rejected | Craft a `control` node with `memory.ecc: false` | Validation fails |
| **A6** | Cross-document check catches duplicate MACs | Create two nodes sharing a MAC | `task validate:inventory` fails with "duplicate MAC" |
| **A7** | Cross-document check catches rack-unit collision | Two nodes at `r01`/`u22` | Validation fails |
| **A8** | Pre-commit hooks are installed and pass | `pre-commit run --all-files` | All hooks pass |
| **A9** | `:latest` tag is rejected | Add `image: foo:latest` to a file under `clusters/`, run `pre-commit run no-latest-tags --all-files` | Hook fails |
| **A10** | A private key is rejected | Attempt to commit a generated test RSA key | `detect-private-key` blocks it. **Delete the test key afterwards.** |
| **A11** | Full validation suite is green | `task validate` | Exit 0 |
| **A12** | CI pipeline runs and passes on a PR | Push a branch, open a PR | All CI jobs green |
| **A13** | Evidence scaffolding works | `task evidence:new -- 00` | `evidence/phase-00/` created with all five templates |
| **A14** | Markdown links in the three design docs resolve | `markdownlint-cli2` + a link checker over `*.md` and `phases/*.md` | No broken relative links |

---

## ↩️ ROLLBACK

This phase creates only files. To undo:

```bash
git checkout -- .          # discard uncommitted changes
git clean -fd              # ⚠️ DANGER: removes untracked files. Review `git clean -nd` first.
pre-commit uninstall
```

No cluster, no hardware, no data is at risk.

---

## 🔧 TROUBLESHOOTING

| Symptom | Likely cause | Fix |
|---|---|---|
| `mise install` fails on `aqua:` backends | aqua registry not initialized | `mise plugins install aqua` or substitute `ubi:owner/repo` |
| `check-jsonschema` reports "unresolvable $ref" | `$id` mismatch or relative ref | Use absolute `$id` values as shown; avoid cross-file `$ref` in Phase 00 |
| `kubeconform` fails on CRDs | CRD schemas are not in the default catalog | `-ignore-missing-schemas` is already set; add the CRD's schema location when the CRD is introduced |
| `yamllint` fails on Helm templates | Go templating is not valid YAML | Confirm `charts/**/templates/` is in the ignore list |
| `shellcheck` SC1091 "not following source" | Sourcing `lib/common.sh` | `-x` flag is already set; ensure the path is relative to the script |
| `detect-secrets` flags a false positive | High-entropy string that is not a secret | `detect-secrets audit .secrets.baseline` and mark it, then commit the baseline |
| Pre-commit is very slow | Running on all files every time | Normal for the first run; subsequent runs are incremental |
| Git rejects `.gitkeep` in a non-empty dir | Directory got populated | Harmless; remove the `.gitkeep` |

---

## 🚫 DO NOT

- **Do not** create any Kubernetes manifests, Helm values, or Talos configs. Those belong to Phases 09/12/15.
- **Do not** populate `inventory/nodes/` with real hardware. That is Phase 01.
- **Do not** define the rack, network, or power schemas beyond stubs. Phases 02–03 own their fields and will do it with the right context.
- **Do not** install cluster tooling on any server. This phase touches only the workstation/repo.
- **Do not** add CI jobs that require a cluster. There is no cluster yet.
- **Do not** commit real secrets, even encrypted, in this phase. The SOPS chain is Phase 10.

---

## 📤 HANDOFF

Write `evidence/phase-00/handoff.md` covering:

1. **CI vendor chosen** (GitHub Actions / GitLab / Gitea) and the file path.
2. **Any tool version substitutions** and why.
3. **The exact repo root path** and whether a remote is configured.
4. **Schema stubs still open** — `rack.schema.json` (Phase 02), `network.schema.json` (Phase 03), `power.schema.json` (Phase 02).
5. **Whether the environment is air-gapped**, since that changes every later `curl`/registry step.
6. **Node naming decisions** — if you deviated from `nx-<a>-r<NN>-<SS>`, state the new pattern and update the schema regex.

---

## ➡️ NEXT

**[PHASE-01 — Hardware Inventory & Capability Model](PHASE-01.md)** — fill `inventory/nodes/` with the real fleet, including PCIe topology and GPU capability flags. The schema you just wrote is what makes that data trustworthy.
