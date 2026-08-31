# PHASE 27 — Tier 2: Rook-Ceph Shared Filesystem & Block

| | |
|---|---|
| **Stage** | 4 — Storage Fabric |
| **Estimated effort** | 5–6 hours (plus rebalance wait time) |
| **Depends on** | 24, 26 |
| **Blocks** | 28, 29, 43, 44 |
| **Risk** | 🔴 High — the cluster's durable data lives here; misconfiguration is expensive to undo |
| **Blast radius** | All shared datasets, home directories, and general block volumes |
| **Architecture refs** | `ARCHITECTURE.md#l6-storage-fabric` (T2), `#l63-crush-map-design`, ADR-015, ADR-016 |

---

## 🎯 MISSION

Stand up **Rook-Ceph** as the cluster's durable, shared, self-healing storage: **CephFS** for datasets, home directories, and anything multiple pods read simultaneously; **RBD** for general-purpose block volumes. Design the CRUSH map to match your real failure domains, and prove the cluster heals from a disk and a node loss without data loss.

> 💡 **WHY Ceph despite its complexity.** Nothing else gives you a single system that provides block, file, and object over the same disks, with configurable durability (replication or erasure coding), CRUSH-driven placement that understands racks, and self-healing that actually works at 100+ nodes. Simpler options (NFS, GlusterFS, MinIO alone) each solve one of these and force you to run three systems. Law X says the boring choice wins — at this scale, Ceph *is* the boring choice.

> ⚠️ **The single most consequential decision in this phase is the CRUSH failure domain.** Get it wrong and a "replica 3" pool has all three copies in one rack. Fixing it later means a full data rebalance measured in days. Decide it now, from Phase 24's failure-domain map, and verify it with `ceph osd tree`.

---

## ✅ PREFLIGHT

