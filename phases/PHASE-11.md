# PHASE 11 — Bootstrap Observability & Gate G2

| | |
|---|---|
| **Stage** | 1 — Bootstrap Infrastructure (**exit gate**) |
| **Estimated effort** | 3 hours |
| **Depends on** | 09, 10 |
| **Blocks** | All of Stage 2 |
| **Risk** | 🟢 Low — observation and measurement; the destructive work already happened in Phase 08 |
| **Blast radius** | Provisioning pipeline only |
| **Architecture refs** | `ARCHITECTURE.md#l31-zero-touch-provisioning-flow`, `ULTIMATE-PLAN.md#13-success-criteria` (G2) |

---

## 🎯 MISSION

Instrument the provisioning pipeline, then **prove gate G2**: a node goes from powered-off bare metal to a fully configured, healthy Talos machine in **under 15 minutes with zero human touch**, repeatably.

> 💡 **WHY measure provisioning before building Kubernetes:** provisioning time is the single number that determines whether 100 nodes is operable. At 15 minutes and parallelizable, a full fleet rebuild is an afternoon. At 90 minutes and serial, it is a week — and you will avoid reimaging nodes, which means you will start "fixing" them by hand, which violates Law IV and puts you back in snowflake territory. **The clock you start here defends the architecture.**

---

## ✅ PREFLIGHT

```bash
bash tools/seed-preflight.sh
bash tools/net-services-check.sh
bash tools/secrets/seal-check.sh

# Phase 08 provisioning works
test -s evidence/phase-08/approvals.log

# Phase 09 configs render and validate
bash tools/talos/render-configs.sh

# At least 3 nodes available to test with (one is not a sample)
ls inventory/nodes/*.yaml | wc -l
```

---

## 📦 DELIVERABLES

```
bootstrap/observability/
  docker-compose.yaml            # Prometheus + Grafana + Loki on the seed node
  prometheus/prometheus.yml
  prometheus/rules/provisioning.yml
  grafana/dashboards/provisioning.json
  grafana/dashboards/fleet-bootstrap.json
  loki/config.yaml
  promtail/config.yaml
tools/provisioning/
  timed-provision.sh             # instrumented end-to-end provisioning
  parallel-provision.sh          # batch with a concurrency limit
  provisioning-report.py         # timing analysis across runs
docs/operations/
  provisioning-slo.md            # the timing budget and what breaks it
evidence/phase-11/
  g2-zero-touch.md               # ⚠️ THE GATE EVIDENCE
  timings/*.json
  acceptance.md handoff.md deviations.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Image |
|---|---|---|
| Prometheus | `3.0.1` | `quay.io/prometheus/prometheus:v3.0.1` |
| Grafana | `11.4.0` | `docker.io/grafana/grafana:11.4.0` |
| Loki | `3.3.0` | `docker.io/grafana/loki:3.3.0` |
| Promtail | `3.3.0` | `docker.io/grafana/promtail:3.3.0` |
| node_exporter | `1.8.2` | `quay.io/prometheus/node-exporter:v1.8.2` |
| blackbox_exporter | `0.25.0` | `quay.io/prometheus/blackbox-exporter:v0.25.0` |
| snmp_exporter | `0.26.0` | `quay.io/prometheus/snmp-exporter:v0.26.0` |

> 💡 **This stack is temporary and deliberately minimal.** Phase 45 builds the real observability platform *inside* the cluster. This one exists so that provisioning is not a black box, and so that you have telemetry during the window when there is no cluster to run monitoring on. Keep it after Phase 45 as the **out-of-band monitor** — the thing that still works when the cluster is down (Law IX).

---

## 📋 TASKS

### Task 1 — The bootstrap monitoring stack

**`bootstrap/observability/prometheus/prometheus.yml`**

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels: { cluster: nexus-prod, source: bootstrap }

rule_files: [ /etc/prometheus/rules/*.yml ]

scrape_configs:
  - job_name: seed-node
    static_configs: [ { targets: ['localhost:9100'] } ]

  # Tinkerbell + bootstrap services
  - job_name: tinkerbell
    static_configs: [ { targets: ['localhost:9090','localhost:42112'] } ]

  # CoreDNS (Phase 07)
  - job_name: coredns
    static_configs: [ { targets: ['localhost:9153'] } ]

  # Node liveness during provisioning — the key signal
  - job_name: node-liveness
    metrics_path: /probe
    params: { module: [icmp] }
    file_sd_configs: [ { files: ['/etc/prometheus/targets/nodes.json'], refresh_interval: 30s } ]
    relabel_configs:
      - { source_labels: [__address__], target_label: __param_target }
      - { source_labels: [__param_target], target_label: instance }
      - { target_label: __address__, replacement: blackbox:9115 }

  # Talos API reachability — proves a node reached maintenance/running mode
  - job_name: talos-api
    metrics_path: /probe
    params: { module: [tcp_connect] }
    file_sd_configs: [ { files: ['/etc/prometheus/targets/talos.json'] } ]
    relabel_configs:
      - { source_labels: [__address__], target_label: __param_target }
      - { target_label: __address__, replacement: blackbox:9115 }

  # PDU power draw — the ground truth for "is this machine actually on?"
  - job_name: pdu
    static_configs: [ { targets: ['10.100.1.11','10.100.1.12'] } ]
    metrics_path: /snmp
    params: { module: [pdu_outlets], auth: [pdu_v3] }
    relabel_configs:
      - { source_labels: [__address__], target_label: __param_target }
      - { source_labels: [__param_target], target_label: instance }
      - { target_label: __address__, replacement: snmp-exporter:9116 }

  # Switch port state — catches cabling and link-negotiation problems early
  - job_name: switches
    static_configs: [ { targets: ['10.100.1.1','10.100.1.2'] } ]
    metrics_path: /snmp
    params: { module: [if_mib] }
    relabel_configs:
      - { source_labels: [__address__], target_label: __param_target }
      - { target_label: __address__, replacement: snmp-exporter:9116 }
```

