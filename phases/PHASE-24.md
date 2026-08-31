# PHASE 24 — Storage Architecture & Device Preparation

| | |
|---|---|
| **Stage** | 4 — Storage Fabric |
| **Estimated effort** | 3–4 hours |
| **Depends on** | 23 (Stage 3 gate) |
| **Blocks** | 25, 26, 27, 28, 29 |
| **Risk** | 🔴 High — device preparation is destructive and irreversible |
| **Blast radius** | Every disk in the cluster |
| **Architecture refs** | `ARCHITECTURE.md#l6-storage-fabric`, `ULTIMATE-PLAN.md#47-consumer-nvme-and-the-fsync-problem`, Law VI |

---

## 🎯 MISSION

Design the five-tier storage fabric concretely for **your** disks, classify every device in the fleet by capability, and prepare them for their assigned tier — safely, idempotently, and with an interlock that makes it impossible to wipe a disk that holds data.

> 💡 **WHY a design phase before any storage software.** Storage decisions are the hardest to reverse. Once Ceph owns a disk, taking it back means a rebalance. Once a dataset lives in the wrong tier, moving it costs hours of I/O. Law VI — *Data Gravity Wins*: **you can move compute to data far more cheaply than data to compute**. Get the placement right on paper, then execute.

> 🚫 **DANGER — THIS PHASE DESTROYS DATA.** Device preparation wipes partition tables and signatures. A wrong device path erases a boot disk or a colleague's dataset. Every destructive operation in this phase is gated behind serial-number matching and typed confirmation. **Do not remove those guards. Do not run preparation scripts with a wildcard device selector.**

---

## ✅ PREFLIGHT

```bash
# Stage 3 gate passed
cat gates/G4-fabric.md gates/G5-accelerator.md | grep -c "☑"

# Full device inventory from Phase 01, with serials
yq '.nodes[].storage' inventory/nodes/*.yaml

# Nothing already claims these disks
kubectl get pv,sc,csidrivers -A

# Network is ready — storage will use it heavily
cat benchmarks/baselines/b3-tcp.json benchmarks/baselines/b4-rdma.json
```

---

## 📦 DELIVERABLES

```
storage/
  design/
    tier-design.md                  # 🎯 the concrete plan for YOUR fleet
    device-classification.yaml      # every disk → tier assignment
    capacity-model.md               # usable vs raw, per tier
    failure-domain-map.yaml         # node → rack → power domain
  tools/
    classify-devices.sh             # read-only: what do we have?
    device-benchmark.sh             # 📊 B6 pre-tier baseline, read-only
    prepare-device.sh               # ⚠️ DESTRUCTIVE — serial-gated
    verify-preparation.sh
docs/user/storage-guide.md          # which tier for which data
benchmarks/baselines/b6-device-raw.json
evidence/phase-24/{preflight,acceptance,handoff,deviations,device-decisions.md}.md
```

---

## 📋 TASKS

### Task 1 — Classify every device (read-only)

**`storage/tools/classify-devices.sh`** — runs on every node, writes a machine-readable record. It **must not write to any device**.

For each block device, capture:

| Field | Source | Why it matters |
|---|---|---|
| Serial number | `lsblk -o SERIAL` / `nvme id-ctrl` | **The only safe device identity.** `/dev/nvme0n1` is not stable. |
| Model | `nvme id-ctrl` | Determines PLP, endurance |
| Size | `blockdev --getsize64` | Capacity model |
| Rotational | `/sys/block/*/queue/rotational` | HDD vs SSD |
| Interface | `nvme`/`sata`/`sas` | Bandwidth class |
| PCIe gen/width | `lspci -vv` | Detects a Gen4 drive in a Gen3 slot |
| PLP (power-loss protection) | Model lookup + `nvme id-ctrl` VWC | **The fsync question** |
| Endurance (DWPD/TBW) | Datasheet lookup | Lifetime under Ceph WAL |
| Current wear | `smartctl -A` percentage used | Do not put a 90 %-worn drive under Ceph |
| Existing signatures | `wipefs -n` (`-n` = **dry run**) | ⚠️ **Is there data on this?** |
| Mounted | `findmnt` | Never touch a mounted device |
| NUMA node | `/sys/block/*/device/numa_node` | Tier-0 alignment with Phase 20 |

