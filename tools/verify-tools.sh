#!/usr/bin/env bash
# Assert every pinned tool in .mise.toml resolves to the pinned version.
# Fails loudly with the mismatch; does not silently continue.
source "$(dirname "$0")/lib/common.sh"
need yq

fail=0

check_version() {
  local tool="$1" want="$2" got_cmd="$3"
  if ! command -v "$tool" >/dev/null 2>&1; then
    warn "$tool: not installed (want $want)"
    fail=1
    return
  fi
  local got
  got="$(eval "$got_cmd" 2>/dev/null || echo unknown)"
  if [[ "$got" == *"$want"* ]]; then
    ok "$tool $got"
  else
    warn "$tool: want $want, got $got"
    fail=1
  fi
}

check_version task     "3.40.0" "task --version"
check_version yq       "4.44.3" "yq --version"
check_version jq       "1.7.1"  "jq --version"
check_version kubectl  "1.34.1" "kubectl version --client --output=yaml 2>/dev/null | yq -r '.clientVersion.gitVersion'"
check_version helm     "3.16.2" "helm version --short"
check_version kustomize "5.5.0" "kustomize version"
check_version sops     "3.9.1"  "sops --version"
check_version age      "1.2.0"  "age --version"
check_version pre-commit "4.0.1" "pre-commit --version"
check_version check-jsonschema "0.29.4" "check-jsonschema --version"

[[ $fail -eq 0 ]] && ok "all pinned tools verified" || die "tool verification FAILED — install/update via mise (see .mise.toml)"
