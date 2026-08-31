# PHASE 18 — NVIDIA GPU Operator

| | |
|---|---|
| **Stage** | 3 — Acceleration Fabric |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 14, 15 |
| **Blocks** | 19, 20, 22, 23 — every GPU workload |
| **Risk** | 🟠 High — driver/toolkit mismatch renders every GPU unusable |
| **Blast radius** | All GPU nodes |
| **Architecture refs** | `ARCHITECTURE.md#l5--resource-abstraction`, `ULTIMATE-PLAN.md#42-the-consumer-gpu-reality-table`, R-03, R-16 |

---

## 🎯 MISSION

Bring every GPU online as a **managed, observable, power-governed cluster resource**: driver validation, the container toolkit in CDI mode, the device plugin, DCGM telemetry, MPS where needed, and the efficiency-knee power caps from Phase 02 — with correct handling for the fact that the driver is baked into the Talos image, not installed by the operator.

> 💡 **WHY the GPU Operator when Talos already ships the driver:** the driver is only one of six things that must be right. The operator manages the container toolkit (so `nvidia` is a valid runtime), the device plugin (so `nvidia.com/gpu` appears as capacity), DCGM and its exporter (so you can see XID errors and utilization), the MPS control daemon, node feature labels, and the validator that refuses to advertise a GPU that does not actually work. **On Talos we run it in `driver.enabled: false` mode** — the operator manages everything except the driver itself.

> ⚠️ **DANGER — R-16.** The driver version is pinned by the Talos image (Phase 09). The container toolkit and device plugin must be compatible with it. A mismatch produces `CUDA driver version is insufficient` errors on every node simultaneously. Upgrades go through Phase 53's canary pool, never fleet-wide at once.

---

## ✅ PREFLIGHT

```bash
argocd app get root                                  # Synced/Healthy
# GPU nodes are labeled and out of quarantine (Phase 14)
kubectl get nodes -l nexus.io/gpu.present=true
kubectl get nodes -l nexus.io/quarantine!=true -l nexus.io/gpu.present=true

# The NVIDIA kernel modules are loaded (from the Talos image, Phase 09)
for n in $GPU_NODES; do talosctl --nodes "$n" read /proc/modules | grep -E '^nvidia'; done

# The driver version the image provides
talosctl --nodes "$N1" read /proc/driver/nvidia/version

# ⚠️ RISK-LEGAL-01 status (R-03) — GeForce datacenter-use determination
test -f evidence/phase-01/legal-review.md && cat evidence/phase-01/legal-review.md
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/acceleration/gpu-operator/
  values.yaml
  application.yaml                    # sync-wave 20
  clusterpolicy.yaml
  mps-config.yaml
  power-cap-daemonset.yaml            # applies inventory powerCapWatts
  dcgm-health-config.yaml
clusters/nexus-prod/acceleration/gpu-validation/
  gpu-smoke-test-job.yaml             # 📊 B1, B2
images/gpu-bench/Dockerfile           # cuda-samples + nccl-tests + dcgm
tools/gpu/
  gpu-inventory.sh                    # live GPU state across the fleet
  gpu-health.sh
  bench-gpu.sh                        # 📊 B1, B2
benchmarks/baselines/{b1-gemm,b2-h2d}.json
docs/operations/gpu-runbook.md
evidence/phase-18/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Note |
|---|---|---|
| NVIDIA GPU Operator | `24.9.2` | `oci://ghcr.io/nvidia/charts/gpu-operator` |
| NVIDIA driver | `550.127.08` | **From the Talos image (Phase 09) — not managed here** |
| container-toolkit | `1.17.3` | Must be compatible with the driver |
| k8s-device-plugin | `0.17.0` | |
| DCGM | `3.3.9` | |
| DCGM exporter | `3.3.9-3.6.1` | |
| CUDA (validator/test images) | `12.6.3` | Must be ≤ the driver's supported CUDA |

