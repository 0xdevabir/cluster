# PHASE 23 — GPU Health, Auto-Remediation & Stage-3 Gate (G4/G5)

| | |
|---|---|
| **Stage** | 3 — Acceleration Fabric |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 18, 19, 20, 21, 22 |
| **Blocks** | 24 (Stage 4 entry), 53, 54 |
| **Risk** | 🟡 Medium — a wrong auto-remediation rule can drain a healthy cluster |
| **Blast radius** | Node availability cluster-wide |
| **Architecture refs** | `ARCHITECTURE.md#x2-failure-domains--blast-radius` (F5–F8), `#l10-observability`, `ULTIMATE-PLAN.md#13-gates` (G4, G5) |

---

## 🎯 MISSION

Make hardware failure a **control-loop event, not an incident**. Detect degraded GPUs, NICs, and thermal conditions automatically; cordon, drain, and quarantine bad nodes before they poison jobs; attempt safe automated recovery; and escalate to a human only when automation cannot fix it. Then close Stage 3 by passing **gates G4 (fabric) and G5 (accelerator)**.

> 💡 **WHY this phase exists.** At 100+ nodes with consumer hardware, something is always broken. Law V — *Fail Loudly, Recover Silently*. The alternative is what most clusters do: a GPU with ECC errors or a thermally-throttled node stays in the pool, silently making every job that lands on it 40 % slower, and the only symptom is users complaining that "the cluster feels slow sometimes." **A degraded node that stays in service is worse than a node that is down**, because the failure is invisible.

> ⚠️ **DANGER — the remediation amplifier.** A remediation controller with a bad threshold can cordon every node in minutes. Every automated action in this phase must have a **rate limit and a cluster-wide floor** (never take more than N nodes out at once, never drop below M % capacity), and must start in **dry-run mode**.

---

## ✅ PREFLIGHT

```bash
# All of Stage 3 is in place
kubectl get pods -n gpu-operator                        # all Running
kubectl get resourceslices                              # DRA slices published
bash tools/net/rdma-verify.sh                           # RDMA healthy
cat benchmarks/baselines/b5-nccl-busbw.json             # B5 recorded

# DCGM metrics are flowing
kubectl exec -n monitoring prometheus-0 -- \
  promtool query instant http://localhost:9090 'count(DCGM_FI_DEV_GPU_TEMP)'

# Prometheus + Alertmanager from Phase 11 are up
kubectl get pods -n monitoring
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/acceleration/health/
  dcgm-health-checks.yaml              # DCGM policy + diagnostic levels
  node-problem-detector.yaml           # kernel/hardware log monitors
  gpu-health-daemonset.yaml            # periodic active probe
  remediation-controller.yaml          # ⚠️ rate-limited, floor-guarded
observability/rules/
  gpu-health-alerts.yaml               # 🚨 the alert set
  fabric-health-alerts.yaml
tools/health/
  gpu-diag.sh                          # dcgmi diag -r 1|2|3
  quarantine-node.sh                   # cordon + taint + annotate + notify
  release-node.sh                      # the reverse, with a required health proof
  health-report.sh                     # fleet-wide health snapshot
docs/operations/
  gpu-failure-runbook.md
  node-lifecycle.md
gates/G4-fabric.md
gates/G5-accelerator.md
evidence/phase-23/{preflight,acceptance,handoff,deviations,gate-g4,gate-g5}.md
```

---

## 📋 TASKS

### Task 1 — The health signal catalog

Define exactly what "unhealthy" means. Every signal gets a source, a threshold, a severity, and an action.

