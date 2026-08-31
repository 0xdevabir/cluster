# PHASE 13 — Cilium Datapath

| | |
|---|---|
| **Stage** | 2 — Kubernetes Substrate |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 12 |
| **Blocks** | 14, 16, 17, 21 — all networking |
| **Risk** | 🟠 High — a CNI misconfiguration disconnects every pod; a BGP error can affect the physical fabric |
| **Blast radius** | All pod networking |
| **Architecture refs** | `ARCHITECTURE.md#l25-cni-datapath-decisions`, `#l22-addressing--vlan-plan`, ADR-004, `ULTIMATE-PLAN.md#81` (performance budget) |

---

## 🎯 MISSION

Install Cilium as the **eBPF datapath** — native routing with no encapsulation, kube-proxy fully replaced, BIG TCP enabled, BGP peering to the leaf switches, and Hubble observability — and measure that it delivers ≥ 92 % of line rate pod-to-pod (benchmark B3).

> 💡 **WHY this configuration and not the defaults:** Cilium's defaults use VXLAN encapsulation and coexist with kube-proxy. That combination costs **15–30 % of network throughput** (50-byte header + encap/decap CPU) and gives O(n) service lookup. The configuration in this phase removes both taxes — it is the single largest line item in the Phase 8.1 performance budget. Every setting below is there to buy back a specific percentage.

> ⚠️ **DANGER — BGP touches the physical network.** A misconfigured BGP session can advertise wrong routes to your leaf switches and affect traffic beyond this cluster. Configure the switch-side prefix filters (Phase 03, Task 5) **before** enabling BGP on the Cilium side.

---

## ✅ PREFLIGHT

```bash
kubectl get nodes                       # all present, NotReady (NetworkPluginNotReady)
kubectl get --raw /healthz              # ok
bash tools/cluster/etcd-health.sh       # healthy

# ⚠️ Confirm nothing else claims the datapath
kubectl get ds -A | grep -Ei 'flannel|calico|kube-proxy|weave'   # expect empty
kubectl get cm -n kube-system kube-proxy 2>/dev/null             # expect NotFound

# Pod CIDR matches the plan
kubectl get node -o jsonpath='{.items[*].spec.podCIDR}'          # /24s from 10.244.0.0/14

# Jumbo frames work node-to-node (Phase 06 verified this for the seed node)
talosctl --nodes <n1> read /sys/class/net/<iface>/mtu            # 9000

# BGP: switch-side config is ready and prefix-filtered
# ssh <leaf> 'show ip prefix-list HOST-IN'
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/infra/cilium/
  values.yaml                  # the datapath contract
  bgp-cluster-config.yaml
  bgp-peer-config.yaml
  bgp-advertisements.yaml
  lb-ip-pool.yaml
  hubble-values.yaml
  kustomization.yaml
tools/net/
  cilium-verify.sh
  bench-pod-network.sh         # 📊 B3
docs/operations/
  cilium-runbook.md
  network-troubleshooting.md
benchmarks/baselines/b3-pod-network.json
evidence/phase-13/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Source |
|---|---|---|
| Cilium | `1.17.1` | `oci://quay.io/cilium/cilium-helm` |
| Hubble Relay / UI | bundled with 1.17.1 | same chart |
| cilium-cli | `0.16.20` | Phase 00 |
| Kernel | ≥ 6.4 (Talos v1.9.5 ships 6.12) | required for BIG TCP + full eBPF host routing |

---

## 📋 TASKS

### Task 1 — The values file (the datapath contract)

**`clusters/nexus-prod/infra/cilium/values.yaml`.** Every line is annotated with what it buys.