**`tools/gen-prometheus-targets.sh`** generates `targets/*.json` from `inventory/` — same generation principle as DNS and DHCP (Law II).

---

### Task 2 — Provisioning alert rules

**`prometheus/rules/provisioning.yml`**

```yaml
groups:
  - name: provisioning
    rules:
      # ── Derived signals ──
      - record: node:power_on:bool
        expr: pdu_outlet_current_amps > 0.1
      - record: node:network_up:bool
        expr: probe_success{job="node-liveness"} == 1
      - record: node:talos_up:bool
        expr: probe_success{job="talos-api"} == 1

      # ── Alerts ──
      - alert: NodePoweredButNoNetwork
        expr: node:power_on:bool == 1 and node:network_up:bool == 0
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "{{ $labels.instance }} is drawing power but not on the network"
          description: "PXE failure, cabling, NIC, or BIOS boot order. Check PiKVM."

      - alert: ProvisioningExceededSLO
        expr: time() - node_provisioning_start_timestamp > 900
        labels: { severity: warning }
        annotations: { summary: "{{ $labels.node }} exceeded the 15-minute G2 budget" }

      - alert: NodeDrawingZeroCurrent
        expr: node:power_on:bool == 0 and node_expected_powered == 1
        for: 2m
        labels: { severity: critical }
        annotations:
          summary: "{{ $labels.instance }} draws no current — probable PSU or hardware failure"

      - alert: SwitchPortDown
        expr: ifOperStatus{ifAlias=~"nx-.*"} != 1
        for: 5m
        labels: { severity: warning }

      - alert: SwitchPortSpeedDegraded
        expr: ifHighSpeed{ifAlias=~"nx-.*"} < 25000
        labels: { severity: warning }
        annotations:
          summary: "{{ $labels.ifAlias }} negotiated {{ $value }} Mbps — below the 25 GbE floor (R-01)"

      - alert: PduCircuitNearLimit
        expr: pdu_circuit_current_amps / pdu_circuit_rating_amps > 0.80
        for: 5m
        labels: { severity: critical }
        annotations: { summary: "Circuit {{ $labels.circuit }} above 80 % — Phase 02 derate limit" }

      - alert: BootstrapServiceDown
        expr: up{job=~"tinkerbell|coredns"} == 0
        for: 2m
        labels: { severity: critical }
```

