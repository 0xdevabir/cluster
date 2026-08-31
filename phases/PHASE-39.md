# PHASE 39 — MPI & Traditional HPC Workloads

| | |
|---|---|
| **Stage** | 6 — Distributed Compute Frameworks |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 20, 21, 31, 35 |
| **Blocks** | 41, 48 |
| **Risk** | 🟡 Medium — the most latency-sensitive workload class in the cluster |
| **Blast radius** | HPC workloads |
| **Architecture refs** | `ARCHITECTURE.md#l8-distributed-compute-runtimes`, `#l2-network-fabric`, `ULTIMATE-PLAN.md#4-physics--honest-constraints` |

---

## 🎯 MISSION

Run **tightly-coupled MPI applications** — CFD, molecular dynamics, FEA, linear algebra — at HPC-grade efficiency on this fabric. Deploy the MPI Operator, build a UCX-based communication stack that uses RDMA natively, enforce the CPU pinning and NUMA alignment these codes require, and prove it with **HPL and a real application benchmark**.

> 💡 **WHY MPI still matters here.** Deep learning collectives are bandwidth-bound and tolerant of a few microseconds. Classic HPC codes are **latency-bound and synchronization-heavy**: a CFD solver may exchange small halo regions thousands of times per second, and a 2 µs increase in point-to-point latency shows up directly in time-to-solution. These workloads are the most demanding test of everything Phases 20 and 21 built — NUMA alignment, exclusive cores, jitter control, RDMA. If MPI runs well, everything else will.

> ⚠️ **The honest limitation to state up front.** This is a commodity-Ethernet RoCE fabric, not InfiniBand. Point-to-point latency will land around 2–4 µs versus ~1 µs on InfiniBand, and OS jitter on a general-purpose Kubernetes node is higher than on a dedicated HPC node. For most codes that is a few percent; for extremely latency-sensitive strong-scaling runs it can be more. **Measure it, publish it, and let users decide** rather than promising parity with a dedicated HPC cluster.

---

## ✅ PREFLIGHT

