# PHASE 49 — Systematic Tuning: BIOS → Kernel → Runtime

| | |
|---|---|
| **Stage** | 8 — Performance Engineering |
| **Estimated effort** | 5–6 hours (plus reboot cycles) |
| **Depends on** | 48 |
| **Blocks** | 50, 52 |
| **Risk** | 🟠 Medium — tuning changes touch every node; a bad setting degrades the fleet |
| **Blast radius** | Fleet-wide performance and stability |
| **Architecture refs** | `ULTIMATE-PLAN.md#81-the-performance-budget`, Law VIII (*Measure Before You Tune*) |

---

## 🎯 MISSION

Close the gap between measured performance and the **Performance Budget** in `ULTIMATE-PLAN.md §8.1`. Work the stack systematically from BIOS upward, **changing one thing at a time**, measuring each change against Phase 48's baseline, and keeping only what demonstrably helps. Produce a tuning record that explains every non-default setting in the cluster.

> 💡 **WHY a dedicated tuning phase rather than tuning as you go.** Tuning during construction is guesswork: there is no stable baseline to measure against, the subsystem under test depends on others still being built, and every change gets attributed to whatever was happening that week. With Phase 48's harness in place, tuning becomes an experiment: change → measure → keep or revert. **That is the difference between a tuned cluster and a cluster full of settings nobody can justify.**

> ⚠️ **The anti-pattern this phase exists to prevent: cargo-cult tuning.** Every blog post has a list of sysctls. Applying them wholesale produces a cluster with 40 non-default settings, of which perhaps 6 help, 3 hurt, and 31 do nothing — and nobody can remove any of them because nobody knows which is which. **Every setting in this cluster must have a measurement behind it or be reverted.**

---

## ✅ PREFLIGHT

```bash
# 📊 The authoritative baseline — nothing here is valid without it
jq '.' benchmarks/baselines/baseline.json

# The harness works and can reserve capacity
bash benchmarks/harness/quiet-check.sh
bash benchmarks/harness/run.sh --quick --dry-run

# Current non-default settings across the stack
talosctl -n <node> read /proc/cmdline
kubectl get nodes -o json | jq '.items[0].status.nodeInfo'
cat inventory/bios-baseline.yaml    # Phase 01's 20-setting table

# A maintenance window — BIOS changes need reboots
```

---

## 📦 DELIVERABLES

```
tuning/
  experiments/                      # 🎯 one directory per experiment
    EXP-001-hugepages-1g/
      hypothesis.md
      change.yaml
      results.json                  # before/after, 3 runs each
      decision.md                   # KEEP or REVERT, with the reason
  applied/
    bios-settings.yaml              # the final, justified BIOS profile
    kernel-args.yaml
    sysctls.yaml
    runtime-config.yaml
  tuning-record.md                  # 🎯 EVERY non-default setting + its evidence
tools/tuning/
  experiment.sh                     # run one experiment end to end
  apply-profile.sh                  # roll a tuning profile fleet-wide, staged
  audit-settings.sh                 # ⚠️ find settings with no justification
docs/operations/tuning-guide.md
evidence/phase-49/{preflight,acceptance,handoff,deviations}.md
```

---

## 📋 TASKS

### Task 1 — 🎯 The experiment protocol

Every change follows the same protocol. No exceptions, including "obvious" ones.

```
1. HYPOTHESIS      "Enabling 1 GiB hugepages should improve B9 by 1–3 % by
                    reducing TLB misses in the training working set."
                   ⚠️ State the expected magnitude. If you can't, you don't
                      have a hypothesis — you have a guess.
2. PREDICT         Which benchmarks should move, and which should NOT.
3. MEASURE BEFORE  3 runs of the affected benchmarks. Median + spread.
4. CHANGE ONE THING
5. MEASURE AFTER   3 runs, same nodes, same conditions.
6. DECIDE          Improvement > 2× the measured spread → KEEP
                   Otherwise → REVERT
7. RECORD          decision.md, committed, whichever way it went
8. CHECK PREDICTIONS  Did benchmarks move that shouldn't have? ⚠️ That's a finding.
```

