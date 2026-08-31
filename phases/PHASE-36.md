# PHASE 36 — Ray & KubeRay

| | |
|---|---|
| **Stage** | 6 — Distributed Compute Frameworks |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 25, 28, 31, 33, 35 |
| **Blocks** | 38, 40, 41, 43, 44 |
| **Risk** | 🟡 Medium — Ray's own scheduler must not fight Kubernetes' |
| **Blast radius** | Ray workloads |
| **Architecture refs** | `ARCHITECTURE.md#l8-distributed-compute-runtimes`, `#l82-ray-cluster-topology`, ADR-021 |

---

## 🎯 MISSION

Deliver **Ray** as the platform's general-purpose distributed compute engine: the answer to "I have a Python function and I want it to run across 40 machines." Stand up KubeRay with autoscaling RayClusters, integrate it with Kueue's quota and gang semantics, wire Ray's object store to the storage tiers, and make `RayJob` the default path for anything that is not a pure PyTorch training run.

> 💡 **WHY Ray first in Stage 6.** Ray covers the widest surface of what "distributed computing across ordinary PCs" actually means for users: parallel Python, hyperparameter sweeps (Ray Tune), distributed data processing (Ray Data), RL, batch inference, and — via Ray Serve and Ray Train — inference and training too. One framework, one mental model, and it degrades gracefully: a Ray script that runs on a laptop runs on 100 nodes without modification. That property is exactly the "unified pool of computational resources" experience this platform was commissioned to deliver.

> ⚠️ **The core integration hazard: two schedulers.** Ray has its own scheduler that places *tasks* onto *Ray workers*. Kubernetes places *Ray worker pods* onto *nodes*. These must be layered, never overlapping — exactly the same discipline as Phase 35's Slurm model. Ray must never assume resources Kubernetes has not given its pods, and the Ray autoscaler must request capacity *through* Kueue, not around it.

---

## ✅ PREFLIGHT

```bash
# Gang scheduling and quota (a Ray cluster is a gang)
kubectl get clusterqueues,podgroups -A
grep -c "☑" gates/G8-scheduling.md

# Storage: scratch for spill, object store for data
kubectl get sc nexus-scratch nexus-fs
s5cmd ls s3://nexus-datasets/

# GPU allocation via DRA
kubectl get deviceclasses

# NCCL config injection works (Ray Train uses it)
grep -A3 "injection" evidence/phase-22/handoff.md
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/compute/ray/
  kuberay-operator-values.yaml
  raycluster-templates/             # small / standard / large / gpu-heavy
  rayjob-template.yaml
  rayservice-template.yaml          # Phase 40 extends this
  ray-clusterqueue-integration.yaml
policies/defaults/
  inject-ray-config.yaml            # spill dirs, object store size, NCCL
images/ray/
  Dockerfile                        # pinned Ray + CUDA + the platform's client libs
tools/ray/
  ray-submit.sh                     # the sanctioned submission path
  ray-status.sh
  bench-ray.sh                      # 📊 task throughput, object transfer, scaling
docs/user/
  ray-guide.md
  ray-patterns.md
observability/rules/ray-alerts.yaml
evidence/phase-36/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Note |
|---|---|---|
| KubeRay operator | `1.2.2` | |
| Ray | `2.40.0` | ⚠️ **Must match exactly between the image and the cluster spec** |
| Python | `3.11` | Ray version compatibility |
| CUDA | `12.6.3` | Phase 18 |

> ⚠️ **Ray version mismatch between head and workers causes obscure failures** — workers connect, then die with serialization errors. Pin one image, use it everywhere, and add a startup assertion that `ray.__version__` matches across the cluster.

---

## 📋 TASKS

### Task 1 — 🎯 RayCluster shapes vs. RayJob

Two deployment modes; users must understand which they want.

| Mode | Lifetime | Use for | Quota behavior |
|---|---|---|---|
| **`RayJob`** | Created for one job, destroyed after | ✅ **Default.** Batch work, sweeps, data processing | Clean: capacity released at completion |
| **`RayCluster`** (long-lived) | Until deleted | Interactive development, a shared team cluster | ⚠️ Holds quota until deleted — the biggest "allocated but idle" source (Phase 34) |
| **`RayService`** | Until deleted, with rolling upgrades | Serving (Phase 40) | Production inference |

> 💡 **Default users to `RayJob` and make long-lived RayClusters require justification.** A persistent RayCluster with a 4-GPU head that someone created in March and forgot is precisely the waste Phase 34's waterfall surfaces. `RayJob` gets the same interactive experience via `ray job submit` while guaranteeing the capacity comes back.

**Cluster templates** — parameterized, not hand-written per job:

| Template | Head | Workers | For |
|---|---|---|---|
| `ray-small` | 2 CPU, 8 Gi, no GPU | 0–4 × (8 CPU, 32 Gi) | Data processing, light parallelism |
| `ray-standard` | 4 CPU, 16 Gi | 0–16 × (16 CPU, 64 Gi, 1 GPU) | Tune sweeps, batch inference |
| `ray-large` | 8 CPU, 32 Gi | 0–64 × (16 CPU, 64 Gi, 1 GPU) | Large sweeps, RL |
| `ray-train` | 4 CPU, 16 Gi | 2–16 × (32 CPU, 128 Gi, 4 GPU) | Ray Train / distributed training |

> ⚠️ **The head node must never hold a GPU by default.** A GPU on the head is idle almost always — Ray's driver and GCS do not use it. Users request it out of habit; the platform should reject it unless justified. This single default recovers meaningful capacity.

---

### Task 2 — Kueue integration: the Ray cluster as a gang

```yaml
apiVersion: ray.io/v1
kind: RayJob
metadata:
  labels:
    kueue.x-k8s.io/queue-name: research-default      # Phase 30
  annotations:
    nexus.io/distributed: "nccl"                     # Phase 22 injection
    kueue.x-k8s.io/podset-preferred-topology: "nexus.io/leaf-domain"   # Phase 31
