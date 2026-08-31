# PHASE 09 — Talos Linux OS Layer

| | |
|---|---|
| **Stage** | 1 — Bootstrap Infrastructure |
| **Estimated effort** | 5–6 hours |
| **Depends on** | 08 |
| **Blocks** | 11, 12, 18, 20, 21, 26 |
| **Risk** | 🔴 High — machine config errors brick nodes; `wipe` directives destroy data |
| **Blast radius** | Every node that receives the config |
| **Architecture refs** | `ARCHITECTURE.md#l3--provisioning--os-layer`, `#l32-why-talos-linux`, `#l33-machine-config-structure`, `#l42-kubelet-configuration-contract` |

---

## 🎯 MISSION

Build the **complete Talos machine-configuration hierarchy**: Image Factory schematics carrying the NVIDIA extensions, a layered patch system (base → archetype → node), the kernel and sysctl tuning every compute node inherits, and the disk layout that Phases 25–27 depend on.

> 💡 **WHY the layered patch structure matters more than any single setting:** at 100 nodes you will change a sysctl once and expect it everywhere. If configs are per-node copies, that change is 100 edits and 100 chances to diverge. With `_base.yaml` → `archetype-*.yaml` → `node-overrides/*.yaml`, it is one edit, one review, one rollout. This structure is the thing that makes Law IV (immutable infrastructure) practical rather than aspirational.

---

## ✅ PREFLIGHT

```bash
bash tools/seed-preflight.sh
# At least one node is in Talos maintenance mode from Phase 08
talosctl --nodes 10.100.0.105 --insecure version
# Expect: version + "maintenance mode"

# Inventory has, for every node: boot disk (with serial), NIC names, VLAN plan
yq -r '.spec.storage[] | select(.role=="boot") | [.device,.serial] | @csv' inventory/nodes/*.yaml
yq -r '.spec.nics[] | [.name,.role] | @csv' inventory/nodes/*.yaml

# Network design and PKI are available
test -f inventory/network/ip-plan.yaml
curl -fsS -k https://ca.nexus.internal:9000/health
```

---

## 📦 DELIVERABLES

```
talos/
  secrets/secrets.enc.yaml            # 🔒 SOPS-encrypted cluster secrets
  schematics/
    base.yaml                         # Image Factory schematic: no GPU
    gpu.yaml                          # + nvidia-container-toolkit + driver
    storage.yaml                      # + iscsi-tools, util-linux-tools, nvme
    schematic-ids.md                  # the resulting IDs — Phase 08 needs these
  patches/
    _base.yaml
    archetype-control.yaml
    archetype-compute-gpu.yaml
    archetype-compute-cpu.yaml
    archetype-storage.yaml
    archetype-infra.yaml
    net-vlans.yaml
    net-sriov.yaml                    # VF count only; Phase 21 does the rest
    kernel-perf.yaml
    disk-layout.yaml
    node-overrides/<node>.yaml        # ONLY per-node facts
  Taskfile.yaml                       # talos:* tasks
  rendered/                           # CI output, .gitignored
tools/talos/
  render-configs.sh
  apply-config.sh
  verify-node.sh
docs/operations/
  talos-debugging.md                  # ⚠️ REQUIRED — there is no SSH
  talos-upgrade.md
evidence/phase-09/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Notes |
|---|---|---|
| Talos Linux | `v1.9.5` | Must match `talosctl` from Phase 00 |
| Kubernetes | `v1.34.1` | DRA is GA at 1.34 (ADR-007) |
| NVIDIA driver (production branch) | `550.127.08` | Via the Image Factory `nvidia-*-production` extensions |
| `nvidia-container-toolkit` extension | matching Talos release | Same schematic |
| `nonfree-kmod-nvidia-production` | matching | Same schematic |

> ⚠️ **The NVIDIA driver version is pinned by the Talos release**, not chosen independently. Upgrading Talos upgrades the driver. Phase 53 handles this with a canary pool; here, just record the pair.

---

## 📋 TASKS

### Task 1 — Image Factory schematics

Talos ships as a minimal image; hardware support comes from **system extensions** baked in at image build time, identified by a **schematic ID**.

**`talos/schematics/gpu.yaml`**
```yaml
customization:
  systemExtensions:
    officialExtensions:
      - siderolabs/nonfree-kmod-nvidia-production   # the kernel module
      - siderolabs/nvidia-container-toolkit-production
      - siderolabs/nvidia-fabricmanager             # only if NVSwitch/NVLink present
      - siderolabs/mei                              # Intel ME (vPro boards)
  extraKernelArgs:
    - net.ifnames=0                                 # predictable NIC naming, see note
