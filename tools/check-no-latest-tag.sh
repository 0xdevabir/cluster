#!/usr/bin/env bash
# Reject :latest image tags anywhere under clusters/ or charts/ (Rule 4 — pin every version).
source "$(dirname "$0")/lib/common.sh"

if grep -rnE 'image:.*:latest' --include='*.yaml' "$ROOT/clusters" "$ROOT/charts" 2>/dev/null; then
  die ":latest tag found (Rule 4)"
fi
ok "no :latest tags found"
