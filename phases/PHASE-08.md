# PHASE 08 — Bare-Metal Provisioning with Tinkerbell

| | |
|---|---|
| **Stage** | 1 — Bootstrap Infrastructure |
| **Estimated effort** | 5–6 hours |
| **Depends on** | 07 |
| **Blocks** | 09, 11, 12 |
| **Risk** | 🔴 High — **this phase wipes disks**. A misconfigured workflow reimages the wrong machine. |
| **Blast radius** | Any machine that PXE-boots on the management VLAN |
| **Architecture refs** | `ARCHITECTURE.md#l31-zero-touch-provisioning-flow`, `ULTIMATE-PLAN.md#46-no-bmc`, R-05 |

---

## 🎯 MISSION

Build the **zero-touch provisioning pipeline**: an unknown machine that powers on is discovered, inventoried, classified, and (once approved) imaged automatically — with remote power control that works on motherboards that have no BMC.

> ⚠️ **DANGER — this is the first destructive phase.** Tinkerbell workflows write to disks. Before enabling any workflow: confirm the target MAC, confirm the target disk, and confirm the machine is one you intend to wipe. **Build the safety interlocks in Task 6 before running your first workflow, not after.**

> 💡 **WHY Tinkerbell over MAAS/Metal³:** Metal³ requires Redfish/BMC, which consumer boards do not have. MAAS is Ubuntu-centric and heavy. Tinkerbell is netboot-native, workflow-driven, Kubernetes-API-shaped, and — critically — lets us script the WoL and PDU power actions that stand in for a missing BMC (ADR from `ULTIMATE-PLAN.md §7`).

---

## ✅ PREFLIGHT

```bash
bash tools/seed-preflight.sh
bash tools/net-services-check.sh              # Phase 07 all green

# A machine actually netbooted in Phase 07
test -s evidence/phase-07/first-netboot.log && echo OK

# Every node has a management MAC in inventory
yq -r '.spec.nics[] | select(.role=="management") | .macAddress' inventory/nodes/*.yaml \
  | grep -c .
# ↑ must equal node count

# PDU reachable and its API answers
snmpwalk -v3 -l authPriv -u "$PDU_USER" ... 10.100.1.11 1.3.6.1.2.1.1.1.0
# or: curl -fsS -u ... https://10.100.1.11/api/outlets

# ⚠️ Confirm you know which machines are safe to wipe
```

---

## 📦 DELIVERABLES

```
bootstrap/tinkerbell/
  docker-compose.yaml           # or a Helm values file if running on K8s later
  hardware/                     # generated Hardware CRs, one per node
  templates/
    discovery.yaml              # inventory collection, NON-destructive
    talos-install.yaml          # ⚠️ DESTRUCTIVE
    wipe.yaml                   # ⚠️ EXPLICITLY DESTRUCTIVE, manual trigger only
  workflows/                    # instantiated per-node workflows
  hook/                         # HookOS kernel + initramfs
  ipxe/auto.ipxe.tmpl
tools/power/
  power-ctl.sh                  # unified: on | off | cycle | status <node>
  wol.sh
  pdu-snmp.sh                   # or pdu-rest.sh, per Phase 02's PDU choice
  pikvm.sh
tools/provisioning/
  approve-node.sh               # the human/automated gate before any wipe
  provision.sh                  # end-to-end: power on → workflow → verify
  discovery-agent/              # image that runs collect.sh and POSTs results
docs/operations/
  provisioning-runbook.md
  power-control-runbook.md      # the escalation ladder from Phase 02
evidence/phase-08/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Source |
|---|---|---|
| Tinkerbell stack (Helm) | `0.6.2` | `oci://ghcr.io/tinkerbell/charts/stack` |
| Smee (boots) | `0.13.0` | `ghcr.io/tinkerbell/smee` |
| Tink server/controller | `0.13.0` | `ghcr.io/tinkerbell/tink` |
| Hegel (metadata) | `0.12.0` | `ghcr.io/tinkerbell/hegel` |
| Rufio (power/BMC) | `0.6.0` | `ghcr.io/tinkerbell/rufio` — used only where BMC exists |
| HookOS | `0.10.0` | `github.com/tinkerbell/hook` releases |
| Talos Image Factory | service | `https://factory.talos.dev` |

