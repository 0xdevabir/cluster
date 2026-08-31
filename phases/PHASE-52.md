# PHASE 52 — Scale-Out Validation to 100+ Nodes (G11)

| | |
|---|---|
| **Stage** | 8 — Performance Engineering |
| **Estimated effort** | 5–6 hours (plus provisioning time for new nodes) |
| **Depends on** | 48, 49, 50, 51 |
| **Blocks** | 53 (Stage 9 entry) |
| **Risk** | 🔴 High — this is where the architecture's assumptions meet reality |
| **Blast radius** | The whole cluster |
| **Architecture refs** | `ULTIMATE-PLAN.md#9-scaling-model`, `#14-roadmap` (M4), `#13-gates` (G11) |

---

## 🎯 MISSION

Grow the cluster to **100+ nodes** and prove that everything built in Phases 0–51 still works at that scale. Find the cliffs before production does: etcd under load, scheduler latency, Cilium's datapath, Ceph rebalance time, the fabric's oversubscription, and the operational processes that were comfortable at 24 nodes and are not at 100. Then pass **gate G11** and close Stage 8.

> 💡 **WHY scale is a phase and not an outcome.** Almost nothing in this architecture is linear. etcd's write latency degrades superlinearly with object count. The scheduler's placement time grows with node count *and* with pod count. Ceph's rebalance moves more data. A rack-level failure takes out 12 nodes instead of 3. **Systems do not fail at scale for one reason; they fail because six things that were each fine at 24 nodes interact at 100.** The only way to know is to get there deliberately, measuring at every step.

> ⚠️ **The cliffs from `ULTIMATE-PLAN.md §9` are predictions, not measurements.** This phase converts them into facts. If a predicted cliff at 64 nodes turns out to be at 40, that is the single most valuable finding in Stage 8 — and it changes the growth plan.

---

## ✅ PREFLIGHT

```bash
# 📊 The baseline at current scale, and the predicted cliffs
jq '.' benchmarks/baselines/baseline.json
grep -A20 "scaling cliffs" ULTIMATE-PLAN.md

# All Stage 8 tooling ready
bash benchmarks/harness/run.sh --quick
bash tools/profiling/profile.sh --help

# Capacity to grow: power, cooling, switch ports, IP space
bash tools/power/power-report.sh
yq '.circuits[] | select(.committed_watts < .budget_watts)' facility/power/power-budget.yaml
yq '.subnets' network/design/ip-plan.yaml

# Provisioning pipeline (Phases 08/09) still works
kubectl get hardware -n tink-system | wc -l
```

---

## 📦 DELIVERABLES

```
scale-out/
  growth-plan.md                    # 🎯 the staged sequence, with gates
  measurements/                     # per-scale-step results
    24-nodes/  48-nodes/  72-nodes/  100-nodes/
  cliff-analysis.md                 # 🎯 predicted vs. MEASURED cliffs
  control-plane-scaling.md          # what had to change and when
tools/scale/
  scale-step.sh                     # add a batch, verify, gate
  scale-benchmark.sh                # the fixed measurement set per step
  control-plane-load-test.sh        # ⚠️ etcd/API under synthetic load
  chaos-at-scale.sh                 # failure tests at 100 nodes
gates/G11-scale.md
evidence/phase-52/{preflight,acceptance,handoff,deviations,gate-g11}.md
```

---

## 📋 TASKS

### Task 1 — 🎯 The staged growth plan

Never jump to 100. Grow in steps, measure at each, and **stop when a step fails its gate.**

| Step | Nodes | Adds | Gate before proceeding |
|---|---|---|---|
| Current | ~24 | — | Baseline established |
| **S1** | 48 | +24 | Full measurement set; no regression > 5 % |
| **S2** | 72 | +24 | Same; **plus control-plane headroom check** |
| **S3** | 100 | +28 | Same; plus a chaos test at scale |
| S4 | 128+ | growth | Only after S3 holds for 2 weeks |

**At every step, measure the same fixed set:**

| Measurement | Why it matters at scale |
|---|---|
| 📊 B1–B12 (`--standard`) | Did per-node performance change? |
| **etcd: object count, DB size, p99 fsync, watch count** | ⚠️ The most common scaling cliff |
| **API server: p99 latency, request rate, inflight** | |
| **Scheduler: placement latency p50/p99** | Grows with nodes × pods |
| Kueue: admission latency at N pending workloads | Phase 30's B11 |
| Cilium: BPF map utilization, identity count, agent memory | ⚠️ Identity explosion is real |
| Ceph: PG count, rebalance time for one OSD loss, mon CPU | |
| Prometheus/Mimir: active series, ingest rate, query latency | Phase 45's cardinality budget |
| **Provisioning: time to add 24 nodes** | Operational scaling |
| **A full-fleet operation: rolling restart of a DaemonSet** | The day-2 reality |
| Power draw, per circuit and total | Phase 32's budget |

