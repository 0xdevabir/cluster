# PHASE 03B — Campus Network Integration & Uplink Budget

| | |
|---|---|
| **Stage** | C — Campus Harvest Plane |
| **Estimated effort** | 4–5 hours (plus campus IT scheduling) |
| **Depends on** | 03, 01B, **04B (network approval signed)** |
| **Blocks** | 08B, 14B, 25B |
| **Risk** | 🔴 R-22 — uplink saturation is the fastest path to R-19, the programme-ending risk |
| **Blast radius** | Every participating lab's teaching traffic |
| **Architecture refs** | `ULTIMATE-PLAN.md#49-the-campus-network-reality`, `CAMPUS-FABRIC.md#5-the-1-gbe-reality`, `#82-the-cache-seed`, R-19, R-22 |

---

## 🎯 MISSION

Integrate harvest nodes into the campus network **as a strictly subordinate tenant of it** — a defined segment, a modeled per-lab uplink budget enforced as a schedulable resource, traffic shaping that always yields to teaching traffic, and working WoL/PXE reachability into every participating room.

> 💡 **WHY this precedes any booting.** Phase 03 designs a fabric we own and can saturate at will. Here we own nothing. The uplink is a shared resource with a prior claimant, and the single most likely way to lose the whole programme is a lab whose video conferencing stuttered the week we arrived. The budget must exist and be enforced *before* the first thirty machines simultaneously pull a 2 GB image.

> ⚠️ **Preflight includes a signed network approval.** No VLAN request, no DHCP change, no directed-broadcast configuration happens on a hunch that IT will be fine with it.

---

## ✅ PREFLIGHT

```bash
# 1. Signed network approval from campus IT exists and names a contact
test -f docs/campus/network-approval.md && grep -q 'Approved by' docs/campus/network-approval.md

# 2. At least one lab agreement is signed
tools/campus/check-consent.sh

# 3. Phase 01B uplink data present (or explicitly null with a plan to measure here)
yq -r '.[] | [.lab, .uplinkSpeedMbps, .measuredBaselineMbps.classHoursP95] | @tsv' inventory/campus/uplinks.yaml

# 4. Phase 03 complete — the core IP plan exists, so campus addressing extends it
#    rather than colliding with it
test -f inventory/network/ip-plan.yaml
```

---

## 📦 DELIVERABLES

```
docs/campus/
  network-design.md                 # segment design, addressing, WoL/PXE reachability
  uplink-budget.md                  # THE deliverable — per-lab ceilings and the math
  shaping-design.md                 # how NEXUS traffic is kept below teaching traffic
inventory/campus/
  uplinks.yaml                      # completed: measured speeds, ceilings, switch/port map
  segments.yaml                     # VLAN/subnet per building or lab, DHCP scope, relay
clusters/nexus-prod/infra/campus/
  locality-topology.yaml            # Kueue TAS topology: campus → building → lab → node
  uplink-resource.yaml              # per-lab uplink modeled as a schedulable resource
  cilium-harvest-policy.yaml        # default-deny + the narrow allow-list harvest nodes need
  harvest-egress-shaping.yaml       # per-node traffic class, applied by DaemonSet
tools/campus/
  uplink-budget.py                  # computes ceilings from uplinks.yaml → uplink-budget.md
  check-wol-reach.sh                # proves a wake packet reaches every lab segment
  measure-uplink.sh                 # off-hours only; guarded against class-hours execution
evidence/phase-03B/
  preflight.md plan.md acceptance.md handoff.md deviations.md
```

---

## 📋 TASKS

### Task 1 — Segment design

Harvest nodes must be **separable from the teaching network by a single switch change**, so that campus IT can isolate us instantly if we misbehave. That property is worth more than any addressing elegance.

**`inventory/campus/segments.yaml`**:

```yaml
- id: harvest-dt-4f
  building: daffodil-tower
  floor: 4
  labs: [cse-402, cse-403]
  vlan: 1240                        # assigned BY campus IT, recorded here — never chosen by us
  subnet: 10.240.4.0/23
  gateway: 10.240.4.1
  dhcp:
    authority: nexus-seed           # our seed node serves this scope, by written agreement
    range: 10.240.4.50-10.240.5.200
    pxe: { nextServer: 10.200.0.10, bootfile: "ipxe.efi" }
  wol:
    method: directed-broadcast      # directed-broadcast | per-segment-relay | agent-relay
    relayHost: null
  isolationSwitch: "shut vlan 1240 on sw-dt-4f-02"   # the one command IT runs to cut us off
```

> 💡 **`isolationSwitch` is not decoration.** Write the literal command campus IT would run, put it in `network-approval.md`, and tell them it exists. An escape hatch you handed them yourself buys more trust than any amount of assurance that you will not need one.

**Addressing rule:** harvest nodes get DHCP addresses from a dedicated range and are **never** given static addresses in the teaching range. A machine that returns to Windows must return to the lab's normal addressing with nothing left behind (promise 2).

### Task 2 — Compute the uplink budget