> 💡 **Deployment choice:** Tinkerbell normally runs *on* Kubernetes, but we have no cluster yet. Two valid paths:
> - **A (recommended):** run a single-node k3s on the seed node purely to host Tinkerbell. Gives you the CRD model that Tinkerbell expects, cleanly.
> - **B:** run the components as plain containers with a file-backed backend.
>
> Pick one, record it in `deviations.md`, and note that this k3s is **not** the NEXUS cluster — it is a bootstrap appliance that gets decommissioned or repurposed after Phase 12.

---

## 📋 TASKS

### Task 1 — Deploy the Tinkerbell stack

```bash
# Path A: k3s on the seed node, bootstrap-only
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.31.4+k3s1 sh -s - \
  --disable traefik --disable servicelb --write-kubeconfig-mode 644 \
  --node-ip 10.100.0.10
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

helm upgrade --install tink-stack \
  oci://ghcr.io/tinkerbell/charts/stack --version 0.6.2 \
  --namespace tink-system --create-namespace \
  --set "global.trustedProxies={10.42.0.0/16}" \
  --set "global.publicIP=10.100.0.10" \
  --set "stack.hook.enabled=true" \
  --set "smee.dhcp.enabled=false" \
  --wait
```

> ⚠️ **`smee.dhcp.enabled=false` matters.** Phase 07's dnsmasq already serves DHCP. Two DHCP servers on one VLAN produce nondeterministic, maddening failures. Choose one:
> - **Keep dnsmasq for DHCP** (chosen here) and let Smee serve only iPXE scripts over HTTP; point dnsmasq's `tag:ipxe` boot URL at Smee.
> - Or disable dnsmasq's DHCP and let Smee own it. Then update Phase 07's config and re-run its A2 check.
>
> Record which you chose. **Never run both.**

---

### Task 2 — Hardware CRs from inventory

Every machine Tinkerbell may touch needs a `Hardware` object keyed by MAC.

```yaml
# bootstrap/tinkerbell/hardware/nx-c-r01-05.yaml  (generated)
apiVersion: tinkerbell.org/v1alpha1
kind: Hardware
metadata:
  name: nx-c-r01-05
  namespace: tink-system
  labels:
    nexus.io/archetype: compute-gpu
    nexus.io/rack: r01
    nexus.io/approved: "false"        # ← THE SAFETY INTERLOCK (Task 6)
spec:
  disks:
    - device: /dev/nvme1n1            # boot disk, from inventory storage[role=boot]
  metadata:
    facility: { facility_code: hq, plan_slug: compute-gpu }
    instance:
      hostname: nx-c-r01-05
      id: "aa:bb:cc:dd:ee:05"
      operating_system: { distro: talos, version: "1.9.5" }
  interfaces:
    - dhcp:
        mac: "aa:bb:cc:dd:ee:05"
        hostname: nx-c-r01-05
        ip: { address: 10.100.0.105, netmask: 255.255.252.0, gateway: 10.100.0.1 }
        name_servers: [10.100.0.10]
        ntp_servers:  [10.100.0.10]
        lease_time: 86400
        arch: x86_64
        uefi: true
      netboot:
        allowPXE: false               # ← flipped to true ONLY by approve-node.sh
        allowWorkflow: false
```

**`tools/provisioning/gen-hardware.sh`** generates these from `inventory/nodes/*.yaml`. Never hand-write them — the MAC and disk must come from the validated inventory, or you will wipe the wrong device.

> ⚠️ **`spec.disks[0].device` is the disk that gets wiped.** Verify it against `inventory/nodes/<n>.yaml` `storage[] where role == boot`. On machines with multiple NVMe drives, `/dev/nvme0n1` is **not** reliably the boot disk — enumeration order varies. Prefer selecting by serial in the workflow (Task 4) and treat the device path as a hint.

---

### Task 3 — Power control (the no-BMC layer)

**`tools/power/power-ctl.sh`** — one interface for four mechanisms, implementing the Phase 02 escalation ladder.

