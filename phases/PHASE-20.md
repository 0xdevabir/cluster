# PHASE 20 — NUMA, CPU & Memory Topology

| | |
|---|---|
| **Stage** | 3 — Acceleration Fabric |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 18 |
| **Blocks** | 21, 26, 31, 39 |
| **Risk** | 🟠 High — `single-numa-node` policy rejects pods that cannot be aligned; a reboot per node is required |
| **Blast radius** | Pod admission on every compute node |
| **Architecture refs** | `ARCHITECTURE.md#l42-kubelet-configuration-contract`, `#l12-pcie-topology-rules`, Law III, `ULTIMATE-PLAN.md#81` |

---

## 🎯 MISSION

Enforce **Law III**: every workload allocation is NUMA-aligned, gets exclusive physical cores, and uses preallocated hugepages — or it is rejected. Build the validation harness that proves alignment on every placement rather than assuming it.

> 💡 **WHY this is worth 10–40 % of memory bandwidth:** on a multi-NUMA machine, a process whose memory is allocated on node 0 while its threads run on node 1 pays a cross-socket hop for every cache miss. For a data-loading pipeline feeding a GPU, that is the difference between saturating the GPU and starving it. `ULTIMATE-PLAN.md §8.1` budgets 0 % for NUMA misplacement, and this phase is how that budget is met.
>
> **On a single-NUMA consumer board this still matters**, just for a different reason: the `static` CPU Manager policy is what gives a workload *exclusive* physical cores. Without it, kubelet and system daemons share cores with your training loop and inject jitter into every collective.

> ⚠️ **DANGER — `single-numa-node` will reject pods.** A pod requesting more cores than one NUMA node has, or requesting devices on different NUMA nodes, gets `TopologyAffinityError` and never starts. **That is correct behavior, not a bug** (ADR-009) — but it will surprise users, so the error path must be documented before you enable it.

---

## ✅ PREFLIGHT

```bash
# Phase 18 complete
kubectl -n gpu-operator get pods

# NUMA topology per node
for n in $COMPUTE_NODES; do
  echo "== $n =="
  talosctl --nodes "$n" read /sys/devices/system/node/online
  talosctl --nodes "$n" read /proc/cmdline | tr ' ' '\n' | grep -E 'hugepages|isolcpus|numa'
done

# GPU and NIC NUMA affinity (Phase 14 computed nexus.io/topology.aligned)
kubectl get nodes -L nexus.io/topology.aligned,nexus.io/numa.nodes

# ⚠️ Changing CPU Manager policy requires deleting the state file AND a reboot.
#    Plan a maintenance window, or roll node by node with drain.
```

---

## 📦 DELIVERABLES

```
talos/patches/
  archetype-compute-gpu.yaml        # extended: CPU/memory manager, hugepages
  kernel-perf.yaml                  # isolcpus, nohz_full, rcu_nocbs
  node-overrides/*.yaml             # per-node hugepage counts and reserved CPUs
clusters/nexus-prod/acceleration/topology-validation/
  numa-validator-daemonset.yaml     # continuous alignment assertion
  topology-test-job.yaml
tools/topology/
  numa-report.sh                    # per-node topology map
  verify-alignment.sh               # assert a running pod is aligned
  bench-numa.sh                     # 📊 memory bandwidth, aligned vs. not
docs/
  user/topology-guide.md            # ⚠️ user-facing: how to request aligned resources
  operations/topology-runbook.md
benchmarks/baselines/numa-bandwidth.json
evidence/phase-20/{preflight,acceptance,handoff,deviations}.md
```

---

## 📋 TASKS

### Task 1 — Map the actual topology

Before changing anything, know what you have. `tools/topology/numa-report.sh`:

```bash
#!/usr/bin/env bash
# Per-node topology map. Run against every compute node.
NODE="$1"
echo "── CPU topology ──"
talosctl --nodes "$NODE" read /sys/devices/system/node/online
talosctl --nodes "$NODE" read /proc/cpuinfo | grep -c processor
# Per-NUMA-node CPU lists
for i in 0 1 2 3; do
  talosctl --nodes "$NODE" read "/sys/devices/system/node/node$i/cpulist" 2>/dev/null \
    && echo "  ↑ node$i"
done

echo "── Device affinity ──"
# GPU
for bdf in $GPU_BDFS; do
  echo "GPU $bdf → numa_node $(talosctl --nodes "$NODE" read "/sys/bus/pci/devices/$bdf/numa_node")"
done
# NIC
for nic in $NICS; do
  echo "NIC $nic → numa_node $(talosctl --nodes "$NODE" read "/sys/class/net/$nic/device/numa_node")"
done

echo "── Hugepages ──"
talosctl --nodes "$NODE" read /proc/meminfo | grep -i huge
```

