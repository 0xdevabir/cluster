# PHASE 12 — HA Control Plane & etcd

| | |
|---|---|
| **Stage** | 2 — Kubernetes Substrate |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 11 (G2 pass) |
| **Blocks** | 13, 14, 15 — everything |
| **Risk** | 🔴 High — `talosctl bootstrap` run twice destroys the etcd cluster |
| **Blast radius** | The entire cluster's state |
| **Architecture refs** | `ARCHITECTURE.md#l4--cluster-substrate`, `#l41-control-plane`, R-09, R-11 |

---

## 🎯 MISSION

Bootstrap a **highly available Kubernetes control plane** on three nodes, with etcd on dedicated power-loss-protected NVMe, an API VIP that survives a node failure, encryption at rest for Secrets, and a full audit log — and prove etcd meets its latency contract before anything is built on top of it.

> ⚠️ **DANGER — `talosctl bootstrap` is a once-per-cluster command.** It initializes the etcd cluster. Running it on a second node, or a second time on the same node, creates a competing etcd cluster and destroys the first. **Run it exactly once, on exactly one node.**

> 💡 **WHY etcd's disk gets this much attention:** etcd commits every write to disk with `fsync` before acknowledging it. On a consumer SSD without power-loss protection, `fsync` costs 5–20 ms instead of 0.2 ms (`ULTIMATE-PLAN.md §4.7`). That latency multiplies through Raft, and the symptom is not "etcd is slow" — it is leader elections, API timeouts, controller thrash, and pods stuck in Pending. **R-09 is the highest-probability self-inflicted outage in this project.**

---

## ✅ PREFLIGHT

```bash
# G2 passed
grep -q "G2 PASS" evidence/phase-11/g2-zero-touch.md && echo OK

# Exactly 3 (or 5) control nodes, provisioned and running
yq -r 'select(.spec.archetype=="control") | .metadata.name' inventory/nodes/*.yaml
for ip in $CTRL_IPS; do talosctl --nodes "$ip" get machinestatus; done   # all "running"

# ⚠️ Each control node has a DEDICATED PLP NVMe mounted at /var/lib/etcd
for ip in $CTRL_IPS; do talosctl --nodes "$ip" get mountstatus | grep etcd; done

# Control nodes are in ≥ 3 distinct racks (R-11)
yq -r 'select(.spec.archetype=="control") | .spec.location.rack' inventory/nodes/*.yaml | sort -u | wc -l

# Time is synchronized across all three (etcd Raft depends on it)
for ip in $CTRL_IPS; do talosctl --nodes "$ip" time; done

# The API VIP is reserved and NOT in use
ping -c2 10.200.0.10 && echo "❌ VIP already in use — STOP" || echo "OK: VIP free"
```

**If any control node lacks a PLP NVMe for etcd, this phase is BLOCKED.** That is finding F-02/R-04 from Phase 05. Do not proceed on a consumer SSD "just for now" — it becomes permanent and the cluster will be unstable in ways that are very hard to diagnose later.

---

## 📦 DELIVERABLES