> ⚠️ **Record the driver ↔ toolkit ↔ CUDA compatibility triple in `handoff.md`.** Phase 53's upgrade procedure must move all three together.

---

## 📋 TASKS

### Task 1 — Install with the driver disabled

**`clusters/nexus-prod/acceleration/gpu-operator/values.yaml`**

```yaml
# ─────────────────────────────────────────────────────────────────────
#  Talos ships the NVIDIA driver as a system extension (Phase 09).
#  The operator manages everything EXCEPT the driver.
# ─────────────────────────────────────────────────────────────────────
driver:
  enabled: false                       # ⚠️ critical for Talos

toolkit:
  enabled: true
  version: v1.17.3-ubuntu20.04
  env:
    # Talos paths differ from a standard distro
    - { name: CONTAINERD_CONFIG,  value: /etc/cri/conf.d/20-customization.part }
    - { name: CONTAINERD_SOCKET,  value: /run/containerd/containerd.sock }
    - { name: CONTAINERD_RUNTIME_CLASS, value: nvidia }
    - { name: CONTAINERD_SET_AS_DEFAULT, value: "false" }   # opt-in via RuntimeClass
    # CDI: the modern, vendor-neutral device injection path
    - { name: NVIDIA_CONTAINER_TOOLKIT_MODE, value: cdi }
    - { name: CDI_ENABLED, value: "true" }

devicePlugin:
  enabled: true
  version: v0.17.0
  config:
    name: device-plugin-config
    default: "default"
  env:
    - { name: DEVICE_LIST_STRATEGY, value: cdi-annotations }
    - { name: DEVICE_ID_STRATEGY,   value: uuid }
    - { name: PASS_DEVICE_SPECS,    value: "true" }
    # ⚠️ Fail rather than silently advertise a broken GPU
    - { name: FAIL_ON_INIT_ERROR,   value: "true" }

dcgm:        { enabled: true }
dcgmExporter:
  enabled: true
  serviceMonitor: { enabled: false }   # TODO(phase-45)
  config:
    name: dcgm-metrics-config          # curated metric set — see Task 4

migManager: { enabled: false }         # ⚠️ consumer GPUs have no MIG (§4.2)
gfd:        { enabled: true }          # GPU Feature Discovery
nodeStatusExporter: { enabled: true }

validator:
  driver:  { env: [ { name: DISABLE_DEV_CHAR_SYMLINK_CREATION, value: "true" } ] }
  plugin:  { env: [ { name: WITH_WORKLOAD, value: "true" } ] }   # actually run a CUDA workload

operator:
  defaultRuntime: containerd
  nodeSelector: { nexus.io/archetype: infra }
  tolerations: [ { key: nexus.io/archetype, operator: Equal, value: infra, effect: NoSchedule } ]

daemonsets:
  tolerations:
    - { key: nvidia.com/gpu,      operator: Exists, effect: NoSchedule }
    - { key: nexus.io/quarantine, operator: Exists, effect: NoSchedule }   # must run pre-gate
  priorityClassName: system-node-critical
```

> 💡 **`WITH_WORKLOAD: "true"` on the plugin validator** makes it run an actual CUDA workload before marking the node ready. Without it, the validator only checks that libraries load — a GPU that has fallen off the bus can still pass. This is a cheap, high-value guard.

**RuntimeClass** so pods opt into the NVIDIA runtime explicitly:
```yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata: { name: nvidia }
handler: nvidia
```

---

### Task 2 — Verify the stack end to end

