# PHASE 38 — Spark & Dask: Distributed Data Processing

| | |
|---|---|
| **Stage** | 6 — Distributed Compute Frameworks |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 25, 28, 30, 36 |
| **Blocks** | 41, 44 |
| **Risk** | 🟢 Low-Medium — CPU-bound; the risk is shuffle I/O swamping the fabric |
| **Blast radius** | Data processing workloads; fabric contention with training |
| **Architecture refs** | `ARCHITECTURE.md#l8-distributed-compute-runtimes`, `#l64-the-data-loading-pipeline`, Law VI |

---

## 🎯 MISSION

Deliver the **CPU-heavy half of the workload spectrum**: distributed ETL, dataset preparation, feature engineering, and large-scale analytics — the work that turns raw data into the sharded, cached datasets Phase 37's training jobs consume. Deploy Spark on Kubernetes and Dask, integrate both with Kueue and the storage tiers, and make sure their shuffle traffic never starves a training job.

> 💡 **WHY this phase matters even in a GPU cluster.** The user's brief explicitly asked for CPU-heavy work distributed across nodes. In practice, every serious ML pipeline spends substantial time on non-GPU work: deduplication, tokenization, filtering, joining, resharding, statistics. Running that on one machine is often the actual bottleneck in a research cycle — a tokenization job that takes 14 hours on a workstation takes 25 minutes across 40 nodes. **This phase is where the "distribute CPU work across machines" promise is delivered concretely**, and it directly feeds Phase 28's dataset import path.

> ⚠️ **The hazard: shuffle traffic.** A large Spark shuffle moves terabytes across the fabric in an all-to-all pattern — the single most bandwidth-hostile traffic shape there is, and precisely what Phase 22 measured as the most fabric-sensitive collective. An unconstrained shuffle running alongside distributed training will slow that training badly and neither user will understand why. Isolation is a deliverable, not a nice-to-have.

---

## ✅ PREFLIGHT

```bash
# CPU node capacity (this work should NOT land on GPU nodes)
kubectl get nodes -l nexus.io/pool=cpu

# Object storage and scratch (shuffle spill)
s5cmd ls s3://nexus-datasets/
kubectl get sc nexus-scratch

# Kueue flavors for CPU-only work (Phase 30)
kubectl get resourceflavors cpu-standard cpu-highmem

# Fabric QoS available for isolation (Phase 03/21 DSCP classes)
grep -A5 "DSCP" network/design/ip-plan.yaml
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/compute/data/
  spark-operator-values.yaml
  spark-templates/                  # small / standard / large SparkApplication
  spark-defaults.conf               # ⚠️ shuffle, S3, memory — the important file
  dask-operator-values.yaml
  dask-cluster-templates/
  data-clusterqueue.yaml            # CPU-flavored quota
policies/defaults/
  inject-spark-config.yaml
  restrict-data-jobs-to-cpu-pool.yaml   # ⚠️ keep ETL off GPU nodes
images/data/
  spark/Dockerfile
  dask/Dockerfile
tools/data/
  bench-data.sh                     # 📊 shuffle throughput, TPC-DS-like
  shuffle-impact-test.sh            # 🧪 does a big shuffle hurt training?
docs/user/
  data-processing-guide.md
  spark-guide.md
  dask-guide.md
observability/rules/data-alerts.yaml
evidence/phase-38/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version |
|---|---|
| Spark Operator (Kubeflow) | `2.1.0` |
| Apache Spark | `3.5.4` |
| Hadoop AWS / S3A | `3.4.1` |
| Dask Operator | `2024.12.1` |
| Dask / distributed | `2024.12.1` |

---

## 📋 TASKS

### Task 1 — Choose the tool per workload

Do not deploy both and let users guess.

| Workload | Tool | Why |
|---|---|---|
| SQL over large tabular data, joins, aggregations | **Spark** | Catalyst optimizer; mature; handles data far larger than memory |
| Existing Spark/Scala/PySpark code | **Spark** | Do not port it |
| Pandas/NumPy code that outgrew one machine | **Dask** | Same API; minimal rewrite |
| Array/tensor computation over huge arrays | **Dask** | `dask.array` |
| Custom Python parallelism, tasks with GPUs | **Ray** (Phase 36) | Better fit than either |
| ML dataset preparation → shards | **Spark or Ray Data** | Both fine; Ray Data if it feeds a Ray pipeline |

> 💡 **Recommend Ray Data for ML-adjacent preprocessing**, Spark for anything SQL-shaped, Dask for scientific Python. Three tools sounds like sprawl, but they serve genuinely different populations and each is small to operate on Kubernetes. What you must not do is let the same workload be written three ways.

---

### Task 2 — ⚠️ Keep data jobs off the GPU pool

**`restrict-data-jobs-to-cpu-pool.yaml`** — a Kyverno rule:
```
SparkApplication / DaskCluster pods MUST:
  · use ResourceFlavor cpu-standard or cpu-highmem
  · carry nodeSelector nexus.io/pool in (cpu, storage)
  · NOT request GPUs unless annotated nexus.io/gpu-etl-approved: "true"
