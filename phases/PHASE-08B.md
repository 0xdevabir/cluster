# PHASE 08B — Netboot Harvest Agent & WoL Fleet Control

| | |
|---|---|
| **Stage** | C — Campus Harvest Plane |
| **Estimated effort** | 6–7 hours (plus one BIOS pass per lab, ~2 min/machine) |
| **Depends on** | 08, 03B, **04B (agreement signed for every lab touched)** |
| **Blocks** | 14B, 19B, 25B |
| **Risk** | 🔴 R-24 (leaving a trace), R-26 (WoL unreliability), R-23 (physical exposure) |
| **Blast radius** | Every lab machine touched — including its Windows install |
| **Architecture refs** | `CAMPUS-FABRIC.md#6-harvest-modes--the-node-lifecycle`, `#83-data-classification`, `ULTIMATE-PLAN.md#archetype-f--harvest`, Rule C1, Law XI |

---

## 🎯 MISSION

Make a borrowed classroom PC boot into an **ephemeral, diskless NEXUS node on command and return to Windows on shutdown, having changed nothing** — and build the wake/quarantine controller that drives several hundred of them without a human walking to a power button.

> ⚠️ **DANGER — this is the phase that touches other people's computers.** Every other campus phase can be reverted with `git revert`. A machine whose Windows install you damaged cannot. Read `CAMPUS-FABRIC.md §6.1` and §9.1 before running a single command, and verify the disk is untouched after the first boot before doing the second.

> 💡 **WHY diskless netboot rather than an installed agent.** An installed agent means: software to update on 400 machines we do not own, a persistent footprint we promised not to leave, a permanent attack surface on a physically accessible machine, and a trust conversation with every lab owner. Netboot means: the OS lives in RAM, every power cycle is a guaranteed wipe, the only persistent change is one BIOS boot-order entry, and rollback is a 30-second setting change any technician can perform. The security and consent properties come for free from the boot design — which is the whole reason this design was chosen.

---

## ✅ PREFLIGHT

```bash
# 1. Consent — for EVERY lab this phase will touch. Non-negotiable (Rule C1).
tools/campus/check-consent.sh --labs <lab-ids>

# 2. Network integration complete for those labs' segments
yq -r '.[] | select(.labs[] == "<lab-id>") | [.id, .vlan, .wol.method] | @tsv' inventory/campus/segments.yaml
tools/campus/check-wol-reach.sh --segment <segment-id>

# 3. Phase 08 complete — Tinkerbell/iPXE provisioning pipeline exists for the Core Plane;
#    this phase adds a diskless profile to it rather than building a second pipeline
test -d bootstrap/tinkerbell

# 4. Physical access scheduled with the lab owner, OUTSIDE class hours,
#    with the owner or technician present or informed.

# 5. A rollback card printed for each lab: how to restore the boot order in 30 seconds.
```

**If any lab in scope lacks a signed agreement, this phase is BLOCKED for that lab.** Proceed with the labs that are cleared; write the rest into `BLOCKED.md`.

---

## 📦 DELIVERABLES

```
bootstrap/campus/
  ipxe-harvest.ipxe                 # chainload script: identify → diskless Talos → join
  talos-harvest-patch.yaml          # machine config: RAM-only, no disk install, ephemeral
  bios-checklist.md                 # the one-time per-machine settings pass
  rollback-card.md                  # printed, one per lab: restoring the boot order
clusters/nexus-prod/infra/campus/
  harvest-controller/               # wake / drain / quarantine controller manifests
    deployment.yaml rbac.yaml config.yaml
tools/campus/
  wake-lab.sh                       # wake a room, bounded, with per-machine result
  verify-no-trace.sh                # proves the internal disk is byte-identical after a cycle
  quarantine-report.sh              # machines that failed to wake or join, and why
docs/campus/
  harvest-agent-design.md           # the boot path, the state machine, the trust model
  no-trace-proof.md                 # THE artifact that backs promise 2
evidence/phase-08B/
  preflight.md plan.md acceptance.md handoff.md deviations.md
  disk-hashes/                      # before/after hashes per representative machine
```

