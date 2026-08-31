# PHASE 14 — Node Onboarding & Label Taxonomy

| | |
|---|---|
| **Stage** | 2 — Kubernetes Substrate |
| **Estimated effort** | 4 hours |
| **Depends on** | 13 |
| **Blocks** | 18, 24, 30, 31 — every scheduling decision |
| **Risk** | 🟡 Medium — wrong labels cause wrong placement, which surfaces as mysterious performance loss |
| **Blast radius** | Scheduling correctness across the fleet |
| **Architecture refs** | `ARCHITECTURE.md#l52-node-feature-discovery-label-taxonomy`, `#x3--naming-labeling--namespace-taxonomy`, R-15 |

---

## 🎯 MISSION

Make every node **self-describing and self-gating**: hardware facts become labels automatically, a quarantine taint keeps unvalidated nodes out of the workload pool until they pass a readiness gate, and the labels the scheduler needs — GPU model, VRAM, RDMA capability, topology alignment, rack, power domain — are present, correct, and continuously reconciled.

> 💡 **WHY labels are load-bearing, not metadata:** every scheduling guarantee in this project is expressed as a label selector. Kueue's `ResourceFlavor` binds quota to `nexus.io/gpu.model` — that is what prevents a 4090 and a 3090 landing in the same gang (R-15). Topology-Aware Scheduling reads `nexus.io/leaf-domain` — that is what keeps a 16-node allreduce inside one switch. Power-aware admission reads `nexus.io/rack`. **A wrong label is a wrong placement, and a wrong placement is invisible until you benchmark.**

> 💡 **WHY a quarantine gate:** a node that boots and joins is not a node that works. Its GPU may have fallen off the bus, its NIC may have negotiated at 10 GbE, its NVMe may be worn out. Without a gate, that node silently accepts a 16-node training job and becomes the straggler that halves your throughput.

---

## ✅ PREFLIGHT

```bash
bash tools/net/cilium-verify.sh          # Phase 13 green
kubectl get nodes                        # all Ready
kubectl top nodes                        # metrics-server may not exist yet — that is fine

# Inventory is complete and validated
task validate:inventory

# Nodes carry the quarantine taint from their Talos config (Phase 09)
kubectl get nodes -o json | jq -r '.items[] | "\(.metadata.name) \(.spec.taints // [])"'
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/infra/nfd/
  values.yaml
  nodefeaturerules.yaml           # NFD rules → nexus.io/* labels
charts/nexus-node-labeler/        # first-party: facts NFD cannot discover
  Chart.yaml values.yaml
  templates/{daemonset,rbac,configmap}.yaml
images/node-labeler/
  Dockerfile main.go|main.py
clusters/nexus-prod/infra/node-readiness/
  readiness-job.yaml              # the gate that clears quarantine
  rbac.yaml
tools/nodes/
  label-audit.sh                  # inventory ↔ live labels reconciliation
  onboard-node.sh
  drain-node.sh
docs/operations/
  node-lifecycle.md               # join → quarantine → validate → pool → drain → retire
  label-taxonomy.md
evidence/phase-14/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Source |
|---|---|---|
| Node Feature Discovery | `0.17.0` | `oci://registry.k8s.io/nfd/charts/node-feature-discovery` |
| metrics-server | `0.7.2` | `oci://ghcr.io/kubernetes-sigs/metrics-server/charts/metrics-server` |
| node-labeler (first-party) | `0.1.0` | built in Phase 42; runs from a local build until then |

---

## 📋 TASKS

### Task 1 — Node Feature Discovery

NFD discovers CPU features, PCI devices, kernel config, and memory topology automatically.