```

> ⚠️ **A Spark executor that lands on a GPU node consumes CPU and memory that the GPU node needs to feed its GPUs.** Phase 37's data pipeline wants ~4 CPU workers per GPU; a Spark job squatting on 60 of those cores starves it. Keep the pools separate, with a narrow, approved exception for genuinely GPU-accelerated ETL (RAPIDS).

**Kueue integration:** data jobs get their own ClusterQueue on CPU flavors, in the same cohort. They can borrow idle CPU capacity from GPU nodes' unused cores only if you explicitly model that — **do not**, at least initially. The isolation is worth more than the utilization.

---

### Task 3 — 🎯 Shuffle: the configuration that decides everything

Spark's shuffle writes intermediate data to disk and reads it across the network. Getting this right is most of the phase.

**`spark-defaults.conf` — the entries that matter:**
```properties
# ── Shuffle spill: LOCAL NVME, never the container overlay FS ──
spark.local.dir                                   /scratch/spark
# ⚠️ Default is /tmp, which is the overlay FS. This one line is worth multiples
#    in performance and prevents filling node root filesystems.

# ── Adaptive execution: let Spark fix bad partition counts at runtime ──
spark.sql.adaptive.enabled                        true
spark.sql.adaptive.coalescePartitions.enabled     true
spark.sql.adaptive.skewJoin.enabled               true      # ⚠️ skew is the #1 Spark pathology

# ── Shuffle sizing ──
spark.sql.shuffle.partitions                      auto      # AQE handles it
spark.shuffle.file.buffer                         1m
spark.shuffle.compress                            true
spark.io.compression.codec                        zstd      # better ratio than lz4 at similar CPU

# ── S3A tuned for Ceph RGW (Phase 28) ──
spark.hadoop.fs.s3a.endpoint                      http://rgw.storage.svc:80
spark.hadoop.fs.s3a.path.style.access             true      # ⚠️ required for RGW/MinIO
spark.hadoop.fs.s3a.connection.maximum            200
spark.hadoop.fs.s3a.fast.upload                   true
spark.hadoop.fs.s3a.committer.name                magic     # ⚠️ NOT the file committer
spark.hadoop.fs.s3a.committer.magic.enabled       true