```
talos/patches/
  archetype-control.yaml         # extended: etcd tuning, audit, encryption, VIP
  audit-policy.yaml
  encryption-config.yaml         # 🔒 contains a key — SOPS-encrypted
tools/cluster/
  bootstrap.sh                   # ⚠️ guarded: refuses to run twice
  etcd-health.sh
  etcd-benchmark.sh
  etcd-backup.sh
  cluster-preflight.sh
docs/operations/
  etcd-runbook.md                # backup, restore, defrag, member replacement
  control-plane-recovery.md      # the "we lost quorum" procedure
clusters/nexus-prod/README.md
evidence/phase-12/
  etcd-baseline.md               # 📊 the latency contract, measured
  acceptance.md handoff.md deviations.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Source |
|---|---|---|
| Kubernetes | `v1.34.1` | Talos-managed |
| etcd | `3.5.17` | Talos-managed (matches the K8s release) |
| kube-vip | `0.8.7` | `ghcr.io/kube-vip/kube-vip` — **or** Talos's built-in VIP |
| etcdctl / etcdutl | `3.5.17` | Extracted from the etcd image for backup operations |

> 💡 **VIP choice:** Talos has a built-in `machine.network.interfaces[].vip` that uses etcd for leader election. It is simpler than kube-vip and has no extra component. **Use it.** kube-vip is documented here only as the fallback if you need BGP-advertised VIPs before Cilium exists.

---

## 📋 TASKS

### Task 1 — Extend the control-plane patch

**`talos/patches/archetype-control.yaml`** — the complete version.

```yaml
machine:
  install:
    image: factory.talos.dev/installer/<BASE-SCHEMATIC-ID>:v1.9.5
    disk: /dev/disk/by-id/<boot-disk-id>

  # ── etcd on a DEDICATED PLP NVMe. Non-negotiable (R-09). ──
  disks:
    - device: /dev/disk/by-id/<plp-nvme-id>
      partitions:
        - mountpoint: /var/lib/etcd

  network:
    interfaces:
      - deviceSelector: { hardwareAddr: "<data-nic-mac>" }
        mtu: 9000
        addresses: [ "10.200.0.11/26" ]
        routes: [ { network: "0.0.0.0/0", gateway: "10.200.0.1" } ]
        # ── The API VIP: Talos elects a holder via etcd ──
        vip:
          ip: 10.200.0.10

  nodeLabels:
    nexus.io/archetype: control
    node-role.kubernetes.io/control-plane: ""
    topology.kubernetes.io/zone: r01        # rack = failure domain

  files:
    - path: /etc/kubernetes/audit-policy.yaml
      permissions: 0o600
      op: create
      content: |
        <see Task 3>
    - path: /etc/kubernetes/encryption-config.yaml
      permissions: 0o600
      op: create
      content: |
        <see Task 4 — 🔒 SOPS-encrypted in the patch file>

cluster:
  allowSchedulingOnControlPlanes: false      # ⚠️ keep user workloads off

  etcd:
    advertisedSubnets: [ "10.200.0.0/20" ]   # cluster VLAN, never management
    extraArgs:
      quota-backend-bytes: "8589934592"      # 8 GiB — alarm before the 2 GiB default bites
      auto-compaction-mode: periodic
      auto-compaction-retention: "5m"
      max-request-bytes: "10485760"          # 10 MiB — some CRDs are large
      heartbeat-interval: "100"              # ms; LAN-appropriate (default 100)
      election-timeout: "1000"               # ms; must be ≥ 10× heartbeat
      snapshot-count: "10000"
      max-snapshots: "5"
      max-wals: "5"
      metrics: extensive                     # per-method histograms for Phase 45
      experimental-initial-corrupt-check: "true"
      experimental-corrupt-check-time: "12h"

  apiServer:
    certSANs: [ "api.nexus.internal", "10.200.0.10", "127.0.0.1" ]
    extraArgs:
      # ── Audit (Phase 04 / P-paths) ──
      audit-policy-file: /etc/kubernetes/audit-policy.yaml
      audit-log-path: /var/log/audit/kube-apiserver.log
      audit-log-maxage: "400"
      audit-log-maxbackup: "30"
      audit-log-maxsize: "500"
      # ── Encryption at rest (P11) ──
      encryption-provider-config: /etc/kubernetes/encryption-config.yaml
      encryption-provider-config-automatic-reload: "true"
      # ── Availability / performance ──
      max-requests-inflight: "800"
      max-mutating-requests-inflight: "400"
      default-not-ready-toleration-seconds: "30"
      default-unreachable-toleration-seconds: "30"
      # ── Feature gates ──
      feature-gates: "DynamicResourceAllocation=true"
      runtime-config: "resource.k8s.io/v1beta1=true"
      # TODO(phase-17): oidc-issuer-url, oidc-client-id, oidc-groups-claim
    extraVolumes:
      - { hostPath: /var/log/audit, mountPath: /var/log/audit, readonly: false }

  controllerManager:
    extraArgs:
      node-monitor-period: "5s"
      node-monitor-grace-period: "40s"
      # ⚠️ Deliberately generous: a brief control-plane hiccup must not evict
      #    a 16-node training gang (ARCHITECTURE.md#0.2 design invariant)
      large-cluster-size-threshold: "50"
      node-cidr-mask-size: "24"              # one /24 per node (Phase 03 ip-plan)

  scheduler:
    extraArgs:
      # Raise later in Phase 31 when scheduler-plugins are added
      percentage-of-nodes-to-score: "50"

  extraManifests: []                          # ⚠️ empty — Argo CD owns manifests (Phase 15)
