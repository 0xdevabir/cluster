# PHASE 37 — PyTorch Distributed Training

| | |
|---|---|
| **Stage** | 6 — Distributed Compute Frameworks |
| **Estimated effort** | 5–6 hours |
| **Depends on** | 22, 28, 31, 33, 36 |
| **Blocks** | 41, 44, 48, 51 |
| **Risk** | 🟡 Medium — the flagship workload; performance here defines the platform |
| **Blast radius** | The primary use case |
| **Architecture refs** | `ARCHITECTURE.md#l8-distributed-compute-runtimes`, `#w1-life-of-a-16-gpu-training-job`, `ULTIMATE-PLAN.md#82` |

---

## 🎯 MISSION

Make **multi-node PyTorch training work at world-class scaling efficiency** on this hardware. Deploy Kubeflow Trainer, provide golden-path templates for DDP / FSDP / pipeline parallelism, wire in checkpointing and elastic membership, and — most importantly — **prove the scaling numbers** from `ULTIMATE-PLAN.md §8.2` on a real model, not a synthetic benchmark.

> 💡 **WHY this phase is the platform's report card.** Everything built so far — RDMA, NCCL tuning, topology placement, gang scheduling, data caching, checkpointing — exists so that a training job scales. Phase 22 proved the fabric can move bytes; this phase proves the *application* converts those bytes into throughput. A cluster with 96 % NCCL busbw and 55 % training scaling efficiency has a problem somewhere else, and this is where you find it.

> ⚠️ **The honest constraint, restated for this workload.** On consumer GPUs there is no NVLink, no P2P, and (on GeForce) no GPUDirect RDMA. Gradients traverse: GPU → host memory over PCIe → NIC → fabric → NIC → host memory → GPU. That extra PCIe hop is real and it is why the parallelism strategy matters more here than on a DGX. **Choosing the right strategy is worth more than any tuning flag.**

---

## ✅ PREFLIGHT

```bash
# 📊 The fabric numbers this phase must convert into throughput
cat benchmarks/baselines/b5-nccl-busbw.json
grep -A10 "per-collective curves" evidence/phase-22/handoff.md

# Data path ready (a starved job is not a scaling problem)
grep -A5 "GPU-utilization comparison" evidence/phase-28/handoff.md

# Gang + topology + checkpointing
kubectl get podgroups -A
bash tools/lifecycle/checkpoint-verify.sh --sample

# GPU VRAM per model (determines what fits)
kubectl get nodes -L nexus.io/gpu.vram-gb
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/compute/training/
  trainer-operator-values.yaml      # Kubeflow Trainer v2
  runtimes/                         # ClusterTrainingRuntime per strategy
    torch-ddp.yaml
    torch-fsdp.yaml
    torch-pipeline.yaml
    torch-elastic.yaml
images/training/
  Dockerfile                        # pinned torch + CUDA + NCCL + DALI
templates/training/
  ddp-example/                      # 🎯 runnable golden paths
  fsdp-example/
  multinode-llm-example/
tools/training/
  bench-training.sh                 # 📊 B9: end-to-end scaling
  scaling-study.sh                  # 1 → 2 → 4 → 8 → 16 → 32 GPUs
  strategy-advisor.sh               # "what parallelism should I use?"
docs/user/
  training-guide.md
  parallelism-strategies.md         # 🎯 the decision the user must get right
benchmarks/baselines/b9-training-scaling.json
observability/rules/training-alerts.yaml
evidence/phase-37/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version |
|---|---|
| Kubeflow Trainer | `v2.0.0` |
| PyTorch | `2.6.0+cu126` |
| NCCL | `2.23.4` (Phase 22) |
| NVIDIA DALI | `1.44.0` |
| transformers / accelerate | pin explicitly in the image |

---

## 📋 TASKS

### Task 1 — 🎯 The parallelism decision (the most valuable content in this phase)

**`docs/user/parallelism-strategies.md`** and `strategy-advisor.sh` implement this table.

```
model_memory ≈ params × (2 bytes weights + 2 grads + 8 optimizer[Adam fp32]) = 12 bytes/param
             + activations (batch × seq × hidden × layers × ~4 bytes)