**`tools/campus/uplink-budget.py`** implements this model and emits `docs/campus/uplink-budget.md`:

```
Per lab:
  U          = uplinkSpeedMbps
  B_class    = measuredBaselineMbps.classHoursP95      # what teaching already uses
  B_off      = measuredBaselineMbps.offHoursP95

  Ceiling_class = min(0.40 × U,  U − B_class − headroom)     # headroom = 0.15 × U
  Ceiling_off   = min(0.70 × U,  U − B_off   − headroom)

  ⚠️ If Ceiling_class ≤ 0 the lab is CLASS-HOURS EXCLUDED — Mode B is impossible there
     regardless of what the agreement permits. The network says no before the paperwork does.

Per-machine sustained share (planning figure):
  PerNode_off = Ceiling_off / machines_in_lab
  → for a 30-machine lab on a 1 Gb uplink: 700/30 ≈ 23 Mb/s ≈ 2.9 MB/s per machine

Cold-start cost check (the number that actually matters):
  ImageBytes × machines  ÷  (Ceiling_off × P2P_efficiency)  ≤  cold_start_budget (300 s, G17)
  With Spegel P2P (25B), the numerator collapses to ~1× ImageBytes per lab, not N×.
  Without it, a 2 GB image × 30 machines needs ~11 minutes of the entire off-hours ceiling.
  ∴ P2P caching is not an optimization; it is a precondition for meeting G17.
```

Record the per-lab result in `uplinks.yaml` and surface it in the report. **A lab whose numbers do not work is documented as such and harvested anyway at reduced concurrency** — not quietly overloaded.

### Task 3 — Make the uplink a schedulable resource

Modeled exactly as Phase 02's per-rack power budget is modeled for Phase 31's power-aware scheduling.

**`clusters/nexus-prod/infra/campus/uplink-resource.yaml`** — each lab exposes a bounded quantity that workloads consume:

```yaml
apiVersion: kueue.x-k8s.io/v1beta1
kind: ResourceFlavor
metadata:
  name: harvest-cse-402
spec:
  nodeLabels:
    nexus.io/plane: harvest
    nexus.io/lab: cse-402
---
# The lab's ClusterQueue carries an uplink dimension alongside cpu/memory/gpu.
# Workloads declare nexus.io/uplink-mbps; admission stops when the lab's ceiling is reached.
  quotas:
    - resource: nexus.io/uplink-mbps
      nominalQuota: 700          # off-hours ceiling; the Oracle swaps this at window boundaries
```

Jobs that stream data declare their expected bandwidth; jobs that do not are charged a small default. **This is the mechanism that prevents a single careless sweep from consuming a lab's entire uplink** — and it is why 25B's caching matters, since cached bytes are not charged against the ceiling.

### Task 4 — Per-node egress shaping

Kueue admission is coarse and advisory; the kernel is where the promise is kept. A DaemonSet on every harvest node installs a traffic class that:

- caps NEXUS egress at `PerNode_off` (or `PerNode_class` during class hours)
- marks all NEXUS traffic at a **lower DSCP priority than default**, so campus QoS deprioritizes us at every hop
- yields immediately under contention rather than competing fairly — we are not teaching traffic's peer

```bash
# Illustrative; the DaemonSet renders this per node from the lab's budget.
tc qdisc add dev <iface> root handle 1: htb default 10
tc class add dev <iface> parent 1: classid 1:10 htb rate ${PER_NODE_MBPS}mbit ceil ${PER_NODE_MBPS}mbit
# Mark NEXUS traffic low-priority end-to-end
iptables -t mangle -A OUTPUT -m cgroup --path nexus.slice -j DSCP --set-dscp-class CS1
```

> ⚠️ **Verify the shaping applies to image pulls and checkpoint flushes**, not only to workload pod traffic. The kubelet's own pulls are the largest single flow a harvest node generates, and they are the easiest to forget.

### Task 5 — WoL and PXE reachability

A wake packet is a broadcast; campus networks routinely drop directed broadcasts across VLANs. **Prove reachability per segment before 08B depends on it** (R-26).

**`tools/campus/check-wol-reach.sh`** — for each segment, send a magic packet to a known-off machine and confirm it wakes:

```bash
wakeonlan -i <segment-broadcast> <mac>          # or a per-segment relay agent
# Confirm with an ARP/ping poll, bounded at 120 s
```

Record per segment: method that worked, the fallback if directed-broadcast is blocked, and the measured wake latency. If no method works for a segment, that segment is **Mode A-incapable** — record it; 08B and 14B must not assume otherwise.

PXE likewise: confirm the DHCP scope hands out the correct `next-server`/`bootfile`, and that it does **not** leak into the teaching range. A lab machine that unexpectedly netboots during a class is a promise-2 violation and an R-19 event.

### Task 6 — Network policy for harvest nodes

Harvest nodes are on a network hundreds of people can physically plug into. Default-deny, with the narrowest possible allow-list:

**`clusters/nexus-prod/infra/campus/cilium-harvest-policy.yaml`** permits only:

- kube-apiserver (control plane), on its port
- the lab's Spegel peers, within the lab only (25B)
- the lab's cache seed for checkpoints and datasets (25B)
- Core Plane object storage for checkpoint tier-3 flush
- DNS, NTP

**Explicitly denied:** harvest → harvest across labs, harvest → Core Plane storage backends (Ceph, etcd), harvest → any teaching subnet, harvest → internet egress except through the registry proxy. A compromised harvest node must not be able to reach anything that matters.

### Task 7 — Locality topology for the scheduler

**`clusters/nexus-prod/infra/campus/locality-topology.yaml`** — Kueue TAS levels, tightest last:

```yaml
levels:
  - nodeLabel: nexus.io/campus       # campus
  - nodeLabel: nexus.io/building
  - nodeLabel: nexus.io/lab          # ← the tightest domain; anything with inter-node
                                     #   traffic is placed entirely within one lab or not at all
```

This is what makes same-lab placement (25B restore locality, 36B Local-SGD training) expressible rather than accidental.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Command |
|---|---|---|
| 1 | Every participating lab has a measured or IT-confirmed uplink speed and computed ceilings | `tools/campus/uplink-budget.py --check` |
| 2 | No computed ceiling exceeds 40 % (class) / 70 % (off-hours) of its uplink | `tools/campus/uplink-budget.py --check` |
| 3 | Labs with `Ceiling_class ≤ 0` are marked class-hours excluded | `yq '.[] \| select(.nexusCeilingMbps.classHours <= 0) \| .lab' inventory/campus/uplinks.yaml` |
| 4 | A wake packet reaches every enrolled segment, with method and latency recorded | `tools/campus/check-wol-reach.sh --all` |
| 5 | PXE offers reach the harvest range and **do not** reach the teaching range | packet capture on both, committed to evidence |
| 6 | Egress shaping is active on a test node and caps image-pull traffic, verified under load | `tc -s class show dev <iface>` before/after a pull |
| 7 | Default-deny policy blocks harvest→teaching and harvest→Ceph; allow-list paths work | `cilium connectivity test` + explicit negative tests |
| 8 | `isolationSwitch` command is documented per segment and acknowledged by campus IT | manual review of `network-approval.md` |
| 9 | No uplink measurement was run during class hours | `measure-uplink.sh` refuses; evidence timestamps confirm |

---

## ↩️ ROLLBACK

```bash
# Per segment, in escalating order:
kubectl cordon -l nexus.io/lab=<lab>            # stop new work
kubectl drain  -l nexus.io/lab=<lab> --ignore-daemonsets
# and, if the network itself is the problem, hand IT the documented isolation command:
#   shut vlan <id> on <switch>
```

Removing the harvest VLAN must return the lab to its exact prior state. Verify this on the first lab before enrolling the second.

---

## 🧯 TROUBLESHOOTING

| Symptom | Cause | Action |
|---|---|---|
| Directed broadcast for WoL blocked | Standard on most campus L3 | Deploy a per-segment relay (a small always-on machine or the cache seed) that emits the packet locally. Record the deviation. |
| DHCP conflicts with the campus scope | Two authorities on one segment | Stop immediately. This breaks teaching machines. Get IT to scope-split or dedicate the VLAN before retrying. |
| Uplink measured far below its nameplate | Oversubscribed access switch chain (very common: switch daisy-chaining) | Trust the measurement, not the diagram. Set ceilings from measured reality and note the chain in `segments.yaml`. |
| Shaping does not affect image pulls | Kubelet traffic not in the shaped cgroup | Shape at the interface level, not the cgroup level, or move containerd into `nexus.slice`. Re-verify under a real pull. |
| Teaching traffic degrades anyway | Ceiling too high, or headroom too small | Halve the ceiling immediately, then investigate. Never debug this at the lab's expense — R-19. |
| Cold-start math cannot meet G17 at any legal ceiling | Uplink genuinely too small | Correct. This is why 25B exists; the answer is caching, never a higher ceiling. |

---

## 🚫 DO NOT

- Do not choose a VLAN, subnet, or DHCP scope yourself. Campus IT assigns; you record.
- Do not run bandwidth measurements during class hours. The tool must refuse.
- Do not raise a ceiling to make a benchmark pass.
- Do not give harvest nodes routes to teaching subnets, Ceph, or etcd.
- Do not enable PXE on any range that includes non-participating machines.
- Do not skip the isolation-command documentation because "we would never need it."
- Do not build the dataset/image caching here — that is 25B. This phase establishes the budget that makes caching mandatory.

---

## 🤝 HANDOFF — write `evidence/phase-03B/handoff.md`

Must state:

- Per lab: VLAN, subnet, uplink speed, class/off-hours ceilings, per-node share, and whether it is class-hours excluded.
- WoL method that works per segment, measured wake latency, and any segment where WoL is impossible (08B needs this to set its yield expectations).
- The cold-start arithmetic per lab and the resulting P2P cache requirement handed to 25B.
- The `isolationSwitch` command per segment, and campus IT's acknowledgement of it.
- Any deviation from the approved design, and IT's sign-off on the deviation.
