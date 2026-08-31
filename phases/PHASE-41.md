# PHASE 41 — Workflow Orchestration & Compute Gate (G6/G9)

| | |
|---|---|
| **Stage** | 6 — Distributed Compute Frameworks |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 36, 37, 38, 39, 40 |
| **Blocks** | 42 (Stage 7 entry), 44, 46 |
| **Risk** | 🟡 Medium — an orchestrator that retries badly can amplify a failure into an outage |
| **Blast radius** | Multi-step pipelines |
| **Architecture refs** | `ARCHITECTURE.md#l8-distributed-compute-runtimes`, `ULTIMATE-PLAN.md#13-gates` (G6, G9) |

---

## 🎯 MISSION

Tie the frameworks together. Deploy **Argo Workflows** so a user can express "prepare the data with Spark → train with PyTorch → evaluate → register the model → deploy it" as one versioned, resumable, observable artifact. Then close Stage 6 by passing **gates G6 (distributed compute) and G9 (end-to-end workload)**.

> 💡 **WHY orchestration is the last piece of Stage 6.** Five frameworks that each work in isolation is a toolbox, not a platform. Real work is a *pipeline*: the ETL job that must finish before training starts, the evaluation that gates deployment, the retry when a node dies at step 3 of 7. Without orchestration, users glue this together with shell scripts and cron, and the cluster's careful scheduling, checkpointing, and accounting all leak out through the seams.

> ⚠️ **The amplification hazard.** An orchestrator with naive retries turns one failed 64-GPU training step into ten failed 64-GPU training steps, consuming a day of cluster capacity on a job that was never going to succeed. Retry policy, backoff, and failure classification are the load-bearing design here — not the DAG syntax.

---

## ✅ PREFLIGHT

```bash
# All Stage 6 frameworks operational
kubectl get pods -n ray-system -n kubeflow-system -n data -n hpc -n kserve

# 📊 The benchmarks each produced
ls benchmarks/baselines/b*.json

# Storage for artifacts between steps
s5cmd ls s3://nexus-artifacts-*/

# Quota and priorities (workflows submit on behalf of users)
kubectl get clusterqueues,workloadpriorityclasses
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/compute/workflows/
  argo-workflows-values.yaml
  workflow-templates/               # 🎯 reusable, platform-owned steps
    spark-step.yaml
    train-step.yaml
    ray-step.yaml
    eval-step.yaml
    deploy-step.yaml
  workflow-defaults.yaml            # ⚠️ retry, TTL, resource limits
  events/                           # Argo Events triggers
policies/defaults/
  require-workflow-limits.yaml      # no unbounded workflows
tools/workflows/
  submit-pipeline.sh
  workflow-report.sh
  bench-e2e.sh                      # 📊 B12: full pipeline, end to end
templates/pipelines/
  train-eval-deploy/                # 🎯 the reference pipeline
docs/user/pipelines-guide.md
gates/G6-distributed-compute.md
gates/G9-end-to-end.md
evidence/phase-41/{preflight,acceptance,handoff,deviations,gate-g6,gate-g9}.md
```

---

## 🔧 VERSION PINNING

| Component | Version |
|---|---|
| Argo Workflows | `v3.6.2` |
| Argo Events | `v1.9.4` |

> 💡 **Argo Workflows over Kubeflow Pipelines or Airflow.** Argo CD is already the GitOps engine (Phase 15), so the operational model and CRD idioms are familiar; Workflows is Kubernetes-native, has no separate scheduler or database to operate, and integrates directly with the Job/PodGroup/Kueue path everything else already uses. Airflow would add a Python DAG runtime and a metadata database for no benefit here. Law X.

---

## 📋 TASKS

### Task 1 — 🎯 Platform-owned WorkflowTemplates

Users should compose steps, not write them. Each template wraps a framework with the correct defaults already established.