> ⚠️ **`wipefs -n` (no-act) is the safe form. `wipefs -a` destroys.** The classification script uses `-n` only. Verify this by reading the script before running it.

**Output** — `device-classification.yaml`:
```yaml
nodes:
  - name: nexus-gpu-014
    devices:
      - serial: "S6XVNJ0T123456"
        path_at_scan: /dev/nvme0n1      # informational only, NEVER used for targeting
        model: "Samsung SSD 990 PRO 2TB"
        size_gb: 2048
        plp: false                       # ⚠️ consumer drive
        dwpd: 0.34
        wear_pct: 3
        numa_node: 0
        pcie: { gen: 4, width: 4 }
        signatures: []                   # empty = safe to prepare
        mounted: false
        assigned_tier: "T0-scratch"
        rationale: "No PLP → unsuitable for Ceph WAL or etcd. Excellent for scratch."
```

---

### Task 2 — The tier design for your actual hardware

Take the five-tier model from `ARCHITECTURE.md#L6` and make it concrete.

| Tier | Purpose | Backing | Access | Durability | Typical latency |
|---|---|---|---|---|---|
| **T0** | Job scratch, shuffle spill, checkpoint staging | Local NVMe, no replication | Node-local only | **None — dies with the node** | ~80 µs |
| **T1** | Fast persistent volumes: databases, hot model weights | Mayastor over NVMe-oF | Cluster-wide block | Replica 2–3 | ~200 µs |
| **T2** | Shared filesystem + general block: datasets, home dirs | Rook-Ceph (RBD + CephFS) | Cluster-wide | Replica 3 or EC | ~1–3 ms |
| **T3** | Object store: datasets, artifacts, backups | Ceph RGW or MinIO on HDD/SSD | S3 API | EC 4+2 | ~10 ms |
| **T4** | Read cache over T3 | JuiceFS/Alluxio + local NVMe | POSIX/S3 over cache | Cache — no durability | ~100 µs on hit |

**The device assignment rules — apply in order:**

| Rule | Condition | Assignment |
|---|---|---|
| D1 | Device is the boot disk | **Never touch.** Excluded permanently. |
| D2 | Device has signatures and is not explicitly released | **Never touch.** Requires a human decision. |
| D3 | Wear > 80 % or SMART failing | Exclude. Replacement queue. |
| D4 | Node is a control-plane node, device has PLP | etcd (already claimed in Phase 12) |
| D5 | Node is a GPU node, NVMe, no PLP | **T0 scratch** — the highest-value use for a consumer NVMe |
| D6 | Node is a storage node, NVMe with PLP | **T1 Mayastor** or Ceph WAL/DB |
| D7 | Node is a storage node, NVMe without PLP | T2 Ceph OSD data (acceptable; see the fsync note) |
| D8 | HDD | **T3** object / cold |
| D9 | Second NVMe on a GPU node | **T4 cache** |

> ⚠️ **The consumer-NVMe fsync problem (`ULTIMATE-PLAN.md §4.7`), restated where it bites.** Drives without power-loss protection either lie about fsync (fast but can lose acknowledged writes on power loss) or honor it through the NAND (safe but 10–50× slower). This is why:
> - etcd **must** be on PLP media (Phase 12 enforced this with a p99 fdatasync gate).
> - Ceph WAL/DB **should** be on PLP media; without it, expect poor small-write performance.
> - T0 scratch on consumer NVMe is **fine** — scratch is explicitly non-durable.
>
> If you have no PLP drives, say so in the deviations file and accept the consequences explicitly rather than discovering them during an outage.