```

> ⚠️ **`extraManifests: []`.** Talos can inject manifests at bootstrap. Do not use it. Every manifest belongs to Argo CD (Law II); Talos-injected manifests are invisible to GitOps and drift silently.

---

### Task 2 — The guarded bootstrap

**`tools/cluster/bootstrap.sh`** — the guard is the point.

```bash
#!/usr/bin/env bash
# Bootstrap the etcd cluster. RUNS EXACTLY ONCE, ON EXACTLY ONE NODE.
source "$(dirname "$0")/../lib/common.sh"
NODE="${1:?first control-plane node name}"
MARKER="$ROOT/evidence/phase-12/BOOTSTRAPPED"

# ── GUARD 1: local marker ──
[[ -f "$MARKER" ]] && die "Cluster was already bootstrapped on $(cat "$MARKER"). REFUSING."

IP=$(yq -r '.spec.clusterIp' "$ROOT/inventory/nodes/$NODE.yaml")

# ── GUARD 2: ask the node whether etcd already exists ──
if talosctl --nodes "$IP" get members 2>/dev/null | grep -q .; then
  die "etcd members already exist on $NODE. REFUSING to bootstrap."
fi

# ── GUARD 3: confirm etcd is on the PLP device, not the OS disk ──
talosctl --nodes "$IP" get mountstatus | grep -q '/var/lib/etcd' \
  || die "/var/lib/etcd is not a separate mount on $NODE. REFUSING (R-09)."

# ── GUARD 4: explicit human confirmation ──
cat <<EOF
═══════════════════════════════════════════════════════════════
  ⚠️  CLUSTER BOOTSTRAP — ONE-TIME, IRREVERSIBLE
  Node: $NODE ($IP)
  This initializes etcd. Running it twice DESTROYS cluster state.
═══════════════════════════════════════════════════════════════
EOF
read -rp "Type BOOTSTRAP to proceed: " c
[[ "$c" == "BOOTSTRAP" ]] || die "aborted"

talosctl --nodes "$IP" bootstrap
printf '%s node=%s ip=%s operator=%s\n' "$(date -Is)" "$NODE" "$IP" "${USER:-unknown}" > "$MARKER"
ok "bootstrap issued; watching etcd come up"