| # | Signal | Source | Threshold | Severity | Automated action |
|---|---|---|---|---|---|
| **H1** | GPU fell off the bus | `nvidia-smi` returns error / DCGM gone | Any | 🔴 Critical | Quarantine + page |
| **H2** | Xid error (fatal class) | dmesg / NPD | Xid 13,31,43,45,48,63,64,74,79,94,95 | 🔴 Critical | Quarantine + page |
| **H3** | Xid error (app class) | dmesg / NPD | Xid 13,31 (app fault) repeated | 🟡 Warn | Count, don't quarantine |
| **H4** | Uncorrectable ECC error | `DCGM_FI_DEV_ECC_DBE_VOL_TOTAL` | > 0 | 🔴 Critical | Quarantine |
| **H5** | Correctable ECC rate rising | `DCGM_FI_DEV_ECC_SBE_VOL_TOTAL` | > 100/hr | 🟡 Warn | Ticket, schedule replacement |
| **H6** | Retired pages pending | `DCGM_FI_DEV_RETIRED_PENDING` | > 0 | 🟡 Warn | Drain at next maintenance |
| **H7** | Thermal throttling sustained | `DCGM_FI_DEV_THERMAL_VIOLATION` | > 0 for 5 min | 🟡 Warn | Reduce power cap, alert |
| **H8** | GPU temperature critical | `DCGM_FI_DEV_GPU_TEMP` | > 88 °C for 2 min | 🟠 High | Cap power, alert; quarantine at 92 °C |
| **H9** | Power violation | `DCGM_FI_DEV_POWER_VIOLATION` | Sustained | 🟡 Warn | Investigate PSU / cap |
| **H10** | PCIe link degraded | `DCGM_FI_DEV_PCIE_LINK_GEN/WIDTH` | Below the Phase 01 baseline | 🟠 High | Quarantine (R-04 tripwire) |
| **H11** | PCIe replay rate | `DCGM_FI_DEV_PCIE_REPLAY_COUNTER` | Rising | 🟡 Warn | Reseat at maintenance |
| **H12** | NVLink error (where present) | DCGM NVLink counters | > 0 | 🟠 High | Quarantine |
| **H13** | Fan failure / fan at 100 % idle | DCGM / IPMI-less inference | — | 🟡 Warn | Physical inspection ticket |
| **H14** | RDMA link down | `rdma link` / port state | Not ACTIVE | 🔴 Critical | Quarantine (F7) |
| **H15** | NIC speed degraded | `ethtool` / SNMP | Below negotiated baseline | 🟠 High | Quarantine (R-01 tripwire) |
| **H16** | PFC storm | Pause-frame rate | > threshold | 🟠 High | Alert; do NOT auto-quarantine (fabric-wide) |
| **H17** | Kernel MCE / EDAC error | NPD kernel monitor | Any | 🔴 Critical | Quarantine |
| **H18** | NVMe media/wearout | SMART via NPD | Wearout > 90 %, media errors | 🟡 Warn | Replace at maintenance |
| **H19** | Node NotReady | kubelet | > 5 min | 🔴 Critical | Standard K8s eviction + investigate |
| **H20** | GPU allocated but 0 % util | DCGM + DRA state | > 30 min | 🟡 Warn | Idle reclaim (Phase 34) |
| **H21** | DCGM exporter not reporting | `up{job="dcgm"}` | == 0 | 🟠 High | Restart; if persistent, quarantine (blind node) |

> ⚠️ **H21 matters more than it looks.** A node whose health telemetry has stopped is not a healthy node — it is an **unobservable** node. Treat missing signal as a fault, not as absence of fault.

**Xid reference** — put the full decoding table in `gpu-failure-runbook.md`. The critical distinction:

| Class | Xids | Meaning | Action |
|---|---|---|---|
| **Application fault** | 13, 31 | Bad user kernel, illegal memory access | Fail the job, keep the node |
| **Hardware/driver fatal** | 48, 63, 64, 74, 79, 92, 94, 95 | Double-bit ECC, GPU fell off bus, video engine failure | Quarantine the node |
| **System/thermal** | 62, 69 | Internal micro-controller halt | Quarantine, power-cycle |

> 💡 Xid 13 and 31 are the most common and the most misdiagnosed. They are usually **the user's kernel**, not your hardware. Auto-quarantining on them would take your cluster offline every time someone writes an out-of-bounds CUDA kernel.

---

### Task 2 — Node Problem Detector

**`node-problem-detector.yaml`** — NPD watches kernel logs and reports conditions on the Node object.