```bash
# 📊 RDMA latency baseline — MPI cannot beat this
jq '.p99_latency_us, .line_rate_pct' benchmarks/baselines/b4-rdma.json

# NUMA/CPU isolation working (Phase 20)
bash tools/topology/alignment-validator.sh
kubectl get nodes -L nexus.io/topology.aligned

# 📊 Jitter measurement from Phase 20 — the number HPC cares about most
grep -A5 "p99 jitter" evidence/phase-20/handoff.md

# SR-IOV VFs available for the pods
kubectl get network-attachment-definitions -n default
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/compute/hpc/
  mpi-operator-values.yaml
  mpijob-templates/                 # small / standard / large
  hpc-clusterqueue.yaml
  hpc-runtimeclass.yaml             # low-jitter node class
images/hpc/
  Dockerfile                        # OpenMPI + UCX + libfabric + compilers + BLAS
tools/hpc/
  bench-hpl.sh                      # 📊 B7-HPL
  bench-osu.sh                      # 📊 OSU micro-benchmarks: the latency truth
  bench-app.sh                      # 📊 a real application (GROMACS / OpenFOAM)
  jitter-test.sh                    # 📊 OS noise measurement
  mpi-verify.sh                     # is UCX actually using RDMA?
docs/user/
  mpi-guide.md
  hpc-performance-notes.md          # ⚠️ the honest comparison
benchmarks/baselines/b7-hpl.json
observability/rules/hpc-alerts.yaml
evidence/phase-39/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Note |
|---|---|---|
| MPI Operator | `v0.6.0` | Kubeflow |
| OpenMPI | `5.0.6` | |
| UCX | `1.17.0` | ⚠️ The transport layer that makes or breaks this |
| libfabric | `1.22.0` | Alternative provider |
| PMIx | `5.0.x` | ⚠️ Must match between launcher and workers |
| OpenBLAS / MKL | pin | HPL performance depends on it |

---

## 📋 TASKS

### Task 1 — The UCX transport stack (where MPI performance lives)

MPI does not talk to the NIC directly; it goes through a transport layer. On this fabric that is UCX.

```bash
# The environment that must be set — inject it, don't ask users
UCX_TLS=rc,ud,sm,self         # RC (reliable connected) over RoCE + shared memory
UCX_NET_DEVICES=mlx5_0:1      # ⚠️ the RDMA device, NOT eth0
UCX_IB_GID_INDEX=3            # from Phase 21's handoff
UCX_IB_TRAFFIC_CLASS=106      # DSCP 26 << 2 — the lossless class
UCX_IB_SL=3                   # service level = PFC priority
UCX_RNDV_THRESH=8192          # eager → rendezvous crossover; sweep it
UCX_ZCOPY_THRESH=auto
UCX_MEMTYPE_CACHE=n           # ⚠️ avoid a known correctness hazard with CUDA memory
OMPI_MCA_pml=ucx              # force UCX, not the legacy ob1/openib path
OMPI_MCA_osc=ucx
OMPI_MCA_btl=^openib,tcp      # ⚠️ explicitly exclude the fallbacks
```

> ⚠️ **The failure mode is identical to Phase 22's NCCL trap: silent TCP fallback.** If UCX cannot open the RDMA device, OpenMPI falls back to TCP and everything works — at 10× the latency. `mpi-verify.sh` must assert the transport:
> ```bash
> mpirun -x UCX_LOG_LEVEL=info ... 2>&1 | grep -E "Selected transport|rc_mlx5|tcp"
> # ✅ "ucp_context: selected transports: rc_mlx5/mlx5_0:1, sm, self"
> # ❌ "tcp" anywhere in the selected transports  → your benchmark is meaningless
> ```

> ⚠️ **`OMPI_MCA_btl=^openib` matters.** The legacy `openib` BTL is deprecated, often present, and will be selected over UCX in some builds — giving worse performance with no warning. Exclude it explicitly.

**`UCX_MEMTYPE_CACHE=n`** is not a tuning knob; it avoids a class of correctness bugs when CUDA memory is freed and re-allocated at the same address. Set it for any job that touches GPU memory.

---

### Task 2 — CPU pinning, NUMA, and jitter (this is where Phase 20 pays off)

HPC codes assume they own the machine. On Kubernetes they do not — unless you configure it.

**The requirements, in order of impact:**

| # | Requirement | Mechanism |
|---|---|---|
| **1** | Exclusive CPUs, no sharing | Guaranteed QoS + CPU Manager `static` (Phase 20) |
| **2** | Ranks pinned to cores, one rank per core | `--bind-to core --map-by ppr:N:numa` |
| **3** | Memory allocated NUMA-local to the rank | Memory Manager `Static` + Topology Manager `single-numa-node` |
| **4** | NIC NUMA-local to the ranks using it | Phase 20's alignment validator |
| **5** | Minimal OS interference | `isolcpus`, `nohz_full`, `rcu_nocbs` (Phase 20) |
| **6** | Hugepages for large working sets | Phase 20 |
| **7** | No CPU frequency scaling mid-run | `performance` governor (Phase 01 BIOS + kernel) |

```bash
# The mpirun invocation the platform generates
mpirun --bind-to core \
       --map-by ppr:$(cores_per_numa):numa:pe=1 \
       --report-bindings \                     # ⚠️ ALWAYS log this
       --mca pml ucx ...
```

> 💡 **`--report-bindings` in every job's output is the cheapest debugging insurance in this phase.** When a user reports "it's slower than our old cluster," the binding report immediately shows whether ranks landed one-per-core on the right NUMA node or were scattered across hyperthreads.

**📊 Jitter measurement — the number that distinguishes an HPC-capable node from a general one:**
```bash
# tools/hpc/jitter-test.sh — a fixed-work loop, timed repeatedly
# Report: min, median, p99, max, and the ratio max/min
```

| Node configuration | Expected p99/median jitter |
|---|---|
| Default Kubernetes node | 5–20 % ⚠️ |
| Guaranteed QoS + static CPU manager | 1–3 % |
| **+ isolcpus / nohz_full (Phase 20)** | **< 1 %** ✅ |

> ⚠️ **Jitter compounds across ranks.** In a synchronizing MPI application, every rank waits for the slowest one at each barrier. With 128 ranks, a 1 % chance of a 10 ms hiccup per rank per barrier means most barriers hit at least one. **This is why `isolcpus` exists**, and why HPC jobs should be steered to nodes carrying the low-jitter configuration via a RuntimeClass or node label.

---

### Task 3 — MPIJob configuration

```yaml
apiVersion: kubeflow.org/v2beta1
kind: MPIJob
metadata:
  labels: { kueue.x-k8s.io/queue-name: hpc-queue }
  annotations:
    kueue.x-k8s.io/podset-required-topology: "nexus.io/leaf-domain"   # ⚠️ REQUIRED, not preferred