> ⚠️ **Step 6's threshold matters.** If B9's run-to-run spread is ±1.5 %, a 1 % "improvement" is noise. **Require the improvement to exceed twice the spread** before keeping a change — otherwise you accumulate settings that do nothing and can never be safely removed.

> 💡 **Step 8 catches the changes that help one thing and hurt another.** `isolcpus` improves MPI jitter and reduces the cores available for data loading, which can hurt B9. Predicting what should *not* move, then checking, is how you find those trades before production does.

**Record reverted experiments as carefully as accepted ones.** "We tried X, it did not help, here is the data" is what stops the same experiment being repeated in eighteen months.

---

### Task 2 — Layer 1: BIOS/firmware

The lowest layer, the largest single wins, and the most expensive to change (reboot per node).

| Setting | Recommended | Expected effect | Verify with |
|---|---|---|---|
| **Above 4G Decoding** | Enabled | ⚠️ **Required** for multiple GPUs | Boot |
| **Resizable BAR** | Enabled | +2–5 % on H2D transfers | B2 |
| **PCIe link speed** | Gen4/Gen5 max | ⚠️ Auto sometimes negotiates low | B2, Phase 23 H10 |
| **CPU power profile** | Max performance | Removes frequency ramp latency | B1, jitter |
| **C-states** | ⚠️ **C1E only, disable deeper** | Removes wake latency; costs idle power | Jitter (Phase 39) |
| **P-states / SpeedStep** | Disabled or performance | Consistent clocks | Jitter |
| **Turbo** | Enabled | Higher single-thread | B7-HPL |
| **SMT / Hyper-Threading** | ⚠️ **Test both** | Helps throughput, hurts latency | B9 vs. jitter |
| **NUMA (node interleaving)** | ⚠️ **Disabled** — expose NUMA | Phase 20 depends on it | `numactl -H` |
| **IOMMU** | Enabled, passthrough mode | Required for SR-IOV; passthrough avoids overhead | B4 |
| **SR-IOV** | Enabled | Phase 21 | VF creation |
| **Memory speed/XMP** | Rated speed | +3–8 % memory bandwidth | STREAM, B1 |
| **Fan curve** | Aggressive | Prevents thermal throttling | Phase 23 H7 |

> ⚠️ **SMT is the setting with a genuine two-sided trade on this platform.** It typically helps throughput-oriented training (more data-loader threads) and hurts latency-sensitive MPI (jitter, cache contention). **Test both, and consider setting it differently per node pool** — SMT off on the HPC pool, on elsewhere. That is a legitimate, evidence-backed heterogeneity, and it must be recorded in Phase 01's BIOS baseline.

