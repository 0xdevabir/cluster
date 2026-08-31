# PHASE 06 — Seed Node & Workstation Toolchain

| | |
|---|---|
| **Stage** | 1 — Bootstrap Infrastructure |
| **Estimated effort** | 2–3 hours |
| **Depends on** | 05 (gate GO) |
| **Blocks** | 07, 08, 09, 10, 11 |
| **Risk** | 🟡 Medium — the seed node becomes a dependency for provisioning; a lost seed node stalls the build |
| **Blast radius** | The provisioning pipeline (not the running cluster, once it exists) |
| **Architecture refs** | `ARCHITECTURE.md#l3--provisioning--os-layer`, `#0.2` (management plane) |

---

## 🎯 MISSION

Build the **seed node** — the one machine from which the entire cluster is provisioned — and establish the operator workstation toolchain. When this phase completes, you can reach the management network, run every pinned CLI, and hold the cluster's root of trust safely.

> 💡 **WHY a dedicated seed node rather than "just use a laptop":** the provisioning services (DHCP, TFTP, PXE, the Tinkerbell stack) must be **always on and always at a known address** on the management VLAN. A laptop that sleeps, roams, or leaves the building breaks node provisioning at the worst moment. The seed node is small, boring, and permanent.
>
> 💡 **WHY it must not become permanent infrastructure:** after Phase 15, the cluster manages itself via GitOps. The seed node's job shrinks to bootstrap and disaster recovery. Design it so the cluster does **not** depend on it at runtime (`ARCHITECTURE.md#0.2` — the management plane fails independently).

---

## ✅ PREFLIGHT

```bash
# 1. Stage 0 gate passed
grep -E "^\[x\] GO" docs/capacity/gate-decision.md && echo "GATE OK"

# 2. The management network design exists
yq -r '.spec.networks.management' inventory/network/ip-plan.yaml

# 3. You have a machine for the seed role (see Task 1 for spec)

# 4. You have physical or network access to the management VLAN
```

---

## 📦 DELIVERABLES

```
bootstrap/seed/
  README.md                      # what the seed node is, and what it is NOT
  install.sh                     # idempotent seed-node setup
  network.md                     # the seed node's interfaces and addresses
  services.md                    # what runs here, and when each is decommissioned
  backup.md                      # how to rebuild the seed node from scratch
docs/operations/
  workstation-setup.md           # for every operator, not just the builder
  access-model.md                # VPN, jump host, who can reach what
tools/
  verify-tools.sh                # completes the Phase 00 stub
  seed-preflight.sh              # asserts the seed node is correctly configured
inventory/nodes/nx-seed-01.yaml  # the seed node IS inventory
evidence/phase-06/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

Extends the Phase 00 table. Install on the seed node **and** on every operator workstation.

| Component | Version | Role |
|---|---|---|
| Ubuntu Server LTS (or Debian stable) | `24.04` | Seed node OS — deliberately boring, long support |
| Docker Engine | `27.3.1` | Runs the bootstrap services before Kubernetes exists |
| docker-compose plugin | `2.29.7` | Declarative bootstrap service stack |
| WireGuard | distro | Operator VPN |
| `step-cli` | `0.28.2` | PKI operations (Phase 07) |
| `nmap` / `iperf3` / `tcpdump` / `ethtool` / `ipmitool` | distro | Network diagnosis |
| `snmpwalk` (net-snmp) | distro | PDU control (Phase 08) |
| `wakeonlan` / `etherwake` | distro | WoL (Phase 08) |
| Everything in `.mise.toml` | per Phase 00 | Cluster CLIs |

---

## 📋 TASKS

### Task 1 — Choose and prepare the seed node

**Specification** (deliberately modest — this is not a compute node):

| Component | Minimum | Why |
|---|---|---|
| CPU | 4 cores | Runs a handful of containers |
| RAM | 16 GB | Tinkerbell + registry mirror + observability seed |
| Disk | 500 GB SSD | OS images are large: Talos images, container layers, discovery images |
| NICs | **2×**: one on the management VLAN (100), one on the cluster VLAN (200) | It must reach both planes |
| Power | **On the UPS** | It is needed during a recovery, which is exactly when power is unreliable |
| Redundancy | None required, but **fully rebuildable in < 1 hour** | See Task 7 |

> ⚠️ **The seed node must not be a cluster node.** Do not plan to "convert it into a worker later." Its job during a disaster is to reprovision the cluster; it cannot do that if it is part of the thing that failed.

**Record it in inventory** as `nx-seed-01` with `archetype: infra` and a note that it is outside the Kubernetes cluster. It still gets a rack position, a PDU outlet, and a switch port like everything else — otherwise it becomes the undocumented machine under someone's desk.

---

### Task 2 — Base OS installation

```bash
# Install Ubuntu Server 24.04 LTS, minimal, no desktop.
# Partitioning: single root ext4/xfs, no LVM complexity needed.
# Enable: OpenSSH server. Nothing else.

