# PHASE 26 — Tier 1: Mayastor Replicated NVMe

| | |
|---|---|
| **Stage** | 4 — Storage Fabric |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 24, 25 |
| **Blocks** | 27, 29, 44 |
| **Risk** | 🟠 Medium-High — distributed state; hugepages requirement affects node config |
| **Blast radius** | All T1 volumes; node hugepage allocation |
| **Architecture refs** | `ARCHITECTURE.md#l6-storage-fabric` (T1), `#l62-storage-decision-matrix`, ADR-014 |

---

## 🎯 MISSION

Deliver **replicated block storage at near-local-NVMe latency** using Mayastor (OpenEBS) over NVMe-oF. T1 is where a database, a hot model repository, or a metadata store lives: it must survive a node loss without the 1–3 ms latency floor of Ceph.

> 💡 **WHY a tier between local NVMe and Ceph.** Ceph RBD is excellent, durable, and flexible — and its write path costs ~1–3 ms because a write is acknowledged only after the primary OSD journals it and replicates it. For a Postgres backing an experiment tracker, or a model registry serving weights, that is the difference between snappy and sluggish. Mayastor's SPDK-based, userspace, poll-mode datapath over NVMe-oF delivers replicated writes in ~200 µs. You pay for it in **dedicated CPU cores and hugepages**, which is the honest trade.

> ⚠️ **The cost is real and must be budgeted.** Mayastor's I/O engine pins cores and uses poll-mode drivers — those cores are **100 % busy by design** and unavailable to workloads. Combined with the hugepage reservation, a Mayastor node gives up meaningful capacity. Deploy it on storage nodes and a subset of GPU nodes, not everywhere.

---

## ✅ PREFLIGHT

```bash
# Devices assigned to T1, prepared and raw
yq '.nodes[].devices[] | select(.assigned_tier == "T1-mayastor")' \
   storage/design/device-classification.yaml

# Kernel prerequisites
talosctl -n <node> read /proc/cmdline | grep -o 'hugepagesz=[^ ]*'
talosctl -n <node> read /sys/module/nvme_core/parameters/multipath

# RDMA fabric healthy (NVMe-oF can use RDMA)
bash tools/net/rdma-verify.sh

# Free cores available for the I/O engine on candidate nodes
bash tools/topology/numa-report.sh
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/storage/tier1/
  mayastor-values.yaml              # Helm values
  diskpools.yaml                    # one DiskPool per device, by serial
  storageclass-fast.yaml            # nexus-fast (replica 3)
  storageclass-fast-r2.yaml         # nexus-fast-r2 (replica 2, cheaper)
  snapshotclass.yaml
talos/patches/
  mayastor-node.yaml                # hugepages, kernel modules, core isolation
tools/storage/
  bench-tier1.sh                    # 📊 B6-T1
  mayastor-health.sh
  replica-failure-test.sh           # 🧪 kill a replica, measure
docs/operations/mayastor-runbook.md
observability/rules/tier1-alerts.yaml
evidence/phase-26/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Note |
|---|---|---|
| OpenEBS Mayastor | `2.7.3` | Chart `openebs/openebs` with `engines.replicated.mayastor.enabled=true` |
| SPDK | bundled | Do not attempt to substitute |
| etcd (Mayastor's own) | bundled `3.5.x` | ⚠️ **Separate from cluster etcd** — see Task 4 |

---

## 📋 TASKS

### Task 1 — Node prerequisites (Talos)

```yaml
# talos/patches/mayastor-node.yaml
machine:
  sysctls:
    vm.nr_hugepages: "2048"          # 2048 × 2 MiB = 4 GiB — MINIMUM per I/O engine
  kernel:
    modules:
      - name: nvme_tcp               # NVMe over TCP transport
      - name: nvme_rdma              # NVMe over RDMA (preferred if RoCE is healthy)
      - name: nbd
  nodeLabels:
    openebs.io/engine: mayastor
    nexus.io/storage.tier1: "true"
