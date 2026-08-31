# PHASE 22 — NCCL & Collective Communication Tuning

| | |
|---|---|
| **Stage** | 3 — Acceleration Fabric |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 21 |
| **Blocks** | 31, 36, 37, 39, 40, 41 |
| **Risk** | 🟡 Medium — wrong NCCL settings degrade performance silently rather than failing |
| **Blast radius** | Every distributed training and inference job |
| **Architecture refs** | `ARCHITECTURE.md#l84-nccl-configuration-contract`, `ULTIMATE-PLAN.md#82-multi-node-scaling-efficiency-targets`, `#83` (B5) |

---

## 🎯 MISSION

Turn a working RDMA fabric into **fast collectives**: build the `nccl-tests` harness, establish busbw baselines at 2/4/8/16 nodes, tune the ring and tree topology, and — critically — **inject the correct NCCL configuration automatically per node pool** so no user ever sets an NCCL environment variable by hand.

> 💡 **WHY automatic injection is the deliverable, not the tuning itself.** NCCL has ~60 tunables. The right values differ between a GDR-capable node and a GeForce node, between 25 GbE and 100 GbE, and between the training and inference pools. Asking users to get this right means most jobs run at a fraction of the fabric's capability, and nobody notices because **the failure mode is slowness, not error**. A Kyverno mutation reading node-pool labels makes the correct configuration the default and the only one.

> ⚠️ **DANGER — silent TCP fallback.** If NCCL cannot use the IB path, it falls back to sockets and keeps working. Your job completes; it is just 10× slower. Every benchmark and every production job must confirm from `NCCL_DEBUG=INFO` that the IB transport was actually selected.

---

## ✅ PREFLIGHT

```bash
# Phase 21 complete: RDMA verified on every training node
cat benchmarks/baselines/b4-rdma.json
bash tools/net/rdma-verify.sh

# GDR status labeled per node
kubectl get nodes -L nexus.io/gpu.gpudirect-rdma,nexus.io/nic.speed-gbps,nexus.io/gpu.model

# The GID index that works (Phase 21 handoff)
grep -i "gid" evidence/phase-21/handoff.md

# At least 4 GPU nodes with working RDMA (2 is not a scaling test)
kubectl get nodes -l nexus.io/pool=training | wc -l
```

---

## 📦 DELIVERABLES

```
images/nccl-tests/Dockerfile           # nccl-tests + CUDA + MPI + perftest
clusters/nexus-prod/acceleration/nccl/
  nccl-config-configmap.yaml           # per-pool NCCL env sets
  nccl-test-job-template.yaml
policies/defaults/
  inject-nccl-env.yaml                 # ⚠️ THE deliverable: automatic configuration
tools/nccl/
  bench-nccl.sh                        # 📊 B5
  scaling-study.sh                     # 2 → 4 → 8 → 16 nodes
  verify-transport.sh                  # assert IB, not sockets
  topology-dump.sh                     # what rings did NCCL build?
docs/
  user/distributed-training-guide.md
  operations/nccl-runbook.md
benchmarks/baselines/b5-nccl-busbw.json
evidence/phase-22/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Note |
|---|---|---|
| NCCL | `2.23.4` | Must match the CUDA/driver from Phase 18 |
| nccl-tests | `2.13.13` | `github.com/NVIDIA/nccl-tests` |
| CUDA | `12.6.3` | Phase 18 |
| OpenMPI | `4.1.7` | For launching multi-node tests |
| UCX | `1.17.0` | Phase 39 uses it; included in the image |

---

## 📋 TASKS

### Task 1 — Understand busbw before measuring it

NCCL reports two numbers. Using the wrong one produces meaningless comparisons.

```
algbw = message_size / time                      ← "algorithm bandwidth"
busbw = algbw × correction_factor                ← "bus bandwidth"

For ring allreduce:  correction = 2 × (N−1) / N
  N=2  → 1.00×      N=8  → 1.75×
  N=4  → 1.50×      N=16 → 1.875×
```

**Always compare `busbw`, never `algbw`.** `busbw` normalizes for the fact that different collectives and different rank counts move different amounts of data per byte of payload; it approximates the per-link bandwidth actually achieved and is therefore comparable across scales.

**Theoretical ceiling:**
```
busbw_max ≈ line_rate × efficiency
  100 GbE = 12.5 GB/s raw
  minus protocol overhead (~4 %) → ~12.0 GB/s
  Target: ≥ 85 % of that at 8 nodes = ~10.2 GB/s   (gate G6)