**Three cases, three configurations:**

| Case | Topology | CPU Manager | Topology Manager | Notes |
|---|---|---|---|---|
| **A. Single NUMA** (AM5, LGA1700) | 1 node, all CPUs | `static` | `single-numa-node` (trivially satisfied) | Still get exclusive cores. **CCX/CCD boundaries still matter** for cache locality — see Task 5. |
| **B. Dual NUMA** (2S Xeon, EPYC NPS2) | 2 nodes | `static` | `single-numa-node` | **The high-value case.** GPU and NIC must be on the same node as the allocated cores. |
| **C. Many NUMA** (EPYC NPS4) | 4 nodes | `static` | `single-numa-node` | Each node has few cores; large pods may be unschedulable. Consider `restricted` instead. |

> 💡 **`numa_node: -1`** means the kernel reports no NUMA affinity, common on single-socket consumer boards. Treat it as "node 0" / "no constraint." Document the rule so the validator does not flag every node.

---

### Task 2 — kubelet topology configuration

**`talos/patches/archetype-compute-gpu.yaml`** — the kubelet block (from `ARCHITECTURE.md#L4.2`):

```yaml
machine:
  kubelet:
    extraConfig:
      # ── CPU Manager: exclusive physical cores for Guaranteed pods ──
      cpuManagerPolicy: static
      cpuManagerPolicyOptions:
        full-pcpus-only: "true"        # never split a physical core's SMT siblings
                                       # across containers — halves effective cache
        distribute-cpus-across-numa: "false"   # keep a pod on one node
      cpuManagerReconcilePeriod: 5s

      # ── Memory Manager: NUMA-local memory ──
      memoryManagerPolicy: Static
      reservedMemory:
        - numaNode: 0
          limits: { memory: "4Gi" }    # must equal systemReserved + kubeReserved memory
        # ⚠️ On multi-NUMA nodes, add an entry per node. Sum must match reservations
        #    exactly or kubelet refuses to start.

      # ── Topology Manager: the enforcement point (Law III) ──
      topologyManagerPolicy: single-numa-node
      topologyManagerScope: pod        # align the WHOLE pod, not each container
      topologyManagerPolicyOptions:
        prefer-closest-numa-nodes: "true"

      # ── Reservations ──
      reservedSystemCPUs: "0-1"        # ⚠️ kubelet+system never steal a workload core
      systemReserved: { cpu: "1", memory: "2Gi", ephemeral-storage: "10Gi" }
      kubeReserved:   { cpu: "1", memory: "2Gi", ephemeral-storage: "10Gi" }

      # ── Hugepages ──
      # Sizes come from the kernel cmdline; kubelet exposes them as resources.
```

**Policy comparison — choose deliberately and record it:**

| Policy | Behavior | Use when |
|---|---|---|
| `none` | No alignment | ❌ Never on compute nodes |
| `best-effort` | Try to align; admit anyway | Development pools only |
| `restricted` | Reject if resources span more NUMA nodes than necessary | Case C (many NUMA nodes, large pods) |
| **`single-numa-node`** | Reject unless everything fits on one NUMA node | ✅ **Default** — Cases A and B |

---

### Task 3 — Kernel-level tuning

**`talos/patches/kernel-perf.yaml`** — every argument justified.