```

**The resource budget per Mayastor node — write this down before deploying:**

| Resource | Reservation | Note |
|---|---|---|
| Hugepages | 2 GiB minimum, 4 GiB recommended | Not available to workloads |
| CPU cores for `io-engine` | 2 (dedicated, isolated) | **Will show 100 % utilization always** |
| CPU for control plane agents | ~0.5 | |
| Memory | ~2 GiB | Beyond hugepages |

> ⚠️ **The I/O engine cores must be isolated.** Use the `isolcpus` set from Phase 20 or a dedicated cpuset. If the Linux scheduler moves other work onto a poll-mode core, both the I/O engine and that workload suffer badly. Also ensure Phase 20's CPU Manager `static` policy does not hand those cores to a Guaranteed pod — reserve them via `kubeReserved`/`systemReserved` or a reserved cpuset.

> ⚠️ **100 % CPU on the io-engine core is NORMAL.** Add an explicit note to the monitoring dashboards and the on-call runbook, or someone will page at 3 a.m. about a "runaway process." Alert on the io-engine being *absent*, not on it being busy.

**Choose the transport:**

| Transport | Latency | Requires | Verdict |
|---|---|---|---|
| **NVMe-oF/RDMA** | ~150–200 µs | Working RoCE (Phase 21) | ✅ Use if G4 passed |
| NVMe-oF/TCP | ~300–500 µs | Nothing extra | Fallback; still good |

---

### Task 2 — DiskPools, declared by serial

```yaml
apiVersion: openebs.io/v1beta2
kind: DiskPool
metadata:
  name: pool-stor001-nvme0
  namespace: openebs
spec:
  node: nexus-stor-001
  disks:
    # ⚠️ by-id ONLY (Phase 24 rule P1)
    - /dev/disk/by-id/nvme-INTEL_SSDPE2KX010T8_PHLJ123456
```

> 🚫 **Mayastor takes the whole device.** There is no sharing with a filesystem, another pool, or Ceph. A device in a DiskPool is gone from every other use until the pool is destroyed.

**Pool sizing rule:** one DiskPool per physical device, never one pool spanning devices. A pool that spans two devices fails when either fails, doubling your failure rate with no benefit — Mayastor already replicates across pools.

---

### Task 3 — StorageClasses and replication policy

```yaml
# storageclass-fast.yaml — the durable default
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nexus-fast
  annotations:
    nexus.io/durability: "Replica 3 across failure domains. Survives 2 node losses."
    nexus.io/latency: "~200 µs write, ~120 µs read"
    nexus.io/backup: "Snapshot + Velero (Phase 29)"
parameters:
  repl: "3"
  protocol: "nvmf"
  ioTimeout: "60"
  thin: "true"                        # thin provisioning — watch the pool fill
  stsAffinityGroup: "false"
  # Spread replicas across the Phase 24 failure domain
  nodeAffinityTopologyLabel: "topology.kubernetes.io/rack"
provisioner: io.openebs.csi-mayastor
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
reclaimPolicy: Delete
```

**Replica count decision:**

| Replicas | Survives | Usable capacity | Write amplification | Use for |
|---|---|---|---|---|
| 1 | Nothing | 100 % | 1× | ❌ Never — use T0 instead |
| **2** | 1 node loss | 50 % | 2× | Rebuildable caches, dev workloads |
| **3** | 2 node losses | 33 % | 3× | ✅ **Default for anything that matters** |

> ⚠️ **`thin: true` needs a pool-fill alert.** Thin provisioning lets you over-commit; a full pool means volumes go read-only and applications crash. Alert at 75 % and page at 85 % of pool capacity, and never let total thin allocation exceed 150 % of raw.

> 💡 **`reclaimPolicy: Delete` on `nexus-fast` is deliberate** but pairs with the Phase 15 guardrail `Prune=false` on stateful resources: Argo must never delete a PVC. A human deletes PVCs; GitOps does not.

---

### Task 4 — ⚠️ Mayastor's own etcd

Mayastor stores volume metadata in its **own etcd**, separate from the Kubernetes control plane.

| Fact | Consequence |
|---|---|
| Losing Mayastor's etcd loses volume→replica mapping | **Your data is on disk but unmountable** |
| It defaults to a PVC-backed StatefulSet | ⚠️ Which StorageClass? Not Mayastor's — that is circular |
| It must be on durable, low-fsync-latency media | Same requirement as cluster etcd (Phase 12) |

**The correct configuration:**
```yaml
etcd:
  persistence:
    storageClass: "<a non-Mayastor class>"   # local-path on PLP media, or Ceph later
  replicaCount: 3
  # Spread across control-plane or storage nodes with PLP devices
