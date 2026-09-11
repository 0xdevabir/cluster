# BGP Design

Phase 03 Task 5. L3-to-the-host per `ARCHITECTURE.md#L2.2`, eliminating spanning tree and large L2 domains.

## Target topology (M3+)

```text
                    ┌──────────┐  ┌──────────┐
                    │ SPINE-1  │  │ SPINE-2  │     AS 65000
                    └────┬─────┘  └─────┬────┘
                  eBGP unnumbered (RFC 5549, IPv6 link-local next-hop)
                    ┌────┴─────┐  ┌─────┴────┐
                    │ LEAF-R01 │  │ LEAF-R02 │     AS 65101, 65102
                    └────┬─────┘  └─────┬────┘
                  eBGP unnumbered to each host
                    ┌────┴─────────────────────┐
                    │ hosts in r01: AS 65201    │  <- Cilium BGP Control Plane
                    │  advertise: PodCIDR /24   │
                    │             LB VIPs /32   │
                    └───────────────────────────┘
```

## M1 status — not yet operationally enabled

Per Task 2's staged topology, **BGP activates at M3** when the spine tier is introduced. At M1 (single rack, no spine, hosts single-homed to one leaf pair), there is nothing for BGP to route between — reachability within `r01` is handled by the leaf switches' local L2/L3 forwarding and MLAG.

**The addressing scheme is assigned now regardless** (per the phase file's own warning: "renumbering a live cluster is a multi-day outage"):

- `spineAsn: 65000` — reserved, unused until a spine exists
- `leafAsnBase: 65100` → `r01` leaves share **AS 65101** (both `r01-leaf-a` and `r01-leaf-b`, per the MLAG pair sharing one ASN toward hosts/spine — `inventory/network/switches.yaml`)
- `hostAsnBase: 65200` → all `r01` hosts share **AS 65201**
- `unnumbered: true` (RFC 5549) — no `/31`s to manage
- `ecmpMaxPaths: 8`
- `gracefulRestart: true` — a Cilium agent restart must not blackhole the node
- BFD: enabled, 300 ms × 3 = 900 ms convergence

Phase 13's `CiliumBGPClusterConfig` reads these ASNs verbatim once BGP is actually turned up.

## Rules

- All hosts in a rack share one ASN — they never transit, so `allowas-in` is not needed.
- Leaves advertise a rack summary upward; spines carry the full table (small at this scale).
- ECMP across both uplinks and both leaves once dual-homing exists.
- Route filtering: hosts may advertise **only** their own PodCIDR and LB VIPs — this is a security control, not just hygiene. A compromised node must not be able to blackhole the cluster by advertising an arbitrary prefix.

## Host prefix-list (applied on every leaf's host-facing session, Phase 21)

```text
ip prefix-list HOST-IN seq 5  permit 10.244.0.0/14 ge 24 le 24
ip prefix-list HOST-IN seq 10 permit 10.10.0.0/24 ge 32 le 32
ip prefix-list HOST-IN seq 99 deny 0.0.0.0/0 le 32
```

This exact prefix-list is embedded in `docs/network/switch-config/leaf-template.conf`.