> 💡 **`SwitchPortSpeedDegraded` is the R-01 tripwire.** A node whose 100 GbE NIC negotiated at 10 GbE (bad cable, wrong transceiver, dirty fiber) looks fine to Kubernetes and destroys distributed-training performance. Catch it at link-up, not in Phase 52.

---

### Task 3 — The provisioning dashboard

**`grafana/dashboards/provisioning.json`** — panels that answer the questions you will actually ask:

| Panel | Query | Answers |
|---|---|---|
| Fleet state (stat grid) | count by (state) of nodes: off / powered / networked / talos-up / configured | "Where is everything right now?" |
| Provisioning timeline | Per-node Gantt of the phases below | "Which step is slow?" |
| Time-to-Ready histogram | `histogram_quantile(0.95, node_provisioning_duration_seconds_bucket)` | "Are we inside the G2 budget?" |
| Per-node current draw | `pdu_outlet_current_amps` | "Is it actually on?" |
| Rack power | `sum by (rack) (pdu_outlet_watts)` vs. the Phase 02 budget | "Are we near a breaker?" |
| Switch port speeds | `ifHighSpeed` per port | "Did every link negotiate correctly?" |
| DHCP lease rate | dnsmasq log-derived counter | "Is the DHCP path healthy?" |
| Failed provisioning attempts | Workflow failure counter by reason | "What is going wrong, and how often?" |
| Bootstrap service health | `up{job=~"tinkerbell\|coredns"}` | "Is the pipeline itself healthy?" |

---

### Task 4 — The timed provisioning harness

**`tools/provisioning/timed-provision.sh`** — records a timestamp at each phase boundary so the 15-minute budget can be attributed.

```bash
#!/usr/bin/env bash
# Instrumented provisioning. Emits a JSON timing record per node.
source "$(dirname "$0")/../lib/common.sh"
NODE="${1:?node}"
OUT="$ROOT/evidence/phase-11/timings/${NODE}-$(date +%s).json"
mkdir -p "$(dirname "$OUT")"

MGMT_IP=$(yq -r '.spec.mgmtIp' "$ROOT/inventory/nodes/$NODE.yaml")
declare -A T
mark() { T[$1]=$(date +%s); log "[$1] t+$(( ${T[$1]} - ${T[t0]} ))s"; }

T[t0]=$(date +%s)

# ── Phase A: power on ──
bash "$ROOT/tools/power/power-ctl.sh" on "$NODE"
mark power_on_issued
until [[ "$(bash "$ROOT/tools/power/power-ctl.sh" status "$NODE" | grep -oP 'current=\K[0-9.]+')" > 0.1 ]]; do sleep 2; done
mark current_drawn

# ── Phase B: DHCP / PXE ──
timeout 300 bash -c "until grep -q 'DHCPACK.*$NODE' /var/log/dnsmasq.log; do sleep 2; done"
mark dhcp_ack
timeout 300 bash -c "until grep -q 'sent .*ipxe.efi' /var/log/dnsmasq.log; do sleep 2; done"
mark ipxe_sent

# ── Phase C: HookOS + image write ──
timeout 1800 bash -c "until kubectl -n tink-system get workflow $NODE -o jsonpath='{.status.state}' 2>/dev/null | grep -q STATE_SUCCESS; do sleep 5; done"
mark workflow_complete

# ── Phase D: Talos maintenance mode ──
timeout 300 bash -c "until talosctl --nodes $MGMT_IP --insecure version >/dev/null 2>&1; do sleep 2; done"
mark talos_maintenance

# ── Phase E: config applied, node running ──
bash "$ROOT/tools/talos/apply-config.sh" "$NODE"
timeout 600 bash -c "until talosctl --nodes $MGMT_IP get machinestatus -o json 2>/dev/null | jq -e '.spec.stage==\"running\"' >/dev/null; do sleep 5; done"
mark talos_running

# ── Emit ──
jq -n --arg node "$NODE" \
  --argjson t0 "${T[t0]}" --argjson pw "${T[current_drawn]}" \
  --argjson dh "${T[dhcp_ack]}" --argjson px "${T[ipxe_sent]}" \
  --argjson wf "${T[workflow_complete]}" --argjson tm "${T[talos_maintenance]}" \
  --argjson tr "${T[talos_running]}" '{
    node: $node,
    phases: {
      power_to_current:      ($pw - $t0),
      current_to_dhcp:       ($dh - $pw),
      dhcp_to_ipxe:          ($px - $dh),
      ipxe_to_workflow_done: ($wf - $px),
      workflow_to_maint:     ($tm - $wf),
      maint_to_running:      ($tr - $tm)
    },
    total_seconds: ($tr - $t0),
    within_g2_budget: (($tr - $t0) < 900)
  }' | tee "$OUT"
```