---

### Task 2 — ⚠️ Control-plane scaling (the first thing to break)

**etcd is the limit that surprises people.** It is not node count that hurts; it is total object count and write rate.

```
Object growth per node, roughly:
  Node, ~4 Pods baseline, DRA ResourceSlices, CSI objects,
  Cilium identities, endpoints, events…
  → ~150–400 etcd objects per node

At 100 nodes with 2,000 running pods:
  ~60,000–100,000 objects, DB perhaps 800 MB–1.5 GB
  ⚠️ etcd's default quota is 2 GB (Phase 12 raised it — verify)
```

| Metric | Watch for | Response |
|---|---|---|
| **etcd DB size** | > 60 % of quota | Raise the quota; compact and defrag more often |
| **etcd p99 fdatasync** | Rising above Phase 12's 2 ms contract | ⚠️ The disk is the problem — PLP media required |
| etcd watch count | > 10k | Reduce watchers; check for a controller with a bad watch |
| **Events volume** | Dominating writes | ⚠️ Shorten event TTL — events are often > 50 % of etcd writes |
| API p99 latency | > 1 s (SLO S2) | Increase API server replicas; add `--max-requests-inflight` |
| Watch cache misses | Rising | Increase watch cache size |
| **Leader elections** | Any unexpected | Network or disk latency; investigate immediately |

> ⚠️ **Kubernetes events are the most common cause of etcd pressure at scale, and the easiest to fix.** A cluster with a crash-looping DaemonSet generates thousands of events per minute, all written to etcd. Set `--event-ttl=30m` (from the 1 h default) and consider a separate etcd for events (`--etcd-servers-overrides=/events#...`). **Do this at S1, before it hurts.**

**`tools/scale/control-plane-load-test.sh`** — synthetic load beyond current scale, so you find the limit before organic growth does:
```
Create N synthetic pods/objects; measure API and etcd behavior at
1×, 2×, and 4× the current object count. Find where p99 latency knees.
⚠️ Run this against a TEST cluster or in a maintenance window — it is disruptive.
```

> 💡 **Knowing the ceiling is worth more than being under it.** "We knee at ~180,000 objects, currently at 78,000" is an actionable capacity statement. "It works fine" is not.

---

### Task 3 — The predicted cliffs, tested

`ULTIMATE-PLAN.md §9` predicted specific cliffs. Test each explicitly.

| Predicted cliff | At | Test | Measured |
|---|---|---|---|
| **Fabric oversubscription** — cross-leaf traffic exceeds uplink capacity | > 32 nodes in a job | B5 at 8/16/32/64 nodes; compare in-leaf vs. cross-leaf | |
| **NCCL ring latency** grows with rank count | > 64 ranks | B5 latency curve | |
| **Scheduler placement time** grows with nodes × pending | > 5,000 pending pods | B11 under synthetic queue depth | |
| **Ceph rebalance** duration and client impact | > 60 OSDs | Kill an OSD at each scale; measure recovery + client impact | |
| **Cilium identity count** | > 10k identities | Monitor; check BPF map limits | |
| **etcd write amplification** | > 100k objects | Task 2 | |
| **Prometheus cardinality** | > 1.5M series | Phase 45's budget | |
| **Provisioning time** grows nonlinearly | Batch > 24 | Time each batch | |

> 📊 **Record predicted vs. measured for every cliff in `cliff-analysis.md`.** Where the prediction was wrong, understand why — that error is information about the model, and the corrected model drives Phase 56's capacity plan.

---

### Task 4 — Operational scaling (the part that is not about performance)

Some things that were fine at 24 nodes become unworkable at 100 without changing.

| Operation | At 24 nodes | ⚠️ At 100 nodes | Change needed |
|---|---|---|---|
| Provisioning a batch | 40 min | Should be ~2.5 h linear — **measure it** | Parallelism, inrush stagger (Phase 11/32) |
| Rolling DaemonSet update | 10 min | Could be 45 min | `maxUnavailable` tuning |
| **Talos OS upgrade, whole fleet** | 2 h | **8+ h** | Staged waves; Phase 53 owns the procedure |
| Ceph rebalance after a node loss | 20 min | Longer, more data | Throttling (Phase 27 C10) |
| **Full `--full` benchmark run** | 8 h | Longer at larger scale | Sampling, not exhaustive |
| Reading `kubectl get pods -A` | Instant | Slow, large | Use selectors; watch the API load |
| Node inventory reconciliation | Manual is fine | **Must be automated** | Phase 01's tooling |
| **Investigating "which node is bad"** | Look at 24 | Need Phase 45/51's tooling | Already built ✅ |