```yaml
# ClusterWorkflowTemplate: train-step
apiVersion: argoproj.io/v1alpha1
kind: ClusterWorkflowTemplate
metadata: { name: train-step }
spec:
  templates:
    - name: train
      inputs:
        parameters:
          - { name: image }
          - { name: nodes, value: "2" }
          - { name: runtime, value: "torch-ddp" }
          - { name: dataset-uri }
      resource:
        action: create
        successCondition: status.conditions.1.status == True
        failureCondition: status.conditions.0.status == True
        manifest: |
          apiVersion: trainer.kubeflow.org/v1alpha1
          kind: TrainJob
          spec:
            runtimeRef: { name: "{{inputs.parameters.runtime}}" }
            trainer:
              numNodes: {{inputs.parameters.nodes}}
              image: "{{inputs.parameters.image}}"
      outputs:
        parameters:
          - name: checkpoint-uri            # ⚠️ steps pass ARTIFACT URIs, not data
```

**The template set:**

| Template | Wraps | Key defaults it supplies |
|---|---|---|
| `spark-step` | SparkApplication (38) | CPU pool, magic committer, scratch spill |
| `ray-step` | RayJob (36) | `/dev/shm`, spill, shutdown-after-finish |
| `train-step` | TrainJob (37) | NCCL, topology, checkpoint sync, gang |
| `mpi-step` | MPIJob (39) | UCX, pinning, required topology |
| `eval-step` | Plain Job | Small GPU claim, short deadline |
| `deploy-step` | InferenceService (40) | Canary traffic, SLO gate |
| `dataset-step` | Phase 28 import/reshard | Catalog registration |

> ⚠️ **Steps must pass URIs, never data.** An Argo artifact passed between steps is copied through the artifact repository. A 200 GB dataset passed as an artifact means two full transfers per step boundary. **Pass `s3://` URIs and let each step read what it needs** — Law VI applied to pipelines. Enforce it in the templates by only declaring string outputs, and say why in the guide.

---

### Task 2 — ⚠️ Retry policy and failure classification

The most consequential configuration in this phase.

**Not all failures should be retried:**

| Failure class | Detection | Retry? |
|---|---|---|
| **Transient infrastructure** — node failure, image pull timeout, spot preemption | Pod deleted, node NotReady, exit 137 without OOM | ✅ **Yes**, up to 3× with backoff |
| **Preemption** (Phase 33) | Exit 0 with the preemption marker | ✅ **Yes** — and it resumes from checkpoint, so it is cheap |
| **OOM** | Exit 137 with an OOMKilled reason | ⚠️ **Retry once at a larger size**, then fail |
| **Application error** — bad code, bad config, assertion | Non-zero exit, no infra signal | 🚫 **Never retry.** It will fail identically. |
| **Data error** — missing input, bad schema | Step-specific | 🚫 Never |
| **Timeout** | Deadline exceeded | ⚠️ Depends — retrying a job that was 90 % done wastes everything |

```yaml
# workflow-defaults.yaml
retryStrategy:
  limit: "3"
  retryPolicy: "OnTransientError"        # ⚠️ NOT "Always"
  backoff:
    duration: "1m"
    factor: "2"
    maxDuration: "30m"
  expression: >-                          # classify before retrying
    lastRetry.exitCode != 1 && lastRetry.exitCode != 2
activeDeadlineSeconds: 86400              # ⚠️ every workflow needs a ceiling
podGCStrategy: OnWorkflowSuccess
ttlStrategy:
  secondsAfterCompletion: 604800          # 7 days of history
  secondsAfterFailure: 2592000            # 30 days — you need failures longer
```

> 🚫 **`retryPolicy: Always` on a GPU-heavy step is a capacity incident waiting to happen.** A 64-GPU training step that fails on a Python `NameError` will retry three times, consuming 3× the GPU-hours to produce the same error. Classify first.

> 💡 **Retention asymmetry: keep failures longer than successes.** Nobody debugs a workflow that worked. Thirty days of failed-workflow history is the difference between "we fixed it" and "it happened again and we still don't know why."

---

### Task 3 — Resource governance for workflows

A workflow submits jobs on a user's behalf — so it must inherit, not escape, their constraints.

