#!/usr/bin/env bash
# For every evidence/phase-NN/ that exists, assert preflight.md, acceptance.md,
# and handoff.md are present and non-empty.
source "$(dirname "$0")/lib/common.sh"

fail=0
shopt -s nullglob

for dir in "$ROOT"/evidence/phase-*/; do
  phase="$(basename "$dir")"
  for required in preflight.md acceptance.md handoff.md; do
    f="$dir$required"
    if [[ ! -s "$f" ]]; then
      warn "$phase: missing or empty $required"
      fail=1
    fi
  done
done

[[ $fail -eq 0 ]] && ok "evidence complete for all phases" || die "evidence check FAILED"
