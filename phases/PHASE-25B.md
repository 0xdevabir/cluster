# PHASE 25B — Edge Cache & the 1 GbE Data Path

| | |
|---|---|
| **Stage** | C — Campus Harvest Plane |
| **Estimated effort** | 5–6 hours |
| **Depends on** | 25, 03B, 14B |
| **Blocks** | 31B, 36B, 52B |
| **Risk** | 🔴 R-22 — this phase is the mitigation for uplink saturation |
| **Blast radius** | Every participating lab's uplink |
| **Architecture refs** | `CAMPUS-FABRIC.md#8-locality-domains--the-data-path`, `#7-the-backend` (M3, M4), `ULTIMATE-PLAN.md#49-the-campus-network-reality`, Gate G17 |

---

## 🎯 MISSION

Make **N machines in a lab cost approximately 1× the bytes, not N×** — per-lab peer-to-peer image distribution, a lab-local dataset cache with an elected seed, and a checkpoint landing zone that survives the loss of any single borrowed machine.

> 💡 **WHY this is a precondition, not an optimization.** Phase 03B's arithmetic is unforgiving: a 2 GB image pulled independently by 30 machines is 60 GB, which is roughly **11 minutes of a lab's entire off-hours uplink ceiling** — and Gate G17 budgets 5 minutes for the whole room to come online. There is no ceiling adjustment that fixes this; the only fix is not fetching the same bytes twice. Everything in this phase exists to collapse an O(N) cost into O(1).

> 🎯 **This is also the mechanism that makes eviction cheap.** A restart that re-reads its inputs over the uplink converts a cheap eviction into an expensive one. Local-first restore (31B) only works if the data is genuinely local — which is what the cache seed provides.

---

## ✅ PREFLIGHT

```bash
# 1. Uplink budget exists and shaping is enforced
test -f docs/campus/uplink-budget.md
tc class show dev <iface>          # on a harvest node: the shaping class is present

# 2. The Oracle is publishing tiers, so we know when a lab is warm vs. cold
kubectl get nodes -l nexus.io/plane=harvest -L nexus.io/availability-tier

# 3. Phase 25 complete — local ephemeral storage (LVM LocalPV / generic ephemeral) exists.
#    On harvest nodes the equivalent is tmpfs + whatever ephemeral space the design allows.

# 4. A cache seed candidate identified per lab: the most stable machine with the largest disk
#    (lab technician workstation or instructor console — with the owner's explicit agreement,
#     since a seed is the one machine we ask to stay on)
yq -r '.[] | [.id, .cacheSeed] | @tsv' inventory/campus/labs.yaml
```

> ⚠️ **A cache seed is an extra ask.** It is the one machine we would like to stay powered on outside the normal window. That is a separate conversation with the lab owner, and it must be recorded in the agreement (04B). If they say no, the lab still works — it just pays uplink cost. **Never designate a seed by assumption.**

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/storage/campus/
  spegel-harvest.yaml               # P2P image mirror, scoped per lab
  lab-cache/                        # dataset cache DaemonSet/StatefulSet + seed election
    seed-election.yaml cache-daemonset.yaml pvc.yaml
  checkpoint-landing.yaml           # tier-2 checkpoint target per lab
  dataset-warmer.yaml               # pre-stage hot datasets before a window opens
tools/campus/
  warm-lab.sh                       # pre-stage images + datasets ahead of a window
  cache-report.sh                   # hit rate, bytes saved, uplink bytes avoided
  cold-start-bench.sh               # G17 measurement harness
docs/campus/
  data-path.md                      # the three tiers, their latencies, and the rules
evidence/phase-25B/
  preflight.md plan.md acceptance.md handoff.md deviations.md
  cold-start/                       # G17 timing runs, 3 labs × 3 repeats
```

---

## 📋 TASKS

### Task 1 — Per-lab P2P image distribution

Spegel, scoped so that peers only discover peers **within the same lab**. Cross-lab peering would push traffic over the very uplinks we are protecting.

```yaml
# spegel-harvest.yaml (illustrative)
spegel:
  # Peer discovery restricted to the lab topology label — NOT cluster-wide
  peerDiscovery:
    topologyKey: nexus.io/lab
  registries: [<harbor-core>]
  # A cold lab still needs one machine to fetch from Core; everything else pulls from it.
  resolveLatestTag: false            # Rule 4 — no :latest, ever
