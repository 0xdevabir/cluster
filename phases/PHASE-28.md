# PHASE 28 — Tier 3/4: Object Storage & Dataset Cache

| | |
|---|---|
| **Stage** | 4 — Storage Fabric |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 25, 27 |
| **Blocks** | 29, 37, 38, 40, 44 |
| **Risk** | 🟡 Medium — data-path performance affects every training job |
| **Blast radius** | Dataset availability and training throughput |
| **Architecture refs** | `ARCHITECTURE.md#l6-storage-fabric` (T3/T4), `#l64-the-data-loading-pipeline`, Law VI |

---

## 🎯 MISSION

Give the cluster an **S3-compatible object store** for datasets, artifacts, and backups (T3), and a **node-local read cache** in front of it (T4) so that GPUs are fed at full speed without every epoch re-reading from the shared tier. Then build the data-loading pipeline that makes the right thing the easy thing.

> 💡 **WHY object storage plus a cache, rather than just CephFS.** Datasets are write-once, read-many, and read by many jobs concurrently. Object storage is the correct shape for that: flat namespace, no metadata server bottleneck, cheap erasure coding, versioning, and it is the native interface for every ML data loader. But a GPU consuming 2 GB/s of training data cannot fetch it over the network every epoch — the fabric belongs to gradients. T4 caches the working set on local NVMe (Phase 25), so the first epoch pays network cost and every subsequent epoch runs at local-NVMe speed. **This is Law VI implemented: move the data once, then keep compute next to it.**

> ⚠️ **The failure this phase prevents:** GPU utilization at 40 % because the data loader is starved. It is the single most common cause of wasted GPU capacity in real clusters, and it is invisible unless you measure the loader separately from the model.

---

## ✅ PREFLIGHT

```bash
# Ceph healthy; RGW can share the cluster
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status

# T0 scratch available on GPU nodes (the cache lives here)
kubectl get nodes -l nexus.io/storage.scratch=true
df -h /var/mnt/scratch   # via node metrics

# 📊 The tier table — the cache must beat T2/T3 or it is pointless
cat benchmarks/baselines/b7-cephfs.json

# Capacity for both bulk data and cache
yq '.tiers' storage/design/capacity-model.md
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/storage/tier3/
  cephobjectstore.yaml              # RGW on the EC pool
  cephobjectstoreuser.yaml
  storageclass-bucket.yaml          # COSI / ObjectBucketClaim
  bucket-policies/                  # per-tenant access
clusters/nexus-prod/storage/tier4/
  juicefs-values.yaml               # or alluxio-values.yaml
  storageclass-cached.yaml          # nexus-cached
  cache-warmer-job.yaml
tools/storage/
  bench-tier3.sh                    # 📊 B8 object throughput
  bench-tier4.sh                    # 📊 cache hit/miss performance
  dataset-import.sh                 # the sanctioned way to add a dataset
  dataset-shard.sh                  # small files → WebDataset shards
docs/user/
  dataset-guide.md                  # 🎯 the most-read doc in the platform
  data-loading-patterns.md
observability/rules/tier34-alerts.yaml
benchmarks/baselines/{b8-object.json,b8-cache.json}
evidence/phase-28/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Note |
|---|---|---|
| Ceph RGW | `v19.2.1` | Same as Phase 27 |
| JuiceFS CSI | `0.25.2` | T4 option A |
| Alluxio | `2.10.0` | T4 option B |
| COSI controller | `v0.2.0` | Bucket provisioning API |
| s5cmd | `2.2.2` | Fast parallel S3 client — **much** faster than aws-cli |

---

## 📋 TASKS

### Task 1 — T3: the object store

**Decision: Ceph RGW vs. standalone MinIO.**

| | Ceph RGW | MinIO |
|---|---|---|
| Shares Ceph disks and CRUSH | ✅ | ❌ Separate disks |
| One system to operate | ✅ | ❌ Two |
| Erasure coding | ✅ Via Ceph pools | ✅ Built in |
| Raw small-object performance | Good | Better |
| Multi-site / versioning / lifecycle | ✅ | ✅ |
| Licensing | Open | ⚠️ AGPL; check your policy |
| **Verdict** | ✅ **Default** — reuses Phase 27 | Use if you need a separate failure domain or MinIO-specific features |

```yaml
# cephobjectstore.yaml
spec:
  metadataPool: { replicated: { size: 3 }, deviceClass: nvme }
  dataPool:
    erasureCoded: { dataChunks: 4, codingChunks: 2 }   # 67 % usable
    deviceClass: hdd                                    # or nvme if that's all you have
  preservePoolsOnDelete: true          # ⚠️ TRUE — deleting the CR must not delete data
  gateway:
    instances: 3                        # HA; scale with client load
    port: 80
    securePort: 443
    resources: { requests: { cpu: "2", memory: "4Gi" } }