**`clusters/nexus-prod/infra/nfd/values.yaml`**
```yaml
worker:
  config:
    core: { sleepInterval: 60s }
    sources:
      pci:
        deviceClassWhitelist: ["03","0b40","12","02"]   # display, co-proc, accel, network
        deviceLabelFields: [vendor, device, subsystem_vendor]
      cpu:
        cpuid: { attributeWhitelist: [AVX512F, AVX512BW, AVX512VNNI, AMXTILE, AMXBF16, AVX2] }
      usb: { deviceClassWhitelist: [] }
  tolerations:
    - { key: nexus.io/quarantine, operator: Exists, effect: NoSchedule }
    - { key: nexus.io/archetype,  operator: Exists, effect: NoSchedule }
    - { key: nvidia.com/gpu,      operator: Exists, effect: NoSchedule }
master:
  replicaCount: 2
  extraLabelNs: [ "nexus.io", "nvidia.com", "feature.node.kubernetes.io" ]
```

> ⚠️ **The tolerations matter.** NFD must run on quarantined and tainted nodes — it is what produces the labels the readiness gate evaluates. A NFD DaemonSet that cannot schedule onto a quarantined node creates a deadlock: no labels → no validation → never leaves quarantine.

**`nodefeaturerules.yaml`** — translate raw NFD features into the NEXUS taxonomy:
```yaml
apiVersion: nfd.k8s-sigs.io/v1alpha1
kind: NodeFeatureRule
metadata: { name: nexus-hardware }
spec:
  rules:
    - name: "nvidia-gpu-present"
      labels: { "nexus.io/gpu.present": "true" }
      matchFeatures:
        - feature: pci.device
          matchExpressions: { vendor: { op: In, value: ["10de"] }, class: { op: In, value: ["0300","0302"] } }

    - name: "mellanox-nic-present"
      labels: { "nexus.io/nic.mellanox": "true" }
      matchFeatures:
        - feature: pci.device
          matchExpressions: { vendor: { op: In, value: ["15b3"] } }

    - name: "rdma-capable"
      labels: { "nexus.io/nic.rdma": "true" }
      matchFeatures:
        - feature: kernel.loadedmodule
          matchExpressions: { ib_core: { op: Exists }, mlx5_ib: { op: Exists } }

    - name: "hugepages-1g"
      labels: { "nexus.io/hugepages.1g": "true" }
      matchFeatures:
        - feature: memory.nv
          matchExpressions: { hugepages-1Gi: { op: Gt, value: ["0"] } }
```

---

### Task 2 — The NEXUS node labeler

NFD cannot discover the facts that matter most: GPU model strings, VRAM size, GPU↔NIC NUMA alignment, disk wear, or PCIe link width under load. A small first-party DaemonSet fills the gap.

**What it must compute and publish:**

| Label | Source | Why the scheduler needs it |
|---|---|---|
| `nexus.io/gpu.model` | `nvidia-smi --query-gpu=name`, slugified (`rtx-4090`) | **Kueue `ResourceFlavor` binding — prevents mixed-model gangs (R-15)** |
| `nexus.io/gpu.count` | `nvidia-smi -L \| wc -l` | Capacity accounting |
| `nexus.io/gpu.vram-gb` | `nvidia-smi --query-gpu=memory.total` | VRAM admission control (Phase 19) |
| `nexus.io/gpu.compute-capability` | `nvidia-smi --query-gpu=compute_cap` | Image/kernel compatibility |
| `nexus.io/gpu.nvlink` | `nvidia-smi nvlink -s` | Intra-node parallelism strategy |
| `nexus.io/gpu.p2p` | `p2pBandwidthLatencyTest` result (cached) | Tensor-parallel feasibility |
| `nexus.io/gpu.gpudirect-rdma` | `nvidia_peermem` module + inventory | **NCCL configuration (Phase 22)** |
| `nexus.io/gpu.pcie-width` | `nvidia-smi --query-gpu=pcie.link.width.max` | Detects degraded slots/risers |
| `nexus.io/nic.speed-gbps` | `/sys/class/net/<if>/speed` | **Training-pool eligibility (R-01)** |
| `nexus.io/nic.sriov-vfs` | `sriov_numvfs` | Phase 21 capacity |
| `nexus.io/numa.nodes` | `/sys/devices/system/node/node*` | Topology Manager expectations |
| **`nexus.io/topology.aligned`** | GPU `numa_node` == NIC `numa_node` | **Law III — the single most important derived label** |
| `nexus.io/storage.tier0-gb` | Sum of scratch device sizes | Scratch capacity scheduling |
| `nexus.io/storage.nvme-wear-pct` | `nvme smart-log` percentage_used | Predictive replacement (R-06) |
| `nexus.io/rack`, `nexus.io/pdu` | From inventory via a ConfigMap | Failure domains, power budget |
| `nexus.io/leaf-domain`, `nexus.io/spine-domain` | LLDP neighbor → `switch-ports.yaml` | **Kueue TAS (Phase 31)** |
| `topology.kubernetes.io/zone` | = rack | Standard topology spread |