spec:
  suspend: true                                      # ⚠️ Kueue gates it
  shutdownAfterJobFinishes: true                     # ⚠️ release capacity
  ttlSecondsAfterFinished: 600
  rayClusterSpec:
    headGroupSpec: { ... }
    workerGroupSpecs:
      - groupName: gpu-workers
        replicas: 4
        minReplicas: 2                               # ⚠️ the gang minimum (Phase 31)
        maxReplicas: 16                              # elastic ceiling (Phase 33)
```

**The three settings that determine whether Ray behaves as a good citizen:**

| Setting | Wrong value | Consequence |
|---|---|---|
| `suspend: true` | `false` | Bypasses Kueue quota entirely |
| `shutdownAfterJobFinishes` | `false` | **The cluster persists after the job ends, holding GPUs forever** |
| `minReplicas` | `= maxReplicas` | No elasticity; waits for full allocation (Phase 31/33) |

> ⚠️ **`shutdownAfterJobFinishes: false` is the single most expensive default mistake in this phase.** Enforce `true` via Kyverno for `RayJob`, with an explicit exemption path.

**Autoscaling through Kueue, not around it:** Ray's autoscaler requests more worker pods; those pods are gated by the same Kueue queue. When quota is exhausted, pods stay pending and Ray sees "pending nodes" — which is correct. Configure:
```yaml
enableInTreeAutoscaling: true
autoscalerOptions:
  upscalingMode: Default            # NOT Aggressive — respects the queue
  idleTimeoutSeconds: 300           # release idle workers back to the cluster