```

> 🚫 **`preservePoolsOnDelete: true` is not optional.** With `false`, a `kubectl delete cephobjectstore` — or an Argo prune, or a fat-fingered `kubectl apply` — destroys every dataset in the cluster. Combine with the Phase 15 guardrail: `Prune=false` on all storage CRs.

**Bucket layout — decide the namespace convention now:**

| Bucket | Purpose | Lifecycle | Versioning |
|---|---|---|---|
| `nexus-datasets` | Shared, read-mostly datasets | None — immutable | ✅ On |
| `nexus-artifacts-<tenant>` | Model checkpoints, outputs | Expire non-current after 90 d | ✅ On |
| `nexus-backups` | Phase 29 backups | Lifecycle to cold; 30-day retention | ✅ On + object lock |
| `nexus-scratch-<tenant>` | Large intermediates | **Expire after 7 days** | ❌ Off |

> 💡 **Turn versioning on for datasets before anyone uploads anything.** Retrofitting it does not protect the objects already there, and the first accidental `s5cmd rm` teaches this lesson expensively.

---

### Task 2 — T4: the cache, and why it exists

**The arithmetic that drives the design:**

```
A single 4090 training a vision model consumes ~1.5–3 GB/s of decoded images.
8 GPUs on a node → 12–24 GB/s of data.

From T3 over 100 GbE: 12.5 GB/s MAX, shared with NCCL. → STARVED.
From T2 CephFS:       ~4 GB/s per client typically.    → STARVED.
From T0 local NVMe:   6.8 GB/s per device.             → adequate with 2 devices.

Conclusion: the working set MUST be node-local by the second epoch.
```

**Choose the mechanism:**

| Option | How it works | Best for | Complexity |
|---|---|---|---|
| **A. Explicit staging** | Job downloads the dataset to `/scratch` at start (`s5cmd`), trains from there | Datasets that fit on local NVMe | ⭐ Lowest — and often the right answer |
| **B. JuiceFS** | POSIX FS over S3 + local NVMe cache + a metadata engine | Datasets larger than local NVMe; POSIX-required loaders | Medium |
| **C. Alluxio** | Distributed cache tier with cluster-wide cache sharing | Very large datasets, many jobs sharing hot data | High |

> 💡 **Start with A.** Explicit staging is boring, fast, debuggable, and covers most real datasets (< 2 TB). Deploy B for the cases A cannot handle, and only consider C if you measure cross-node cache sharing as a real win. Law X.

**Implement both A and B in this phase**, and document when to use each.

**JuiceFS requires a metadata engine** — this is the part people miss:

| Engine | Verdict |
|---|---|
| Redis | ⚠️ Fast but a single point of data loss unless carefully configured |
| **PostgreSQL (CloudNativePG) on T1** | ✅ Durable, backed up, already deployed in Phase 17 |
| TiKV | Overkill here |

> ⚠️ **The JuiceFS metadata engine holds the entire filesystem namespace.** Losing it means the objects in S3 are intact but unaddressable — exactly the Mayastor-etcd hazard from Phase 26. Put it on T1, replicate it, and back it up in Phase 29.

**Cache sizing:**
```
cache_size_per_node = min(
    local_NVMe_capacity × 0.6,          # leave room for scratch
    working_set_of_concurrent_jobs
)
Cache eviction: LRU. Monitor the hit rate — below ~85 % means the cache is too small
                     or jobs are thrashing between datasets.
