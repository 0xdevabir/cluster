# PHASE 01 — Hardware Inventory & Capability Model

| | |
|---|---|
| **Stage** | 0 — Foundation & Design |
| **Estimated effort** | 3–4 hours |
| **Depends on** | 00 |
| **Blocks** | 02, 03, 05 — and every provisioning phase |
| **Risk** | 🟢 Low — read-only discovery; no configuration changes |
| **Blast radius** | None (boot a live ISO; nothing is written to disk) |
| **Architecture refs** | `ARCHITECTURE.md#l1--hardware-layer`, `ULTIMATE-PLAN.md#42-the-consumer-gpu-reality-table`, `#43-pcie-lane-budget` |

---

## 🎯 MISSION

Produce a **complete, machine-readable, schema-valid record of every physical machine** — including the facts that later phases cannot recover: PCIe link width, NUMA affinity of each GPU and NIC, GPU capability flags (NVLink / P2P / GPUDirect / MIG), disk power-loss-protection class, MAC addresses, and measured idle/peak power.

> 💡 **WHY this cannot be deferred:** every architectural decision downstream is a function of these facts. The scheduler's topology awareness (Law III), the storage tiering (§4.7), the network design (Phase 03), the power budget (Phase 02), and the Talos machine configs (Phase 09) all read from `inventory/`. Discovering in Phase 21 that half the fleet has its NIC behind a chipset x4 link is a project-schedule catastrophe. Discovering it now is a purchase order.

---

## ✅ PREFLIGHT

```bash
# 1. Phase 00 is complete and its gates pass
test -f inventory/schema/nodespec.schema.json && echo OK
task validate                                     # must exit 0

# 2. You can physically boot each machine from USB, or they already run Linux
#    (If neither: this phase is BLOCKED. Note it and stop.)

# 3. A discovery medium is available
#    Recommended: a Linux live USB with pciutils, nvme-cli, dmidecode,
#    numactl, ethtool, lshw, smartmontools, and the NVIDIA driver.
```

**If you have zero machines available yet:** you can still complete Tasks 1, 2, 7, and 8 (schema extension, the discovery script, the classification rules, the procurement gap analysis) using the specification-sheet values for machines you intend to buy. Mark every such record `spec.status: planned` and re-run discovery when hardware arrives. Note this in `handoff.md`.

---

## 📦 DELIVERABLES

```
tools/discover-node.sh                  # runs ON a target machine, emits NodeSpec YAML
tools/discovery/
  collect.sh                            # raw fact collection
  emit-nodespec.py                      # raw facts → schema-valid YAML
  capability-matrix.yaml                # GPU model → capability flags lookup
  disk-class-matrix.yaml                # SSD model → class/PLP/DWPD lookup
inventory/nodes/nx-*.yaml               # one per machine, schema-valid
inventory/fleet-summary.md              # generated: totals, heterogeneity report
tools/inventory-stats.sh
tools/render-node-table.sh
docs/hardware-classification.md         # the rules used to assign archetypes
evidence/phase-01/
  raw/<hostname>/*.txt                  # verbatim discovery output per machine
  acceptance.md  handoff.md  deviations.md
```

---

## 📋 TASKS

### Task 1 — Build the GPU capability matrix

The single most consequential lookup table in the project. GPU model → what it can actually do.