---

## 📋 TASKS

### Task 1 — The one-time BIOS pass

Per machine, ~2 minutes, done once, with the lab owner's knowledge. **Record every setting you change, per machine, so it can be reverted exactly.**

| Setting | Value | Why | Reversible? |
|---|---|---|---|
| Wake-on-LAN / Power On by PCIe | **Enabled** | The entire Mode A wake path (R-26) | ✅ |
| ErP / EuP / Deep Sleep | **Disabled** | ErP cuts NIC standby power; WoL silently stops working | ✅ |
| Network (PXE) boot | **Enabled, first in order** | iPXE decides everything after; falls through to Windows when we do not answer | ✅ **This is the one change that matters** |
| Secure Boot | Leave as found | Use a signed Talos/iPXE image rather than disabling it | — |
| Restore on AC power loss | **Last state** or Off | Avoids a room powering itself on after an outage | ✅ |
| Boot order after PXE | **Unchanged** (Windows next) | The fallback path *is* the promise | ✅ |

> 💡 **The fall-through is the whole design.** PXE-first does not mean "always netboot" — it means "ask us first." When the harvest window is closed, our DHCP/PXE service simply does not answer for that MAC, the machine falls through to Windows in ~2 seconds, and a student sees a normal boot. No orchestration, no agent, no timing race. The window logic lives in one place (14B) and the machine needs to know nothing.

**`bootstrap/campus/rollback-card.md`** — printed and left with each lab technician:

```
To remove this lab from the research computing service immediately:
  1. Enter BIOS/UEFI setup (usually F10 / F2 / DEL at power-on)
  2. Boot Order → move "Network / PXE" below "Windows Boot Manager"
  3. Save and exit.
That is the only change that was made. Nothing else needs undoing.
```

### Task 2 — The diskless Talos profile

**`bootstrap/campus/talos-harvest-patch.yaml`** — the machine config that makes "no trace" structural rather than aspirational:

```yaml
machine:
  install:
    disk: ""                      # ← NO INSTALL. Talos runs entirely from RAM.
  # Every writable path is tmpfs. There is no persistent volume of any kind.
  kubelet:
    extraArgs:
      node-labels: "nexus.io/plane=harvest,nexus.io/lab=${LAB_ID},nexus.io/building=${BUILDING}"
      register-with-taints: "nexus.io/harvest=true:NoSchedule"
      node-status-update-frequency: "30s"   # reduce etcd churn from a churning fleet
  sysctls:
    vm.swappiness: "0"            # never swap — there is nowhere to swap to
  features:
    hostDNS: { enabled: true }
```

**Hard rules encoded here and verified in Task 5:**

- No `install.disk` — nothing is written to any block device.
- The internal disk is **not mounted, not enumerated as a mount candidate, and not passed to any container**.
- Node identity is short-lived and scoped: a harvest node's credentials permit joining and running workloads, nothing else. It cannot read secrets, cannot reach etcd, cannot enumerate other namespaces (R-23).
- Every writable path is tmpfs, so **power loss is a guaranteed, complete wipe**.

RAM budget: Talos in RAM (~500 MB) + image layer cache + workload. On a 8 GB machine this leaves ~6 GB usable — record the per-machine usable figure, because 14B's admission control needs measured RAM, not nameplate (`ULTIMATE-PLAN.md` Archetype F).

### Task 3 — iPXE chainload logic

**`bootstrap/campus/ipxe-harvest.ipxe`** decides, per boot, in this order:

```
1. Is this MAC in an enrolled, agreed lab?           → no  : exit 1 (fall through to Windows)
2. Is the lab's harvest window open right now?       → no  : exit 1 (fall through to Windows)
3. Is the lab withdrawn or in a blackout?            → yes : exit 1 (fall through to Windows)
4. Is this MAC in excludedMachines?                  → yes : exit 1 (fall through to Windows)
5. Otherwise                                          → chainload the diskless Talos image
```