```

> 🚫 **Do not put Mayastor's etcd on a Mayastor volume.** It cannot bootstrap. Do not put it on `nexus-scratch` — a node reboot destroys your storage metadata. Use PLP-backed local-path on three separate nodes, and **back it up in Phase 29**.

**Add to the backup plan (Phase 29):** Mayastor etcd snapshot, hourly, verified by restore.

---

### Task 5 — 📊 Benchmark T1 (B6-T1)

Compare against Phase 24's raw device numbers and Phase 25's T0 numbers.

| Test | Raw device (B6) | T0 local | **T1 replica-3** | Expected T1 |
|---|---|---|---|---|
| Rand read 4K IOPS | 950k | 900k | | 400–600k |
| Rand write 4K IOPS | 180k | 165k | | 60–100k (3× amplification) |
| Seq read 1M | 6.8 GB/s | 6.5 GB/s | | 3–5 GB/s (network-bound) |
| Seq write 1M | 2.1 GB/s | 2.0 GB/s | | 0.6–1.0 GB/s |
| **Write latency p50** | 20 µs | 25 µs | | **150–250 µs** |
| **Write latency p99** | 200 µs | 250 µs | | **< 1 ms** |
| Read latency p99 | 150 µs | 180 µs | | < 400 µs |

📊 **The comparison that justifies the tier:** run the same profile against a Ceph RBD volume in Phase 27 and put all three in one table. If T1 is not meaningfully faster than T2 on write latency, T1 is not worth its CPU cost — say so and reconsider.

📊 **Also measure the replica-3 vs replica-2 delta** — it tells users what durability costs them.

---

### Task 6 — 🧪 Failure and recovery testing

This is the point of a replicated tier. Test it now, not during an incident.

| # | Test | Method | Expected |
|---|---|---|---|
| **F1** | Kill one replica's node | Power off via PDU | I/O continues uninterrupted; volume degraded |
| **F2** | Measure I/O pause during failover | fio running throughout, watch for stalls | < 5 s stall, no errors |
| **F3** | Rebuild after the node returns | Bring the node back | Auto-rebuild starts; measure duration |
| **F4** | Rebuild impact on foreground I/O | Run fio during rebuild | Quantify the degradation |
| **F5** | Kill two replicas (replica-3) | Two nodes down | Volume still available |
| **F6** | Kill all replicas | Three nodes down | Volume unavailable; recovers when nodes return |
| **F7** | Kill the nexus (the volume's target node) | Power off | Target moves; measure the pause |
| **F8** | Mayastor etcd loss | Delete one etcd pod | No impact; quorum holds |
| **F9** | Mayastor control-plane restart | Restart the agent | **Running I/O must not stop** |
| **F10** | Fill a pool to 100 % (thin) | Write until full | Graceful: volumes go RO, alert fires, no corruption |

> ⚠️ **F9 is the invariant that matters most** and mirrors the architecture's core rule: *a control-plane outage must never terminate a running job*. Mayastor's data path is in the io-engine; the control plane only orchestrates. Verify that killing every control-plane pod leaves running I/O untouched. If it does not, that is a blocking finding.

**`tools/storage/replica-failure-test.sh`** automates F1–F4 and produces the numbers for the runbook.

---

### Task 7 — Observability

| Metric | Alert | Threshold |
|---|---|---|
| DiskPool usage | `MayastorPoolFilling` / `MayastorPoolCritical` | 75 % / 85 % |
| Thin over-commit ratio | `MayastorOvercommitted` | > 150 % |
| Volume replica count < desired | `MayastorVolumeDegraded` | Any, for 5 min |
| Rebuild in progress | Info | — |
| Rebuild stuck | `MayastorRebuildStalled` | No progress in 30 min |
| io-engine pod absent | `MayastorIOEngineDown` | Any |
| Volume I/O latency p99 | `MayastorLatencyHigh` | > 2 ms for 5 min |
| Mayastor etcd unhealthy | `MayastorEtcdDegraded` | Any |
| Hugepages unavailable | `MayastorHugepagesInsufficient` | Any |

> 💡 **Do not alert on io-engine CPU.** It is 100 % by design (Task 1). Alert on its absence.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Hugepages allocated on every Mayastor node | `/proc/meminfo` | ≥ 2 GiB |
| **A2** | `nvme_tcp`/`nvme_rdma` modules loaded | `lsmod` | Loaded |
| **A3** | io-engine cores isolated from the scheduler and CPU Manager | Phase 20 validator | Isolated |
| **A4** | All io-engine pods Running | `kubectl get pods -n openebs` | Running |
| **A5** | DiskPools Online, one per device, referenced by-id | `kubectl get diskpools` | All Online |
| **A6** | A PVC on `nexus-fast` provisions and mounts | Test | Bound |
| **A7** | Replicas land in **different failure domains** | Inspect volume topology | Distinct racks/hosts |
| **A8** | 📊 **B6-T1 recorded and compared to raw and T0** | `bench-tier1.sh` | Table complete |
| **A9** | 📊 Write latency p99 < 1 ms | B6-T1 | Met |
| **A10** | 📊 Replica-3 vs replica-2 cost measured | Both classes | Quantified |
| **A11** | 🧪 **F1: node loss does not interrupt I/O** | `replica-failure-test.sh` | No errors |
| **A12** | 🧪 F2: failover stall < 5 s | Measured | Met |
| **A13** | 🧪 F3/F4: rebuild completes; impact quantified | Measured | Documented |
| **A14** | 🧪 **F9: control-plane restart does not stop running I/O** | Test | Uninterrupted |
| **A15** | 🧪 F10: full pool degrades gracefully, no corruption | Test | Graceful |
| **A16** | Mayastor etcd is NOT on a Mayastor volume | Inspect | Confirmed |
| **A17** | Mayastor etcd is on PLP-backed media | Inspect | Confirmed |
| **A18** | Volume expansion works online | Expand a PVC | Expanded |
| **A19** | Snapshot and restore work | Test | Restored, data intact |
| **A20** | All nine alerts fire correctly | Induce each | Fire |
| **A21** | Pool-fill alerts fire before exhaustion | Fill a test pool | Fires at 75 % |
| **A22** | Argo does not prune PVCs | Delete a PVC from Git | PVC survives |
| **A23** | Runbook covers every failure mode tested | Read it | Complete |

---

## ↩️ ROLLBACK

```bash
# Stop new provisioning; existing volumes keep serving
kubectl patch sc nexus-fast -p '{"metadata":{"annotations":{"nexus.io/deprecated":"true"}}}'
kubectl delete sc nexus-fast          # existing PVs unaffected