```yaml
# ─────────────────────────────────────────────────────────────────────
#  NEXUS Cilium datapath. Each setting maps to ULTIMATE-PLAN.md §8.1.
#  Changing any of these requires a benchmark (Law VIII).
# ─────────────────────────────────────────────────────────────────────

k8sServiceHost: "10.200.0.10"        # the API VIP — Cilium must reach the API
k8sServicePort: 6443                 # WITHOUT kube-proxy, so it needs this explicitly

# ── Routing: no encapsulation ────────────────────────────────────────
# Saves the 50-byte VXLAN header and all encap/decap CPU: 15–30 % of throughput.
# Requires L3 reachability between PodCIDRs — BGP (Task 3) provides it.
routingMode: native
ipv4NativeRoutingCIDR: "10.244.0.0/14"
autoDirectNodeRoutes: true           # direct routes for same-L2 nodes; BGP for the rest

# ── IPAM ─────────────────────────────────────────────────────────────
ipam:
  mode: kubernetes                   # honor node.spec.podCIDR from Phase 12

# ── kube-proxy replacement: O(1) eBPF service lookup ──────────────────
kubeProxyReplacement: true
bpf:
  masquerade: true                   # eBPF NAT instead of iptables
  hostLegacyRouting: false           # ⚠️ false = skip the host netfilter stack entirely
  preallocateMaps: true              # avoids map-growth stalls under load
  lbMapMax: 131072
  mapDynamicSizeRatio: 0.005
  tproxy: true
installIptablesRules: false          # nothing left that needs them

# ── BIG TCP: 192 KB GSO/GRO superpackets ─────────────────────────────
# Measurably lowers CPU-per-Gb at 100 GbE. Requires kernel ≥ 5.19.
enableIPv4BIGTCP: true
enableIPv6BIGTCP: false              # IPv6 not used in the underlay

# ── Load balancing ───────────────────────────────────────────────────
loadBalancer:
  algorithm: maglev                  # consistent hashing: stable across backend churn
  mode: dsr                          # Direct Server Return — reply skips the LB node
  acceleration: native               # XDP offload where the NIC supports it
  dsrDispatch: geneve                # required for DSR across L3 boundaries
maglev:
  tableSize: 65521
  hashSeed: "<REPLACE-ME: base64 of 12 random bytes>"

# ── Bandwidth manager: EDT pacing + BBR ──────────────────────────────
bandwidthManager:
  enabled: true
  bbr: true

# ── Host-level performance ───────────────────────────────────────────
enableHostLegacyRouting: false
localRedirectPolicy: true            # node-local DNS / Spegel benefit from this
sessionAffinity: true

# ── MTU: match the fabric (Phase 03) ─────────────────────────────────
MTU: 9000

# ── Health, HA, resources ────────────────────────────────────────────
operator:
  replicas: 2
  prometheus: { enabled: true, serviceMonitor: { enabled: false } }   # TODO(phase-45)
prometheus:
  enabled: true
  serviceMonitor: { enabled: false }                                   # TODO(phase-45)

resources:
  requests: { cpu: 300m, memory: 512Mi }
  limits:   { memory: 4Gi }          # ⚠️ no CPU limit: throttling the datapath is fatal

# ── Hubble: flow observability without tcpdump ───────────────────────
hubble:
  enabled: true
  metrics:
    enabled:
      - dns:query
      - drop
      - tcp
      - flow
      - port-distribution
      - icmp
      - "httpV2:exemplars=true;labelsContext=source_ip,destination_ip,traffic_direction"
  relay: { enabled: true, replicas: 2 }
  ui:    { enabled: true }           # exposed via Gateway API in Phase 17, not here

# ── Policy ───────────────────────────────────────────────────────────
policyEnforcementMode: default       # ⚠️ 'always' in Phase 16, AFTER policies exist
policyAuditMode: false

# ── BGP (configured in Task 3) ───────────────────────────────────────
bgpControlPlane:
  enabled: true

# ── Encryption: NOT enabled ──────────────────────────────────────────
# WireGuard/IPsec transparent encryption costs 30–50 % of throughput at 100 GbE.
# Trust model is trusted-org (ADR-023). Revisit if that changes.
encryption:
  enabled: false

# ── Talos-specific: no kube-proxy, cgroup path, security context ─────
securityContext:
  capabilities:
    ciliumAgent: [CHOWN, KILL, NET_ADMIN, NET_RAW, IPC_LOCK, SYS_ADMIN, SYS_RESOURCE, DAC_OVERRIDE, FOWNER, SETGID, SETUID]
    cleanCiliumState: [NET_ADMIN, SYS_ADMIN, SYS_RESOURCE]
cgroup:
  autoMount: { enabled: false }
  hostRoot: /sys/fs/cgroup
```

> ⚠️ **No CPU limit on the Cilium agent.** A CPU limit causes cgroup throttling, and throttling the process that programs the datapath produces intermittent, extremely hard-to-diagnose packet loss. Memory limit yes; CPU limit no. This is one of the audited exceptions.

> 💡 **`routingMode: native` requires that every node can route to every PodCIDR.** With `autoDirectNodeRoutes: true`, nodes on the same L2 segment learn each other's routes directly. Across racks (different L3 subnets), BGP carries them. **If BGP is not working, cross-rack pod traffic breaks.** At M1 (one rack) you can run without BGP; from M3 it is mandatory.

