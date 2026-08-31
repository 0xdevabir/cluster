# PHASES — Execution Protocol

### How to build Project NEXUS, one bounded work order at a time

> **You are an implementation agent.** This file is your operating manual. Read it once, fully, before opening any `PHASE-XX.md`.
> Companion documents: `../ULTIMATE-PLAN.md` (why) · `../ARCHITECTURE.md` (how the pieces fit).

---

## 1. The Contract

Each `PHASE-XX.md` is a **self-contained work order**. It is sized so that one focused session completes it. When you open a phase file:

| You will find | You must produce |
|---|---|
| A precise **Mission** | Working, committed artifacts at the exact paths listed in **Deliverables** |
| **Preflight** checks (commands that must pass first) | Evidence of every preflight passing, in `evidence/phase-XX/preflight.md` |
| Numbered **Tasks** with real commands and real file contents | Every task done, in order, with no silent skips |
| **Acceptance Criteria** (machine-checkable) | `evidence/phase-XX/acceptance.md` showing each criterion PASS with its command output |
| **Rollback** procedure | Nothing — unless you need it |
| **Troubleshooting** table | Consult it *before* improvising |
| **Do NOT** list | Absolute scope boundary. Do not cross it. |
| **Handoff** notes | `evidence/phase-XX/handoff.md` — anything the next phase needs to know that isn't already written down |

---

## 2. The Nine Rules

These override any instinct you have to be helpful in a different way.

### Rule 1 — Never start a phase whose Preflight fails.
If a preflight check fails, **stop**, write what failed to `evidence/phase-XX/BLOCKED.md`, and report it. Do not "work around" a missing prerequisite. A phase built on a broken foundation is worse than no phase.

### Rule 2 — Stay inside the phase boundary.
Every phase has a **Do NOT** section. It exists because the excluded work belongs to a later phase that has the right context for it. Building ahead creates conflicts, half-configured components, and untestable states. If you find yourself thinking *"while I'm here I might as well…"* — stop and write it in `handoff.md` instead.

### Rule 3 — Everything goes in Git. Nothing is applied by hand.
After Phase 14 (GitOps), the *only* legitimate way to change the cluster is a commit. Before Phase 14, bootstrap commands are allowed but must be recorded verbatim in `bootstrap/`. If you ever type `kubectl apply -f` against a live cluster after Phase 14, you have created an incident, not a feature.

### Rule 4 — Pin every version. Always.
No `:latest`. No unpinned Helm chart versions. No `curl | bash` from a moving target. Every phase file states the exact versions to use. If a version is unavailable, record the substitution and its reason in `handoff.md` — do not silently take a newer one.

### Rule 5 — Write the evidence as you go, not at the end.
`evidence/phase-XX/` is the proof the phase actually works. Paste real command output. If you did not run it, do not claim it passed. **A phase with fabricated evidence is a catastrophic failure**, because every later phase trusts it.

### Rule 6 — When reality contradicts the phase file, reality wins — and you write it down.
Hardware differs. Upstream projects rename flags. If the instructions do not match what you find, adapt, and record the divergence in `evidence/phase-XX/deviations.md` with: what the file said, what you found, what you did, why.

### Rule 7 — Idempotency is mandatory.
Every script, manifest, and procedure you write must be safe to run twice. Use `kubectl apply` not `create`, `helm upgrade --install` not `install`, `mkdir -p` not `mkdir`. Assume the phase will be re-run.

### Rule 8 — Benchmark before you claim performance.
Any statement of the form "this is fast" requires a number, a command that produced it, and a comparison to the baseline in `benchmarks/baselines/`. Law VIII.

### Rule 9 — Secrets never enter Git in plaintext.
SOPS+age for bootstrap material, Vault/ESO for everything after Phase 09. If a phase file shows a placeholder like `<REPLACE-ME>`, it means *generate a real value and store it in the secret store*, not *commit the placeholder*.

---

## 3. Standard Working Procedure

