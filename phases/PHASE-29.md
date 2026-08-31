# PHASE 29 — Backup, Disaster Recovery & Storage Gate (G7)

| | |
|---|---|
| **Stage** | 4 — Storage Fabric |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 26, 27, 28 |
| **Blocks** | 30 (Stage 5 entry), 54 |
| **Risk** | 🟠 Medium-High — an untested backup is not a backup |
| **Blast radius** | Recoverability of the entire platform |
| **Architecture refs** | `ARCHITECTURE.md#x2-failure-domains--blast-radius`, `ULTIMATE-PLAN.md#13-gates` (G7), Law V |

---

## 🎯 MISSION

Make the platform **recoverable**. Enumerate every piece of state whose loss would be unrecoverable, back it up on a schedule, **prove each backup restores**, and document a disaster-recovery procedure someone can execute at 3 a.m. Then pass **gate G7** and close Stage 4.

> 💡 **WHY this is a full phase, not a checkbox.** The number of clusters with a backup job that has never been restored is close to all of them. A backup you have not restored is a hypothesis. This phase converts hypotheses into facts by restoring every class of state at least once, and by making restoration a scheduled, automated verification — not an annual fire drill.

> ⚠️ **The state that actually kills you is not the obvious state.** Everyone backs up user data. Almost nobody backs up: Mayastor's etcd, JuiceFS's metadata engine, Ceph's monitor store, the Talos machine secrets, the age/SOPS private key, the OpenBao unseal shares, or Keycloak's realm database. Losing any one of those makes healthy on-disk data unusable or the cluster unrebuildable.

---

## ✅ PREFLIGHT

```bash
# All storage tiers operational
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status
kubectl get diskpools -n openebs
s5cmd ls s3://nexus-datasets/

# Backup target exists and has capacity
s5cmd ls s3://nexus-backups/

# ⚠️ Is there an OFF-CLUSTER target? If not, that is a finding, not a detail.
cat storage/design/backup-targets.yaml
```

---

## 📦 DELIVERABLES

```
backup/
  state-inventory.md                # 🎯 EVERY piece of critical state
  velero-values.yaml
  schedules/                        # Velero Schedule CRs per class
  etcd-backup-cronjob.yaml          # cluster etcd (Phase 12 extended)
  mayastor-etcd-backup.yaml
  juicefs-meta-backup.yaml
  ceph-mon-backup.yaml
  keycloak-db-backup.yaml
  offsite-sync.yaml                 # replication off-cluster
tools/backup/
  verify-restore.sh                 # 🧪 automated restore verification
  dr-drill.sh                       # full rebuild rehearsal
  backup-report.sh
docs/operations/
  disaster-recovery.md              # 🎯 the 3 a.m. document
  restore-runbooks/                 # one per state class
gates/G7-storage.md
evidence/phase-29/{preflight,acceptance,handoff,deviations,gate-g7,restore-proofs/}.md
```

---

## 🔧 VERSION PINNING