```yaml
machine:
  install:
    extraKernelArgs:
      # ── Hugepages: eliminate TLB pressure ──
      - hugepagesz=1G
      - default_hugepagesz=1G
      - hugepages=8                     # per-node value; override where RAM differs
      - hugepagesz=2M
      - hugepages=2048                  # 4 GiB of 2 M pages for Mayastor/SPDK (Phase 26)

      # ── NUMA: we pin explicitly; autonuma fights the Topology Manager ──
      - numa_balancing=disable

      # ── THP: 'always' causes multi-ms stalls in allocation-heavy trainers ──
      - transparent_hugepage=madvise

      # ── IOMMU passthrough: SR-IOV needs IOMMU; pt removes translation cost ──
      - amd_iommu=on                    # or intel_iommu=on
      - iommu=pt

      # ── PCIe: ASPM downtrains links at idle (Phase 01/A5) ──
      - pcie_aspm=off
      - pcie_port_pm=off

      # ── Jitter-free cores for RDMA polling / MPI ranks ──
      # ⚠️ Only on nodes that run latency-critical polling. Isolated CPUs are
      #    INVISIBLE to the general scheduler — you lose them for normal work.
      #    Coordinate with reservedSystemCPUs so the sets do not overlap.
      - isolcpus=managed_irq,domain,28-31
      - nohz_full=28-31
      - rcu_nocbs=28-31
      - irqaffinity=0-1                 # keep IRQs off both workload and isolated cores

      # ── Frequency: consistent clocks beat peak clocks ──
      - intel_pstate=passive            # or amd_pstate=passive
      - cpufreq.default_governor=performance

      # ⚠️ mitigations stay ON (ADR-027)
  sysctls:
    kernel.numa_balancing: "0"
    vm.zone_reclaim_mode: "0"           # ⚠️ 1 causes pathological reclaim stalls
    vm.swappiness: "0"
    vm.min_free_kbytes: "1048576"       # 1 GiB — headroom for atomic allocations
    kernel.sched_min_granularity_ns: "10000000"
    kernel.sched_wakeup_granularity_ns: "15000000"
```

> ⚠️ **`isolcpus` is a trade, not a free win.** Isolated CPUs are removed from the general scheduler's domains. On a 16-core node, isolating 4 leaves 12 for everything else. Only isolate on nodes that run polling-mode RDMA or tightly-coupled MPI, and **measure the trade in Phase 49** before applying it fleet-wide. Start without it; add it where a benchmark justifies it.

**Applying these requires a reboot per node.** Roll it:
```bash
for n in $COMPUTE_NODES; do
  bash tools/nodes/drain-node.sh "$n"
  bash tools/talos/apply-config.sh "$n"        # --mode=reboot
  # wait for Ready + readiness gate (Phase 14)
  bash tools/nodes/onboard-node.sh "$n"
  kubectl uncordon "$n"
done
```

> ⚠️ **Changing `cpuManagerPolicy` also requires deleting kubelet's CPU manager state file** (`/var/lib/kubelet/cpu_manager_state`). Talos handles this on a config change that triggers a kubelet restart, but verify — a stale state file makes kubelet refuse to start with a policy-mismatch error.

---

### Task 4 — The alignment validator

A DaemonSet that continuously asserts that running pods are actually aligned. **Assumption is not verification.**

**What it checks, per Guaranteed pod on the node:**

| # | Check | Method |
|---|---|---|
| V1 | Pod has exclusive CPUs (not the shared pool) | Read the container's `cpuset.cpus`; assert it does not equal the full shared set |
| V2 | All assigned CPUs are on one NUMA node | Compare `cpuset.cpus` against `/sys/devices/system/node/node*/cpulist` |
| V3 | `cpuset.mems` matches the CPU's NUMA node | Read `cpuset.mems` |
| V4 | Assigned GPU's `numa_node` matches | Cross-reference the DRA claim's device with sysfs |
| V5 | Assigned NIC VF's `numa_node` matches | Same (Phase 21 adds VFs) |
| V6 | `full-pcpus-only` honored: SMT siblings allocated together | Compare against `/sys/devices/system/cpu/cpu*/topology/thread_siblings_list` |
| V7 | Hugepages available as requested | `/proc/meminfo` and the container's limits |

**Emits Prometheus metrics** (Phase 45 alerts on these):
```
nexus_topology_pod_aligned{namespace,pod,node} 0|1
nexus_topology_violation_total{node,check="V2"} <counter>
nexus_topology_numa_nodes{node} <gauge>
```

> 💡 **This validator is what turns Law III from a hope into a fact.** Kubelet's Topology Manager *should* align everything, but bugs, policy misconfiguration, and the `best-effort` fallback all produce silent misalignment. A pod that quietly runs cross-NUMA loses 10–40 % of memory bandwidth and nobody notices — until this metric goes to 0.

---

### Task 5 — 📊 Measure the benefit

**`tools/topology/bench-numa.sh`** — proves the configuration is worth its complexity (Law VIII).

**Test 1 — memory bandwidth, aligned vs. misaligned:**
```bash
# Aligned: numactl --cpunodebind=0 --membind=0 ./stream
# Cross:   numactl --cpunodebind=0 --membind=1 ./stream
# On a 2S system expect 30–50 % degradation on the cross case.
# On single-NUMA both are identical — record that and move on.
```