```

**`talos/schematics/storage.yaml`**
```yaml
customization:
  systemExtensions:
    officialExtensions:
      - siderolabs/iscsi-tools        # required by Mayastor / some CSI drivers
      - siderolabs/util-linux-tools   # nsenter, lsblk helpers used by Rook
      - siderolabs/nvme-cli           # ⚠️ verify availability in the Talos release
```

**Generate the schematic IDs:**
```bash
for s in base gpu storage; do
  id=$(curl -sSX POST --data-binary @talos/schematics/$s.yaml \
        https://factory.talos.dev/schematics | jq -r .id)
  echo "$s: $id"
done | tee talos/schematics/schematic-ids.md
```

Each ID yields deterministic URLs:
```
Installer:  factory.talos.dev/installer/<ID>:v1.9.5
Disk image: factory.talos.dev/image/<ID>/v1.9.5/metal-amd64.raw.zst
Kernel:     pxe.factory.talos.dev/image/<ID>/v1.9.5/kernel-amd64
Initramfs:  pxe.factory.talos.dev/image/<ID>/v1.9.5/initramfs-amd64.xz
```

> 💡 **Mirror these locally.** If the environment is air-gapped or the factory is unreachable during a recovery, provisioning stops. Download each image to `bootstrap/services/boot-artifacts/talos/<ID>/`, record checksums, and point Phase 08's workflow at the local copy.

> ⚠️ **`net.ifnames=0`:** this reverts to `eth0`-style naming. It makes configs portable across otherwise-identical hardware but breaks if a machine has NICs on different buses. **Decide once per archetype.** If your inventory shows stable predictable names (`enp2s0f0np0`), prefer keeping them and selecting interfaces by *hardware address* in the machine config instead — that is the most robust option and is what the examples below use.

---

### Task 2 — Cluster secrets

```bash
talosctl gen secrets -o talos/secrets/secrets.yaml

# 🔒 Encrypt IMMEDIATELY — this file contains the cluster CA private keys
sops --encrypt --age "$AGE_PUBLIC_KEY" \
  talos/secrets/secrets.yaml > talos/secrets/secrets.enc.yaml
shred -u talos/secrets/secrets.yaml
```

**What is in this file:** the Kubernetes CA, etcd CA, front-proxy CA, the Talos machine CA, the bootstrap token, and the cluster ID. **Losing it means you cannot add nodes or issue new client certificates — you would have to rebuild the cluster.** Its custody is the age-key custody procedure from Phase 04/A12.

---

### Task 3 — The base patch

**`talos/patches/_base.yaml`** — applies to every node without exception.

```yaml
machine:
  # ── Time: everything downstream depends on it (Phase 07) ──
  time:
    disabled: false
    servers: [ "10.100.0.10" ]
    bootTimeout: 2m

  # ── Name resolution ──
  network:
    nameservers: [ "10.100.0.10" ]
    extraHostEntries:
      - ip: 10.200.0.10
        aliases: [ api.nexus.internal ]

  # ── Trust our internal CA ──
  files:
    - path: /etc/ssl/certs/nexus-root-ca.pem
      permissions: 0o644
      op: create
      content: |
        -----BEGIN CERTIFICATE-----
        <NEXUS Root CA — the PUBLIC certificate, safe to commit>
        -----END CERTIFICATE-----

  # ── Registry mirrors: survive an internet outage (Law IX) ──
  registries:
    mirrors:
      docker.io:      { endpoints: [ "https://harbor.nexus.internal/v2/proxy-docker" ] }
      ghcr.io:        { endpoints: [ "https://harbor.nexus.internal/v2/proxy-ghcr" ] }
      registry.k8s.io:{ endpoints: [ "https://harbor.nexus.internal/v2/proxy-k8s" ] }
      quay.io:        { endpoints: [ "https://harbor.nexus.internal/v2/proxy-quay" ] }
    # TODO(phase-42): Harbor does not exist yet. Until then these mirrors fall
    # through to upstream. Do NOT remove — Phase 42 activates them.

  # ── Disk encryption: protects data on a removed/decommissioned disk (P9) ──
  systemDiskEncryption:
    state:     { provider: luks2, keys: [ { nodeID: {}, slot: 0 } ] }
    ephemeral: { provider: luks2, keys: [ { nodeID: {}, slot: 0 } ] }

  # ── kubelet: the contract from ARCHITECTURE.md#L4.2 ──
  kubelet:
    extraArgs:
      rotate-server-certificates: "true"
      node-status-update-frequency: "10s"
    extraConfig:
      serializeImagePulls: false
      registryPullQPS: 20
      registryBurst: 40
      maxPods: 64
      evictionHard:
        memory.available:  "1Gi"
        nodefs.available:  "10%"
        imagefs.available: "10%"
      systemReserved: { cpu: "1",  memory: "2Gi", ephemeral-storage: "10Gi" }
      kubeReserved:   { cpu: "1",  memory: "2Gi", ephemeral-storage: "10Gi" }
    nodeIP:
      validSubnets: [ "10.200.0.0/20" ]     # ⚠️ pin to the cluster VLAN, not mgmt

  # ── Baseline sysctls (per-archetype tuning adds more) ──
  sysctls:
    net.core.somaxconn: "65535"
    net.ipv4.tcp_congestion_control: "bbr"
    net.core.default_qdisc: "fq"
    vm.max_map_count: "1048576"
    fs.inotify.max_user_watches: "524288"
    fs.inotify.max_user_instances: "8192"
    kernel.pid_max: "4194304"

cluster:
  network:
    cni: { name: none }                     # ⚠️ Cilium is installed in Phase 13
    podSubnets:     [ "10.244.0.0/14" ]
    serviceSubnets: [ "10.96.0.0/16" ]
  proxy: { disabled: true }                 # ⚠️ Cilium replaces kube-proxy (ADR-004)
  discovery:
    enabled: true
    registries: { kubernetes: { disabled: false }, service: { disabled: true } }
```

> ⚠️ **`cni: none` and `proxy: disabled` are load-bearing.** Talos would otherwise install Flannel and kube-proxy, both of which conflict with the Cilium design (`ARCHITECTURE.md#L2.5`). Setting them here means Phase 13 installs Cilium into a clean cluster instead of fighting an incumbent.

> ⚠️ **`nodeIP.validSubnets`** must name the cluster VLAN. Without it, Talos may pick the management IP as the node IP, and every pod-to-pod path will traverse the 1 GbE management network. This is a silent 100× performance bug.

---

### Task 4 — Archetype patches

**`talos/patches/archetype-compute-gpu.yaml`** — the most consequential file in the phase.

```yaml
machine:
  install:
    image: factory.talos.dev/installer/<GPU-SCHEMATIC-ID>:v1.9.5
    wipe: false                # ⚠️ true wipes the disk on EVERY apply. Keep false.

  kernel:
    modules:
      - name: nvidia
      - name: nvidia_uvm
      - name: nvidia_drm
      - name: nvidia_modeset

  # ── Kernel command line: the performance contract ──
  # Each argument is justified; see ARCHITECTURE.md#L3.3 and Phase 49.
  install:
    extraKernelArgs:
      - amd_iommu=on                # or intel_iommu=on — REQUIRED for SR-IOV
      - iommu=pt                    # passthrough mode: no DMA translation cost
      - pcie_aspm=off               # ASPM downtrains PCIe links (Phase 01/A5)
      - transparent_hugepage=madvise  # 'always' stalls allocation-heavy trainers
      - hugepagesz=1G
      - default_hugepagesz=1G
      - hugepages=8                 # 8 GiB of 1 G pages; size per node in overrides
      - numa_balancing=disable      # autonuma fights the Topology Manager
      - nvidia.NVreg_EnableGpuFirmware=1
      - nvidia.NVreg_PreserveVideoMemoryAllocations=1
      # ⚠️ mitigations stay ON (ADR-027). Do not add mitigations=off here.

  sysctls:
    # Network: 100 GbE TCP fallback path
    net.core.rmem_max:              "268435456"
    net.core.wmem_max:              "268435456"
    net.core.rmem_default:          "33554432"
    net.core.wmem_default:          "33554432"
    net.ipv4.tcp_rmem:              "4096 87380 268435456"
    net.ipv4.tcp_wmem:              "4096 65536 268435456"
    net.core.netdev_max_backlog:    "250000"
    net.ipv4.tcp_mtu_probing:       "1"
    # Memory / NUMA
    kernel.numa_balancing:          "0"
    vm.zone_reclaim_mode:           "0"
    vm.swappiness:                  "0"
    vm.dirty_ratio:                 "10"
    vm.dirty_background_ratio:      "5"
    # Shared memory — NCCL, Ray plasma, PyTorch dataloader workers
    kernel.shmmax:                  "68719476736"
    kernel.shmall:                  "16777216"
    # File descriptors — a 64-rank job opens a lot
    fs.file-max:                    "2097152"

  kubelet:
    extraConfig:
      # ── Law III: topology alignment (ARCHITECTURE.md#L4.2) ──
      cpuManagerPolicy: static
      cpuManagerPolicyOptions:
        full-pcpus-only: "true"
      memoryManagerPolicy: Static
      reservedMemory:
        - numaNode: 0
          limits: { memory: "4Gi" }
      topologyManagerPolicy: single-numa-node
      topologyManagerScope: pod
      reservedSystemCPUs: "0-1"
      featureGates:
        DynamicResourceAllocation: true
        MemoryQoS: true

  # ── Persist WoL across reboots (Phase 08 depends on it) ──
  # Talos does not expose ethtool directly; use an ExtensionServiceConfig or a
  # privileged bootstrap DaemonSet. Document which you used.
  # TODO(phase-09): implement and record in deviations.md

  nodeLabels:
    nexus.io/archetype: compute-gpu
    nexus.io/quarantine: "true"      # Phase 14's readiness gate clears this
  nodeTaints:
    nexus.io/quarantine: "true:NoSchedule"
```

> ⚠️ **`install.wipe: false`.** With `wipe: true`, every `talosctl apply-config` reinstalls and destroys the node's data. It is correct for a first install (Phase 08's workflow writes the image directly, so even then it is unnecessary) and catastrophic for a config update. **Default false. Always.**