```bash
# Devices for T2, prepared and raw
yq '.nodes[].devices[] | select(.assigned_tier | test("T2"))' \
   storage/design/device-classification.yaml

# Failure domain decision from Phase 24
yq '.rules.ceph_failure_domain' storage/design/failure-domain-map.yaml

# 📊 The fdatasync numbers — this determines your WAL placement
jq '.[] | {model, fdatasync_p99_ms}' benchmarks/baselines/b6-device-raw.json

# Minimum viable: 3 nodes with OSD devices, ideally 4+
kubectl get nodes -l nexus.io/storage.tier2=true

# Network headroom — Ceph replication is 2× write traffic on the wire
cat benchmarks/baselines/b3-tcp.json
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/storage/tier2/
  rook-operator-values.yaml
  cephcluster.yaml                  # ⚠️ the CRUSH + OSD topology
  cephblockpool-replicated.yaml
  cephblockpool-ec.yaml             # erasure-coded, for capacity
  cephfilesystem.yaml               # CephFS + MDS
  storageclass-block.yaml           # nexus-block (RBD)
  storageclass-fs.yaml              # nexus-fs (CephFS, RWX)
  storageclass-home.yaml            # nexus-home (CephFS subvolume, quota'd)
  snapshotclasses.yaml
  ceph-toolbox.yaml
tools/storage/
  bench-tier2.sh                    # 📊 B7
  ceph-health.sh
  crush-verify.sh                   # 🧪 prove replicas span domains
  ceph-failure-test.sh
docs/operations/ceph-runbook.md
observability/rules/tier2-alerts.yaml
benchmarks/baselines/b7-cephfs.json
evidence/phase-27/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version |
|---|---|
| Rook | `v1.16.2` |
| Ceph | `v19.2.1` (Squid) |
| CSI drivers | Bundled with Rook |

> ⚠️ Rook and Ceph versions are coupled. Use the Ceph version that the Rook release documents as supported. Do not mix.

---

## 📋 TASKS

### Task 1 — 🎯 The CRUSH design (decide before deploying)

**The rule:** the failure domain must be the largest unit that fails together.

| You have | Failure domain | Replica 3 means | Honest assessment |
|---|---|---|---|
| 1 rack | `host` | 3 different nodes, 1 rack | Survives node loss. **Does not survive a rack power loss.** Say so. |
| 2 racks | `host` (with a rack-aware rule if possible) | 3 nodes | Cannot place 3 replicas in 3 racks. Consider replica 2 + rack domain, or accept `host`. |
| **3+ racks** | **`rack`** | **3 racks** | ✅ Survives a full rack loss |

```yaml
# In cephcluster.yaml — the topology comes from node labels
# Rook reads these standard labels to build the CRUSH hierarchy:
#   topology.kubernetes.io/region
#   topology.kubernetes.io/zone
#   topology.rook.io/rack        ← set this in Phase 14's labeler
#   topology.rook.io/chassis
#   kubernetes.io/hostname
```

> 🧪 **`crush-verify.sh` must actually prove it**, not assume it:
> ```bash
> # For a test object, show where its replicas actually landed:
> ceph osd map <pool> <object>
> # Then map each OSD back to its host and rack, and assert all three differ.
> # Do this for 100 random objects, not one.
> ```

**The pool design:**

| Pool | Type | Failure domain | For | Usable |
|---|---|---|---|---|
| `nexus-block` | Replica 3 | rack/host | RBD volumes, databases | 33 % |
| `nexus-fs-data` | Replica 3 | rack/host | CephFS file data | 33 % |
| `nexus-fs-meta` | **Replica 3, on the fastest devices** | rack/host | CephFS metadata | 33 % |
| `nexus-bulk` | **EC 4+2** | rack/host | Large datasets, cold-ish | 67 % |

> ⚠️ **CephFS metadata pool must be on your fastest, lowest-latency devices.** Metadata operations dominate the experience of a shared filesystem. A metadata pool on HDD makes CephFS feel broken even when data throughput is fine.

**Erasure coding — when and when not:**

| | Replication (3×) | EC 4+2 |
|---|---|---|
| Usable capacity | 33 % | 67 % |
| Write latency | Lower | **Higher** (must write 6 chunks) |
| Small-write efficiency | Good | **Poor** (read-modify-write) |
| Recovery cost | Read 1 replica | **Read k chunks, recompute** |
| CPU | Low | Higher |
| Use for | Hot data, RBD, CephFS metadata | Large sequential objects, archives |

> 🚫 **Do not use EC for RBD volumes backing databases or for CephFS metadata.** The read-modify-write penalty on small writes is severe. EC is for large, mostly-sequential, mostly-read data.

---

### Task 2 — WAL/DB placement (where the fdatasync numbers pay off)

BlueStore separates the OSD's data from its write-ahead log and RocksDB metadata.

| Layout | When | Effect |
|---|---|---|
| All on one device | Only one device per node | Simplest; WAL competes with data |
| **WAL+DB on a PLP NVMe, data on HDD/SATA** | Mixed media available | ✅ **Large win** — small writes and metadata get NVMe latency |
| WAL+DB on the same NVMe as data | All-NVMe nodes | Fine; minimal benefit from separation |

**Sizing:** DB ≈ 4 % of the data device for RBD/CephFS workloads (Ceph's guidance is 1–4 %; use 4 % to avoid RocksDB spilling to the slow device). WAL ≈ 2 GiB, and it lives inside the DB device if you specify only DB.

> ⚠️ **A DB device shared by too many OSDs is a failure domain.** If one NVMe holds the DB for six HDD OSDs, losing that NVMe loses six OSDs at once. Ceph will recover, but that is a large simultaneous failure. Cap it at 4–6 OSDs per DB device and make sure CRUSH treats them as one failure unit (`chassis` or a custom bucket).

---

### Task 3 — The CephCluster resource

Key fields and why each matters:

```yaml
spec:
  cephVersion: { image: quay.io/ceph/ceph:v19.2.1 }
  mon:
    count: 3                              # 5 at >50 nodes
    allowMultiplePerNode: false           # ⚠️ never true in production
  mgr:
    count: 2
    modules: [{ name: rook, enabled: true }, { name: pg_autoscaler, enabled: true }]
  dashboard: { enabled: true, ssl: true }
  network:
    provider: host                        # ⚠️ see the note below
    connections:
      encryption: { enabled: false }      # msgr2 encryption costs ~10-20% — decide explicitly
      compression: { enabled: false }
  storage:
    useAllNodes: false                    # ⚠️ NEVER true — be explicit
    useAllDevices: false                  # ⚠️ NEVER true — this is how disks get eaten
    nodes:
      - name: nexus-stor-001
        devices:
          - name: "/dev/disk/by-id/nvme-INTEL_..._PHLJ123456"   # by-id (Phase 24 P1)
        config: { osdsPerDevice: "1" }
  placement:
    osd:
      nodeAffinity: { ... nexus.io/storage.tier2: "true" ... }
  resources:
    osd:   { requests: { cpu: "2", memory: "6Gi" }, limits: { memory: "8Gi" } }
    mon:   { requests: { cpu: "1", memory: "2Gi" } }
    mds:   { requests: { cpu: "2", memory: "8Gi" } }
  disruptionManagement:
    managePodBudgets: true                # Rook creates PDBs; drains stay safe