# Post-install hardening (bootstrap/seed/install.sh, idempotent)
set -euo pipefail

# 1. Unattended security updates only — never unattended feature upgrades
sudo apt-get update
sudo apt-get install -y unattended-upgrades
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

# 2. SSH: keys only, no root, no passwords
sudo install -m 0644 /dev/stdin /etc/ssh/sshd_config.d/99-nexus.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AllowGroups nexus-operators
X11Forwarding no
ClientAliveInterval 300
EOF
sudo systemctl reload ssh

# 3. Firewall: default deny inbound, allow only what the seed node serves
sudo apt-get install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow in on wg0                       # VPN
sudo ufw allow in on <mgmt-iface> to any port 22   proto tcp   # SSH from mgmt only
sudo ufw allow in on <mgmt-iface> to any port 67   proto udp   # DHCP  (Phase 07)
sudo ufw allow in on <mgmt-iface> to any port 69   proto udp   # TFTP  (Phase 07)
sudo ufw allow in on <mgmt-iface> to any port 53                # DNS   (Phase 07)
sudo ufw allow in on <mgmt-iface> to any port 123  proto udp   # NTP   (Phase 07)
sudo ufw allow in on <mgmt-iface> to any port 8080 proto tcp   # HTTP boot artifacts
sudo ufw --force enable

# 4. Time sync — everything downstream depends on it
sudo apt-get install -y chrony
# (configured properly in Phase 07; for now, upstream NTP is fine)

# 5. Kernel/sysctl for a service host
sudo install -m 0644 /dev/stdin /etc/sysctl.d/99-nexus-seed.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv4.conf.all.rp_filter = 2
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
fs.inotify.max_user_watches = 524288
fs.inotify.max_user_instances = 512
EOF
sudo sysctl --system
```

> 💡 **`ip_forward = 1`** because the seed node will NAT the management VLAN to the internet during provisioning (nodes need to fetch images before Harbor exists). Turn it off after Phase 42 if you want a fully isolated management plane.

---

### Task 3 — Network configuration

The seed node sits on both the management and cluster networks (`ARCHITECTURE.md#0.2`).

```yaml
# /etc/netplan/01-nexus.yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    <mgmt-iface>:
      addresses: [10.100.0.10/22]        # from ip-plan.yaml
      mtu: 1500                          # management network is 1500 (PXE)
      nameservers: { addresses: [127.0.0.1] }   # it serves its own DNS (Phase 07)
    <cluster-iface>:
      addresses: [10.200.0.5/26]
      mtu: 9000                          # jumbo on the data network
      routes:
        - to: default
          via: <upstream-gw>
```

```bash
sudo netplan apply

# Verify BOTH MTUs — a mismatch here is a subtle, painful bug
ip link show <mgmt-iface>    | grep 'mtu 1500'
ip link show <cluster-iface> | grep 'mtu 9000'

# Verify jumbo frames actually work end-to-end (not just locally configured)
ping -M do -s 8972 -c 3 <another-host-on-vlan-200>
#     ↑ do-not-fragment, 8972 = 9000 − 28 bytes of IP+ICMP header
# If this fails, some switch port in the path is not configured for jumbo.
```

📊 **Record the jumbo-frame test result.** It is the first real validation of the Phase 03 design, and MTU mismatches are the single most common cause of "the network works but is mysteriously slow."

---

### Task 4 — NAT for the management network