spec:
  slotsPerWorker: 32
  runPolicy:
    cleanPodPolicy: Running
    suspend: true                                    # Kueue gates it
  sshAuthMountPath: /home/mpiuser/.ssh
  mpiImplementation: OpenMPI
  mpiReplicaSpecs:
    Launcher: { replicas: 1 }
    Worker:
      replicas: 8
      template:
        spec:
          containers:
            - resources:
                requests: { cpu: "32", memory: "128Gi", hugepages-2Mi: "16Gi" }
                limits:   { cpu: "32", memory: "128Gi", hugepages-2Mi: "16Gi" }   # Guaranteed
              securityContext:
                capabilities: { add: ["IPC_LOCK"] }   # ⚠️ required for RDMA (Phase 21)
          annotations:
            k8s.v1.cni.cncf.io/networks: sriov-rdma   # Phase 21 VF
```

> ⚠️ **`required` topology, not `preferred`, is the right default for MPI** — the opposite of Phase 31's general guidance. A latency-bound synchronizing code spread across leaf switches loses more than it gains by starting sooner. Make this the default in the HPC templates, and tell users why in the guide.

**PMIx version matching:** the launcher and workers must agree on the PMIx version, or `mpirun` fails with opaque wire-protocol errors. Use one image for both — the same discipline as Ray and Dask version pinning.

**Gang scheduling is mandatory** (Phase 31): an MPI job with a missing rank hangs at `MPI_Init` forever.

---

### Task 4 — 📊 The benchmark suite

Three levels, each answering a different question.

**Level 1 — OSU micro-benchmarks: what is the fabric capable of, through MPI?**

| Test | What it isolates | Target |
|---|---|---|
| `osu_latency` | Point-to-point latency | **Record it.** ~2–4 µs on RoCE |
| `osu_bw` | Point-to-point bandwidth | ≥ 90 % of B4's `ib_write_bw` |
| `osu_bibw` | Bidirectional | |
| `osu_allreduce` | Collective, small messages | Compare to NCCL's B5 |
| `osu_alltoall` | ⚠️ The bisection test | The most fabric-sensitive |
| `osu_barrier` | Synchronization cost | Scales with rank count |

📊 **Compare `osu_latency` against Phase 21's `ib_send_lat`.** The gap is MPI's software overhead:
```
ib_send_lat p99:      2.1 µs      ← the fabric
osu_latency:          2.8 µs      ← + MPI/UCX overhead = 0.7 µs  ✅ reasonable
If the gap is > 2 µs, something is wrong (wrong transport, jitter, no pinning).
```

**Level 2 — HPL (B7-HPL): the standard yardstick.**
```
Efficiency = R_max / R_peak
  R_peak = cores × GHz × FLOPs/cycle  (or GPUs × peak FP64)
Targets: CPU HPL ≥ 75 % of peak;  ⚠️ GPU HPL on consumer cards is FP64-crippled —
         a 4090 has ~1/64 FP64 rate. Report it, but do not treat it as a failure.