**`tools/discovery/capability-matrix.yaml`**
```yaml
# Capability flags that CANNOT be reliably auto-detected and must be looked up.
# Sources: NVIDIA product briefs, driver release notes, CUDA docs.
# ⚠️ Verify each entry against the actual hardware in Task 4 where a runtime probe exists.
gpus:
  "NVIDIA GeForce RTX 3090":
    vramGB: 24, computeCapability: "8.6", tdpWatts: 350
    nvlink: true          # 2-way NVLink bridge available
    p2pCapable: true      # over NVLink; PCIe P2P also works on Ampere GeForce
    gpudirectRdma: false  # not officially supported on GeForce
    migCapable: false
    eccSupported: false
    fp8: false
  "NVIDIA GeForce RTX 4090":
    vramGB: 24, computeCapability: "8.9", tdpWatts: 450
    nvlink: false         # removed on Ada
    p2pCapable: false     # ⚠️ disabled in driver; community patch exists (Phase 18)
    gpudirectRdma: false
    migCapable: false
    eccSupported: false
    fp8: true
  "NVIDIA GeForce RTX 5090":
    vramGB: 32, computeCapability: "12.0", tdpWatts: 575
    nvlink: false, p2pCapable: false, gpudirectRdma: false
    migCapable: false, eccSupported: false, fp8: true, fp4: true
  "NVIDIA RTX 6000 Ada Generation":
    vramGB: 48, computeCapability: "8.9", tdpWatts: 300
    nvlink: false, p2pCapable: true, gpudirectRdma: true
    migCapable: false, eccSupported: true, fp8: true
  "NVIDIA A100-SXM4-80GB":
    vramGB: 80, computeCapability: "8.0", tdpWatts: 400
    nvlink: true, p2pCapable: true, gpudirectRdma: true
    migCapable: true, eccSupported: true, fp8: false
  "NVIDIA H100 PCIe":
    vramGB: 80, computeCapability: "9.0", tdpWatts: 350
    nvlink: true, p2pCapable: true, gpudirectRdma: true
    migCapable: true, eccSupported: true, fp8: true

# Fallback rule for unknown models — conservative, must be corrected by hand
default:
  nvlink: false
  p2pCapable: false
  gpudirectRdma: false
  migCapable: false
  eccSupported: false
  _requiresManualReview: true
```

> ⚠️ **DANGER — optimistic defaults kill projects.** If a model is not in this table, the default assumes *no* advanced capability. That is deliberate. An incorrectly optimistic `gpudirectRdma: true` will produce a NCCL configuration that silently falls back to TCP, and you will spend Phase 22 hunting a 10× performance mystery.

**`tools/discovery/disk-class-matrix.yaml`** — same treatment for storage. Key field: `plp` (power-loss protection). Rule of thumb encoded as a comment: *if the datasheet does not explicitly say "power loss protection" or "PLP" or list a capacitor, it is `false`.* Consumer drives (990 PRO, SN850X, Firecuda, MP600, etc.) → `class: consumer-nvme, plp: false`. Enterprise (PM9A3, D7-P5520, DC1500M, Micron 7450, Kioxia CD8) → `class: enterprise-nvme, plp: true`.

---

### Task 2 — Write the discovery script

**`tools/discovery/collect.sh`** — runs on a target machine as root, writes raw facts to stdout as a structured blob. Must be read-only.