**The failure-domain map** — Ceph's CRUSH and Mayastor's replica placement both need to know what fails together:
```yaml
# failure-domain-map.yaml
domains:
  - rack: R1
    power_feeds: [PDU-1A, PDU-1B]
    nodes: [nexus-stor-001, nexus-stor-002, nexus-gpu-001..012]
  - rack: R2
    ...
rules:
  # Replicas must span racks; a rack loses power as a unit.
  ceph_failure_domain: rack       # if ≥3 racks; otherwise host
  mayastor_topology_key: topology.kubernetes.io/rack
```

> 💡 **A three-replica pool inside one rack is a one-replica pool with extra steps.** If you only have one or two racks today, set the failure domain to `host`, and write down that upgrading to `rack` is a Milestone-M3 task requiring a rebalance.

---

### Task 3 — 📊 Raw device baselines (B6, before any storage software)

Measure the disks bare. Every later number is meaningless without this.

**`storage/tools/device-benchmark.sh`** — **read-only tests only** on devices holding data; full tests only on devices already classified as free.

```bash
# Sequential read (safe on any device)
fio --name=seqread --rw=read --bs=1M --iodepth=32 --numjobs=4 \
    --direct=1 --runtime=60 --time_based --filename=/dev/nvmeXn1 --readonly

# The rest require an EMPTY device (--readonly cannot be used):
# Random read IOPS
fio --name=randread --rw=randread --bs=4k --iodepth=128 --numjobs=4 --direct=1
# Random write IOPS (steady state — run ≥ 10 min, the first 60 s is SLC cache)
fio --name=randwrite --rw=randwrite --bs=4k --iodepth=128 --numjobs=4 --direct=1 --runtime=600
# ⚠️ THE ONE THAT MATTERS FOR CEPH/ETCD: sync write latency
fio --name=fsync --rw=write --bs=4k --iodepth=1 --numjobs=1 --fdatasync=1 --runtime=120
```

**Record per device model:**

| Metric | Consumer NVMe (typical) | PLP enterprise NVMe | What it predicts |
|---|---|---|---|
| Seq read | 6–7 GB/s | 6–7 GB/s | Dataset streaming |
| Seq write (SLC burst) | 5–6 GB/s | 5 GB/s | **Misleading** — measure steady state |
| Seq write (steady) | 1.5–2.5 GB/s | 4–5 GB/s | Checkpoint writes |
| Rand read 4K | 800k–1M IOPS | 900k IOPS | Random dataset access |
| Rand write 4K steady | 100–200k IOPS | 400k+ IOPS | Ceph OSD load |
| **fdatasync p99** | **1–10 ms** | **< 0.2 ms** | **etcd, Ceph WAL, databases** |

> 💡 **Run the write test for at least 10 minutes.** Consumer NVMe holds a pseudo-SLC cache that makes the first 30–100 GB look enterprise-grade. Steady-state performance is often 3–4× lower. Benchmark the steady state; that is what your cluster will experience.

---

### Task 4 — ⚠️ Device preparation (the destructive step)

**`storage/tools/prepare-device.sh`** — the only script permitted to write to a raw device.

**The interlocks — all mandatory:**

| # | Interlock | Enforcement |
|---|---|---|
| **P1** | Target is specified by **serial number**, never by path | Script resolves serial → current path; refuses a `/dev/...` argument |
| **P2** | The device must appear in `device-classification.yaml` with an `assigned_tier` | Refuse otherwise |
| **P3** | The device must have `signatures: []` **at run time**, re-checked with `wipefs -n` | Refuse if anything is found, even if the YAML says empty |
| **P4** | The device must not be mounted, in an LVM VG, or in an md array | `findmnt`, `pvs`, `mdadm --examine` |
| **P5** | The device must not be the boot disk | Compare against the root device's parent |
| **P6** | The device must not be the etcd disk | Compare against the Phase 12 record |
| **P7** | Operator types the **full serial number** to confirm | Typed confirmation, not `y/N` |
| **P8** | `--dry-run` is the default; `--commit` is required to act | Default-safe |
| **P9** | Every action is appended to `storage/prepared-devices.log` with timestamp, serial, operator, and tier | Audit |
| **P10** | Refuses if more than N devices are targeted in one invocation | Blast-radius cap |

