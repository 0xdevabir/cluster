# PHASE 19 — Dynamic Resource Allocation for GPUs

| | |
|---|---|
| **Stage** | 3 — Acceleration Fabric |
| **Estimated effort** | 5–6 hours |
| **Depends on** | 18 |
| **Blocks** | 30, 36, 40, 43 |
| **Risk** | 🟠 High — DRA is the newest API in the stack; a broken driver plugin makes GPUs unschedulable |
| **Blast radius** | All GPU scheduling |
| **Architecture refs** | `ARCHITECTURE.md#l51-dra--how-gpus-actually-get-allocated`, ADR-007, ADR-008 |

---

## 🎯 MISSION

Replace opaque GPU counting (`nvidia.com/gpu: 1`) with **attribute-based, shareable, topology-aware allocation** via Dynamic Resource Allocation: DeviceClasses for exclusive, MPS-shared, and time-sliced GPUs, ResourceClaimTemplates users actually reference, and — critically — **VRAM admission control**, because neither sharing mode partitions VRAM on its own.

> 💡 **WHY DRA and not just the device plugin:** `nvidia.com/gpu: 1` cannot express "24 GB, Ada-class, on the same PCIe root complex as `mlx5_0`, shared with three other pods, with a 6 GB VRAM budget." That gap is exactly where a heterogeneous consumer-GPU fleet lives. DRA lets the *scheduler* reason about device attributes, so a job asking for ≥ 24 GB never lands on a 12 GB card and a gang never mixes GPU models (R-15).

> ⚠️ **DANGER — the VRAM trap.** Time-slicing and MPS both let N pods share one GPU. **Neither partitions VRAM by default.** Four pods on a 24 GB card each see 24 GB; the third one to allocate memory OOMs the others. This phase's VRAM admission control is not a nicety — without it, shared GPUs are a source of random, unattributable job failures.

---

## ✅ PREFLIGHT

```bash
# Phase 18 complete and benchmarked
kubectl -n gpu-operator get pods
cat benchmarks/baselines/b1-gemm.json

# DRA feature gate is on (Phase 12) and the API is served
kubectl get --raw /apis/resource.k8s.io/v1beta1 | jq -r '.resources[].name'
# Expect: deviceclasses, resourceclaims, resourceclaimtemplates, resourceslices

kubectl api-resources | grep resource.k8s.io

# kubelet has DRA enabled (Phase 09)
talosctl --nodes "$GPU_NODE" read /etc/kubernetes/kubelet.yaml | grep -A3 featureGates
```

**If the DRA APIs are not served, this phase is BLOCKED.** Fix the API-server `runtime-config` and feature gates in Phase 12's patch first.

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/acceleration/dra/
  values.yaml                          # NVIDIA DRA driver
  application.yaml                     # sync-wave 20
  deviceclasses/
    gpu-exclusive.yaml
    gpu-24g.yaml  gpu-12g.yaml         # capability-tiered
    gpu-mps-quarter.yaml
    gpu-timeslice-4.yaml
  claimtemplates/
    training-gpu.yaml  inference-gpu.yaml  notebook-gpu.yaml
policies/governance/
  enforce-vram-budget.yaml             # Kyverno: the VRAM admission control
  inject-mps-limits.yaml               # mutation: sets CUDA_MPS_* env
docs/
  user/gpu-allocation-guide.md         # ⚠️ user-facing — how to ask for a GPU
  operations/dra-runbook.md
tools/gpu/
  dra-verify.sh
  vram-audit.sh                        # find over-committed GPUs
evidence/phase-19/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Source |
|---|---|---|
| NVIDIA DRA driver for GPUs | `25.3.0` | `oci://ghcr.io/nvidia/k8s-dra-driver-gpu` |
| Kubernetes | `1.34.1` | DRA structured parameters GA |
| `resource.k8s.io` API | `v1beta1` | ⚠️ **Verify the served version and adjust every manifest** |