```bash
#!/usr/bin/env bash
# Unified power control for BMC-less nodes.
#   power-ctl.sh <on|off|cycle|status> <node-name>
source "$(dirname "$0")/../lib/common.sh"
need yq; need snmpwalk; need wakeonlan

ACTION="${1:?on|off|cycle|status}"; NODE="${2:?node name}"
SPEC="$ROOT/inventory/nodes/$NODE.yaml"
[[ -f "$SPEC" ]] || die "unknown node: $NODE"

MAC=$(yq -r '.spec.nics[] | select(.role=="management") | .macAddress' "$SPEC")
PDU=$(yq -r '.spec.location.pdu[0]' "$SPEC")     # e.g. r01-pdu-a:12
PDU_HOST="${PDU%%:*}"; OUTLET="${PDU##*:}"
PDU_IP=$(yq -r ".pdus[] | select(.name==\"$PDU_HOST\") | .mgmtIp" "$ROOT/inventory/power/pdu-map.yaml")

pdu_set() {   # pdu_set <on|off>
  local state="$1" val
  [[ "$state" == on ]] && val=1 || val=2      # vendor-specific OID values
  snmpset -v3 -l authPriv -u "$PDU_SNMP_USER" -a SHA -A "$PDU_SNMP_AUTH" \
          -x AES -X "$PDU_SNMP_PRIV" "$PDU_IP" \
          "$PDU_OUTLET_OID.$OUTLET" i "$val"
}
pdu_state() {
  snmpget -v3 -l authPriv -u "$PDU_SNMP_USER" ... "$PDU_IP" "$PDU_OUTLET_OID.$OUTLET" \
    | awk '{print $NF}'
}
pdu_current() {   # amps drawn — distinguishes "off" from "dead"
  snmpget -v3 ... "$PDU_IP" "$PDU_CURRENT_OID.$OUTLET" | awk '{print $NF}'
}

case "$ACTION" in
  on)
    if [[ "$(pdu_state)" != 1 ]]; then
      log "outlet is off — powering the outlet on"; pdu_set on; sleep 15
    fi
    log "sending WoL to $MAC"
    wakeonlan -i 10.100.3.255 "$MAC"
    ;;
  off)
    # ALWAYS try graceful first (Law: never yank power on a node that can be asked)
    if talosctl --nodes "$NODE.nexus.internal" shutdown 2>/dev/null; then
      log "graceful shutdown issued; waiting up to 120 s"
      for _ in $(seq 24); do
        [[ "$(pdu_current)" == "0" ]] && { ok "node powered down"; exit 0; }
        sleep 5
      done
      warn "graceful shutdown did not complete"
    fi
    warn "forcing outlet off"; pdu_set off
    ;;
  cycle)
    pdu_set off; sleep 10; pdu_set on; sleep 15; wakeonlan -i 10.100.3.255 "$MAC"
    ;;
  status)
    printf 'outlet=%s current=%sA ' "$(pdu_state)" "$(pdu_current)"
    ping -c1 -W1 "$NODE.mgmt.nexus.internal" >/dev/null 2>&1 && echo "network=up" || echo "network=down"
    ;;
esac
```

**Fill in the vendor OIDs** from the PDU's MIB (Phase 02 chose the model). Document them in `power-control-runbook.md` — the next operator will not want to read a MIB.

🔒 **SECRET:** `PDU_SNMP_*` credentials come from the environment, sourced from the secret store (Phase 10). Never hardcode.

**Test the ladder before trusting it:**
```bash
tools/power/power-ctl.sh status nx-c-r01-05     # baseline
tools/power/power-ctl.sh off   nx-c-r01-05      # graceful path
tools/power/power-ctl.sh on    nx-c-r01-05      # WoL path
# Then simulate a hang: pull the network cable, and verify `cycle` still works.
tools/power/power-ctl.sh cycle nx-c-r01-05
```

📊 **Record time-to-power-on** for each mechanism. WoL should be < 5 s to POST; a PDU cycle adds ~25 s.

---

### Task 4 — Workflow templates

**Discovery (non-destructive) — `templates/discovery.yaml`**