Steps 1–4 read `policies/campus/harvest-eligibility.yaml` (04B) through the controller's API. **The default answer is always "fall through to Windows."** Any error, any timeout, any unreachable controller — the machine boots Windows. Fail-safe means fail-to-the-human.

### Task 4 — The harvest controller

A controller in the Core Plane driving the node state machine from `CAMPUS-FABRIC.md §6.3`:

```
OFFLINE ──WoL──► BOOTING ──join──► READY ──admit──► RUNNING
   ▲               │(fail)          │(window closing)   │(human/class)
   │           QUARANTINE        CORDONED ──────────► DRAINING
   └──────────────────── shutdown ◄─────────────────────┘
```

Responsibilities:

| Function | Behavior | Budget |
|---|---|---|
| **Wake a lab** | Emit magic packets in batches (10 at a time, 2 s apart) to avoid an inrush current spike and a DHCP thundering herd | Room ready in < 5 min (G17) |
| **Track join** | A machine that does not join within 180 s of wake → `QUARANTINE`, with the reason recorded | — |
| **Quarantine, do not chase** | Report it. Never retry more than twice. A machine that will not wake is a data point for the report, not an on-call task (R-26) | — |
| **Cordon at window close** | `reserved_buffer_minutes` before the timetable block, stop admitting | From 14B |
| **Drain and shut down** | Cordon → evict (31B's path) → `talosctl shutdown` → machine is off before the buffer expires | Drain complete before the bell, 100 % |
| **Emit metrics** | Every transition, with timestamps — this is 33B's raw data | — |

> ⚠️ **Inrush current.** Thirty PCs powering on simultaneously draws a large transient. Batch the wake packets. A tripped breaker in a teaching lab is both an R-19 event and an embarrassing one.

### Task 5 — Prove no trace

This is the acceptance artifact that backs promise 2, and it must be **measured, not asserted**.

**`tools/campus/verify-no-trace.sh`**, on a representative machine per lab configuration:

```bash
# BEFORE — from a live USB, read-only, hash every partition on the internal disk
for part in $(lsblk -lno NAME,TYPE | awk '$2=="part"{print $1}'); do
  sha256sum /dev/$part            # or hash the first+last 1 GiB for large disks, documented either way
done > evidence/phase-08B/disk-hashes/<machine>-before.txt

# Then: full harvest cycle — wake → join → run a real workload → drain → shutdown → boot Windows once → shut down

# AFTER — identical procedure
... > evidence/phase-08B/disk-hashes/<machine>-after.txt

diff before.txt after.txt          # must be empty for every partition NEXUS could reach
```

> 💡 Windows itself writes to its own disk when it boots, so hash **before the Windows boot and after the harvest cycle** to isolate our effect. Document the method precisely in `no-trace-proof.md` — a proof whose methodology is vague proves nothing to the person who matters.

### Task 6 — Fleet-scale wake operation

**`tools/campus/wake-lab.sh`** — the operator-facing command, idempotent, bounded, honest:

```bash
tools/campus/wake-lab.sh --lab cse-402 --timeout 300
# → 32 machines targeted
#   28 joined       (avg 94 s)
#    2 quarantined  (no WoL response after 2 attempts)
#    1 excluded     (instructor console)
#    1 in use       (already powered on, human session detected — SKIPPED, never interrupted)
```

**The "already on" case is the one to get right.** A machine that is powered on when the window opens may have a human at it. **Never wake, never reboot, never claim a machine that is already running.** Skip it, log it, move on. Mode A only ever claims machines it found powered off.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Command |
|---|---|---|
| 1 | A machine wakes from off, netboots diskless, and joins as `Ready` with harvest labels and taint | `kubectl get node <n> -o jsonpath='{.spec.taints}'` |
| 2 | **Disk hashes are byte-identical before and after a full harvest cycle** on ≥ 1 machine per lab configuration | `diff` output committed to `evidence/phase-08B/disk-hashes/` |
| 3 | With the harvest window closed, the machine falls through to Windows in < 5 s and never netboots | timed, repeated 3× |
| 4 | With the controller unreachable, the machine falls through to Windows (fail-safe) | stop the controller, power on, observe |
| 5 | A machine already powered on with a session is never woken, rebooted, or claimed | log evidence from a deliberate test |
| 6 | Wake of a 30-machine room completes in < 5 min with batching, no breaker trip | `wake-lab.sh` output + timing |
| 7 | Machines failing to wake are quarantined and reported, not retried indefinitely | `quarantine-report.sh` |
| 8 | Node credentials cannot read secrets, reach etcd, or list other namespaces | explicit negative RBAC tests |
| 9 | Rollback card restores the machine to its original boot behavior in ≤ 30 s | timed, with the lab technician performing it |
| 10 | Talos runs entirely in RAM; no block device is mounted | `talosctl -n <node> mounts` shows no internal disk |

---

## ↩️ ROLLBACK

**Per machine:** BIOS → move Network/PXE below Windows Boot Manager. Done. (Verify with acceptance #9 that this is genuinely 30 seconds.)
**Per lab:** `harvest_status: withdrawn` → controller cordons and drains, then stops answering PXE for those MACs. The next boot is Windows.
**Fleet-wide emergency:** stop the harvest controller. Every machine falls through to Windows on its next boot, and running nodes drain. **The fail-safe direction is always toward the human.**

---

## 🧯 TROUBLESHOOTING

| Symptom | Cause | Action |
|---|---|---|
| WoL packet sent, machine does not wake | ErP/deep-sleep enabled, or the NIC lost standby power after a full power cut | Recheck BIOS; note that some machines lose WoL after being unplugged. Quarantine and report; do not chase. |
| Machine netboots during class hours | PXE range leaked into the teaching range, or window logic wrong | **Stop the fabric immediately.** This is an R-19 event. Fix the DHCP scope (03B) before resuming, and tell the lab owner what happened before they ask. |
| Talos boots but does not join | DHCP/DNS/NTP path, or clock skew | Check `talosctl dmesg`; harvest nodes are especially prone to clock skew after long power-off. Ensure NTP is in the allow-list. |
| Out-of-memory on 8 GB machines | Talos + image cache + workload exceeds RAM | Record the real usable figure; 14B must admit against measured RAM. Consider excluding 4 GB machines entirely. |
| Disk hash differs after a cycle | **Something wrote to the disk** | Stop harvesting that lab. Find it before doing anything else — this breaks promise 2, and the promise is the programme. |
| Secure Boot rejects the image | Unsigned iPXE/Talos | Use a signed image. Do **not** disable Secure Boot on a machine you do not own. |
| Room breaker trips on wake | Inrush from simultaneous power-on | Reduce batch size and increase spacing. Record the room's safe batch size in `labs.yaml`. |

---

## 🚫 DO NOT

- **Do not install anything on the internal disk. Do not mount it. Do not "just check" it.**
- Do not disable Secure Boot on a lab machine.
- Do not wake, reboot, or claim a machine that is already powered on.
- Do not touch a lab without a signed agreement (Rule C1).
- Do not retry a non-waking machine more than twice — quarantine and report.
- Do not build the Availability Oracle here; this phase takes window state as an input from 14B and defaults to "closed" until it exists.
- Do not implement Mode B (in-Windows or idle-during-class harvesting) in this phase at all.
- Do not skip the disk-hash proof because the design "obviously" cannot write to disk.

---

## 🤝 HANDOFF — write `evidence/phase-08B/handoff.md`

Must state:

- Per lab: machines with the BIOS pass complete, wake success rate, average wake-to-`Ready` time, quarantined machines and why.
- **Measured usable RAM per machine configuration** after the Talos RAM footprint — 14B admits against this.
- The disk-hash proof result and the exact method used.
- Any machine or lab where WoL is unavailable, so 14B does not promise availability it cannot deliver.
- The safe wake batch size per room, if a breaker limit was found.
- Where the rollback cards were left, and who has them.