| Control | Rule |
|---|---|
| Queue | Every step inherits the submitting user's LocalQueue |
| Priority | Steps cannot exceed the workflow's declared priority |
| Total budget | ⚠️ Declare a **GPU-hour budget per workflow**; halt when exceeded |
| Parallelism | `parallelism:` capped per workflow, and per namespace |
| Deadline | `activeDeadlineSeconds` mandatory |
| Identity | ⚠️ Steps run as the submitting user's ServiceAccount, never a privileged workflow SA |

> 🔒 **The identity rule matters for security.** A workflow controller with a powerful ServiceAccount that runs arbitrary user-provided steps is a privilege-escalation path: a user writes a step that reads secrets from another namespace. Steps must run with the submitting user's identity and the tenant's RBAC. Add this to the Phase 04 threat model as an explicit control.

**GPU-hour budget enforcement:**
```yaml
metadata:
  annotations:
    nexus.io/workflow-gpu-hour-budget: "500"
# A controller tracks consumption across all steps and suspends the workflow at the cap
# with a clear message. This prevents a runaway pipeline from consuming a tenant's month.
```

---

### Task 4 — The reference pipeline

**`templates/pipelines/train-eval-deploy/`** — must actually run, not just be documentation.

```
┌──────────────┐
│ prepare-data │  spark-step: reshard raw → WebDataset, register in catalog
└──────┬───────┘
       │ dataset-uri
┌──────▼───────┐
│    train     │  train-step: 8 GPUs, FSDP, checkpoint sync on
└──────┬───────┘
       │ checkpoint-uri
┌──────▼───────┐
│   evaluate   │  eval-step: run the eval suite; emit metrics
└──────┬───────┘
       │ metrics
┌──────▼───────┐
│  gate check  │  ⚠️ conditional: proceed ONLY if metrics beat the current model
└──────┬───────┘
       │ (when: accuracy > baseline)
┌──────▼───────┐
│   register   │  push to the model registry (Phase 44)
└──────┬───────┘
┌──────▼───────┐
│    deploy    │  deploy-step: canary 5% → monitor SLO → 100%
└──────────────┘
```

> 💡 **The conditional gate is the step that makes this a pipeline rather than a script.** A pipeline that deploys whatever it trained is dangerous; one that deploys only when the evaluation beats the incumbent is an actual MLOps workflow. Make the gate a first-class template so every team gets it.

---

### Task 5 — 📊 B12: the end-to-end benchmark

**`tools/workflows/bench-e2e.sh`** — the number that describes the platform as a whole.

| Stage | Duration | GPU-hours | Notes |
|---|---|---|---|
| Data prep (Spark, 40 CPU nodes) | | 0 | |
| Queue wait before training | | 0 | Phase 30's admission latency |
| Training (8 GPUs) | | | |
| Evaluation | | | |
| Registration | | 0 | |
| Deployment + canary | | | |
| **Total wall time** | | | |
| **Total GPU-hours** | | | |

📊 **Run it three ways** to expose where time actually goes:
```
1. Uncontended cluster                    → the floor
2. Loaded cluster (60 % utilized)         → the realistic case
3. With an induced node failure at step 3 → the resilience case
```

📊 **The resilience run is the most valuable.** It answers: "if a node dies mid-pipeline, how much do we lose?" With Phase 33's checkpointing and this phase's retry classification, the answer should be minutes, not the whole pipeline.

---

### Task 6 — 🚪 GATE G6 — Distributed Compute

**`gates/G6-distributed-compute.md`**