# ── Dynamic allocation: return executors when idle (Phase 34) ──
spark.dynamicAllocation.enabled                   true
spark.dynamicAllocation.shuffleTracking.enabled   true      # ⚠️ required on K8s
spark.dynamicAllocation.minExecutors              2
spark.dynamicAllocation.executorIdleTimeout       120s
```

> 🚫 **`spark.hadoop.fs.s3a.committer.name` must not be the default file committer.** The classic Hadoop committer renames files to "commit" them — but object stores have no rename; it becomes copy-then-delete of every output file. On a large job this turns a 5-minute write into an hour, and is not atomic. Use the magic committer.

> ⚠️ **Data skew is the single most common cause of "my Spark job is slow."** One partition with 400× the data of its peers means 999 executors finish in 2 minutes and one runs for 3 hours. `adaptive.skewJoin.enabled` handles the common join case; document the diagnosis (look at the max-vs-median task duration in the Spark UI) because it will come up constantly.

**Executor sizing — the rule that beats intuition:**
```
Prefer FEWER, LARGER executors up to a point, then stop:
  · 4–5 cores per executor is the sweet spot (HDFS/S3 throughput plateaus above ~5)
  · Memory: 4–8 GB per core
  · ⚠️ Do NOT create one giant executor per node — JVM GC pauses become brutal
  · ⚠️ Do NOT create one-core executors — shuffle fan-out explodes
```

---

### Task 4 — 🧪 The shuffle-vs-training isolation test

**`tools/data/shuffle-impact-test.sh`** — the experiment that justifies the isolation design.

```
Baseline:  run a 16-GPU training job alone.        Record step time.
Test:      run the same job WHILE a large Spark shuffle runs.  Record step time.
```

| Scenario | Training step time | Degradation |
|---|---|---|
| Training alone | 0.34 s | — |
| Training + Spark shuffle (no isolation) | | ⚠️ Expect 10–40 % |
| Training + Spark shuffle (DSCP class separation) | | Target < 5 % |
| Training + Spark shuffle (separate CPU pool + QoS) | | Target < 3 % |

**Isolation mechanisms, in order of effectiveness:**

| Mechanism | Effect |
|---|---|
| **Separate node pools** | Removes CPU/memory contention entirely |
| **DSCP marking + switch QoS** | Shuffle traffic in a lower-priority class than RoCE (Phase 03/21 already reserved priority 3 for RDMA) |
| Bandwidth limits per pod (Cilium) | Blunt but effective |
| Time separation (schedule ETL at night) | Free; often sufficient at small scale |

> 💡 **DSCP separation is the elegant answer and it is already half-built.** Phase 03 defined the lossless class for RoCE at priority 3 with DSCP 26. Mark Spark shuffle traffic into a best-effort class and the switch will drain it behind RDMA automatically. Verify the marking survives the CNI path — this is the kind of thing that silently does not work.

📊 **Publish the measured degradation.** If a big shuffle costs training 3 %, co-scheduling is fine and the cluster runs hotter. If it costs 35 %, ETL needs a time window. **Measure, then decide** (Law VIII).

---

### Task 5 — Dask

Dask is simpler and needs less configuration, but two things must be right.

```yaml
apiVersion: kubernetes.dask.org/v1
kind: DaskCluster
spec:
  worker:
    replicas: 20
    spec:
      containers:
        - name: worker
          args: ["dask-worker",
                 "--nthreads", "4",
                 "--memory-limit", "16GB",          # ⚠️ set it, or workers OOM-kill
                 "--local-directory", "/scratch/dask"]   # ⚠️ spill to NVMe