```

> 💡 **`idleTimeoutSeconds` is the Ray-level answer to Phase 34's "allocated but idle."** A sweep whose trials finish should shrink, not sit at 16 workers. 300 s is a good default: long enough to avoid thrash between trials, short enough to return capacity.

---

### Task 3 — Object store, spill, and the memory boundary

Ray's object store is where the platform's central physics constraint becomes concrete for users.

```
Ray object store = shared memory (/dev/shm) on EACH node, NOT a cluster-wide pool.
An object created on node A lives on node A. Another node reading it triggers a
NETWORK TRANSFER. Ray makes this invisible; it is not free.
```

> ⚠️ **This is `ULTIMATE-PLAN.md §4.1` restated in Ray's vocabulary.** Ray's `ObjectRef` API makes remote objects look local, which is exactly why users write code that ships gigabytes across the fabric per task without realizing it. The platform's job is to configure it well and *tell them*.

**Configuration:**

| Setting | Value | Why |
|---|---|---|
| `object_store_memory` | ~30 % of pod memory | Too large starves the workload; too small causes constant spilling |
| `/dev/shm` size | Must be ≥ `object_store_memory` | ⚠️ **Kubernetes defaults `/dev/shm` to 64 MB** — Ray will fail or spill relentlessly |
| Spill directory | `/scratch/ray-spill` (T0 NVMe) | ⚠️ **Never the container overlay filesystem** |
| `max_direct_call_object_size` | Default | |

```yaml
# The /dev/shm fix — required on EVERY Ray pod
volumes:
  - name: dshm
    emptyDir: { medium: Memory, sizeLimit: 32Gi }
volumeMounts:
  - name: dshm
    mountPath: /dev/shm
```

> 🚫 **A Ray deployment without the `/dev/shm` fix will appear to work and perform terribly.** It is the most common Ray-on-Kubernetes misconfiguration. Inject it via mutation so no user can forget it.

**Spilling to `/scratch`** connects Ray to Phase 25: when the object store fills, Ray writes objects to disk. On the container overlay FS that is slow and can fill the node's root filesystem; on T0 NVMe it is fast and bounded. Inject the spill config:
```json
{"object_spilling_config": {"type": "filesystem",
  "params": {"directory_path": "/scratch/ray-spill"}}}
```

---

### Task 4 — Data and storage integration

| Ray feature | Backed by | Notes |
|---|---|---|
| `ray.data.read_parquet("s3://...")` | T3 object (Phase 28) | Configure the S3 endpoint and credentials via ESO |
| Dataset caching | T4 / `/scratch` | Phase 28's patterns apply unchanged |
| Checkpoints (Ray Train/Tune) | `/checkpoints` → T3 | ⚠️ Wire Ray's checkpoint dir to Phase 33's sync sidecar |
| Logs | Loki (Phase 45) | Ray writes to `/tmp/ray/session_*/logs` — mount and ship it |

> 💡 **Ray Train and Ray Tune already have checkpoint abstractions.** Point them at `/checkpoints` so Phase 33's sidecar picks them up automatically, rather than having Ray upload to S3 itself on the critical path. Same reasoning as Phase 33 Task 3: local write, background replication.

---

### Task 5 — 📊 Benchmark Ray

**`tools/ray/bench-ray.sh`:**

| Benchmark | Measures | Target |
|---|---|---|
| Empty task throughput (1 node) | Ray scheduling overhead | > 10k tasks/s |
| Empty task throughput (16 nodes) | Distributed scheduling | > 50k tasks/s |
| **Object transfer, 1 GB, same node** | Shared memory path | > 8 GB/s |
| **Object transfer, 1 GB, cross node** | ⚠️ **The network reality** | ~ line rate, and record it |
| Actor creation latency | | < 500 ms |
| Ray cluster startup (RayJob, 8 workers) | Time to first task | < 90 s |
| Autoscale-up latency (add 8 workers) | | < 120 s |
| `ray.data` read throughput from T3 | End-to-end data path | Compare to Phase 28's B8 |
| **Ray Train 8-GPU vs. native PyTorch DDP** | ⚠️ Ray's overhead on training | **≤ 5 %** |

📊 **The same-node vs. cross-node object transfer table is the number to publish.** It is the concrete, measured version of "RAM is not a unified pool," in the framework users actually touch:
```
Same node (shared memory):     8.4 GB/s      ~0.12 s for 1 GB
Cross node (100 GbE):          11.2 GB/s     ~0.09 s for 1 GB   ← competes with NCCL
Cross node (25 GbE nodes):      2.9 GB/s     ~0.34 s for 1 GB
⚠️ A task that pulls a 10 GB object from another node stalls ~1–3 s before it starts.
   Design your task graph so data stays where it was produced.