```

| Model size | Fits on 1 GPU (24 GB)? | Strategy | Collective pattern | Fabric sensitivity |
|---|---|---|---|---|
| < 1.5B params | ✅ Yes | **DDP** | `all_reduce` per step | 🟡 Moderate |
| 1.5B – 7B | ❌ No, fits on 1 NODE (4×24 GB) | **FSDP within the node**, DDP across nodes | `all_gather`+`reduce_scatter` intra-node, `all_reduce` inter | 🟡 Moderate |
| 7B – 30B | ❌ Needs multiple nodes | **FSDP across nodes** | `all_gather`+`reduce_scatter` across the fabric | 🔴 **High** |
| 30B+ | ❌ | **Tensor parallel within a node + pipeline parallel across nodes** | Small activations across nodes | 🟢 **Low** ✅ |
| MoE, any size | — | Expert parallel | `alltoall` | 🔴 **Highest** |

> 💡 **The counter-intuitive result that this hardware makes true:** for very large models, *pipeline parallelism across nodes is better than FSDP across nodes*, even though FSDP is simpler and more popular. FSDP moves parameters and gradients over the fabric every step; pipeline parallelism moves only layer activations at stage boundaries — often 10–50× less data. **On a fabric without NVLink or GDR, minimizing cross-node bytes beats maximizing simplicity.** Phase 22's per-collective curves are the evidence: check `all_gather` throughput at your rank count before committing to FSDP-across-nodes.

**The rule to publish:** *keep the chatty parallelism inside a node, put the quiet parallelism across nodes.*
```
Within a node (PCIe, ~25 GB/s host-mediated):   tensor parallel, FSDP sharding
Across nodes (100 GbE, ~12 GB/s, shared):       pipeline parallel, data parallel
```

**`strategy-advisor.sh`** — takes params, batch size, sequence length, and available GPUs; returns the recommended strategy with the memory arithmetic shown. This turns a research question into a command.

---

### Task 2 — Kubeflow Trainer runtimes

Trainer v2 separates the **runtime** (platform-owned, correct by construction) from the **job** (user-owned, minimal).

```yaml
# ClusterTrainingRuntime: torch-ddp — the platform owns everything hard
apiVersion: trainer.kubeflow.org/v1alpha1
kind: ClusterTrainingRuntime
metadata: { name: torch-ddp }
spec:
  mlPolicy:
    numNodes: 2
    torch: { numProcPerNode: "4" }        # one process per GPU
  template:
    spec:
      replicatedJobs:
        - name: node
          template:
            spec:
              template:
                metadata:
                  annotations:
                    nexus.io/distributed: "nccl"                # Phase 22
                    kueue.x-k8s.io/podset-preferred-topology: "nexus.io/leaf-domain"
                    nexus.io/scratch: "200Gi"                   # Phase 25
                    nexus.io/checkpoint-sync: "true"            # Phase 33
                spec:
                  containers:
                    - name: node
                      resources:
                        claims: [{ name: gpus }]                # Phase 19 DRA
                      volumeMounts:
                        - { name: dshm, mountPath: /dev/shm }   # ⚠️ DataLoader needs this
```

**The user's side becomes trivial:**
```yaml
apiVersion: trainer.kubeflow.org/v1alpha1
kind: TrainJob
metadata: { namespace: research-vision }
spec:
  runtimeRef: { name: torch-ddp }
  trainer:
    numNodes: 4
    image: myregistry/my-training:v1.2.3
    command: ["python", "train.py"]