```
 ┌─ 1. READ ──────────────────────────────────────────────────────────────┐
 │  Read the whole phase file. Then read the ARCHITECTURE.md sections it   │
 │  references. Do not start typing until you can state the mission in     │
 │  one sentence and name every deliverable.                               │
 └────────────────────────────────────────────────────────────────────────┘
 ┌─ 2. PREFLIGHT ─────────────────────────────────────────────────────────┐
 │  Run every preflight command. Capture output. All must pass.            │
 │  → evidence/phase-XX/preflight.md                                       │
 └────────────────────────────────────────────────────────────────────────┘
 ┌─ 3. PLAN ──────────────────────────────────────────────────────────────┐
 │  Write evidence/phase-XX/plan.md: the ordered list of tasks, the files  │
 │  each will create, and any decision you must make. 10 lines is enough.  │
 └────────────────────────────────────────────────────────────────────────┘
 ┌─ 4. EXECUTE ───────────────────────────────────────────────────────────┐
 │  Tasks in order. After each: does it do what it should? Prove it.       │
 │  Commit after each logical task with a conventional-commit message:     │
 │     feat(phase-12): install cilium with kube-proxy replacement          │
 └────────────────────────────────────────────────────────────────────────┘
 ┌─ 5. VERIFY ────────────────────────────────────────────────────────────┐
 │  Run every acceptance criterion. Capture real output.                   │
 │  → evidence/phase-XX/acceptance.md                                      │
 │  ANY failure → fix it, or mark the phase INCOMPLETE. Never both.        │
 └────────────────────────────────────────────────────────────────────────┘
 ┌─ 6. HANDOFF ───────────────────────────────────────────────────────────┐
 │  evidence/phase-XX/handoff.md — what the next phase must know:          │
 │  addresses assigned, credentials created (by reference, not value),     │
 │  deviations, deferred work, surprises.                                  │
 └────────────────────────────────────────────────────────────────────────┘
 ┌─ 7. REPORT ────────────────────────────────────────────────────────────┐
 │  Summarize: what was built, what passed, what deviated, what's next.    │
 │  Be factual. If something is incomplete, say so plainly and first.      │
 └────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Conventions Used in Every Phase File

### 4.1 Callout markers

| Marker | Meaning |
|---|---|
| 🎯 **MISSION** | The one thing this phase exists to achieve |
| ⚠️ **DANGER** | An action that can destroy data or break the cluster. Read twice, confirm before running. |
| 🔒 **SECRET** | Handling material that must never be committed in plaintext |
| 📊 **BENCHMARK** | A measurement that must be recorded in `benchmarks/` |
| 🧪 **VERIFY** | An acceptance check |
| 💡 **WHY** | Rationale — read it; it prevents wrong improvisation |
| 🚫 **DO NOT** | Hard scope boundary |
| ↩️ **ROLLBACK** | How to undo |

### 4.2 Placeholder syntax

| Placeholder | Meaning |
|---|---|
| `<REPLACE-ME>` | Generate a real value now; store it in the secret store |
| `${VAR}` | Read from the environment or `inventory/` — never hardcode |
| `# TODO(phase-NN)` | Deliberately deferred to a later phase. Leave it. |
| `nx-c-r01-05` | Example node name. Substitute from `inventory/nodes/`. |
| `10.200.0.x` | Example IP. Substitute from `inventory/network/ip-plan.yaml`. |

### 4.3 Commit message format

```
<type>(phase-NN): <imperative summary>

<what changed and why, 1–3 lines>

Evidence: evidence/phase-NN/<file>
Refs: ARCHITECTURE.md#<section>
```
Types: `feat` · `fix` · `docs` · `chore` · `perf` · `refactor` · `test` · `bench`

### 4.4 Version pinning table

Every phase that installs software declares versions in a table like this, and those exact versions go into the manifest:

| Component | Version | Source |
|---|---|---|
| example | `v1.2.3` | `oci://registry/chart` |

**If a pinned version no longer exists**, record the substitution in `deviations.md` and pick the nearest patch release of the same minor version. Never jump a minor version without noting it.

---

## 5. Milestone Gates

Phases are grouped into stages. **A stage's exit gate must pass before the next stage begins.** Gates are defined in `ULTIMATE-PLAN.md §13`; the phase that verifies each one is noted below.

| After stage | Gate(s) | Verified in |
|---|---|---|
| 0 — Foundation | G0, G1 | Phase 05 |
| 1 — Bootstrap | G2 | Phase 11 |
| 2 — Substrate | G3 | Phase 17 |
| 3 — Acceleration | G4, G5 | Phase 23 |
| 4 — Storage | G7 (partial) | Phase 29 |
| 5 — Scheduling | G8 | Phase 35 |
| 6 — Compute | G6, G9 | Phase 41 |
| 7 — Platform | G12, G14 | Phase 47 |
| 8 — Performance | G11 | Phase 52 |
| 9 — Operations | G10, G13 | Phase 56 |

---