```

---

### Task 3 — 🎯 The dataset import path (make the right thing easy)

**`tools/storage/dataset-import.sh`** — the sanctioned way to add a dataset. It should:

1. Validate the source and compute checksums.
2. **Detect the small-file problem** and refuse or auto-shard:
   ```
   if file_count > 100_000 and mean_file_size < 1 MiB:
       ⚠️ "This dataset has 4.2M files averaging 18 KiB.
           Reading it will be metadata-bound and slow (see Phase 27 B7).
           Sharding it into WebDataset tar files: [Y/n]"
   ```
3. Shard via `dataset-shard.sh` into ~1 GiB tar shards (WebDataset convention) or Parquet.
4. Upload with `s5cmd` using parallel transfers.
5. Write a **dataset manifest** to the catalog:
   ```yaml
   name: imagenet-1k
   version: "1.0"
   uri: s3://nexus-datasets/imagenet-1k/v1.0/
   format: webdataset
   shards: 1024
   size_bytes: 148000000000
   checksum_manifest: s3://.../SHA256SUMS
   license: "Non-commercial research only"     # ⚠️ record this
   imported_by: <user>
   imported_at: <ts>
   ```
6. Register it so `dataset-guide.md` and Phase 46's portal can list it.

> 🔒 **Record dataset licensing at import time.** It is the only moment anyone knows. This ties to RISK-LEGAL-01 from Phase 01 and to Phase 55's compliance work.

**Why sharding matters, with the number from Phase 27:**
```
4M individual files @ ~2,000 metadata ops/s (B7) = ~33 minutes just to LIST the dataset
1,024 tar shards of ~1 GiB   = 1,024 sequential reads at 6 GB/s = ~25 seconds
```

---

### Task 4 — 📊 Benchmark T3 and T4 (B8)

**T3 object throughput:**
```bash
# warp or s5cmd-based; measure single-client and aggregate
warp get   --objects 1000 --obj.size 100MiB --concurrent 32
warp put   --objects 1000 --obj.size 100MiB --concurrent 32
warp mixed --objects 10000 --obj.size 1MiB              # small-object behavior
```

| Metric | Target | Measured |
|---|---|---|
| Single-client GET (large objects) | ≥ 2 GB/s | |
| Aggregate GET (32 clients) | ≥ 80 % of the RGW pool's capability | |
| Small-object (1 MiB) ops/s | Record it | |
| PUT throughput | ≥ 1 GB/s | |
| p99 first-byte latency | < 100 ms | |

**T4 cache performance — the numbers that matter most:**

| Scenario | Expected | Measured |
|---|---|---|
| Cold read (cache miss, from T3) | ~T3 speed | |
| **Warm read (cache hit)** | **≥ 90 % of T0 local NVMe** | |
| Cache hit rate, steady state, single dataset | > 95 % | |
| First-epoch vs. second-epoch training step time | **2nd epoch ≥ 2× faster** | |
| Metadata operation latency (JuiceFS) | < 5 ms | |

📊 **The end-to-end test that proves the tier works:**
```
Run the same 3-epoch training job three ways and record GPU utilization:
  1. Reading directly from T3 (S3)          → expect GPU util 30–50 %
  2. Reading from T2 (CephFS)               → expect GPU util 50–70 %
  3. Staged to T0 / read through T4 cache   → expect GPU util > 90 %
```
This table is the justification for the entire tier design, and it is the most persuasive artifact you can hand a user who wants to "just read from S3."

---

### Task 5 — The data-loading patterns doc

**`docs/user/data-loading-patterns.md`** — the practical guide.

```markdown
# Feeding your GPUs

## The rule
Your GPUs can consume 2–3 GB/s EACH. If your data loader cannot sustain that,
you are renting expensive silicon to wait on I/O.

## Pattern 1 — Stage to scratch (use this first)
    initContainer:
      s5cmd cp 's3://nexus-datasets/imagenet-1k/v1.0/*' /scratch/data/
    then train from /scratch/data
