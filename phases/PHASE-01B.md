# PHASE 01B — Campus Fleet Survey & Harvest Inventory

| | |
|---|---|
| **Stage** | C — Campus Harvest Plane |
| **Estimated effort** | 5–6 hours (plus walking time; budget 30 min per lab) |
| **Depends on** | 01 |
| **Blocks** | 04B, 03B, 08B, 14B, 19B |
| **Risk** | 🟠 A wrong survey produces a fabric that promises capacity it does not have |
| **Blast radius** | Every downstream campus decision |
| **Architecture refs** | `CAMPUS-FABRIC.md#10-capacity-math-for-diu`, `ULTIMATE-PLAN.md#archetype-f--harvest`, R-25, R-26, R-27, R-28 |

---

## 🎯 MISSION

Produce the **machine-readable source of truth for every candidate DIU classroom and lab machine** — what it is, where it is, what it is attached to, when it is free, and whether it is safe to load — so that every later campus phase computes from surveyed reality rather than from assumption.

> 💡 **WHY this is a separate phase from 01.** Phase 01 inventories machines we own and control, and it can assume we may reboot them, open them, and benchmark them at will. None of that is true here. A harvest survey is a *field survey of someone else's equipment*, constrained to what can be read non-invasively, and it must capture things a datacenter inventory never does: the room's timetable, its ventilation, its uplink, and its owner's name.

> 🚫 **This phase does not harvest anything.** You are counting and measuring, not enrolling. Nothing is woken, netbooted, or reconfigured until 04B passes. See Rule C1.

---

## ✅ PREFLIGHT

```bash
# 1. Phase 01 complete — the core inventory schema and tooling exist
task validate:inventory
test -f inventory/schema/nodespec.schema.json && echo OK

# 2. You have written permission to *survey* (not yet to harvest) the candidate rooms
#    — a short email from the department head naming the rooms is sufficient at this stage.
#    Record it at docs/campus/survey-authorization.md

# 3. You can obtain, in some form:
#    - the room timetable (registrar export, printed schedule, or a photograph of the door)
#    - the name and contact of each room's owner / responsible technician
#    - read access to the switch the room is patched into, OR a willing network contact

# 4. A USB stick with a live Linux image for non-invasive probing, and permission to boot it
#    on ONE representative machine per lab configuration.
```

**If you cannot answer #2, this phase is BLOCKED.** Write `evidence/phase-01B/BLOCKED.md`. Surveying university equipment without written authorization is exactly the failure mode R-20 describes, only earlier.

---

## 📦 DELIVERABLES

```
docs/campus/
  survey-authorization.md           # the written permission to survey
  survey-method.md                  # how each field was measured, and its confidence
  fleet-report.md                   # THE deliverable — narrative summary + capacity math
  room-photos/                      # ventilation, patch panel, machine layout (no people)
inventory/campus/
  labs.yaml                         # one entry per room: owner, uplink, circuit, timetable ref
  machines.yaml                     # one entry per surveyed machine
  timetable.yaml                    # per-room weekly occupancy, exam/holiday blackouts
  uplinks.yaml                      # per-lab uplink speed, switch, port, measured headroom
inventory/schema/
  lab.schema.json
  harvestnode.schema.json
  timetable.schema.json
tools/campus/
  probe-harvest-node.sh             # non-invasive live-USB probe → one machine YAML
  survey-stats.sh                   # rolls machines.yaml up into the capacity table
  validate-campus.sh                # schema + cross-reference validation
evidence/phase-01B/
  preflight.md plan.md acceptance.md handoff.md deviations.md
  raw/                              # raw probe output, verbatim
```

---

## 📋 TASKS

### Task 1 — Define the lab record

A **lab is the harvest fabric's atomic unit** — it is simultaneously the consent domain, the uplink domain, the failure domain, and the scheduling locality domain (`CAMPUS-FABRIC.md §8.1`). Model it accordingly.

**`inventory/campus/labs.yaml`** — one entry per room:

```yaml
- id: cse-402                         # lowercase, stable, used as a K8s topology label value
  building: daffodil-tower
  floor: 4
  room: "402"
  owner:
    name: "<REPLACE-ME>"              # role/title is enough; avoid storing personal data
    role: "Lab in-charge, CSE"
    contact_ref: "docs/campus/agreements/cse-402.md"   # filled in by 04B
  machines: 32
  timetable_ref: cse-402              # key into timetable.yaml
  uplink_ref: cse-402                 # key into uplinks.yaml
  power:
    circuits_observed: 2              # from the room's DB / breaker panel label
    measured_room_draw_w: null        # optional clamp-meter reading, all machines idle
    ventilation: "ac-split-2x1.5ton"  # or "ceiling-fan-only", "none"
    ventilation_verdict: adequate     # adequate | marginal | inadequate  ← gates sustained load
  harvest_status: surveyed            # surveyed → agreed → enrolled → active | withdrawn
  notes: "Machines patched to two 24-port switches; second switch uplinks via the first."
```

> ⚠️ **`ventilation_verdict: inadequate` is a hard exclusion for sustained GPU load** (R-28). A room with 30 machines, no air conditioning, and a closed door will reach an unpleasant and hardware-shortening temperature within an hour. Record it honestly; Phase 19B reads this field and will refuse to place GPU work there.

### Task 2 — Probe one representative machine per configuration

Most labs contain 20–40 identical machines. **Probe one thoroughly, then verify the rest cheaply** (model string + RAM + GPU presence).

**`tools/campus/probe-harvest-node.sh`** boots from the live USB and must collect, without writing anything to the internal disk:

```bash
# Identity & chassis
dmidecode -s system-manufacturer -s system-product-name -s baseboard-product-name
# CPU
lscpu | grep -E 'Model name|^CPU\(s\)|Thread|Socket|NUMA'
# Memory — MEASURED, never nameplate
free -b | awk '/Mem:/{print $2}'
dmidecode -t memory | grep -E 'Size|Speed|Type:'
# GPU — the field that decides which pool this node joins
lspci -nn | grep -Ei 'vga|3d|display'
nvidia-smi --query-gpu=name,memory.total,compute_cap,driver_version --format=csv 2>/dev/null
# Disk — READ ONLY. Confirm what is there; never mount, never write.
lsblk -o NAME,SIZE,TYPE,FSTYPE,PARTLABEL     # expect an NTFS/Windows layout — leave it alone
# Network — the constraint that governs everything
ethtool <iface> | grep -E 'Speed|Duplex|Link detected'
ethtool <iface> | grep -i 'Wake-on'          # 'Wake-on: g' means WoL is usable
ip -o link show <iface> | awk '{print $2,$(NF-2)}'   # iface + MAC, needed for WoL
# Firmware/boot capability
ls /sys/firmware/efi >/dev/null 2>&1 && echo UEFI || echo BIOS
# Thermal baseline (idle)
sensors 2>/dev/null | grep -E 'Core|edge|temp1'
```

> 🚫 **DO NOT** mount, image, resize, defragment, or write to the internal disk. Not once, not "read-only just to check". The leave-no-trace promise (`CAMPUS-FABRIC.md §9.1`) starts here, and a single accidental write to a lab's Windows install would justifiably end the programme.

Emit one record per machine into **`inventory/campus/machines.yaml`**:

```yaml
- id: hv-cse402-07
  lab: cse-402
  mac: "<REPLACE-ME>"                 # required for WoL
  chassis: "HP ProDesk 600 G6"
  cpu: { model: "Core i5-10500", cores: 6, threads: 12, sockets: 1 }
  memory: { measuredBytes: 17179869184 }        # 16 GiB measured, not "16GB claimed"
  gpu:
    - model: "NVIDIA GeForce GTX 1650"
      vramMiB: 4096
      computeCapability: "7.5"
      class: consumer-discrete        # none | igpu | consumer-discrete
  disk: { internal: "512GB NVMe", layout: "windows-ntfs", policy: never-touch }
  network: { speedMbps: 1000, wolCapable: true, switchPort: null }   # port filled by 03B
  firmware: { mode: UEFI, secureBoot: true, pxeCapable: null }       # pxe verified in 08B
  probeConfidence: representative     # probed | representative | assumed
  harvestEligible: null               # decided in 14B, never guessed here
```

