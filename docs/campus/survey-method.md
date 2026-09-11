# Campus Survey Method

How each field in `inventory/campus/` is measured, and its confidence
label. Fill in the per-lab notes as rooms are actually surveyed; this file
documents the *method*, not the results (those live in `fleet-report.md`
and the `inventory/campus/*.yaml` records).

## Non-invasive probing (Task 2)

- Boots a live Linux USB image on the target machine. Never boots the
  machine's own installed OS to collect these facts.
- `tools/campus/probe-harvest-node.sh <lab-id> <machine-id> [iface]` runs
  the fixed command set from `phases/PHASE-01B.md` Task 2 (dmidecode,
  lscpu, free, nvidia-smi, lsblk, ethtool, sensors) and prints the result.
- **Read-only guarantee:** the script never calls `mount`, `dd`, `parted`,
  `mkfs`, or any write path against `/dev/*`. `lsblk` output is inspected,
  never acted on.
- One machine is `probed` per distinct hardware configuration in a lab;
  the rest are `representative` (verified by model string + RAM + GPU
  presence only) or `assumed` (asset sticker only, when live-boot isn't
  possible — see Troubleshooting in the phase file).
- Raw probe output is committed verbatim to `evidence/phase-01B/raw/<id>.txt`
  for every record marked `probeConfidence: probed` — enforced by
  `tools/campus/validate-campus.sh`.

## Timetable (Task 3)

- `confidence: authoritative` — sourced from a registrar export.
- `confidence: observed` — a photographed door schedule.
- `confidence: assumed` — inferred, to be corrected by Phase 14B's
  historical model; used only when neither of the above is obtainable.

## Uplink (Task 4)

- Preferred: switch SNMP counters, 7-day sample, `classHoursP95` /
  `offHoursP95` from teaching-hours vs. off-hours utilization.
- Fallback (only when switch access is refused): a single `iperf3` test
  between a lab machine and one outside the lab, **run off-hours only**.
  Record the method used in the `source` field of
  `inventory/campus/uplinks.yaml` either way.
- Never run a bandwidth test during class hours — see the phase's DO NOT
  list and R-19.

## Ventilation verdict (Task 1)

- `adequate` / `marginal` / `inadequate`, assigned by direct observation
  of active cooling (AC split, ceiling fan only, none) plus a subjective
  judgment of whether the room can sustain 30+ machines at load for hours
  with the door closed. When in doubt, mark `marginal` or `inadequate` —
  this field is a hard exclusion gate for GPU harvesting in Phase 19B and
  erring conservative costs capacity, not safety.

## Personal data policy

Only role/title and an institutional contact reference are stored — never
phone numbers, national IDs, or personal email addresses. See
`phases/PHASE-01B.md` acceptance criterion 7.