**Test 2 — exclusive cores vs. shared (this is the one that matters on consumer boards):**
```bash
# Run a fixed compute kernel in a Guaranteed pod (exclusive cores) and in a
# Burstable pod (shared pool) WHILE the node has background load.
# Measure: p50 and p99 iteration time.
# Expect: similar p50, but MUCH tighter p99 with exclusive cores.
# The p99 is what matters — in a 16-rank collective, every rank waits for the slowest.
```

**Test 3 — hugepages:**
```bash
# Allocate 32 GB and random-access it, with and without hugepages.
# Measure TLB misses via perf: `perf stat -e dTLB-load-misses`
```

**Test 4 — CCX/CCD locality (single-NUMA AMD):**
```bash
# On a 7950X (2 CCDs × 8 cores, separate L3):
#   Pin 8 threads within one CCD  vs.  spread across both.
# Cache-heavy workloads can differ by 10–20 %.
# → If significant, add a CCD-aware allocation note to the topology guide.
```

**Record all four in `benchmarks/baselines/numa-bandwidth.json`** with the node model. These numbers justify the configuration to anyone who later asks "why is this so complicated?"

---

### Task 6 — The user guide

**`docs/user/topology-guide.md`** — because `TopologyAffinityError` is otherwise baffling.

```markdown
# Getting aligned, exclusive resources

## You must request Guaranteed QoS
Exclusive CPUs require:
  · requests == limits for CPU and memory
  · CPU requests must be a WHOLE NUMBER (1, 2, 4 — not 1.5 or 500m)
Anything else lands in the shared CPU pool with no alignment guarantee.

```yaml
resources:
  requests: { cpu: "8", memory: "64Gi", hugepages-1Gi: "8Gi" }
  limits:   { cpu: "8", memory: "64Gi", hugepages-1Gi: "8Gi" }
