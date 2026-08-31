# PHASE 45 — Full Observability Stack

| | |
|---|---|
| **Stage** | 7 — Platform Experience |
| **Estimated effort** | 5–6 hours |
| **Depends on** | 11, 27, 28, 41 |
| **Blocks** | 46, 47, 50, 51, 54 |
| **Risk** | 🟠 Medium — observability that falls over during an incident is worse than none |
| **Blast radius** | Every diagnostic capability |
| **Architecture refs** | `ARCHITECTURE.md#l10-observability--control`, `#l101-the-signal-map`, `ULTIMATE-PLAN.md#13-gates` |

---

## 🎯 MISSION

Replace Phase 11's bootstrap Prometheus with the **full observability platform**: long-term metrics (Mimir), logs (Loki), traces (Tempo), continuous profiling (Parca), and power telemetry (Kepler) — unified in Grafana, correlated across signals, and **sized so it survives the incident it exists to diagnose**.

> 💡 **WHY replace rather than extend.** Phase 11's Prometheus was deliberately minimal: enough to see nodes provisioning and catch a switch port at the wrong speed. It has ~15 days of local retention, no HA, and no logs or traces. At 100+ nodes with production inference, you need 13 months of history (Phase 34's accounting), logs correlated to the metric spike that prompted the search, and traces through the inference path. That is a different system, not a bigger one.

> ⚠️ **The failure mode that matters: observability collapsing under load.** When a cluster-wide incident happens, every component logs harder, metric cardinality explodes, and an under-provisioned monitoring stack falls over exactly when you need it. **Observability must be sized for the bad day**, run on separate failure domains from what it watches, and degrade gracefully — dropping detail, never dying.

---

## ✅ PREFLIGHT

```bash
# Object storage for long-term retention
s5cmd ls s3://nexus-metrics/ s3://nexus-logs/ s3://nexus-traces/ 2>/dev/null

# Current bootstrap monitoring, and what depends on it
kubectl get prometheusrules -A | wc -l
kubectl get servicemonitors -A | wc -l

# Storage tiers for the hot path
kubectl get sc nexus-fast nexus-block

# 📊 Current metric volume — sizes everything downstream
promtool query instant 'sum(scrape_samples_scraped)'
promtool tsdb analyze /prometheus | head -30
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/observability/
  mimir-values.yaml                 # long-term metrics
  loki-values.yaml                  # logs
  tempo-values.yaml                 # traces
  grafana-values.yaml               # the single pane
  alloy-daemonset.yaml              # collection agent (metrics/logs/traces)
  parca-values.yaml                 # continuous profiling
  kepler-values.yaml                # power attribution
  alertmanager-config.yaml
  retention-policies.yaml
observability/
  dashboards/                       # 🎯 the curated set
  rules/                            # migrated + expanded from Phase 11
  cardinality-budget.md             # ⚠️ or metrics eat the cluster
tools/observability/
  cardinality-report.sh             # what is expensive?
  correlate.sh                      # metric → logs → traces for one incident
  bench-observability.sh            # 📊 query latency, ingest rate
docs/operations/
  observability-guide.md
  dashboard-index.md
evidence/phase-45/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Role |
|---|---|---|
| Grafana Mimir | `2.15.x` | Long-term metrics, HA, object-backed |
| Grafana Loki | `3.3.x` | Logs |
| Grafana Tempo | `2.7.x` | Traces |
| Grafana | `11.5.x` | UI |
| Grafana Alloy | `1.6.x` | Unified collector (replaces Promtail/OTel agent) |
| Parca | `0.23.x` | Continuous profiling |
| Kepler | `0.8.x` | Power attribution per pod |
| Alertmanager | `0.28.x` | |

> 💡 **Alloy as a single collection agent** rather than Promtail + OTel Collector + node-exporter sidecars. One DaemonSet, one config language, one thing to upgrade. Law X.

---

## 📋 TASKS

### Task 1 — 🎯 The signal map (decide what you collect and why)

Do not collect everything. Every series costs storage, query time, and money forever.

| Signal | Source | Retention | Purpose |
|---|---|---|---|
| **Node/hardware metrics** | node-exporter, DCGM, PDU SNMP, switch SNMP | **13 months** | Capacity, accounting, health |
| **Kubernetes state** | kube-state-metrics | 13 months | Scheduling analysis |
| **Workload metrics** | Application `/metrics` | 90 days | Debugging |
| **Accounting series** (Phase 34) | Recording rules | **13 months, downsampled** | Showback, waterfall |
| **Logs — platform components** | Alloy | 30 days | Incident investigation |
| **Logs — user workloads** | Alloy | 7 days | Debugging; ⚠️ high volume |
| **Traces** | OTel from inference and API paths | 7 days | Latency analysis |
| **Profiles** | Parca | 14 days | Performance work (Phase 51) |
| **Power** | Kepler + PDU | 13 months | Phase 32, cost model |
| **Audit logs** (Phase 12) | API server | **1 year** | 🔒 Security |

> ⚠️ **Retention drives cost more than anything else, and 13 months is not arbitrary** — it is what lets you compare this month to the same month last year, which is what capacity planning actually needs. Use downsampling: full resolution for 30 days, 5-minute for 6 months, 1-hour for 13 months.

**Cardinality budget — `cardinality-budget.md`:**
```
Estimated at 100 nodes:
  node-exporter        ~1,200 series/node  ×100 =  120,000
  DCGM (curated set)      ~40 series/GPU   ×400 =   16,000
  kube-state-metrics                             ~150,000
  cAdvisor (⚠️ the expensive one)                ~400,000
  Cilium/Hubble                                  ~200,000
  Application metrics                            ~100,000
  ─────────────────────────────────────────────────────────
  TOTAL                                        ~1,000,000 active series

