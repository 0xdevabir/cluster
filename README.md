# Project NEXUS

**A private, bare-metal AI/HPC cloud built on two compute planes: a small dedicated Core, and several hundred borrowed DIU classroom PCs.**

---

## Start here

| Document | What it answers |
|---|---|
| [`ULTIMATE-PLAN.md`](ULTIMATE-PLAN.md) | **What and why.** The laws, the physics, the hardware model, the risk register, the acceptance gates, the roadmap. |
| [`CAMPUS-FABRIC.md`](CAMPUS-FABRIC.md) | **Plane B — the harvest fabric.** How borrowed lab PCs become efficient, consented, measured capacity. Required reading before any `*B` phase. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | **How the pieces fit.** The eleven-layer stack, component catalog, ADRs, and the Plane A/B layer deltas (§0.4). |
| [`phases/README.md`](phases/README.md) | **How to build it.** The execution protocol, the phase index, and the Beachhead path. |

## The two planes

```
                CONTROL PLANE  (3 nodes: etcd · Kueue · Argo CD · Availability Oracle)
                       │
        ┌──────────────┴───────────────┐
        ▼                              ▼
  PLANE A — CORE                 PLANE B — CAMPUS HARVEST
  4–8 dedicated racked nodes     150–400 existing DIU classroom/lab PCs
  25–100 GbE RoCEv2, Ceph        Campus 1 GbE, borrowed outside class hours
  Multi-node training, storage,  Sweeps, batch inference, preprocessing, CI,
  all state, tightly-coupled     single-node training, rendering
  Cost: procurement              Cost: electricity only ($0 capex)
```

**The thesis:** the campus already contains more idle compute than the department could afford to buy. The engineering problem is not acquiring capacity — it is harvesting it without waste and without ever inconveniencing a human.

## Where to begin building

The **Beachhead path** (`phases/README.md §8`) reaches real users in ~8 weeks with zero hardware purchase:

```
00 → 01 → 01B → 04B → 03B → 06 → 07 → 08B → 09 → 12 → 13 → 15 → 14 → 14B → 25B → 30 → 31B
```

> 🚧 **Phase 04B is a hard barrier.** No campus machine is woken, netbooted, or enrolled before that lab's participation agreement is signed and recorded. See `phases/README.md` Rule C1.

## Repository layout

| Path | Contents |
|---|---|
| `phases/` | Numbered work orders (`00`–`56` Core, `*B` Campus) |
| `evidence/` | Per-phase proof: preflight, acceptance, handoff, deviations |
| `inventory/` | Machine-readable source of truth — nodes, racks, power, network, `campus/` |
| `clusters/nexus-prod/` | GitOps-managed cluster state |
| `docs/` | Facility, security, and `campus/` documentation |
| `policies/` | Kyverno policies and `campus/` consent records |
| `tools/` | Validation, discovery, and `campus/` operational tooling |
| `benchmarks/` | Suites, runners, baselines, reports |
| `runbooks/` | Operator procedures, one per alert |
