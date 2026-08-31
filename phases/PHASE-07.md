# PHASE 07 — Core Network Services (DHCP / DNS / NTP / PKI)

| | |
|---|---|
| **Stage** | 1 — Bootstrap Infrastructure |
| **Estimated effort** | 3–4 hours |
| **Depends on** | 03, 06 |
| **Blocks** | 08, 09, 11, 12, 17 |
| **Risk** | 🟠 High — a rogue DHCP server on a shared network breaks other people's machines |
| **Blast radius** | Every device on the management VLAN |
| **Architecture refs** | `ARCHITECTURE.md#l3--provisioning--os-layer`, `#x1--security-architecture` (PKI), Phase 04 `pki-hierarchy.md` |

---

## 🎯 MISSION

Stand up the four services that must exist **before any node can boot**: DHCP with PXE options, DNS (internal zone + upstream forwarding), NTP (accurate, cluster-local), and the Platform Intermediate CA. Everything downstream — netboot, Talos, etcd, TLS, Kubernetes — depends on these four being correct.

> ⚠️ **DANGER — rogue DHCP.** If the management VLAN is not fully isolated, a DHCP server here will answer requests from machines that are not yours. Before enabling DHCP: confirm VLAN 100 is isolated (Phase 03), and run `nmap --script broadcast-dhcp-discover` to prove no other DHCP server is already answering.

> 💡 **WHY time before everything:** etcd's Raft, Kubernetes certificates, TLS validation, Ceph's clock-skew detection, and every distributed-systems assumption in the stack depend on synchronized clocks. A 5-minute skew makes certificates invalid and etcd unstable, and the resulting symptoms look like a dozen unrelated bugs.

---

## ✅ PREFLIGHT

```bash
bash tools/seed-preflight.sh          # Phase 06 must pass

# Network design available
yq -r '.spec.networks.management' inventory/network/ip-plan.yaml

# ⚠️ CRITICAL: prove no other DHCP server answers on this VLAN
sudo nmap --script broadcast-dhcp-discover -e <mgmt-iface>
# Expected: no offers. If ANY server responds, STOP — VLAN 100 is not isolated.

# Confirm the VLAN is isolated from the corporate LAN
sudo tcpdump -i <mgmt-iface> -n -c 50 not arp and not stp
# Expected: only your own traffic
```

**If another DHCP server responds, write `evidence/phase-07/BLOCKED.md` and fix the VLAN isolation first.** Do not proceed.

---

## 📦 DELIVERABLES