Until Harbor exists (Phase 42), nodes need outbound internet to pull images.

```bash
# nftables NAT, management VLAN → upstream
sudo apt-get install -y nftables
sudo install -m 0644 /dev/stdin /etc/nftables.conf <<'EOF'
#!/usr/sbin/nft -f
flush ruleset
table inet nat {
  chain postrouting {
    type nat hook postrouting priority srcnat;
    ip saddr 10.100.0.0/22 oifname "<cluster-iface>" masquerade
  }
}
EOF
sudo systemctl enable --now nftables
```

> 🚫 **Do not NAT the cluster VLAN (200) through the seed node.** Cluster nodes reach the internet through the real network gateway. Routing production traffic through the seed node makes it a runtime dependency — exactly what the mission forbids.

---

### Task 5 — Operator access model

**`docs/operations/access-model.md`**

```
                    ┌──────────────┐
   Operator laptop ─┤  WireGuard   ├─► seed node (10.100.0.10)
                    └──────────────┘        │
                                            ├─► management VLAN 100
                                            │     · switch mgmt IPs
                                            │     · PDUs (SNMPv3)
                                            │     · PiKVM
                                            │     · Talos maintenance API :50000
                                            │
                                            └─► cluster VLAN 200
                                                  · Talos API :50000
                                                  · Kubernetes API :6443

  Rules:
   · No operator connects directly to the management VLAN. VPN → seed node only.
   · The seed node is the only jump point. All access is logged there.
   · Operator WireGuard keys are per-person and revocable.
   · The kubeconfig and talosconfig NEVER live on a laptop unencrypted —
     they live on the seed node, or in an encrypted store the operator unlocks.
```

**WireGuard setup:**
```bash
sudo apt-get install -y wireguard
umask 077
wg genkey | sudo tee /etc/wireguard/server.key | wg pubkey | sudo tee /etc/wireguard/server.pub

sudo install -m 0600 /dev/stdin /etc/wireguard/wg0.conf <<'EOF'
[Interface]
Address = 10.99.0.1/24
ListenPort = 51820
PrivateKey = <server.key contents>

# One [Peer] block per operator — never share a key
[Peer]
# operator: <name>
PublicKey  = <operator public key>
AllowedIPs = 10.99.0.2/32
EOF

sudo systemctl enable --now wg-quick@wg0
```

🔒 **SECRET:** WireGuard private keys are secrets. They are not committed. Record only the *public* keys and the peer-to-person mapping in `docs/operations/access-model.md`.

---

### Task 6 — Toolchain installation & verification

```bash
# mise from Phase 00's .mise.toml
curl -fsSL https://mise.run | sh          # pin the installer version if air-gapped
eval "$(~/.local/bin/mise activate bash)"
cd /opt/nexus && mise install
task tools:verify                          # must pass
```

**Complete `tools/verify-tools.sh`** (stubbed in Phase 00):

```bash
#!/usr/bin/env bash
# Assert every pinned tool is present at the pinned version.
source "$(dirname "$0")/lib/common.sh"
need yq

fail=0
check() {                    # check <binary> <version-flag> <expected>
  local bin="$1" flag="$2" want="$3" got
  if ! command -v "$bin" >/dev/null; then warn "MISSING: $bin"; fail=1; return; fi
  got="$("$bin" $flag 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
  if [[ "$got" == "$want" ]]; then ok "$bin $got"
  else warn "VERSION MISMATCH: $bin want=$want got=$got"; fail=1; fi
}

check kubectl   "version --client -o yaml" "1.34.1"
check helm      "version --short"          "3.16.2"
check talosctl  "version --client --short" "1.9.5"
check yq        "--version"                "4.44.3"
check sops      "--version"                "3.9.1"
check age       "--version"                "1.2.0"
# ... one line per pinned tool

[[ $fail -eq 0 ]] && ok "all tools verified" || die "toolchain verification FAILED"
```

**`docs/operations/workstation-setup.md`** — the same procedure for every operator, so a second person can join without archaeology. Include: mise install, repo clone, WireGuard config request process, SSH key registration, and how to obtain (not copy) a kubeconfig.

---

### Task 7 — Seed node rebuild procedure