> ⚠️ **The full-fleet OS upgrade is the operation that most often becomes intractable.** At 100 nodes with a 15-minute reboot cycle and 3-node waves, that is 8+ hours of continuous supervised operation. Measure it here so Phase 53 can design a procedure that fits in a maintenance window — or explicitly accept multi-day rolling upgrades.

---

### Task 5 — 🧪 Failure testing at scale

Failures that were survivable at 24 nodes have different characteristics at 100.

| # | Test | At 24 nodes | ⚠️ At 100 nodes |
|---|---|---|---|
| **X1** | Single node loss | Trivial | Trivial — verify it stays trivial |
| **X2** | **Rack loss (12 nodes)** | Significant | ⚠️ Ceph rebalance moves much more data; test the client impact |
| **X3** | Leaf switch loss | Partition risk | Verify no cluster partition; measure the capacity loss |
| **X4** | Control-plane node loss | Quorum holds | Verify at full object count |
| **X5** | **Simultaneous multi-node failure (power event)** | Recoverable | ⚠️ Test the Phase 23 storm guard at scale |
| **X6** | Mass restart (all nodes) | 40 min | ⚠️ **Thundering herd**: registry (Phase 42), etcd, DNS, Ceph |
| **X7** | Network partition (leaf isolated) | | Verify split-brain protection |
| **X8** | Storage node loss during heavy I/O | | Client impact under real load |