---

### Task 2 — Install

```bash
helm repo add cilium https://helm.cilium.io/
helm upgrade --install cilium cilium/cilium --version 1.17.1 \
  --namespace kube-system \
  --values clusters/nexus-prod/infra/cilium/values.yaml \
  --wait --timeout 10m

# Nodes should become Ready within ~60 s
kubectl get nodes -w

# The definitive check
cilium status --wait
cilium connectivity test --test-concurrency 4
```

> 💡 **`cilium connectivity test` is not optional.** It runs ~60 scenarios (pod-to-pod same/different node, pod-to-service, pod-to-world, DNS, policy enforcement) and catches misconfigurations that `kubectl get pods` never will. **Run it here, and again after every Cilium change.**

**Verify the taxes are actually gone:**
```bash
# 1. kube-proxy replacement is complete (not "partial")
kubectl -n kube-system exec ds/cilium -- cilium-dbg status | grep -i "KubeProxyReplacement"
# Expect: "True"

# 2. No iptables rules for services
talosctl --nodes <node> read /proc/net/ip_tables_names 2>/dev/null | wc -l   # expect 0 or minimal

# 3. Native routing, no tunnel device
kubectl -n kube-system exec ds/cilium -- cilium-dbg status | grep -i "Routing"
# Expect: "Network: Native   Host: BPF"
talosctl --nodes <node> get links | grep -E 'cilium_vxlan|cilium_geneve'      # expect NOT present

# 4. BIG TCP active
talosctl --nodes <node> read /sys/class/net/<iface>/gro_max_size              # expect 196608
talosctl --nodes <node> read /sys/class/net/<iface>/gso_max_size              # expect 196608

# 5. MTU is 9000 on the pod path
kubectl run t --image=nicolaka/netshoot --rm -it -- ip link show eth0         # mtu 9000
```

---

### Task 3 — BGP peering

**`bgp-cluster-config.yaml`** — which nodes peer, and with whom.

```yaml
apiVersion: cilium.io/v2alpha1
kind: CiliumBGPClusterConfig
metadata: { name: nexus-bgp }
spec:
  nodeSelector:
    matchLabels: { "kubernetes.io/os": linux }
  bgpInstances:
    - name: "rack-instance"
      localASN: 65201                 # per-rack ASN from Phase 03's bgp-design.md
      peers:
        - name: "leaf-a"
          peerASN: 65101
          peerAddress: "10.200.1.1"   # or unnumbered via peerAddress omitted + interface
          peerConfigRef: { name: leaf-peer-config }
        - name: "leaf-b"
          peerASN: 65101
          peerAddress: "10.200.1.2"
          peerConfigRef: { name: leaf-peer-config }
```

**`bgp-peer-config.yaml`**
```yaml
apiVersion: cilium.io/v2alpha1
kind: CiliumBGPPeerConfig
metadata: { name: leaf-peer-config }
spec:
  timers: { connectRetryTimeSeconds: 12, holdTimeSeconds: 9, keepAliveTimeSeconds: 3 }
  gracefulRestart: { enabled: true, restartTimeSeconds: 120 }   # agent restart ≠ blackhole
  ebgpMultihop: 1
  families:
    - afi: ipv4
      safi: unicast
      advertisements:
        matchLabels: { advertise: nexus }
```

**`bgp-advertisements.yaml`** — advertise only what we should.
```yaml
apiVersion: cilium.io/v2alpha1
kind: CiliumBGPAdvertisement
metadata:
  name: nexus-advertisements
  labels: { advertise: nexus }
spec:
  advertisements:
    - advertisementType: "PodCIDR"
      attributes: { communities: { standard: ["65000:100"] } }
    - advertisementType: "Service"
      service: { addresses: [ LoadBalancerIP ] }
      selector:
        matchExpressions:
          - { key: "nexus.io/advertise", operator: In, values: ["true"] }
      attributes: { communities: { standard: ["65000:200"] } }
```

> ⚠️ **The `selector` on Service advertisement is a safety control.** Without it, every LoadBalancer service in the cluster is advertised to the physical network. Requiring an explicit `nexus.io/advertise: "true"` label means exposure is a deliberate act.