> 💡 **`reservedSystemCPUs: "0-1"` + `cpuManagerPolicy: static`**: this is what gives workloads *exclusive* physical cores. Without the reservation, kubelet and system daemons steal cycles from pinned workload cores and you get unexplained jitter in tightly-coupled collectives.

**`talos/patches/archetype-control.yaml`**
```yaml
machine:
  install:
    image: factory.talos.dev/installer/<BASE-SCHEMATIC-ID>:v1.9.5
    disk: /dev/disk/by-id/<boot-disk-id>     # by-id, never /dev/sdX
  nodeLabels:
    nexus.io/archetype: control
cluster:
  etcd:
    # ⚠️ etcd on a DEDICATED PLP NVMe (ARCHITECTURE.md#L4.1). This is the single
    # most important storage decision in the cluster.
    advertisedSubnets: [ "10.200.0.0/20" ]
    extraArgs:
      quota-backend-bytes: "8589934592"       # 8 GiB
      auto-compaction-mode: periodic
      auto-compaction-retention: "5m"
      max-request-bytes: "10485760"
      heartbeat-interval: "100"               # ms — LAN-appropriate
      election-timeout: "1000"
  apiServer:
    extraArgs:
      audit-log-path: /var/log/audit/kube-apiserver.log
      audit-log-maxage: "400"
      audit-log-maxbackup: "30"
      audit-policy-file: /etc/kubernetes/audit-policy.yaml
      encryption-provider-config: /etc/kubernetes/encryption-config.yaml
      # TODO(phase-17): oidc-* flags added when Keycloak exists
  controllerManager:
    extraArgs:
      node-monitor-period: "5s"
      node-monitor-grace-period: "40s"
  allowSchedulingOnControlPlanes: false
```

