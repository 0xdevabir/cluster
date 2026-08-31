# PHASE 25 — Tier 0: Local NVMe Scratch

| | |
|---|---|
| **Stage** | 4 — Storage Fabric |
| **Estimated effort** | 3–4 hours |
| **Depends on** | 24 |
| **Blocks** | 28, 33, 36, 37, 38 |
| **Risk** | 🟢 Low — node-local, no distributed state |
| **Blast radius** | One node per failure |
| **Architecture refs** | `ARCHITECTURE.md#l6-storage-fabric` (T0), `#l64-the-data-loading-pipeline`, Law VI |

---

## 🎯 MISSION

Deliver the **fastest storage in the cluster** and the easiest to reason about: node-local NVMe exposed as ephemeral, quota'd, automatically-reclaimed scratch volumes. Make it trivially available to every job, and make its non-durability impossible to misunderstand.

> 💡 **WHY T0 first, before any distributed tier.** T0 solves 70 % of real storage demand — shuffle spill, dataset staging, checkpoint staging, container layers, compile caches — at zero network cost and zero coordination cost. Every byte served from T0 is a byte that does not traverse the fabric competing with NCCL. Building it first also means Phases 26–28 can be benchmarked *against* something.

> ⚠️ **The one hazard: users treating scratch as storage.** T0 has no replication, no snapshots, no backup, and vanishes when the pod ends or the node dies. If the platform does not make this loud and obvious, someone will lose three weeks of work. The naming, the docs, and the mount path all have to say "scratch."

---

## ✅ PREFLIGHT

```bash
# Phase 24 complete, devices prepared for T0
yq '.nodes[].devices[] | select(.assigned_tier == "T0-scratch")' \
   storage/design/device-classification.yaml | wc -l
cat storage/prepared-devices.log

# 📊 Raw baselines exist to compare against
cat benchmarks/baselines/b6-device-raw.json

# Talos can mount extra disks (machine config user volumes)
talosctl -n <node> get disks
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/storage/tier0/
  local-path-provisioner.yaml       # or the OpenEBS LocalPV variant
  storageclass-scratch.yaml         # nexus-scratch (ephemeral)
  storageclass-scratch-node.yaml    # nexus-scratch-node (node-bound, persists)
  scratch-quota-policy.yaml         # Kyverno: enforce sizeLimit
  scratch-reclaim-cronjob.yaml      # orphan cleanup
talos/patches/
  scratch-volumes.yaml              # user volume / disk mount config
tools/storage/
  scratch-verify.sh
  bench-tier0.sh                    # 📊 B6-T0
docs/user/scratch-guide.md
observability/rules/tier0-alerts.yaml
evidence/phase-25/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Note |
|---|---|---|
| local-path-provisioner | `v0.0.31` | Rancher; simplest correct option |
| OpenEBS LocalPV-LVM | `1.6.2` | Only if you need per-volume quota via LVM |
| Talos | Phase 09 pin | User volumes are machine-config driven |

---

## 📋 TASKS

### Task 1 — Choose the T0 mechanism

Three options. Pick one per node archetype; document why.

| Option | Quota enforcement | Capacity sharing | Complexity | Use when |
|---|---|---|---|---|
| **A. `emptyDir` on a fast mount** | ⚠️ `sizeLimit` — eviction-based, not hard | Shared pool | Lowest | Default for most jobs |
| **B. local-path-provisioner PV** | ❌ None (directory on a shared FS) | Shared pool | Low | Node-bound persistence across pod restarts |
| **C. LVM logical volume per PV** | ✅ **Hard block-level quota** | Carved | Medium | Multi-tenant nodes where one job must not fill the disk |

**Recommended combination:**
- **`emptyDir` (A)** for the default `nexus-scratch` experience — a pod asks for scratch, gets it, and it disappears cleanly. Zero provisioning latency.
- **LVM (C)** on shared multi-tenant nodes, where a hard quota matters more than simplicity.
- **local-path (B)** only for the narrow case of "this data must survive a pod restart on the same node" (e.g. a long-lived cache warmer).

> ⚠️ **`emptyDir.sizeLimit` is not a hard limit.** The kubelet monitors usage periodically and *evicts* the pod when it exceeds the limit. A job can briefly exceed it, and a burst can fill the disk before eviction fires. This is acceptable for cooperative workloads and unacceptable for hostile ones. If you need a hard limit, use LVM (C) or an XFS project quota.

---

### Task 2 — Mount the scratch device on Talos

Talos does not let you `mkfs` by hand. Disks are declared in the machine config.

```yaml
# talos/patches/scratch-volumes.yaml
machine:
  disks:
    - device: /dev/disk/by-id/nvme-Samsung_SSD_990_PRO_2TB_S6XVNJ0T123456
      # ⚠️ by-id, NOT /dev/nvme1n1 — paths reorder across reboots (Phase 24 rule P1)
      partitions:
        - mountpoint: /var/mnt/scratch
  kubelet:
    extraMounts:
      - destination: /var/mnt/scratch
        type: bind
        source: /var/mnt/scratch
        options: [bind, rshared, rw]