```

> ⚠️ **Be honest about FP64 on consumer GPUs in the results.** GeForce cards deliberately restrict double-precision throughput. Any HPC code that needs FP64 GPU performance will run poorly here regardless of the fabric — that is a hardware selection consequence, not a platform defect. **State it in `hpc-performance-notes.md` so nobody is surprised after porting an application.**

**Level 3 — a real application.** Pick one that matches actual demand (GROMACS, OpenFOAM, LAMMPS, WRF) and run a standard published benchmark case at 1, 2, 4, 8, 16 nodes. This is the number users will believe.

📊 **The strong-scaling table users need:**

| Nodes | Ranks | Time to solution | Speedup | Efficiency |
|---|---|---|---|---|
| 1 | 32 | | 1.0× | 100 % |
| 2 | 64 | | | |
| 4 | 128 | | | |
| 8 | 256 | | | ⚠️ where it typically breaks down |
| 16 | 512 | | | |

---

### Task 5 — The honest performance notes

**`docs/user/hpc-performance-notes.md`** — write this before users ask.

```markdown
# What to expect from HPC codes on NEXUS

## Compared to a dedicated InfiniBand HPC cluster
| Point-to-point latency | ~2.8 µs here vs. ~1.2 µs on IB HDR      | +1.6 µs |
| Bandwidth              | ~11 GB/s vs. ~25 GB/s (HDR200)          | Lower per-node |
| OS jitter              | <1 % on tuned nodes; comparable         | ✅ |
| FP64 GPU               | ⚠️ RTX 4090 FP64 is ~1/64 of FP32       | ❌ Not suitable |
| FP32/TF32/FP16 GPU     | Excellent                               | ✅ |
| Cost per node-hour     | Substantially lower                     | ✅ |

## What this means for YOUR code
· Bandwidth-bound, weak-scaling codes:      near parity ✅
· Latency-bound, strong-scaling at high rank counts: expect 5-20 % longer
· FP64-heavy GPU codes:                     use the CPU nodes, or don't run here
· Codes with large halo exchanges:          fine — request `required` topology
· Codes doing many tiny messages/barriers:  measure before committing