> ⚠️ **The DRA API version moved across recent Kubernetes releases** (`v1alpha3` → `v1beta1` → `v1`). Check what your cluster actually serves and use that version consistently. Record it in `handoff.md` — Phase 30's Kueue integration and Phase 36's Ray manifests both reference it.

---

## 📋 TASKS

### Task 1 — Install the NVIDIA DRA driver

```yaml
# clusters/nexus-prod/acceleration/dra/values.yaml
nvidiaDriverRoot: /                     # driver is in the Talos root (Phase 09)
resources:
  gpus: { enabled: true }
  computeDomains: { enabled: false }     # IMEX — multi-node NVLink; not applicable here
kubeletPlugin:
  nodeSelector: { nexus.io/gpu.present: "true" }
  tolerations:
    - { key: nvidia.com/gpu,      operator: Exists, effect: NoSchedule }
    - { key: nexus.io/quarantine, operator: Exists, effect: NoSchedule }
  priorityClassName: system-node-critical
controller:
  nodeSelector: { nexus.io/archetype: infra }
  tolerations: [ { key: nexus.io/archetype, operator: Equal, value: infra, effect: NoSchedule } ]
```

**Verify the ResourceSlices — this is the whole mechanism:**
```bash
kubectl get resourceslices
kubectl get resourceslice -o yaml | yq '.items[0].spec.devices[0]'
# Expect per-device attributes:
#   productName, architecture, cudaComputeCapability, driverVersion,
#   index, uuid, minor, and capacity.memory
```

> 💡 **ResourceSlices are what make DRA work.** Each node's kubelet plugin publishes a slice describing every device it has, with attributes. The scheduler reads all slices cluster-wide and matches them against DeviceClass selectors. If a node publishes no slice, its GPUs are invisible to DRA — check the plugin's logs first, always.

---

### Task 2 — DeviceClasses

A DeviceClass is a reusable selector over device attributes. Users reference classes, not raw devices.

**`gpu-24g.yaml`** — capability-tiered exclusive access:
```yaml
apiVersion: resource.k8s.io/v1beta1
kind: DeviceClass
metadata:
  name: nexus-gpu-24g
  annotations:
    nexus.io/description: "One whole GPU with at least 24 GB VRAM. No sharing."
spec:
  selectors:
    - cel:
        expression: |
          device.driver == "gpu.nvidia.com" &&
          device.capacity["gpu.nvidia.com"].memory.compareTo(quantity("24Gi")) >= 0
```

**`gpu-mps-quarter.yaml`** — shared, with a VRAM budget:
```yaml
apiVersion: resource.k8s.io/v1beta1
kind: DeviceClass
metadata:
  name: nexus-gpu-mps-quarter
  annotations:
    nexus.io/description: "1/4 of a GPU via MPS. ~6 GB VRAM budget. Inference/serving."
    nexus.io/vram-budget: "6Gi"
    nexus.io/sharing-mode: "mps"
    nexus.io/warning: |
      MPS shares one CUDA context space. There is NO fault isolation: a fatal
      error in one client can affect the others. Not for training.
spec:
  selectors:
    - cel:
        expression: |
          device.driver == "gpu.nvidia.com" &&
          device.attributes["gpu.nvidia.com"].productName.matches("RTX 4090|RTX 3090") &&
          device.capacity["gpu.nvidia.com"].memory.compareTo(quantity("24Gi")) >= 0
  config:
    - opaque:
        driver: gpu.nvidia.com
        parameters:
          apiVersion: resource.nvidia.com/v1beta1
          kind: GpuConfig
          sharing:
            strategy: MPS
            mpsConfig:
              defaultActiveThreadPercentage: 25
              defaultPinnedDeviceMemoryLimit: "6Gi"   # ⚠️ THE VRAM GUARD
```

