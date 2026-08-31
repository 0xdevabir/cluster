# PHASE 53 — Day-2 Operations & Upgrades

| | |
|---|---|
| **Stage** | 9 — Operations & Sustainment |
| **Estimated effort** | 5–6 hours |
| **Depends on** | 47, 50, 52 |
| **Blocks** | 54, 55, 56 |
| **Risk** | 🔴 High — upgrades are the most common cause of self-inflicted outages |
| **Blast radius** | The entire cluster, during every upgrade |
| **Architecture refs** | `ARCHITECTURE.md#x2-failure-domains`, `ULTIMATE-PLAN.md#14-roadmap`, Law IV (*Immutable Infrastructure*) |

---

## 🎯 MISSION

Make **keeping the cluster running a boring, repeatable procedure**. Build the upgrade runbooks for every layer — Talos, Kubernetes, GPU driver, CNI, storage, operators — with the correct order, staged rollouts, verification gates, and rollback paths. Then handle the routine work: node add/remove, hardware replacement, certificate rotation, capacity adjustment.

> 💡 **WHY upgrades deserve the most careful procedure in the project.** Fifty-two phases built a cluster that works. The thing most likely to break it now is a well-intentioned upgrade on a Tuesday afternoon. Every layer has a version, every version has a compatibility matrix, and the failure modes compound: a GPU driver upgrade that requires a kernel version that Talos does not ship until a release that needs a newer Kubernetes. **The upgrade order is not a preference; it is a dependency graph, and getting it wrong is how clusters end up unrecoverable.**

> ⚠️ **The rule that prevents most upgrade disasters: never upgrade two layers at once.** When something breaks after a combined Kubernetes + CNI upgrade, you cannot bisect, cannot attribute, and cannot roll back cleanly. One layer, verified, then the next.

---

## ✅ PREFLIGHT

```bash
# 📊 Current versions of everything
bash tools/ops/version-report.sh 2>/dev/null || \
  kubectl get nodes -o json | jq '.items[0].status.nodeInfo'

# 📊 The performance baseline (Phase 50's pre/post upgrade requirement)
jq '.metadata.timestamp' benchmarks/baselines/baseline.json

# Phase 52's measured full-fleet upgrade estimate
grep -A5 "full-fleet upgrade" evidence/phase-52/handoff.md

# Backups verified recently (Phase 29)
bash tools/backup/backup-report.sh
```

---

## 📦 DELIVERABLES

```
operations/
  version-matrix.md                 # 🎯 what versions work together
  upgrade-order.md                  # 🎯 the dependency graph
  runbooks/
    upgrade-talos.md                # OS
    upgrade-kubernetes.md           # control plane + kubelet
    upgrade-gpu-driver.md           # ⚠️ the most constrained
    upgrade-cilium.md               # ⚠️ datapath — highest risk
    upgrade-storage.md              # Ceph, Mayastor
    upgrade-operators.md            # the long tail
    node-add.md / node-remove.md
    hardware-replacement.md
    certificate-rotation.md
    emergency-procedures.md
tools/ops/
  version-report.sh                 # what is running where
  upgrade-check.sh                  # ⚠️ preflight compatibility validation
  staged-upgrade.sh                 # the wave engine
  post-upgrade-verify.sh
observability/rules/upgrade-alerts.yaml
evidence/phase-53/{preflight,acceptance,handoff,deviations}.md
```

---

## 📋 TASKS

### Task 1 — 🎯 The version matrix and upgrade order

**`operations/version-matrix.md`** — the compatibility facts, written down.

```
┌─────────────────────────────────────────────────────────────┐
│ LAYER                CURRENT      NEXT       CONSTRAINED BY  │
├─────────────────────────────────────────────────────────────┤
│ Talos                v1.9.2       v1.10.x    K8s version     │
│ Kubernetes           1.34.3       1.35.x     Talos, operators│
│ NVIDIA driver        560.35.03    575.x      ⚠️ Talos extension
│ CUDA                 12.6.3       12.8       Driver          │
│ Cilium               1.17.1       1.17.x     K8s, kernel     │
│ Rook/Ceph            1.16.2/19.2  ...        K8s             │
│ Mayastor             2.7.3        ...        K8s, kernel     │
│ Kueue                0.10.1       ...        K8s (⚠️ DRA API)│
│ GPU Operator         24.9.x       ...        Driver, K8s     │
└─────────────────────────────────────────────────────────────┘
```