**Expected budget** — record actuals against it in `docs/operations/provisioning-slo.md`:

| Phase | Budget | Dominated by |
|---|---|---|
| Power on → current draw | 15 s | WoL / PDU relay |
| Current → DHCP ACK | 45 s | POST time (memory training dominates on large-RAM boards) |
| DHCP → iPXE sent | 10 s | TFTP |
| iPXE → workflow complete | **8 min** | **HookOS boot + image write — the dominant term** |
| Workflow → Talos maintenance | 45 s | Reboot + Talos boot (~12 s) |
| Maintenance → running | 90 s | Config apply + reboot |
| **Total** | **~11 min** | Budget 15 min, ~27 % headroom |

---

### Task 5 — Parallel provisioning

At 100 nodes you provision in batches. Find the limit before you need it.

**`tools/provisioning/parallel-provision.sh`**
```bash
#!/usr/bin/env bash
# parallel-provision.sh <concurrency> <node...>
CONC="${1:?concurrency}"; shift
printf '%s\n' "$@" | xargs -P "$CONC" -I{} bash tools/provisioning/timed-provision.sh {}
```

**Test the concurrency ladder:** 1 → 2 → 4 → 8, recording total wall time and per-node time.

| Bottleneck | Symptom at high concurrency | Mitigation |
|---|---|---|
| Image server bandwidth | Per-node time grows linearly with concurrency | Serve images over the cluster VLAN; add a second file server; use a CDN-style local mirror |
| Management link (1 GbE) | Saturates at ~2 concurrent image writes | **The most likely limit.** 8 GB image ÷ 1 Gb/s ≈ 65 s minimum per node, serialized. |
| dnsmasq DHCP throughput | Lease failures, retries | Rarely a limit below 50 concurrent |
| Tinkerbell controller | Workflows stuck in PENDING | Raise controller resources |
| **Rack inrush current** | ⚠️ **Breaker trip** | **Stagger power-on by 5 s per node.** This is a real risk (Phase 02). |

> ⚠️ **Never power on a full rack simultaneously.** PSU inrush current can be 3–5× steady-state for tens of milliseconds. Twelve nodes starting together can trip a breaker that comfortably handles them running. Enforce a stagger in `parallel-provision.sh`.

📊 Record the optimal concurrency in `handoff.md`. Phase 52's fleet expansion uses it.

---

### Task 6 — Gate G2 evidence

**G2: a node reimages from bare metal to `Ready` with zero human touch in < 15 minutes, repeated 3×.**

**`evidence/phase-11/g2-zero-touch.md`**