| Component | Version |
|---|---|
| Velero | `v1.15.2` |
| Velero CSI plugin | `v0.7.1` |
| Velero AWS plugin (S3) | `v1.11.1` |
| Kopia (Velero's uploader) | bundled |

---

## 📋 TASKS

### Task 1 — 🎯 The state inventory (do this first, exhaustively)

Every row must have an owner, an RPO, an RTO, and a proven restore.

| # | State | Where | Loss means | RPO | RTO | Method |
|---|---|---|---|---|---|---|
| **S1** | **Cluster etcd** | Control plane | Cluster gone (workloads survive briefly) | 15 min | 1 h | `etcdctl snapshot` (Phase 12) |
| **S2** | **Talos machine secrets** | `secrets.yaml`, SOPS | **Cannot re-provision or join nodes.** | On change | — | Git (SOPS) + offline copy |
| **S3** | **SOPS/age private key** | Offline | **Every encrypted secret is unreadable.** | On change | — | ⚠️ 3 copies, 2 offline (Phase 10) |
| **S4** | **OpenBao unseal shares** | Distributed | Vault cannot unseal | On change | — | ⚠️ Physical custody |
| **S5** | **Mayastor etcd** | T1 metadata | **T1 volumes unmountable** — data on disk, unusable | 1 h | 2 h | etcd snapshot |
| **S6** | **Ceph mon store** | Ceph | Cluster map lost; recoverable but painful | 6 h | 4 h | mon store export |
| **S7** | **JuiceFS metadata (Postgres)** | T1 | **Objects intact but the filesystem namespace is gone** | 1 h | 2 h | CNPG backup to S3 |
| **S8** | **Keycloak realm DB** | T1 Postgres | All identity and RBAC bindings lost | 6 h | 2 h | CNPG backup |
| S9 | Persistent volumes (T1/T2) | Mayastor/Ceph | Application data | 24 h | 4 h | Velero + CSI snapshots |
| S10 | Kubernetes resources | etcd | Redeployable from Git — but PVC bindings are not | 1 h | 1 h | Velero |
| S11 | Object storage (datasets) | T3 | Datasets — often re-downloadable, sometimes not | 24 h | days | Bucket replication / versioning |
| S12 | Artifacts and checkpoints | T3 | User work | 24 h | 4 h | Versioning + replication |
| S13 | Container registry | Phase 42 | Rebuildable, but slowly | 24 h | 4 h | Registry replication |
| S14 | Prometheus/Mimir/Loki data | Observability | Historical signal | 24 h | best-effort | Object-store backed |
| S15 | Git repository | Gitea mirror + origin | **The entire platform definition** | On push | 1 h | Multiple remotes |
| S16 | Experiment tracking DB | Phase 44 | Research history | 6 h | 2 h | CNPG backup |
| S17 | PKI: step-ca root + intermediate | Phase 07 | Must re-issue every certificate | On change | — | Offline |
| S18 | PDU/switch configs | Network | Reconstructible from Git | On change | 2 h | Git + config export |

> ⚠️ **S2, S3, S4, and S17 are not backed up by any tool in this phase.** They are *offline, physically-held* secrets. Their protection is custody, not automation. Verify the Phase 10 custody rules are actually followed — 3 copies, 2 offline, none on a laptop or a synced password manager — and record who holds what. **A cluster with perfect Velero coverage and one lost age key is unrecoverable.**

> 💡 **S5 and S7 are the ones that produce the worst incident**, because everything *looks* fine: the disks are healthy, the objects are there, and nothing can read them. Test their restore explicitly (Task 4).

---

### Task 2 — Backup targets and the 3-2-1 question

```
3 copies · 2 different media/systems · 1 off-site
```

| Target | What | Honest assessment |
|---|---|---|
| **On-cluster S3 (`nexus-backups`)** | Everything | ⚠️ **Not a backup for a cluster-wide event.** A Ceph catastrophe takes the backups with it. |
| **Off-cluster NAS / second Ceph** | Everything critical | ✅ Different failure domain |
| **Cloud object storage** | S1–S8, S15, S17 (small, critical) | ✅ True off-site; costs little for metadata-sized state |
| **Offline / air-gapped** | S2, S3, S4, S17 | ✅ Required for the key material |

> 🚫 **Do not accept "backed up to the cluster's own object store" as a backup strategy.** It protects against accidental deletion and single-component failure. It does not protect against the event you actually fear. If no off-cluster target exists yet, that is a **G7 finding with an owner and a date**, not something to gloss over.

**Encryption:** backups contain secrets. Encrypt at rest with a key that is *not* stored in the cluster being backed up. Velero supports repository encryption via Kopia; record where that passphrase lives (and back it up per S3's rules).

---

### Task 3 — Velero and the schedules

```yaml
# Critical platform state — frequent, long retention
schedule: "0 * * * *"                    # hourly
template:
  includedNamespaces: [openebs, rook-ceph, keycloak, openbao, storage]
  snapshotVolumes: true
  defaultVolumesToFsBackup: false        # prefer CSI snapshots
  ttl: 720h                              # 30 days
---
# Tenant workloads — daily
schedule: "0 2 * * *"
template:
  includedNamespaces: ["tenant-*"]
  ttl: 168h                              # 7 days
---
# Full cluster resource manifest — daily, no volumes
schedule: "0 3 * * *"
template:
  includeClusterResources: true
  snapshotVolumes: false
  ttl: 2160h                             # 90 days
```

**What Velero does and does not cover:**

| | Covered |
|---|---|
| Kubernetes resources | ✅ |
| PV data via CSI snapshots | ✅ (where the driver supports it) |
| PV data via filesystem backup | ✅ (slower; use for drivers without snapshots) |
| **Object storage buckets** | ❌ — use bucket replication |
| **etcd itself** | ❌ — separate job |
| **Mayastor/JuiceFS/Ceph internal metadata** | ❌ — separate jobs |
| **Anything outside Kubernetes** | ❌ |

> ⚠️ **Velero's coverage gap is the whole reason for the state inventory.** Deploying Velero and stopping there leaves S1, S5, S6, S7, S11, S15, S17 unprotected — which is most of the state that matters.

---

### Task 4 — 🧪 Restore verification (the part that makes it real)

**`tools/backup/verify-restore.sh`** runs on a schedule and proves restorability without touching production.

| Class | Verification | Frequency |
|---|---|---|
| **S1 cluster etcd** | Restore the snapshot into a throwaway etcd, `etcdctl endpoint status`, count keys | Weekly |
| **S5 Mayastor etcd** | Restore into a scratch etcd, verify the volume records parse | Weekly |
| **S7 JuiceFS metadata** | Restore Postgres to a temp instance, mount JuiceFS read-only against it, `ls` a known path | Weekly |
| **S8 Keycloak DB** | Restore to temp, start Keycloak, verify the realm and a known user | Monthly |
| **S9 PVs** | Restore a PVC into a scratch namespace, checksum a known file | Weekly (rotating) |
| **S10 K8s resources** | Restore into a scratch namespace, diff against the source | Weekly |
| **S6 Ceph mon** | Verify the export is parseable (full restore is a DR drill) | Monthly |
| **S15 Git** | Clone from each remote, verify HEAD matches | Daily |

**The verification must check content, not exit codes.**
```
❌ "velero restore describe → Completed"        ← proves nothing about the data
✅ Restore a PVC, read a file, compare its SHA-256 against a value recorded at backup time
```

> 💡 **Plant canaries.** Write a known file with a known checksum into each backed-up volume and each database at a known key. Verification then has something unambiguous to check, and a partial or silently-corrupted restore is caught instead of passing.

**`evidence/phase-29/restore-proofs/`** holds the output of every restore performed in this phase — one file per state class, with the checksum comparison. This directory *is* the G7 evidence.

---

### Task 5 — The disaster-recovery document

**`docs/operations/disaster-recovery.md`** — written to be executed under stress, by someone who did not build the cluster.

Structure it by scenario, worst first:

| # | Scenario | Recovery path | Est. RTO |
|---|---|---|---|
| **D1** | Total loss — building gone, hardware gone | Rebuild from Git + offline keys + off-site backups. **This is the scenario that validates S2/S3/S17.** | Days |
| **D2** | All control-plane nodes lost, workers intact | Restore etcd from snapshot onto new control-plane nodes; workers rejoin | 2–4 h |
| **D3** | Ceph cluster unrecoverable | Rebuild Ceph, restore PVs from Velero, re-import datasets from off-site | 1–2 days |
| **D4** | Mayastor etcd lost | Restore Mayastor etcd; volumes reattach | 2 h |
| **D5** | JuiceFS metadata lost | Restore Postgres; namespace returns; objects were never lost | 2 h |
| **D6** | Accidental deletion of a namespace | Velero restore | 30 min |
| **D7** | Ransomware / malicious deletion | Restore from immutable/versioned backups; **this is why object lock exists** | 4–8 h |
| **D8** | A single node lost | Nothing — Phase 23 handles it | Automatic |

**Each entry needs**, in this order: **Detection** (how you know) → **Decide** (is this really D3?) → **Communicate** (who to tell) → **Execute** (numbered, copy-pasteable commands) → **Verify** (how you know it worked) → **Post-mortem**.

> ⚠️ **Write down the dependency order for D1/D2.** You cannot restore Kubernetes resources before etcd; you cannot decrypt secrets before you have the age key; you cannot provision nodes before Talos secrets. A restore executed in the wrong order wastes hours. Put the ordered list at the top of the document.

**The bootstrap chain for D1 — the most important paragraph in the phase:**
```
1. Offline: age private key (S3), Talos secrets (S2), step-ca root (S17), Bao shares (S4)
2. Git repository (S15) from an external remote
3. Seed node (Phase 06) → network services (Phase 07) → provisioning (Phase 08)
4. Talos nodes (Phase 09) → control plane (Phase 12) → restore etcd (S1) OR rebuild from GitOps
5. Cilium (13) → Argo CD (15) → the app-of-apps reconciles the rest
6. Storage (24–28) → restore PVs (S9) and metadata stores (S5, S6, S7)
7. Restore object data (S11, S12) from off-site
```
This is precisely the phase order of this project — which is the point. **A GitOps-defined cluster rebuilds by re-running its own phases.**

---

### Task 6 — 🚪 GATE G7 — Storage

**`gates/G7-storage.md`**

| # | Check | Evidence | Pass |
|---|---|---|---|
| G7.1 | All five tiers operational | Tier health checks | ☐ |
| G7.2 | 📊 Full tier performance table complete (B6–B8) | Baselines | ☐ |
| G7.3 | 📊 T0/T4 path achieves > 90 % GPU utilization end to end | Phase 28 test | ☐ |
| G7.4 | 🧪 Ceph survives OSD, node, and (if applicable) rack loss | Phase 27 C1–C4 | ☐ |
| G7.5 | 🧪 Mayastor survives node loss without I/O interruption | Phase 26 F1/F9 | ☐ |
| G7.6 | CRUSH replicas provably span failure domains | `crush-verify.sh` | ☐ |
| G7.7 | Recovery impact on client I/O quantified and tuned | Phase 27 C10 | ☐ |
| G7.8 | **State inventory complete — every class has an owner and RPO/RTO** | `state-inventory.md` | ☐ |
| G7.9 | Backups running on schedule for every class | `backup-report.sh` | ☐ |
| G7.10 | 🧪 **Every state class has a documented successful restore** | `restore-proofs/` | ☐ |
| G7.11 | 🧪 Restore verification checks content, not exit codes | Read the script | ☐ |
| G7.12 | Off-cluster backup target exists (or a dated finding with an owner) | Evidence | ☐ |
| G7.13 | Offline key custody verified (S2, S3, S4, S17) | Physical check | ☐ |
| G7.14 | Backups are encrypted with a key not stored in this cluster | Verify | ☐ |
| G7.15 | Object versioning and lifecycle policies active | Verify | ☐ |
| G7.16 | DR document covers D1–D8 with executable steps | Read it | ☐ |
| G7.17 | 🧪 **A DR drill has been executed at least once** | Drill record | ☐ |
| G7.18 | Storage alerts complete and firing correctly | Test | ☐ |
| G7.19 | Capacity headroom ≥ 30 % on every tier | Metrics | ☐ |
| G7.20 | User-facing storage docs published and accurate | Review | ☐ |

> 🧪 **G7.17 — the drill.** Pick D2 or D4 (recoverable, bounded) and actually execute it on the real cluster during a maintenance window, timed. Not a tabletop. Record the actual RTO against the target; if it is 4× the estimate, that is the most valuable finding in Stage 4.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | State inventory lists all 18 classes with owner/RPO/RTO | Read it | Complete |
| **A2** | Velero installed, backup location available | `velero backup-location get` | Available |
| **A3** | All schedules created and firing | `velero schedule get` | Firing |
| **A4** | Non-Velero backup jobs (etcd ×2, Ceph mon, JuiceFS, Keycloak) running | CronJob status | Running |
| **A5** | Backups land in the off-cluster target | Check the remote | Present |
| **A6** | Backups are encrypted at rest | Inspect | Encrypted |
| **A7** | The encryption key is NOT stored only in this cluster | Verify custody | Confirmed |
| **A8** | 🧪 **S1 cluster etcd restore verified** | Restore proof | Verified |
| **A9** | 🧪 **S5 Mayastor etcd restore verified** | Restore proof | Verified |
| **A10** | 🧪 **S7 JuiceFS metadata restore verified — namespace intact** | Restore proof | Verified |
| **A11** | 🧪 S8 Keycloak DB restore verified | Restore proof | Verified |
| **A12** | 🧪 S9 PV restore verified with a checksum match | Restore proof | Match |
| **A13** | 🧪 S10 resource restore verified | Restore proof | Verified |
| **A14** | Canary files planted and checked by verification | Inspect | Present |
| **A15** | Verification runs on a schedule, not manually | CronJob | Scheduled |
| **A16** | A failed verification alerts | Break one deliberately | Alerts |
| **A17** | Offline key custody physically verified | Check | Verified |
| **A18** | DR document has the ordered bootstrap chain | Read | Present |
| **A19** | 🧪 **A DR drill executed and timed** | Drill record | Executed |
| **A20** | Actual RTO recorded against target for the drilled scenario | Record | Recorded |
| **A21** | 🚪 **Gate G7 passes with all evidence** | `gates/G7-storage.md` | All ☑ |

---

## ↩️ ROLLBACK

```bash
# Pause schedules without losing existing backups
velero schedule pause --all

# Remove Velero (backups in object storage remain)
helm uninstall velero -n velero
```

> 💡 **Never delete backup data as part of a rollback.** Backups outlive the tool that made them. If you replace Velero with something else, keep the old repository until the new one has proven restores.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Velero backup `PartiallyFailed` | Some PVs lack snapshot support, or hooks failed | Check per-item errors; enable fs-backup for those volumes |
| CSI snapshots not created | VolumeSnapshotClass missing or not default | Create it (Phase 26/27 deliverable) |
| Restore succeeds but the app does not start | PVC bound to a PV with stale data, or a missing secret | Restore order; check the dependency chain |
| Restore verification passes but real restores fail | Verification checked exit codes, not content | **This is A12.** Fix the verification. |
| etcd restore produces an empty cluster | Restored the wrong snapshot, or the member list is wrong | Follow the Phase 12 recovery runbook exactly |
| Mayastor volumes still unmountable after etcd restore | Restored to the wrong etcd, or io-engines need a restart | Restart the control plane; check volume records |
| JuiceFS mounts but the namespace is empty | Restored the wrong Postgres database, or the wrong point in time | Verify the DB contents before mounting |
| Backups consume unbounded space | TTL not set or lifecycle not applied | Set TTL; apply the bucket lifecycle |
| Off-site sync lagging | Bandwidth, or too much data | Back up state, not datasets; replicate datasets separately |
| Cannot decrypt a backup | Key lost | **This is the unrecoverable case.** It is why S3 has three copies. |

---

## 🚫 DO NOT

- **Do not** treat an unrestored backup as a backup.
- **Do not** store backups only inside the cluster they protect.
- **Do not** store the backup encryption key in the cluster being backed up.
- **Do not** skip S5, S6, S7 because "Ceph/Mayastor is self-healing." Self-healing does not cover metadata loss.
- **Do not** verify restores by exit code.
- **Do not** pass G7 without at least one real, timed DR drill.
- **Do not** delete backup data during a rollback.
- **Do not** proceed to Stage 5 with a G7 check unmet and unrecorded.

---

## 📤 HANDOFF

`evidence/phase-29/handoff.md` must state:

1. **🚪 The G7 gate result** with links to all evidence, including `restore-proofs/`.
2. **The complete state inventory** with the owner, RPO, RTO, and *proven* restore for each class.
3. **🧪 The DR drill record** — scenario, actual RTO vs. target, and what went wrong. What went wrong is the valuable part.
4. **Where the off-cluster and offline copies live**, and who holds the offline key material.
5. **Known unprotected state**, if any, with an owner and a date to fix it.
6. **The bootstrap chain for D1**, validated against the actual phase order.
7. **Backup storage consumption and growth rate** — feeds Phase 56's capacity planning.
8. **Stage 4 declaration** — five tiers operational, benchmarked, failure-tested, backed up, and restorable. Stage 5 (scheduling) may begin.

---

## ➡️ NEXT

**[PHASE-30 — Kueue: Quotas, Cohorts & Fair Share](PHASE-30.md)** — begin Stage 5. The cluster has resources; now decide who gets them and in what order.