## 6. Complete Phase Index

### Stage 0 — Foundation & Design (`00`–`05`)
*No hardware is purchased and no cable is run until this stage is complete.*

| # | Phase | Mission | Est. | Depends on |
|---|---|---|---|---|
| **00** | [Repository Foundation & Toolchain](PHASE-00.md) | Create the repo skeleton, dev tooling, linting, CI scaffold, and the conventions every later phase relies on | 2–3 h | — |
| **01** | [Hardware Inventory & Capability Model](PHASE-01.md) | Build the machine-readable source of truth for every physical machine, including PCIe/NUMA topology and GPU capability flags | 3–4 h | 00 |
| **02** | [Facility, Power & Thermal Design](PHASE-02.md) | Electrical load study, cooling plan, rack layout, PDU/KVM strategy — the plan that keeps breakers closed | 3–4 h | 01 |
| **03** | [Network Fabric Design](PHASE-03.md) | Leaf-spine topology, IP/VLAN plan, BGP design, RoCE lossless configuration contract, switch port map | 4–5 h | 01, 02 |
| **04** | [Security Architecture & Threat Model](PHASE-04.md) | Trust zones, PKI hierarchy, identity model, policy baseline, the audited privileged-workload list | 3 h | 00 |
| **05** | [Capacity Model & Stage-0 Gate](PHASE-05.md) | Sizing math for compute/storage/network/power; validate G0 + G1; produce the buy list | 3 h | 01–04 |

### Stage 1 — Bootstrap Infrastructure (`06`–`11`)

| # | Phase | Mission | Est. | Depends on |
|---|---|---|---|---|
| **06** | [Seed Node & Workstation Toolchain](PHASE-06.md) | Build the single machine from which everything else is provisioned; install and pin every CLI | 2–3 h | 05 |
| **07** | [Core Network Services (DHCP/DNS/NTP/PKI)](PHASE-07.md) | Kea/dnsmasq, CoreDNS, chrony, step-ca — the services every node needs before it can exist | 3–4 h | 03, 06 |
| **08** | [Bare-Metal Provisioning with Tinkerbell](PHASE-08.md) | Netboot pipeline, hardware discovery, WoL + PDU power control, zero-touch workflow | 5–6 h | 07 |
| **09** | [Talos Linux OS Layer](PHASE-09.md) | Image Factory schematics, machine-config patch hierarchy, kernel/sysctl tuning, per-archetype configs | 5–6 h | 08 |
| **10** | [Secrets & Configuration Management](PHASE-10.md) | SOPS+age bootstrap chain, OpenBao deployment plan, secret taxonomy and rotation policy | 3–4 h | 04, 06 |
| **11** | [Bootstrap Observability & Gate G2](PHASE-11.md) | Provisioning-time metrics/logs; prove a node goes bare-metal → Ready in < 15 min, untouched | 3 h | 09, 10 |

### Stage 2 — Kubernetes Substrate (`12`–`17`)

| # | Phase | Mission | Est. | Depends on |
|---|---|---|---|---|
| **12** | [HA Control Plane & etcd](PHASE-12.md) | 3-node control plane, VIP, etcd on PLP NVMe with a hard performance contract | 4–5 h | 11 |
| **13** | [Cilium Datapath](PHASE-13.md) | eBPF, kube-proxy replacement, native routing, BGP peering, BIG TCP, Hubble | 4–5 h | 12 |
| **14** | [Node Onboarding & Label Taxonomy](PHASE-14.md) | NFD + the NEXUS hardware labeler, taints, quarantine/readiness gate, pool membership | 4 h | 13 |
| **15** | [GitOps with Argo CD](PHASE-15.md) | App-of-apps, sync waves, drift detection, the repo structure everything after this lives in | 4–5 h | 12 |
| **16** | [Policy, Tenancy & RBAC](PHASE-16.md) | Kyverno, Pod Security Admission, Capsule tenants, default-deny network policy, OIDC → RBAC | 4–5 h | 15, 10 |
| **17** | [Ingress, Certificates & Identity](PHASE-17.md) | Gateway API + Envoy, cert-manager, external-dns, Keycloak, OIDC everywhere; verify G3 | 4–5 h | 16 |

### Stage 3 — Acceleration Fabric (`18`–`23`)