```bash
# The intended usage — explicit, one device, dry-run first
./prepare-device.sh --serial S6XVNJ0T123456 --tier T0-scratch --dry-run
./prepare-device.sh --serial S6XVNJ0T123456 --tier T0-scratch --commit
#   > Device /dev/nvme1n1 (Samsung SSD 990 PRO 2TB, 2048 GB) on nexus-gpu-014
#   > Assigned tier: T0-scratch
#   > THIS WILL DESTROY ALL DATA ON THIS DEVICE.
#   > Type the full serial number to confirm: _
```

**What preparation actually does, per tier:**

| Tier | Preparation |
|---|---|
| T0 scratch | `blkdiscard` (fast, restores full performance), then create a filesystem or leave raw for LVM |
| T1 Mayastor | `blkdiscard`, leave **raw** — Mayastor claims the whole device |
| T2 Ceph OSD | `blkdiscard`, leave **raw** — Rook/ceph-volume claims it |
| T3 HDD | `wipefs -a` + partition table only |
| T4 cache | `blkdiscard` + filesystem |

> 💡 **`blkdiscard` before use is not optional on NVMe.** A drive whose flash translation layer thinks every block is written performs at its worst. Trimming the whole device restores near-fresh performance and is nearly instantaneous.

---

### Task 5 — The user-facing storage guide

**`docs/user/storage-guide.md`** — written for someone who has never thought about tiers.

```markdown
# Where should my data live?

| I have... | Use | StorageClass | Notes |
|---|---|---|---|
| Temp files, shuffle spill, an unpacked dataset for this job | T0 scratch | `nexus-scratch` | ⚠️ DELETED when your pod ends. Fast. Free. |
| A checkpoint I need if the job restarts | T2 CephFS | `nexus-fs` | Write here, or write to T0 and copy |
| A database, a hot model I serve | T1 | `nexus-fast` | Replicated. Costs quota. |
| A dataset many jobs read | T3 object + T4 cache | S3 bucket | First read is slow, then cached |
| Results, artifacts, anything I must not lose | T3 object | S3 bucket | Backed up (Phase 29) |
| My home directory / notebooks | T2 CephFS | `nexus-home` | Quota'd, snapshotted |

## The rules that will save you
1. **T0 is not storage. It is scratch.** If losing it would ruin your day, it does not belong there.
2. **Do not run training directly against T3 object storage.** Stage through T4 cache or T0.
3. **Small files kill shared filesystems.** A dataset of 4 million 20 KB images should be
   packed into WebDataset/tar shards or a Parquet file, not stored as 4 million files.
4. **Write checkpoints from ONE rank**, not all of them.
5. Ask for quota before you need it, not when a job fails at 3 a.m.
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Every block device in the fleet is classified | Count devices vs. inventory | 100 % |
| **A2** | Classification script never writes to a device | Read the script; `wipefs -n` only | Verified |
| **A3** | Every device has a serial number recorded | Query the YAML | No nulls |
| **A4** | Boot disks and etcd disks are explicitly excluded | Query | Excluded |
| **A5** | Devices with existing signatures are flagged, not assigned | Query | Flagged |
| **A6** | PLP status determined for every NVMe model | Table | Complete |
| **A7** | Wear level recorded; drives > 80 % excluded | Query | Excluded |
| **A8** | Tier design document is concrete (real models, real counts) | Read it | No placeholders |
| **A9** | Capacity model: raw → usable per tier, with replication factored | `capacity-model.md` | Arithmetic shown |
| **A10** | Failure-domain map matches physical reality | Compare to Phase 02 rack layout | Matches |
| **A11** | 📊 **B6 raw baselines captured for every device model** | `b6-device-raw.json` | Recorded |
| **A12** | 📊 Steady-state write measured (≥ 10 min), not SLC burst | Read the fio config | Confirmed |
| **A13** | 📊 **fdatasync p99 recorded per model** | B6 | Recorded |
| **A14** | `prepare-device.sh` refuses a `/dev/...` argument | Try it | Refused |
| **A15** | Refuses a device with signatures | Try on a device with a filesystem | Refused |
| **A16** | Refuses a mounted device | Try | Refused |
| **A17** | **Refuses the boot disk** | Try | Refused |
| **A18** | Requires the typed serial to proceed | Try with `y` | Refused |
| **A19** | `--dry-run` is the default | Run with no flag | No writes |
| **A20** | Prepared devices are logged with full provenance | Read the log | Complete |
| **A21** | Re-running preparation on an already-prepared device is safe | Run twice | Idempotent |
| **A22** | Storage guide is written and reviewed by a non-storage person | Ask someone | Understandable |

---

## ↩️ ROLLBACK

> ⚠️ **Device preparation is NOT reversible.** A wiped device is wiped. The rollback here is preventive, not corrective:
> - Before any `--commit`, the dry-run output must be reviewed and pasted into `evidence/phase-24/device-decisions.md`.
> - The classification YAML is committed **before** preparation, so the pre-state is in Git.
> - If a device was prepared in error: it is gone. Record it in `deviations.md`, restore from backup if the data mattered, and add the interlock that would have prevented it.

```bash
# What IS reversible: tier assignment before preparation
git revert <classification commit>
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Serial number empty for a device | Some SATA/USB bridges do not pass it through | Use WWN or `/dev/disk/by-id`; never fall back to `/dev/sdX` |
| Same serial on two devices | Counterfeit or a buggy controller | Do not use for replicated storage — replicas could land on one physical device |
| `wipefs -n` finds a signature you did not expect | Prior use, or a leftover Ceph/LVM label | **Stop.** Determine what it is before wiping. |
| Device shows lower PCIe gen than expected | Slot sharing with a GPU (Phase 01 lane budget) | Accept and record; or move the device |
| fdatasync p99 in tens of ms | Consumer drive honoring sync through NAND | Expected. Do not put etcd or Ceph WAL here. |
| Write performance collapses after ~100 GB | SLC cache exhausted | Expected. Your steady state is the real number. |
| `blkdiscard` fails | Device does not support discard, or is in use | `wipefs -a` + `dd` the first/last MB instead |
| Fewer usable devices than the capacity model assumed | Signatures, wear, or boot-disk exclusions | Revise the capacity model **before** Phase 26/27 |