✅ Simple, fastest, no extra systems.  ❌ Dataset must fit on local NVMe.

## Pattern 2 — Cached filesystem (dataset too large for scratch)
    volumes: [{ name: data, persistentVolumeClaim: { claimName: pvc-on-nexus-cached }}]
✅ Any size. Transparent POSIX.  ❌ First epoch is slow; needs a warm cache.

## Pattern 3 — Stream shards (dataset much larger than any node)
    WebDataset / FFCV / MosaicML StreamingDataset reading tar shards directly from S3,
    with a local cache directory on /scratch.
✅ Scales to any size.  ❌ Requires your loader to support it.

## ❌ Anti-patterns — these will starve your GPUs
· One file per sample, millions of files. Shard it. (See the import tool.)
· Reading from S3 without a cache, every epoch.
· `num_workers=2` with 8 GPUs. Use ~4 CPU workers per GPU.
· Decoding JPEGs on the CPU when DALI/nvJPEG can do it on the GPU.
· Random access across a 200 TB dataset. Shuffle within shards instead.

## Diagnosing a starved job
    · GPU util (DCGM_FI_PROF_SM_ACTIVE) low but the job is "running"  → I/O bound
    · Check: is the step time dominated by the data wait? Profile it (Phase 51).
    · Check your cache hit rate on the T4 dashboard.
```

> 💡 **`DCGM_FI_PROF_SM_ACTIVE`, not `DCGM_FI_DEV_GPU_UTIL`** (Phase 18). `GPU_UTIL` reports "a kernel was resident," which reads near 100 % even when the SMs are idle waiting on data. `SM_ACTIVE` shows the truth.

---

### Task 6 — Alerts

| Alert | Threshold |
|---|---|
| `RGWDown` | Any gateway instance down |
| `RGWHighLatency` | p99 > 500 ms for 10 min |
| `BucketNearQuota` | > 85 % of tenant quota |
| `ObjectPoolNearFull` | > 80 % |
| `CacheHitRateLow` | < 80 % for 30 min |
| `CacheFull` | Cache device > 90 % |
| `JuiceFSMetadataDown` | Metadata engine unreachable |
| `JuiceFSMetadataLagging` | Slow metadata ops |
| `DatasetImportFailed` | Import job failure |
| `LifecyclePolicyStalled` | Expiration not running |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | RGW healthy, all instances serving | `ceph status`, HTTP check | Healthy |
| **A2** | S3 API works with `s5cmd` and `boto3` | Test both | Works |
| **A3** | `preservePoolsOnDelete: true` | Read the CR | True |
| **A4** | Buckets created per the layout, with the right versioning | `s5cmd` list + get-versioning | Correct |
| **A5** | Lifecycle policies applied and running | Check expiration on a test object | Runs |
| **A6** | Per-tenant bucket isolation enforced | Cross-tenant access attempt | Denied |
| **A7** | 📊 **B8 object throughput recorded** | `bench-tier3.sh` | Recorded |
| **A8** | 📊 Small-object performance recorded | warp mixed | Recorded |
| **A9** | Pattern 1 (explicit staging) works end to end | Test job | Works |
| **A10** | JuiceFS/Alluxio mounts and reads correctly | Test PVC | Works |
| **A11** | JuiceFS metadata engine is on T1, replicated | Inspect | Confirmed |
| **A12** | Cache directory is on T0 scratch, sized per the model | Inspect | Confirmed |
| **A13** | 📊 **Warm cache read ≥ 90 % of local NVMe speed** | `bench-tier4.sh` | Met |
| **A14** | 📊 Cache hit rate > 95 % steady state | Measure | Met |
| **A15** | 📊 **The three-way GPU-utilization comparison is recorded** | End-to-end test | Complete |
| **A16** | 📊 GPU util > 90 % with the cached/staged path | Same test | Met |
| **A17** | `dataset-import.sh` detects and offers to shard small-file datasets | Import a small-file dataset | Detected |
| **A18** | Sharding produces valid WebDataset shards a loader can read | Test with PyTorch | Reads |
| **A19** | Dataset manifest written, including license | Import | Complete |
| **A20** | Dataset catalog lists imported datasets | Query | Listed |
| **A21** | Cache eviction works under pressure; no job failure | Fill the cache | Evicts cleanly |
| **A22** | Losing the cache does not lose data | Delete the cache dir | Re-reads from T3 |
| **A23** | All alerts fire | Induce each | Fire |
| **A24** | Data-loading guide is written and accurate | Read + verify the numbers | Accurate |

---

## ↩️ ROLLBACK

```bash
# T4 cache — safe to remove, it holds no authoritative data
helm uninstall juicefs-csi -n storage
# Jobs fall back to Pattern 1 (staging). Slower first epoch, no data loss.