```bash
#!/usr/bin/env bash
# NEXUS hardware discovery — READ ONLY. Writes nothing to the target.
# Usage:  sudo ./collect.sh > /tmp/$(hostname)-raw.txt
set -uo pipefail   # NOT -e: a missing tool must not abort the whole collection

section() { printf '\n===== %s =====\n' "$1"; }
try()     { printf '\n--- %s ---\n' "$*"; "$@" 2>&1 || echo "(command failed: $*)"; }

section HOSTNAME;  hostname; cat /etc/machine-id 2>/dev/null
section DMI
  try dmidecode -t system
  try dmidecode -t baseboard
  try dmidecode -t bios
  try dmidecode -t processor
  try dmidecode -t memory
section CPU
  try lscpu
  try cat /proc/cpuinfo
  try lscpu -e                       # per-CPU topology: NUMA, socket, core, cache
section NUMA
  try numactl --hardware
  try lstopo-no-graphics --of console
  try cat /sys/devices/system/node/node*/meminfo
section MEMORY
  try free -h
  try dmidecode -t 17                 # per-DIMM: size, speed, ECC, rank
  try grep -i ecc /sys/devices/system/edac/mc/mc*/ce_count 2>/dev/null
section PCI
  try lspci -nnvv                     # LnkCap/LnkSta are here — CRITICAL
  try lspci -tv                       # topology tree: which root complex
  for d in /sys/bus/pci/devices/*; do
    printf '%s numa_node=%s\n' "$(basename "$d")" "$(cat "$d/numa_node" 2>/dev/null)"
  done
section IOMMU
  try dmesg | grep -iE 'DMAR|IOMMU|AMD-Vi'
  try ls /sys/kernel/iommu_groups/
section GPU
  try nvidia-smi -q                   # full query: UUID, VBIOS, power limits, PCIe
  try nvidia-smi topo -m              # GPU↔GPU and GPU↔NIC affinity matrix
  try nvidia-smi --query-gpu=index,name,uuid,memory.total,pci.bus_id,pcie.link.gen.max,pcie.link.width.max,pcie.link.gen.current,pcie.link.width.current,power.limit,power.max_limit --format=csv
  try nvidia-smi nvlink -s
section NET
  for i in $(ls /sys/class/net | grep -v lo); do
    printf '\n-- iface %s --\n' "$i"
    cat "/sys/class/net/$i/address" 2>/dev/null
    cat "/sys/class/net/$i/device/numa_node" 2>/dev/null
    readlink -f "/sys/class/net/$i/device" 2>/dev/null
    ethtool "$i" 2>&1 | head -30
    ethtool -i "$i" 2>&1
    ethtool -k "$i" 2>&1 | head -20
  done
  try ibv_devinfo -v                  # RDMA verbs devices
  try rdma link show
  try ls /sys/class/infiniband/
section STORAGE
  try lsblk -O -J                     # JSON, includes rota/model/serial/size
  try nvme list
  for n in /dev/nvme[0-9]n[0-9]; do
    [ -e "$n" ] || continue
    try nvme id-ctrl "$n"
    try nvme smart-log "$n"
  done
  try smartctl --scan
  for d in $(smartctl --scan | awk '{print $1}'); do try smartctl -a "$d"; done
section FIRMWARE
  try efibootmgr -v
  try mokutil --sb-state
  try cat /sys/firmware/efi/efivars/SecureBoot-* 2>/dev/null | xxd
section KERNEL
  try uname -a
  try cat /proc/cmdline
section POWER
  try cat /sys/class/hwmon/hwmon*/name
  try sensors
  try turbostat --num_iterations 1 --interval 1 2>&1 | head -20
```

**`tools/discovery/emit-nodespec.py`** — parses the raw blob and emits schema-valid YAML. Key parsing responsibilities:

| Field | Source | Parsing note |
|---|---|---|
| `cpu.numaNodes` | `numactl --hardware` → "available: N nodes" | On AM5 this is usually 1; on EPYC it depends on NPS setting |
| `gpus[].pcieWidth` | `nvidia-smi --query-gpu=pcie.link.width.max` | **Use `max`, not `current`** — current drops to x1 when idle (ASPM) |
| `gpus[].numaAffinity` | `/sys/bus/pci/devices/<busid>/numa_node` | `-1` means "no NUMA affinity reported" — flag for manual review |
| `nics[].rdma.device` | `ls /sys/class/infiniband/` cross-referenced with `ibdev2netdev` | Empty ⇒ `rdma.capable: false` |
| `nics[].pcieWidth` | `lspci -vv` `LnkSta:` line for the NIC's bus ID | ⚠️ **This is the field that reveals a chipset-attached NIC** |
| `storage[].plp` | `disk-class-matrix.yaml` lookup by model string | Unknown model ⇒ `plp: false` + `_requiresManualReview` |
| `memory.ecc` | `dmidecode -t 16` "Error Correction Type" | `Single-bit ECC`/`Multi-bit ECC` ⇒ true; `None` ⇒ false |
| `firmware.iommu` | `dmesg` grep for `DMAR: IOMMU enabled` / `AMD-Vi: Interrupt remapping enabled` | Absent ⇒ false ⇒ **node cannot do SR-IOV** |
| `firmware.wol` | `ethtool <mgmt-iface>` → `Wake-on: g` | Anything but `g` ⇒ WoL is off ⇒ Phase 08 cannot power it on |