| # | Phase | Mission | Est. | Depends on |
|---|---|---|---|---|
| **18** | [NVIDIA GPU Operator](PHASE-18.md) | Driver/toolkit/device-plugin/DCGM lifecycle, CDI, persistence mode, per-model handling | 4–5 h | 14, 15 |
| **19** | [Dynamic Resource Allocation for GPUs](PHASE-19.md) | NVIDIA DRA driver, DeviceClasses, sharing modes (MPS/time-slice), VRAM admission control | 5–6 h | 18 |
| **20** | [NUMA, CPU & Memory Topology](PHASE-20.md) | CPU Manager static, Topology Manager single-numa-node, hugepages, isolcpus, validation harness | 4–5 h | 18 |
| **21** | [SR-IOV, RDMA & RoCE Fabric](PHASE-21.md) | Network Operator, Multus, SR-IOV VFs, RDMA device plugin, PFC/ECN/DCQCN end-to-end | 6–7 h | 13, 20 |
| **22** | [NCCL & Collective Communication Tuning](PHASE-22.md) | nccl-tests harness, per-pool NCCL env injection, ring/tree analysis, busbw baselines | 4–5 h | 21 |
| **23** | [GPU Health & Auto-Remediation; Gate G4/G5](PHASE-23.md) | DCGM health policies, XID→taint pipeline, NPD, Medik8s NHC, full-mesh fabric validation | 4–5 h | 22 |

### Stage 4 — Storage Fabric (`24`–`29`)

| # | Phase | Mission | Est. | Depends on |
|---|---|---|---|---|
| **24** | [Storage Architecture & Device Preparation](PHASE-24.md) | Classify every disk, wipe/partition policy, tier assignment, `fio` device baselines | 3–4 h | 14 |
| **25** | [Tier 0 — Local NVMe Scratch](PHASE-25.md) | LVM LocalPV, generic ephemeral volumes, quota enforcement, zero-overhead validation | 3–4 h | 24 |
| **26** | [Tier 1 — Replicated Fast Block (Mayastor)](PHASE-26.md) | OpenEBS Mayastor over NVMe-oF/RDMA, 2-way replication, hugepage requirements | 4–5 h | 25, 21 |
| **27** | [Tier 2 — Rook-Ceph Distributed Storage](PHASE-27.md) | Ceph cluster, rack-aware CRUSH, RBD + CephFS, WAL/DB on PLP NVMe, recovery throttles | 6–7 h | 26 |
| **28** | [Tier 3/4 — Object Storage & Dataset Cache](PHASE-28.md) | RGW/MinIO S3, erasure coding, JuiceFS cache over T0, dataset warmer, auto-mount policy | 5–6 h | 27 |
| **29** | [Backup, DR & Storage Gate](PHASE-29.md) | Velero, etcd snapshots, CSI snapshot schedules, restore drill, rack-loss chaos test (G7) | 4–5 h | 28 |

### Stage 5 — Scheduling & Resource Management (`30`–`35`)

| # | Phase | Mission | Est. | Depends on |
|---|---|---|---|---|
| **30** | [Kueue: Quotas, Cohorts & Fair Share](PHASE-30.md) | ClusterQueues, ResourceFlavors bound to real hardware, borrowing, priority classes | 5–6 h | 19, 16 |
| **31** | [Gang Scheduling & Topology-Aware Placement](PHASE-31.md) | coscheduling PodGroups, Kueue TAS, the Topology CR, placement-quality measurement | 5–6 h | 30, 22 |
| **32** | [Power-Aware Scheduling & Node Power Control](PHASE-32.md) | Watts as a schedulable resource, per-rack breaker envelopes, WoL scale-to-zero, PDU controller | 5–6 h | 31, 02 |
| **33** | [Preemption, Checkpointing & Elastic Jobs](PHASE-33.md) | Checkpoint hook contract, graceful preemption, requeue policy, elastic world-size support | 5–6 h | 31 |
| **34** | [Accounting, Showback & Quota Governance](PHASE-34.md) | GPU-seconds, Watt-hours, per-tenant reports, idle-hoarding detection, quota review loop | 4–5 h | 30, 32 |
| **35** | [Slurm Interoperability & Scheduling Gate (G8)](PHASE-35.md) | Slinky Slurm-on-K8s overlay for `sbatch` users; verify gang + TAS placement end-to-end | 5–6 h | 31, 33 |

### Stage 6 — Distributed Compute Runtimes (`36`–`41`)