```markdown
# Gate G2 — Zero-Touch Provisioning

## Method
Three different nodes, each provisioned from a powered-off state with no human
interaction after invoking `timed-provision.sh`. No console access, no USB, no
BIOS interaction, no manual config application.

## Definition of "zero touch"
The only human action is issuing the command. Specifically NOT permitted:
  · pressing a power button          · attaching a keyboard or monitor
  · selecting a boot device          · entering BIOS
  · manually applying a Talos config · reseating anything

## Definition of "Ready"
Talos `machinestatus.spec.stage == "running"`, the Talos API answers over mTLS,
the machine config matches the rendered config, and all expected network
interfaces are up at the expected MTU.
(Kubernetes `Ready` is not achievable until Phase 12/13 — that is noted and G3
covers it.)

## Results
| Run | Node | Archetype | Total | Within budget | Notes |
|---|---|---|---|---|---|
| 1 | nx-c-r01-05 | compute-gpu | 11m 04s | ✅ | |
| 2 | nx-c-r01-06 | compute-gpu | 10m 47s | ✅ | |
| 3 | nx-s-r01-01 | storage      | 13m 22s | ✅ | larger image; more disks to enumerate |

## Phase breakdown (median)
<paste the JSON timing records>

## Parallel provisioning
| Concurrency | Nodes | Wall time | Per-node | Bottleneck |
| 1 | 3 | 33m | 11.0m | — |
| 2 | 4 | 24m | 12.0m | image transfer |
| 4 | 4 | 31m | 15.5m | ⚠️ management link saturated |
→ Recommended concurrency: 2 over the management link; retest after Phase 42
  when images are served from Harbor over the cluster VLAN.

## VERDICT: G2 PASS / FAIL
```

---

### Task 7 — Failure-mode catalogue

While provisioning repeatedly, you will hit real failures. Catalogue them — this becomes the seed of `runbooks/` (Phase 47).

**`docs/operations/provisioning-slo.md`**, failure section:

| Failure | Frequency observed | Detection | Automated response | Manual step |
|---|---|---|---|---|
| WoL does not wake the node | | `NodeDrawingZeroCurrent` after WoL | Fall back to a PDU cycle | Check BIOS ErP |
| PXE times out | | No DHCPDISCOVER within 90 s | Retry once, then PDU cycle | PiKVM to see the POST screen |
| Image write fails midway | | Workflow state FAILED | Retry the workflow (idempotent) | Check the image URL and checksum |
| Node boots the old OS instead of PXE | | Talos API answers with the wrong version | — | BIOS boot order |
| Config apply rejected | | `talosctl validate` or apply error | — | Fix the patch, re-render |
| Node stuck in maintenance mode | | `machinestatus.stage != running` | Re-apply the config | Check `talosctl dmesg` |
| Link negotiated below spec | | `SwitchPortSpeedDegraded` | Cordon the node | Replace cable/transceiver |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Bootstrap monitoring stack is running | `docker compose ps` | All healthy |
| **A2** | Prometheus scrapes seed, Tinkerbell, CoreDNS, PDUs, switches, node liveness | `curl localhost:9090/api/v1/targets` | All `up` |
| **A3** | Targets are generated from inventory, not hand-written | Read `gen-prometheus-targets.sh`; regenerate | Deterministic |
| **A4** | Provisioning dashboard renders with real data | Open Grafana | All nine panels populated |
| **A5** | PDU power metrics are collected per outlet | Query `pdu_outlet_current_amps` | Values present for every node |
| **A6** | Switch port speed is monitored and alerts below 25 GbE | Query `ifHighSpeed`; force a test alert | Alert fires |
| **A7** | `NodePoweredButNoNetwork` fires correctly | Unplug a node's data cable while powered | Alert within 5 min |
| **A8** | `PduCircuitNearLimit` threshold matches Phase 02's derate | Compare the rule to `circuits.yaml` | 0.80 |
| **A9** | `timed-provision.sh` emits a complete phase breakdown | Run it | JSON with all six phases |
| **A10** | **G2: three nodes provisioned zero-touch in < 15 min each** | `evidence/phase-11/g2-zero-touch.md` | All three within budget |
| **A11** | Zero-touch is genuinely zero-touch | Observe the runs | No human action after the command |
| **A12** | Provisioning is idempotent | Reprovision the same node | Same result, no error |
| **A13** | Parallel provisioning tested; concurrency limit and bottleneck identified | Timing records | Documented |
| **A14** | Power-on staggering prevents an inrush trip | Provision a batch; watch circuit current | No breaker event; current ramps |
| **A15** | Failure catalogue has ≥ 5 real observed failures with detection and response | Read the SLO doc | Present |
| **A16** | The monitoring stack survives a seed-node reboot | Reboot; check | Comes back automatically |