**`lb-ip-pool.yaml`**
```yaml
apiVersion: cilium.io/v2alpha1
kind: CiliumLoadBalancerIPPool
metadata: { name: nexus-lb-pool }
spec:
  blocks: [ { cidr: "10.10.0.0/24" } ]      # from Phase 03's ip-plan
  serviceSelector:
    matchExpressions:
      - { key: "nexus.io/lb-pool", operator: In, values: ["default"] }
```

**Verify:**
```bash
cilium bgp peers                 # Session State: established, on every node
cilium bgp routes advertised ipv4 unicast
# On the switch:
#   show bgp ipv4 unicast summary
#   show ip route bgp | include 10.244   ← PodCIDRs should be present
```

---

### Task 4 — 📊 Benchmark B3: pod-to-pod network

**`tools/net/bench-pod-network.sh`** — establishes the baseline every later phase compares against.

```bash
#!/usr/bin/env bash
# B3: pod-to-pod TCP throughput and latency across nodes.
source "$(dirname "$0")/../lib/common.sh"
NS=bench-net
kubectl create ns "$NS" --dry-run=client -o yaml | kubectl apply -f -

# Server and client pinned to different nodes, ideally different racks
kubectl -n "$NS" run iperf-server --image=networkstatic/iperf3:latest \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"'"$SERVER_NODE"'"}}}' \
  -- -s
kubectl -n "$NS" wait --for=condition=Ready pod/iperf-server --timeout=120s
SRV_IP=$(kubectl -n "$NS" get pod iperf-server -o jsonpath='{.status.podIP}')

# ── Throughput: 8 parallel streams ──
kubectl -n "$NS" run iperf-client --rm -i --restart=Never --image=networkstatic/iperf3:latest \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"'"$CLIENT_NODE"'"}}}' \
  -- -c "$SRV_IP" -P 8 -t 30 -J | tee "$ROOT/benchmarks/baselines/b3-raw.json"

# ── Latency ──
kubectl -n "$NS" run netperf --rm -i --restart=Never --image=networkstatic/netperf \
  -- -H "$SRV_IP" -t TCP_RR -l 30 -- -o MEAN_LATENCY,P99_LATENCY

kubectl delete ns "$NS"
```

**Targets — record in `benchmarks/baselines/b3-pod-network.json`:**

| Metric | 25 GbE target | 100 GbE target | Measured | Verdict |
|---|---|---|---|---|
| Pod→pod, same node | ≥ 40 Gb/s | ≥ 80 Gb/s | | |
| Pod→pod, cross node, same rack | ≥ 23 Gb/s (92 %) | ≥ 92 Gb/s (92 %) | | |
| Pod→pod, cross rack | ≥ 23 Gb/s | ≥ 90 Gb/s | | |
| Pod→Service (ClusterIP) | within 3 % of pod→pod | same | | |
| TCP_RR p99 latency | < 100 µs | < 80 µs | | |
| CPU per Gb/s (client side) | — | < 1.5 % of a core | | |

> ⚠️ **If cross-node throughput is far below target, check in this order:** (1) MTU 9000 end-to-end — the #1 cause; (2) `routingMode: native` actually active, no tunnel device; (3) NIC offloads enabled (`ethtool -k`); (4) IRQ affinity and RSS queues (Phase 49); (5) the physical link negotiated at full speed (Phase 11's `SwitchPortSpeedDegraded` alert).

📊 **Compare against the host-to-host baseline.** Run `iperf3` between the same two nodes' host interfaces (not pods). **Pod-to-pod should be within 3 % of host-to-host.** A larger gap means the datapath is still taxing you, and this configuration exists to prevent exactly that.

---

### Task 5 — Verification script

**`tools/net/cilium-verify.sh`** — the standing check, re-run after any network change.

```bash
#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
fail=0
chk() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else warn "FAIL: $1"; fail=1; fi; }

chk "all cilium agents ready"      "cilium status --wait --wait-duration 60s"
chk "kube-proxy replacement true"  "kubectl -n kube-system exec ds/cilium -- cilium-dbg status | grep -q 'KubeProxyReplacement.*True'"
chk "native routing (no tunnel)"   "kubectl -n kube-system exec ds/cilium -- cilium-dbg status | grep -qi 'Routing.*Native'"
chk "no vxlan/geneve device"       "! talosctl --nodes $N1 get links | grep -qE 'cilium_(vxlan|geneve)'"
chk "BIG TCP enabled"              "[ \$(talosctl --nodes $N1 read /sys/class/net/$IF/gro_max_size) -gt 65536 ]"
chk "pod MTU is 9000"              "kubectl run mtu-$RANDOM --rm -i --restart=Never --image=busybox -- ip link show eth0 | grep -q 'mtu 9000'"
chk "all nodes Ready"              "[ \$(kubectl get nodes --no-headers | grep -cv ' Ready') -eq 0 ]"
chk "BGP sessions established"     "! cilium bgp peers -o json | jq -e '.[].peers[] | select(.session_state != \"established\")'"
chk "PodCIDRs advertised"          "cilium bgp routes advertised ipv4 unicast | grep -q 10.244"
chk "hubble relay healthy"         "cilium hubble port-forward & sleep 3; hubble status; kill %1"
chk "connectivity test passes"     "cilium connectivity test --test-concurrency 4"

[[ $fail -eq 0 ]] && ok "CILIUM DATAPATH OK" || die "cilium verification FAILED"
```

