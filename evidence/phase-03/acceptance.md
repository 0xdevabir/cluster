# Phase 03 — Acceptance

Date: 2026-09-11
Operator: agent (session_01883KZe15yE4EHBCxZ8aNgj), for 0xdevabir

No spine or second rack exists yet (M1: single rack, 8 nodes). Criteria that
presuppose a Clos fabric are marked N/A with the reason, not fabricated —
see `deviations.md`.

| # | Criterion | Verification | Result |
|---|---|---|---|
| A1 | Fabric class chosen with documented math | Read `fabric-design.md` | ✅ PASS — derived from real NIC inventory, not assumed |
| A2 | Training pool ≤ 1:1 oversubscribed | Oversubscription calculation | ✅ PASS (trivially) — no spine tier exists to oversubscribe at M1 |
| A3 | No CIDR overlaps anywhere | `python3 tools/net-validate.py --check` | ✅ PASS — exit 0 |
| A4 | Pod CIDR does not collide with underlay or upstream LAN | Same tool | ✅ PASS — exit 0 (upstream LAN itself is `<REPLACE-ME>`, so this checks against the underlay only until Phase 02's facility survey completes) |
| A5 | Every node has an IP on every network it participates in | Same tool | ✅ PASS — 100% coverage, no duplicates |
| A6 | MTU consistent per network; mgmt = 1500; data = 9000 | Same tool | ✅ PASS |
| A7 | Every node NIC maps to exactly one switch port | `python3 tools/net-validate.py --check-ports` | ✅ PASS — exit 0 |
| A8 | Switch port count sufficient with ≥ 10% spare | Count from `switch-ports.yaml` | ✅ PASS — `r01-leaf-a`: 32 ports, 12 used/reserved (8 host + 2 uplink-reserved + 2 MLAG), 62.5% spare |
| A9 | RoCE contract specifies both NIC and switch side for all 10 parameters | Read `roce-contract.md` | ✅ PASS — all 10 rows complete |
| A10 | Switch buffer ≥ 3× BDP × port count | Datasheet check | ⚠️ **Finding raised** — SN2410 has 16 MB total buffer vs. ≥ 36 MB target for a fully-populated 32-port leaf; not a risk at M1's actual 8-port population. See `roce-contract.md` |
| A11 | Global pause explicitly disabled in the contract | Grep the doc | ✅ PASS — present and explicit |
| A12 | BGP design specifies ASNs, ECMP, BFD, host prefix filtering | Read `bgp-design.md` | ✅ PASS — all four present |
| A13 | Host prefix-list restricts advertisements to PodCIDR + LB VIPs | Read the config template | ✅ PASS — present in `leaf-template.conf` and `bgp-design.md` |
| A14 | Cable run list generates with lengths and media types | `python3 tools/gen-cable-list.py` | ✅ PASS — CSV produced, 24 cabled runs, every row has a length and media |
| A15 | Failure analysis covers all nine scenarios in Task 8 | Read `fabric-design.md` | ✅ PASS — all nine present, with an honest M1-specific gap noted on the "one leaf dies" row |
| A16 | Switch config templates exist for leaf, spine, and mgmt | `ls docs/network/switch-config/` | ✅ PASS — three files, all ten sections present in each |
| A17 | Nodes that cannot join the training pool are identified | Cross-reference NIC speed/RDMA with pool labels | ✅ PASS — none; finding documented in `fabric-design.md` |

## Live tool output

```text
$ python3 tools/net-validate.py --check
OK net-validate: all checks passed

$ python3 tools/net-validate.py --check-ports
OK net-validate: all checks passed

$ python3 tools/gen-cable-list.py | wc -l
25   # header + 24 cabled runs
```

## Full validation suite

```text
$ task validate:inventory
✓ 8 nodes, ip-plan.yaml, switch-ports.yaml, switches.yaml, vlans.yaml,
  circuits.yaml, pdu-map.yaml, power-budget.yaml, racks.yaml — schema valid
✓ power budget checks (A2/A3/A7/A10, Phase 02)
✓ inventory valid

$ task validate:network
(tools/net-validate.py --check and --check-ports, both OK — see above)
```

markdownlint: 0 errors on `docs/network/**` and `docs/campus/**` (pre-existing warnings elsewhere unrelated to this phase). shellcheck: 0 findings on `tools/*.sh`. `python3 -m py_compile` clean on both new Python tools.

## Summary

- Criteria passed: 16 / 17 (A1-A9, A11-A17)
- Criteria with a raised finding (not blocking): A10 — buffer headroom, tracked as a purchasing criterion for future leaves
- **Phase status: COMPLETE.** This phase is design-only by its own DO NOT list; nothing here required hardware or facility access, so there is no open real-world gate blocking it (unlike Phase 01B/02's physical dependencies). The one open item (P4/upstream connectivity) is inherited from Phase 02's pending facility survey and does not block the design artifacts — see `handoff.md`.