```

## Sizing: do not ask for more cores than one NUMA node has
Node topology: `kubectl get nodes -L nexus.io/numa.nodes`
On a 2-NUMA node with 32 cores, a 24-core request CANNOT be satisfied on one
NUMA node → TopologyAffinityError. Ask for ≤ 16, or run two pods.

## "TopologyAffinityError" — what it means
The pod's CPU, memory, and device requirements could not all be satisfied
within a single NUMA node.
Fixes, in order of preference:
  1. Reduce the CPU request so it fits one NUMA node.
  2. Reduce the device count (e.g. 2 GPUs instead of 4).
  3. Split into multiple pods (and let gang scheduling group them — Phase 31).
  4. Only if none apply: request the `best-effort` node pool (dev only, no guarantees).

## Rule of thumb for a GPU job
  ~4 CPU cores per GPU for data loading, plus 2 for the framework.
  Requesting 32 cores for 1 GPU wastes capacity AND makes alignment harder.

## Hugepages
Request them explicitly; they are a distinct resource. Your process must use
them (`madvise`, or a framework that does) — requesting without using them
just removes memory from everyone else.
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Topology map produced for every compute node | `numa-report.sh` fleet-wide | Recorded |
| **A2** | `cpuManagerPolicy: static` active on all compute nodes | Read kubelet config | Confirmed |
| **A3** | `topologyManagerPolicy: single-numa-node`, scope `pod` | Same | Confirmed |
| **A4** | `full-pcpus-only: true` | Same | Confirmed |
| **A5** | `reservedSystemCPUs` set and non-overlapping with `isolcpus` | Compare | No overlap |
| **A6** | `reservedMemory` sums exactly to system+kube reservations | Check; kubelet started | Kubelet healthy |
| **A7** | Hugepages allocated as configured | `/proc/meminfo` | Matches |
| **A8** | Hugepages appear as a schedulable resource | `kubectl describe node \| grep hugepages` | Present |
| **A9** | A Guaranteed pod gets exclusive CPUs | Inspect `cpuset.cpus` | Not the shared pool |
| **A10** | A Guaranteed pod's CPUs are all on one NUMA node | `verify-alignment.sh` | Aligned |
| **A11** | `cpuset.mems` matches the CPU NUMA node | Same | Aligned |
| **A12** | SMT siblings are allocated together | Compare with `thread_siblings_list` | Together |
| **A13** | **A GPU pod's GPU and CPUs share a NUMA node** | Validator V4 | Aligned |
| **A14** | **An over-large pod is rejected with `TopologyAffinityError`** | Request more cores than one NUMA node has | Rejected with that reason |
| **A15** | Validator DaemonSet runs and exports metrics | `curl <validator>:9100/metrics` | `nexus_topology_pod_aligned` present |
| **A16** | Validator reports 100 % alignment for Guaranteed pods | Query the metric | All 1 |
| **A17** | 📊 Memory bandwidth measured, aligned vs. cross-NUMA | `bench-numa.sh` | Recorded (single-NUMA: identical, noted) |
| **A18** | 📊 **p99 jitter with exclusive cores measured under load** | Test 2 | Recorded; exclusive is tighter |
| **A19** | 📊 Hugepage TLB benefit measured | Test 3 | Recorded |
| **A20** | 📊 CCX/CCD locality effect measured on AMD nodes | Test 4 | Recorded |
| **A21** | Rolling reboot completed with no unplanned workload loss | Watch during the roll | Drains respected PDBs |
| **A22** | User guide explains Guaranteed QoS and `TopologyAffinityError` | Read it | Complete |

---

## ↩️ ROLLBACK

```bash
# Revert the kubelet policy (requires a drain + reboot per node)
git revert <commit>
bash tools/talos/apply-config.sh <node>   # --mode=reboot
# If kubelet refuses to start after a policy change:
#   the CPU manager state file must be removed — Talos does this on kubelet restart,
#   but verify via `talosctl logs kubelet`.
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Kubelet will not start after the change | `reservedMemory` does not sum to the reservations, or a stale CPU manager state file | Fix the arithmetic exactly; check `talosctl logs kubelet` for the specific mismatch |
| Every pod gets `TopologyAffinityError` | `reservedSystemCPUs` too large, or requests exceed one NUMA node | Recompute: allocatable = total − reserved − isolated |
| Pods get shared CPUs despite `static` policy | Not Guaranteed QoS, or fractional CPU request | requests == limits, whole-number CPUs |
| Hugepages show 0 after reboot | Memory too fragmented to reserve 1 G pages | 1 G pages must be reserved at boot. Reduce the count, or use 2 M pages. |
| Performance did not improve | Single-NUMA node — alignment was already trivial | Expected. The value here is **exclusive cores and reduced jitter**, not bandwidth. Record it. |
| `isolcpus` made things worse | Too many cores isolated; the general scheduler is starved | Reduce or remove. Measure before applying (Law VIII). |
| GPU and CPU on different NUMA nodes, unavoidable | Physical slot placement | Move the card to a slot on the correct root complex, or exclude the node from `pool=training` |
| Validator reports misalignment for a system pod | System pods are Burstable by design | Filter to Guaranteed pods only |
| `zone_reclaim_mode` causing stalls | Set to 1 | Must be 0. This causes multi-second stalls under memory pressure. |

---

## 🚫 DO NOT

- **Do not** use `best-effort` topology policy on the training pool. It silently misaligns.
- **Do not** set `mitigations=off` (ADR-027).
- **Do not** apply `isolcpus` fleet-wide without a benchmark showing it helps.
- **Do not** overlap `isolcpus` with `reservedSystemCPUs`.
- **Do not** set `vm.zone_reclaim_mode: 1`.
- **Do not** apply this config to all nodes simultaneously. Roll with drain.
- **Do not** treat `TopologyAffinityError` as a bug to work around. It is the guarantee working.
- **Do not** configure SR-IOV VF NUMA affinity here. Phase 21 — but it will depend on this alignment being in place.

---

## 📤 HANDOFF

`evidence/phase-20/handoff.md` must state:

1. **Per-node NUMA topology** and which of Cases A/B/C each node is.
2. **Allocatable CPU per NUMA node after reservations** — this is the ceiling users must size against, and Phase 31's gang sizing depends on it.
3. **Hugepage configuration per node** — Phase 26 (Mayastor/SPDK) and Phase 39 (MPI) both consume them.
4. **Whether `isolcpus` was applied**, where, and the benchmark that justified it.
5. **📊 All four benchmark results** — Phase 48 uses them as the topology baseline.
6. **Nodes where GPU/NIC alignment is physically impossible** — excluded from `pool=training`.
7. **The validator's metric names** — Phase 45 alerts on them.
8. **Any workload class that cannot satisfy `single-numa-node`** and how it is handled.

---

## ➡️ NEXT

**[PHASE-21 — SR-IOV, RDMA & RoCE Fabric](PHASE-21.md)** — put line-rate, kernel-bypass networking directly into pods, and make the lossless fabric real.