---

### Task 3 — Run discovery on every machine

Two paths, depending on what you have:

**Path A — machines already run Linux:**
```bash
for host in $(cat hosts.txt); do
  scp tools/discovery/collect.sh "$host:/tmp/"
  ssh "$host" 'sudo bash /tmp/collect.sh' > "evidence/phase-01/raw/${host}-raw.txt"
done
```

**Path B — bare machines (recommended, gives a clean baseline):**
1. Build a discovery USB from a Linux live image with the required tools plus the NVIDIA driver.
2. Boot each machine, run `collect.sh`, write the output to a second USB or POST it to the seed node.
3. **While you are physically at each machine, also do the BIOS pass** (Task 5) — you will not want to walk the racks twice.

📊 Record the wall-clock time per machine. At 100 nodes, a 10-minute-per-machine process is 17 hours of labor. This number justifies the vPro/AMT recommendation in `ULTIMATE-PLAN.md §4.6`.

---

### Task 4 — Runtime capability probes (do not trust the datasheet alone)

Three capabilities must be *measured*, not looked up:

**P1 — GPU peer-to-peer**
```bash
# from cuda-samples
./p2pBandwidthLatencyTest
# Look for: "Peer access from ... to ... : Yes/No"
# On a 4090 pair you will see No. Record p2pCapable accordingly.
```

**P2 — PCIe link width under load** (idle links downtrain; you must load them)
```bash
# Start a transfer, then read the link status in another shell
./bandwidthTest --memory=pinned --mode=shmoo &
watch -n1 'sudo lspci -vv -s <gpu-bus-id> | grep -E "LnkCap|LnkSta"'
# LnkSta must show the same width as LnkCap. If LnkSta < LnkCap under load:
#   → bad riser, bad slot, or a BIOS bifurcation setting. FIX IT NOW.
```

**P3 — GPUDirect RDMA availability**
```bash
lsmod | grep nvidia_peermem       # module present?
ls /sys/kernel/mm/memory_peers/   # peer memory clients registered?
# And definitively, in Phase 22: NCCL_DEBUG=INFO will print
#   "NCCL INFO ... [GDR] ..." or "GDRDMA disabled"
```

Record all three in `evidence/phase-01/raw/<host>/probes.txt` and reconcile against the capability matrix. **Any mismatch between matrix and probe is a finding for `deviations.md`.**

---

### Task 5 — BIOS/UEFI standardization pass

While physically at each machine, apply and record this settings baseline. These cannot be changed later without another physical visit (unless the board has vPro/Redfish).

| Setting | Required value | Why | Breaks if wrong |
|---|---|---|---|
| **Boot order** | Network (PXE/UEFI) **first**, then local disk | Zero-touch provisioning (Phase 08) | Cannot reimage remotely — fatal at scale |
| **Network stack / PXE** | Enabled, **IPv4 UEFI PXE** | Same | Same |
| **Wake-on-LAN** | Enabled | Remote power-on (§4.6) | Cannot power on remotely |
| **ErP / Deep Sleep / EuP** | **Disabled** | ErP cuts standby power to the NIC, killing WoL | WoL silently fails |
| **Restore on AC power loss** | **Power On** (or "Last State") | Rack power event → nodes come back | Manual button press on 100 machines |
| **IOMMU / VT-d / AMD-Vi** | **Enabled** | Required for SR-IOV (Phase 21) | No RDMA in pods |
| **SR-IOV support** | Enabled | Same | Same |
| **Above 4G Decoding** | Enabled | Required for large-BAR GPUs and multi-GPU | GPU not detected / limited |
| **Resizable BAR** | Enabled | Host↔device transfer performance | ~5–10 % transfer loss |
| **PCIe slot bifurcation** | Set explicitly per the node's card layout | Determines actual GPU/NIC widths | Silent x8/x4 downgrade |
| **PCIe ASPM** | **Disabled** on slots holding GPU/NIC | Power-save downtrains the link | Latency spikes, link flap |
| **C-States** | C1E on, deeper C-states **disabled** on compute nodes | Wake latency hurts RDMA polling | +µs latency jitter |
| **CPU power / performance mode** | Max performance / disable EIST throttling ceiling | Consistent clocks | 3–10 % throughput variance |
| **SMT / Hyper-Threading** | **Enabled** (default) | More dataloader threads | Revisit in Phase 49 with a benchmark |
| **Memory profile** | XMP/EXPO **enabled at the validated speed** | Memory bandwidth is the dataloader bottleneck | 20–30 % memory BW loss |
| **Secure Boot** | Disabled initially; revisit in Phase 55 | Talos supports it, but it complicates bring-up | — |
| **TPM** | Enabled if present | Disk-encryption key sealing (Phase 09) | Falls back to a stored key |
| **Fan curve** | Aggressive / performance | Consumer boards default to quiet | Thermal throttling (§4.5) |
| **Onboard audio / serial / parallel / RGB** | Disabled | Fewer devices, fewer IRQs, less firmware surface | — |