```

> 💡 **This split is the whole point of a platform.** The user declares intent (4 nodes, my image, my script). The runtime supplies NCCL configuration, topology placement, scratch, checkpoint sync, `/dev/shm`, DRA claims, queue membership, and gang semantics. **Every one of those is something a user would get wrong, and none of them are their job.**

> ⚠️ **`/dev/shm` matters here for a different reason than Ray.** PyTorch's `DataLoader` with `num_workers > 0` uses shared memory to pass tensors between worker processes. The 64 MB default causes `RuntimeError: DataLoader worker (pid X) is killed by signal: Bus error` — one of the most-searched PyTorch errors, and pure platform misconfiguration.

---

### Task 3 — Rendezvous, elasticity, and fault tolerance

**Rendezvous backend:** `torchrun` needs a coordination point.

| Backend | Verdict |
|---|---|
| `c10d` on rank 0 | ✅ **Default.** Simple; Trainer/JobSet provides stable headless DNS for rank 0 |
| etcd | ❌ Do not point it at cluster etcd — never let a user workload write to the control-plane store |

**Elastic training** (`--nnodes=2:8`) connects to Phase 33:
```
· minReplicas=2 → the gang minimum (Phase 31)
· Job starts as soon as 2 nodes are available
· Grows to 8 as capacity appears; shrinks under reclaim
· ⚠️ World-size changes alter the effective batch size
```

> ⚠️ **Say the correctness consequence out loud in the guide** (as Phase 33 established): elastic training changes effective batch size, which changes the learning-rate schedule and can change convergence. Either use a scaling rule (linear LR scaling with warmup), fix the global batch size by adjusting gradient accumulation as the world grows, or accept run-to-run variance. **This is not an ops detail; it is a research-validity detail.**

**Fault tolerance settings that must be right:**

| Setting | Value | Why |
|---|---|---|
| `--max-restarts` | 3 | Recover from a transient node failure without infinite looping |
| `NCCL_ASYNC_ERROR_HANDLING` | `1` (Phase 22) | A dead rank fails the job instead of hanging forever |
| `TORCH_NCCL_BLOCKING_WAIT` | `0` + timeout | Combined with async handling, gives a bounded failure |
| Process-group timeout | 30 min | Long enough for a slow checkpoint, short enough to detect a hang |
| `activeDeadlineSeconds` | Set it | Unbounded jobs are how quota disappears |

> ⚠️ **A hung collective is the worst training failure mode**: all ranks alive, all GPUs allocated, zero progress, no error, indefinitely. `NCCL_ASYNC_ERROR_HANDLING=1` plus a process-group timeout converts it into a clean crash that restarts from a checkpoint. Verify this behavior explicitly (A14).

---

### Task 4 — The data pipeline (where scaling efficiency actually dies)

Phase 28 built the tiers; here they meet the training loop.

**The golden-path loader:**
```python
# 1. Stage once, at job start (initContainer, Phase 28 Pattern 1)
#    s5cmd cp 's3://nexus-datasets/imagenet-1k/v1.0/*' /scratch/data/

# 2. Read shards from local NVMe
dataset = wds.WebDataset("/scratch/data/shard-{000000..001023}.tar")

# 3. Size the loader to the GPUs
loader = DataLoader(dataset,
    num_workers=4 * gpus_per_node,     # ⚠️ ~4 CPU workers per GPU
    prefetch_factor=4,
    pin_memory=True,                    # ⚠️ enables async H2D
    persistent_workers=True)            # ⚠️ don't respawn every epoch

# 4. Overlap transfer with compute
for batch in loader:
    batch = batch.to(device, non_blocking=True)   # requires pin_memory
```

📊 **Prove the loader is not the bottleneck before measuring scaling.** Run the training loop with synthetic data (no loader) and with the real loader:
```
Synthetic data step time:  0.184 s   → 100 % GPU-bound
Real loader step time:     0.191 s   → loader costs 3.8 %  ✅ acceptable
If the gap is > 10 %, fix the loader BEFORE running the scaling study —
otherwise you will measure your data pipeline and call it scaling efficiency.
```

**Also connect to Phase 20:** CPU workers should be NUMA-local to their GPU. Guaranteed QoS + Topology Manager `single-numa-node` already ensures this — verify it holds for the training pods specifically.

---

### Task 5 — 📊 The scaling study (B9)

**This is gate G9's evidence and the platform's headline number.**

**`tools/training/scaling-study.sh`** — a real model, real data, weak and strong scaling.

| GPUs | Nodes | Strategy | Step time | Samples/s | **Scaling eff.** | Target (§8.2) |
|---|---|---|---|---|---|---|
| 1 | 1 | single | | | 100 % | — |
| 4 | 1 | DDP intra-node | | | | ≥ 97 % |
| 8 | 2 | DDP | | | | ≥ 94 % |
| 16 | 4 | DDP | | | | ≥ 91 % |
| 32 | 8 | DDP | | | | ≥ 88 % |
| 64 | 16 | DDP | | | | ≥ 84 % |

**Run the study for at least two model classes**, because they stress different things:

| Model | Stresses | Why include it |
|---|---|---|
| ResNet-50 / ViT (vision) | Data pipeline, `all_reduce` | Classic; loader-sensitive |
| A 1–7B LLM (`all_gather`/`reduce_scatter`) | Fabric, FSDP | The workload people actually care about |

📊 **Decompose every step**, because "scaling is 84 %" is not actionable:
```
Step time breakdown at 32 GPUs:
  forward          0.062 s   34 %
  backward         0.089 s   48 %
  ├─ compute       0.061 s
  └─ allreduce     0.028 s   ← overlapped? measure the EXPOSED portion
  optimizer        0.011 s    6 %
  data wait        0.007 s    4 %
  other/sync       0.015 s    8 %