```

| Setting | Why |
|---|---|
| `--memory-limit` | Dask spills and pauses based on this. Unset → the worker uses everything and gets OOM-killed by the kernel, losing all its data. |
| `--local-directory` | Same `/scratch` reasoning as Spark and Ray |
| Worker/client version match | Same hazard as Ray (Phase 36 A12) |

> ⚠️ **Dask's memory management is advisory, not enforced.** It pauses new tasks at 80 % and spills at 70 % of the *declared* limit. If the declared limit exceeds the container limit, the kernel kills the worker before Dask ever reacts. **Set the Dask limit to ~85 % of the container memory limit**, and inject this relationship rather than trusting users.

---

### Task 6 — 📊 Benchmarks

**`tools/data/bench-data.sh`:**

| Benchmark | Measures | Note |
|---|---|---|
| Shuffle throughput (1 TB sort) | Fabric + disk under all-to-all | The headline data number |
| TPC-DS subset (or similar) at 1 TB | Query performance | Comparable across configurations |
| S3A read throughput vs. Phase 28's B8 | The committer/connector path | Should be within 15 % of raw |
| S3A write with the magic committer | ⚠️ vs. the file committer | Show the difference — it is dramatic |
| Dataset resharding (small files → WebDataset) | The Phase 28 import path at scale | Real utility |
| Dask array computation, 20 workers | Scientific path | |
| Dynamic allocation release time | Capacity returned when idle | < 3 min |

📊 **The most useful number to publish:** *"Resharding a 4-million-file dataset into 1,024 WebDataset shards: 14 hours on one workstation → 23 minutes on 40 nodes."* That single comparison sells the platform to the people who will use it most.

---

### Task 7 — The user guide

**`docs/user/data-processing-guide.md`**

````markdown
# Distributed data processing

## Which tool?
| SQL, joins, huge tabular data | Spark |
| Pandas/NumPy that outgrew one machine | Dask |
| Custom Python, GPU tasks | Ray (see the Ray guide) |

## Spark, minimally
    kind: SparkApplication
    spec:
      template: spark-standard        # the platform's defaults are already right
      mainApplicationFile: s3://.../my_job.py
      executor: { instances: 20 }

## What the platform sets for you
· Shuffle spill to fast local NVMe (not the container filesystem)
· S3A tuned for our object store, with the magic committer (10-100× faster writes)
· Adaptive query execution and skew handling on
· Dynamic allocation, so idle executors return to the pool
· Your job runs on CPU nodes, not GPU nodes

## ⚠️ The thing that will make your job slow: SKEW
If 999 tasks finish in 2 minutes and 1 runs for 3 hours, you have data skew.
    · Look at the Spark UI: max task duration vs. median
    · Salt your join keys, or repartition on a better column
    · AQE handles common cases automatically, not all of them

## Reading and writing our storage
    df = spark.read.parquet("s3://nexus-datasets/mydata/")
    df.write.mode("overwrite").parquet("s3://nexus-artifacts-myteam/output/")
⚠️ Write Parquet, not CSV. Write few large files, not many small ones.
   A 4-million-file output will make every downstream job slow (Phase 27's numbers).

## Preparing a dataset for training
The most common job here. Use the platform's tool:
    nexus dataset reshard --input s3://.../raw/ --output s3://.../v1.0/ \
                          --format webdataset --shard-size 1GB
It runs as a Spark job and registers the result in the dataset catalog.

## Big shuffles and other people's training jobs
A 1 TB shuffle moves a lot of data. We measured its impact on training as <X %>.
If you are running something very large, mention it — or schedule it off-peak.
````

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | Spark Operator healthy; a SparkApplication runs end to end | Submit | Completes |
| **A2** | Dask Operator healthy; a DaskCluster runs | Submit | Completes |
| **A3** | **Data jobs cannot land on GPU nodes** | Submit without the exception annotation | Rejected/constrained |
| **A4** | Both are gated by Kueue quota | Submit over quota | Queued |
| **A5** | **Shuffle spills to `/scratch`, not the overlay FS** | 🧪 Force a large shuffle; inspect paths | `/scratch` |
| **A6** | Node root filesystems do not fill during a large shuffle | Monitor | Stable |
| **A7** | S3A reads/writes to Ceph RGW work | Test | Works |
| **A8** | **Magic committer in use; writes are fast and atomic** | 📊 Compare against the file committer | Dramatic difference recorded |
| **A9** | `path.style.access: true` set | Read the config | Set |
| **A10** | AQE and skew join enabled | Read config; test a skewed join | Enabled, handled |
| **A11** | Dynamic allocation releases idle executors | 🧪 Idle test | Released < 3 min |
| **A12** | Dask memory limit set below the container limit | Inspect | ~85 % |
| **A13** | Dask worker spills rather than being OOM-killed | 🧪 Overfill | Spills |
| **A14** | Dask client/worker versions match | Assertion | Enforced |
| **A15** | 📊 **Shuffle throughput benchmarked** | `bench-data.sh` | Recorded |
| **A16** | 📊 **TPC-DS-like suite recorded as a baseline** | Benchmark | Recorded |
| **A17** | 🧪 **Shuffle-vs-training impact measured** | `shuffle-impact-test.sh` | Quantified |
| **A18** | Isolation reduces that impact to target | With DSCP/pool separation | < 5 % |
| **A19** | DSCP marking survives the CNI path | 🧪 Capture on the wire | Marked |
| **A20** | 📊 The resharding comparison (1 workstation vs. cluster) recorded | Benchmark | Published |
| **A21** | `nexus dataset reshard` works and registers in the catalog | Run it | Works |
| **A22** | Spark UI / Dask dashboard reachable behind SSO | Browser | Works |
| **A23** | All alerts fire | Induce | Fire |

---

## ↩️ ROLLBACK

```bash
# Stop new data jobs; running ones finish
kubectl patch clusterqueue data-queue --type merge -p '{"spec":{"stopPolicy":"Hold"}}'