```

---

### Task 2 — Build the test image and run B5

**`images/nccl-tests/Dockerfile`** — CUDA base + OpenMPI + `nccl-tests` built against the pinned NCCL, plus `perftest` (`ib_write_bw`) so one image covers Phase 21 and 22 diagnostics.

**`tools/nccl/bench-nccl.sh`:**
```bash
# Launch N pods, one per node, one GPU each, with the RDMA VF attached,
# then run under mpirun:

all_reduce_perf -b 8 -e 8G -f 2 -g 1 -c 1 -n 50 -w 10
#   -b 8    : start at 8 bytes
#   -e 8G   : end at 8 GiB
#   -f 2    : double each step
#   -g 1    : 1 GPU per process
#   -c 1    : check correctness (do this at least once; disable for timing runs)
#   -n 50   : 50 iterations
#   -w 10   : 10 warmup iterations

# Also run the collectives that actually appear in real workloads:
all_gather_perf     -b 8 -e 8G -f 2 -g 1     # FSDP parameter gather
reduce_scatter_perf -b 8 -e 8G -f 2 -g 1     # FSDP gradient scatter
broadcast_perf      -b 8 -e 1G -f 2 -g 1     # weight sync at startup
alltoall_perf       -b 8 -e 1G -f 2 -g 1     # MoE / expert parallelism
```

> 💡 **Do not benchmark only `all_reduce`.** FSDP is dominated by `all_gather` + `reduce_scatter`; MoE models are dominated by `alltoall`, which is far more sensitive to fabric bisection. A fabric that looks fine on allreduce can be badly oversubscribed for alltoall.

**Message-size profile — read the curve, not one number:**

| Size | Regime | What it tells you |
|---|---|---|
| 8 B – 1 KB | Latency-bound | Fabric latency and NCCL overhead |
| 1 KB – 1 MB | Transition | Where the protocol switches (LL → LL128 → Simple) |
| 1 MB – 128 MB | **Bandwidth-bound** | **The number that matters for training** |
| 128 MB – 8 GB | Saturated | Should be flat; a drop means buffer or memory pressure |

---

### Task 3 — Verify the transport (do this before believing any number)

**`tools/nccl/verify-transport.sh`:**
```bash
NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=INIT,NET,GRAPH <test> 2>&1 | tee nccl-init.log

# ✅ WHAT YOU WANT TO SEE
grep "NCCL INFO NET/IB" nccl-init.log
#   NCCL INFO NET/IB : Using [0]mlx5_0:1/RoCE [RO]; OOB net1:10.220.1.5<0>
grep "NCCL INFO Ring" nccl-init.log
#   NCCL INFO Ring 00 : 0 -> 1 -> 2 -> ... -> 15 -> 0