```bash
# 1. All operator pods running on GPU nodes
kubectl -n gpu-operator get pods -o wide

# 2. Validators passed
kubectl -n gpu-operator logs -l app=nvidia-operator-validator --tail=20
kubectl get node "$GPU_NODE" -o jsonpath='{.metadata.labels}' | jq | grep nvidia

# 3. GPUs appear as allocatable capacity
kubectl get nodes -o custom-columns=\
'NAME:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu'

# 4. A real CUDA workload runs
kubectl run cuda-smoke --rm -it --restart=Never \
  --image=nvcr.io/nvidia/cuda:12.6.3-base-ubuntu22.04 \
  --overrides='{"spec":{"runtimeClassName":"nvidia","tolerations":[{"key":"nvidia.com/gpu","operator":"Exists","effect":"NoSchedule"}]}}' \
  --limits=nvidia.com/gpu=1 -- nvidia-smi

# 5. CDI devices are generated
talosctl --nodes "$GPU_NODE" read /var/run/cdi/nvidia.yaml | head -20
```

---

### Task 3 — Power caps (the Phase 02 efficiency knee)

A DaemonSet applies each node's `powerCapWatts` from inventory. This is worth ~13 kW and ~4 tons of cooling at 100 nodes for ~8 % throughput (Phase 02, Task 4).

```yaml
# power-cap-daemonset.yaml (sketch — implement as a small init-style loop)
# Reads the node's nexus.io/gpu.power-cap-watts label (set by the Phase 14 labeler)
# and applies it, then re-applies every 5 minutes (the driver resets caps on some events).
#
#   nvidia-smi -pm 1                       # persistence mode ON — avoids re-init latency
#   nvidia-smi -pl "$CAP"                  # power limit
#   nvidia-smi -lgc <min>,<max>  (optional) # lock clocks, only with benchmark evidence
#
# ⚠️ Requires privileged + host PID. It is NOT on the Phase 04 allow-list yet —
#    add it via ADR, or fold this into the GPU Operator's existing privileged DaemonSet.
#    Do not silently add a new privileged workload (Phase 16).
```

> ⚠️ **This is a new privileged workload.** Phase 16's `disallow-privileged` policy will reject it unless it is added to the allow-list with an ADR. Either extend the allow-list properly, or implement the cap through the GPU Operator's `ClusterPolicy` if a supported field exists. **Do not weaken the policy to make this work.**

📊 **Measure the trade-off on your hardware** rather than trusting the table. Run B1 at stock and at the capped value; record both. Phase 49 refines this.

---

### Task 4 — DCGM metrics (curated, not everything)

DCGM exposes hundreds of fields. Exporting all of them at 100 nodes × N GPUs is a cardinality problem for Prometheus (a scaling cliff in `ULTIMATE-PLAN.md §9`).

**The curated set — every metric here has a consumer:**

| Metric | Consumer |
|---|---|
| `DCGM_FI_DEV_GPU_UTIL` | Utilization dashboards, idle-hoarder detection (Phase 34) |
| `DCGM_FI_PROF_SM_ACTIVE` | **Real** utilization — SM occupancy, not "is a process attached" |
| `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE` | Are tensor cores actually being used? |
| `DCGM_FI_DEV_FB_USED` / `_FREE` | VRAM accounting (Phase 19) |
| `DCGM_FI_DEV_POWER_USAGE` | Power-aware scheduling (Phase 32), Kepler correlation |
| `DCGM_FI_DEV_GPU_TEMP` / `_MEMORY_TEMP` | Thermal control loop (Phase 45) |
| `DCGM_FI_DEV_SM_CLOCK` | Throttle detection |
| `DCGM_FI_DEV_CLOCK_THROTTLE_REASONS` | **Why** it throttled: power, thermal, or reliability |
| `DCGM_FI_DEV_XID_ERRORS` | **The primary GPU health signal** (Phase 23) |
| `DCGM_FI_DEV_PCIE_REPLAY_COUNTER` | Link integrity — rising = bad riser/slot |
| `DCGM_FI_PROF_PCIE_TX_BYTES` / `_RX_BYTES` | Host↔device bandwidth utilization |
| `DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL` | Where NVLink exists |
| `DCGM_FI_DEV_ECC_SBE_VOL_TOTAL` / `_DBE_` | ECC errors (datacenter GPUs only) |