```yaml
apiVersion: tinkerbell.org/v1alpha1
kind: Template
metadata: { name: nexus-discovery, namespace: tink-system }
spec:
  data: |
    version: "0.1"
    name: nexus-discovery
    global_timeout: 1800
    tasks:
      - name: discover
        worker: "{{.device_1}}"
        volumes:
          - /dev:/dev
          - /sys:/sys:ro
          - /lib/firmware:/lib/firmware:ro
        actions:
          - name: collect-hardware-facts
            image: harbor.nexus.internal/nexus/discovery-agent:0.3.0
            timeout: 600
            environment:
              COLLECTOR_ENDPOINT: "http://10.100.0.10:8080/api/discovery"
              NODE_MAC: "{{.device_1}}"
            # READ ONLY — this action must never write to a block device
          - name: report-and-halt
            image: harbor.nexus.internal/nexus/discovery-agent:0.3.0
            command: ["/bin/report-complete"]
            timeout: 60
```

**Talos install (DESTRUCTIVE) — `templates/talos-install.yaml`**

```yaml
apiVersion: tinkerbell.org/v1alpha1
kind: Template
metadata: { name: nexus-talos-install, namespace: tink-system }
spec:
  data: |
    version: "0.1"
    name: nexus-talos-install
    global_timeout: 3600
    tasks:
      - name: install-talos
        worker: "{{.device_1}}"
        volumes: [ "/dev:/dev", "/statedir:/statedir" ]
        actions:

          # ── GUARD 1: confirm we are on the machine we think we are ──
          - name: verify-identity
            image: harbor.nexus.internal/nexus/guard:0.2.0
            timeout: 60
            environment:
              EXPECT_MAC:    "{{.expected_mac}}"
              EXPECT_SERIAL: "{{.expected_board_serial}}"
            # Exits non-zero (aborting the workflow) if either does not match.

          # ── GUARD 2: confirm the target disk by SERIAL, not path ──
          - name: verify-disk
            image: harbor.nexus.internal/nexus/guard:0.2.0
            command: ["/bin/verify-disk"]
            timeout: 60
            environment:
              EXPECT_DISK_SERIAL: "{{.boot_disk_serial}}"
              DEST_DISK: "{{.dest_disk}}"

          # ── ⚠️ DESTRUCTIVE FROM HERE ──
          - name: wipe-partition-table
            image: quay.io/tinkerbell/actions/wipefs:latest
            timeout: 120
            environment: { DEST_DISK: "{{.dest_disk}}" }

          - name: write-talos-image
            image: quay.io/tinkerbell/actions/image2disk:latest
            timeout: 1200
            environment:
              DEST_DISK: "{{.dest_disk}}"
              IMG_URL: "http://10.100.0.10:8080/talos/{{.schematic_id}}/metal-amd64.raw.zst"
              COMPRESSED: "true"

          - name: reboot
            image: quay.io/tinkerbell/actions/reboot:latest
            timeout: 90
            pid: host
```

> 💡 **Why two guards:** the single most expensive provisioning mistake is imaging the wrong machine or the wrong disk. A MAC can be reassigned; a device path can shift between boots. Board serial + disk serial are stable identifiers. **Both guards abort the workflow rather than proceeding on a mismatch.**

---

### Task 5 — The discovery agent

A container image that runs Phase 01's `collect.sh` inside HookOS and POSTs the result.

```dockerfile
# images/discovery-agent/Dockerfile
FROM debian:12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      pciutils nvme-cli dmidecode numactl ethtool lshw smartmontools \
      hwloc-nox jq curl iproute2 usbutils \
    && rm -rf /var/lib/apt/lists/*
COPY collect.sh /usr/local/bin/collect.sh
COPY report.sh  /usr/local/bin/report.sh
COPY report-complete /bin/report-complete
RUN chmod +x /usr/local/bin/*.sh /bin/report-complete
CMD ["/usr/local/bin/report.sh"]
```

`report.sh` runs `collect.sh`, wraps the output in JSON with the MAC and a timestamp, and POSTs to the seed node's collector endpoint. The collector (a small service on the seed node) writes to `evidence/phase-08/discovery/<mac>.json` and generates a **draft** NodeSpec for human review.

> 🚫 **The discovery agent must never write to a block device.** Review the image's contents; it contains no `dd`, `mkfs`, `sgdisk`, or `wipefs`. Add a test that greps for those binaries and fails the build if present.

---

### Task 6 — Safety interlocks

**This is the most important task in the phase.** Build these before running any destructive workflow.