---

## ↩️ ROLLBACK

```bash
cd bootstrap/observability && docker compose down
```
Monitoring only — removing it does not affect provisioning, only visibility.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| snmp_exporter returns nothing for the PDU | Wrong module, or SNMPv3 credentials not configured | Build a module with `generator` from the vendor MIB; test with `snmpwalk` first |
| Blackbox ICMP probes all fail | Container lacks `NET_RAW` | Add the capability, or use `tcp_connect` instead of `icmp` |
| Provisioning timings vary wildly | Image served over a congested link, or disk speed differences | Record per-node; correlate with `inventory` disk class. Consumer SATA SSD writes far slower than NVMe. |
| Total time exceeds 15 min consistently | Image transfer dominates over 1 GbE | Serve images over the cluster VLAN (9000 MTU), or accept a higher budget and document it |
| Grafana dashboard is empty | Datasource URL wrong inside the Docker network | Use the service name (`http://prometheus:9090`), not `localhost` |
| Alert rules never fire | `for:` duration too long for the test, or the expression matches nothing | Test with `promtool test rules`; shorten `for:` temporarily |
| Breaker trips during a batch | No power-on stagger | Add a 5 s stagger. **Verify the circuit is reset before retrying.** |
| Logs from provisioning are not in Loki | Promtail is not reading the dnsmasq/Tinkerbell log paths | Check the promtail scrape config and volume mounts |

---

## 🚫 DO NOT

- **Do not** build the full observability platform here. That is Phase 45. This stack is deliberately minimal and out-of-band.
- **Do not** bootstrap Kubernetes. That is Phase 12.
- **Do not** install monitoring agents on the nodes. Talos nodes expose telemetry through their own API; in-cluster monitoring comes in Phase 45.
- **Do not** claim G2 passed with fewer than three runs, or with any human intervention during a run.
- **Do not** provision a whole rack at once without a power-on stagger.
- **Do not** delete this stack after Phase 45. It becomes the out-of-band monitor that still works when the cluster does not.

---

## 📤 HANDOFF

`evidence/phase-11/handoff.md` must state:

1. **G2 verdict** with the three timing records — this authorizes Stage 2.
2. **The provisioning time budget** and which phase dominates (almost always image write).
3. **Optimal parallel concurrency** and the bottleneck — Phase 52 provisions the fleet using this.
4. **The power-on stagger interval** required to avoid inrush trips.
5. **Observed failure modes** with frequency — seeds Phase 47's runbooks and Phase 23's auto-remediation.
6. **Any node that could not be provisioned zero-touch**, and why (BIOS, NIC, cabling). These need physical remediation before Stage 2.
7. **Bootstrap monitoring endpoints** — Phase 45 federates or replaces these.
8. **Switch ports that negotiated below spec** — R-01 tripwires that must be fixed before Phase 21.

---

## ➡️ NEXT

**[PHASE-12 — HA Control Plane & etcd](PHASE-12.md)** — bootstrap Kubernetes on three control nodes, with etcd on PLP NVMe and a hard performance contract. Stage 2 begins.