```

---

### Task 6 — The user guide

**`docs/user/ray-guide.md`**

````markdown
# Ray on NEXUS

## Submitting
    nexus ray submit --template ray-standard --gpus 8 -- python train.py
Or directly:
    kubectl apply -f my-rayjob.yaml     # use the template; it has the right defaults

## What the platform sets for you
· /dev/shm sized correctly (Ray fails badly without this)
· Spill to fast local NVMe, not the container filesystem
· NCCL configured for this fabric
· Placement packed onto one leaf switch where possible
· Checkpoints synced to durable storage automatically
· The cluster SHUTS DOWN when your job finishes

## Patterns that work

### Parallel map — the 90 % case
    @ray.remote(num_gpus=1)
    def process(shard): ...
    refs = [process.remote(s) for s in shards]
    results = ray.get(refs)

### Hyperparameter sweep
    tuner = tune.Tuner(trainable, param_space=..., tune_config=...)
    # Set max_concurrent_trials — otherwise Tune requests everything at once
    # and your whole quota goes to one sweep.

### Distributed training
    trainer = TorchTrainer(train_fn,
        scaling_config=ScalingConfig(num_workers=8, use_gpu=True))
    # Ray Train handles the process group; NCCL config is injected.

## ⚠️ The one thing that will surprise you: data locality
Ray makes remote objects look local. They are not.
    big = ray.put(ten_gb_array)      # lives on THIS node
    task.remote(big)                 # may run elsewhere → 10 GB crosses the network

Instead:
 · Pass references to data already in object storage, and read inside the task
 · Use ray.data, which handles locality-aware scheduling for you
 · Keep the task graph local: produce and consume on the same node where possible

Measured on this cluster:
    same-node object transfer:   8.4 GB/s
    cross-node object transfer:  <see the table — depends on your node's NIC>

## Long-lived clusters
Use RayJob. If you genuinely need a persistent RayCluster, it requires approval,
and it is accounted against your quota for its entire lifetime, idle or not.
````

---

### Task 7 — Alerts

| Alert | Threshold |
|---|---|
| `KubeRayOperatorDown` | Any |
| `RayHeadNotReady` | Head pod unready > 5 min |
| `RayWorkersPending` | Workers pending > 10 min (quota or capacity) |
| `RayObjectStoreFull` | Spilling continuously |
| `RaySpillDiskFull` | `/scratch` > 85 % from spill |
| `RayClusterIdle` | A RayCluster with < 5 % SM_ACTIVE for 1 h (feeds Phase 34) |
| `RayClusterLongLived` | A RayCluster older than 7 days |
| `RayVersionMismatch` | Head and worker versions differ |
| `RayJobNotShutdown` | A completed RayJob whose cluster still exists |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | KubeRay operator healthy | `kubectl get pods -n ray-system` | Running |
| **A2** | A RayJob runs end to end and shuts down | Submit one | Completes, cluster gone |
| **A3** | **`shutdownAfterJobFinishes: true` enforced** | Submit with `false` | Rejected or mutated |
| **A4** | RayJob gated by Kueue quota | Submit over quota | Queued |
| **A5** | Ray cluster treated as a gang (min replicas) | 🧪 Insufficient capacity | Nothing starts |
| **A6** | Elastic: starts at `minReplicas`, grows to `maxReplicas` | Test | Grows |
| **A7** | Autoscaler releases idle workers after `idleTimeoutSeconds` | Test | Released |
| **A8** | **`/dev/shm` sized ≥ object store on every Ray pod** | Inspect a pod | Correct |
| **A9** | `/dev/shm` config injected automatically | Submit without it | Injected |
| **A10** | Spilling goes to `/scratch`, not the overlay FS | 🧪 Force spilling; check paths | `/scratch` |
| **A11** | Head has no GPU unless justified | Submit with a GPU head | Rejected/warned |
| **A12** | Ray version identical across head and workers | Startup assertion | Enforced |
| **A13** | `ray.data` reads from T3 object storage | Test | Works |
| **A14** | Ray Train checkpoints land in `/checkpoints` and sync | 🧪 Preempt a Ray Train job | Checkpoint in S3 |
| **A15** | Ray Train job survives preemption and resumes | 🧪 Preempt | Resumes |
| **A16** | Ray logs shipped to Loki | Query | Present |
| **A17** | 📊 **Benchmarks recorded, including same- vs cross-node object transfer** | `bench-ray.sh` | Table complete |
| **A18** | 📊 **Ray Train overhead vs. native DDP ≤ 5 %** | Benchmark | Met |
| **A19** | Placement packs workers into one leaf domain when possible | `placement-report.sh` | Packed |
| **A20** | NCCL config injected into Ray Train workers | `env \| grep NCCL` | Present, correct |
| **A21** | All templates deploy and run | Test each | All work |
| **A22** | All alerts fire | Induce | Fire |
| **A23** | 📊 The object-transfer table is published in the user guide | Read it | Published |

---

## ↩️ ROLLBACK

```bash
# Stop new Ray workloads
kubectl delete cpol inject-ray-config   # defaults stop being applied