```

**The math this must achieve:**

```
Without P2P:  ImageBytes × N_machines  over the uplink
With P2P:     ImageBytes × ~1.1        over the uplink, rest over the lab LAN (1 Gb, unshared)

2 GB image, 30 machines, 700 Mb/s ceiling:
   without:  60 GB → ~11.4 min of the full ceiling      ❌ fails G17
   with:    2.2 GB → ~25 s of uplink + LAN fan-out       ✅
```

> ⚠️ **Verify the fan-out actually stays on the LAN.** A misconfigured peer discovery that lets a lab pull from a *different* lab's peer is worse than no P2P at all — it doubles uplink traffic and crosses a locality boundary. Test it explicitly with a packet capture on the uplink.

### Task 2 — Seed election and the lab cache

Each lab elects **one cache seed** (`CAMPUS-FABRIC.md §8.2`), preferring, in order: an agreed always-on machine → the machine with the largest disk and longest observed uptime → the first node to join.

| Seed role | Content | Persistence |
|---|---|---|
| Spegel peer priority | Image layers | Ephemeral (RAM/scratch), rebuilt per window |
| Dataset cache | Hot read-only datasets, pinned by the warmer | Ephemeral, TTL'd, re-warmed per window |
| Checkpoint landing zone | Tier-2 checkpoints from the lab's nodes | Held until flushed to Core |
| Metrics relay | The lab's telemetry, aggregated into one uplink stream | — |

**The load-bearing constraint: the seed must never be load-bearing.**

```
If the seed disappears mid-window:
  → the lab keeps running, paying uplink cost
  → in-flight tier-2 checkpoints fall back to tier-3 (Core) directly
  → a new seed is elected within 60 s
  → NOTHING is lost, because tier-2 is a cache, never the only copy