# ❌ RED FLAGS — any of these means you are not testing what you think
grep -E "NET/Socket|GDRDMA disabled|Using network Socket" nccl-init.log
#   "NCCL INFO NET/Socket : Using [0]eth0"  → TCP FALLBACK. Everything below is invalid.
```

**`tools/nccl/topology-dump.sh`** — `NCCL_TOPO_DUMP_FILE=/tmp/topo.xml` writes the topology NCCL detected. Compare it against the physical reality from Phase 01/14. A mismatch (e.g. NCCL thinks two GPUs are NVLink-connected when they are not) produces bad ring construction.

---

### Task 4 — The per-pool configuration sets

**`nccl-config-configmap.yaml`** — one env set per pool, derived from node labels.

```yaml
# Pool: training, 100 GbE RoCE, NO GPUDirect (GeForce)
NCCL_IB_HCA: "mlx5_0"
NCCL_IB_GID_INDEX: "3"                 # ← from Phase 21's handoff; verify per NIC
NCCL_IB_TC: "106"                      # DSCP 26 << 2 — must match the lossless class
NCCL_IB_SL: "3"                        # service level = PFC priority
NCCL_IB_QPS_PER_CONNECTION: "4"        # ECMP entropy across leaf uplinks
NCCL_IB_TIMEOUT: "22"
NCCL_IB_RETRY_CNT: "7"
NCCL_IB_DISABLE: "0"
NCCL_NET_GDR_LEVEL: "LOC"              # ⚠️ GDR unavailable on GeForce
NCCL_SOCKET_IFNAME: "net1"             # the VF — NOT eth0
NCCL_P2P_DISABLE: "1"                  # ⚠️ RTX 4090 has no working PCIe P2P
NCCL_SHM_DISABLE: "0"
NCCL_DEBUG: "WARN"
NCCL_ASYNC_ERROR_HANDLING: "1"         # so a hung rank fails instead of hanging forever
NCCL_CROSS_NIC: "1"
NCCL_BUFFSIZE: "8388608"               # 8 MiB
NCCL_NSOCKS_PERTHREAD: "4"
NCCL_SOCKET_NTHREADS: "2"
---
# Pool: training-gdr (RTX PRO / datacenter GPUs)
NCCL_NET_GDR_LEVEL: "PIX"              # GDR across the PCIe switch
NCCL_NET_GDR_READ: "1"
NCCL_P2P_DISABLE: "0"                  # P2P works here
# ... rest identical
---
# Pool: inference (single-node or small TP groups)
NCCL_ALGO: "Ring"                      # lower latency than Tree for small groups
NCCL_PROTO: "LL"                       # low-latency protocol for small messages
NCCL_MIN_NCHANNELS: "4"
```

**The variables that matter most, and why:**

| Variable | Consequence if wrong |
|---|---|
| `NCCL_IB_HCA` | Unset → NCCL may pick the **management NIC**. 100× slowdown. |
| `NCCL_IB_GID_INDEX` | Wrong → cannot establish RoCEv2; silent socket fallback |
| `NCCL_IB_TC` / `NCCL_IB_SL` | Wrong → traffic lands in a lossy class; drops and retransmits on RoCE |
| `NCCL_SOCKET_IFNAME` | Unset → bootstrap over the wrong interface; connection failures or slow init |
| `NCCL_NET_GDR_LEVEL` | `PIX` on GeForce → wasted init, fallback, confusing logs |
| `NCCL_P2P_DISABLE` | `0` on a 4090 → NCCL attempts P2P, fails, retries; slow init |
| `NCCL_IB_QPS_PER_CONNECTION` | `1` → all traffic on one ECMP hash; uses one uplink of four |
| `NCCL_ASYNC_ERROR_HANDLING` | `0` → a dead rank hangs the whole job indefinitely instead of failing |

---

### Task 5 — Automatic injection (the real deliverable)

**`policies/defaults/inject-nccl-env.yaml`** — a Kyverno mutation.

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata: { name: inject-nccl-env }
spec:
  rules:
    - name: nccl-env-from-pool
      match:
        any:
          - resources:
              kinds: [Pod]
              annotations: { "nexus.io/distributed": "nccl" }
      context:
        - name: nodeLabels
          apiCall:
            urlPath: "/api/v1/nodes/{{ request.object.spec.nodeName }}"
            jmesPath: "metadata.labels"
        - name: nccl
          configMap:
            # Select the config set from the node's GDR capability and NIC speed
            name: "nccl-{{ nodeLabels.\"nexus.io/gpu.gpudirect-rdma\" == 'true' && 'gdr' || 'nogdr' }}"
            namespace: acceleration
      mutate:
        patchStrategicMerge:
          spec:
            containers:
              - (name): "*"
                envFrom:
                  - configMapRef: { name: "{{ nccl.metadata.name }}" }
```

> ⚠️ **Node labels are not available at admission time for a pod that has not been scheduled yet.** Two workable approaches: (a) inject a superset config plus a small entrypoint wrapper that selects per-node values at container start; or (b) use a per-pool `ResourceFlavor`/nodeSelector so the pool is known at admission. **Pick one, implement it, and document why** — this is a real engineering constraint, not a detail to hand-wave.

**The recommended approach (b):** Phase 30's Kueue ResourceFlavors already pin a workload to a homogeneous pool. Since the pool is known at admission, the mutation can select the config from the pod's `nodeSelector`, which is deterministic.

**Verify injection works:**
```bash
# Submit a job with only:  annotations: { nexus.io/distributed: nccl }
# Then inspect the running pod:
kubectl exec <pod> -- env | grep NCCL | sort
# Every variable from the pool's set must be present, and correct for that pool.
```

---

### Task 6 — 📊 The scaling study

**`tools/nccl/scaling-study.sh`** — runs `all_reduce_perf` at 2, 4, 8, and 16 nodes and produces the table that gate G6 and `ULTIMATE-PLAN.md §8.2` are measured against.

| Nodes | GPUs | busbw target (100 G) | Measured | % of theoretical | Scaling eff. |
|---|---|---|---|---|---|
| 1 | 1 | — (intra-node) | | | 100 % |
| 2 | 2 | ≥ 11.0 GB/s | | | |
| 4 | 4 | ≥ 10.8 GB/s | | | |
| **8** | **8** | **≥ 10.2 GB/s** | | | **Gate G6: ≥ 85 %** |
| 16 | 16 | ≥ 9.6 GB/s | | | |
| 32 | 32 | ≥ 9.0 GB/s | | | |