---

### Task 6 — Runbook

**`docs/operations/cilium-runbook.md`** — the commands you need under pressure.

| Question | Command |
|---|---|
| Is the datapath healthy? | `cilium status --wait` |
| Why can't pod A reach pod B? | `hubble observe --from-pod ns/a --to-pod ns/b --last 100` |
| What is being dropped, and why? | `hubble observe --verdict DROPPED --last 200` |
| What policy applies to this pod? | `cilium policy get`; `kubectl -n <ns> exec ds/cilium -- cilium-dbg endpoint get <id>` |
| What is the endpoint's identity? | `cilium-dbg endpoint list` |
| Service backend list | `cilium-dbg service list` |
| BGP state | `cilium bgp peers`; `cilium bgp routes` |
| eBPF map usage (approaching limits?) | `cilium-dbg bpf lb list`; `cilium-dbg map list --verbose` |
| Restart the datapath on one node | `kubectl -n kube-system delete pod -l k8s-app=cilium --field-selector spec.nodeName=<n>` |
| Full connectivity validation | `cilium connectivity test` |

**Include the three highest-value diagnostic recipes:**
1. **"Pods can't resolve DNS"** → `hubble observe --protocol dns --last 50`; check `localRedirectPolicy`; check CoreDNS endpoints in `cilium-dbg service list`.
2. **"Cross-rack traffic fails, same-rack works"** → BGP is down. `cilium bgp peers`, then the switch's `show bgp summary`.
3. **"Throughput dropped after a change"** → re-run B3 and compare to `benchmarks/baselines/b3-pod-network.json`. Law VIII exists for this.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | All nodes are `Ready` | `kubectl get nodes` | 100 % Ready |
| **A2** | `cilium status` fully healthy on every node | `cilium status --wait` | No errors |
| **A3** | `cilium connectivity test` passes completely | Run it | All tests pass |
| **A4** | kube-proxy replacement reports `True` (not partial) | `cilium-dbg status` | True |
| **A5** | No kube-proxy DaemonSet or iptables service rules exist | `kubectl get ds -A`; check iptables | Absent |
| **A6** | Native routing active; **no tunnel device on any node** | `talosctl get links` on all | No `cilium_vxlan`/`cilium_geneve` |
| **A7** | BIG TCP active (`gro_max_size` > 65536) | Read sysfs | 196608 |
| **A8** | Pod MTU is 9000 | Test pod | 9000 |
| **A9** | Cilium agent has **no CPU limit** | `kubectl get ds cilium -o yaml` | No `limits.cpu` |
| **A10** | 📊 **B3: cross-node pod-to-pod ≥ 92 % of line rate** | `bench-pod-network.sh` | Recorded, within target |
| **A11** | 📊 Pod-to-pod is within 3 % of host-to-host | Compare both iperf3 runs | Gap < 3 % |
| **A12** | 📊 Pod→ClusterIP within 3 % of pod→pod | Benchmark | Within target |
| **A13** | 📊 TCP_RR p99 latency within target | Benchmark | Recorded |
| **A14** | BGP sessions established on every node | `cilium bgp peers` | All `established` |
| **A15** | PodCIDRs visible in the switch's BGP table | `show ip route bgp` on a leaf | Present |
| **A16** | Only labeled services are advertised | Create an unlabeled LB service | Not advertised |
| **A17** | Cross-rack pod-to-pod connectivity works | Ping/iperf between racks | Works (or N/A at M1) |
| **A18** | Hubble flows are observable | `hubble observe` | Flows streaming |
| **A19** | A node reboot restores networking automatically | Reboot one node | Ready + connectivity within 3 min |
| **A20** | `policyEnforcementMode: default` (not `always`) | Grep values | `default` — Phase 16 changes it |
| **A21** | Benchmark baseline committed | `ls benchmarks/baselines/b3-*.json` | Present |

