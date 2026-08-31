#!/usr/bin/env bash
# Shared helpers. Source this; do not execute it.
set -euo pipefail

readonly C_RED='\033[0;31m' C_GRN='\033[0;32m' C_YEL='\033[0;33m' C_RST='\033[0m'

ROOT="$(git rev-parse --show-toplevel)"
# shellcheck disable=SC2034  # part of the shared lib API; not every caller uses it directly
readonly ROOT

log()  { printf '%s\n' "$*" >&2; }
ok()   { printf "${C_GRN}✓${C_RST} %s\n" "$*" >&2; }
warn() { printf "${C_YEL}!${C_RST} %s\n" "$*" >&2; }
die()  { printf "${C_RED}✗${C_RST} %s\n" "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "required tool not found: $1"; }