**Two critical implementation notes:**

1. **LLDP is the ground truth for network topology.** Read the node's LLDP neighbor and cross-reference `inventory/network/switch-ports.yaml`. **If they disagree, the node is miscabled** — label it `nexus.io/topology.mismatch=true` and let the readiness gate fail it. At 100 nodes, miscabling is certain; automated detection is the only defense.

2. **`topology.aligned` must be computed, not assumed.** Read `/sys/bus/pci/devices/<gpu-bdf>/numa_node` and `/sys/class/net/<nic>/device/numa_node` and compare. On a single-NUMA consumer board both read `0` (or `-1`) and alignment is trivially true; on a dual-socket EPYC it is the difference between full and half memory bandwidth.

```bash
# The DaemonSet writes labels via the Kubernetes API using a narrowly-scoped RBAC:
#   patch on nodes/status and nodes  — nothing else.
# It reconciles every 5 minutes so labels track reality (e.g. a GPU that fell off the bus).
```

---

### Task 3 — The taint taxonomy

```
nexus.io/quarantine=true:NoSchedule            # new/failed nodes — cleared by the gate
nexus.io/archetype=storage:NoSchedule          # only Ceph tolerates
nexus.io/archetype=infra:NoSchedule            # only platform services tolerate
nvidia.com/gpu=present:NoSchedule              # CPU jobs never squat on GPU nodes
node.nexus.io/unhealthy=<reason>:NoExecute     # auto-remediation (Phase 23)
node.nexus.io/maintenance=true:NoSchedule      # planned work (Phase 53)
```

> 💡 **`nvidia.com/gpu=present:NoSchedule` is a utilization control, not a safety one.** Without it, a CPU-only batch job lands on a GPU node, occupies its cores, and blocks a GPU job from being scheduled there. The GPU sits idle while a queue waits. Requiring an explicit toleration makes GPU nodes opt-in.

**Standard toleration blocks** (document in `label-taxonomy.md` so every later phase copies them):
```yaml
# A GPU workload
tolerations:
  - { key: nvidia.com/gpu, operator: Exists, effect: NoSchedule }
# A platform service
tolerations:
  - { key: nexus.io/archetype, operator: Equal, value: infra, effect: NoSchedule }
# A node-level agent that must run everywhere, including quarantine
tolerations:
  - { operator: Exists }        # ⚠️ use sparingly; audit every use
```

---

### Task 4 — The readiness gate

**`clusters/nexus-prod/infra/node-readiness/readiness-job.yaml`** — a Job that runs on a quarantined node, validates it, and either clears the taint or leaves it and raises an alert.

**The checks, in order:**