# Drain: let running RayJobs finish, block new ones
kubectl patch clusterqueue <queue> --type merge -p '{"spec":{"stopPolicy":"Hold"}}'

# ⚠️ Deleting the operator orphans RayClusters — delete RayClusters first
kubectl get rayclusters -A
helm uninstall kuberay-operator -n ray-system
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Workers connect then die | Ray version mismatch | A12 |
| Terrible performance, constant disk I/O | `/dev/shm` too small → spilling | A8 — the most common issue |
| Node root filesystem fills | Spilling to the overlay FS | A10 |
| RayJob completes but GPUs stay held | `shutdownAfterJobFinishes: false` | A3 |
| Autoscaler requests pods that never schedule | Quota exhausted — correct behavior | Check `describe workload`; not a Ray bug |
| Tune sweep consumes the entire quota instantly | No `max_concurrent_trials` | Document it; consider a default |
| Tasks slow to start | Large objects transferring cross-node | The data-locality section; restructure the task graph |
| Head OOM | Driver holding results; too many object refs | Increase head memory; use `ray.data` streaming |
| Cross-node object transfer far below line rate | Ray using the wrong interface | Check `RAY_NODE_IP_ADDRESS` / the interface binding |
| Ray Train slower than native DDP by > 5 % | NCCL config not injected, or placement spread | A18/A19/A20 |
| Persistent RayCluster forgotten | No lifecycle policy | `RayClusterLongLived` alert; require justification |

---

## 🚫 DO NOT

- **Do not** deploy Ray pods without the `/dev/shm` fix.
- **Do not** let Ray spill to the container overlay filesystem.
- **Do not** allow `shutdownAfterJobFinishes: false` by default.
- **Do not** put a GPU on the Ray head by default.
- **Do not** let Ray's autoscaler bypass Kueue.
- **Do not** mix Ray versions across a cluster.
- **Do not** default users to long-lived RayClusters.
- **Do not** build Ray Serve deployments here. Phase 40.

---

## 📤 HANDOFF

`evidence/phase-36/handoff.md` must state:

1. **📊 The Ray benchmark table**, especially same-node vs. cross-node object transfer — the user-facing statement of the memory boundary.
2. **📊 Ray Train's overhead vs. native PyTorch DDP** — determines whether Phase 37 should recommend Ray Train or native.
3. **The templates deployed** and their resource shapes.
4. **The defaults injected** (`/dev/shm`, spill, NCCL, topology, shutdown) so Phase 46's golden paths document them.
5. **Autoscaler settings** and the observed release behavior.
6. **🧪 The preemption-survival result** for Ray Train.
7. **Known Ray-specific pitfalls** found during the phase.
8. **Any long-lived RayCluster approved**, with justification and an expiry.

---

## ➡️ NEXT

**[PHASE-37 — PyTorch Distributed Training](PHASE-37.md)** — the workload this cluster exists for: multi-node training that actually scales.