**Mount the etcd disk** via `machine.disks` so etcd lives on the PLP device:
```yaml
machine:
  disks:
    - device: /dev/disk/by-id/<plp-nvme-id>
      partitions:
        - mountpoint: /var/lib/etcd
```

**`talos/patches/archetype-storage.yaml`** — leaves OSD devices **untouched** so Rook can claim them in Phase 27:
```yaml
machine:
  install:
    image: factory.talos.dev/installer/<STORAGE-SCHEMATIC-ID>:v1.9.5
  kernel:
    modules: [ { name: iscsi_tcp }, { name: nvme_tcp }, { name: nvme_fabrics }, { name: vfio_pci } ]
  sysctls:
    vm.nr_hugepages: "2048"          # Mayastor/SPDK requires hugepages (Phase 26)
  nodeLabels:
    nexus.io/archetype: storage
  nodeTaints:
    nexus.io/archetype: "storage:NoSchedule"
# ⚠️ Do NOT add OSD disks to machine.disks. Rook needs them raw and unmounted.
```

---

### Task 5 — Network configuration

Select interfaces by **hardware address**, not by name. Names change; MACs do not.

**`talos/patches/net-vlans.yaml`**
```yaml
machine:
  network:
    interfaces:
      # Cluster network (VLAN 200) — the node IP lives here
      - deviceSelector: { hardwareAddr: "<data-nic-mac>" }
        mtu: 9000
        dhcp: false
        addresses: [ "10.200.1.5/26" ]
        routes:
          - { network: "0.0.0.0/0", gateway: "10.200.1.1" }
        vlans:
          # Storage networks (Phase 27)
          - vlanId: 300
            mtu: 9000
            addresses: [ "10.210.1.5/22" ]
          - vlanId: 301
            mtu: 9000
            addresses: [ "10.211.1.5/22" ]
          # RDMA (Phase 21 attaches SR-IOV VFs here)
          - vlanId: 400
            mtu: 9000
            addresses: [ "10.220.1.5/24" ]

      # Management network (VLAN 100) — 1500 MTU, no default route
      - deviceSelector: { hardwareAddr: "<mgmt-nic-mac>" }
        mtu: 1500
        dhcp: false
        addresses: [ "10.100.0.105/22" ]
```