```yaml
# Custom monitor: nexus-gpu-monitor.json
{
  "plugin": "kmsg",
  "logPath": "/dev/kmsg",
  "rules": [
    { "type": "permanent", "condition": "GPUFatalXid",
      "reason": "NVRMXidFatal",
      "pattern": "NVRM: Xid \\(PCI:[0-9a-f:.]+\\): (48|63|64|74|79|92|94|95)," },
    { "type": "permanent", "condition": "GPUFellOffBus",
      "reason": "GPUFellOffBus",
      "pattern": "NVRM: GPU at PCI:[0-9a-f:.]+ has fallen off the bus" },
    { "type": "permanent", "condition": "KernelMCE",
      "reason": "MachineCheckException",
      "pattern": "mce: \\[Hardware Error\\]" },
    { "type": "temporary", "reason": "GPUAppXid",
      "pattern": "NVRM: Xid \\(PCI:[0-9a-f:.]+\\): (13|31)," }
  ]
}
```

> 🔒 NPD runs privileged and reads the host kernel log. It belongs on the Phase 04 privileged allow-list with an ADR. On Talos it must read `/dev/kmsg` via a hostPath — verify this is permitted by the Talos machine config.

**Conditions become taints** via the remediation controller, not NPD directly — you want the rate limiting in one place.

---

### Task 3 — Active health probing

Passive telemetry misses faults that only appear under load. Add an active probe.

**`gpu-health-daemonset.yaml`** — runs on every GPU node, on a schedule, in a **low-priority preemptible pod** so it never displaces real work.

```bash
# tools/health/gpu-diag.sh — DCGM diagnostic levels
dcgmi diag -r 1     #  ~seconds   — quick: deployment/software checks. Safe anytime.
dcgmi diag -r 2     #  ~2 min     — medium: memory + PCIe bandwidth. Needs an idle GPU.
dcgmi diag -r 3     #  ~15 min    — long: full stress, EUD. Maintenance windows only.
dcgmi diag -r 4     #  ~1 hr      — extended. Pre-acceptance and RMA evidence only.
```

**The probe schedule:**

| Level | When | Requires |
|---|---|---|
| `-r 1` | Every 30 min, always | Nothing — safe on a busy GPU |
| `-r 2` | When the GPU is idle > 10 min | Idle GPU (must not run alongside a job) |
| `-r 3` | On node release from quarantine; monthly rotation | Cordoned node |
| `-r 4` | New-node acceptance (Phase 14); RMA evidence | Cordoned node |

> 🚫 **Never run `-r 2` or higher on a GPU with a running workload.** It will corrupt the user's results or crash their job. Gate the probe on DRA allocation state, not just on utilization being low.

**Also probe what DCGM does not cover:**
- A tiny NCCL two-rank allreduce against a known-good partner node — catches fabric faults DCGM cannot see.
- A short `ib_write_bw` to the leaf's designated reflector node.
- A `fio` read on the local NVMe scratch device.

---

### Task 4 — ⚠️ The remediation controller (with the guards)

This is the highest-risk component in the phase. Build it defensively.

**Control loop:**
```
observe(node health signals)
  → classify(severity, confidence)
    → check guards
      → act(cordon | taint | drain | reboot | quarantine | page)
        → record(event, evidence, timestamp)
          → verify(did the action help?)
```

**The guards — every one is mandatory:**

| Guard | Rule | Why |
|---|---|---|
| **G-DRYRUN** | Ships in `dryRun: true`. Runs for 7 days emitting only events. | You will find false positives. Find them before they cordon anything. |
| **G-RATE** | ≤ 2 nodes quarantined per 10 minutes, cluster-wide | Stops a bad rule from cascading |
| **G-FLOOR** | Never take the cluster below 80 % of schedulable GPU capacity | Availability floor |
| **G-POOL** | Never quarantine more than 25 % of any single pool | Protects small pools |
| **G-CONFIRM** | A signal must persist for its `for:` duration before acting | Kills transient flaps |
| **G-DRAIN** | Drain respects PDBs; running jobs get a grace period | Law: don't kill a 20-hour training run over a warning |
| **G-CP** | **Never auto-remediate a control-plane node** | Human decision only |
| **G-STORM** | If > 5 nodes report the same fault within 5 min, **stop and page** | This is a fabric/power/software event, not N node failures |
| **G-AUDIT** | Every action writes a Kubernetes Event + a row in the health log | Forensics |