# T3 — ⚠️ DO NOT delete the CephObjectStore. preservePoolsOnDelete protects the
# pools, but the correct rollback is to stop provisioning new buckets:
kubectl delete storageclass nexus-bucket
# Existing buckets and data remain accessible via the S3 endpoint.
```

> 💡 **T4 is the safe thing to roll back** — it is a cache by definition, and every byte in it exists in T3. That property is worth preserving in the design: if the cache holds anything that is not also in T3, it is not a cache.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| S3 throughput far below target | Too few RGW instances; or EC pool on slow HDDs | Scale gateways; check the pool's device class |
| Small-object performance poor | Expected on EC | Use a replicated pool for small-object buckets |
| Cache hit rate low | Cache too small, or jobs thrash between datasets | Resize; or co-schedule jobs on the same dataset (Phase 31) |
| First epoch slow, later epochs also slow | The cache is not being populated — check the mount and the cache dir | Verify the cache path is on NVMe, not the root FS |
| JuiceFS mount hangs | Metadata engine unreachable | Check Postgres on T1; this is why it is replicated |
| GPU util still low with the cache warm | Not I/O bound — it is CPU preprocessing | Increase workers; move decode to GPU (DALI) |
| `s5cmd` slow | Insufficient concurrency | `--numworkers 256`; check the client's NIC |
| Objects deleted accidentally | Versioning off | Restore from a version if on; from backup if not. **This is A4.** |
| Bucket quota exceeded mid-job | No pre-flight quota check | Add quota to the Phase 30 admission checks |
| Import tool refuses a dataset | Small-file detection | It is right. Shard it. |

---

## 🚫 DO NOT

- **Do not** set `preservePoolsOnDelete: false`.
- **Do not** let a dataset into the platform without a manifest and a license record.
- **Do not** train directly from S3 without a cache or staging step.
- **Do not** store anything authoritative only in the T4 cache.
- **Do not** put the JuiceFS metadata engine on scratch or on JuiceFS itself.
- **Do not** use `DCGM_FI_DEV_GPU_UTIL` to judge whether a job is I/O bound.
- **Do not** deploy Alluxio "because it is more capable" without measuring that JuiceFS or plain staging is insufficient.
- **Do not** implement backups here. Phase 29.

---

## 📤 HANDOFF

`evidence/phase-28/handoff.md` must state:

1. **📊 The three-way GPU-utilization comparison** (S3 direct / CephFS / cached) — the artifact that settles data-loading arguments for the life of the cluster.
2. **📊 B8 object and cache benchmarks**, and the warm-cache-vs-local-NVMe ratio.
3. **Which T4 mechanism is deployed** and the decision rule for A vs. B.
4. **Cache capacity per node** and the working-set size it supports.
5. **The bucket layout, lifecycle policies, and versioning state.**
6. **Where the JuiceFS metadata engine lives** — feeds Phase 29's backup scope.
7. **The dataset catalog** — what is imported, sizes, formats, licenses.
8. **The small-file penalty numbers** used by the import tool's warning.
9. **Total T3 usable capacity** and the growth plan.

---

## ➡️ NEXT

**[PHASE-29 — Backup, Disaster Recovery & Storage Gate G7](PHASE-29.md)** — prove you can get the data back, then close Stage 4.