**🎯 The upgrade order — a dependency graph, not a preference:**

```
1. Backups verified + a pre-upgrade --full benchmark (Phase 50)
   ↓
2. OPERATORS that support the target K8s version (forward-compatible ones first)
   ↓
3. KUBERNETES control plane  ← one minor version at a time, NEVER skip
   ↓
4. KUBELET / node components (via Talos)
   ↓
5. TALOS OS (may combine with 4 if the release pairs them)
   ↓
6. CNI (Cilium)             ← ⚠️ highest datapath risk; alone, never with others
   ↓
7. GPU DRIVER + operator    ← ⚠️ Talos extension; requires a node reboot
   ↓
8. STORAGE (Ceph, Mayastor) ← ⚠️ slowest; one at a time; wait for health
   ↓
9. Post-upgrade --full benchmark + comparison (Phase 50)
```

> ⚠️ **Kubernetes minor versions must never be skipped.** 1.34 → 1.36 is not supported; you must pass through 1.35. Skipping produces API-storage migration failures that are painful to recover from. The same applies to Talos releases that carry storage-format changes.

> ⚠️ **The GPU driver on Talos is the most constrained upgrade in the stack**, because the driver ships as a Talos system extension baked into an image (Phase 09's Image Factory schematic). Upgrading it means a new schematic, a new image, and a node reboot — it is an OS upgrade wearing a driver's clothes. **Plan it as one.**

**`tools/ops/upgrade-check.sh`** — refuses an upgrade that violates the matrix:
```
Checks before ANY upgrade:
  ✓ Target version is in the compatibility matrix
  ✓ No version skip (K8s minor, Talos)
  ✓ All CRD API versions used are still served in the target
  ✓ Deprecated APIs in use (⚠️ scan the cluster, not just the manifests)
  ✓ Backups verified within 24 h (Phase 29)
  ✓ Pre-upgrade benchmark recorded (Phase 50)
  ✓ No active incidents, no exhausted error budgets (Phase 47)
  ✓ Maintenance window declared and users notified
```

---

### Task 2 — The staged upgrade engine

**`tools/ops/staged-upgrade.sh`** — the same wave pattern as Phase 49, with upgrade-specific gates.

| Wave | Scope | Soak | Gate before proceeding |
|---|---|---|---|
| **Canary** | 1 node | 2 h | Node Ready; a test workload runs; `--quick` benchmark |
| **Wave 1** | 3 nodes | 4 h | Same + no new alerts |
| **Wave 2** | 10 % | 12 h | Same + a real workload completed on upgraded nodes |
| **Wave 3** | 50 % | 24 h | Same + `--standard` benchmark |
| **Final** | 100 % | — | `--full` benchmark; compare to pre-upgrade |

**Mandatory guards:**

| Guard | Rule |
|---|---|
| **Halt on any gate failure** | And roll back that wave |
| **Never all control-plane nodes at once** | One at a time, verify quorum between |
| Drain respects PDBs and checkpointing (Phase 33) | Long training jobs get to checkpoint |
| **Stagger reboots per power circuit** | Phase 32's inrush constraint |
| Storage nodes: wait for `HEALTH_OK` between nodes | ⚠️ Never drain two OSD nodes concurrently |
| Inference services: verify replicas stay above minimum | Phase 40's SLO |
| Abort if any SLO burn-rate alert fires | Phase 47 |

> ⚠️ **Storage upgrades need a stricter rule than everything else: wait for full health between nodes, not just Ready.** Draining a second Ceph node while the first is still backfilling can push a pool below its minimum replica count. `HEALTH_OK`, not `HEALTH_WARN`, is the gate.

> 💡 **The 24-hour soak at 50 % is where slow-burn problems appear** — a memory leak, a subtle datapath issue, a driver bug that only manifests under sustained load. Resist compressing it. Phase 52 measured that a full-fleet upgrade takes 8+ hours anyway; a multi-day staged upgrade is the honest plan.

---

### Task 3 — Per-layer runbooks: the specific hazards

**Talos (`upgrade-talos.md`):**
```
talosctl upgrade --nodes <node> --image <factory-image-with-schematic>
⚠️ The image must carry the SAME system extensions (NVIDIA, etc.) — Phase 09.
   Upgrading to a stock image silently removes the GPU driver.
⚠️ `--preserve` for control-plane nodes (keeps etcd data).
⚠️ Talos upgrades are A/B: the previous version remains bootable. That is
   your rollback — verify it works on the canary before proceeding.
```

**Kubernetes (`upgrade-kubernetes.md`):**
```
⚠️ Control plane first, one node at a time, verify etcd quorum between each.
⚠️ Check for removed APIs BEFORE upgrading: `kubectl-convert`, Pluto, or
   the API-server deprecation metrics. A removed API breaks controllers silently.
⚠️ kubelet may be at most 3 minor versions behind the API server — but do not
   rely on that. Upgrade nodes promptly after the control plane.
```

**Cilium (`upgrade-cilium.md`) — the highest-risk single upgrade:**
```
⚠️ The datapath carries ALL traffic. A failed Cilium upgrade partitions the cluster.
· Read the upgrade notes for THIS version pair. Every time. They matter.
· Run `cilium preflight` — it checks CRD and identity compatibility
· Upgrade with `--set upgradeCompatibility=<old version>` where the docs say to
· ⚠️ Watch for identity churn: a bad upgrade can invalidate every identity,
     dropping all policy-permitted traffic at once
· Canary a single node; verify pod-to-pod, pod-to-service, BGP, and RDMA paths
· 📊 Run B3 and B5 on the canary before proceeding — a datapath regression here
     is invisible functionally and expensive
```

**GPU driver (`upgrade-gpu-driver.md`):**
```
⚠️ New Talos schematic → new image → node reboot. Treat as an OS upgrade.
⚠️ Driver and CUDA and the GPU Operator versions must be compatible together.
· Verify the driver on ONE node: nvidia-smi, dcgmi diag -r 2, B1 benchmark
· ⚠️ Check that DRA ResourceSlices republish correctly (Phase 19)
· ⚠️ Verify NCCL still selects the IB transport (Phase 22's A2)
· Known-good rollback: the previous Talos image with the previous schematic
```

**Storage (`upgrade-storage.md`):**
```
⚠️ Rook and Ceph versions are coupled — upgrade Rook first, then Ceph.
⚠️ NEVER upgrade Ceph while a rebalance is in progress.
⚠️ Mayastor: verify its own etcd is healthy and backed up first (Phase 26/29).
· One OSD node at a time; HEALTH_OK between each
· Expect this to be the longest phase of any upgrade cycle
```

---

### Task 4 — Routine operations

The non-upgrade work that happens weekly.

| Runbook | Key points |
|---|---|
| **Node add** | Phase 08/09 pipeline → Phase 14 readiness gate → `dcgmi diag -r 3` acceptance → label → uncordon. ⚠️ Check power budget first (Phase 32). |
| **Node remove** | Cordon → drain (respect PDBs, let jobs checkpoint) → ⚠️ **if it holds OSDs, mark them out and wait for rebalance** → remove from inventory, Tinkerbell, DNS, monitoring |
| **Hardware replacement** | Same as remove, plus: preserve the node name if possible (less churn), re-run Phase 01 discovery, verify against the capability matrix, **re-benchmark before returning to service** |
| **GPU replacement** | ⚠️ New GPU may differ in model/VRAM → update labels, verify DRA slices, check it does not break `matchAttribute` homogeneity (R-15) |
| **Disk replacement** | Phase 24's serial-gated preparation; never reuse a device without wiping |
| **Certificate rotation** | Kubernetes PKI (Talos-managed), step-ca intermediate, service certs (cert-manager). ⚠️ **Test the rotation before the expiry**, not after |
| **Capacity adjustment** | Kueue quota changes (Phase 30) — verify Σ nominal ≤ allocatable |
| **Scaling a tenant** | Phase 46's `onboard-tenant.sh` extended |

> ⚠️ **Certificate expiry is a classic self-inflicted outage** and it is entirely preventable. Alert at 30 days before expiry for every certificate in the cluster — Kubernetes PKI, step-ca root and intermediate, ingress certs, service mesh certs. **Include the step-ca root**, which may have a multi-year life and will be forgotten precisely because it is long-lived.

---

### Task 5 — The maintenance calendar

Predictability is a feature. Users plan around a known window; they cannot plan around surprises.

| Cadence | Work | Window |
|---|---|---|
| **Weekly** | Backup verification (Phase 29); etcd defrag; alert review | Automated |
| **Monthly** | Operator patch upgrades; `--full` benchmark; security patching (Phase 55); SLO review (Phase 47) | 2nd Tue, 02:00–06:00 |
| **Quarterly** | Kubernetes minor upgrade; Talos upgrade; driver upgrade; DR drill (Phase 54) | Scheduled ≥ 2 weeks ahead |
| **Annually** | Certificate root rotation; hardware refresh review; capacity plan (Phase 56) | Planned |
| Ad hoc | Security-critical patches | ⚠️ Expedited but still staged |

> 💡 **Publish the calendar on the status page (Phase 46) and announce each window twice** — a week ahead and an hour ahead. The single most common user complaint about maintenance is not the downtime; it is not knowing about it. Also state clearly what is affected: "batch jobs will be preempted and resume from checkpoint; inference is unaffected."

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | Version matrix documents every layer and its constraints | Read | Complete |
| **A2** | 🎯 **Upgrade order documented as a dependency graph** | Read | Documented |
| **A3** | **`upgrade-check.sh` refuses a version skip** | 🧪 Try 1.34 → 1.36 | Refused |
| **A4** | Refuses without a recent verified backup | 🧪 Try | Refused |
| **A5** | Refuses without a pre-upgrade benchmark | 🧪 Try | Refused |
| **A6** | Detects deprecated APIs in use in the live cluster | 🧪 Create a deprecated resource | Detected |
| **A7** | Staged upgrade engine executes waves with gates | 🧪 Run a real upgrade | Staged |
| **A8** | 🧪 **A failing gate halts and rolls back that wave** | Inject a failure | Halts |
| **A9** | Control-plane nodes upgraded one at a time | Observe | One at a time |
| **A10** | Drain lets training jobs checkpoint before eviction | 🧪 Upgrade with a job running | Checkpointed |
| **A11** | **Storage upgrades wait for `HEALTH_OK` between nodes** | Observe | Waits |
| **A12** | Inference replicas never drop below minimum during an upgrade | 🧪 Observe | Never |
| **A13** | Reboots staggered per power circuit | Observe | Staggered |
| **A14** | 🧪 **A full Talos upgrade executed end to end** | Do it | Completed |
| **A15** | 🧪 A Talos rollback tested on the canary | Roll back one node | Works |
| **A16** | 🧪 **A Cilium upgrade executed with B3/B5 verification on the canary** | Do it | Verified |
| **A17** | 🧪 A GPU driver upgrade executed; DRA and NCCL verified after | Do it | Verified |
| **A18** | 📊 **Pre/post `--full` benchmark comparison for each upgrade** | Phase 50 workflow | Compared |
| **A19** | Node add runbook executed for a real node | Do it | Works |
| **A20** | Node remove drains Ceph OSDs before removal | 🧪 Remove a storage node | Drained first |
| **A21** | **Certificate expiry alerts fire at 30 days for every certificate** | Audit + test | All covered |
| **A22** | step-ca root expiry is monitored | Verify | Monitored |
| **A23** | 🧪 Certificate rotation tested before expiry | Rotate one | Works |
| **A24** | Maintenance calendar published on the status page | Browser | Published |
| **A25** | Users notified twice per window | Observe one window | Notified |
| **A26** | 📊 Total fleet upgrade time measured against Phase 52's estimate | Timed | Measured |

---

## ↩️ ROLLBACK

Each layer has a distinct rollback path — document and **test** each:

```bash
# Talos: A/B partition — previous version remains bootable
talosctl rollback --nodes <node>

# Kubernetes: ⚠️ NOT reversible once API storage migration has occurred.
#   The rollback is: restore etcd from the pre-upgrade snapshot (Phase 29).
#   This is why the backup gate (A4) exists.

# Cilium: helm rollback, but ⚠️ identity/CRD changes may not reverse cleanly
helm rollback cilium <revision> -n kube-system

# GPU driver: revert to the previous Talos schematic image
# Storage: ⚠️ Ceph upgrades are generally NOT reversible. Forward-fix only.
```

> ⚠️ **Two layers are effectively one-way: Kubernetes (after storage migration) and Ceph.** For those, the rollback is a restore from backup, which means downtime and data loss back to the snapshot. **This is precisely why the canary and staged waves exist** — the goal is to never need the irreversible path. State this in the runbook so nobody assumes an easy undo.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Node does not come back after a Talos upgrade | Wrong image, or a missing system extension | ⚠️ Check the schematic includes NVIDIA; roll back the partition |
| GPUs disappear after an OS upgrade | Stock image without the extension | The most common Talos+GPU mistake — rebuild the schematic |
| Controllers crash-loop after a K8s upgrade | Removed API | A6 should have caught it; upgrade the controller |
| Cluster network breaks during a Cilium upgrade | Identity or CRD incompatibility | Follow the version-pair notes; `upgradeCompatibility` |
| Traffic drops but pods are Ready | Policy identities invalidated | Cilium-specific; check identity count before/after |
| Ceph stuck in `HEALTH_WARN` during an upgrade | Rebalance in progress | ⚠️ **Stop.** Wait for `HEALTH_OK` before the next node. |
| Upgrade takes far longer than estimated | Serialized waits; drain timeouts | Phase 52's measurement; adjust the window |
| Training jobs killed rather than checkpointed | Drain grace period too short | Phase 33's grace periods |
| Certificate expired in production | No 30-day alert | A21 — inexcusable and preventable |
| Post-upgrade benchmark regressed | The upgrade cost performance | Phase 50's override process — decide explicitly |
| Cannot roll back Kubernetes | Storage migration happened | Restore etcd; this is why backups gate upgrades |

---

## 🚫 DO NOT

- **Do not** upgrade two layers at once.
- **Do not** skip a Kubernetes minor version.
- **Do not** upgrade without a verified backup and a pre-upgrade benchmark.
- **Do not** upgrade to a Talos image without the required system extensions.
- **Do not** drain a second storage node before `HEALTH_OK`.
- **Do not** compress the soak periods.
- **Do not** upgrade during an active incident or an exhausted error budget.
- **Do not** assume Kubernetes or Ceph upgrades are reversible.
- **Do not** let any certificate reach 30 days from expiry without an alert.

---

## 📤 HANDOFF

`evidence/phase-53/handoff.md` must state:

1. **🎯 The version matrix and upgrade order**, and which layers are one-way.
2. **📊 The measured duration** of each upgrade type at current scale, against Phase 52's estimate.
3. **🧪 Which upgrades have actually been executed** in this phase, and what went wrong.
4. **The rollback path per layer**, and which have been tested.
5. **The maintenance calendar** and the notification process.
6. **Certificate inventory** with expiry dates and alert coverage — including the step-ca root.
7. **Known upgrade hazards** discovered, especially any that are not in upstream documentation.
8. **📊 Any performance change attributable to an upgrade** performed here (Phase 50's comparison).

---

## ➡️ NEXT

**[PHASE-54 — Chaos Engineering & Disaster Recovery (G10)](PHASE-54.md)** — stop waiting for failures to teach you; cause them deliberately, on your schedule.