| # | Check | Evidence | Pass |
|---|---|---|---|
| G6.1 | 📊 **B5: NCCL busbw ≥ 85 % at 8 nodes** | Phase 22 | ☐ |
| G6.2 | 📊 **B9: training scaling meets §8.2 at every scale** | Phase 37 | ☐ |
| G6.3 | 📊 B9 covers ≥ 2 model classes | Phase 37 | ☐ |
| G6.4 | 📊 Exposed communication measured, not just total | Phase 37 | ☐ |
| G6.5 | Ray runs multi-node with correct defaults | Phase 36 | ☐ |
| G6.6 | 📊 Ray Train overhead vs. native DDP ≤ 5 % | Phase 36 | ☐ |
| G6.7 | Spark/Dask run on the CPU pool with tuned shuffle | Phase 38 | ☐ |
| G6.8 | 🧪 Shuffle impact on training measured and mitigated | Phase 38 | ☐ |
| G6.9 | 📊 MPI: OSU latency gap vs. raw RDMA < 2 µs | Phase 39 | ☐ |
| G6.10 | 📊 HPL ≥ 75 % of CPU peak | Phase 39 | ☐ |
| G6.11 | 📊 Jitter < 1 % on the HPC pool | Phase 39 | ☐ |
| G6.12 | 📊 **B10: inference SLOs met; latency-throughput curve recorded** | Phase 40 | ☐ |
| G6.13 | Multi-node TP works (or is documented as avoided via quantization) | Phase 40 | ☐ |
| G6.14 | No framework bypasses Kueue quota | Audit each | ☐ |
| G6.15 | Every framework's traffic uses the correct fabric path (RDMA where applicable) | Verify transports | ☐ |
| G6.16 | All framework alerts fire correctly | Test | ☐ |

---

### Task 7 — 🚪 GATE G9 — End-to-End Workload

**`gates/G9-end-to-end.md`** — the gate that tests the platform as a user experiences it.

| # | Check | Evidence | Pass |
|---|---|---|---|
| G9.1 | 📊 **B12: the reference pipeline runs end to end** | `bench-e2e.sh` | ☐ |
| G9.2 | 📊 B12 recorded on an uncontended AND a loaded cluster | Both runs | ☐ |
| G9.3 | 🧪 **B12 survives an induced node failure mid-pipeline** | Resilience run | ☐ |
| G9.4 | 🧪 Pipeline resumes from checkpoint after that failure | Same run | ☐ |
| G9.5 | Application errors are NOT retried | 🧪 Inject a code error | ☐ |
| G9.6 | Transient failures ARE retried with backoff | 🧪 Inject | ☐ |
| G9.7 | The conditional gate blocks deployment on a worse model | 🧪 Test | ☐ |
| G9.8 | Steps run as the submitting user, not a privileged SA | Inspect | ☐ |
| G9.9 | GPU-hour budget halts a runaway workflow | 🧪 Test | ☐ |
| G9.10 | Every step's GPU-hours attribute to the right tenant | Phase 34 query | ☐ |
| G9.11 | A user can go from zero to a running pipeline using only the docs | 🧪 **Ask a real user** | ☐ |
| G9.12 | Pipeline artifacts are URIs, not copied data | Inspect | ☐ |
| G9.13 | Failed workflow history retained ≥ 30 days | Config | ☐ |
| G9.14 | Argo UI reachable behind SSO | Browser | ☐ |

> 🧪 **G9.11 is the check nobody wants to run and everybody needs.** Hand the documentation to someone who did not build the platform and watch them try to run a pipeline. Every place they get stuck is a documentation defect that would otherwise be discovered by fifty users individually.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | Argo Workflows healthy; a hello-world workflow runs | Submit | Runs |
| **A2** | All seven ClusterWorkflowTemplates deploy and run | Test each | Work |
| **A3** | Templates inject the framework defaults from their phases | Inspect generated pods | Present |
| **A4** | **Steps pass URIs, not data** | Inspect artifacts | URIs |
| **A5** | **`retryPolicy` is not `Always`** | Read the defaults | Classified |
| **A6** | 🧪 An application error fails immediately without retry | Inject | No retry |
| **A7** | 🧪 A node failure retries with backoff | Inject | Retries |
| **A8** | 🧪 A preempted step resumes from checkpoint | Preempt | Resumes |
| **A9** | OOM retries once at a larger size, then fails | Test | Correct |
| **A10** | `activeDeadlineSeconds` mandatory | Submit without | Rejected |
| **A11** | Workflow parallelism capped | Submit a wide fan-out | Capped |
| **A12** | **GPU-hour budget suspends a runaway workflow** | 🧪 Test | Suspended |
| **A13** | 🔒 **Steps run with the submitting user's ServiceAccount** | Inspect | User's SA |
| **A14** | 🔒 A step cannot read another tenant's secrets | 🧪 Try | Denied |
| **A15** | Steps inherit the user's Kueue queue and priority | Inspect workloads | Inherited |
| **A16** | The reference pipeline runs unmodified | Run it | Runs |
| **A17** | The conditional gate blocks a worse model | 🧪 Test | Blocked |
| **A18** | 📊 **B12 recorded in all three modes** | `bench-e2e.sh` | Complete |
| **A19** | Failed workflows retained 30 days; successes 7 | Config | Set |
| **A20** | Argo UI behind SSO; users see only their namespace | Test | Isolated |
| **A21** | Argo Events can trigger a pipeline (e.g. new dataset in S3) | Test | Triggers |
| **A22** | 🚪 **Gate G6 passes** | `gates/G6-distributed-compute.md` | All ☑ |
| **A23** | 🚪 **Gate G9 passes** | `gates/G9-end-to-end.md` | All ☑ |