> ⚠️ **Only one interface carries the default route.** Two default routes produce asymmetric routing that works for ping and fails for TCP under load.

---

### Task 6 — Rendering and applying

**`tools/talos/render-configs.sh`** — deterministic, idempotent, CI-runnable.

```bash
#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
need talosctl; need sops; need yq

SECRETS=$(mktemp); trap 'shred -u "$SECRETS"' EXIT
sops --decrypt "$ROOT/talos/secrets/secrets.enc.yaml" > "$SECRETS"

mkdir -p "$ROOT/talos/rendered"
for f in "$ROOT"/inventory/nodes/*.yaml; do
  node=$(yq -r '.metadata.name' "$f")
  arch=$(yq -r '.spec.archetype' "$f")
  [[ "$arch" == "control" ]] && type=controlplane || type=worker

  patches=( "@$ROOT/talos/patches/_base.yaml"
            "@$ROOT/talos/patches/archetype-$arch.yaml"
            "@$ROOT/talos/patches/kernel-perf.yaml" )
  [[ -f "$ROOT/talos/patches/node-overrides/$node.yaml" ]] \
    && patches+=( "@$ROOT/talos/patches/node-overrides/$node.yaml" )

  talosctl gen config nexus-prod https://api.nexus.internal:6443 \
    --with-secrets "$SECRETS" \
    --output-types "$type" \
    --output "$ROOT/talos/rendered/$node.yaml" \
    --kubernetes-version 1.34.1 \
    --force \
    $(printf -- '--config-patch %s ' "${patches[@]}")

  # 🧪 Validate BEFORE it can reach a node
  talosctl validate --config "$ROOT/talos/rendered/$node.yaml" --mode metal
  ok "rendered $node ($type)"
done
```

**`tools/talos/apply-config.sh`** — always dry-run first:
```bash
talosctl apply-config --nodes "$IP" --file "talos/rendered/$NODE.yaml" --dry-run
talosctl apply-config --nodes "$IP" --file "talos/rendered/$NODE.yaml" --insecure   # first time only
# Subsequent applies use mTLS and support --mode=auto|no-reboot|reboot|staged
```

> 💡 **Apply modes matter.** `--mode=no-reboot` applies only changes that do not require a restart. `--mode=staged` writes the config to be applied at the next reboot — the safe choice for a fleet-wide change you want to roll out with the node-drain cycle in Phase 53.

