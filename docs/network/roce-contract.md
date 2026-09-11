# RoCEv2 Lossless Contract

Phase 03 Task 4. Both ends of every fabric link must agree on this table exactly, or RoCE degrades silently (R-10). This document is the design; Phase 21 applies it to real NICs and switches, and Phase 22 validates it under load.

## The contract

| Parameter | Value | NIC side | Switch side |
|---|---|---|---|
| Traffic class | DSCP 26 → PCP 3 | `mlnx_qos -i <dev> --trust dscp`<br>`cma_roce_tos -d mlx5_0 -t 106` | Ingress: `qos map dscp 26 to traffic-class 3` |
| PFC | **Priority 3 only** | `mlnx_qos -i <dev> --pfc 0,0,0,1,0,0,0,0` | `priority-flow-control priority 3 enable` on every fabric port |
| PFC watchdog | Enabled, 200 ms | — | `priority-flow-control watchdog` |
| ECN / WRED | min 150 KB, max 1500 KB, prob 100% | `echo 1 > /sys/class/net/<dev>/ecn/roce_np/enable/3`<br>`echo 1 > .../roce_rp/enable/3` | `random-detect ecn minimum-threshold 150 kbytes maximum-threshold 1500 kbytes` on TC3 |
| CNP (congestion notification) | DSCP 48, priority 6 | `echo 48 > /sys/class/net/<dev>/ecn/roce_np/cnp_dscp` | Priority 6 must also be lossless, or CNPs get dropped under congestion |
| DCQCN | Enabled (default rate params) | `mlx5` defaults are usually correct; tune only with evidence | — |
| Buffer | Dedicated lossless pool ≥ 3 × BDP | — | Per-vendor buffer profile — the #1 thing vendors get wrong by default |
| MTU | 9000 L2 / RoCE MTU 4096 | `ip link set <dev> mtu 9000` | `mtu 9216` (switch overhead allowance) |
| Trust mode | DSCP (not PCP) | `mlnx_qos --trust dscp` | `qos trust dscp` |
| Link-level flow control (global pause) | **DISABLED** | `ethtool -A <dev> rx off tx off` | `no flowcontrol receive/send` |

> ⚠️ **Global pause vs. PFC.** Enabling both, or global pause instead of PFC, produces a fabric that appears to work and then collapses under load with head-of-line blocking across *all* traffic classes. **Global pause off. PFC on priority 3 only.**

## BDP sizing and the M1 buffer finding (A10)

```text
BDP = link_rate × RTT
    = 100 Gb/s × 10 µs = 125 KB   (single hop, leaf only — the M1 case, no spine)
    = 100 Gb/s × 30 µs = 375 KB   (through a spine — M3+)
Recommended lossless headroom ≥ 3 × BDP ≈ 1.1 MB per port (single hop)
→ A 32-port leaf needs ≥ 36 MB of buffer dedicated to the lossless pool.
```

**Finding (A10):** `inventory/network/switches.yaml` records `r01-leaf-a`/`r01-leaf-b` (NVIDIA SN2410) at **16 MB total buffer** — below the ≥ 36 MB target for a fully-populated 32-port leaf at single-hop BDP. At M1's actual population (8 of 32 ports used, only 5 of them RDMA-capable at ≥ 25 Gbps), the realistic per-port share is well within the 16 MB pool, so this is **not a current outage risk** — but it is a documented constraint: do not plan to fully populate this leaf with 100 GbE RDMA hosts without re-verifying buffer headroom, and treat "shared buffer ≥ 32 MB" as a hard purchasing criterion for any M2+ leaf (Phase 05).

## Validation commands (for Phase 21)

```bash
# NIC side
mlnx_qos -i <netdev>                          # confirm PFC on prio 3 only, trust dscp
ethtool -S <netdev> | grep -E 'pause|prio3'   # rx_pause/tx_pause should be ~0 at idle
cat /sys/class/infiniband/mlx5_0/ports/1/hw_counters/np_ecn_marked_roce_packets

# End-to-end
ib_write_bw -d mlx5_0 -x 3 -F --report_gbits -D 30 <peer>   # >= 96% line rate
ib_send_lat -d mlx5_0 -x 3 -F                                 # p99 < 3 us
```

## Scope note

Nothing in this document has been applied to hardware. Phase 07 stages the management network; Phase 21 applies this QoS contract to real NICs/switches after they exist to test against. This is a design artifact only, per the phase's DO NOT list.