Budget: 2,000,000. Alert at 1,500,000.
```

> 🚫 **The cardinality killers, in order:** (1) a label containing a pod name or UID on a high-frequency metric, (2) an unbounded label like a request URL or a user ID, (3) cAdvisor's per-container metrics at full fidelity, (4) histogram buckets multiplied by high-cardinality labels. **Drop what you do not use at collection time**, not at query time — Alloy's `metric_relabel_configs` is the right place.

> 💡 **A cardinality explosion looks exactly like an outage.** Ingesters OOM, queries time out, alerts stop evaluating. Set a per-tenant series limit in Mimir so one team's bad label cannot take down monitoring for everyone.

---

### Task 2 — Deployment topology (separate failure domains)

```
⚠️ THE RULE: observability must not depend on what it observes.

Mimir/Loki/Tempo ingesters  → run on nodes NOT in the GPU pool
Object storage backend      → Ceph RGW (shared) ⚠️ see the caveat
Alertmanager                → 3 replicas, spread across racks
Grafana                     → stateless, 2 replicas
Alloy                       → DaemonSet everywhere (it must see everything)
```

> ⚠️ **The Ceph dependency is a real circularity.** If observability stores metrics in Ceph and Ceph fails, you lose the ability to diagnose the Ceph failure. Mitigations: (a) local WAL/ingester storage on T1 so recent data survives an object-store outage; (b) an out-of-band minimal Prometheus (Phase 11's, kept alive) that scrapes only critical targets with local storage; (c) alerting that can fire without the query path.
>
> **Implement (a) and (b).** Keep the Phase 11 Prometheus as a small, independent "monitoring of last resort" with 24-hour local retention and only the critical alerts. It costs almost nothing and it is the thing that tells you what happened when everything else is down.

**Sizing for the bad day:**

| Component | Normal | ⚠️ Size for |
|---|---|---|
| Log ingest | ~5 GB/day | **50 GB/day** — an incident makes everything log |
| Metric ingest | ~1 M series | 2 M series with burst headroom |
| Query load | Occasional | **10 people opening dashboards simultaneously** |
| Ingester memory | | Enough to survive a 3× cardinality spike without OOM |

---

### Task 3 — Correlation (the capability that makes it worth the cost)

Three signals in three systems is three times the work unless they are linked.

| Link | Mechanism |
|---|---|
| Metric spike → logs at that moment | Grafana data-source correlation; shared labels (`namespace`, `pod`, `node`) |
| Log line → trace | `trace_id` in structured logs; Loki derived field → Tempo |
| Trace span → metrics | Exemplars: Prometheus histogram exemplars carry `trace_id` |
| Trace → profile | Parca labels matching span attributes |
| Any signal → the job that caused it | ⚠️ **A consistent `nexus.io/job-id` label on every signal** |

> 💡 **The `job-id` label is the highest-value piece of correlation glue in the platform.** With it, one query answers "show me everything about job 8823": its GPU metrics, its logs, its traces, its power draw, its cost, its checkpoints. Inject it at admission (alongside Phase 22/30/31/44's injections) so it appears on every signal without user action.

**`tools/observability/correlate.sh`** — the incident-time tool:
```
$ nexus correlate --node nexus-gpu-014 --time "2026-08-31T14:22:00Z" --window 10m
→ Metrics: GPU temp spike 78→91°C, SM_ACTIVE dropped 94%→31%
→ Logs:    "NVRM: Xid (PCI:0000:41:00): 79" at 14:23:07
→ Events:  Node cordoned by remediation-controller at 14:23:45
→ Jobs:    train-8823 (16 GPUs) restarted from checkpoint at 14:26:12
→ Power:   Rack R2 draw dropped 1.8kW at 14:23:50
```

---

### Task 4 — The curated dashboard set

Ten good dashboards beat two hundred. Everything else is ad-hoc exploration.

| Dashboard | Audience | Answers |
|---|---|---|
| **Cluster overview** | Everyone | Is the cluster healthy? What is running? |
| **Utilization waterfall** (Phase 34) | Operators | Where did the GPU-hours go? |
| **GPU fleet** | Operators | Per-GPU health, temp, power, SM_ACTIVE, errors |
| **Fabric** | Operators | Throughput, RDMA health, PFC, errors, per-leaf load |
| **Storage** | Operators | Per-tier capacity, latency, Ceph health, pool fill |
| **Scheduling** | Operators | Queue depth, admission latency, fragmentation, preemptions |
| **Power & thermal** (Phase 32) | Operators | Per-circuit headroom, temps, cap tier |
| **Inference SLO** (Phase 40) | Service owners | TTFT/TPOT/errors/saturation |
| **Job explorer** | Users | One job: everything, via `job-id` |
| **Tenant view** | Tenant leads | Their usage, queue, cost, jobs |
| **Observability self-monitoring** | Operators | ⚠️ Is monitoring itself healthy? |

> ⚠️ **Dashboards as code, in Git, deployed by Argo.** A dashboard edited in the UI and not committed is lost on the next redeploy — and the one time you need it is during an incident after a redeploy. Use the Grafana operator or sidecar-loaded ConfigMaps; make UI editing produce a Git PR.

---

### Task 5 — Alerting discipline

Phase 11 established alerts; this phase makes them sustainable.

**The rules:**

| Rule | Rationale |
|---|---|
| **Every alert must be actionable** | If there is no action, it is a dashboard, not an alert |
| **Every alert links to a runbook** | `runbook_url` annotation, mandatory |
| **Page only for user-visible impact or imminent data loss** | Everything else is a ticket |
| **No alert without a tested firing condition** | Untested alerts do not fire when needed |
| Group and inhibit aggressively | A rack losing power should page once, not 12 times |
| **Review alert noise monthly** | Delete what nobody acts on |

```yaml
# alertmanager-config.yaml — inhibition is what prevents alert storms
inhibit_rules:
  - source_matchers: [severity="critical", alertname="NodeDown"]
    target_matchers: [severity=~"warning|info"]
    equal: [node]                    # a down node silences its own subsidiary alerts
  - source_matchers: [alertname="CoolingFailureSuspected"]
    target_matchers: [alertname="GPUThermalThrottling"]
    equal: [rack]                    # one cause, one page