> 💡 **`DCGM_FI_PROF_SM_ACTIVE` vs `DCGM_FI_DEV_GPU_UTIL`:** the latter reports 100 % if *any* kernel is running, even a trivial one. The former reports the fraction of SMs actually busy. A notebook idling at `GPU_UTIL=100%, SM_ACTIVE=0.03` is the single most common form of waste in a shared GPU cluster (`ARCHITECTURE.md#P1`). **Dashboards and quota policy must use `SM_ACTIVE`.**

---

### Task 5 — MPS (for Phase 19's sharing modes)

```yaml
# mps-config.yaml — enabled per node-pool, not fleet-wide
version: v1
sharing:
  mps:
    resources:
      - name: nvidia.com/gpu
        replicas: 4                    # 4 concurrent clients per GPU
```

**What MPS does and does not do:**

| | MPS | Time-slicing |
|---|---|---|
| Concurrent kernel execution | ✅ True concurrency | ❌ Context switching |
| Fault isolation | ❌ One client's fault can kill the others | ⚠️ Partial |
| **VRAM partitioning** | ⚠️ Only via `CUDA_MPS_PINNED_DEVICE_MEM_LIMIT` | ❌ **None** |
| Overhead | 2–4 % | 5–15 % under contention |
| Suits | Many small inference replicas | Notebooks, dev, CI |

> ⚠️ **Neither mode partitions VRAM by default.** Four pods on a 24 GB card each see 24 GB and will OOM each other. Phase 16's `require-vram-declaration` policy plus `CUDA_MPS_PINNED_DEVICE_MEM_LIMIT` is the enforcement; Phase 19 wires it into DeviceClasses.

**Restrict MPS to the inference/interactive pools.** A training job must never share a GPU — the contention destroys the throughput you are trying to measure.

---

### Task 6 — 📊 Benchmarks B1 and B2

**`tools/gpu/bench-gpu.sh`** — runs on every GPU node, results committed as baselines.

**B1 — FP16/BF16 GEMM (compute):**
```bash
# From cuda-samples or a small cublasLt harness
./matrixMulCUBLAS -sizemult=10
# Or, more representative of real training:
python -c "
import torch, time
a=torch.randn(8192,8192,device='cuda',dtype=torch.bfloat16)
b=torch.randn(8192,8192,device='cuda',dtype=torch.bfloat16)
for _ in range(10): torch.mm(a,b)          # warm up
torch.cuda.synchronize(); t=time.time()
for _ in range(100): torch.mm(a,b)
torch.cuda.synchronize()
tflops = 100*2*8192**3/(time.time()-t)/1e12
print(f'{tflops:.1f} TFLOPS')"
```
**Gate: ≥ 97 % of the vendor's spec for that model** (at the applied power cap — record both the cap and the number).

**B2 — Host↔device bandwidth:**
```bash
./bandwidthTest --memory=pinned --mode=quick --htod --dtoh --device=all
```
**Gate: ≥ 90 % of PCIe theoretical for the negotiated width.**

| Link | Theoretical | 90 % gate |
|---|---|---|
| PCIe 4.0 x16 | 31.5 GB/s | 28.4 GB/s |
| PCIe 4.0 x8 | 15.8 GB/s | 14.2 GB/s |
| PCIe 3.0 x16 | 15.8 GB/s | 14.2 GB/s |
| PCIe 3.0 x1 (bad riser!) | 0.99 GB/s | — ❌ |

> ⚠️ **B2 is how you catch a bad riser or a degraded slot** that Phase 01's static check missed. A node reporting 1 GB/s host-to-device has a PCIe x1 link and will destroy any data-loading-bound workload. **Fail the node, do not tune around it.**