# ── Wait for etcd and the API ──
timeout 300 bash -c "until talosctl --nodes $IP service etcd | grep -q 'STATE.*Running'; do sleep 5; done"
talosctl --nodes "$IP" kubeconfig "$ROOT/.secrets/kubeconfig" --force
timeout 300 bash -c 'until kubectl get --raw /healthz | grep -q ok; do sleep 5; done'
ok "control plane is up"
```

**Then join the remaining control nodes:**
```bash
# They join automatically once their config is applied — no bootstrap needed.
for n in nx-m-r02-01 nx-m-r03-01; do bash tools/talos/apply-config.sh "$n"; done
talosctl --nodes "$FIRST_IP" get members     # expect 3 members
kubectl get nodes                            # 3 nodes, NotReady (no CNI — expected)
```

> 💡 **`NotReady` is correct here.** Without a CNI, kubelet reports `NetworkPluginNotReady`. Phase 13 fixes it. If you see `Ready` at this point, something installed a CNI you did not intend — check that `cni: none` is really in the config.

---

### Task 3 — Audit policy

**`talos/patches/audit-policy.yaml`** — high signal, bounded volume.

```yaml
apiVersion: audit.k8s.io/v1
kind: Policy
omitStages: [ "RequestReceived" ]
rules:
  # ── Never log these: high volume, no security value ──
  - level: None
    users: ["system:kube-proxy"]
    verbs: ["watch"]
  - level: None
    userGroups: ["system:nodes"]
    verbs: ["get","list","watch"]
  - level: None
    nonResourceURLs: ["/healthz*","/readyz*","/livez*","/version","/metrics"]
  - level: None
    resources: [{ group: "", resources: ["events"] }]

  # ── ⚠️ ALWAYS log at full fidelity: the security-critical surface ──
  - level: RequestResponse
    resources:
      - { group: "", resources: ["pods/exec","pods/attach","pods/portforward"] }
  - level: RequestResponse
    resources:
      - { group: "rbac.authorization.k8s.io", resources: ["*"] }
  - level: Metadata          # Metadata only — never log secret VALUES
    resources:
      - { group: "", resources: ["secrets","configmaps","serviceaccounts/token"] }

  # ── Everything else that changes state ──
  - level: RequestResponse
    verbs: ["create","update","patch","delete","deletecollection"]
  - level: Metadata
```

> ⚠️ **`level: Metadata` for secrets, never `RequestResponse`.** Logging secrets at RequestResponse writes their plaintext contents into the audit log, which is then shipped to Loki. That converts your log store into a secret store with weaker access controls.

---

### Task 4 — Encryption at rest

```bash
# Generate a 32-byte key
head -c 32 /dev/urandom | base64
```

**`encryption-config.yaml`** (🔒 the patch containing it is SOPS-encrypted):
```yaml
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources: [ secrets, configmaps ]
    providers:
      - aescbc:
          keys:
            - name: key-2026-08          # date-stamped for rotation clarity
              secret: <BASE64-32-BYTE-KEY>
      - identity: {}                     # ⚠️ MUST be last: allows reading pre-existing
                                         #    unencrypted resources during migration
```

**After applying, re-write existing resources so they are encrypted:**
```bash
kubectl get secrets --all-namespaces -o json | kubectl replace -f -
kubectl get configmaps --all-namespaces -o json | kubectl replace -f -

# Verify: read the raw etcd value — it must start with k8s:enc:aescbc
talosctl --nodes "$IP" etcd read /registry/secrets/default/test-secret | head -c 40
```

> 💡 **Key rotation** (document in `etcd-runbook.md`): add the new key **second** in the list, restart apiservers, rewrite all resources, then promote the new key to first and remove the old. Never remove a key before every resource has been rewritten.

---

### Task 5 — 📊 The etcd latency contract

**This is the most important measurement in the phase.** `tools/cluster/etcd-benchmark.sh`:

```bash
#!/usr/bin/env bash
# Establish the etcd performance baseline. Run on a QUIET cluster.
source "$(dirname "$0")/../lib/common.sh"
NODE="${1:?control node ip}"

# ── 1. Raw disk fsync latency — the physical floor ──
# Anything above ~2 ms p99 here means the disk lacks PLP (R-04/R-09).
talosctl --nodes "$NODE" read /proc/mounts | grep etcd
echo "Run fio against the etcd device from a privileged debug pod:"
cat <<'EOF'
fio --name=etcd-fsync --filename=/var/lib/etcd/fio-test \
    --rw=write --bs=2300 --size=22m --fdatasync=1 \
    --ioengine=sync --iodepth=1 --numjobs=1 --runtime=60 --time_based
# Report: sync.lat_ns percentiles.  p99 MUST be < 2 ms.  Remove the test file after.
EOF