| # | Interlock | Implementation | Prevents |
|---|---|---|---|
| **I1** | `allowPXE: false` by default on every Hardware CR | Generated that way | Accidental netboot of an unapproved machine |
| **I2** | `allowWorkflow: false` by default | Same | Accidental workflow execution |
| **I3** | An explicit approval step flips both to `true` | `tools/provisioning/approve-node.sh` | Requires deliberate human/automated intent |
| **I4** | Guard action verifying board serial | Workflow action, aborts on mismatch | Imaging the wrong machine |
| **I5** | Guard action verifying disk serial | Workflow action, aborts on mismatch | Wiping the wrong disk |
| **I6** | Unknown MACs boot **discovery only**, never install | iPXE template branches on the Hardware CR's existence | Wiping a visitor's laptop that netbooted by accident |
| **I7** | The destructive `wipe.yaml` template is never referenced by automation | Manual `kubectl create` only, with a typed confirmation | Accidental fleet wipe |
| **I8** | Approval is logged with operator, timestamp, and reason | `approve-node.sh` appends to `evidence/phase-08/approvals.log` | No audit trail |
| **I9** | A dry-run mode prints exactly what would be wiped | `provision.sh --dry-run` | Blind execution |

**`tools/provisioning/approve-node.sh`**
```bash
#!/usr/bin/env bash
# Approve exactly one node for destructive provisioning.
source "$(dirname "$0")/../lib/common.sh"
NODE="${1:?node name}"; REASON="${2:?reason}"
SPEC="$ROOT/inventory/nodes/$NODE.yaml"
[[ -f "$SPEC" ]] || die "unknown node: $NODE"

echo "═══════════════════════════════════════════════════════════════"
echo "  ⚠️  DESTRUCTIVE PROVISIONING APPROVAL"
echo "  Node:        $NODE"
echo "  MAC:         $(yq -r '.spec.nics[]|select(.role=="management")|.macAddress' "$SPEC")"
echo "  Rack/U:      $(yq -r '.spec.location.rack' "$SPEC")/$(yq -r '.spec.location.u' "$SPEC")"
echo "  Boot disk:   $(yq -r '.spec.storage[]|select(.role=="boot")|.device' "$SPEC")"
echo "  Disk serial: $(yq -r '.spec.storage[]|select(.role=="boot")|.serial' "$SPEC")"
echo "  ALL DATA ON THIS DISK WILL BE DESTROYED."
echo "═══════════════════════════════════════════════════════════════"
read -rp "Type the node name to confirm: " confirm
[[ "$confirm" == "$NODE" ]] || die "confirmation did not match — aborted"

kubectl -n tink-system patch hardware "$NODE" --type=merge \
  -p '{"spec":{"interfaces":[{"netboot":{"allowPXE":true,"allowWorkflow":true}}]}}'
kubectl -n tink-system label hardware "$NODE" nexus.io/approved=true --overwrite

printf '%s\t%s\t%s\t%s\n' "$(date -Is)" "${USER:-unknown}" "$NODE" "$REASON" \
  >> "$ROOT/evidence/phase-08/approvals.log"
ok "approved $NODE"
```

---

### Task 7 — The dynamic iPXE script

Smee generates this per-MAC. The branching logic is the safety boundary I6.

```ipxe
#!ipxe
# NEXUS boot dispatcher
set mac ${net0/mac}
echo NEXUS :: MAC ${mac} :: UUID ${uuid}

# Ask the seed node what to do with this MAC
chain --autofree http://10.100.0.10:8080/api/boot?mac=${mac} || goto discovery

:discovery
echo No approved workflow. Booting DISCOVERY (read-only).
kernel http://10.100.0.10:8080/hook/vmlinuz-x86_64 \
  ip=dhcp modules=loop,squashfs console=tty0 console=ttyS0,115200 \
  tink_worker_image=harbor.nexus.internal/nexus/discovery-agent:0.3.0 \
  syslog_host=10.100.0.10 grpc_authority=10.100.0.10:42113 \
  tinkerbell_tls=false worker_id=${mac}
initrd http://10.100.0.10:8080/hook/initramfs-x86_64
boot || goto failed

:failed
echo Boot failed. Halting for 60s then rebooting.
sleep 60
reboot
```