```

> 💡 **The number that matters is *exposed* communication, not total communication.** PyTorch DDP overlaps gradient allreduce with the backward pass via gradient bucketing. A job with 40 % communication and 95 % overlap is fine; a job with 15 % communication and no overlap is not. Measure exposed comm — Phase 51's profiling tools make this precise, but a first-order measurement (step time with and without `no_sync()`) works here.

**Tune the overlap:**
```
DDP:  gradient_as_bucket_view=True, bucket_cap_mb=25–100 (sweep it)
FSDP: forward_prefetch=True, backward_prefetch=BACKWARD_PRE,
      limit_all_gathers=True, use_orig_params=True
```

---

### Task 6 — The user guide and templates

**`docs/user/training-guide.md`** — lead with the decision, then the template.

```markdown
# Distributed training

## Step 1 — Pick your strategy
    nexus training advise --params 7B --gpus 16 --batch 256 --seqlen 4096
    → Recommended: FSDP within node (4 GPUs), DDP across 4 nodes
      Memory: 84 GB/node of 96 GB available ✅
      Expected scaling efficiency at 16 GPUs: ~91 %
      ⚠️ Your model does not fit on one GPU; FSDP is required.

## Step 2 — Submit
    kind: TrainJob
    spec:
      runtimeRef: { name: torch-fsdp }
      trainer: { numNodes: 4, image: ..., command: [...] }
That's the whole spec. The platform supplies everything else.

## Step 3 — Verify you're getting what you paid for
    nexus training status <job>
    → GPUs: 16   SM_ACTIVE: 91 %   Step: 0.34 s   Scaling vs 1 GPU: 89 %
      Placement: all 4 nodes on leaf-01 ✅
      Data wait: 3 % ✅   Exposed comm: 8 %
If SM_ACTIVE < 70 %, something is wrong — see the diagnosis table.

## Diagnosis
| SM_ACTIVE low + data wait high | Data pipeline → data-loading guide |
| SM_ACTIVE low + comm high      | Wrong strategy, or ranks spread across leaves |
| Scaling drops sharply at N nodes | You crossed a leaf boundary — check placement |
| Step time varies run to run     | Co-tenancy or thermal throttling — check the dashboard |
| Job hangs, no error             | A rank died; check NCCL_ASYNC_ERROR_HANDLING |
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | Kubeflow Trainer operator healthy | `kubectl get pods` | Running |
| **A2** | All four runtimes deploy and run | Test each | Work |
| **A3** | A minimal TrainJob spec runs end to end | Submit the 6-line spec | Runs |
| **A4** | Runtime injects NCCL, topology, scratch, checkpoint sync, `/dev/shm` | Inspect a pod | All present |
| **A5** | **`/dev/shm` sized correctly; DataLoader workers do not crash** | `num_workers=16` test | No bus error |
| **A6** | DRA GPU claims work in TrainJobs | Inspect | Allocated |
| **A7** | Gang semantics: partial allocation never starts | 🧪 Test | Never |
| **A8** | Topology placement packs ranks | `placement-report.sh` | Packed |
| **A9** | Checkpoints written and synced | 🧪 Run + inspect S3 | Synced |
| **A10** | 🧪 **Job survives preemption and resumes from checkpoint** | Preempt mid-run | Resumes |
| **A11** | 🧪 **Job survives a node failure and restarts from checkpoint** | Kill a node | Recovers |
| **A12** | Elastic job starts at min and grows | 🧪 Test | Grows |
| **A13** | `max-restarts` bounded; a persistently failing job stops | Test | Stops |
| **A14** | 🧪 **A hung collective fails cleanly instead of hanging forever** | Kill one rank's process | Fails within the timeout |
| **A15** | 📊 **Loader overhead < 10 % vs. synthetic data** | Measure both | Met |
| **A16** | CPU workers NUMA-aligned with their GPU | Phase 20 validator | Aligned |
| **A17** | 📊 **B9 scaling study complete for 2 model classes, 1→64 GPUs** | `scaling-study.sh` | Complete |
| **A18** | 📊 **Scaling efficiency meets §8.2 targets at every scale** | Compare | Met or explained |
| **A19** | 📊 Step-time decomposition recorded at each scale | Study | Recorded |
| **A20** | 📊 Exposed communication measured, not just total | Study | Measured |
| **A21** | Overlap tuning swept and the winner justified | Evidence | Benchmark-backed |
| **A22** | `strategy-advisor.sh` gives correct recommendations | 🧪 Test 5 model sizes | Correct |
| **A23** | 📊 **Pipeline-vs-FSDP across nodes measured for a large model** | Benchmark | Table exists |
| **A24** | All three templates run unmodified | Test | Run |
| **A25** | Training alerts fire | Induce | Fire |