# ── 2. etcd's own committed metrics ──
for m in etcd_disk_wal_fsync_duration_seconds etcd_disk_backend_commit_duration_seconds; do
  echo "── $m ──"
  kubectl -n kube-system exec etcd-"$NODE" -- \
    etcdctl --endpoints=https://127.0.0.1:2379 endpoint status -w table
done

# ── 3. Throughput ──
etcdctl check perf --load=s     # small: 50 clients / 100 conns
# Expect PASS with "Slowest request took" well under 1 s
```

**The contract — record actuals in `evidence/phase-12/etcd-baseline.md`:**

| Metric | Target | Alert | Measured | Verdict |
|---|---|---|---|---|
| Disk `fdatasync` p99 (fio) | < 2 ms | > 5 ms | | |
| `etcd_disk_wal_fsync_duration_seconds` p99 | < 10 ms | > 25 ms | | |
| `etcd_disk_backend_commit_duration_seconds` p99 | < 25 ms | > 50 ms | | |
| `etcd_network_peer_round_trip_time_seconds` p99 | < 2 ms | > 10 ms | | |
| `etcd_server_leader_changes_seen_total` | 0/hour | any | | |
| `etcdctl check perf --load=s` | PASS | FAIL | | |
| DB size | < 1 GB at bootstrap | > 6 GB | | |

> ⚠️ **If `fdatasync` p99 exceeds 5 ms, stop and fix the disk.** Everything you build on this cluster inherits that latency. It is the difference between a cluster that feels instant and one that feels broken, and no amount of tuning above etcd compensates for it.

---

### Task 6 — Backup

**`tools/cluster/etcd-backup.sh`** — runs from the seed node, out-of-band.

```bash
#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
DEST="${1:-$ROOT/.backups}"; mkdir -p "$DEST"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
SNAP="$DEST/etcd-$STAMP.db"

talosctl --nodes "$ETCD_NODE" etcd snapshot "$SNAP"

# ⚠️ VERIFY the snapshot — an unverified backup is not a backup
etcdutl snapshot status "$SNAP" -w table
sha256sum "$SNAP" > "$SNAP.sha256"

# Also back up what the snapshot does NOT contain:
sops --decrypt "$ROOT/talos/secrets/secrets.enc.yaml" > /dev/null || die "secrets unreadable"
cp "$ROOT/talos/secrets/secrets.enc.yaml" "$DEST/secrets-$STAMP.enc.yaml"

ok "snapshot $SNAP verified"
# TODO(phase-29): ship to object storage with retention + immutability
```

**Schedule:** hourly on the seed node via systemd timer until Phase 29 moves it into the cluster with Velero. Retain 24 hourly, 7 daily, 4 weekly.

**Document the restore in `etcd-runbook.md`** and **test it** (A13):
```
RESTORE (total cluster loss)
 1. Provision three control nodes via Phase 08/09 — do NOT bootstrap.
 2. Copy the snapshot to the first node.
 3. talosctl --nodes <ip> bootstrap --recover-from=/var/lib/etcd-snapshot.db
 4. Apply configs to nodes 2 and 3; they join the recovered cluster.
 5. Verify: kubectl get nodes; kubectl get all -A
 6. Argo CD reconciles everything else from Git (Phase 15).
