# RISK-LEGAL-01 — GeForce Driver EULA / Datacenter Deployment Review

Date opened: 2026-08-31
Tracking reference: `ULTIMATE-PLAN.md` R-03 (§10, risk register)
Status: **OPEN — determination not yet made**

## The issue

NVIDIA's GeForce driver licence has historically restricted "datacenter deployment" of
GeForce products. Project NEXUS's M1 pilot plan (`ULTIMATE-PLAN.md §9`) and the current
planned inventory (`inventory/nodes/nx-c-r01-04.yaml` through `nx-c-r01-07.yaml`) specify
four `NVIDIA GeForce RTX 4090` GPUs as the training pool. Before scaling beyond the pilot,
someone qualified to make the determination must confirm whether this deployment falls
within NVIDIA's permitted use.

**This document does not provide legal advice.** It is a tracking record for a determination
that must be made by qualified counsel or a licensing specialist.

## Options if the answer is "not permitted"

| Option | Description | Cost/schedule impact |
|---|---|---|
| (a) | Use RTX PRO / datacenter SKUs (e.g. RTX PRO 6000 Blackwell, H100 PCIe) for the production pool | Materially higher per-GPU cost; re-run Phase 05 capacity model |
| (b) | Obtain written clarification from NVIDIA | Unknown timeline; blocks M2 procurement until resolved |
| (c) | Restrict GeForce nodes to a use-case clearly permitted (e.g. non-production R&D) | Limits the training pool's production role |

## Blocking scope

- Does **not** block Phases 02–20 (facility design, bootstrap, substrate, and early
  acceleration-fabric work proceed on the planned inventory as-is).
- **Does** block the M2 (24-node) procurement decision (`ULTIMATE-PLAN.md §9`) — no
  additional GeForce-class GPUs should be purchased for production use until this
  determination is recorded below.

## Determination

_Not yet made. Update this section with the determination and its date once a qualified
reviewer has responded._

| Date | Reviewer | Determination | Notes |
|---|---|---|---|
| — | — | — | Pending |