```

> 🚫 **`useAllDevices: true` is the single most destructive setting in Rook.** It tells Rook to consume every unclaimed block device on every matching node. Combined with a broad node selector, it will eat your scratch disks, your Mayastor disks, and anything else that happens to look free. **Always enumerate devices explicitly by-id.**

> ⚠️ **`network.provider: host` vs. the pod network.** Host networking gives Ceph the full NIC and avoids the CNI datapath, meaningfully improving throughput and latency. It also means Ceph daemons bind host ports and bypass NetworkPolicy. Given the RoCE/jumbo-frame fabric already in place, host networking is the right choice here — but it must be recorded in the Phase 04 threat model as an accepted exception, with the Ceph public network restricted to the storage VLAN.

**Separate public and cluster networks:**
```
public network  = client ↔ Ceph traffic          (VLAN 300)
cluster network = OSD ↔ OSD replication/recovery (VLAN 301, or the same if only one fabric)
```
Separating them keeps a recovery storm from starving client I/O. If you have only one high-speed fabric, use QoS/DSCP to prioritize client traffic instead, and say so.

---

### Task 4 — CephFS and the MDS

```yaml
# cephfilesystem.yaml
spec:
  metadataPool: { replicated: { size: 3 }, deviceClass: nvme }   # ⚠️ fastest devices
  dataPools:
    - name: default
      replicated: { size: 3 }
      deviceClass: nvme
    - name: bulk
      erasureCoded: { dataChunks: 4, codingChunks: 2 }
      deviceClass: hdd
  metadataServer:
    activeCount: 2                    # multiple active MDS = directory sharding
    activeStandby: true
    resources: { requests: { cpu: "2", memory: "8Gi" }, limits: { memory: "16Gi" } }
```

**MDS sizing — the rule that surprises people:** the MDS caches inodes in **memory**, and its cache size determines how many files can be "hot." A dataset of 10 million small files needs a large MDS cache or every access becomes a metadata read from disk.

```
mds_cache_memory_limit ≈ 8–16 GiB per active MDS
Rough capacity: ~1 KiB per cached inode → 8 GiB ≈ 8M inodes
```

> ⚠️ **`activeCount: 2` sharded MDS requires pinning to be effective.** Without directory pinning, the balancer moves subtrees dynamically, which can be worse than a single MDS. Either pin large directories explicitly (`setfattr -n ceph.dir.pin`) or run a single active MDS with standby-replay. **Start with `activeCount: 1` + standby-replay**, and scale out only when the MDS is measurably the bottleneck.

---

### Task 5 — StorageClasses

| Class | Backend | Access modes | For |
|---|---|---|---|
| `nexus-block` | RBD, replica 3 | RWO | Databases, general block |
| `nexus-block-ec` | RBD on EC pool | RWO | Large, sequential block |
| `nexus-fs` | CephFS | **RWX** | Shared datasets, multi-pod reads |
| `nexus-home` | CephFS subvolume | RWX | Per-user home, quota'd |
| `nexus-bulk` | CephFS on EC pool | RWX | Large archives |

```yaml
# Critical RBD parameters
parameters:
  imageFeatures: layering,exclusive-lock,object-map,fast-diff,deep-flatten
  csi.storage.k8s.io/fstype: ext4
  # ⚠️ mounter: choose rbd-nbd only if the kernel client lacks features you need
```

> 💡 **RWX is the reason CephFS exists in this platform.** Multiple training pods reading the same dataset simultaneously is the defining shared-storage workload. RBD cannot do this safely. Every dataset that more than one job reads belongs on CephFS or object storage, never on an RBD volume with a hand-rolled sharing scheme.

---

### Task 6 — 📊 Benchmark T2 (B7) and complete the three-way table

**`tools/storage/bench-tier2.sh`** — the same fio profiles, now on RBD and CephFS.

📊 **The table that justifies the whole tier design:**

| Test | Raw (B6) | T0 local | T1 Mayastor | **T2 RBD** | **T2 CephFS** |
|---|---|---|---|---|---|
| Seq read 1M | 6.8 GB/s | 6.5 | 4.2 | | |
| Seq write 1M | 2.1 GB/s | 2.0 | 0.8 | | |
| Rand read 4K IOPS | 950k | 900k | 500k | | |
| Rand write 4K IOPS | 180k | 165k | 80k | | |
| Write latency p50 | 20 µs | 25 µs | 200 µs | | |
| **Write latency p99** | 200 µs | 250 µs | 800 µs | | |

**Plus the CephFS-specific tests that predict real pain:**

```bash
# Metadata operations — the CephFS make-or-break number
mdtest -n 10000 -i 3 -u -d /mnt/cephfs/test    # create/stat/delete rates