```

> 💡 **Inhibition rules are what make a 100-node cluster's alerting survivable.** Without them, a rack power loss produces 12 NodeDown + 48 GPU alerts + 6 Ceph OSD alerts + storage warnings + job failures — 80 pages for one event. With them: one page that says "rack R2 lost power."

---

### Task 6 — Profiling and power attribution

**Parca** (continuous profiling) — always-on, low overhead, and it answers "why is this using CPU?" without reproducing the problem.
```
Overhead: ~1 % CPU. Value: finding the hot path in a production job without a repro.
⚠️ Verify the overhead on GPU nodes specifically — profiling must not perturb training.
```

**Kepler** (power per pod) — attributes node power to workloads, connecting Phase 32's facility view to Phase 34's per-tenant accounting.
```
⚠️ Kepler's per-pod attribution is MODELED, not measured — it apportions node power
   by resource usage. Treat it as a good relative signal, not a billing-grade number.
   The PDU reading is ground truth; Kepler tells you which pod is responsible.
   State this distinction wherever Kepler numbers appear.
```

---

### Task 7 — 📊 Benchmark and migrate

**`tools/observability/bench-observability.sh`:**

| Metric | Target | Measured |
|---|---|---|
| Metric ingest rate sustained | ≥ 2× current | |
| Query: 1 h range, single series | < 1 s | |
| Query: 30 d range, aggregated | < 10 s | |
| Query: 13 mo downsampled | < 30 s | |
| Log ingest sustained | ≥ 50 GB/day | |
| Log query: 1 h, one namespace | < 5 s | |
| Trace lookup by ID | < 2 s | |
| **Dashboard load, 10 concurrent users** | < 5 s | |
| Alert evaluation lag | < 30 s | |
| **Recovery after an ingester restart** | No data loss | |

**Migration from Phase 11:** run both in parallel for a week, compare alert firing, then cut over. **Keep the Phase 11 Prometheus** as the independent last-resort monitor (Task 2).

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | Mimir, Loki, Tempo, Grafana all healthy | `kubectl get pods -n observability` | Running |
| **A2** | Alloy DaemonSet on every node, collecting all three signals | Check coverage | 100 % |
| **A3** | Metrics retained 13 months with downsampling | Config + query old data | Configured |
| **A4** | Logs retained per the tier table | Config | Set |
| **A5** | 🔒 Audit logs retained 1 year, access-restricted | Config + RBAC | Correct |
| **A6** | **Cardinality within budget; per-tenant limits enforced** | `cardinality-report.sh` | Within |
| **A7** | 🧪 A cardinality spike in one tenant does not affect others | Inject | Isolated |
| **A8** | Observability components run outside the GPU pool | Inspect placement | Outside |
| **A9** | **The Phase 11 independent Prometheus is retained and working** | Query it | Working |
| **A10** | 🧪 **Alerts still fire when the object store is unavailable** | Scale RGW to 0 | Fire |
| **A11** | `nexus.io/job-id` label present on metrics, logs, and traces | Query each | Present |
| **A12** | 🎯 **`correlate.sh` returns all signals for one incident** | Run on a real event | Complete |
| **A13** | Log → trace linking works (derived fields) | Click through | Works |
| **A14** | Metric → trace linking works (exemplars) | Click through | Works |
| **A15** | All 11 curated dashboards deploy from Git | `kubectl get configmap` | Present |
| **A16** | A UI-edited dashboard does not survive redeploy (by design) | Test | Reverts |
| **A17** | Every alert has a `runbook_url` | Lint the rules | 100 % |
| **A18** | 🧪 **A simulated rack power loss produces ONE page, not 80** | Simulate | One |
| **A19** | Alertmanager HA: killing one replica does not lose alerts | Kill one | No loss |
| **A20** | Parca profiling overhead < 2 % on GPU nodes | 📊 Measure training throughput with/without | Met |
| **A21** | Kepler attributes power per pod; the modeled caveat is documented | Query + read docs | Documented |
| **A22** | 📊 All benchmark targets met | `bench-observability.sh` | Met |
| **A23** | 🧪 Ingester restart loses no data | Restart | No loss |
| **A24** | 10 concurrent dashboard users do not degrade the system | Load test | Stable |
| **A25** | Grafana behind SSO; tenants see only their own data | Test | Isolated |
| **A26** | Phase 11's alerts all migrated or explicitly retired | Diff | Accounted |

---

## ↩️ ROLLBACK

```bash
# The Phase 11 Prometheus is the rollback target — it is still running (A9)
# Point Alertmanager back at it:
kubectl patch prometheus bootstrap -n monitoring --type merge \
  -p '{"spec":{"alerting":{"alertmanagers":[{"name":"alertmanager-main"}]}}}'