| # | Phase | Mission | Est. | Depends on |
|---|---|---|---|---|
| **36** | [Ray on Kubernetes (KubeRay)](PHASE-36.md) | RayCluster CRDs, GCS fault tolerance, autoscaler, object store on T0, RDMA transport | 5–6 h | 31, 25 |
| **37** | [PyTorch Distributed Training](PHASE-37.md) | Kubeflow Trainer v2, torchrun rendezvous, FSDP/DDP/DeepSpeed recipes, distributed checkpointing | 6–7 h | 36, 33 |
| **38** | [Spark & Dask for Data Processing](PHASE-38.md) | Spark on K8s with T0 shuffle, Dask clusters, dataset pipelines feeding training | 4–5 h | 28, 31 |
| **39** | [MPI & Classic HPC Workloads](PHASE-39.md) | MPI Operator, UCX over RDMA, PMIx, HPL/HPCG validation, hugepage-backed ranks | 5–6 h | 21, 31 |
| **40** | [LLM Inference: vLLM, KServe & Multi-Node Serving](PHASE-40.md) | vLLM runtime, LeaderWorkerSet TP/PP topology, KV-cache-aware routing, autoscaling | 6–7 h | 36, 19 |
| **41** | [Workflow Orchestration & Compute Gate (G6/G9)](PHASE-41.md) | Argo Workflows + Events, end-to-end pipeline, node-kill resilience test, NCCL busbw gate | 5–6 h | 37, 40 |

### Stage 7 — Platform Experience (`42`–`47`)

| # | Phase | Mission | Est. | Depends on |
|---|---|---|---|---|
| **42** | [Container Registry & Build Farm](PHASE-42.md) | Harbor, Spegel P2P mirror, BuildKit build farm, base image catalog, signing pipeline | 5–6 h | 17, 27 |
| **43** | [Notebooks & Interactive Development](PHASE-43.md) | JupyterHub with DRA GPU claims, idle culling, code-server, SSH-into-job, VS Code remote | 4–5 h | 19, 27 |
| **44** | [Experiment Tracking & Model Registry](PHASE-44.md) | MLflow, artifact store on T3, model→OCI promotion path into serving | 4–5 h | 42, 28 |
| **45** | [Full Observability Stack](PHASE-45.md) | Prometheus+Mimir, Grafana dashboard set, Loki, Tempo, OTel, Parca, Kepler, cardinality control | 6–7 h | 17 |
| **46** | [Developer Portal & Golden Paths](PHASE-46.md) | Backstage, software templates, the `nexus` CLI, user documentation, onboarding flow | 5–6 h | 45, 41 |
| **47** | [SLOs, Alerting & On-Call (G12/G14)](PHASE-47.md) | SLO definitions, burn-rate alerts, runbook-per-alert coverage, incident process, drift audit | 4–5 h | 45 |

### Stage 8 — Performance Engineering (`48`–`52`)

| # | Phase | Mission | Est. | Depends on |
|---|---|---|---|---|
| **48** | [Benchmark Harness & Baselines (B1–B12)](PHASE-48.md) | Build the full benchmark suite, run it fleet-wide, commit the baseline corpus | 6–7 h | 41 |
| **49** | [Systematic Tuning: BIOS → Kernel → Runtime](PHASE-49.md) | The tuning campaign: firmware, kernel args, IRQ affinity, power caps, PCIe — each change measured | 7–8 h | 48 |
| **50** | [Performance CI & Regression Gates](PHASE-50.md) | Continuous benchmarking, statistical regression detection, merge-blocking gates, trend dashboards | 5–6 h | 49 |
| **51** | [Distributed Profiling & Bottleneck Analysis](PHASE-51.md) | Nsight Systems at scale, torch profiler, Parca, roofline analysis, the "why is my job slow" runbook | 5–6 h | 50 |
| **52** | [Scale-Out Validation to 100+ Nodes (G11)](PHASE-52.md) | Staged expansion, scaling-cliff mitigations, full-fleet benchmark, SLO verification at scale | 6–8 h | 51 |

### Stage 9 — Operations & Evolution (`53`–`56`)

| # | Phase | Mission | Est. | Depends on |
|---|---|---|---|---|
| **53** | [Day-2 Operations & Upgrades](PHASE-53.md) | Canary pool, staged Talos/K8s/driver upgrades, automated rollback, maintenance windows | 5–6 h | 52 |
| **54** | [Chaos Engineering & Disaster Recovery (G10)](PHASE-54.md) | F1–F14 fault injection, scheduled game days, full cluster rebuild drill from Git + backups | 6–7 h | 53, 29 |
| **55** | [Security Hardening & Supply Chain (G13)](PHASE-55.md) | CIS benchmarks, cosign enforcement, SBOM attestation, Tetragon policies, audit review | 5–6 h | 54 |
| **56** | [Capacity Planning, Documentation & Handover](PHASE-56.md) | Growth model, procurement playbook, complete operator documentation, training, roadmap | 4–5 h | 55 |