```

---

### Task 7 — Health checks & recovery runbook

**`tools/cluster/etcd-health.sh`** — the command you run when something feels wrong:
```bash
talosctl --nodes "$IPS" service etcd
talosctl --nodes "$IPS" get members
etcdctl endpoint health --cluster -w table
etcdctl endpoint status --cluster -w table     # shows leader, DB size, raft index
etcdctl alarm list                             # NOSPACE/CORRUPT alarms
kubectl get --raw /healthz?verbose
kubectl get --raw /metrics | grep -E 'etcd_disk_wal_fsync|etcd_server_leader_changes'
```

**`docs/operations/control-plane-recovery.md`** — the scenarios:

| Scenario | Quorum | Procedure |
|---|---|---|
| 1 of 3 members down | ✅ Retained | Fix or replace the node. Cluster fully operational. |
| 2 of 3 down | ❌ Lost | API is read-only/unavailable. **Running pods keep running.** Restore the members; if unrecoverable, `--force-new-cluster` on the survivor, then re-add. |
| 3 of 3 down | ❌ | Restore from snapshot (Task 6). |
| Member data corrupted | ✅ | Remove the member, wipe `/var/lib/etcd`, re-add. It resyncs from the leader. |
| `NOSPACE` alarm | ✅ | `etcdctl defrag --cluster`, then `etcdctl alarm disarm`. Raise `quota-backend-bytes` if recurring. |
| Split brain after a partition | — | Raft prevents it. If you somehow have two clusters, **restore from backup** — do not merge. |

⚠️ **Emphasize in the runbook: losing the control plane does not kill running workloads.** kubelet is autonomous (`ARCHITECTURE.md#0.2`). Panic-rebooting nodes during a control-plane incident turns a recoverable problem into an outage.

---

### Task 8 — Weekly defrag

```yaml
# CronJob — deployed via Argo CD in Phase 15; defined here
apiVersion: batch/v1
kind: CronJob
metadata: { name: etcd-defrag, namespace: kube-system }
spec:
  schedule: "0 3 * * 0"                # Sunday 03:00
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          # ⚠️ Defrag one member at a time. Defragging all simultaneously
          #    blocks writes on every member and stalls the API.
          containers:
            - name: defrag
              image: registry.k8s.io/etcd:3.5.17-0
              command: ["/bin/sh","-c"]
              args:
                - |
                  for ep in $ETCD_ENDPOINTS; do
                    echo "defragmenting $ep"
                    etcdctl --endpoints="$ep" defrag && sleep 60
                  done
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Exactly 3 (or 5) etcd members, all healthy | `etcdctl endpoint health --cluster` | All healthy |
| **A2** | Control nodes are in ≥ 3 distinct racks | Query inventory | True |
| **A3** | etcd is on a dedicated PLP mount on every member | `talosctl get mountstatus` | `/var/lib/etcd` separate on all |
| **A4** | **Disk `fdatasync` p99 < 2 ms** | fio (Task 5) | Recorded, within target |
| **A5** | `etcd_disk_wal_fsync_duration_seconds` p99 < 10 ms | Metrics | Recorded |
| **A6** | `etcdctl check perf` passes | Run it | PASS |
| **A7** | Zero leader changes over a 1-hour idle period | `etcd_server_leader_changes_seen_total` | 0 |
| **A8** | API VIP answers and is held by exactly one node | `curl -k https://10.200.0.10:6443/healthz`; `talosctl get addresses` | ok; one holder |
| **A9** | **VIP fails over when the holder is powered off** | `power-ctl.sh off <holder>`; time the recovery | API back within 30 s |
| **A10** | Bootstrap script refuses a second run | Run it again | Refuses at GUARD 1 |
| **A11** | Secrets are encrypted at rest in etcd | Read the raw etcd value | Starts with `k8s:enc:aescbc` |
| **A12** | Audit log records `pods/exec` at RequestResponse and secrets at Metadata only | `kubectl exec` into a pod; grep the audit log | Correct levels; **no secret values present** |
| **A13** | **Snapshot restore tested end-to-end** | Restore to a scratch cluster or VMs | Cluster comes back with its resources |
| **A14** | Snapshot verification is part of the backup script | Read it; run it | `etcdutl snapshot status` runs |
| **A15** | Hourly backup schedule is active | `systemctl list-timers` | Enabled |
| **A16** | Control-plane recovery runbook covers all six scenarios | Read it | Complete |
| **A17** | Runbook states that running pods survive a control-plane outage | Grep it | Present and prominent |
| **A18** | `allowSchedulingOnControlPlanes: false` is in effect | `kubectl describe node <ctrl> \| grep Taints` | `NoSchedule` taint present |
| **A19** | Nodes are `NotReady` with `NetworkPluginNotReady` | `kubectl get nodes` | Expected pre-CNI state |
| **A20** | `extraManifests` is empty | Grep the patch | Empty |