```

**Filesystem choice:**

| FS | Pros | Cons | Verdict |
|---|---|---|---|
| **XFS** | Excellent large-file and parallel-write performance; project quotas | No transparent compression | ✅ **Default for scratch** |
| ext4 | Universally understood | Slower on highly parallel workloads | Acceptable |
| Btrfs | Compression, snapshots | Overhead; more failure modes | ❌ Not for scratch |

Mount options that matter:
```
noatime          # do not write on every read — significant for dataset scanning
nodiratime
discard=async    # keep the FTL healthy without inline discard stalls
logbsize=256k    # XFS: larger log buffer for write-heavy workloads
```

> 💡 **`noatime` alone can be worth 5–15 % on a dataset-scanning workload.** Every file read otherwise causes a metadata write.

---

### Task 3 — StorageClasses and the naming that teaches

```yaml
# storageclass-scratch.yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nexus-scratch
  annotations:
    nexus.io/durability: "NONE — data is destroyed when the pod terminates"
    nexus.io/backup: "NEVER"
    description: >
      Node-local NVMe scratch. Fastest storage available (~6 GB/s read).
      DATA IS LOST when your pod ends or the node fails. Not backed up.
      Not replicated. Use for shuffle spill, staging, and temporary files only.
provisioner: rancher.io/local-path
volumeBindingMode: WaitForFirstConsumer    # ⚠️ MANDATORY — see below
reclaimPolicy: Delete
allowVolumeExpansion: false
```

> ⚠️ **`WaitForFirstConsumer` is mandatory for any node-local storage.** With `Immediate`, the PV is bound to a node before the scheduler places the pod, and the pod is then forced onto that node regardless of GPU availability, topology, or Kueue's decision. This is one of the most common and most damaging Kubernetes storage mistakes: it silently overrides your scheduler.

**The mount path teaches too.** Standardize on `/scratch` inside containers, never `/data` or `/mnt`. A path called `/scratch` is self-documenting when someone reads a Dockerfile six months later.

---

### Task 4 — Quota and admission

**`scratch-quota-policy.yaml`** — Kyverno rules:

| Rule | Enforcement |
|---|---|
| Every `emptyDir` on the scratch medium must declare `sizeLimit` | Reject if missing |
| `sizeLimit` must not exceed the per-pod cap (e.g. 500 Gi) | Reject |
| Sum of scratch requests in a namespace must respect the tenant quota | Reject |
| Mutate: default `sizeLimit` to 50 Gi when the annotation `nexus.io/scratch: "true"` is present | Convenience |

> 💡 **Default it, then enforce it.** A mutation that adds a sane default means most users never think about quota, and the enforcement rule only fires for people doing something unusual.

**Capacity accounting** — `emptyDir` does not appear in `kubectl describe node` allocatable. Track it yourself:
```
ephemeral-storage requests/limits  → the kubelet's own accounting for the node FS
scratch device usage               → node_filesystem_avail_bytes{mountpoint="/var/mnt/scratch"}
```
Alert on both.

---

### Task 5 — Reclamation and the orphan problem

Scratch leaks. A node reboots mid-pod, a PV is orphaned, a directory is left behind.

**`scratch-reclaim-cronjob.yaml`** — runs hourly on each node:

```
For each directory under /var/mnt/scratch/:
  · Extract the pod UID from the path
  · Query the API for a pod with that UID
  · If the pod does not exist AND the directory is older than 1 hour:
        → log the path, size, and age
        → delete it
  · If total usage > 85 %:
        → delete the oldest orphans first
        → 🚨 alert