**`gpu-timeslice-4.yaml`** — for notebooks and CI:
```yaml
    - opaque:
        driver: gpu.nvidia.com
        parameters:
          apiVersion: resource.nvidia.com/v1beta1
          kind: GpuConfig
          sharing:
            strategy: TimeSlicing
            timeSlicingConfig: { interval: Default }
# ⚠️ Time-slicing does NOT limit VRAM. Kyverno (Task 4) is the only enforcement.
```

**The complete class table — publish this to users:**

| DeviceClass | Mode | VRAM guarantee | Isolation | Overhead | Use for |
|---|---|---|---|---|---|
| `nexus-gpu-exclusive` | Whole device | Full | Full | 0 % | Training, benchmarking |
| `nexus-gpu-24g` | Whole device, ≥ 24 GB | Full | Full | 0 % | Large-model training |
| `nexus-gpu-12g` | Whole device, ≥ 12 GB | Full | Full | 0 % | Smaller jobs, dev |
| `nexus-gpu-mps-quarter` | MPS, 25 % | **6 GB enforced** | ⚠️ None | 2–4 % | Inference replicas |
| `nexus-gpu-timeslice-4` | Time-slice, 4 shares | ⚠️ **None** | ⚠️ None | 5–15 % | Notebooks, CI |

---

### Task 3 — ResourceClaimTemplates

Templates are what users actually put in a pod spec.

```yaml
apiVersion: resource.k8s.io/v1beta1
kind: ResourceClaimTemplate
metadata: { name: training-gpu, namespace: team-vision }
spec:
  spec:
    devices:
      requests:
        - name: gpu
          deviceClassName: nexus-gpu-24g
          allocationMode: ExactCount
          count: 1
```

**Usage:**
```yaml
apiVersion: v1
kind: Pod
spec:
  runtimeClassName: nvidia
  tolerations: [ { key: nvidia.com/gpu, operator: Exists, effect: NoSchedule } ]
  resourceClaims:
    - name: gpu
      resourceClaimTemplateName: training-gpu
  containers:
    - name: trainer
      image: harbor.nexus.internal/nexus/pytorch:2.5.1-cu124
      resources:
        claims: [ { name: gpu } ]
```

**Multi-GPU on one node, with a constraint** — this is where DRA earns its keep:
```yaml
spec:
  devices:
    requests:
      - name: gpus
        deviceClassName: nexus-gpu-24g
        allocationMode: ExactCount
        count: 4
    constraints:
      # ⚠️ All four must be the SAME MODEL — prevents an intra-node straggler (R-15)
      - requests: [gpus]
        matchAttribute: "gpu.nvidia.com/productName"
```

> 💡 **`matchAttribute` is the DRA feature that makes heterogeneous fleets safe.** Without it, a 4-GPU request on a mixed node could return two 4090s and two 3090s, and every collective would run at 3090 speed. Kueue's ResourceFlavors (Phase 30) provide the same guarantee *across* nodes; `matchAttribute` provides it *within* a node.

---

### Task 4 — VRAM admission control (the critical safety net)

Two policies, working together.

**`policies/governance/enforce-vram-budget.yaml`** — validation:
```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata: { name: enforce-vram-budget }
spec:
  validationFailureAction: Audit          # → Enforce after the audit period (Phase 16)
  rules:
    - name: shared-gpu-requires-vram-request
      match:
        any:
          - resources:
              kinds: [Pod]
              # any pod whose claim template maps to a sharing DeviceClass
              annotations: { "nexus.io/gpu-shared": "true" }
      validate:
        message: >-
          Pods using a shared GPU DeviceClass must set the annotation
          nexus.io/vram-request (e.g. "6Gi"). Shared GPUs do not partition VRAM;
          without a declared budget you will OOM your co-tenants.
          See docs/user/gpu-allocation-guide.md.
        pattern:
          metadata: { annotations: { "nexus.io/vram-request": "?*" } }

    - name: vram-request-within-class-budget
      match:
        any: [ { resources: { kinds: [Pod], annotations: { "nexus.io/gpu-shared": "true" } } } ]
      validate:
        message: "nexus.io/vram-request exceeds the DeviceClass budget."
        # Compare the annotation against the class's nexus.io/vram-budget via a context lookup
        # of the DeviceClass. Implement with a Kyverno API-call context.
```