---

### Task 7 — The debugging runbook (mandatory)

**`docs/operations/talos-debugging.md`.** Talos has no SSH and no shell (`ARCHITECTURE.md#L3.2`). Without this document, the first incident is a crisis.

| I want to... | Command |
|---|---|
| See kernel messages | `talosctl dmesg --nodes <ip>` |
| Follow a service's logs | `talosctl logs --nodes <ip> --follow kubelet` |
| List services and health | `talosctl services --nodes <ip>` |
| Read the running config | `talosctl get machineconfig --nodes <ip> -o yaml` |
| List block devices | `talosctl get disks --nodes <ip>` |
| List network links / addresses / routes | `talosctl get links` / `addresses` / `routes` |
| Capture packets | `talosctl pcap --nodes <ip> --interface eth0 -o /tmp/c.pcap` |
| See resource usage | `talosctl dashboard --nodes <ip>` |
| Read an arbitrary file | `talosctl read --nodes <ip> /proc/cmdline` |
| Run a shell "on" the node | Deploy a privileged debug pod with `hostPID`/`hostNetwork` and `nsenter` |
| Get a full support bundle | `talosctl support --nodes <ip> -O bundle.zip` |
| Reboot / shut down / reset | `talosctl reboot` / `shutdown` / `reset` ⚠️ |
| Roll back to the previous config | `talosctl rollback --nodes <ip>` |
| Upgrade Talos | `talosctl upgrade --nodes <ip> --image factory.talos.dev/installer/<ID>:v1.9.6` |