helm uninstall spark-operator -n data
helm uninstall dask-operator -n data
# Users fall back to single-node processing — slower, not broken.
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Job very slow, one task runs forever | **Data skew** | Salt keys; repartition; check AQE is on |
| Node root filesystem fills | Shuffle spilling to `/tmp` on the overlay FS | A5 |
| Writing to S3 takes hours | File committer, not magic | A8 |
| `s3a` 403 / bucket-not-found | `path.style.access` not set for RGW | A9 |
| Executors OOM-killed | Memory limit vs. JVM heap mismatch; or skew | Set `memoryOverhead`; check skew |
| Dask workers killed, work lost | `--memory-limit` unset or above the container limit | A12 |
| Training jobs slow when ETL runs | Fabric or CPU contention | A17/A18 — apply isolation |
| Executors never released | Dynamic allocation off, or `shuffleTracking` off | Both required on K8s |
| Too many small output files | Not coalescing before write | `repartition()` before writing |
| Spark UI unreachable | Ingress/SSO config | Phase 17 |
| Job succeeds but output is missing | Committer wrote to a staging path and failed to commit | Check the committer; this is why magic matters |

---

## 🚫 DO NOT

- **Do not** let shuffle spill to the container overlay filesystem.
- **Do not** use the default file committer against object storage.
- **Do not** let data jobs run on GPU nodes without explicit approval.
- **Do not** leave Dask's `--memory-limit` unset.
- **Do not** create one-core or one-per-node executors.
- **Do not** write CSV, or millions of small files, as pipeline output.
- **Do not** assume shuffle traffic is harmless to training — measure it.
- **Do not** deploy Spark, Dask, and Ray for the same workload shape.

---

## 📤 HANDOFF

`evidence/phase-38/handoff.md` must state:

1. **📊 Shuffle throughput and the query benchmark baseline** — Phase 50's regression gates use these.
2. **🧪 The shuffle-vs-training impact measurement**, with and without isolation — determines whether ETL needs a time window.
3. **📊 The magic-vs-file committer comparison** — the number that justifies the config.
4. **📊 The resharding comparison** (single machine vs. cluster) — the user-facing value statement.
5. **The tool-selection guidance** given to users, and any workload that ended up in the wrong tool.
6. **Executor/worker sizing** defaults and the reasoning.
7. **Whether data jobs share the fabric with training**, and under what policy.
8. **How `nexus dataset reshard` integrates** with Phase 28's import path and catalog.

---

## ➡️ NEXT

**[PHASE-39 — MPI & Traditional HPC Workloads](PHASE-39.md)** — the tightly-coupled scientific codes that need every microsecond of the RDMA fabric.