**`policies/governance/inject-mps-limits.yaml`** — mutation, so users cannot forget:
```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata: { name: inject-mps-limits }
spec:
  rules:
    - name: set-cuda-mps-env
      match: { any: [ { resources: { kinds: [Pod], annotations: { "nexus.io/gpu-shared": "true" } } } ] }
      mutate:
        patchStrategicMerge:
          spec:
            containers:
              - (name): "*"
                env:
                  - name: CUDA_MPS_PINNED_DEVICE_MEM_LIMIT
                    value: "{{ request.object.metadata.annotations.\"nexus.io/vram-request\" }}"
                  - name: CUDA_MPS_ACTIVE_THREAD_PERCENTAGE
                    value: "25"
```

**`tools/gpu/vram-audit.sh`** — detects over-commitment before it becomes an outage:
```bash
# For every GPU with >1 pod attached:
#   declared = Σ nexus.io/vram-request across its pods
#   physical = DCGM_FI_DEV_FB_TOTAL
#   actual   = DCGM_FI_DEV_FB_USED
# Report any GPU where declared > physical × 0.90  → OVERCOMMITTED
# Report any GPU where actual > declared           → a pod is exceeding its budget
```

Run it on a schedule; alert on both conditions (Phase 45).

---

### Task 5 — The user-facing guide

**`docs/user/gpu-allocation-guide.md`.** This is the document users read; make it short and decisive.

```markdown
# How to ask for a GPU

## Decide what you need
| I am doing... | Use | Why |
| Training a model | nexus-gpu-24g (or -exclusive) | Sharing destroys throughput and adds jitter |
| Multi-GPU training on one node | nexus-gpu-24g, count: N, matchAttribute productName | Guarantees identical GPUs |
| Serving many small models | nexus-gpu-mps-quarter | True concurrency; declare your VRAM |
| A notebook or interactive work | nexus-gpu-timeslice-4 | Cheap; preemptible; do not leave it idle |
| Benchmarking anything | nexus-gpu-exclusive | Any sharing invalidates the measurement |

## The three rules of shared GPUs
1. **You must declare `nexus.io/vram-request`.** Sharing does not partition VRAM.
2. **There is no fault isolation.** A fatal CUDA error can affect co-tenants.
3. **Never share for training.** Contention makes step times unpredictable.

## Copy-paste examples
<the three patterns from Task 3>

## Checking what you got
kubectl get resourceclaim -n <ns>
kubectl describe pod <pod> | grep -A5 "Resource Claims"
nvidia-smi                       # inside the pod

## Why is my pod Pending?
kubectl get events --field-selector reason=FailedScheduling
# "cannot allocate all claims" → no device matches your DeviceClass right now.
#   · Check `kubectl get resourceslices` for what exists.
#   · Your VRAM requirement may exceed every available GPU.
#   · The pool may be full — check your Kueue queue (Phase 30).
```

---

### Task 6 — Verification

**`tools/gpu/dra-verify.sh`**
```bash
#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
fail=0
chk() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else warn "FAIL: $1"; fail=1; fi; }

chk "DRA API served"             "kubectl get --raw /apis/resource.k8s.io/v1beta1"
chk "kubelet plugin on all GPU nodes" \
  "[ \$(kubectl get pods -n nvidia-dra-driver-gpu -l app=kubelet-plugin --no-headers | grep -c Running) -eq \$GPU_NODE_COUNT ]"
chk "ResourceSlices published"   "[ \$(kubectl get resourceslices --no-headers | wc -l) -gt 0 ]"
chk "slices carry memory capacity" \
  "kubectl get resourceslice -o json | jq -e '.items[].spec.devices[].basic.capacity.memory'"
chk "all DeviceClasses exist"    "kubectl get deviceclass nexus-gpu-exclusive nexus-gpu-24g nexus-gpu-mps-quarter nexus-gpu-timeslice-4"

# Functional: allocate, verify, release
chk "exclusive claim allocates"  "bash tests/dra/test-exclusive.sh"
chk "mps claim allocates 4 pods on 1 GPU" "bash tests/dra/test-mps-sharing.sh"
chk "matchAttribute enforces identical models" "bash tests/dra/test-match-attribute.sh"
chk "claim released on pod deletion" "bash tests/dra/test-claim-cleanup.sh"

[[ $fail -eq 0 ]] && ok "DRA OK" || die "DRA verification FAILED"
```