```

**The guards:**

| Guard | Rule |
|---|---|
| Never delete a directory younger than 1 hour | Avoids racing pod startup |
| Never delete a directory whose pod exists in any state | Including `Pending` and `Terminating` |
| Always log before deleting (path, size, age, reason) | Forensics |
| Ships with `DRY_RUN=true` for the first 48 hours | Same discipline as Phase 23 |
| Refuse to run if `/var/mnt/scratch` is not the expected mount | Prevents deleting `/` if the mount failed |

> ⚠️ **That last guard is not paranoia.** If the scratch device fails to mount, `/var/mnt/scratch` is an empty directory on the root filesystem — and a cleanup script that does not check will happily start deleting from the wrong place, or worse, a bug in path construction lands it at `/`. Verify the mount is the expected device (by serial) before any deletion.

---

### Task 6 — 📊 Benchmark T0 (B6-T0)

**`tools/storage/bench-tier0.sh`** — the same fio profiles as Phase 24's raw baseline, now through the filesystem and the container.

| Test | Raw (B6) | Through XFS + container | Acceptable overhead |
|---|---|---|---|
| Seq read 1M | 6.8 GB/s | | ≤ 5 % |
| Seq write 1M (steady) | 2.1 GB/s | | ≤ 8 % |
| Rand read 4K | 950k IOPS | | ≤ 10 % |
| Rand write 4K (steady) | 180k IOPS | | ≤ 15 % |
| fdatasync p99 | 3.2 ms | | ≤ 10 % |

📊 **Also run the workload-shaped test** — the one that predicts real behavior:
```bash
# Simulated dataset read: many concurrent readers, mixed file sizes
fio --name=dataset --rw=randread --bs=128k --iodepth=32 --numjobs=16 \
    --directory=/scratch --size=10G --group_reporting

# Simulated checkpoint write: large sequential from one writer
fio --name=ckpt --rw=write --bs=4M --iodepth=8 --numjobs=1 \
    --directory=/scratch --size=40G --fsync_on_close=1
```

📊 **The number to publish to users:** "T0 gives you ~X GB/s sequential read and ~Y GB/s sequential write per node." That single sentence prevents most storage-related design mistakes downstream.

---

### Task 7 — The user guide

**`docs/user/scratch-guide.md`**

```markdown
# Scratch storage (/scratch)

## Getting it — the easy way
    metadata:
      annotations:
        nexus.io/scratch: "100Gi"
The platform mounts fast local NVMe at /scratch. That's it.

## What you get
· ~6 GB/s sequential read, ~2 GB/s sustained write, per node
· No network involved — it does not compete with your training traffic
· Zero provisioning delay

## 🚨 WHAT YOU LOSE
· When your pod ends — for ANY reason — /scratch is GONE
· If the node fails, /scratch is GONE
· It is NOT backed up. It is NOT replicated. There are NO snapshots.
· Nothing you cannot regenerate belongs here.

## The correct pattern for checkpoints
    1. Write the checkpoint to /scratch     (fast, no network)
    2. Async-copy it to CephFS or S3        (durable)
    3. Keep the last N locally for fast restart on the same node
The platform provides a sidecar that does this: see checkpoint-sync in the guide.

## The correct pattern for datasets
    1. Stage the dataset from S3 to /scratch once, at job start
    2. Train from /scratch for every epoch
NOT: read from S3 or CephFS every epoch. That is 100× more expensive and
it competes with the fabric your gradients need.