```
bootstrap/services/
  docker-compose.yaml            # the whole bootstrap stack, declarative
  .env.example
  dnsmasq/
    dnsmasq.conf                 # DHCP + TFTP + DNS-forward
    hosts.d/                     # static A records, generated from inventory
    dhcp-hosts.d/                # MAC → IP reservations, generated from inventory
  coredns/
    Corefile
    zones/nexus.internal.zone
  chrony/chrony.conf
  step-ca/
    ca.json
    provisioners.md
tools/
  gen-dhcp-reservations.sh       # inventory/ → dnsmasq dhcp-hosts
  gen-dns-records.sh             # inventory/ → zone file
  net-services-check.sh          # end-to-end validation
docs/operations/
  dns-naming.md
  pki-operations.md              # issuing, renewing, revoking
evidence/phase-07/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Image | Role |
|---|---|---|---|
| dnsmasq | `2.90` | `docker.io/jpillora/dnsmasq:1.1.0` or distro package | DHCP + TFTP + PXE options |
| CoreDNS | `1.12.0` | `docker.io/coredns/coredns:1.12.0` | Authoritative internal DNS |
| chrony | `4.5` | distro package on the host | NTP server |
| step-ca | `0.28.2` | `docker.io/smallstep/step-ca:0.28.2` | Platform Intermediate CA |
| step-cli | `0.28.2` | host binary | CA operations |

> 💡 **Why dnsmasq for DHCP but CoreDNS for DNS:** dnsmasq's DHCP+TFTP+PXE combination is the simplest reliable netboot server, and Tinkerbell's Smee can replace it later. CoreDNS is the same DNS engine Kubernetes uses, so the zone file and behavior stay consistent from bootstrap to production. Running dnsmasq's DNS *and* CoreDNS on port 53 will conflict — **dnsmasq DNS is disabled** (`port=0`).

---

## 📋 TASKS

### Task 1 — DNS naming scheme

**`docs/operations/dns-naming.md`.** Decide once; it appears in certificates, kubeconfigs, and every runbook.

```
Zone: nexus.internal            (internal only — never resolvable from the internet)

  ┌ Infrastructure ────────────────────────────────────────────────┐
  seed.nexus.internal                → 10.100.0.10
  ca.nexus.internal                  → 10.100.0.10
  ntp.nexus.internal                 → 10.100.0.10
  boot.nexus.internal                → 10.100.0.10   (HTTP boot artifacts)

  ┌ Network devices ───────────────────────────────────────────────┐
  r01-leaf-a.mgmt.nexus.internal     → 10.100.1.1
  r01-pdu-a.mgmt.nexus.internal      → 10.100.1.11
  r01-pikvm.mgmt.nexus.internal      → 10.100.1.21

  ┌ Nodes (two records each — mgmt and cluster) ───────────────────┐
  nx-c-r01-05.mgmt.nexus.internal    → 10.100.0.105
  nx-c-r01-05.nexus.internal         → 10.200.1.5

  ┌ Cluster services ──────────────────────────────────────────────┐
  api.nexus.internal                 → 10.200.0.10  (Kubernetes API VIP)
  *.apps.nexus.internal              → 10.10.0.x    (Gateway API LB VIPs, Phase 17)

  ┌ Reverse zones ─────────────────────────────────────────────────┐
  0.100.10.in-addr.arpa, 200.10.in-addr.arpa, ...

Rules:
  · Every record is generated from inventory/ — never hand-edited (Law II)
  · `.internal` is reserved for private use and will never collide with a public TLD
  · Do NOT reuse a public domain you own for internal names: split-horizon DNS
    causes certificate and resolution confusion that outlives the person who set it up
```

---

### Task 2 — DHCP + TFTP + PXE (dnsmasq)

**`bootstrap/services/dnsmasq/dnsmasq.conf`**

```ini
# ─────────── NEXUS bootstrap DHCP / TFTP ───────────
# DNS is served by CoreDNS. dnsmasq must NOT bind port 53.
port=0

interface=<mgmt-iface>
bind-interfaces                 # ⚠️ never listen on interfaces we do not own
except-interface=lo

# ── DHCP: two pools ────────────────────────────────
# 1. Discovery pool — unknown MACs get a short lease and boot the discovery image
dhcp-range=set:discovery,10.100.2.1,10.100.3.254,255.255.252.0,10m

# 2. Known hosts get a reservation (generated from inventory) — see dhcp-hosts.d/
conf-dir=/etc/dnsmasq.d/dhcp-hosts.d,*.conf

dhcp-option=option:router,10.100.0.1
dhcp-option=option:dns-server,10.100.0.10
dhcp-option=option:ntp-server,10.100.0.10
dhcp-option=option:domain-name,mgmt.nexus.internal
dhcp-authoritative

# ── PXE / iPXE chainload ───────────────────────────
enable-tftp
tftp-root=/srv/tftp
tftp-no-blocksize               # some PXE ROMs mishandle blocksize negotiation

# Client-architecture-aware boot file selection.
# Getting this wrong is the #1 cause of "PXE-E##" errors.
dhcp-match=set:bios,option:client-arch,0        # legacy BIOS x86
dhcp-match=set:efi32,option:client-arch,6       # EFI IA32
dhcp-match=set:efi64,option:client-arch,7       # EFI x86-64
dhcp-match=set:efibc,option:client-arch,9       # EFI x86-64 (alt)
# Detect iPXE itself, so we chainload only once (avoids an infinite boot loop)
dhcp-match=set:ipxe,175

dhcp-boot=tag:bios,tag:!ipxe,undionly.kpxe
dhcp-boot=tag:efi64,tag:!ipxe,ipxe.efi
dhcp-boot=tag:efibc,tag:!ipxe,ipxe.efi
dhcp-boot=tag:efi32,tag:!ipxe,ipxe32.efi
# Once iPXE is running, hand it a script over HTTP instead of another binary
dhcp-boot=tag:ipxe,http://10.100.0.10:8080/auto.ipxe