**The MPS sharing test is the important one:**
```bash
# tests/dra/test-mps-sharing.sh
# 1. Create 4 pods, each with a nexus-gpu-mps-quarter claim and vram-request 6Gi
# 2. Assert all 4 land on the SAME physical GPU (compare the UUID each sees)
# 3. Assert each sees CUDA_MPS_PINNED_DEVICE_MEM_LIMIT=6Gi
# 4. Have one pod try to allocate 12 GB → it must FAIL, not steal from the others
# 5. Assert the other three are unaffected
```

> ⚠️ **Step 4 is the acceptance criterion that matters.** If a pod can allocate beyond its budget, the VRAM guard is not working and shared GPUs are unsafe.

---

### Task 7 — Migration from the device plugin

Both mechanisms can coexist during transition, but a GPU must not be allocatable through both simultaneously.

| Approach | When |
|---|---|
| **DRA only** (recommended) | Set `devicePlugin.enabled: false` in the GPU Operator once every workload uses claims |
| **Both, disjoint node pools** | Label some nodes `nexus.io/gpu-api=dra` and others `=device-plugin`; never both on one node |
| **Both on the same node** | ❌ **Never.** Double-allocation: two pods get the same physical GPU with no coordination. |

Document the chosen path. If you keep the device plugin for compatibility, **verify by inspection** that no node advertises both `nvidia.com/gpu` capacity and DRA ResourceSlices for the same devices.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | DRA API is served; the version is recorded | `kubectl get --raw` | Version recorded in handoff |
| **A2** | Kubelet plugin runs on every GPU node | Pod count | Matches GPU node count |
| **A3** | ResourceSlices published with full attributes | `kubectl get resourceslice -o yaml` | productName, memory, cc, uuid present |
| **A4** | All five DeviceClasses exist and are documented | `kubectl get deviceclass` | Present with description annotations |
| **A5** | An exclusive claim allocates a whole GPU | Test pod | `nvidia-smi` shows one full GPU |
| **A6** | A ≥ 24 GB class never matches a smaller GPU | Create a 12 GB device (or simulate) | Not selected |
| **A7** | **MPS: 4 pods share one physical GPU** | Test | Same GPU UUID in all four |
| **A8** | **MPS: `CUDA_MPS_PINNED_DEVICE_MEM_LIMIT` is injected** | Inspect env in the pod | Present, matches the annotation |
| **A9** | **A pod cannot exceed its VRAM budget** | Allocate beyond it | Allocation fails; co-tenants unaffected |
| **A10** | Kyverno rejects a shared-GPU pod with no `vram-request` | Apply one | Rejected (after enforce flip) |
| **A11** | `matchAttribute` enforces identical GPU models within a claim | Multi-GPU claim on a mixed node | All same model, or Pending |
| **A12** | Claims are released when the pod is deleted | Delete; check `resourceclaims` | Cleaned up |
| **A13** | A node reboot restores slices and claims | Reboot a GPU node | Slices republished; scheduling resumes |
| **A14** | `vram-audit.sh` detects a deliberately over-committed GPU | Create the condition | Detected |
| **A15** | No node advertises both device-plugin capacity and DRA slices for the same GPU | Inspect | Confirmed disjoint |
| **A16** | 📊 Exclusive-claim performance equals Phase 18's B1 | Re-run B1 through a DRA claim | Within 1 % |
| **A17** | 📊 MPS overhead measured and ≤ 5 % | Run B1 with 1 vs. 4 MPS clients | Recorded |
| **A18** | User guide exists and covers the three rules of shared GPUs | Read it | Complete |
| **A19** | "Why is my pod Pending" guidance is accurate | Cause a real failure; follow the guide | Leads to the cause |