**`bootstrap/seed/backup.md`.** The seed node is a single point of failure for *provisioning*. Make it a cheap one.

| What must survive | Where it lives | Recovery |
|---|---|---|
| The Git repository | Remote (GitHub/Gitea) + a local bare mirror on the seed node | `git clone` |
| The age private key (bootstrap root of trust) | **Offline, two physical locations** (Phase 04/A12) | Manual, ceremonial |
| Talos machine secrets | SOPS-encrypted in Git | Decrypt with the age key |
| WireGuard server key | Encrypted backup + printed in the safe | Restore or regenerate (peers must update) |
| Downloaded OS/boot images | Regenerable from Talos Image Factory | Re-download (or keep an offline copy if air-gapped) |
| Docker volumes for bootstrap services | Regenerable from `docker-compose.yaml` in Git | `docker compose up` |
| The `install.sh` script and configs | Git | Re-run |

**Test the rebuild** — this is the acceptance criterion, not a suggestion:
```bash
# On a spare machine or a VM:
#   1. Install Ubuntu 24.04
#   2. git clone <repo> /opt/nexus
#   3. sudo bash /opt/nexus/bootstrap/seed/install.sh
#   4. Restore the age key from offline custody
#   5. tools/seed-preflight.sh must pass
# Target: < 1 hour, fully documented, no undocumented step.
```

---

### Task 8 — Seed preflight script

**`tools/seed-preflight.sh`** — one command that says whether the seed node is ready to provision. Every later Stage-1 phase runs it first.

```bash
#!/usr/bin/env bash
source "$(dirname "$0")/lib/common.sh"
fail=0
chk() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else warn "FAIL: $1"; fail=1; fi; }

chk "management interface has the planned IP"  "ip -4 addr show | grep -q 10.100.0.10"
chk "cluster interface has the planned IP"     "ip -4 addr show | grep -q 10.200.0.5"
chk "management MTU is 1500"                   "ip link show <mgmt-iface> | grep -q 'mtu 1500'"
chk "cluster MTU is 9000"                      "ip link show <cluster-iface> | grep -q 'mtu 9000'"
chk "jumbo frames work on the cluster VLAN"    "ping -M do -s 8972 -c1 -W2 <peer>"
chk "IP forwarding enabled"                    "[ \$(sysctl -n net.ipv4.ip_forward) = 1 ]"
chk "NAT rule present"                         "nft list ruleset | grep -q masquerade"
chk "chrony is synchronized"                   "chronyc tracking | grep -q 'Leap status.*Normal'"
chk "docker is running"                        "docker info"
chk "WireGuard is up"                          "wg show wg0"
chk "firewall is active"                       "ufw status | grep -q 'Status: active'"
chk "toolchain verified"                       "bash tools/verify-tools.sh"
chk "repo is clean and current"                "git -C /opt/nexus diff --quiet"
chk "can reach the internet"                   "curl -sSfI https://github.com"
chk "can reach every switch mgmt IP"           "bash tools/ping-switches.sh"
chk "can reach every PDU"                      "bash tools/ping-pdus.sh"

[[ $fail -eq 0 ]] && ok "SEED NODE READY" || die "seed node preflight FAILED"
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Seed node is recorded in inventory and schema-valid | `task validate:inventory` | `nx-seed-01.yaml` present and valid |
| **A2** | Both interfaces have the planned IPs and MTUs | `tools/seed-preflight.sh` | Pass |
| **A3** | Jumbo frames verified end-to-end on VLAN 200 | `ping -M do -s 8972` | 0 % loss |
| **A4** | Management VLAN MTU is 1500 | Preflight | Pass |
| **A5** | SSH allows keys only; no root; no passwords | `sshd -T \| grep -E 'permitrootlogin\|passwordauthentication'` | `no` for both |
| **A6** | Firewall is active with only the intended ports open | `ufw status verbose` | Matches Task 2's list |
| **A7** | WireGuard is up with ≥ 1 operator peer that can reach the mgmt VLAN | Connect from a laptop, `ping 10.100.1.1` | Reachable |
| **A8** | The seed node can reach every switch and PDU management IP | Preflight | 100 % |
| **A9** | NAT works: a host on VLAN 100 can reach the internet | From a test host, `curl -I https://github.com` | 200/301 |
| **A10** | Every pinned tool is installed at the exact pinned version | `task tools:verify` | Exit 0 |
| **A11** | Time is synchronized | `chronyc tracking` | Leap status Normal, offset < 50 ms |
| **A12** | **Seed node rebuild tested from scratch in < 1 h** | Perform it on a VM; record timing | Documented, timed, reproducible |
| **A13** | Access model documented; no direct operator access to the mgmt VLAN | Read `access-model.md`; verify by attempting a direct connection | Direct access blocked |
| **A14** | No secrets committed | `gitleaks detect` | Clean |
| **A15** | `seed-preflight.sh` passes completely | Run it | Exit 0 |