# ── Logging (essential for debugging netboot) ──────
log-dhcp
log-queries=extra
log-facility=/var/log/dnsmasq.log
```

> ⚠️ **`bind-interfaces` is not optional.** Without it, dnsmasq listens on `0.0.0.0` and will answer DHCP on any network the seed node can reach — including, potentially, the corporate LAN.

> 💡 **The `tag:ipxe` guard:** without it, iPXE receives `ipxe.efi` as its own boot file, loads iPXE again, and loops forever. This is the classic PXE boot loop.

**`tools/gen-dhcp-reservations.sh`** — generates one file per node from inventory:
```bash
#!/usr/bin/env bash
source "$(dirname "$0")/lib/common.sh"
out="$ROOT/bootstrap/services/dnsmasq/dhcp-hosts.d"
mkdir -p "$out"; rm -f "$out"/*.conf

for f in "$ROOT"/inventory/nodes/*.yaml; do
  name=$(yq -r '.metadata.name' "$f")
  mac=$(yq -r '.spec.nics[] | select(.role=="management") | .macAddress' "$f")
  ip=$(yq -r '.spec.mgmtIp // ""' "$f")     # or compute from the ip-plan
  [[ -n "$mac" && -n "$ip" ]] || { warn "$name: missing mgmt MAC or IP"; continue; }
  printf 'dhcp-host=%s,%s,%s,infinite\n' "$mac" "$ip" "$name" > "$out/$name.conf"
done
ok "generated $(ls "$out" | wc -l) reservations"
```

---

### Task 3 — DNS (CoreDNS)

**`bootstrap/services/coredns/Corefile`**

```
nexus.internal:53 {
    file /etc/coredns/zones/nexus.internal.zone {
        reload 30s
    }
    log
    errors
    prometheus 0.0.0.0:9153        # scraped in Phase 11
}

100.10.in-addr.arpa:53 {
    file /etc/coredns/zones/100.10.in-addr.arpa.zone { reload 30s }
    log
    errors
}

. :53 {
    forward . <upstream-dns-1> <upstream-dns-2> {
        policy sequential
        health_check 10s
    }
    cache 300
    loop                            # detect and refuse forwarding loops
    log
    errors
    prometheus 0.0.0.0:9153
}
```

**The zone file is generated**, never hand-edited (`tools/gen-dns-records.sh`):

```
$ORIGIN nexus.internal.
$TTL 300
@   IN SOA ns.nexus.internal. admin.nexus.internal. (
        2026083001  ; serial — bump on every regeneration
        3600 900 604800 300 )
@   IN NS  ns.nexus.internal.
ns  IN A   10.100.0.10

; ── infrastructure ──
seed  IN A 10.100.0.10
ca    IN A 10.100.0.10
ntp   IN A 10.100.0.10
boot  IN A 10.100.0.10
api   IN A 10.200.0.10

; ── generated from inventory/nodes/ ──
nx-c-r01-05                 IN A 10.200.1.5
nx-c-r01-05.mgmt            IN A 10.100.0.105
; ...
```

> ⚠️ **The serial number must increase on every regeneration.** Use `date +%Y%m%d%H` or a commit counter. A stale serial with `reload` enabled generally still works, but any secondary DNS or cache that honors SOA serials will serve stale data.

---

### Task 4 — NTP (chrony)

Runs **on the host**, not in a container — clock discipline needs direct access to the system clock.

**`/etc/chrony/chrony.conf`**
```conf
# Upstream sources — use a pool plus at least one specific server
pool 2.pool.ntp.org iburst maxsources 4
server <upstream-or-gps> iburst

# Serve the management and cluster networks
allow 10.100.0.0/22
allow 10.200.0.0/20

# ⚠️ Serve time even if we lose upstream. Without this, a WAN outage means
#    the whole cluster loses time sync — a far worse failure than mild drift.
local stratum 10

driftfile /var/lib/chrony/chrony.drift
makestep 1.0 3            # step (not slew) the clock at startup if off by > 1 s
rtcsync
leapsectz right/UTC
logdir /var/log/chrony
log tracking measurements statistics
```

```bash
sudo systemctl enable --now chrony
chronyc tracking          # Leap status: Normal; System time offset < 50 ms
chronyc sources -v
chronyc clients           # after nodes boot, they should appear here
```

**Clock accuracy requirements — write these into the acceptance criteria:**

| Consumer | Max tolerable skew | Consequence of exceeding |
|---|---|---|
| Kubernetes cert validation | ~5 min | TLS handshake failures across the cluster |
| etcd Raft | < 1 s recommended | Leader election instability, spurious timeouts |
| Ceph | 50 ms (default `mon_clock_drift_allowed`) | `HEALTH_WARN clock skew`, then mon flapping |
| Distributed tracing | < 10 ms | Traces render with negative durations |
| Log correlation | < 100 ms | Events appear out of order across nodes |

**Target: all nodes within 10 ms of the seed node.** This is achievable on a LAN and is what the Phase 11 check will assert.

---

### Task 5 — Platform Intermediate CA (step-ca)

Implements the middle tier of the Phase 04 PKI hierarchy.

**Root CA creation — a ceremony, done once, offline:**

```bash
# ⚠️ Perform on an air-gapped machine if your threat model requires it.
step certificate create "NEXUS Root CA" root_ca.crt root_ca.key \
  --profile root-ca \
  --not-after 87600h \
  --kty EC --curve P-384

# 🔒 SECRET: root_ca.key
#   · NEVER goes on the seed node
#   · Stored offline, split custody, two physical locations
#   · Passphrase held separately from the key material
#   · Used only to sign intermediates (roughly annually)
```

**Intermediate CA (this one lives on the seed node):**

```bash
step certificate create "NEXUS Platform Intermediate CA" \
  intermediate_ca.crt intermediate_ca.key \
  --profile intermediate-ca \
  --ca root_ca.crt --ca-key root_ca.key \
  --not-after 43800h \
  --kty EC --curve P-256
```

**`bootstrap/services/step-ca/ca.json`**
```json
{
  "root": "/home/step/certs/root_ca.crt",
  "crt":  "/home/step/certs/intermediate_ca.crt",
  "key":  "/home/step/secrets/intermediate_ca_key",
  "address": ":9000",
  "dnsNames": ["ca.nexus.internal", "10.100.0.10"],
  "db": { "type": "badgerv2", "dataSource": "/home/step/db" },
  "authority": {
    "claims": {
      "maxTLSCertDuration":     "2160h",
      "defaultTLSCertDuration":  "2160h",
      "disableRenewal": false
    },
    "provisioners": [
      {
        "type": "JWK",
        "name": "admin@nexus.internal",
        "key": { "...": "generated" },
        "encryptedKey": "..."
      },
      {
        "type": "ACME",
        "name": "acme",
        "forceCN": true,
        "claims": { "defaultTLSCertDuration": "2160h" }
      }
    ]
  },
  "tls": {
    "cipherSuites": [
      "TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256",
      "TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384"
    ],
    "minVersion": 1.3, "maxVersion": 1.3, "renegotiation": false
  }
}
```

> 💡 **Why an ACME provisioner:** cert-manager (Phase 17) speaks ACME natively. Exposing an internal ACME endpoint means every platform service gets automatic 90-day certificate rotation from your own CA, with zero bespoke integration.

**`docs/operations/pki-operations.md`** must document: issuing a cert manually, renewing, **revoking**, rotating the intermediate, and the root-CA ceremony. Include the root CA fingerprint in plain text so operators can verify a bootstrap:
```bash
step certificate fingerprint root_ca.crt
# Publish this fingerprint in the runbook, the wiki, and the onboarding doc.
```

---

### Task 6 — Compose the bootstrap stack

**`bootstrap/services/docker-compose.yaml`**

```yaml
name: nexus-bootstrap

services:
  dnsmasq:
    image: docker.io/jpillora/dnsmasq:1.1.0
    network_mode: host                 # DHCP requires L2 access; no bridge
    cap_add: [NET_ADMIN, NET_BIND_SERVICE]
    volumes:
      - ./dnsmasq/dnsmasq.conf:/etc/dnsmasq.conf:ro
      - ./dnsmasq/dhcp-hosts.d:/etc/dnsmasq.d/dhcp-hosts.d:ro
      - tftp:/srv/tftp
      - dnsmasq-log:/var/log
    restart: unless-stopped

  coredns:
    image: docker.io/coredns/coredns:1.12.0
    command: ["-conf", "/etc/coredns/Corefile"]
    network_mode: host
    volumes:
      - ./coredns/Corefile:/etc/coredns/Corefile:ro
      - ./coredns/zones:/etc/coredns/zones:ro
    restart: unless-stopped

  step-ca:
    image: docker.io/smallstep/step-ca:0.28.2
    ports: ["9000:9000"]
    volumes:
      - step-ca-data:/home/step
      - ./step-ca/ca.json:/home/step/config/ca.json:ro
    environment:
      DOCKER_STEPCA_INIT_NAME: NEXUS
      DOCKER_STEPCA_INIT_DNS_NAMES: ca.nexus.internal,10.100.0.10
    restart: unless-stopped

  fileserver:                          # HTTP boot artifacts (iPXE scripts, kernels)
    image: docker.io/library/nginx:1.27-alpine
    ports: ["8080:80"]
    volumes:
      - ./boot-artifacts:/usr/share/nginx/html:ro
      - ./nginx/autoindex.conf:/etc/nginx/conf.d/default.conf:ro
    restart: unless-stopped

volumes:
  tftp: {}
  dnsmasq-log: {}
  step-ca-data: {}
```

```bash
cd bootstrap/services
docker compose up -d
docker compose ps          # all Up
docker compose logs -f     # watch for bind errors
```

---

### Task 7 — Fetch iPXE binaries

```bash
mkdir -p bootstrap/services/boot-artifacts
cd bootstrap/services/boot-artifacts

# Pin to a specific iPXE release — do NOT pull from boot.ipxe.org at boot time
IPXE_VER=1.21.1
for f in undionly.kpxe ipxe.efi ipxe32.efi; do
  curl -fsSLO "https://github.com/ipxe/ipxe/releases/download/v${IPXE_VER}/${f}" \
    || curl -fsSLO "https://boot.ipxe.org/${f}"    # fallback; record which you used
done
sha256sum *.kpxe *.efi | tee ipxe-checksums.txt    # commit the checksums

# Copy into the TFTP volume
docker cp undionly.kpxe nexus-bootstrap-dnsmasq-1:/srv/tftp/
docker cp ipxe.efi      nexus-bootstrap-dnsmasq-1:/srv/tftp/
docker cp ipxe32.efi    nexus-bootstrap-dnsmasq-1:/srv/tftp/
```

**A placeholder `auto.ipxe`** (Phase 08 replaces it with Tinkerbell's dynamic version):
```ipxe
#!ipxe
echo
echo NEXUS bootstrap — MAC ${net0/mac} — UUID ${uuid}
echo No workflow assigned yet. Phase 08 will handle this machine.
echo
sleep 30
reboot
```

---

### Task 8 — End-to-end validation

**`tools/net-services-check.sh`**

```bash
#!/usr/bin/env bash
source "$(dirname "$0")/lib/common.sh"
fail=0
chk() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else warn "FAIL: $1"; fail=1; fi; }

# DNS
chk "forward lookup"            "dig +short @10.100.0.10 seed.nexus.internal | grep -q 10.100.0.10"
chk "reverse lookup"            "dig +short -x 10.100.0.10 @10.100.0.10 | grep -q nexus.internal"
chk "upstream forwarding"       "dig +short @10.100.0.10 github.com | grep -qE '^[0-9]+\.'"
chk "no DNS loop"               "! docker compose logs coredns | grep -qi 'loop detected'"
chk "every node resolves"       "bash tools/check-all-node-dns.sh"

# DHCP / TFTP
chk "TFTP serves ipxe.efi"      "tftp 10.100.0.10 -c get ipxe.efi /tmp/t.efi && test -s /tmp/t.efi"
chk "HTTP serves auto.ipxe"     "curl -fsS http://10.100.0.10:8080/auto.ipxe | grep -q ipxe"
chk "dnsmasq bound to mgmt only" "! ss -ulnp | grep ':67 ' | grep -q '0.0.0.0'"

# NTP
chk "chrony synchronized"       "chronyc tracking | grep -q 'Leap status *: Normal'"
chk "offset under 50 ms"        "[ \$(chronyc tracking | awk '/System time/{printf \"%d\", \$4*1000}') -lt 50 ]"
chk "chrony serves clients"     "chronyc -a 'accheck 10.200.1.5' | grep -qi accessible"

# PKI
chk "step-ca is healthy"        "curl -fsS -k https://10.100.0.10:9000/health | grep -q ok"
chk "can issue a certificate"   "step ca certificate test.nexus.internal /tmp/t.crt /tmp/t.key --force --provisioner acme"
chk "issued cert chains to root" "step certificate verify /tmp/t.crt --roots root_ca.crt"
chk "root fingerprint matches"  "[ \"\$(step certificate fingerprint root_ca.crt)\" = \"<RECORDED-FINGERPRINT>\" ]"

[[ $fail -eq 0 ]] && ok "ALL NETWORK SERVICES OK" || die "network services check FAILED"
```

**Then do the real test — boot one machine:**
```bash
# Power on a single node and watch the whole chain
docker compose logs -f dnsmasq | grep -E 'DHCPDISCOVER|DHCPOFFER|DHCPACK|sent'
# Expected sequence:
#   DHCPDISCOVER(mgmt0) <mac>
#   DHCPOFFER(mgmt0) 10.100.2.x <mac>
#   DHCPREQUEST / DHCPACK
#   sent /srv/tftp/ipxe.efi to 10.100.2.x
#   (then iPXE re-requests with tag:ipxe)
#   DHCPACK → http://10.100.0.10:8080/auto.ipxe
# The node should display the NEXUS placeholder message and reboot.
```

📊 **This is the first end-to-end proof that netboot works.** Record the full log in `evidence/phase-07/first-netboot.log`.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | No rogue DHCP server on the VLAN | `nmap --script broadcast-dhcp-discover` | Only the seed node answers |
| **A2** | dnsmasq listens only on the management interface | `ss -ulnp \| grep :67` | Bound to `<mgmt-ip>`, not `0.0.0.0` |
| **A3** | DHCP reservations generated from inventory for every node | `ls dhcp-hosts.d/*.conf \| wc -l` | Equals node count |
| **A4** | A test machine receives its reserved IP | Boot it, check the lease | IP matches inventory |
| **A5** | PXE chainload works for UEFI and (if used) legacy BIOS | Boot one of each | iPXE loads, fetches `auto.ipxe` |
| **A6** | No PXE boot loop | Watch the boot | iPXE loads exactly once |
| **A7** | Forward and reverse DNS work for every node | `tools/net-services-check.sh` | 100 % |
| **A8** | Upstream DNS forwarding works | `dig @seed github.com` | Resolves |
| **A9** | No DNS forwarding loop | CoreDNS logs | No `loop detected` |
| **A10** | chrony is synchronized, offset < 50 ms | `chronyc tracking` | Normal, within tolerance |
| **A11** | chrony serves the management and cluster networks | `chronyc clients` after a node boots | Client appears |
| **A12** | chrony has `local stratum 10` (survives WAN loss) | Grep the config; test by blocking upstream | Still serves time |
| **A13** | step-ca is healthy and issues certificates | `net-services-check.sh` | Cert issued |
| **A14** | Issued certificates chain to the root CA | `step certificate verify` | Valid |
| **A15** | Root CA private key is **not** on the seed node | `find / -name 'root_ca.key'` on the seed node | Not found |
| **A16** | Root CA fingerprint is recorded in the runbook | Read `pki-operations.md` | Present, matches |
| **A17** | PKI operations documented: issue, renew, **revoke**, rotate | Read the doc | All four |
| **A18** | Full bootstrap stack restarts cleanly | `docker compose down && docker compose up -d` | All healthy, no manual steps |
| **A19** | End-to-end netboot log captured | `evidence/phase-07/first-netboot.log` | Full DISCOVER→ACK→TFTP→HTTP sequence |

---

## ↩️ ROLLBACK

```bash
cd bootstrap/services && docker compose down     # stops DHCP/DNS/CA immediately
sudo systemctl stop chrony
```
⚠️ Stopping DHCP does not release existing leases; nodes keep their addresses until lease expiry. That is intentional — it means a services restart does not disrupt booted machines.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| `PXE-E53: No boot filename received` | `dhcp-boot` not matching the client architecture | Check `log-dhcp` output for the client-arch value; add the matching `dhcp-match` tag |
| `PXE-E32: TFTP open timeout` | TFTP not reachable, firewall, or wrong `tftp-root` | `tftp <seed> -c get ipxe.efi`; open UDP 69; verify the file is in the volume |
| iPXE loads repeatedly (boot loop) | Missing `tag:!ipxe` guard | Add it — see Task 2 |
| DHCP offers but the node does not take the lease | Two DHCP servers racing, or an incorrect subnet mask | `tcpdump -i <if> port 67 or port 68`; verify VLAN isolation |
| DNS resolves internally but not externally | Forwarder unreachable or blocked | `dig @<upstream> github.com` from the seed node; check egress firewall |
| CoreDNS `plugin/loop: Loop detected` | CoreDNS forwarding to itself (`/etc/resolv.conf` points at 127.0.0.1) | Forward to explicit upstream IPs, never `/etc/resolv.conf` |
| chrony will not step a large offset | `makestep` not configured for large deltas | `makestep 1.0 3` allows stepping in the first 3 updates; for a huge offset use `chronyc makestep` |
| Node clocks drift despite a working NTP server | Firewall blocks UDP 123 from nodes, or `allow` is missing | Add the subnet to `allow`; verify with `chronyc clients` |
| step-ca `x509: certificate signed by unknown authority` | Root CA not distributed to clients | Bundle the root into the node's trust store — Talos does this via `machine.files` (Phase 09) |
| Certificate issued with the wrong lifetime | Provisioner claims override the global default | Check the per-provisioner `claims` block |
| dnsmasq answers on the wrong interface | `bind-interfaces` missing | Add it. Restart. Re-run A2. |

---

## 🚫 DO NOT

- **Do not** enable DHCP before proving the VLAN is isolated. A rogue DHCP server on a shared network is an outage for other people.
- **Do not** run dnsmasq's DNS and CoreDNS simultaneously on port 53. `port=0` in dnsmasq.
- **Do not** put the root CA private key on the seed node. Ever.
- **Do not** use a public domain you own for the internal zone. Use `.internal`.
- **Do not** hand-edit the zone file or DHCP reservations. Generate them from inventory (Law II).
- **Do not** install Tinkerbell yet. That is Phase 08 — this phase's placeholder `auto.ipxe` is deliberate.
- **Do not** point CoreDNS forwarding at `/etc/resolv.conf`. It creates a loop.
- **Do not** skip `local stratum 10` in chrony. Losing the WAN must not desynchronize the cluster.

---

## 📤 HANDOFF

`evidence/phase-07/handoff.md` must state:

1. **DNS zone name and the seed node's DNS address** — Phase 09 configures Talos resolvers; Phase 12 uses `api.nexus.internal`.
2. **DHCP pool ranges and reservation mechanism** — Phase 08's Tinkerbell either replaces dnsmasq's DHCP or coexists with it; state which.
3. **iPXE version and checksums** — Phase 08 chainloads from here.
4. **NTP server address and measured accuracy** — Phase 09 configures Talos NTP; Phase 27 depends on < 50 ms for Ceph.
5. **CA endpoint, ACME directory URL, and the root fingerprint** — Phase 17's cert-manager `ClusterIssuer` uses these verbatim.
6. **Where the root CA key is held** (process description, not location detail).
7. **The first-netboot log** — proof the chain works before Phase 08 builds on it.
8. **Any device that failed to PXE** and why (feeds Phase 08's exception handling).

---

## ➡️ NEXT

**[PHASE-08 — Bare-Metal Provisioning with Tinkerbell](PHASE-08.md)** — turn the working netboot chain into a zero-touch provisioning pipeline with hardware discovery, WoL, and PDU power control.