---

## ↩️ ROLLBACK

```bash
helm -n kube-system rollback cilium
# Full removal (⚠️ disconnects ALL pods):
cilium uninstall
# Clean per-node eBPF state if a reinstall misbehaves:
kubectl -n kube-system rollout restart ds/cilium
```
⚠️ Removing the CNI makes every node `NotReady` and every pod unreachable. Running containers keep running but cannot communicate.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Nodes stay `NotReady` after install | Cilium cannot reach the API server | `k8sServiceHost`/`Port` must be set — without kube-proxy there is no ClusterIP path to the API yet |
| `KubeProxyReplacement: Partial` | Kernel too old, or a required config is missing | Check the kernel version; `cilium-dbg status --verbose` names the missing feature |
| Cross-node pod traffic fails, same-node works | Native routing without route propagation | Enable `autoDirectNodeRoutes` for same-L2; BGP for cross-subnet. Check `ip route` on the node. |
| Throughput far below line rate | MTU mismatch somewhere in the path | Verify 9000 on: pod, node interface, VLAN subinterface, **and every switch port**. One 1500 hop halves it. |
| BGP session stuck in `active`/`connect` | Peer address wrong, ASN mismatch, or the switch is not configured | `cilium bgp peers -o json`; check the switch's BGP config and prefix-lists |
| BGP established but no routes advertised | Advertisement label selector does not match | Confirm `CiliumBGPAdvertisement` labels match `peerConfigRef`'s `matchLabels` |
| Intermittent packet loss under load | Cilium agent CPU-throttled | Remove the CPU limit (A9). This is the classic cause. |
| `cilium connectivity test` fails on `to-world` | Egress NAT or upstream firewall | Check `bpf.masquerade`; verify the node can reach the internet |
| Service load balancing is uneven | Maglev seed not set, or too few backends | Set `maglev.hashSeed`; Maglev needs a reasonable backend count to distribute well |
| DSR does not work across racks | DSR requires the reply path to reach the client | `dsrDispatch: geneve` handles L3; verify the switch does not drop Geneve |
| Hubble UI empty | Relay not reachable, or metrics disabled | `cilium hubble port-forward`; check `hubble.relay.enabled` |

---

## 🚫 DO NOT

- **Do not** enable VXLAN/Geneve tunnel mode. It costs 15–30 % of throughput and this design does not need it.
- **Do not** leave kube-proxy installed. Partial replacement gives you both taxes.
- **Do not** set a CPU limit on the Cilium agent.
- **Do not** set `policyEnforcementMode: always` yet. With no policies defined, it drops all traffic. Phase 16.
- **Do not** enable transparent encryption (WireGuard/IPsec). ADR-023; it costs 30–50 % at 100 GbE.
- **Do not** advertise all LoadBalancer services by default. Require the label.
- **Do not** configure SR-IOV or RDMA here. Phase 21 adds the secondary network; Cilium governs only the default interface.
- **Do not** skip `cilium connectivity test`. It is the only check that exercises the full matrix.
- **Do not** accept a B3 result below target and "move on." The whole performance thesis rests on this number.

---

## 📤 HANDOFF

`evidence/phase-13/handoff.md` must state:

1. **📊 B3 results** — pod-to-pod throughput, latency, and the gap versus host-to-host. Phase 22 and Phase 48 both compare against this.
2. **Confirmation that native routing and full kube-proxy replacement are active.**
3. **BGP state** — ASNs, peer addresses, session status, and what is advertised. Phase 17's LoadBalancer services depend on it.
4. **The LoadBalancer IP pool** and the `nexus.io/advertise` label requirement — Phase 17 uses both.
5. **MTU verified end-to-end** — Phase 21's RDMA work assumes 9000 works.
6. **`policyEnforcementMode` is `default`** — Phase 16 changes it to `always` after policies exist.
7. **Any node where BIG TCP or an offload could not be enabled**, and why.
8. **Hubble endpoints** — Phase 45 scrapes these metrics.

---

## ➡️ NEXT

**[PHASE-14 — Node Onboarding & Label Taxonomy](PHASE-14.md)** — automate hardware labeling, the taint taxonomy, and the readiness gate that keeps unvalidated nodes out of the workload pool.