> ⚠️ **G-STORM is the one people skip and regret.** Twelve nodes reporting "RDMA link down" at once is a failed switch, not twelve failed NICs. Quarantining all twelve makes the outage worse and destroys the evidence. Correlated failures escalate; they do not auto-remediate.

**The remediation ladder** — try the cheapest fix first, escalate only on failure:

| Step | Action | When | Reversible |
|---|---|---|---|
| 1 | **Cordon** — stop new placement | Any 🟠/🔴 | Yes |
| 2 | **Taint** `nexus.io/quarantine=hardware:NoSchedule` | Same | Yes |
| 3 | **Wait for drain** — let running jobs finish, up to `maxDrainWait` | Non-critical faults | Yes |
| 4 | **Evict** — drain with grace | Critical faults, or drain timeout | Job restarts |
| 5 | **GPU reset** — `nvidia-smi -r` (needs no processes on the GPU) | Xid/hang, GPU present | Usually |
| 6 | **Node reboot** — via Talos API `talosctl reboot` | Reset failed | Yes |
| 7 | **Power cycle** — via the PDU (Phase 02/08) | Node unreachable | Yes |
| 8 | **Quarantine + page** | Everything above failed | Human |

> 💡 Steps 5–7 are how you replace a BMC (`ULTIMATE-PLAN.md §4.6`). Talos' API reboot plus PDU power control gives you remote recovery without IPMI. Test both paths in this phase — not during an outage.

**`tools/health/release-node.sh`** — the reverse path, and it **requires proof**:
```
Refuses to release a node unless ALL of:
  · dcgmi diag -r 3 passed within the last hour
  · No health signals firing for 30 min
  · RDMA link ACTIVE and ib_write_bw within 5 % of the B4 baseline
  · A human typed the node name to confirm
  · The reason for quarantine is recorded as resolved with a note
```

---

### Task 5 — 🚨 Alerts

**`gpu-health-alerts.yaml`** — alert on the health signals, but **route by severity**:

| Route | Signals | Destination |
|---|---|---|
| Page immediately | H1, H2, H4, H14, H17, G-STORM trip | On-call |
| Ticket | H5, H6, H11, H13, H18 | Maintenance queue |
| Dashboard only | H3, H20 | Grafana |
| Capacity alert | Quarantined nodes > 5 % of the fleet | Platform team |