---

## ↩️ ROLLBACK

```bash
argocd app rollback nvidia-dra-driver
# Fall back to the classic device plugin
# (set devicePlugin.enabled: true in the GPU Operator values and remove DRA claims)
kubectl delete resourceclaimtemplates --all -A
```
> ⚠️ Deleting ResourceClaimTemplates does not evict running pods, but new pods referencing them will fail to schedule. Migrate workloads first.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| `no resource slices` | Kubelet plugin not running, or the driver root path is wrong | Check plugin logs; on Talos `nvidiaDriverRoot: /` |
| Pod Pending: "cannot allocate all claims" | No device matches the DeviceClass selector | `kubectl get resourceslice -o yaml` and compare attributes against the CEL expression |
| CEL expression never matches | Attribute name or type mismatch | Attribute names are namespaced (`gpu.nvidia.com/productName`); memory comparisons need `quantity()` |
| MPS pods do not share a GPU | Sharing config not applied, or the class selects exclusive devices | Check the DeviceClass `config.opaque` block; verify the MPS daemon is running |
| MPS clients OOM each other | `defaultPinnedDeviceMemoryLimit` not set, or the app ignores it | Set it in the class **and** inject the env var. Some frameworks need `PYTORCH_CUDA_ALLOC_CONF` too. |
| Claims leak after pod deletion | Controller not reconciling | Check the DRA controller logs; verify the finalizer is removed |
| Scheduling is slow with many claims | Scheduler evaluating many slices | Reduce `percentageOfNodesToScore`; keep DeviceClass selectors tight |
| Double allocation of one GPU | Both device plugin and DRA active on the node | **Disable one.** This is a correctness bug, not a performance one. |
| API version errors on manifests | Cluster serves a different `resource.k8s.io` version | Align every manifest to the served version; record it |

---

## 🚫 DO NOT

- **Do not** run the device plugin and DRA against the same GPUs on the same node.
- **Do not** offer a sharing DeviceClass without a VRAM budget and the Kyverno enforcement.
- **Do not** allow shared classes on the training pool.
- **Do not** create DeviceClasses that select across GPU models without `matchAttribute` on multi-device claims.
- **Do not** enable `computeDomains`/IMEX. It is for multi-node NVLink hardware you do not have.
- **Do not** benchmark on a shared GPU. Any result is meaningless.
- **Do not** set Kueue quotas here. Phase 30 — but it will bind quota to these DeviceClasses, so name them carefully.
- **Do not** hardcode the API version in scripts. Read it from the cluster.

---

## 📤 HANDOFF

`evidence/phase-19/handoff.md` must state:

1. **The served `resource.k8s.io` API version** — every later manifest must match.
2. **The DeviceClass catalogue** with names, selectors, VRAM budgets, and intended use. Phase 30 binds ResourceFlavors to these; Phase 36/40/43 reference them.
3. **The VRAM enforcement mechanism** and proof it works (A9).
4. **📊 MPS overhead measured** — Phase 40's inference density planning uses it.
5. **Whether the device plugin is still enabled**, and on which nodes.
6. **The ResourceClaimTemplate patterns** users are expected to copy.
7. **Any GPU model that could not be expressed** as a DeviceClass selector.
8. **Known DRA limitations encountered** — this is the newest API in the stack; record what did not work.

---

## ➡️ NEXT

**[PHASE-20 — NUMA, CPU & Memory Topology](PHASE-20.md)** — enforce Law III: exclusive cores, NUMA-aligned allocations, hugepages, and a validation harness that proves alignment on every placement.