**Then the same study with topology-aware placement vs. random** — this quantifies what Phase 31 buys:
```bash
# Run A: all ranks in one leaf domain (nodeSelector on nexus.io/leaf-domain)
# Run B: ranks spread across leaf domains
# Expect Run A to be 8–14 % faster at 16 nodes (ARCHITECTURE.md#L7.3).
# Record both — this is the evidence that justifies Kueue TAS.
```

📊 **Also record the collective-specific curves** (`all_gather`, `reduce_scatter`, `alltoall`). Phase 37 picks a parallelism strategy from these numbers.

---

### Task 7 — Algorithm and protocol tuning

Only tune with evidence (Law VIII). Sweep, record, then pin.

| Variable | Options | When to override the auto choice |
|---|---|---|
| `NCCL_ALGO` | `Ring`, `Tree`, `CollNet`, `NVLS` | Tree is better for small messages at high rank counts; Ring for large. NCCL usually picks well — override only with a benchmark. |
| `NCCL_PROTO` | `LL`, `LL128`, `Simple` | `LL` for latency-bound small messages; `Simple` for large. |
| `NCCL_NCHANNELS` | 2–32 | More channels = more parallelism, more memory. Sweep it. |
| `NCCL_BUFFSIZE` | 4–32 MiB | Larger helps big messages; costs GPU memory per rank. |
| `NCCL_IB_SPLIT_DATA_ON_QPS` | 0/1 | Can help spread across ECMP paths |
| `NCCL_TUNER_PLUGIN` | path | Advanced: a custom tuner for your exact topology |

**The sweep script** should vary one variable at a time over the message-size range and emit a comparison table. **Commit the winning configuration and the evidence that chose it.**

---

### Task 8 — The user guide

**`docs/user/distributed-training-guide.md`**