| # | Check | Command | Fail action |
|---|---|---|---|
| R1 | Kubelet healthy, node `Ready` | API status | Fail |
| R2 | Labels present and non-empty for all required keys | Label query | Fail — labeler problem |
| R3 | CPU count matches inventory | `nproc` vs. NodeSpec | Fail — wrong machine or a dead core |
| R4 | Memory within 5 % of inventory | `/proc/meminfo` | Fail — a DIMM died |
| R5 | GPU count matches inventory | `nvidia-smi -L` | Fail — GPU fell off the bus |
| R6 | **GPU PCIe link at full width** | `nvidia-smi --query-gpu=pcie.link.width.max` | Fail — riser/slot problem |
| R7 | GPU passes a DCGM short diagnostic | `dcgmi diag -r 1` | Fail — hardware fault |
| R8 | **NIC negotiated at the expected speed** | `/sys/class/net/<if>/speed` | Fail — **R-01 tripwire** |
| R9 | MTU 9000 on the data interface | `ip link` | Fail |
| R10 | **LLDP neighbor matches `switch-ports.yaml`** | LLDP query | Fail — **miscabled** |
| R11 | `topology.aligned == true` for training-pool candidates | Label | Demote from `pool=training` |
| R12 | Scratch NVMe present and writable at expected size | `fio` 10 s smoke | Fail |
| R13 | NVMe wear < 80 % | `nvme smart-log` | Warn; < 90 % fail |
| R14 | Clock within 50 ms of the NTP source | `chronyc`/Talos time | Fail |
| R15 | Pod-to-pod connectivity to a known-good node | `iperf3` 10 s, ≥ 80 % of expected | Fail |
| R16 | Hugepages allocated as configured | `/proc/meminfo` | Fail |

**On success:** remove the `nexus.io/quarantine` taint and label, set `nexus.io/pool` from the archetype rules, and record the validation timestamp in a node annotation.

**On failure:** leave quarantine in place, write the failing check to a node annotation, and emit an event + alert. The node stays in the cluster (so it can be inspected) but takes no work.

```bash
# tools/nodes/onboard-node.sh <node>
kubectl create job --from=cronjob/node-readiness "readiness-$NODE-$(date +%s)" \
  --dry-run=client -o yaml \
  | yq '.spec.template.spec.nodeName = env(NODE)' | kubectl apply -f -
kubectl wait --for=condition=complete job/readiness-... --timeout=15m
kubectl get node "$NODE" -o jsonpath='{.metadata.annotations.nexus\.io/readiness}'
```

> ⚠️ **The gate must be re-runnable, and it must run again after any hardware change or reboot-after-repair.** Phase 53's node-replacement procedure calls it; Phase 23's auto-remediation calls it after a remediation attempt.

---

### Task 5 — Label audit

**`tools/nodes/label-audit.sh`** — reconciles the live cluster against `inventory/`. Runs in CI and on a schedule.

```bash
#!/usr/bin/env bash
# Compare live node labels to inventory. Any drift is a finding.
source "$(dirname "$0")/../lib/common.sh"
fail=0

for f in "$ROOT"/inventory/nodes/*.yaml; do
  node=$(yq -r '.metadata.name' "$f")
  kubectl get node "$node" >/dev/null 2>&1 || { warn "$node: in inventory, not in cluster"; continue; }

  cmp() {   # cmp <label> <expected>
    local got; got=$(kubectl get node "$node" -o jsonpath="{.metadata.labels.$1}" 2>/dev/null)
    [[ "$got" == "$2" ]] || { warn "$node: $1 expected=$2 got=${got:-<unset>}"; fail=1; }
  }
  cmp 'nexus\.io/archetype'     "$(yq -r '.spec.archetype' "$f")"
  cmp 'nexus\.io/rack'          "$(yq -r '.spec.location.rack' "$f")"
  cmp 'nexus\.io/gpu\.count'    "$(yq -r '[.spec.gpus[]?] | length' "$f")"
  cmp 'nexus\.io/nic\.speed-gbps' "$(yq -r '[.spec.nics[]|select(.role=="cluster")][0].speedGbps' "$f")"
done

# Nodes in the cluster that are not in inventory — a serious finding
kubectl get nodes -o name | sed 's|node/||' | while read -r n; do
  [[ -f "$ROOT/inventory/nodes/$n.yaml" ]] || { warn "$n: in cluster, NOT in inventory"; fail=1; }
done

[[ $fail -eq 0 ]] && ok "labels match inventory" || die "LABEL DRIFT DETECTED"
```

> 💡 **"In cluster, not in inventory" is a security finding, not a hygiene one.** An unknown node that joined the cluster is either a provisioning mistake or something worse. Alert on it.