Record the exact settings applied per machine in `evidence/phase-01/raw/<host>/bios.md`. **Where a board cannot provide a required setting, that machine gets `spec.status: failed` with a note — it is not fleet-eligible.**

---

### Task 6 — Assign archetypes

**`docs/hardware-classification.md`** — write down the decision rules so classification is reproducible, not vibes.

```
IF  has ≥1 NVIDIA GPU  AND  NIC ≥ 25 Gbps with rdma.capable
    → compute-gpu, pool=training

ELIF has ≥1 NVIDIA GPU  AND  NIC ≥ 10 Gbps
    → compute-gpu, pool=general        # cannot join a distributed training gang

ELIF has ≥1 NVIDIA GPU  AND  NIC < 10 Gbps
    → compute-gpu, pool=inference|interactive   # single-node work only
    → RAISE FINDING: this node needs a NIC upgrade before it can train (R-01)

ELIF ≥6 data disks  AND  ≥1 enterprise-nvme with plp=true  AND  memory.ecc=true
    → storage

ELIF memory.ecc=true  AND  ≥1 enterprise disk  AND  no GPU  AND  high single-thread CPU
    → control      (need exactly 3 or 5 of these)

ELIF no GPU
    → compute-cpu  (or infra, if you need 2–3 for platform services)

Post-conditions that MUST hold across the fleet:
  · count(control) ∈ {3, 5}
  · count(storage) ≥ 3 at M1, ≥ 5 at M3   (Ceph needs ≥3 failure domains)
  · every compute-gpu in pool=training has topology.aligned == true
  · no two control nodes share a rack (once ≥3 racks exist)
```

Then apply the archetype to each `inventory/nodes/*.yaml` and re-run `task validate:inventory`.

---

### Task 7 — Heterogeneity & gap analysis

Generate `inventory/fleet-summary.md` via `tools/inventory-stats.sh`. It must report:

```markdown
## Fleet Totals
| Metric | Value |
| Nodes (by archetype) | control: 3, compute-gpu: 42, storage: 5, ... |
| Physical CPU cores | 704 |
| Logical threads | 1408 |
| Total RAM | 5.4 TB  (ECC: 0.9 TB / non-ECC: 4.5 TB) |
| GPUs | 42 |
| Total VRAM | 1008 GB |
| Aggregate FP16 TFLOPS (spec) | 3,486 |
| Tier-0 NVMe | 84 TB |
| Enterprise (PLP) NVMe | 12 TB |
| Aggregate NIC bandwidth | 4,200 Gbps |
| Estimated peak power | 31.2 kW |

## Heterogeneity Report   ← this drives ResourceFlavors in Phase 30
| GPU model | Count | VRAM | Nodes | Can train together? |
| RTX 4090 | 28 | 24 GB | nx-c-r01-* … | ✅ (28-way max gang) |
| RTX 3090 | 14 | 24 GB | nx-c-r04-* … | ✅ (14-way max gang) |
| Mixed 4090+3090 | — | — | — | ❌ FORBIDDEN — straggler risk (R-15) |

## FINDINGS  ← the actionable output of this phase
| ID | Severity | Finding | Affected nodes | Remediation | Cost |
| F-01 | 🔴 | 18 nodes have only 1 GbE — cannot join the training pool | nx-c-r03-* | Add ConnectX-5 | $3,240 |
| F-02 | 🔴 | No enterprise PLP NVMe anywhere — Ceph WAL/DB has no home (R-04) | all storage | Buy 6× 1.6 TB PM9A3 | $2,400 |
| F-03 | 🟠 | 12 nodes: NIC on a chipset x4 link, capped ~32 Gbps | nx-c-r02-* | Move to CPU slot or accept the cap | $0 |
| F-04 | 🟠 | 0 nodes have ECC — no control-node candidate meets the spec | all | Buy 3 workstation-class control nodes | $5,400 |
| F-05 | 🟡 | 7 nodes: IOMMU disabled and BIOS has no option | nx-u-r05-* | Reclassify to compute-cpu (no SR-IOV) | $0 |
| F-06 | 🟡 | GPU EULA review not yet performed (R-03) | all GeForce | Legal review before scale-out | — |
```

> 💡 **This findings table is the real deliverable of Phase 01.** Phase 05 turns it into a purchase order. Everything else here is supporting evidence.

---

### Task 8 — RISK-LEGAL-01: the GPU licence review

Open a tracked item (issue/ticket) referencing `ULTIMATE-PLAN.md` R-03:

> NVIDIA's GeForce driver licence has historically restricted "datacenter deployment" of GeForce products. Before scaling beyond the pilot, obtain a determination on whether this deployment falls within permitted use, from someone qualified to make it.
> **Options if the answer is no:** (a) use RTX PRO / datacenter SKUs for the production pool; (b) obtain written clarification from the vendor; (c) restrict GeForce nodes to a use-case that is clearly permitted.
> **This plan does not provide legal advice.** Record the determination and its date in `evidence/phase-01/legal-review.md`.

Do not let this block Phases 02–20; do let it block the M2 (24-node) procurement decision.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Every machine has a schema-valid NodeSpec | `task validate:inventory` | Exit 0 |
| **A2** | Node count matches physical reality | `ls inventory/nodes/*.yaml \| wc -l` vs. a physical count | Equal |
| **A3** | Every node has raw discovery evidence | `for f in inventory/nodes/*.yaml; do test -d "evidence/phase-01/raw/$(basename $f .yaml)"; done` | All present |
| **A4** | No duplicate MACs, names, or rack units | `task validate:inventory` cross-checks | Pass |
| **A5** | Every GPU has `pcieWidth` from the **max** query, verified under load | Inspect probes.txt | LnkSta == LnkCap under load, or a documented finding |
| **A6** | Every GPU capability flag is either matrix-sourced or probe-verified | Grep for `_requiresManualReview` | Zero remaining, or each listed in `deviations.md` |
| **A7** | Every NIC has `numaAffinity` and `rdma.capable` populated | `yq` query across all nodes | No nulls |
| **A8** | Every disk has `class`, `plp`, `tier`, `role` | `yq` query | No nulls |
| **A9** | Archetype post-conditions hold | Run the checks from Task 6 | control ∈ {3,5}; storage ≥ 3 |
| **A10** | `fleet-summary.md` generates and is committed | `task inventory:stats` | File produced, totals plausible |
| **A11** | Findings table exists with severity, remediation, and cost | Read it | ≥ 1 finding, or an explicit "no findings" statement |
| **A12** | BIOS baseline recorded per machine | `ls evidence/phase-01/raw/*/bios.md` | One per node |
| **A13** | Every `pool=training` node has `topology.aligned == true` | Cross-check GPU and NIC `numaAffinity` | All match, or node demoted from the pool |
| **A14** | Legal review item is opened and referenced | `evidence/phase-01/legal-review.md` | Exists with a tracking reference |