```

This is a direct application of `CAMPUS-FABRIC.md §3.3`: no borrowed machine may hold the only copy of anything. Test it by killing the seed under load (Task 6).

### Task 3 — The three-tier data path

Document and implement (`docs/campus/data-path.md`):

| Tier | Location | Latency | Holds | Lost when |
|---|---|---|---|---|
| **T-local** | Node tmpfs / ephemeral scratch | µs–ms | Working set, current checkpoint | Node powers off (every window close) |
| **T-lab** | Cache seed, same LAN | ~1–10 ms, 1 Gb unshared | Images, hot datasets, recent checkpoints | Seed leaves (tolerated) |
| **T-core** | Core Plane object store | uplink-bound, shaped | Durable checkpoints, datasets of record, all results | Never (Plane A durability) |

**Read path:** T-local → T-lab → T-core. **Write path:** T-local immediately, T-lab async (< 60 s), T-core async (< 10 min).

**Rule:** anything a user would be upset to lose must reach T-core. Anything in T-local or T-lab is, by construction, disposable.

### Task 4 — The dataset warmer

Pre-stage before the window opens, so that machines coming online at 18:30 find their data already in the lab rather than fetching it in a 30-way stampede.

```bash
tools/campus/warm-lab.sh --lab cse-402 --at "18:00"
# Reads: pending workloads targeting cse-402, their declared datasets and images
# Pulls into the seed BEFORE the wake, at low priority, over 30 minutes
# → by the time nodes boot at 18:30, the bytes are already inside the lab
```

> 💡 **Warming exploits time we are already wasting.** The half-hour before a window opens is a period when the uplink is quiet and no machine is running. Spending it at low priority costs nothing and removes the single largest cold-start cost. This is `CAMPUS-FABRIC.md §7` M4 in its most effective form.

Workloads declare their datasets (36B provides the template); the warmer resolves them to content-addressed blobs and pins them with a TTL.

### Task 5 — Uplink accounting for cached bytes

03B made the uplink a schedulable resource. This phase must make it **accurate**:

- Bytes served from T-lab are **not** charged against the lab's uplink quota.
- Bytes fetched from T-core **are** charged, against the pulling node's lab.
- The cache reports `bytes_served_local` and `bytes_fetched_remote` per lab, per window.

`tools/campus/cache-report.sh` surfaces the number that matters:

```
cse-402, window 2026-09-15 18:30–07:30
  images:    served local 58.2 GB | fetched remote 2.1 GB | hit rate 96.5 %
  datasets:  served local 41.0 GB | fetched remote 6.4 GB | hit rate 86.5 %
  uplink bytes avoided: 99.2 GB  (would have been ~14× the ceiling's hourly budget)
```

This feeds 33B's `overhead` classification directly.

### Task 6 — Measure the cold start (G17)

**`tools/campus/cold-start-bench.sh`** — the gate measurement, run in **3 different labs, 3 repeats each**:

```
t0  wake packets emitted
t1  first node Ready
t2  50 % of nodes Ready
t3  first workload pod Running
t4  90 % of admitted pods Running        ← G17: p95 ≤ 300 s from t0
```

Run it **twice per lab**: once cold (no warming, empty cache) and once warmed. Report both. The warmed number is the operational one; the cold number tells you what a lab looks like after a long holiday, which is a real state you will hit.

**Also test seed loss:** kill the seed at t3 and confirm the room continues, checkpoints redirect to T-core, and a new seed is elected within 60 s.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Command |
|---|---|---|
| 1 | Image pull for a 30-machine room costs ≤ 1.3× the image size over the uplink | packet capture on the uplink during a cold wake |
| 2 | Peer discovery never crosses a lab boundary | capture + `spegel` peer list per node |
| 3 | **G17: cold start of a 30-PC room, p95 ≤ 5 min from WoL to first workload pod** (warmed) | `cold-start-bench.sh`, 3 labs × 3 repeats, committed |
| 4 | Cold (unwarmed) cold-start figure measured and reported honestly, even if it exceeds 5 min | same harness, `--no-warm` |
| 5 | Killing the cache seed mid-window loses no work and elects a new seed within 60 s | chaos test, recorded |
| 6 | Bytes served from T-lab are not charged against the uplink quota; remote bytes are | `cache-report.sh` vs. Kueue quota consumption |
| 7 | Dataset warmer completes before the window opens and does not exceed low-priority shaping | timed run, `tc` counters |
| 8 | No dataset classified as restricted (04B) is present in any lab cache | Kyverno policy report + cache inventory scan |
| 9 | Cache contents are wiped on node power-off (verify tmpfs/ephemeral, not disk) | post-shutdown disk hash (08B's harness) |

---

## ↩️ ROLLBACK

Disable Spegel and the lab cache; every node pulls from Core directly. **The fabric still works — it just becomes slow and uplink-hungry, and G17 will fail.** If you roll back, immediately reduce per-lab concurrency so the uplink ceiling is still respected. Do not run an uncached fabric at full concurrency; that is R-22 in its most direct form.

---

## 🧯 TROUBLESHOOTING

| Symptom | Cause | Action |
|---|---|---|
| Cold start far above 5 min | Cache empty, or P2P not peering | Check peer discovery first — a silent peering failure looks exactly like a slow network. Warm the lab and re-measure. |
| Uplink traffic still O(N) | Peers not discovering each other (network policy, or topology key wrong) | 03B's default-deny may be blocking peer ports. Add the narrow intra-lab allow rule. |
| Seed disk fills | TTL not enforced, or dataset pinning unbounded | Cap the cache size; evict by LRU. A full seed must degrade to pass-through, never to an error. |
| Checkpoints piling up on the seed | T-core flush failing or shaped too aggressively | Alert on tier-2 depth. A seed holding the only copy of a checkpoint violates §3.3 — this is urgent, not cosmetic. |
| Warming runs during class hours | Scheduler misconfigured | Fix immediately; warming is bulk traffic and must be off-hours only (R-19). |
| Cross-lab peering observed | Topology key misconfigured | Fix and re-verify with a capture. Do not trust the config; trust the packets. |

---

## 🚫 DO NOT

- Do not allow cross-lab peer discovery.
- Do not designate a cache seed without the lab owner's explicit agreement to keep it on.
- Do not let any tier-2 content be the only copy of anything.
- Do not cache restricted-class data (04B) in a lab, ever.
- Do not run the warmer during class hours.
- Do not persist cache content to a machine's internal disk (promise 2, 08B).
- Do not implement the eviction/checkpoint *logic* here — this phase provides the landing zone; 31B provides the mechanism.

---

## 🤝 HANDOFF — write `evidence/phase-25B/handoff.md`

Must state:

- Per lab: cache seed identity, its agreed power state, its capacity, and the fallback if it leaves.
- Measured cold-start figures, warmed and cold, per lab, with the G17 verdict.
- Cache hit rates and uplink bytes avoided in the first full window.
- The T-lab → T-core flush latency actually observed — 31B's checkpoint budget depends on it.
- Any lab where P2P underperformed and why (switch config, VLAN isolation, small LAN MTU).