---

## 🚫 DO NOT

- **Do not** target a device by `/dev/` path. Ever. Paths reorder across reboots.
- **Do not** remove or weaken any interlock P1–P10.
- **Do not** wipe a device with unknown signatures. Find out what it is first.
- **Do not** put etcd or Ceph WAL on a drive without PLP.
- **Do not** trust a 60-second write benchmark on consumer NVMe.
- **Do not** set the Ceph failure domain to `rack` when you have one rack.
- **Do not** install Mayastor, Ceph, or MinIO in this phase. Phases 26–28 own them.

---

## 📤 HANDOFF

`evidence/phase-24/handoff.md` must state:

1. **The device classification** — how many devices per tier, per node archetype.
2. **📊 B6 raw baselines per drive model** — Phases 25–28 compare their overhead against these.
3. **📊 The fdatasync table** — the single most important number for Phases 26 and 27.
4. **PLP availability** — how many PLP drives exist and where they went. If zero, the explicit acceptance of the consequences.
5. **The capacity model** — raw TB, usable TB per tier after replication/EC.
6. **The failure domain decision** (`host` vs `rack`) and what would need to change to improve it.
7. **Devices excluded and why** — signatures, wear, boot. Anything requiring a human decision.
8. **Devices prepared**, with serials, from `prepared-devices.log`.
9. **Any device that was prepared in error**, in `deviations.md`.

---

## ➡️ NEXT

**[PHASE-25 — Tier 0: Local NVMe Scratch](PHASE-25.md)** — the fastest storage in the cluster, and the easiest to get right. Start here before the distributed tiers.