## Get the most out of it
1. Request `required` leaf-domain topology (it is the default for MPI here)
2. Use the low-jitter node pool (also default)
3. Check --report-bindings in your output: one rank per core, right NUMA node
4. Verify RDMA is in use: grep for "rc_mlx5" in the UCX log
5. Sweep UCX_RNDV_THRESH for your message-size profile
```

> 💡 **Publishing the gap builds more trust than hiding it.** A researcher who ports their code expecting IB latency and finds 2.8 µs will conclude the platform is broken. One who was told 2.8 µs up front and measures 2.8 µs concludes it works as advertised.

---

### Task 6 — Alerts

| Alert | Threshold |
|---|---|
| `MPIJobStuckAtInit` | No progress 10 min after all pods running (a missing rank) |
| `MPITCPFallback` | UCX selected TCP transport |
| `MPIJobRankImbalance` | Per-rank timing variance > 20 % (a straggler) |
| `HPCNodeJitterHigh` | Jitter test p99 > 2 % |
| `MPIJobPendingLong` | Required topology unsatisfiable |
| `HPLRegressed` | HPL below the baseline (feeds Phase 50) |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | MPI Operator healthy; an MPIJob runs end to end | Submit | Completes |
| **A2** | **UCX selects an RDMA transport, never TCP** | `mpi-verify.sh` | `rc_mlx5` selected |
| **A3** | Legacy `openib` BTL excluded | Read the config | Excluded |
| **A4** | `UCX_MEMTYPE_CACHE=n` set for GPU jobs | Inspect env | Set |
| **A5** | `IPC_LOCK` and the SR-IOV VF present in worker pods | Inspect | Present |
| **A6** | Pods get Guaranteed QoS with integer CPUs | `kubectl describe pod` | Guaranteed |
| **A7** | **`--report-bindings` shows one rank per core, correct NUMA** | Read job output | Correct |
| **A8** | Memory is NUMA-local to the rank | Phase 20 validator | Local |
| **A9** | Hugepages allocated and used | Inspect | Used |
| **A10** | 📊 **Jitter p99 < 1 % on the HPC node pool** | `jitter-test.sh` | Met |
| **A11** | MPI jobs steered to low-jitter nodes by default | Inspect placement | Steered |
| **A12** | `required` leaf-domain topology is the MPI default | Submit without annotation | Injected as required |
| **A13** | Gang scheduling prevents partial starts | 🧪 Test | Prevented |
| **A14** | 📊 **OSU suite recorded; latency gap vs. B4 < 2 µs** | `bench-osu.sh` | Met |
| **A15** | 📊 `osu_bw` ≥ 90 % of `ib_write_bw` | Compare | Met |
| **A16** | 📊 **HPL recorded; CPU efficiency ≥ 75 % of peak** | `bench-hpl.sh` | Met |
| **A17** | 📊 GPU FP64 limitation measured and documented | HPL GPU run | Documented |
| **A18** | 📊 **A real application strong-scaling study, 1→16 nodes** | `bench-app.sh` | Complete |
| **A19** | PMIx versions match launcher and workers | Same image | Matched |
| **A20** | A failed rank fails the job rather than hanging forever | 🧪 Kill a rank | Fails |
| **A21** | Jobs are preemptible or explicitly declared not (Phase 33) | Check | Declared |
| **A22** | Performance notes published with the honest comparison | Read | Published |
| **A23** | All alerts fire | Induce | Fire |

---

## ↩️ ROLLBACK

```bash
kubectl patch clusterqueue hpc-queue --type merge -p '{"spec":{"stopPolicy":"Hold"}}'
helm uninstall mpi-operator -n hpc
# Users fall back to Slurm's native MPI path (Phase 35) if deployed, or single-node runs.
```

> 💡 **Phase 35's Slurm deployment is a genuine fallback here**, since HPC users may be more comfortable with `srun` anyway. If MPI Operator proves problematic, the Slurm path serves the same population with the same UCX stack.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| 10× worse latency than expected | **TCP fallback** | A2 — check UCX transport selection first, always |
| `mpirun` fails with PMIx errors | Version mismatch launcher/worker | Use one image |
| Job hangs at `MPI_Init` | A rank never started (gang not enforced) | A13 |
| Performance varies run to run | Ranks landing on different NUMA nodes or hyperthreads | A7 — read the bindings |
| Straggler rank | Thermal throttling, or a node with different tuning | Phase 23 H7; check the node's jitter |
| Poor scaling past 8 nodes | Crossed leaf boundaries, or the code's own limit | Check placement; run the same case on 8 nodes in one leaf |
| CUDA-aware MPI crashes | `UCX_MEMTYPE_CACHE` not disabled | A4 |
| `ibv_open_device` fails | No VF, or `IPC_LOCK` missing | A5 |
| HPL far below peak | Wrong BLAS, bad block size, or memory not hugepage-backed | Tune `NB`, `P×Q`; verify the BLAS library |
| GPU HPL terrible | FP64 restriction on consumer cards | A17 — expected, documented |
| Jitter high on some nodes | `isolcpus` not applied there | Check the Phase 20 kernel args on that node |

---

## 🚫 DO NOT

- **Do not** report any MPI benchmark without verifying the RDMA transport was selected.
- **Do not** leave the legacy `openib` BTL enabled.
- **Do not** default MPI jobs to `preferred` topology.
- **Do not** run MPI without exclusive CPUs and explicit binding.
- **Do not** omit `--report-bindings` from job output.
- **Do not** present GPU FP64 numbers without the consumer-hardware caveat.
- **Do not** promise InfiniBand-class latency on a RoCE fabric.
- **Do not** let an MPI job hang indefinitely on a dead rank.

---

## 📤 HANDOFF

`evidence/phase-39/handoff.md` must state:

1. **📊 The OSU suite results**, and the MPI-over-fabric overhead (osu_latency minus ib_send_lat).
2. **📊 B7-HPL** with efficiency against peak, CPU and GPU, with the FP64 caveat.
3. **📊 The real-application strong-scaling table** — the number HPC users will judge the platform by.
4. **📊 Jitter measurements** per node configuration — the evidence that Phase 20's isolation was worth it.
5. **The UCX environment** in force and how it is injected.
6. **The honest comparison table** published to users.
7. **Which node pool serves HPC** and what makes it different.
8. **Applications validated** and any that were found unsuitable (and why).

---

## ➡️ NEXT

**[PHASE-40 — LLM Inference Serving with vLLM & KServe](PHASE-40.md)** — the other production workload: low-latency, high-throughput model serving.