**`probeConfidence` is not optional.** A fleet where 400 records claim to be `probed` when 20 were actually probed is a fleet that will surprise you at 3am. `representative` means "one identical machine in this lab was probed"; `assumed` means "we read the asset sticker". Both are acceptable; lying about which is not.

### Task 3 — Capture the timetable

This is the single highest-value dataset in the campus plane — it is *known future*, and it is what makes the Availability Oracle (14B) possible rather than merely statistical.

**`inventory/campus/timetable.yaml`**:

```yaml
- lab: cse-402
  timezone: Asia/Dhaka
  source: "registrar export 2026-09-01"      # or "photographed door schedule 2026-09-03"
  confidence: authoritative                  # authoritative | observed | assumed
  weekly:                                    # occupied blocks; everything else is candidate window
    - { day: sun, from: "08:30", to: "17:00" }
    - { day: mon, from: "08:30", to: "17:00" }
    - { day: tue, from: "08:30", to: "13:00" }
    - { day: wed, from: "08:30", to: "17:00" }
    - { day: thu, from: "08:30", to: "15:30" }
  blackouts:                                 # hard exclusions, no harvesting at all
    - { from: "2026-12-10", to: "2026-12-24", reason: "final examinations" }
  reserved_buffer_minutes: 30                # drain must COMPLETE this long before a block starts
```

> 💡 **`reserved_buffer_minutes` is the promise-keeping margin.** A 30-minute buffer means a class starting at 08:30 sees machines that finished draining at 08:00 and have been sitting at the Windows login screen since. Do not tune this below 30 minutes to buy capacity; the capacity is not worth the risk (R-19).

### Task 4 — Measure the uplink, not the port

Every machine has a 1 Gb access port. The **shared lab uplink is the real budget** (`ULTIMATE-PLAN.md §4.9`).

**`inventory/campus/uplinks.yaml`**:

```yaml
- lab: cse-402
  accessSwitch: "sw-dt-4f-02"
  accessPortSpeedMbps: 1000
  uplinkSpeedMbps: 1000               # ← measure or confirm; do NOT assume 10G
  uplinkOversubscription: "32:1"      # 32 access ports behind one 1G uplink
  measuredBaselineMbps:               # teaching-hours utilization, from switch counters or a 24h capture
    classHoursP95: 180
    offHoursP95: 12
  nexusCeilingMbps:                   # computed, enforced in 03B
    classHours: 400                   # 40 % of uplink
    offHours: 700                     # 70 % of uplink
  source: "switch SNMP counters, 7-day sample"
```

If you cannot get switch counters, a `iperf3` test between a lab machine and a machine outside the lab, run **off-hours only**, is acceptable — record it as such. **Never run a bandwidth test during class hours.** You would be creating R-19 while surveying for it.

### Task 5 — Roll up the capacity math

**`tools/campus/survey-stats.sh`** must emit the table that `docs/campus/fleet-report.md` is built around, mirroring `CAMPUS-FABRIC.md §10`:

```
Per lab and total:
  machines_total
  machines_gpu_discrete / machines_igpu / machines_cpu_only
  cpu_cores_total, memory_bytes_total (MEASURED)
  weekly_free_hours   = 168 − Σ(occupied blocks) − amortized blackouts
  effective_hours     = weekly_free_hours × 0.94        # wake/boot/drain overhead
  gpu_hours_per_week  = machines_gpu_discrete × effective_hours
  core_hours_per_week = cpu_cores_total × effective_hours
Excluded from all totals:
  labs with ventilation_verdict: inadequate  (GPU work only)
  machines with wolCapable: false            (until 08B proves an alternative)
```

Report the number **and the assumption behind it**. "9,270 GPU-h/week" alone is a number that will be quoted back at you in a meeting; "9,270 GPU-h/week, assuming 12 labs sign, 94 % wake success, and no exam-week harvesting" is a number you can defend.

### Task 6 — Validation tooling

**`tools/campus/validate-campus.sh`** must fail the build on any of:

- a machine referencing a lab that does not exist in `labs.yaml`
- a lab with no `timetable_ref` or no `uplink_ref`
- a machine with `wolCapable: true` but no MAC address
- a lab whose `machines:` count disagrees with the machine records by more than 10 %
- any `nexusCeilingMbps` exceeding 40 %/70 % of its `uplinkSpeedMbps`
- any record with `probeConfidence: probed` lacking a corresponding file in `evidence/phase-01B/raw/`

Wire it into `Taskfile.yaml` as `task validate:campus` and into CI alongside `task validate:inventory`.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Command |
|---|---|---|
| 1 | Every candidate lab has a record with an owner, timetable ref, uplink ref, and ventilation verdict | `task validate:campus` |
| 2 | ≥ 1 machine per distinct lab configuration has `probeConfidence: probed` with raw evidence committed | `yq '.[] \| select(.probeConfidence=="probed") \| .id' inventory/campus/machines.yaml \| wc -l` |
| 3 | Every machine record has a measured (not nameplate) memory value and a MAC | `tools/campus/validate-campus.sh` |
| 4 | Uplink ceilings computed for every lab and none exceeds policy | `tools/campus/validate-campus.sh` |
| 5 | `docs/campus/fleet-report.md` states total GPU-h/week and core-h/week **with its assumptions listed** | manual review — the assumptions clause is mandatory |
| 6 | Zero writes to any lab machine's internal disk | `evidence/phase-01B/acceptance.md` attests the method; `survey-method.md` documents read-only probing |
| 7 | No personal data beyond role and institutional contact reference is stored in Git | `grep -rniE 'phone|nid|personal|@gmail' inventory/campus/ \| wc -l` → `0` |

---

## ↩️ ROLLBACK

Nothing was changed on any machine, so rollback is `git revert`. If a lab owner objects to being surveyed at all, delete their records and note it in `deviations.md` — do not keep data you were asked not to keep.

---

## 🧯 TROUBLESHOOTING

| Symptom | Cause | Action |
|---|---|---|
| `ethtool` shows `Wake-on: d` | WoL disabled in the OS driver or BIOS | Record `wolCapable: false` for now; 08B's BIOS pass may fix it. Do not change settings during the survey. |
| Machine will not boot the live USB | Secure Boot, or USB boot disabled | Record `probeConfidence: assumed` with the reason. Do not disable Secure Boot on someone else's machine during a survey. |
| No registrar timetable available | Common | Photograph the door schedule; set `confidence: observed`. 14B's historical model will correct it. |
| Switch access refused | Network team not engaged yet | Defer `uplinks.yaml` measurement to 03B; mark fields `null` rather than guessing. Do not run bandwidth tests to work around it. |
| Lab has mixed hardware generations | Very common | One `probed` record per distinct configuration, not per lab. Heterogeneity is expected (R-27). |
| A room is in use when you arrive | — | Leave. Come back. This is the whole ethic of the phase in miniature. |

---

## 🚫 DO NOT

- **Do not wake, netboot, enrol, or reconfigure any machine.** That is 08B, and only after 04B.
- **Do not mount or write to any internal disk.**
- Do not change BIOS settings "while you are there".
- Do not run bandwidth or load tests during class hours.
- Do not store personal data (names beyond role, phone numbers, student identifiers) in Git.
- Do not extrapolate a lab you did not visit into `labs.yaml`. An absent lab is better than a fictional one.
- Do not compute capacity totals that include labs which have not agreed to participate — mark them `harvest_status: surveyed` and exclude them from the headline number.

---

## 🤝 HANDOFF — write `evidence/phase-01B/handoff.md`

Must state:

- The list of labs surveyed, each with machine count, GPU count, ventilation verdict, and timetable confidence.
- **Which labs are the best first candidates** for 04B, and why (large, well-ventilated, cooperative owner, generous free window, GPU-equipped — in that order of usefulness).
- Every lab excluded and the reason.
- The headline capacity numbers and their assumptions.
- WoL-capable percentage — 08B's yield target depends on it.
- Uplink fields left `null` for 03B to complete.
- Any owner who expressed hesitancy. 04B needs to know before walking in.