**`tools/gpu/gpu-inventory.sh`** — fleet-wide live state, cross-checked against inventory:
```bash
# For every GPU node: model, UUID, VRAM, PCIe gen/width, power cap, temp,
# persistence mode, ECC state, throttle reasons, and B1/B2 results.
# Any divergence from inventory/nodes/*.yaml is a finding.
```

---

### Task 7 — The P2P question (RTX 4090/5090)

`ULTIMATE-PLAN.md §4.2`: PCIe peer-to-peer is disabled in the driver on 40/50-series GeForce. A community patch to NVIDIA's open kernel modules enables it.

**Document the decision explicitly in `gpu-runbook.md`:**

| | Stock driver (P2P disabled) | Community P2P patch |
|---|---|---|
| Intra-node multi-GPU transfer | Through pinned host memory | Direct GPU↔GPU over PCIe |
| Speedup for tensor-parallel on one node | baseline | ~2× on the transfer path |
| Supported by NVIDIA | ✅ | ❌ **Unsupported** |
| Works with the Talos system extension | ✅ | ❌ Requires a custom extension build |
| Risk | None | Driver instability; no vendor support; breaks on upgrade |
| **Recommendation** | **Use stock.** Design around it: tensor-parallel within a node only where P2P exists; pipeline-parallel across nodes. | Only for an isolated experimental pool, with an ADR |

**Set `nexus.io/gpu.p2p` correctly** (Phase 14 measures it) so Phase 40's inference topology and Phase 37's parallelism strategy make the right choice automatically.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | GPU Operator installed with `driver.enabled: false` | Grep values | Confirmed |
| **A2** | All operator DaemonSets running on every GPU node | `kubectl -n gpu-operator get pods -o wide` | Complete coverage |
| **A3** | The operator validator passes with a real workload | Validator logs | `WITH_WORKLOAD` passed |
| **A4** | Every GPU node advertises the correct `nvidia.com/gpu` count | Compare with inventory | Exact match |
| **A5** | A CUDA pod runs and sees the GPU | Smoke test | `nvidia-smi` output correct |
| **A6** | CDI device specs are generated | Read `/var/run/cdi/nvidia.yaml` | Present |
| **A7** | The `nvidia` RuntimeClass exists and is opt-in (not default) | `kubectl get runtimeclass`; check containerd config | Not default |
| **A8** | Persistence mode is on for every GPU | `nvidia-smi -q \| grep Persistence` | Enabled |
| **A9** | Power caps applied and match inventory | `nvidia-smi --query-gpu=power.limit` | Matches `powerCapWatts` |
| **A10** | Power caps survive a node reboot | Reboot; re-check | Reapplied |
| **A11** | The power-cap workload is on the privileged allow-list with an ADR | Check Phase 04 doc and Git history | Present |
| **A12** | DCGM exporter serves the curated metric set | `curl <exporter>:9400/metrics` | All Task-4 metrics present |
| **A13** | `DCGM_FI_PROF_SM_ACTIVE` is available (not just `GPU_UTIL`) | Query it | Present |
| **A14** | 📊 **B1 ≥ 97 % of spec on every GPU** | `bench-gpu.sh` fleet-wide | Recorded; any failure is a finding |
| **A15** | 📊 **B2 ≥ 90 % of PCIe theoretical on every GPU** | Same | Recorded |
| **A16** | **B2 catches any degraded PCIe link** | Compare per-node results | Outliers identified and the nodes failed |
| **A17** | GPU inventory matches `inventory/nodes/*.yaml` | `gpu-inventory.sh` | No divergence |
| **A18** | `nexus.io/gpu.p2p` reflects measured reality | Compare label with a P2P test | Matches |
| **A19** | MPS is configured only for the inference/interactive pools | Check the config's node selectors | Training pool excluded |
| **A20** | A GPU pod without the toleration cannot schedule on a GPU node | Try it | Pending |
| **A21** | Removing a GPU (simulated) removes capacity | Drain and re-add, or use the DCGM injection path | Capacity updates |