> ⚠️ **X6 — the mass restart — is the test everyone skips and everyone eventually needs.** After a facility power event, 100 nodes boot simultaneously: they all pull images (Phase 42's thundering herd), all register with the API server, all start Cilium agents, all rejoin Ceph. **Test it in a maintenance window.** The findings usually include at least one component that cannot handle 100 simultaneous clients.

---

### Task 6 — 🚪 GATE G11 — Scale

**`gates/G11-scale.md`**

| # | Check | Evidence | Pass |
|---|---|---|---|
| G11.1 | **100+ nodes provisioned and Ready** | `kubectl get nodes` | ☐ |
| G11.2 | 📊 **B1–B12 at 100 nodes; no regression > 5 % vs. baseline** | `--standard` run | ☐ |
| G11.3 | 📊 **B9 scaling efficiency at 64 GPUs meets §8.2** | B9 | ☐ |
| G11.4 | 📊 B5 measured at 8/16/32/64 nodes; the curve is documented | B5 | ☐ |
| G11.5 | etcd DB size < 60 % of quota; p99 fsync within the Phase 12 contract | Metrics | ☐ |
| G11.6 | API p99 latency meets SLO S2 | Metrics | ☐ |
| G11.7 | 📊 **Control-plane ceiling measured (load test)** | `control-plane-load-test.sh` | ☐ |
| G11.8 | Scheduler placement latency within B11 targets at 5,000 pending | Test | ☐ |
| G11.9 | Cilium BPF maps and identity count within limits | Metrics | ☐ |
| G11.10 | Prometheus active series within the Phase 45 budget | Metrics | ☐ |
| G11.11 | Ceph healthy; PG count appropriate; rebalance time measured | `ceph status` + test | ☐ |
| G11.12 | Power draw within budget at full load, every circuit | Phase 32 | ☐ |
| G11.13 | 🧪 **X2: rack loss survived; client impact quantified** | Test | ☐ |
| G11.14 | 🧪 X3: leaf switch loss does not partition the cluster | Test | ☐ |
| G11.15 | 🧪 **X5: multi-node failure trips the storm guard, not mass remediation** | Test | ☐ |
| G11.16 | 🧪 **X6: mass restart recovers; time measured; issues recorded** | Test | ☐ |
| G11.17 | 📊 Provisioning time for a 24-node batch measured at this scale | Timed | ☐ |
| G11.18 | 📊 Full-fleet DaemonSet rollout time measured | Timed | ☐ |
| G11.19 | 🎯 **Predicted vs. measured cliffs documented** | `cliff-analysis.md` | ☐ |
| G11.20 | 📊 Cluster GPU utilization ≥ 65 % sustained at scale (SLO S12) | Phase 34 | ☐ |
| G11.21 | No SLO (Phase 47) breached during or after scale-out | SLO dashboard | ☐ |
| G11.22 | All alerts still function; no alert storm at scale | Observe | ☐ |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | Growth executed in steps, each gated | `growth-plan.md` + records | Staged |
| **A2** | **A failing step halted growth** (or none failed, documented) | Records | Honored |
| **A3** | The same measurement set taken at every step | `measurements/` | Consistent |
| **A4** | 📊 Every metric trended across 24 → 48 → 72 → 100 | Charts | Trended |
| **A5** | Event TTL reduced; event write volume measured before/after | Metrics | Reduced |
| **A6** | 📊 **etcd ceiling measured via load test** | Test | Measured |
| **A7** | etcd headroom stated as a number, not a feeling | Handoff | Stated |
| **A8** | Each predicted cliff explicitly tested | `cliff-analysis.md` | All tested |
| **A9** | 🎯 **Cliffs that differed from prediction are explained** | Analysis | Explained |
| **A10** | 🧪 X1–X8 executed | Records | Executed |
| **A11** | 🧪 **X6 mass restart executed in a maintenance window** | Record | Executed |
| **A12** | X6 findings recorded and fixed or ticketed | Records | Actioned |
| **A13** | 📊 Operational timings measured (provision, rollout, upgrade estimate) | Timed | Measured |
| **A14** | Full-fleet OS upgrade duration estimated for Phase 53 | Estimate | Estimated |
| **A15** | 📊 No benchmark regressed > 5 % from the 24-node baseline | Compare | Met |
| **A16** | Where a benchmark did regress, the cause is structural and named | Analysis | Named |
| **A17** | Power within budget with all nodes at load | Phase 32 test | Within |
| **A18** | Performance CI (Phase 50) still functioning at scale | Observe | Functioning |
| **A19** | 🚪 **Gate G11 passes** | `gates/G11-scale.md` | All ☑ |
| **A20** | 📊 A new baseline accepted at 100-node scale | `baseline.json` | Accepted |

---

## ↩️ ROLLBACK

```bash
# Scale-out rollback = remove the most recently added batch
bash tools/scale/scale-step.sh --remove-batch <n> --drain-first

# ⚠️ Removing nodes triggers a Ceph rebalance. Drain storage first:
#   1. Mark the OSDs out, wait for rebalance to complete
#   2. Then remove the nodes
# Do not power off storage nodes and expect Ceph to be fine.
```

> 💡 **The gated growth plan makes rollback rarely necessary** — a failing step stops the growth before more nodes are added, so the rollback is at most one batch. That is the entire value of staging.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| etcd latency rising with node count | Object growth; or the disk cannot keep up | Event TTL; compaction; verify PLP media (Phase 12) |
| API server slow | Too few replicas; or a controller with a hot watch loop | Scale replicas; find the offender in the audit log |
| Scheduler placement slow | Node count × pending pods; or an expensive predicate | Check TAS cost (Phase 31); tune `percentageOfNodesToScore` |
| Cilium agent memory growing | Identity explosion from many namespaces/policies | Consolidate policies; check BPF map sizing |
| B5 degrades sharply past 32 nodes | Fabric oversubscription | Structural (Phase 03) — document, do not chase |
| Ceph rebalance takes hours | More data per failure domain | Phase 27's C10 tuning; consider more, smaller OSDs |
| Mass restart does not recover | A component cannot handle 100 simultaneous clients | X6's purpose — find and fix it here |
| Provisioning batch times grow nonlinearly | DHCP/TFTP/registry serialization | Phase 07/42 scaling |
| Alert storm during scale-out | New nodes fire alerts before stabilizing | Quarantine tolerations (Phase 14); inhibition (Phase 45) |
| Utilization falls after adding nodes | Demand did not grow with supply | Real finding — feeds Phase 56's capacity plan |

---

## 🚫 DO NOT

- **Do not** jump to 100 nodes in one step.
- **Do not** proceed past a failing gate.
- **Do not** skip the mass-restart test.
- **Do not** add nodes without power-budget headroom (Phase 32).
- **Do not** remove storage nodes without draining Ceph first.
- **Do not** chase a structural cliff with tuning.
- **Do not** accept "it works" as a control-plane capacity statement — measure the ceiling.
- **Do not** pass G11 with a check unmet and no dated finding.

---

## 📤 HANDOFF

`evidence/phase-52/handoff.md` must state:

1. **🚪 The G11 gate result** with evidence at each scale step.
2. **📊 Every metric trended across 24 → 48 → 72 → 100** — the scaling behavior of the whole platform in one place.
3. **🎯 The cliff analysis** — predicted vs. measured, with explanations for every divergence.
4. **📊 The control-plane ceiling** — object count, etcd headroom, API capacity. **The number that bounds further growth.**
5. **🧪 The mass-restart (X6) findings** — what broke, what was fixed.
6. **📊 Operational timings** at scale, especially the full-fleet upgrade estimate for Phase 53.
7. **Structural limits found** and what removing each would cost — feeds Phase 56.
8. **The new 100-node baseline** accepted under Phase 48's rules.
9. **Stage 8 declaration** — performance is measured, tuned, gated against regression, diagnosable, and validated at target scale. Stage 9 (operations) may begin.

---

## ➡️ NEXT

**[PHASE-53 — Day-2 Operations & Upgrades](PHASE-53.md)** — begin Stage 9. The cluster is built and fast; now make keeping it that way a routine, boring procedure.