---

## ↩️ ROLLBACK

```bash
# Reset a single misbehaving member (it will rejoin and resync)
talosctl --nodes <ip> reset --graceful --system-labels-to-wipe EPHEMERAL

# ⚠️ FULL cluster reset — DESTROYS ALL STATE
talosctl --nodes <all-ctrl-ips> reset --graceful=false --reboot
rm evidence/phase-12/BOOTSTRAPPED   # only then can bootstrap.sh run again
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Nodes `NotReady`, `NetworkPluginNotReady` | No CNI installed | **Expected.** Phase 13. |
| etcd will not start after bootstrap | Time skew, or the data directory is not writable | `talosctl time`; `talosctl logs etcd`; verify the mount |
| Leader elections every few minutes | Disk too slow, or peer network latency | Check `wal_fsync` p99 and `peer_round_trip_time`. Almost always the disk (R-09). |
| `etcdserver: mvcc: database space exceeded` | Quota reached, no compaction | `etcdctl defrag --cluster`; `alarm disarm`; verify `auto-compaction-retention` |
| API VIP does not move on failure | VIP requires a healthy etcd quorum to elect a holder | If etcd lost quorum, the VIP will not move — that is by design. Fix etcd. |
| Two nodes claim the VIP | Split-brain in the VIP election, usually a network partition | Check `talosctl get addresses` on both; resolve the partition |
| Very high API latency with low load | `max-requests-inflight` too low, or etcd slow | Check `apiserver_request_duration_seconds` by verb; correlate with etcd |
| Audit log fills the disk | Policy too verbose | Tighten the `level: None` rules; verify `audit-log-maxsize`/`maxbackup` |
| Secrets still unencrypted after config change | Existing resources are not rewritten automatically | `kubectl get secrets -A -o json \| kubectl replace -f -` |
| `kubectl` works via a node IP but not the VIP | VIP not in `certSANs` | Add it and re-apply the config |

---

## 🚫 DO NOT

- **Do not** run `talosctl bootstrap` more than once, or on more than one node.
- **Do not** put etcd on a consumer SSD, even temporarily.
- **Do not** allow user workloads on control-plane nodes.
- **Do not** use `extraManifests`. Argo CD owns manifests (Phase 15).
- **Do not** install a CNI here. Phase 13.
- **Do not** log secrets at `RequestResponse` in the audit policy.
- **Do not** remove the `identity: {}` provider from the encryption config before every resource has been rewritten.
- **Do not** defragment all etcd members simultaneously.
- **Do not** reboot worker nodes during a control-plane incident. Running pods are fine.
- **Do not** configure OIDC yet — Keycloak does not exist until Phase 17.

---

## 📤 HANDOFF

`evidence/phase-12/handoff.md` must state:

1. **The API endpoint** (`https://api.nexus.internal:6443`) and where the kubeconfig lives.
2. **etcd member list, endpoints, and the measured baseline** — Phase 45 builds alerts against these numbers.
3. **Measured VIP failover time** — feeds gate G3 in Phase 17.
4. **Backup location, schedule, and the restore test result** — Phase 29 takes this over.
5. **Encryption key name and rotation procedure** — the key itself stays in SOPS.
6. **Audit log path and retention** — Phase 45 ships it to Loki.
7. **Feature gates enabled** (`DynamicResourceAllocation`) — Phase 19 depends on it.
8. **That nodes are NotReady pending CNI** — expected, and Phase 13's starting condition.

---

## ➡️ NEXT

**[PHASE-13 — Cilium Datapath](PHASE-13.md)** — install the eBPF datapath with kube-proxy replacement, native routing, and BGP, and make the nodes Ready.