The `/api/boot` endpoint returns an install iPXE script **only** if the Hardware CR exists, `allowPXE` is true, and `allowWorkflow` is true. Otherwise it returns 404 and the node falls through to discovery.

---

### Task 8 — End-to-end provisioning run

```bash
# 1. Cold machine, unknown MAC
tools/power/power-ctl.sh on nx-c-r01-05
#    → DHCP → iPXE → /api/boot returns 404 → DISCOVERY boots
#    → agent POSTs facts → draft NodeSpec at evidence/phase-08/discovery/<mac>.json

# 2. Review and merge the draft into inventory/nodes/, then:
task validate:inventory
tools/provisioning/gen-hardware.sh
kubectl apply -f bootstrap/tinkerbell/hardware/nx-c-r01-05.yaml

# 3. Dry run
tools/provisioning/provision.sh --dry-run nx-c-r01-05
#    Prints: target MAC, board serial, disk path AND serial, image URL, actions.

# 4. Approve (typed confirmation)
tools/provisioning/approve-node.sh nx-c-r01-05 "initial M1 build"

# 5. Provision
time tools/provisioning/provision.sh nx-c-r01-05
#    → power cycle → PXE → install workflow → guards pass → image written → reboot
#    → Talos boots into MAINTENANCE mode, listening on :50000

# 6. Verify
talosctl --nodes 10.100.0.105 --insecure version
#    Expect: the Talos version, "maintenance mode"
```

📊 **Record the wall-clock time from power-on to maintenance mode.** Gate G2 requires < 15 minutes. Typical breakdown: 30 s POST, 20 s PXE+iPXE, 60 s HookOS boot, 3–8 min image write (size- and disk-dependent), 30 s reboot, 15 s Talos boot.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Exactly one DHCP server on the VLAN | `nmap --script broadcast-dhcp-discover` | One responder |
| **A2** | Hardware CRs generated from inventory for every node | `kubectl -n tink-system get hardware \| wc -l` | Equals node count |
| **A3** | All Hardware CRs default to `allowPXE: false` | `kubectl get hardware -o json \| jq '...allowPXE'` | All false before approval |
| **A4** | WoL powers on a cold node | `power-ctl.sh on`, observe POST | Node powers on |
| **A5** | PDU outlet toggle powers a node off and on | `power-ctl.sh cycle` | Works |
| **A6** | Graceful shutdown is attempted before forcing | Read the script; test on a live node | Graceful path taken first |
| **A7** | Power status distinguishes "off" from "dead" using outlet current | `power-ctl.sh status` on a powered-off node | Reports 0 A |
| **A8** | An unknown MAC boots discovery, not install | Boot a machine with no Hardware CR | Discovery only; **no disk written** |
| **A9** | Discovery agent contains no disk-writing tools | Grep the image for `dd`, `mkfs*`, `wipefs`, `sgdisk` | None present |
| **A10** | Discovery produces a valid draft NodeSpec | Inspect the JSON and the generated YAML | Schema-valid after review |
| **A11** | Guard I4 aborts on a board-serial mismatch | Deliberately set the wrong expected serial | Workflow fails **before** any write |
| **A12** | Guard I5 aborts on a disk-serial mismatch | Same for disk | Workflow fails before any write |
| **A13** | Approval requires typed confirmation | Run `approve-node.sh`, type the wrong name | Aborts |
| **A14** | Approvals are logged with operator, time, and reason | `cat evidence/phase-08/approvals.log` | Entries present |
| **A15** | `--dry-run` prints the full plan without acting | Run it | No workflow created |
| **A16** | End-to-end provisioning succeeds | Provision one node | Talos in maintenance mode |
| **A17** | **Time from power-on to maintenance mode < 15 min** | Timed run | Recorded (feeds G2 in Phase 11) |
| **A18** | Re-running provisioning on the same node is idempotent | Run it twice | Same result; no error |
| **A19** | Provisioning three nodes in parallel works | Run concurrently | All three succeed |
| **A20** | Power-control runbook documents the 4-step escalation ladder | Read it | Present, matches Phase 02 |

---

## ↩️ ROLLBACK