Add the meta-alerts that catch the health system itself failing:
- `RemediationControllerDown`
- `RemediationDryRunStillEnabled` (fires after the 7-day soak — so you don't forget to arm it)
- `RemediationRateLimitHit` (the rate limiter engaged — investigate why)
- `QuarantinedNodeStale` (a node has been quarantined > 7 days with no action)

---

### Task 6 — 🚪 GATE G4 — Fabric

**`gates/G4-fabric.md`** — Stage 3's network gate. All sub-checks must pass with evidence.

| # | Check | Evidence | Pass |
|---|---|---|---|
| G4.1 | Every training node has an ACTIVE RDMA link | `rdma-verify.sh` | ☐ |
| G4.2 | 📊 B3: TCP throughput ≥ 94 % line rate | `b3-tcp.json` | ☐ |
| G4.3 | 📊 B4: RDMA ≥ 96 % line rate, p99 lat < 3 µs | `b4-rdma.json` | ☐ |
| G4.4 | Full-mesh test: every pair within 10 % of median | `full-mesh-test.sh` heatmap | ☐ |
| G4.5 | 📊 B5: NCCL busbw ≥ 85 % of theoretical at 8 nodes | `b5-nccl-busbw.json` | ☐ |
| G4.6 | NCCL uses the IB transport, never sockets | Init logs | ☐ |
| G4.7 | RoCE health signature correct (ECN present, pause ~0) | `roce-health.sh` | ☐ |
| G4.8 | A leaf-switch failure does not partition the cluster | Chaos test | ☐ |
| G4.9 | Cilium BGP sessions established on every node | `cilium-verify.sh` | ☐ |
| G4.10 | No switch port below its negotiated speed (R-01) | SNMP audit | ☐ |
| G4.11 | Jumbo frames end to end, no fragmentation | `ping -M do -s 8972` mesh | ☐ |
| G4.12 | Fabric alerts fire correctly under an induced fault | Test | ☐ |

---

### Task 7 — 🚪 GATE G5 — Accelerator

**`gates/G5-accelerator.md`**

| # | Check | Evidence | Pass |
|---|---|---|---|
| G5.1 | Every GPU node exposes its GPUs via DRA ResourceSlices | `kubectl get resourceslices` | ☐ |
| G5.2 | 📊 B1: GEMM ≥ 98 % of the bare-metal baseline | `b1-gemm.json` | ☐ |
| G5.3 | 📊 B2: H2D/D2H ≥ 90 % of PCIe theoretical | `b2-pcie.json` | ☐ |
| G5.4 | All five DeviceClasses allocate correctly | Test claims | ☐ |
| G5.5 | MPS sharing enforces the VRAM budget | The "cannot exceed" test | ☐ |
| G5.6 | VRAM admission control rejects an over-request | Kyverno test | ☐ |
| G5.7 | `matchAttribute` guarantees identical GPUs in a multi-GPU claim | Test | ☐ |
| G5.8 | NUMA alignment verified for Guaranteed pods (V1–V7) | Phase 20 validator | ☐ |
| G5.9 | 📊 p99 jitter within target with exclusive cores | Phase 20 benchmark | ☐ |
| G5.10 | DCGM metrics present for every GPU | Prometheus query | ☐ |
| G5.11 | `dcgmi diag -r 2` passes on every GPU | Fleet report | ☐ |
| G5.12 | Power caps applied and verified | `nvidia-smi -q -d POWER` | ☐ |
| G5.13 | A simulated fatal Xid quarantines the node within 5 min | Induced test | ☐ |
| G5.14 | An app-class Xid does **not** quarantine the node | Induced test | ☐ |
| G5.15 | Remediation guards verified (rate, floor, storm, CP-exempt) | Simulated | ☐ |
| G5.16 | Node release requires and enforces a health proof | Try to release a bad node | ☐ |
| G5.17 | GPU reset → reboot → PDU power-cycle ladder all tested | Runbook execution | ☐ |
| G5.18 | No GPU consistently underperforms its cohort | Fleet B1 comparison | ☐ |

> 🧪 **G5.13/G5.14 require inducing real faults.** Use `dcgmi test --inject` to inject a field value, or a deliberately out-of-bounds CUDA kernel for an app Xid. Do this on a cordoned node. Record both the fault and the response.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | All 21 health signals defined with source, threshold, action | Read the catalog | Complete |
| **A2** | NPD running and reporting conditions | `kubectl get nodes -o json \| jq .status.conditions` | Custom conditions present |
| **A3** | Fatal Xid detected within 60 s | Injected test | Detected |
| **A4** | App-class Xid does not trigger quarantine | Injected test | Node stays schedulable |
| **A5** | Active probes run on schedule without disturbing jobs | Observe over 1 hr | No job impact |
| **A6** | `-r 2` never runs on an allocated GPU | Inspect the gating logic + observe | Never |
| **A7** | Remediation controller ships in dry-run | Read the config | `dryRun: true` |
| **A8** | 7-day dry-run soak completed, false positives catalogued | Evidence | Documented |
| **A9** | G-RATE limits to 2 nodes / 10 min | Simulated storm | Limit holds |
| **A10** | G-FLOOR prevents dropping below 80 % capacity | Simulated | Holds |
| **A11** | **G-STORM escalates instead of remediating** | Simulate 6 correlated faults | Pages, does not cordon |
| **A12** | Control-plane nodes are never auto-remediated | Simulated CP fault | No action |
| **A13** | Drain respects PDBs and job grace periods | Test with a running job | Job not killed abruptly |
| **A14** | GPU reset recovers a hung GPU | Induce a hang | Recovered |
| **A15** | Talos API reboot works remotely | Execute | Node returns |
| **A16** | PDU power cycle works on an unreachable node | Execute | Node returns |
| **A17** | `release-node.sh` refuses a node that has not passed diagnostics | Try it | Refused |
| **A18** | All alerts route to the correct destination | Test each route | Correct |
| **A19** | `RemediationDryRunStillEnabled` fires after the soak | Wait / simulate | Fires |
| **A20** | 🚪 **Gate G4 passes with all evidence** | `gates/G4-fabric.md` | All ☑ |
| **A21** | 🚪 **Gate G5 passes with all evidence** | `gates/G5-accelerator.md` | All ☑ |
| **A22** | Fleet health report generates and is readable | `health-report.sh` | Clean output |
| **A23** | GPU failure runbook covers every signal in the catalog | Read it | Complete |

---

## ↩️ ROLLBACK

```bash
# Disarm automation immediately — keep detection, stop action
kubectl set env deploy/remediation-controller -n acceleration DRY_RUN=true

# Full stop
kubectl scale deploy/remediation-controller -n acceleration --replicas=0

# Release everything the controller quarantined (only after establishing why)
bash tools/health/release-node.sh --list-controller-quarantined
```

> 💡 Rolling back to `DRY_RUN=true` rather than deleting the controller keeps the detection and alerting working while you fix the rule. Never lose visibility as part of a rollback.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Controller cordons healthy nodes | Threshold too tight, or a flapping signal | Extend `for:`; re-enter dry run; fix the rule |
| Xid not detected | NPD cannot read `/dev/kmsg` on Talos | Verify the hostPath and Talos permissions |
| `dcgmi diag` fails with "GPU in use" | Probe ran against an allocated GPU | Fix the DRA-state gating (A6) |
| Quarantined nodes accumulate | Nobody is working the queue | `QuarantinedNodeStale` alert; assign an owner |
| Rate limiter constantly engaged | A systemic problem, not node failures | Stop. Find the common cause. |
| `nvidia-smi -r` fails | Processes still hold the GPU | Kill them; if it persists, reboot |
| Talos reboot does not return the node | Boot order, or the node hung in POST | PDU power cycle; then PiKVM to see the console |
| PDU cycle does not help | Real hardware failure | Physical service; RMA with `-r 4` evidence |
| Health metrics missing for one node | DCGM exporter crashed | H21 — treat as a fault; restart, then quarantine if persistent |
| Alerts fire but nobody is paged | Alertmanager routing | Test every route explicitly (A18) |
| Gate G4/G5 check fails | Real deficiency | **Do not proceed to Stage 4.** Fix it or record an accepted deviation with an owner and a date. |

---

## 🚫 DO NOT

- **Do not** arm the remediation controller without the 7-day dry-run soak.
- **Do not** auto-quarantine on app-class Xids (13, 31).
- **Do not** auto-remediate correlated failures — escalate them.
- **Do not** auto-remediate control-plane nodes.
- **Do not** run `dcgmi diag -r 2+` on a GPU that has a running workload.
- **Do not** release a quarantined node without a passing diagnostic.
- **Do not** treat missing telemetry as health.
- **Do not** pass G4 or G5 with a check marked "probably fine." Evidence or deviation, nothing else.
- **Do not** start Stage 4 storage work here. Phase 24 owns it.

---

## 📤 HANDOFF

`evidence/phase-23/handoff.md` must state:

1. **🚪 The G4 and G5 gate results** with links to every piece of evidence.
2. **The health signal catalog as implemented**, including any thresholds you changed from this document and why.
3. **The dry-run soak findings** — every false positive you found and how you fixed it. This is the most valuable artifact of the phase.
4. **The remediation guard values in production** (rate, floor, pool %, storm threshold).
5. **Nodes currently quarantined**, why, and the plan for each.
6. **GPUs underperforming their cohort** — even if they pass, note them; Phase 48 will re-check.
7. **Which recovery paths you tested** (reset / Talos reboot / PDU) and any node where one does not work.
8. **Stage 3 declaration** — the acceleration fabric is complete: GPUs are allocatable via DRA, topology-aligned, RDMA-connected, NCCL-tuned, health-monitored, and self-healing. Stage 4 may begin.

---

## ➡️ NEXT

**[PHASE-24 — Storage Architecture & Device Preparation](PHASE-24.md)** — begin Stage 4. Compute is fast; now feed it. Data gravity wins (Law VI).