# Many concurrent readers on one dataset (the real workload)
fio --name=shared --rw=read --bs=1M --numjobs=32 --directory=/mnt/cephfs/dataset

# Small-file read storm — the anti-pattern, measured so you can quote it to users
fio --name=smallfiles --rw=randread --bs=16k --nrfiles=100000 --filesize=20k
```

📊 **Publish the small-file number to users.** "CephFS does ~N file opens/second; a dataset of 4 million files takes X minutes just to enumerate" is the single most effective argument for packing datasets into shards.

---

### Task 7 — 🧪 Failure testing

| # | Test | Expected |
|---|---|---|
| **C1** | Stop one OSD | `HEALTH_WARN`, PGs degraded, I/O continues, recovery begins |
| **C2** | Destroy one OSD's data and re-add | Backfill completes, `HEALTH_OK`, no data loss |
| **C3** | Power off one node | Recovery starts after `mon_osd_down_out_interval`; I/O continues |
| **C4** | Power off a whole rack (if rack domain) | Cluster survives; **prove it** |
| **C5** | Kill one mon | Quorum holds |
| **C6** | Kill two mons (of 3) | ⚠️ **Cluster I/O stops.** This is expected and is why you run 5 mons at scale. Document the recovery. |
| **C7** | Kill the active MDS | Standby takes over; measure the CephFS pause |
| **C8** | Fill a pool to `nearfull` (85 %) | Warning fires before write blocking |
| **C9** | Fill to `full` (95 %) | Writes blocked, no corruption; recovery procedure works |
| **C10** | Recovery storm impact on client I/O | Quantify; tune `osd_max_backfills`, `osd_recovery_sleep` |

> ⚠️ **C10 is where clusters actually fall over.** Default recovery settings can saturate the fabric and make every workload crawl during a rebuild. Measure it, then tune:
> ```
> osd_max_backfills = 1          # conservative during business hours
> osd_recovery_max_active = 3
> osd_recovery_sleep_ssd = 0
> osd_mclock_profile = balanced   # or high_client_ops when clients matter more
> ```
> Ceph Squid's mClock scheduler is the right tool; set the profile deliberately rather than leaving it default.

---

### Task 8 — Observability and alerts

| Alert | Threshold |
|---|---|
| `CephClusterWarning` / `CephClusterError` | `HEALTH_WARN` 15 min / `HEALTH_ERR` any |
| `CephOSDDown` | Any OSD down > 5 min |
| `CephOSDNearFull` / `CephOSDFull` | 85 % / 95 % |
| `CephPoolNearFull` | 80 % |
| `CephPGsDegraded` / `CephPGsUnavailable` | Any degraded 30 min / any unavailable |
| `CephMonQuorumAtRisk` | Down to bare quorum |
| `CephMDSCacheOversized` | Cache pressure |
| `CephSlowOps` | Ops > 30 s |
| `CephRecoveryStalled` | No progress 1 hr |
| `CephDaemonCrash` | Any new crash report |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | `ceph status` reports `HEALTH_OK` | Toolbox | OK |
| **A2** | All expected OSDs up and in | `ceph osd tree` | All |
| **A3** | **CRUSH hierarchy matches the physical failure-domain map** | `ceph osd tree` vs. Phase 24 map | Matches |
| **A4** | 🧪 **`crush-verify.sh` proves replicas span domains for 100 objects** | Run it | All distinct |
| **A5** | `useAllDevices` and `useAllNodes` are both `false` | Read the CR | False |
| **A6** | Every OSD device is referenced by-id | Read the CR | All by-id |
| **A7** | No device outside the T2 assignment was consumed | Compare to Phase 24 classification | None |
| **A8** | CephFS metadata pool is on the fastest device class | `ceph osd pool get` | nvme |
| **A9** | PG autoscaler enabled and PG counts sane | `ceph osd pool autoscale-status` | Sane |
| **A10** | RBD PVC provisions, mounts, survives pod restart | Test | Works |
| **A11** | CephFS RWX PVC mounts on 3 pods simultaneously | Test | All 3 read/write |
| **A12** | CephFS quota enforced on `nexus-home` | Exceed it | Enforced |
| **A13** | Snapshot and restore work for RBD and CephFS | Test both | Restored |
| **A14** | 📊 **B7 recorded; three-way tier table complete** | `bench-tier2.sh` | Complete |
| **A15** | 📊 CephFS metadata ops/sec measured | mdtest | Recorded |
| **A16** | 📊 Small-file penalty quantified and published | Benchmark + docs | Published |
| **A17** | 🧪 C1–C3 pass: OSD and node loss recover without data loss | Failure tests | Pass |
| **A18** | 🧪 C4: rack loss survived (if rack domain) | Test | Pass or N/A documented |
| **A19** | 🧪 C7: MDS failover pause measured | Test | Measured |
| **A20** | 🧪 C8/C9: full-pool behavior is graceful | Test | No corruption |
| **A21** | 🧪 **C10: recovery impact quantified and tuned** | Test + tune | Documented |
| **A22** | All alerts fire correctly | Induce each | Fire |
| **A23** | Ceph dashboard reachable behind SSO | Browser | Works |
| **A24** | Runbook covers every failure mode tested | Read | Complete |

---

## ↩️ ROLLBACK

```bash
# Stop new provisioning
kubectl delete sc nexus-block nexus-fs      # existing PVs unaffected