> 🚫 **Disabling deep C-states costs real idle power** (Phase 32's budget). Measure the idle draw delta before and after; if it pushes a circuit over budget, the jitter benefit is not free.

**Rolling BIOS changes across 100 nodes without a BMC** (`ULTIMATE-PLAN.md §4.6`): this is where the no-BMC decision hurts most. Options: vendor CLI tools applied from a booted OS where supported, a scripted UEFI shell via PXE, or PiKVM + physical rotation. **Whichever you use, do it in staged waves with benchmark verification between waves.**

---

### Task 3 — Layer 2: kernel and OS

Phase 20 already applied the topology-critical settings. This layer tests the rest.

| Area | Candidates | Measure |
|---|---|---|
| **Hugepages** | 2 MiB (already) → test 1 GiB; THP `madvise` vs `always` | B1, B9 |
| **Scheduler** | `sched_migration_cost_ns`, `sched_autogroup` | Jitter, B9 |
| **Network stack** | `net.core.rmem_max`, `wmem_max`, `netdev_max_backlog`, `tcp_rmem/wmem` | B3 |
| **BBR vs CUBIC** | `net.ipv4.tcp_congestion_control` | B3 |
| **IRQ affinity** | Pin NIC IRQs to NUMA-local cores | B3, B4 |
| **RPS/XPS** | Receive/transmit packet steering | B3 |
| **NIC ring buffers** | `ethtool -G` rx/tx | B3, B4 |
| **NIC coalescing** | `ethtool -C` adaptive vs. fixed | ⚠️ B3 throughput **vs.** B4 latency |
| **Block I/O scheduler** | `none` for NVMe (not `mq-deadline`) | B6 |
| **`nr_requests`, `read_ahead_kb`** | Per device class | B6 |
| **VM tunables** | `swappiness=0`, `dirty_ratio`, `min_free_kbytes` | B6, stability |
| **Transparent hugepage defrag** | ⚠️ `defer+madvise` — `always` causes stalls | Jitter |

> ⚠️ **Interrupt coalescing is the clearest throughput-vs-latency trade in the list.** Higher coalescing means fewer interrupts and better B3 throughput; it also adds microseconds to B4 latency and hurts MPI. **Set it per node pool**, matching Phase 39's HPC pool separation.

> 💡 **IRQ affinity is frequently the largest single network win** and is almost always left at default. If the NIC's interrupts land on cores in a different NUMA node from the NIC, every packet crosses the interconnect. Phase 20's alignment validator already knows the NIC's NUMA node — use it to pin IRQs, then measure B3 and B4.

---

### Task 4 — Layer 3: runtime and framework

| Area | Candidates | Measure |
|---|---|---|
| **GPU clocks** | `nvidia-smi -lgc` locked clocks vs. auto | B1 consistency |
| **Power cap** | The Phase 32 tier table — re-verify the knee | B1, B9, perf/W |
| **Persistence mode** | `nvidia-smi -pm 1` | Removes init latency |
| **NCCL** | Phase 22's sweeps, re-run against the new baseline | B5 |
| **UCX** | `UCX_RNDV_THRESH`, `UCX_TLS` ordering | B4, OSU |
| **PyTorch** | `TORCH_CUDNN_V8_API`, `torch.compile`, TF32 on/off, channels-last | B9 |
| **CUDA** | `CUDA_DEVICE_MAX_CONNECTIONS`, graph capture | B1, B9 |
| **containerd** | Snapshotter (overlayfs vs. stargz), cgroup driver | Pod start time |
| **Cilium** | BIG TCP verification, bandwidth manager, BPF map sizes | B3 |
| **Ceph** | `osd_memory_target`, mClock profile (Phase 27's C10 tuning) | B6, B7 |

> ⚠️ **TF32 is a numerics change, not just a speed knob.** Enabling it can give a large speedup on matmuls at reduced mantissa precision. That is fine for most deep learning and **not** fine for scientific codes that need FP32 semantics. If you enable it by default, say so loudly in the user docs and provide the opt-out — a silent precision change is a correctness incident waiting to be discovered in someone's published results.

> 💡 **`torch.compile` can be worth 10–30 % on B9** and costs compilation time at startup plus occasional graph-break debugging. Test it; recommend it in the golden path; do not force it.

---

### Task 5 — ⚠️ Rolling changes fleet-wide, safely

**`tools/tuning/apply-profile.sh`** — staged rollout with verification.

| Stage | Nodes | Gate before proceeding |
|---|---|---|
| **Canary** | 2 | `--quick` benchmark; 24 h soak; no new alerts |
| **Wave 1** | 10 % | `--quick`; 24 h |
| **Wave 2** | 50 % | `--standard`; 48 h |
| **Full** | 100 % | `--full` |

**The guards:**

| Guard | Rule |
|---|---|
| One change per rollout | Never bundle tuning changes with anything else |
| **Automatic revert on regression** | If `--quick` regresses > 3 %, halt and revert the wave |
| Drain before reboot-requiring changes | Respect PDBs; let jobs checkpoint (Phase 33) |
| **Never touch all control-plane nodes at once** | Same rule as Phase 23's remediation |
| Stagger reboots per power circuit | ⚠️ Phase 32's inrush limit |
| Record the profile version on each node as a label | So you can tell which nodes have what |

> ⚠️ **The node label recording profile version is what makes a partial rollout debuggable.** When wave 2 shows a regression that canary did not, you need to know exactly which nodes have which settings — and `nexus.io/tuning-profile: v3` on every node answers it instantly.

---

### Task 6 — 🎯 The tuning record and the audit

**`tuning/tuning-record.md`** — every non-default setting in the cluster, with its evidence.

```markdown
| Setting | Value | Default | Layer | Evidence | Benefit | Decided |
|---|---|---|---|---|---|---|
| vm.nr_hugepages | 2048 | 0 | kernel | EXP-001 | Mayastor req. | 2026-06-12 |
| net.core.rmem_max | 268435456 | 212992 | kernel | EXP-014 | B3 +6.2 % | 2026-08-14 |
| NIC IRQ affinity | NUMA-local | spread | kernel | EXP-018 | B3 +9.1 %, B4 -0.4 µs | 2026-08-15 |
| SMT (HPC pool) | Disabled | Enabled | BIOS | EXP-007 | jitter 1.8 %→0.6 % | 2026-08-13 |
| tcp_congestion_control | bbr | cubic | kernel | EXP-016 | B3 +1.1 % ⚠️ within noise | ❌ REVERTED |
```

**`tools/tuning/audit-settings.sh`** — the enforcement:
```
Compare the running configuration against tuning-record.md.
🔴 Report any non-default setting with NO entry in the record.
Run it in CI and monthly.
```

> 🚫 **A setting with no entry in the record is a defect**, even if it works. It is a future mystery: nobody will know whether it is load-bearing or leftover, so nobody will ever remove it, and it will be copied to the next cluster. The audit is what keeps the record honest.

---

### Task 7 — 📊 Close the performance budget

Return to `ULTIMATE-PLAN.md §8.1` and account for every remaining percent.

```
PERFORMANCE BUDGET — final accounting
                                  target    achieved   gap    mechanism
  Single-node GEMM vs. bare metal   ≥98 %      99.1 %    —     CDI, no runtime tax
  PCIe H2D vs. theoretical          ≥90 %      92.4 %    —     ReBAR (EXP-003)
  TCP vs. line rate                 ≥94 %      95.8 %    —     BIG TCP + IRQ affinity
  RDMA vs. line rate                ≥96 %      97.2 %    —     lossless class correct
  NCCL busbw @8 vs. theoretical     ≥85 %      87.1 %    —     tuned (EXP-021)
  Training scaling @32 GPUs         ≥88 %      88.6 %    —     topology + overlap
  ─────────────────────────────────────────────────────────────────────────
  ⚠️ REMAINING GAPS
  Training scaling @64 GPUs         ≥84 %      81.2 %   -2.8   fabric oversubscription
                                                               (Phase 03 design choice,
                                                                not a tuning problem)
```

> 💡 **A gap with a named structural cause is a finished result, not a failure.** "64-GPU scaling is 2.8 points below target because the fabric is 2:1 oversubscribed above 32 nodes, which was a documented cost decision in Phase 03" is a complete answer. **Chasing it with more tuning would be waste.** Say so explicitly, and record what it would cost to close it (Phase 56's capacity plan can then price it).

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | Every experiment has a written hypothesis with an expected magnitude | Read `experiments/` | All |
| **A2** | Every experiment measured 3× before and 3× after | Results | All |
| **A3** | **No change kept whose improvement is within 2× the spread** | Review decisions | None |
| **A4** | Reverted experiments recorded with their data | `experiments/` | Recorded |
| **A5** | Predictions checked: unexpected movements investigated | Decisions | Checked |
| **A6** | 🎯 **Every non-default setting appears in `tuning-record.md` with evidence** | `audit-settings.sh` | 100 % |
| **A7** | 🧪 **The audit detects an undocumented setting** | Inject one | Detects |
| **A8** | Audit runs in CI and monthly | Check | Scheduled |
| **A9** | BIOS profile applied and verified across the fleet | Spot-check 5 nodes | Applied |
| **A10** | PCIe negotiating at full speed on every node | B2 + Phase 23 H10 | Full |
| **A11** | **SMT decision made per pool with evidence** | Record | Evidence-backed |
| **A12** | 📊 Idle power delta from C-state changes measured | Phase 32 comparison | Measured |
| **A13** | NIC IRQ affinity NUMA-local on every node | Verify | Local |
| **A14** | Interrupt coalescing set per pool (throughput vs. latency) | Verify | Per pool |
| **A15** | NVMe block scheduler is `none` | Verify | `none` |
| **A16** | GPU persistence mode enabled | `nvidia-smi -q` | Enabled |
| **A17** | TF32 decision made, documented, and opt-out available | Docs + test | Documented |
| **A18** | Staged rollout used for every fleet-wide change | Rollout logs | Staged |
| **A19** | 🧪 **A regression during a wave halts and reverts automatically** | Simulate | Halts |
| **A20** | Reboots staggered per power circuit | Observe | Staggered |
| **A21** | Every node labeled with its tuning profile version | `kubectl get nodes -L` | Labeled |
| **A22** | 📊 **The performance budget is fully accounted** | `§8.1` table completed | Complete |
| **A23** | Every remaining gap has a named structural cause | Read | Named |
| **A24** | 📊 A new baseline accepted post-tuning (Phase 48's rules) | `baseline.json` | Accepted |

---

## ↩️ ROLLBACK

```bash
# Per-experiment: every change is a committed, revertible unit
git revert <experiment commit>
bash tools/tuning/apply-profile.sh --profile <previous-version> --staged

# Emergency: revert the whole fleet to the last known-good profile
bash tools/tuning/apply-profile.sh --profile <last-good> --all --reason "regression"
# ⚠️ BIOS changes require reboots — plan the window
```

> 💡 **Because each experiment is one change in one commit, rollback is precise.** This is the payoff for the one-change-at-a-time discipline: when something regresses three weeks later, you can bisect the tuning history the same way you bisect code.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Change helps on canary, not at scale | Interaction with load or the fabric | Expected — this is why waves exist |
| Improvement within noise | Spread too large, or the change does nothing | A3 — revert it |
| A benchmark that should not have moved, moved | An interaction | A5 — investigate; often the real finding |
| BIOS setting reverts after a power loss | CMOS battery, or the setting is not persisted | Check; some boards need a save-profile step |
| PCIe negotiating below max | Riser cable, slot sharing, or BIOS auto | Phase 01's lane budget; try a different slot |
| Jitter improved but B9 got worse | `isolcpus` took cores from data loading | The trade is real — set per pool |
| Power draw rose after C-state changes | Expected | A12 — check it against Phase 32's budget |
| Some nodes perform differently after tuning | Profile not applied uniformly | A21 — check the labels |
| Cannot apply BIOS changes remotely | No BMC | Phase 02's PiKVM + physical rotation; plan for it |
| Results improve but stability worsens | Aggressive setting (memory timing, clocks) | Revert. **Stability always beats a few percent.** |

---

## 🚫 DO NOT

- **Do not** change more than one thing per experiment.
- **Do not** keep a change whose benefit is within measurement noise.
- **Do not** apply a tuning list from the internet without measuring each item.
- **Do not** leave any non-default setting out of the tuning record.
- **Do not** roll a change fleet-wide without staged waves.
- **Do not** enable TF32 silently.
- **Do not** trade stability for a few percent.
- **Do not** keep tuning a gap whose cause is structural.
- **Do not** build the regression CI here. Phase 50.

---

## 📤 HANDOFF

`evidence/phase-49/handoff.md` must state:

1. **📊 The completed performance budget** (`§8.1`) with every percent accounted.
2. **🎯 The tuning record** — every non-default setting and its evidence.
3. **The experiments that were REVERTED** and why — as valuable as the ones kept.
4. **Per-pool differences** (SMT, coalescing, isolcpus) and their justification.
5. **📊 The new post-tuning baseline** accepted under Phase 48's rules.
6. **Total improvement** from the tuning phase, per benchmark.
7. **Remaining gaps with structural causes**, and what closing each would cost — feeds Phase 56.
8. **How BIOS changes are applied without a BMC**, and how long a fleet-wide BIOS change takes.

---

## ➡️ NEXT

**[PHASE-50 — Performance CI & Regression Gates](PHASE-50.md)** — lock in everything just gained, so it cannot silently erode.