---

## ↩️ ROLLBACK

```bash
# Stop new workflows; running ones finish
kubectl patch cm workflow-controller-configmap -n argo \
  --type merge -p '{"data":{"parallelism":"0"}}'

# Suspend a specific runaway workflow
argo suspend <workflow> -n <ns>

helm uninstall argo-workflows -n argo
# ⚠️ Running workflows' pods are orphaned — they keep running but nothing tracks them.
# Terminate workflows first.
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| A failing step retries and burns GPU-hours | `retryPolicy: Always` | A5 — classify failures |
| Workflow consumes a tenant's whole quota | No parallelism cap or budget | A11/A12 |
| Steps hang waiting for resources | Kueue quota exhausted — correct | Check `describe workload` |
| Huge data copied between steps | Artifacts instead of URIs | A4 |
| Step succeeds but the workflow reports failure | Wrong `successCondition` for the CR | Fix the resource template's conditions |
| Workflow can access another tenant's data | Privileged workflow SA | A13 — security defect, fix immediately |
| Argo controller OOM at scale | Many large workflows in etcd | Enable workflow archiving; tighten TTL |
| History disappears | TTL too aggressive | A19 |
| Deploy step deploys a worse model | Conditional gate missing or wrong | A17 |
| Node failure loses the whole pipeline | Checkpointing not wired into the step | Phase 33's contract; G9.4 |

---

## 🚫 DO NOT

- **Do not** use `retryPolicy: Always` on any step that consumes GPUs.
- **Do not** pass datasets as Argo artifacts.
- **Do not** run workflow steps with a privileged ServiceAccount.
- **Do not** allow workflows without a deadline or a budget.
- **Do not** deploy a model without an evaluation gate.
- **Do not** delete failed workflow history early.
- **Do not** pass G6 or G9 with an unmet check and no dated finding.
- **Do not** skip G9.11 — the real-user documentation test.

---

## 📤 HANDOFF

`evidence/phase-41/handoff.md` must state:

1. **🚪 The G6 and G9 gate results**, with links to every benchmark from Phases 36–40.
2. **📊 B12** in all three modes — uncontended, loaded, and with an induced failure. **This is the platform's end-to-end story.**
3. **The retry classification** as implemented, and what it saved during testing.
4. **The template set** and the defaults each injects.
5. **🧪 The G9.11 result** — what a real user got stuck on, and what was fixed.
6. **The GPU-hour budget mechanism** and its interaction with Phase 34's accounting.
7. **🔒 The workflow identity model**, added to the Phase 04 threat model.
8. **Stage 6 declaration** — the cluster now runs distributed training, general distributed Python, large-scale data processing, tightly-coupled HPC, production inference, and multi-step pipelines, all through one scheduler, one quota system, and one accounting model. Stage 7 (platform experience) may begin.

---

## ➡️ NEXT

**[PHASE-42 — Container Registry & Build Farm](PHASE-42.md)** — begin Stage 7. Everything above runs in containers; make building and distributing them fast, reproducible, and secure.