---

### Task 6 — Node lifecycle documentation

**`docs/operations/node-lifecycle.md`**

```
 ┌──────────┐  provision   ┌────────────┐   labeler    ┌──────────────┐
 │ inventory│─────────────►│ joined     │─────────────►│ quarantined  │
 │ (planned)│  Phase 08/09 │ NotReady   │   Phase 14   │ +labels      │
 └──────────┘              └────────────┘              └──────┬───────┘
                                                              │ readiness gate
                            ┌─────────────────────────────────┴──────┐
                            │ PASS                              FAIL │
                            ▼                                        ▼
                     ┌─────────────┐                        ┌────────────────┐
                     │ IN POOL     │                        │ QUARANTINED    │
                     │ schedulable │                        │ + alert        │
                     └──────┬──────┘                        └───────┬────────┘
          ┌─────────────────┼─────────────────┐                     │ repair
          │ maintenance     │ unhealthy       │ retire              │
          ▼                 ▼                 ▼                     │
   ┌─────────────┐  ┌───────────────┐  ┌───────────┐                │
   │ cordoned    │  │ tainted       │  │ drained,  │                │
   │ +drained    │  │ NoExecute     │  │ removed,  │◄───────────────┘
   │ (Phase 53)  │  │ (Phase 23)    │  │ wiped     │
   └─────────────┘  └───────────────┘  └───────────┘
```