---

## 7. Dependency Graph

```
 00 ──┬─► 01 ──┬─► 02 ──┬─► 03 ──┬─► 05 ──► 06 ──► 07 ──► 08 ──► 09 ──┬─► 11 ──► 12
      │        │        │        │                                    │
      └─► 04 ──┴────────┴────────┘                       10 ──────────┘
                                                          │
                                    12 ──┬─► 13 ──► 14 ───┼──► 18 ──► 19 ──┬──► 30
                                         │                │                 │
                                         └─► 15 ──► 16 ───┴──► 17           │
                                                            │               │
                                    13,20 ──► 21 ──► 22 ──► 23              │
                                              │                             │
                              14 ──► 24 ──► 25 ──► 26 ──► 27 ──► 28 ──► 29  │
                                              │                             │
                                              └──────────► 30 ──► 31 ──┬──► 32 ──► 34
                                                                       ├──► 33
                                                                       └──► 35
                                                                            │
                              31,25 ──► 36 ──┬─► 37 ──┬──► 41 ──► 42 ──► 43 ┤
                                             ├─► 38   │              44 ────┤
                                             ├─► 39   │              45 ──► 46 ──► 47
                                             └─► 40 ──┘                            │
                                                                                   ▼
                                                   48 ──► 49 ──► 50 ──► 51 ──► 52 ──►
                                                   53 ──► 54 ──► 55 ──► 56
```

**Parallelizable groups** (safe to run concurrently if you have multiple agents):
- `{02, 03, 04}` after 01
- `{18, 24}` after 14
- `{15, 13}` after 12
- `{37, 38, 39, 40}` after 36
- `{42, 43, 44, 45}` after 41

---

## 8. Minimum Viable Cluster (the fast path)

If you need a working 4-node GPU cluster before building the full platform, execute this subset in order. Everything else layers on top later without rework.

```
00 → 01 → 03 → 06 → 07 → 08 → 09 → 12 → 13 → 14 → 15 → 18 → 19 → 20 → 25 → 30 → 31 → 36
```

That is **18 phases** and yields: provisioned Talos nodes, HA Kubernetes, Cilium, GPU allocation via DRA, NUMA-aligned scheduling, local scratch storage, quota-managed batch queues, gang scheduling, and Ray. You can train on it. It is missing: RDMA, distributed storage, observability, multi-tenancy, and every performance guarantee. **Do not call it production.**

---

## 9. What "Done" Looks Like

A phase is complete when — and only when — all of these are true:

- [ ] Every deliverable exists at its specified path
- [ ] Every acceptance criterion passes, with real output in `evidence/phase-XX/acceptance.md`
- [ ] All work is committed with conventional-commit messages
- [ ] `evidence/phase-XX/handoff.md` exists and is honest
- [ ] Any deviation is recorded in `evidence/phase-XX/deviations.md`
- [ ] Nothing from the **Do NOT** list was built
- [ ] Nothing was applied to the cluster outside of Git (post-Phase-15)
- [ ] Secrets are in the secret store; none are in Git

If any box is unchecked, the phase is **incomplete**. Report it as incomplete. An honestly incomplete phase is recoverable; a falsely complete one poisons everything downstream.

---

## 10. Emergency Reference

| Situation | Do this |
|---|---|
| Preflight fails | Stop. `evidence/phase-XX/BLOCKED.md`. Report. |
| A command destroys something | Follow the phase's ↩️ ROLLBACK. Then write `evidence/phase-XX/incident.md`. |
| Upstream docs contradict this file | Reality wins. Record in `deviations.md`. |
| You do not have the hardware yet | Phases 00–05 need none. Phases 06–11 need one machine. Note the blocker and do what you can. |
| A phase seems too large | It is not; it is dense. Work the tasks in order. Do not skip to the end. |
| You are unsure whether something is in scope | If it is not in **Deliverables**, it is not in scope. Put it in `handoff.md`. |
| The cluster is down and you need to fix it fast | `runbooks/` (built in Phase 47). Before that, `evidence/*/handoff.md` is your history. |

---

*Begin with [PHASE-00.md](PHASE-00.md).*