# Then remove the new stack
helm uninstall mimir loki tempo -n observability
# ⚠️ Historical data in object storage is retained — do not delete the buckets.
```

> 💡 **Keeping Phase 11's Prometheus alive is what makes this rollback safe**, and it is also the answer to the circular-dependency problem. It is a small standing cost that buys both.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Ingesters OOM | Cardinality spike | A6/A7 — find the offending label; drop at collection |
| Queries time out | Too much data scanned; missing recording rules | Pre-aggregate with recording rules |
| Logs missing for some pods | Alloy not scheduled there, or a log path issue | Check DaemonSet coverage |
| Traces incomplete | Sampling too aggressive, or instrumentation missing | Check the sampler; check OTel setup |
| Alerts stopped firing | Rule evaluation failing, or Prometheus down | A9/A10 — the independent monitor should tell you |
| Alert storm during an incident | Inhibition rules missing | A18 |
| Dashboard disappeared after a deploy | Edited in UI, not committed | A16 — by design; commit it |
| Metrics missing after the cutover | ServiceMonitor not migrated | A26 |
| Observability slow during a cluster incident | Under-sized for the bad day | Task 2's sizing — increase and retest |
| Kepler numbers do not match the PDU | Modeled, not measured | Expected; A21 |
| Parca perturbing training | Overhead too high on GPU nodes | Reduce sampling frequency, or exclude the GPU pool |

---

## 🚫 DO NOT

- **Do not** collect metrics you have no plan to query.
- **Do not** put pod names or UIDs in labels on high-frequency metrics.
- **Do not** run observability entirely on the infrastructure it monitors.
- **Do not** decommission Phase 11's Prometheus.
- **Do not** ship an alert without a runbook link.
- **Do not** page for anything that is not user-visible or imminent data loss.
- **Do not** edit dashboards in the UI as the source of truth.
- **Do not** present Kepler's modeled per-pod power as measured.
- **Do not** size for the normal day.

---

## 📤 HANDOFF

`evidence/phase-45/handoff.md` must state:

1. **📊 The benchmark results**, especially query latency at 13-month range and behavior under 10 concurrent users.
2. **The cardinality budget** — current usage, the limit, and the biggest contributors.
3. **🧪 The alert-storm test result** — one page for a rack loss.
4. **🧪 The object-store-outage test** — alerts still fire.
5. **The correlation model** — how `job-id` flows through every signal, and any gap.
6. **The dashboard set** and where they live in Git.
7. **What Phase 11's independent Prometheus still covers**, and why it stays.
8. **Retention and projected storage cost** per signal — feeds Phase 56.
9. **Parca's measured overhead** on GPU nodes.

---

## ➡️ NEXT

**[PHASE-46 — Developer Portal & Golden Paths](PHASE-46.md)** — pull every capability built so far behind one door that a new user can walk through unaided.