```bash
# Disable all netboot immediately (the emergency stop)
kubectl -n tink-system get hardware -o name | xargs -I{} kubectl -n tink-system patch {} \
  --type=merge -p '{"spec":{"interfaces":[{"netboot":{"allowPXE":false,"allowWorkflow":false}}]}}'

# Stop the stack
helm -n tink-system uninstall tink-stack

# Cancel an in-flight workflow
kubectl -n tink-system delete workflow <name>
```

⚠️ **A workflow that has already begun `wipe-partition-table` cannot be undone.** The data is gone. The interlocks exist precisely because this rollback does not.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Node PXEs but iPXE gets 404 from `/api/boot` | Hardware CR missing, or not approved | Expected for an unapproved node — it falls through to discovery. Approve it if intended. |
| Workflow stays `PENDING` forever | `allowWorkflow: false`, or the worker ID does not match | Check the Hardware CR; the worker ID must equal the MAC used at boot |
| HookOS boots but the agent never connects to Tink | `grpc_authority` wrong, or firewall blocking 42113 | Check kernel cmdline; open the port |
| `image2disk` fails partway | Network interruption or a bad image URL | Verify the URL with `curl -I`; check the image checksum; the workflow is safe to retry |
| Node reboots into HookOS again after install | BIOS boot order still PXE-first with no fallback, or the install did not write a bootloader | Expected: PXE-first is correct. `/api/boot` must return 404 after install so it falls through to local disk. Verify the endpoint's post-install behavior. |
| WoL does not wake the node | ErP/deep-sleep on, WoL disabled, or the magic packet is not reaching the subnet | Phase 01 BIOS checklist. Send to the **subnet broadcast** (`-i 10.100.3.255`), not the node IP. |
| PDU SNMP set works but the outlet does not change | Wrong OID, or write access not granted to the SNMPv3 user | Check the vendor MIB; verify the user's access level |
| Two machines received the same IP | Duplicate MAC in inventory, or a stale lease | `task validate:inventory` catches duplicates (Phase 00 check C3); clear the lease file |
| Provisioning is very slow (> 20 min) | Image transfer over a 1 GbE management link | Expected. Serve the image over the cluster VLAN (9000 MTU) if the node has it up, or accept the one-time cost. |
| A guard action passes when it should fail | Guard logic inverted, or the variable is empty | Test the guard explicitly (A11/A12). An empty expected value must **fail**, not pass. |

---

## 🚫 DO NOT

- **Do not** run a destructive workflow before the guards (I4, I5) are tested and proven to abort.
- **Do not** run two DHCP servers. Choose dnsmasq or Smee, not both.
- **Do not** set `allowPXE: true` by default in the generator.
- **Do not** identify the target disk by device path alone. Use the serial.
- **Do not** point the discovery workflow at any action that can write to a block device.
- **Do not** write Talos machine configurations here. That is Phase 09 — this phase only gets machines into maintenance mode.
- **Do not** bootstrap Kubernetes. That is Phase 12.
- **Do not** repurpose the bootstrap k3s as the NEXUS cluster.
- **Do not** skip the parallel-provisioning test. At 100 nodes you will provision in batches; discovering a race condition then is expensive.

---

## 📤 HANDOFF

`evidence/phase-08/handoff.md` must state:

1. **Which DHCP server owns the VLAN** (dnsmasq or Smee) and where its config lives.
2. **Tinkerbell deployment path** (k3s or plain containers) and how to reach its API.
3. **The power-control interface** — `power-ctl.sh` usage, the PDU OIDs, and where credentials come from. Phase 32's power controller and Phase 23's auto-remediation both call this.
4. **Measured provisioning time** per node, and the parallel-provisioning limit you found.
5. **The approval procedure** — Phase 11 and Phase 53 (node replacement) both use it.
6. **The Talos schematic ID placeholder** — Phase 09 fills in the real one and updates `talos-install.yaml`.
7. **Nodes that failed to PXE or WoL**, with the reason (BIOS, NIC, cabling) — these need physical attention.
8. **Guard-test evidence** — proof that I4 and I5 abort correctly.

---

## ➡️ NEXT

**[PHASE-09 — Talos Linux OS Layer](PHASE-09.md)** — build the machine-config hierarchy, the Image Factory schematics with NVIDIA extensions, and the kernel tuning that every compute node inherits.