**Include the three most common failure signatures and their diagnosis:** node not joining (check `talosctl logs kubelet` for CA/token errors), disk not found (check `talosctl get disks` against the config's `by-id` path), and network unreachable (check `talosctl get addresses` for the expected VLAN interfaces).

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Schematic IDs generated and recorded for all three variants | `cat talos/schematics/schematic-ids.md` | Three IDs |
| **A2** | Talos images mirrored locally with checksums | `ls boot-artifacts/talos/*/`; verify sha256 | Present, checksums match |
| **A3** | Cluster secrets encrypted; plaintext absent from disk and Git | `find . -name 'secrets.yaml'`; `gitleaks detect` | Not found; clean |
| **A4** | Every node renders a valid config | `tools/talos/render-configs.sh` | All `talosctl validate` pass |
| **A5** | Rendering is deterministic | Run twice, diff the output | Identical |
| **A6** | `cni: none` and `proxy: disabled` in every rendered config | `grep` the rendered files | Present in all |
| **A7** | `nodeIP.validSubnets` is the cluster VLAN, not management | `grep validSubnets` | `10.200.0.0/20` |
| **A8** | `install.wipe: false` in every archetype patch | `grep -r 'wipe:' talos/patches/` | All false |
| **A9** | Boot and etcd disks referenced by `by-id`, never `/dev/sdX` | `grep -r 'device:' talos/patches/` | All `by-id` |
| **A10** | A node accepts the config and boots to Ready-pending-CNI | Apply to one node; `talosctl get machinestatus` | `running`, healthy |
| **A11** | Kernel args applied | `talosctl read --nodes <ip> /proc/cmdline` | IOMMU, hugepages, ASPM, THP all present |
| **A12** | Hugepages allocated | `talosctl read --nodes <ip> /proc/meminfo \| grep Huge` | Matches the configured count |
| **A13** | IOMMU is active | `talosctl dmesg \| grep -iE 'IOMMU\|DMAR\|AMD-Vi'` | Enabled |
| **A14** | NVIDIA modules loaded on a GPU node | `talosctl read /proc/modules \| grep nvidia` | `nvidia`, `nvidia_uvm` present |
| **A15** | All configured VLAN interfaces exist with correct MTU | `talosctl get addresses`, `talosctl get links` | VLANs 300/301/400 up at 9000 |
| **A16** | Exactly one default route | `talosctl get routes \| grep '0.0.0.0/0'` | One |
| **A17** | Time is synchronized to the seed node | `talosctl time --nodes <ip>` | Offset < 50 ms |
| **A18** | Disk encryption is active | `talosctl get volumestatus` / `dmesg \| grep -i luks` | STATE and EPHEMERAL encrypted |
| **A19** | Debugging runbook covers all 13 operations | Read it | Complete |
| **A20** | Config rollback works | `talosctl rollback` after a benign change | Node returns to the previous config |
| **A21** | Applying the same config twice is a no-op | Apply twice | No reboot, no change on the second |

---

## ↩️ ROLLBACK

```bash
talosctl rollback --nodes <ip>                          # previous machine config
talosctl apply-config --nodes <ip> --file <known-good>  # explicit known-good
talosctl reset --nodes <ip> --graceful=false --reboot   # ⚠️ WIPES THE NODE, reprovision via Phase 08
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Node boots but is not reachable on the expected IP | Interface selector matched the wrong NIC | Use `deviceSelector.hardwareAddr`; verify with `talosctl get links --insecure` from maintenance mode |
| `talosctl apply-config` reboots the node unexpectedly | The change requires a reboot (kernel args, disks) | Expected. Use `--mode=staged` to defer it to a planned drain. |
| Hugepages show 0 | Insufficient contiguous memory at boot, or the arg is missing | 1 G pages must be reserved at boot. Confirm the kernel arg; reduce the count if allocation fails. |
| NVIDIA modules do not load | Wrong schematic, or the module list omits them | Verify the installer image ID matches the GPU schematic; check `talosctl dmesg` for module load errors |
| VLAN interfaces missing | Switch port not trunked, or the wrong parent interface | Phase 03's switch config must tag those VLANs to the port; verify with `talosctl get links` |
| etcd is slow / high fsync latency | etcd landed on the OS disk instead of the PLP NVMe | Verify the `machine.disks` mount for `/var/lib/etcd`; this is a hard requirement |
| Node joins with the management IP as its node IP | `nodeIP.validSubnets` missing or wrong | Fix it and reapply. **Every pod-to-pod byte would otherwise cross the 1 GbE management network.** |
| Config applies but settings do not take effect | Patch ordering — a later patch overwrote an earlier one | Talos merges patches in order. Check the rendered output, not the patch files. |
| `talosctl validate` fails on an unknown field | Talos version mismatch between CLI and config | `talosctl` and the node must be the same minor version |
| Disk encryption fails to unlock after a motherboard swap | `nodeID` key derivation is tied to machine identity | Documented consequence. Record the recovery procedure: reprovision the node. |

---

## 🚫 DO NOT

- **Do not** set `install.wipe: true` in an archetype patch. It destroys data on every apply.
- **Do not** reference disks by `/dev/sdX` or `/dev/nvmeXn1`. Enumeration order is not stable. Use `by-id`.
- **Do not** install a CNI or kube-proxy. Phase 13 owns the datapath.
- **Do not** add OSD disks to `machine.disks` on storage nodes. Rook needs them raw (Phase 27).
- **Do not** add `mitigations=off` (ADR-027).
- **Do not** bootstrap etcd or the cluster here. That is Phase 12.
- **Do not** configure SR-IOV VFs beyond the count. Phase 21 owns the RDMA stack.
- **Do not** hand-edit anything in `talos/rendered/`. It is generated.
- **Do not** commit the decrypted secrets file, even briefly.

---

## 📤 HANDOFF

`evidence/phase-09/handoff.md` must state:

1. **The three schematic IDs** and their local mirror paths — Phase 08's workflow and Phase 53's upgrades both need them.
2. **Talos and Kubernetes versions**, and the NVIDIA driver version they imply.
3. **Where the encrypted cluster secrets live** and how to decrypt them (process, not key).
4. **The patch hierarchy and merge order** — every later phase that changes node configuration edits these files.
5. **Per-node overrides that exist** and why (disk IDs, static IPs, hugepage counts).
6. **How WoL persistence was implemented** (the Task 4 TODO) — Phase 08's power control depends on it.
7. **Which nodes have been configured** and which are still in maintenance mode.
8. **Any hardware that would not accept the config** and the reason.
9. **A pointer to the debugging runbook** — the first person paged will need it.

---

## ➡️ NEXT

**[PHASE-10 — Secrets & Configuration Management](PHASE-10.md)** — establish the SOPS/age bootstrap chain and the OpenBao deployment plan that every later phase's credentials flow through.