---

## ↩️ ROLLBACK

Discovery is read-only; there is nothing to roll back in software. **BIOS changes are the exception** — record the *previous* value of every setting you change in `bios.md` so a machine can be restored.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| `nvidia-smi` not found on the live USB | Driver not in the live image | Use a CUDA-enabled live image, or read `lspci -nn \| grep -i nvidia` and look up the device ID |
| `numa_node` reads `-1` for every device | Kernel found no SRAT/SLIT, or a single-NUMA system | On single-socket consumer boards this is normal. Set `numaAffinity: 0` and note it. |
| `LnkSta` shows x1 or x4 when `LnkCap` says x16 | ASPM downtraining at idle, a bad riser, or wrong bifurcation | Re-measure under load (Task 4/P2). If it persists: reseat, replace the riser, fix bifurcation |
| `ethtool` reports `Wake-on: d` | WoL disabled in BIOS or by the driver | BIOS: enable WoL, disable ErP. Runtime: `ethtool -s <if> wol g` (does not persist across reboot on all NICs — Phase 09 sets it via Talos) |
| `dmidecode -t 17` says ECC "None" on a board that claims ECC support | Unbuffered ECC unsupported by the CPU, or ECC disabled in BIOS | Consumer AMD boards often "support" ECC without validating it. Trust `dmidecode`, not marketing. |
| Two NICs report the same MAC | Bonded/teamed interface, or a cloned VM template | Report the *physical* MACs from `/sys/class/net/*/address` on the slaves |
| `ibv_devinfo` shows nothing on a ConnectX card | `mlx5_ib` module not loaded, or the card is in Ethernet-only firmware mode | `modprobe mlx5_ib`; check `mlxconfig -d <dev> q` for `LINK_TYPE_P1` |
| Disk model string is not in the class matrix | New/unlisted drive | Look up the datasheet, add an entry, default `plp: false` if unproven |
| A machine will not PXE at all | No UEFI network stack, or NIC lacks a PXE ROM | Try an iPXE USB as a permanent chainloader, or replace the NIC |

---

## 🚫 DO NOT

- **Do not** install any operating system or write to any disk. Discovery is read-only.
- **Do not** guess capability flags. An unknown GPU gets the conservative default and a manual-review flag.
- **Do not** assign `pool=training` to a node that fails the RDMA or topology-alignment test. That is exactly how R-01 and R-15 materialize.
- **Do not** configure networking, VLANs, or switches. That is Phase 03.
- **Do not** perform the electrical load study here. That is Phase 02 (it consumes this phase's power data).
- **Do not** buy anything yet. Phase 05 consolidates all findings into one purchase decision.
- **Do not** skip the BIOS pass because "we can do it later." At 100 nodes, later means never.

---

## 📤 HANDOFF

`evidence/phase-01/handoff.md` must state:

1. **Fleet composition** — counts by archetype, GPU models, and the resulting `ResourceFlavor` groups Phase 30 will need.
2. **The findings table** — verbatim, with severities. Phase 02 needs F-power items; Phase 03 needs F-network items; Phase 05 needs all of them.
3. **Measured idle and peak power per archetype** — Phase 02's load study depends entirely on these numbers.
4. **MAC address list** — Phase 08's Tinkerbell `Hardware` CRs are keyed on these.
5. **Which nodes cannot do SR-IOV** (IOMMU unavailable) — Phase 21 must exclude them.
6. **Which nodes have `gpudirectRdma: true`** — Phase 22 configures NCCL differently for them.
7. **BIOS settings that could not be applied** and on which machines.
8. **Legal review status.**

---

## ➡️ NEXT

**[DONE_PHASE-02 — Facility, Power & Thermal Design](DONE_PHASE-02.md)** ✅ — turn the measured per-node power draw into an electrical load study, a cooling plan, and a rack layout that keeps the breakers closed.