**Include the drain procedure** — it is used by Phases 23, 53, and 54:
```bash
# tools/nodes/drain-node.sh <node> [--force]
kubectl cordon "$NODE"
# ⚠️ Respect PodDisruptionBudgets and give gang jobs time to checkpoint
kubectl drain "$NODE" \
  --ignore-daemonsets \
  --delete-emptydir-data \
  --grace-period=120 \
  --timeout=600s
# Verify Ceph is healthy before draining a storage node (Phase 27 adds this check)
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | NFD runs on every node, including quarantined and tainted ones | `kubectl get pods -n nfd -o wide` | One per node |
| **A2** | NFD labels appear on all nodes | `kubectl get nodes --show-labels \| grep feature.node` | Present |
| **A3** | Node labeler runs and publishes all `nexus.io/*` labels | Query a GPU node | All labels from Task 2 present |
| **A4** | `nexus.io/gpu.model` is correct and slugified | Compare with `nvidia-smi` | Matches |
| **A5** | **`nexus.io/topology.aligned` is computed, not hardcoded** | Read the labeler source; verify on a node | Derived from sysfs |
| **A6** | `nexus.io/leaf-domain` derived from LLDP, cross-checked with `switch-ports.yaml` | Query a node; compare | Matches |
| **A7** | **A deliberately miscabled node is flagged** | Move one cable to a different port; wait a cycle | `topology.mismatch=true`; readiness fails |
| **A8** | All six taints in the taxonomy are applied where expected | `kubectl get nodes -o json \| jq '.items[].spec.taints'` | Correct per archetype |
| **A9** | A CPU-only pod cannot schedule onto a GPU node without a toleration | Create one; observe | Stays Pending |
| **A10** | Readiness gate runs all 16 checks | Inspect the job's output | All present |
| **A11** | A passing node has quarantine cleared and `nexus.io/pool` set | Run the gate | Taint removed, pool labeled |
| **A12** | **A failing node stays quarantined with the reason annotated** | Simulate a failure (e.g. set an expected NIC speed higher) | Quarantine retained; annotation set; alert fires |
| **A13** | R8 (NIC speed) catches a degraded link | Force a port to 10 GbE | Gate fails |
| **A14** | R6 (GPU PCIe width) catches a degraded slot | Simulate or use a known-bad node | Gate fails |
| **A15** | Label audit detects a manually-changed label | `kubectl label node <n> nexus.io/rack=wrong --overwrite` | `label-audit.sh` fails |
| **A16** | Label audit detects a node in the cluster but not in inventory | Temporarily move an inventory file | Detected |
| **A17** | Labels are reconciled — a manual change is reverted within one cycle | Change a label; wait 5 min | Reverted by the labeler |
| **A18** | Node lifecycle document covers all states and transitions | Read it | Complete |
| **A19** | Drain respects PDBs and uses a 120 s grace period | Read `drain-node.sh`; test | Correct |
| **A20** | Onboarding a fresh node end-to-end works unattended | Provision + onboard one node | Reaches the pool with no manual labeling |

---

## ↩️ ROLLBACK

```bash
helm -n nfd uninstall node-feature-discovery
kubectl delete ds -n kube-system nexus-node-labeler
# Re-apply quarantine to everything if labels became untrustworthy:
kubectl taint nodes --all nexus.io/quarantine=true:NoSchedule --overwrite
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| NFD pods Pending on quarantined nodes | Missing tolerations | Add them (Task 1). This is a deadlock if missed. |
| `nexus.io/*` labels missing | Labeler RBAC lacks node patch, or the DaemonSet cannot tolerate a taint | Check the pod logs and the ClusterRole |
| `gpu.model` differs between identical cards | Vendor string variation (`NVIDIA GeForce RTX 4090` vs `RTX 4090`) | Normalize aggressively in the labeler; **an inconsistent model label splits a homogeneous pool and breaks gang formation** |
| `topology.aligned=false` on a single-NUMA machine | `numa_node` reads `-1` | Treat `-1` as "no NUMA constraint" → aligned. Document the rule. |
| LLDP returns nothing | LLDP disabled on the switch, or `lldpd` not running on the node | Enable LLDP on switch ports (Phase 03 template); Talos needs an extension or the labeler must read it another way |
| Readiness gate times out | DCGM diag takes minutes on some GPUs | Use `-r 1` (short) in the gate; run `-r 3` in Phase 23's periodic health check |
| Gate passes but the node is still bad | A check is missing or too lenient | Add the check. Every field failure that reached the pool is a gate bug — record it. |
| Labels flap between values | Two controllers writing the same label | Only the labeler may write `nexus.io/*`. Find and stop the other writer. |
| Node joined that is not in inventory | Provisioning error or a stale Hardware CR | Investigate as a security finding, then either add it to inventory or remove it |

---

## 🚫 DO NOT

- **Do not** label nodes by hand. Every `nexus.io/*` label comes from the labeler; manual edits are reverted and flagged.
- **Do not** skip the quarantine gate to "get the node into the pool faster." That is precisely how a straggler enters a training gang.
- **Do not** put GPU sharing or DRA configuration here. Phases 18–19.
- **Do not** create Kueue ResourceFlavors here. Phase 30 — but they will consume these labels, so get the labels right.
- **Do not** allow a node with `topology.mismatch=true` into any pool.
- **Do not** use `tolerations: [{operator: Exists}]` casually. Every use must be justified and audited.
- **Do not** clear quarantine manually on a node that failed the gate. Fix the hardware.

---

## 📤 HANDOFF

`evidence/phase-14/handoff.md` must state:

1. **The complete label taxonomy as actually implemented**, with an example node's full label set. Phases 19, 30, and 31 build selectors against these exact keys.
2. **The taint taxonomy** and the standard toleration blocks.
3. **Which nodes are in which pool**, and which failed the readiness gate and why.
4. **Any miscabling found** by the LLDP cross-check — this is a physical remediation list.
5. **Nodes with `topology.aligned=false`** — Phase 20 and Phase 31 must exclude them from latency-critical placement.
6. **Nodes excluded from `pool=training`** on NIC speed grounds (R-01).
7. **The readiness gate's check list and runtime** — Phase 23 and Phase 53 re-run it.
8. **NVMe wear readings** — nodes above 70 % are replacement candidates (R-06).

---

## ➡️ NEXT

**[PHASE-15 — GitOps with Argo CD](PHASE-15.md)** — establish the reconciliation loop that owns every manifest from here to the end of the project.