---

## ↩️ ROLLBACK

```bash
# Network changes
sudo netplan apply --rollback           # or restore the previous /etc/netplan/*.yaml
# Firewall
sudo ufw --force reset
# WireGuard
sudo systemctl disable --now wg-quick@wg0
# NAT
sudo systemctl disable --now nftables
```
The seed node holds no cluster state at this point; reimaging it is always safe.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Jumbo ping fails | A switch port in the path is not set to jumbo, or the peer's MTU is 1500 | Check every hop. `tracepath <peer>` reports the path MTU. Fix the switch port (Phase 03 template sets `mtu 9216`). |
| `netplan apply` locks you out | Wrong interface name or a typo in the address | Use `netplan try` (auto-reverts after 120 s) instead of `apply` for remote changes. Always. |
| DHCP from the seed node is not seen by nodes | It is on a different VLAN, or a DHCP relay is missing | The seed node must have an interface **in** VLAN 100, or the switch must relay DHCP to it |
| `mise install` fails behind a proxy | No proxy configuration | `export HTTPS_PROXY=...` and add it to `/etc/environment` |
| chrony will not sync | Firewall blocks outbound UDP 123, or the upstream is unreachable | `chronyc sources -v`; open egress; consider a local GPS/PPS source if air-gapped |
| WireGuard connects but cannot reach the mgmt VLAN | `ip_forward` off, or missing route on the client | Enable forwarding; the client's `AllowedIPs` must include `10.100.0.0/22` |
| UFW blocks Docker container traffic | Docker writes its own iptables rules that bypass UFW | Known interaction. Either use `nftables` exclusively, or set `iptables: false` in `/etc/docker/daemon.json` and manage rules yourself. **Verify with an actual port scan from another host.** |
| Two default routes (mgmt + cluster) | Both interfaces got a gateway | Only the cluster interface should carry the default route. Remove it from mgmt. |

---

## 🚫 DO NOT

- **Do not** install Tinkerbell, DHCP, DNS, or PKI services yet. That is Phase 07.
- **Do not** install Kubernetes on the seed node. It is not a cluster member.
- **Do not** plan to convert the seed node into a worker later. It must survive the cluster's failure.
- **Do not** route cluster (VLAN 200) traffic through the seed node. Only the management VLAN is NATed.
- **Do not** store the age private key, kubeconfig, or talosconfig on an operator laptop.
- **Do not** open the management VLAN to the corporate LAN "for convenience." VPN + jump host, per Phase 04.
- **Do not** skip the rebuild test. An untested recovery procedure is a fiction.

---

## 📤 HANDOFF

`evidence/phase-06/handoff.md` must state:

1. **Seed node address on both networks**, and the interface names — Phase 07's DHCP/DNS/TFTP configs bind to these.
2. **NAT status** and whether nodes have internet during provisioning (changes Phase 08/09 image-fetch strategy).
3. **WireGuard server public key and endpoint**, plus the peer-onboarding procedure.
4. **Jumbo-frame verification result** — if it failed, Phase 21 will fail too; fix it now.
5. **Toolchain deviations** — any version substitutions.
6. **Rebuild test result** — timing and any undocumented steps discovered.
7. **Where the age key custody is** (by description of the process, never the key).
8. **Whether the environment is air-gapped**, and if so, where the offline image mirror lives.

---

## ➡️ NEXT

**[PHASE-07 — Core Network Services (DHCP/DNS/NTP/PKI)](PHASE-07.md)** — stand up the services every node needs before it can exist.