# Full removal — ⚠️ MIGRATE DATA FIRST. Deleting DiskPools destroys the replicas.
# 1. Migrate every T1 volume to T2 (Phase 27) or restore from backup
# 2. Verify no PVCs reference io.openebs.csi-mayastor
# 3. helm uninstall; then delete DiskPools
# 4. Revert the Talos hugepage patch and reclaim the cores
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| io-engine pod CrashLoopBackOff | Hugepages not available | Verify `vm.nr_hugepages`; may need a reboot |
| DiskPool stuck in `Pending` | Device path wrong, or the device has signatures | Verify by-id path; re-run Phase 24 preparation |
| PVC Pending forever | No pool with enough space, or the topology constraint cannot be satisfied | Check pool capacity; relax or fix the topology label |
| Latency far above target | Fell back to TCP instead of RDMA; or io-engine cores not isolated | Check the transport; check the cpuset |
| Volume degraded, no rebuild | Insufficient pools in other failure domains | Add a pool, or reduce replica count |
| Rebuild very slow | Competing with foreground I/O or the fabric | Throttle rebuild; schedule during quiet hours |
| io-engine at 100 % CPU | **Normal.** Poll-mode driver. | Document it. Do not "fix" it. |
| Volumes unmountable after an outage | Mayastor etcd lost | Restore the etcd backup (Phase 29). This is why it is backed up. |
| Pool fills unexpectedly | Thin over-commit | Enforce the 150 % cap; alert earlier |
| Node reboot leaves volumes offline | nvme modules not loaded at boot | Add to the Talos module list, not a runtime `modprobe` |

---

## 🚫 DO NOT

- **Do not** put Mayastor's etcd on a Mayastor volume or on scratch.
- **Do not** create a DiskPool spanning multiple physical devices.
- **Do not** run replica-1 volumes. Use T0 if you do not need durability.
- **Do not** alert on io-engine CPU utilization.
- **Do not** deploy Mayastor on every node — budget the CPU and hugepage cost deliberately.
- **Do not** let thin provisioning exceed 150 % of raw without an explicit decision.
- **Do not** delete a DiskPool without confirming no volume has a replica on it.
- **Do not** build Ceph here. Phase 27.

---

## 📤 HANDOFF

`evidence/phase-26/handoff.md` must state:

1. **📊 B6-T1 results** alongside raw and T0 — the three-way table.
2. **📊 Whether T1 justifies its cost** — the latency advantage over what Phase 27 will measure for Ceph, versus the CPU and hugepage reservation. If the answer is "marginally," say so.
3. **The per-node resource cost** of Mayastor (cores, hugepages, memory) so Phase 30's quota model can subtract it.
4. **🧪 Failure test results F1–F10**, especially the F9 control-plane-independence result.
5. **Rebuild duration and its impact on foreground I/O** — needed for the Phase 53 maintenance planning.
6. **Total T1 usable capacity** at replica 3 and replica 2.
7. **Where Mayastor's etcd lives** and its backup plan (feeds Phase 29).
8. **The transport in use** (RDMA or TCP) and why.

---

## ➡️ NEXT

**[PHASE-27 — Tier 2: Rook-Ceph Shared Filesystem & Block](PHASE-27.md)** — the workhorse tier: CephFS for shared datasets and home directories, RBD for general block.