---

## ↩️ ROLLBACK

```bash
argocd app rollback gpu-operator
# Or disable it entirely (GPUs become invisible to the scheduler; nodes stay healthy)
kubectl -n gpu-operator scale deploy gpu-operator --replicas=0
```
> ⚠️ Removing the operator does not remove the driver (it lives in the Talos image), so nodes stay bootable. Running GPU pods continue until they exit.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| `nvidia.com/gpu: 0` on a node with GPUs | Device plugin not running, or the validator failed | Check validator logs; the driver may not be loaded — `talosctl read /proc/driver/nvidia/version` |
| `CUDA driver version is insufficient` | Toolkit/CUDA newer than the driver | Pin the CUDA base image ≤ the driver's supported version. **R-16.** |
| Toolkit cannot write the containerd config | Wrong Talos path | Use `/etc/cri/conf.d/20-customization.part`; a standard-distro path silently does nothing |
| Pods get a GPU but `nvidia-smi` is missing | `RuntimeClass` not specified | Set `runtimeClassName: nvidia`, or use CDI annotations |
| GPU works but B2 shows ~1 GB/s | PCIe x1 link — bad riser or slot | Physical fix. Do not accept. |
| Node has GPUs but the validator fails intermittently | GPU falling off the bus (XID 79) | Check `dmesg`; likely power delivery or a seating problem. Phase 23 automates the response. |
| Power cap resets after some time | Driver reset the limit, or the DaemonSet is not re-applying | Re-apply on an interval; ensure persistence mode is on |
| DCGM exporter high memory | Too many metrics × GPUs | Use the curated set; drop unused fields |
| MPS clients see each other's memory | MPS shares an address space by design | This is expected. Enforce with `CUDA_MPS_PINNED_DEVICE_MEM_LIMIT` and the VRAM declaration policy. |
| Operator pods pending on quarantined nodes | Missing quarantine toleration | Add it — the GPU stack must come up before the readiness gate can test the GPU |

---

## 🚫 DO NOT

- **Do not** let the operator manage the driver on Talos. `driver.enabled: false`.
- **Do not** set the NVIDIA runtime as containerd's default. Opt-in via RuntimeClass keeps non-GPU pods clean.
- **Do not** enable MIG configuration. Consumer GPUs do not support it.
- **Do not** enable MPS or time-slicing on the training pool.
- **Do not** add a new privileged DaemonSet without an ADR and an allow-list entry (Phase 16).
- **Do not** export every DCGM field. Curate.
- **Do not** use `DCGM_FI_DEV_GPU_UTIL` as the utilization metric in dashboards or quota policy. Use `SM_ACTIVE`.
- **Do not** configure DRA here. Phase 19.
- **Do not** apply the community P2P patch to the production pool.
- **Do not** accept a node that fails B1 or B2. Fix the hardware.

---

## 📤 HANDOFF

`evidence/phase-18/handoff.md` must state:

1. **The driver ↔ toolkit ↔ CUDA compatibility triple** — Phase 53's upgrade procedure moves all three together.
2. **📊 B1 and B2 results per GPU model**, at the applied power cap. Phase 48 uses these as the single-GPU baseline.
3. **Applied power caps** and the measured throughput trade-off.
4. **Which nodes failed B1/B2** and the physical cause.
5. **`nexus.io/gpu.p2p` values** — Phase 37 and Phase 40 choose parallelism strategies from this.
6. **The DCGM metric set exported** — Phase 45 builds dashboards and Phase 23 builds health rules on these names.
7. **Where MPS is enabled** and where it is deliberately not.
8. **The privileged allow-list additions** made in this phase.
9. **RISK-LEGAL-01 status** (R-03).

---

## ➡️ NEXT

**[PHASE-19 — Dynamic Resource Allocation for GPUs](PHASE-19.md)** — replace opaque GPU counting with attribute-based, shareable, topology-aware allocation.