# ⚠️ FULL REMOVAL DESTROYS ALL T2 DATA. Migrate first.
# 1. Copy every T2 volume to T3 object storage or external backup
# 2. Verify the copies (checksums, not "it looked fine")
# 3. Delete CephFilesystem, CephBlockPools, then CephCluster
# 4. Run the Rook cleanup job to zap the OSD devices
# 5. Re-run Phase 24 preparation before any reuse
```

> 💡 **A partial rollback is usually better:** reduce the pool size, move data between pools, or change the CRUSH rule, all online. Full teardown is almost never the right answer.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| OSDs will not create | Device has signatures, or is already claimed | Re-run Phase 24 preparation; check `ceph-volume inventory` |
| Rook consumed an unexpected disk | `useAllDevices: true` | **Stop.** This is A5. Recover the disk only after confirming what it held. |
| `HEALTH_WARN: too few PGs` | Autoscaler still converging | Wait, or set `pg_num` manually |
| PGs stuck `undersized` | Not enough failure domains for the replica count | Reduce replicas, add nodes, or relax the CRUSH rule |
| All replicas in one rack | CRUSH rule uses `host` not `rack`, or labels are missing | Fix the labels (Phase 14) and the rule; expect a rebalance |
| CephFS very slow on small files | MDS cache too small; or the metadata pool is on slow media | Increase cache; move the metadata pool |
| Client I/O crawls during recovery | Default recovery aggressiveness | Set the mClock profile; throttle backfills (C10) |
| `slow ops` warnings | A slow disk, network issue, or overloaded OSD | Find the OSD; check its device against B6 |
| MDS repeatedly restarting | Cache pressure / OOM | Raise the memory limit and `mds_cache_memory_limit` together |
| Mount hangs after a node reboot | Stale kernel client session | Evict the session; check `ceph tell mds.* client ls` |
| Pool full, writes blocked | Genuine capacity exhaustion | Delete data, add OSDs, or raise the ratio **temporarily** while adding capacity |

---

## 🚫 DO NOT

- **Do not** set `useAllDevices: true` or `useAllNodes: true`.
- **Do not** deploy without verifying the CRUSH failure domain against physical reality.
- **Do not** put CephFS metadata on HDD.
- **Do not** use erasure coding for RBD volumes backing databases, or for CephFS metadata.
- **Do not** run `activeCount > 1` MDS without directory pinning.
- **Do not** run 3 mons at 100+ nodes — go to 5.
- **Do not** leave recovery tuning at defaults; measure C10 and set the mClock profile.
- **Do not** let users store millions of small files without telling them what it costs.
- **Do not** build object storage or the dataset cache here. Phase 28.

---

## 📤 HANDOFF

`evidence/phase-27/handoff.md` must state:

1. **📊 The complete three-way (five-column) tier performance table** — raw, T0, T1, T2-RBD, T2-CephFS. This is the artifact users and Phase 46's golden paths are built on.
2. **📊 CephFS metadata performance and the small-file penalty**, with the sentence to publish to users.
3. **The CRUSH failure domain in force**, the proof it works, and what it does *not* protect against.
4. **Usable capacity per pool**, replicated and EC.
5. **🧪 Failure test results C1–C10**, especially recovery impact (C10) and the tuning applied.
6. **MDS configuration** and the inode capacity it implies.
7. **The host-networking decision** and its threat-model exception.
8. **WAL/DB placement** and the OSD-per-DB-device ratio.
9. **Whether T1 (Mayastor) is justified** now that T2 numbers exist — the honest comparison.

---

## ➡️ NEXT

**[PHASE-28 — Tier 3/4: Object Storage & Dataset Cache](PHASE-28.md)** — S3 for datasets and artifacts, and the caching layer that makes reading them fast enough to feed GPUs.