## Pitfalls
· Exceeding your sizeLimit gets your pod EVICTED. Ask for what you need.
· Millions of small files are slow even locally. Use tar/WebDataset shards.
· /scratch is per-pod. Two pods do not share it, even on the same node.
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Scratch device mounted on every GPU node | `talosctl df` / node metrics | Present |
| **A2** | Mounted by `by-id`, never by `/dev/nvmeXn1` | Read the machine config | By-id |
| **A3** | Mount survives a reboot with the same device | Reboot a node | Same device |
| **A4** | `noatime` and the chosen mount options are active | `findmnt -o OPTIONS` | Present |
| **A5** | `nexus-scratch` StorageClass exists with `WaitForFirstConsumer` | `kubectl get sc` | Set |
| **A6** | **A scratch PVC does not pin the pod to a node before scheduling** | Create a PVC, observe it stays Pending until a pod exists | Pending |
| **A7** | Annotation-driven scratch works end to end | Submit a pod with just the annotation | `/scratch` present, correct size |
| **A8** | `sizeLimit` is enforced (pod evicted on exceeding it) | Fill the volume | Evicted |
| **A9** | Kyverno rejects an `emptyDir` scratch with no `sizeLimit` | Try it | Rejected |
| **A10** | Scratch is destroyed on pod termination | Delete a pod, check the node | Directory gone |
| **A11** | 📊 **B6-T0: throughput within the overhead budget vs. raw** | `bench-tier0.sh` | Within table |
| **A12** | 📊 Workload-shaped benchmarks recorded | Run them | Committed |
| **A13** | Reclaim job identifies orphans correctly in dry run | 48-hour soak | No false positives |
| **A14** | Reclaim job **refuses to run when the mount is missing** | Unmount on a test node | Refuses |
| **A15** | Reclaim job never deletes a directory with a live pod | Simulated | Never |
| **A16** | Usage alerts fire at 85 % | Fill a test node | Fires |
| **A17** | Two pods on the same node get isolated scratch | Test | Isolated |
| **A18** | LVM path (option C) works where deployed | Test on a multi-tenant node | Hard quota enforced |
| **A19** | Scratch guide states non-durability unmistakably | Read it | Unmissable |
| **A20** | A node reboot with active scratch pods recovers cleanly | Reboot | Clean, orphans reclaimed |

---

## ↩️ ROLLBACK

```bash
# Disable new scratch allocations, keep existing ones running
kubectl patch sc nexus-scratch -p \
  '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"false"}}}'
kubectl delete cpol require-scratch-sizelimit

# Stop reclamation immediately
kubectl patch cronjob scratch-reclaim -n storage -p '{"spec":{"suspend":true}}'

# Unmount the device (nodes must be drained first)
# Revert the Talos patch and re-apply — the device is untouched, data is scratch anyway
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Pod scheduled to a node without a scratch device | No node selector on the scratch requirement | Add a `nexus.io/storage.scratch=true` label + selector |
| PVC stuck Pending forever | `WaitForFirstConsumer` and no pod is consuming it | Expected. Create the pod. |
| Pod pinned to an unexpected node | `volumeBindingMode: Immediate` | Change to `WaitForFirstConsumer`. This is A6. |
| Scratch performance far below B6 | Wrong mount options; or the device is not the one you think | `findmnt`; verify by serial |
| Write performance degrades over weeks | FTL exhaustion, no discard | Confirm `discard=async`; run `fstrim` weekly |
| Pods evicted unexpectedly | `sizeLimit` exceeded, or the node FS is full from orphans | Check reclaim job; raise the limit |
| Node disk full despite the reclaim job | Reclaim in dry run, or orphans younger than the threshold | Check `DRY_RUN`; check the age threshold |
| `/scratch` empty inside the container | The mount did not propagate | Check `rshared` propagation in the kubelet extra mounts |
| Reclaim job deleted something it should not have | A guard was missing | **Post-mortem.** Add the guard. This is why dry run exists. |

---

## 🚫 DO NOT

- **Do not** use `volumeBindingMode: Immediate` for node-local storage.
- **Do not** reference the device by `/dev/` path.
- **Do not** let the reclaim job run without the mount-verification guard.
- **Do not** name the StorageClass anything that sounds durable (`fast`, `local`, `nvme`). Call it `scratch`.
- **Do not** back up T0. It exists precisely because it is not backed up.
- **Do not** put datasets here permanently — stage them from T3/T4 per job.
- **Do not** build the checkpoint-sync sidecar here beyond a reference example. Phase 33 owns checkpointing.

---

## 📤 HANDOFF

`evidence/phase-25/handoff.md` must state:

1. **📊 T0 performance per node archetype** — the sentence you publish to users, plus the full B6-T0 table.
2. **The overhead of the filesystem + container layer** vs. raw devices.
3. **Which mechanism is deployed where** (emptyDir / LVM / local-path) and why.
4. **Scratch capacity per node and cluster-wide** — Phase 28's cache sizing and Phase 33's checkpoint strategy depend on this.
5. **The reclaim dry-run findings** — orphan rate, any false positives.
6. **Nodes without a scratch device**, and the implication for scheduling.
7. **The default `sizeLimit`** and the per-pod cap in force.

---

## ➡️ NEXT

**[PHASE-26 — Tier 1: Mayastor Replicated NVMe](PHASE-26.md)** — the first distributed tier. Fast block storage that survives a node loss.