```markdown
# Running a distributed training job

## You do NOT set NCCL variables
The platform injects them based on the node pool you land on. Add:
    annotations: { nexus.io/distributed: "nccl" }
and the correct configuration appears in your container.
If you override an NCCL variable, you are almost certainly making it slower.
If you believe otherwise, bring a benchmark (Law VIII).

## Choose your parallelism strategy
| Model fits on 1 GPU | Data parallel (DDP) | allreduce per step |
| Model fits on 1 NODE | FSDP / ZeRO-3 within the node | all_gather + reduce_scatter |
| Model spans NODES | Pipeline parallel across nodes + TP within a node | small activation transfers |
| MoE / expert parallel | alltoall — check the alltoall busbw first; it is the most fabric-sensitive |

## Ask for the right shape
· All ranks must be the SAME GPU MODEL (the platform enforces this — R-15)
· Request topology-aware placement: the platform does this by default (Phase 31)
· Size CPU requests at ~4 cores per GPU for data loading

## Verify you are actually using RDMA
    NCCL_DEBUG=INFO in your job, then:
    kubectl logs <pod> | grep "NET/IB"
    ✅ "NCCL INFO NET/IB : Using [0]mlx5_0:1/RoCE"
    ❌ "NCCL INFO NET/Socket"  → tell the platform team; something is wrong

## My job is slower than expected
1. Confirm the IB transport (above).
2. Check the busbw baseline for your rank count: benchmarks/baselines/b5-nccl-busbw.json
3. Profile: is it compute, communication, or data loading? (Phase 51's guide)
4. Are your ranks in one leaf domain? `kubectl get pods -o wide` and compare
   nexus.io/leaf-domain labels.
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | nccl-tests image builds with the pinned NCCL | Build; `nccl-tests --version` | Matches |
| **A2** | **NCCL selects the IB transport, not sockets** | `verify-transport.sh` | `NET/IB` present, `NET/Socket` absent |
| **A3** | The correct HCA is selected (not the mgmt NIC) | Init log | `mlx5_0` |
| **A4** | Rings are constructed across all ranks | Init log | Complete ring printed |
| **A5** | Correctness check passes | `all_reduce_perf -c 1` | No errors |
| **A6** | 📊 **B5: busbw ≥ 85 % of theoretical at 8 nodes (G6)** | `bench-nccl.sh` | Recorded; gate met |
| **A7** | 📊 Scaling study complete at 2/4/8/16 nodes | `scaling-study.sh` | Table filled |
| **A8** | 📊 Scaling efficiency meets `ULTIMATE-PLAN.md §8.2` targets | Compare | Within targets, or the gap is explained |
| **A9** | 📊 `all_gather`, `reduce_scatter`, `alltoall` curves recorded | Run them | Committed |
| **A10** | 📊 **Topology-aware vs. random placement measured** | Two runs | Difference quantified |
| **A11** | Message-size curve shows no anomalous drop at large sizes | Read the curve | Flat at saturation |
| **A12** | NCCL env injection works from a single annotation | Submit a job with only the annotation | All variables present and correct |
| **A13** | GDR and non-GDR pools receive different configurations | Compare two pods | `NCCL_NET_GDR_LEVEL` differs correctly |
| **A14** | `NCCL_P2P_DISABLE=1` on 4090 nodes | Inspect env | Set |
| **A15** | `NCCL_ASYNC_ERROR_HANDLING=1` everywhere | Inspect env | Set |
| **A16** | Killing one rank fails the job promptly instead of hanging | Kill a rank mid-run | Job fails within the timeout |
| **A17** | Tuning sweeps recorded with the chosen values justified | Read the evidence | Benchmark-backed |
| **A18** | User guide tells users not to set NCCL variables | Read it | Present |

---

## ↩️ ROLLBACK

```bash
# Remove the injection policy — users fall back to NCCL defaults (slower but working)
kubectl delete cpol inject-nccl-env
# Revert the config sets
argocd app rollback nccl-config
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| `NET/Socket` in the log | No IB device visible, or `NCCL_IB_DISABLE=1` | Check `/dev/infiniband` in the pod (Phase 21 A4); check the env |
| busbw far below line rate | Wrong `NCCL_IB_TC`/`SL` → lossy class; or PFC storming | `roce-health.sh`; verify the lossless class end to end |
| busbw good at 2 nodes, poor at 8 | Fabric oversubscription, or ranks spread across leaves | Check placement; check the leaf uplink utilization |
| Very slow initialization | NCCL attempting P2P or GDR that does not exist | Set `NCCL_P2P_DISABLE=1` / `NCCL_NET_GDR_LEVEL=LOC` on GeForce |
| Job hangs with no error | A rank died; async error handling off | `NCCL_ASYNC_ERROR_HANDLING=1`; set `NCCL_IB_TIMEOUT` |
| "unhandled system error" during init | GID index wrong, or the VF cannot reach its peer | `show_gids`; ping over `net1` first |
| Performance varies run to run | Placement varies, or another job shares the fabric | Pin placement; check for co-tenants; re-run on a quiet cluster |
| One rank is consistently slow | GPU thermal throttling, a degraded PCIe link, or a bad cable | Correlate with DCGM throttle reasons and Phase 21's mesh heatmap |
| `alltoall` much worse than `allreduce` | Bisection-limited — expected on an oversubscribed fabric | This is a design constraint (Phase 03). Document it for MoE users. |
| busbw drops at very large messages | GPU memory pressure; buffers spilling | Reduce `NCCL_BUFFSIZE` or `NCCL_NCHANNELS` |

---

## 🚫 DO NOT

- **Do not** trust any benchmark number without first confirming the IB transport was used.
- **Do not** compare `algbw` across different rank counts. Use `busbw`.
- **Do not** benchmark only `all_reduce`.
- **Do not** ask users to set NCCL variables. Inject them.
- **Do not** set `NCCL_NET_GDR_LEVEL=PIX` on GeForce nodes.
- **Do not** override an NCCL algorithm or protocol without a sweep proving it helps.
- **Do not** run benchmarks on a busy cluster and treat the results as a baseline.
- **Do not** implement gang scheduling or topology-aware placement here. Phase 31 — this phase measures what it will be worth.

---

## 📤 HANDOFF

`evidence/phase-22/handoff.md` must state:

1. **📊 B5 results and the full scaling study** — gate G6's evidence, and the baseline Phase 50's regression gates compare against.
2. **📊 The topology-aware vs. random placement delta** — the quantified justification for Phase 31's TAS work.
3. **📊 Per-collective curves** — Phase 37 and Phase 40 choose parallelism strategies from these.
4. **The final NCCL configuration per pool**, and how injection is implemented (approach (a) or (b) from Task 5).
5. **The GID index, TC, and SL values** that work.
6. **Nodes or pairs with anomalous performance**, cross-referenced with Phase 21's heatmap.
7. **Tuning decisions** and the sweeps that justified them.
8. **The observed ceiling** — the best busbw achieved, and what limits it (line rate, oversubscription, or GDR absence).

---

## ➡️ NEXT

**[PHASE-23 — GPU Health & Auto-Remediation; Gate G4/G5](PHASE-23.md)** — make hardware failure a control-loop event rather than an incident, and close out Stage 3.