---

## ↩️ ROLLBACK

```bash
# Stop new TrainJobs; running ones finish
kubectl delete clustertrainingruntime torch-ddp torch-fsdp torch-pipeline torch-elastic
# Users fall back to raw PyTorchJob / manual torchrun — functional, less correct by default

helm uninstall trainer -n kubeflow-system
```

> 💡 **The runtimes are the valuable artifact, not the operator.** If Trainer v2 proves unstable, the same annotations and settings can be applied to a plain `Job` + `torchrun`. Keep the runtime definitions as documentation of what correct looks like.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| `DataLoader worker killed by signal: Bus error` | `/dev/shm` too small | A5 — pure platform misconfiguration |
| Job hangs at initialization | Rendezvous unreachable; or ranks cannot see each other | Check headless service DNS; check NCCL init logs |
| Job hangs mid-training, no error | A rank died; async error handling off | A14 |
| Scaling efficiency far below target | Check in order: loader → placement → exposed comm → strategy | The decomposition tells you which |
| Sharp scaling drop at a specific node count | Crossed a leaf boundary | Check placement; consider `required` topology |
| OOM at a scale that should fit | Activation memory grew with batch; or FSDP not sharding as expected | Recompute the memory arithmetic; enable activation checkpointing |
| FSDP slower than DDP for a model that fits | FSDP has overhead when sharding is unnecessary | Use DDP when the model fits |
| Loss diverges after an elastic resize | Effective batch size changed | Task 3's warning — fix the LR schedule |
| First epoch far slower than later | Dataset staging, or a cold cache | Expected; Phase 28's patterns |
| Different step times across ranks (stragglers) | One GPU throttling, or a degraded PCIe link | Phase 23 H7/H10; check per-rank timing |
| Job restarts lose all progress | Checkpointing not wired, or not restoring | Phase 33's contract; verify `NEXUS_RESUME_FROM` |

---

## 🚫 DO NOT

- **Do not** run a scaling study before proving the data loader is not the bottleneck.
- **Do not** report total communication as if it were exposed communication.
- **Do not** default to FSDP across nodes for very large models without measuring pipeline parallelism.
- **Do not** ship runtimes without the `/dev/shm` fix.
- **Do not** point the rendezvous backend at cluster etcd.
- **Do not** leave `activeDeadlineSeconds` unset.
- **Do not** let a hung collective hang indefinitely.
- **Do not** claim a scaling number measured on a quiet cluster as a production number without saying so.
- **Do not** build inference serving here. Phase 40.

---

## 📤 HANDOFF

`evidence/phase-37/handoff.md` must state:

1. **📊 The B9 scaling table** for both model classes, 1→64 GPUs, against §8.2's targets — **the platform's headline result**.
2. **📊 The step-time decomposition** at each scale, with exposed communication separated.
3. **📊 The pipeline-vs-FSDP-across-nodes comparison** — the evidence for the strategy guidance.
4. **📊 Loader overhead** and the proof it is not contaminating the scaling numbers.
5. **The scaling cliff** — where efficiency drops sharply, and why (leaf boundary, oversubscription, strategy).
6. **The runtimes deployed** and every default they inject.
7. **🧪 Fault-tolerance results** — preemption, node failure, hung collective.
8. **Tuning applied** (bucket sizes, prefetch) with the sweeps that justified it.
9. **The gap, if any, between NCCL busbw (B5) and training scaling (B9)** — where the fabric's capability is not reaching the application.

---

## ➡️ NEXT

**[PHASE-38 — Spark & Dask: Distributed Data Processing](PHASE-38.md)** — the CPU-heavy half of the workload spectrum, and the ETL that feeds training.
