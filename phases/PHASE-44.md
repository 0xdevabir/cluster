# PHASE 44 — Experiment Tracking & Model Registry

| | |
|---|---|
| **Stage** | 7 — Platform Experience |
| **Estimated effort** | 3–4 hours |
| **Depends on** | 26, 28, 37, 40, 41, 42 |
| **Blocks** | 46, 47 |
| **Risk** | 🟢 Low-Medium — the risk is a tracking store that becomes a single point of research history loss |
| **Blast radius** | Research reproducibility; model deployment provenance |
| **Architecture refs** | `ARCHITECTURE.md#l9-platform-services`, Phase 29's state inventory (S16), Phase 41's pipeline gate |

---

## 🎯 MISSION

Make research **reproducible** and models **traceable from data to deployment**. Deploy an experiment tracking server and a model registry, wire them automatically into every training job, and connect the registry to Phase 40's serving so that what runs in production is always a registered, versioned, evaluated artifact with a known lineage.

> 💡 **WHY the platform should own this rather than each team.** Every research group will otherwise run its own MLflow in a pod on a `hostPath` volume, and six months later nobody can reproduce the model that is in production. Centralizing tracking gives: one place to compare runs across teams, automatic capture (so nobody forgets to log), backed-up history (Phase 29's S16), and — the part that matters most — **a hard link from a deployed model back to the exact code, data, hyperparameters, and hardware that produced it.**

> ⚠️ **The lineage question is the one that gets asked at the worst time.** "This model is behaving strangely in production — what data was it trained on?" If the answer requires archaeology through Slack, the platform has failed. Every deployed model must answer that question in one query.

---

## ✅ PREFLIGHT

```bash
# Postgres on T1 for metadata; object storage for artifacts
kubectl get sc nexus-fast
s5cmd ls s3://nexus-artifacts-*/

# Training and serving both operational
kubectl get clustertrainingruntimes
kubectl get inferenceservices -A

# Dataset catalog from Phase 28 (lineage links to it)
cat storage/catalog/datasets.yaml 2>/dev/null | head

# Backup coverage exists for S16 (Phase 29)
grep -A2 "S16" backup/state-inventory.md
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/platform/mlops/
  mlflow-values.yaml                # tracking + registry
  postgres-cluster.yaml             # CNPG on T1
  artifact-store-config.yaml        # S3-backed
  model-registry-webhooks.yaml      # registry → deployment
policies/defaults/
  inject-tracking-env.yaml          # ⚠️ automatic capture — the key deliverable
tools/mlops/
  register-model.sh
  lineage.sh                        # 🎯 "where did this model come from?"
  promote-model.sh                  # staging → production, with gates
  cleanup-runs.sh                   # retention
docs/user/
  experiment-tracking-guide.md
  model-lifecycle.md
observability/rules/mlops-alerts.yaml
evidence/phase-44/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Note |
|---|---|---|
| MLflow | `2.20.x` | Tracking + Model Registry in one |
| CloudNativePG | Phase 17 pin | Postgres operator |
| PostgreSQL | `16.x` | |

> 💡 **MLflow over Weights & Biases or Neptune** for a private cluster: it is open source, self-hostable with no external data egress (important for a private platform), and combines tracking and registry. If a team already depends on W&B, run its self-hosted variant alongside rather than forcing a migration — but keep the *registry* central, because that is what production depends on.

---

## 📋 TASKS

### Task 1 — Deployment and the state that must not be lost

```yaml
# The two stores, with different requirements
backendStore:                       # runs, params, metrics, model metadata
  postgres:
    storageClass: nexus-fast        # T1 — small, transactional, latency-sensitive
    instances: 3                    # CNPG HA
    backup:
      barmanObjectStore:            # ⚠️ continuous backup to S3 (Phase 29 S16)
        destinationPath: s3://nexus-backups/mlflow-pg/
artifactStore:                      # checkpoints, plots, SBOMs, eval outputs
  s3: s3://nexus-artifacts/mlflow/  # T3 — large, immutable
```

> ⚠️ **The Postgres store holds the entire research history of the organization.** It is small (gigabytes) and irreplaceable. It is Phase 29's S16 with a 6-hour RPO — verify that the restore has actually been tested, not just configured. A tracking database that has never been restored is the same hypothesis problem as any other backup.

> 🚫 **Do not put artifacts in Postgres.** MLflow will happily store a 40 GB checkpoint as a blob if the artifact store is misconfigured, and the database becomes unbackupable. Artifacts go to S3; the database holds only pointers.

---

### Task 2 — 🎯 Automatic capture (the deliverable that makes this work)

Users forget to log. The platform must not depend on them remembering.

**`inject-tracking-env.yaml`** — a Kyverno mutation on every TrainJob/RayJob/workflow step:

```yaml
env:
  - MLFLOW_TRACKING_URI:       https://mlflow.nexus.internal
  - MLFLOW_EXPERIMENT_NAME:    "{{ tenant }}/{{ job-name }}"
  - MLFLOW_RUN_ID:             "{{ generated }}"
  - MLFLOW_TRACKING_TOKEN:     "{{ from the user's OIDC identity }}"
  # ⚠️ Auto-captured platform context — the lineage backbone:
  - NEXUS_JOB_ID:              "{{ job uid }}"
  - NEXUS_IMAGE_DIGEST:        "{{ resolved image digest, not the tag }}"
  - NEXUS_GIT_COMMIT:          "{{ from the build, Phase 42 }}"
  - NEXUS_DATASET_URI:         "{{ from the workflow input }}"
  - NEXUS_GPU_MODEL:           "{{ node label }}"
  - NEXUS_NODE_COUNT:          "{{ replicas }}"
  - NEXUS_TENANT:              "{{ tenant }}"
  - NEXUS_USER:                "{{ submitter }}"
```

Plus **MLflow autologging** enabled by default in the curated images:
```python
# In the base image's sitecustomize.py — active unless the user opts out
import mlflow
mlflow.autolog()          # captures params, metrics, and the model for PyTorch/sklearn/etc.
```

> 💡 **`NEXUS_IMAGE_DIGEST`, not the image tag, is the field that makes reproducibility real.** A tag can be overwritten (which is why Phase 42 made `prod-*` immutable); a digest cannot. Recording the digest means "rerun this experiment" resolves to *exactly* the same bytes of code and dependencies.

**What gets captured without the user doing anything:**

| Captured | Source |
|---|---|
| Hyperparameters | Autolog / user code |
| Metrics per step | Autolog |
| Model artifact | Autolog / checkpoint sync (Phase 33) |
| **Code version** | Git commit + image digest |
| **Data version** | Dataset URI + catalog entry (Phase 28) |
| **Hardware** | GPU model, node count, topology placement |
| **Cost** | GPU-hours consumed (Phase 34) |
| Environment | pip freeze / conda export from the image |

---

### Task 3 — 🎯 The lineage query

**`tools/mlops/lineage.sh`** — the tool that answers the question from the Mission.

```
$ nexus lineage model://fraud-detector/v7

MODEL       fraud-detector v7        registered 2026-08-12, stage: Production
├─ RUN      mlflow://run/8a3f2c...   accuracy 0.941, f1 0.918
├─ CODE     git@internal:ml/fraud@a91f3e2
│           image registry.nexus.internal/ml/fraud:train@sha256:4c9b...
├─ DATA     s3://nexus-datasets/transactions/v2.3/   (catalog: 4.2 TB, 1024 shards)
│           license: Internal only · imported 2026-07-01 by <user>
├─ COMPUTE  8× RTX 4090 · 4 nodes · leaf-01 · 18.4 GPU-hours · $11.41
├─ PIPELINE argo://workflow/train-eval-deploy-8823
├─ EVAL     eval-suite v3 · passed the deployment gate (baseline 0.928)
└─ SERVING  isvc/fraud-detector  · deployed 2026-08-13 · 3 replicas · currently live
```

> 💡 **Build this as a single command and put it in the portal (Phase 46).** The value is not the data — MLflow already has most of it — it is that one query traverses the whole chain without anyone knowing which system holds which piece. That is what makes it get used during an incident.

**Bidirectional linking:** Phase 40's InferenceService must record its model version, and the registry must record where each version is deployed. Without the reverse link, "which model is serving traffic right now?" requires guesswork.

---

### Task 4 — Model lifecycle and promotion gates

```
None → Staging → Production → Archived
```

**`tools/mlops/promote-model.sh`** — promotion is gated, not a button.

| Transition | Gates |
|---|---|
| → Staging | Model registered with metrics; artifact present and checksummed |
| **→ Production** | ⚠️ **All of:** eval score ≥ the current production model; eval suite version recorded; model card present; approved by a named human; source image signed (Phase 42) |
| → Archived | Not serving traffic; retention period elapsed |

> ⚠️ **The "≥ current production" gate is the same conditional gate from Phase 41's reference pipeline**, and it is the single most valuable control in the model lifecycle. It prevents the most common MLOps failure: deploying a model that is worse than the one it replaced because someone was in a hurry.

**The model card** — required for production, minimal enough that people fill it in:
```markdown
# fraud-detector v7
Purpose:        <what it does, what it is NOT for>
Training data:  <URI + a sentence on composition, and known gaps>
Metrics:        <the eval numbers, and on what suite>
Limitations:    <where it is known to fail>
Owner:          <a person, not a team alias>
Review date:    <when this must be re-evaluated>
```

> 💡 **Keep the model card short enough to be real.** A 12-section template goes unfilled or gets copy-pasted. Six fields that someone actually thinks about beat a comprehensive form that gets `TODO`-ed.

---

### Task 5 — Retention (research history is not free)

| Data | Retention | Rationale |
|---|---|---|
| Run metadata (params, metrics) | **Forever** | Tiny; irreplaceable; it is the research record |
| Run artifacts (checkpoints) | 90 days, unless the run produced a registered model | Large; most are dead ends |
| **Registered model artifacts** | **Forever while in Staging/Production; 2 years after Archived** | Production provenance |
| Failed/deleted runs | 30 days | |
| Autolog plots, small artifacts | 1 year | |

> ⚠️ **Never garbage-collect an artifact referenced by a registered model.** `cleanup-runs.sh` must check the registry before deleting anything — the same principle as Phase 42's registry GC. A production model whose weights were cleaned up is unrecoverable and undetectable until it needs to be redeployed.

📊 **Track storage growth** and report it — a tracking server that quietly accumulates 40 TB of dead-end checkpoints becomes a capacity problem Phase 56 has to solve.

---

### Task 6 — Alerts

| Alert | Threshold |
|---|---|
| `MLflowDown` | Tracking server unavailable |
| `MLflowDBDown` | Postgres unavailable — **runs are being lost** |
| `MLflowDBBackupStale` | No successful backup in 12 h |
| `MLflowArtifactStorageHigh` | > 80 % of quota |
| `ModelPromotedWithoutGate` | 🔴 A production promotion bypassed the checks |
| `ProductionModelUnregistered` | An InferenceService serving a model not in the registry |
| `ModelCardMissing` | A production model without a card |
| `ModelReviewOverdue` | Past its review date |
| `RunArtifactGCBlocked` | GC skipped a referenced artifact (informational, confirms the guard works) |

> ⚠️ **`ProductionModelUnregistered` is the drift detector.** It catches the case where someone deployed a model by hand, bypassing the registry — which breaks lineage for everything downstream. It should be rare and always investigated.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | MLflow healthy; UI reachable behind SSO | Browser | Works |
| **A2** | Postgres HA (3 instances) on T1 | `kubectl get cluster` | Healthy |
| **A3** | Continuous backup to S3 configured | Check backup status | Running |
| **A4** | 🧪 **Postgres restore tested (Phase 29 S16)** | Restore to a temp instance, verify a known run | Verified |
| **A5** | Artifacts go to S3, not the database | Check DB size after a large artifact | Small |
| **A6** | **Tracking env injected automatically into every training job** | Submit without config | Injected |
| **A7** | Autologging captures params and metrics with no user code | Run a bare training script | Captured |
| **A8** | **`NEXUS_IMAGE_DIGEST` recorded, not just the tag** | Inspect a run | Digest |
| **A9** | Git commit recorded | Inspect | Recorded |
| **A10** | Dataset URI recorded and links to the catalog | Inspect | Linked |
| **A11** | Hardware context (GPU model, node count) recorded | Inspect | Recorded |
| **A12** | GPU-hours/cost from Phase 34 attached to the run | Inspect | Attached |
| **A13** | 🎯 **`lineage.sh` returns the full chain for a deployed model** | Run it | Complete chain |
| **A14** | The reverse link works: registry knows where a version is deployed | Query | Known |
| **A15** | Promotion to Production requires all gates | 🧪 Try to promote a worse model | Blocked |
| **A16** | Promotion requires a named human approver | 🧪 Try automated | Blocked |
| **A17** | Promotion requires a signed source image | 🧪 Unsigned | Blocked |
| **A18** | Model card required for production | 🧪 Without | Blocked |
| **A19** | Phase 41's pipeline registers and promotes through this path | Run the reference pipeline | Registered |
| **A20** | Phase 40's serving pulls only registered models | 🧪 Deploy an unregistered model | Alerted/blocked |
| **A21** | 🧪 **Artifact GC never deletes a registered model's artifact** | Test | Never |
| **A22** | Retention policy runs; dead-end run artifacts expire | Verify | Expire |
| **A23** | Tenants see only their own experiments | Cross-tenant test | Isolated |
| **A24** | 📊 Storage growth tracked and reported | Dashboard | Tracked |
| **A25** | All alerts fire | Induce | Fire |

---

## ↩️ ROLLBACK

```bash
# Tracking is non-blocking by design — if MLflow is down, training must still run.
# 🧪 VERIFY THIS: scale MLflow to zero and submit a training job.
kubectl scale deploy/mlflow -n mlops --replicas=0
# Expected: the job runs, logging fails gracefully with a warning, training completes.

helm uninstall mlflow -n mlops
# ⚠️ Do NOT delete the Postgres cluster or the S3 artifacts — that is the research record.
```

> ⚠️ **Tracking must never be on the critical path of a training job.** If MLflow being down causes training jobs to crash, one platform component's outage becomes a cluster-wide research outage. Make the client fail open with a warning, and **test it** (it is in the rollback for exactly this reason).

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Training job crashes when MLflow is down | Client failing closed | **Fix immediately** — see the rollback note |
| Database growing very fast | Artifacts stored in Postgres | A5 |
| Runs missing for some jobs | Injection not covering that workload type | Extend the mutation |
| Lineage incomplete | A field not injected (usually dataset URI from a manual submission) | Add it; consider requiring it |
| Model in production not in the registry | Manual deployment bypassed the flow | `ProductionModelUnregistered` — investigate |
| Cannot reproduce an old run | Image tag was mutable, or the dataset was deleted | A8 (digest) + Phase 28 versioning |
| Promotion blocked incorrectly | Eval suite version mismatch | Check which baseline it compared against |
| Artifact storage quota hit | Retention not running, or too many dead-end checkpoints | A22; tighten checkpoint retention in Phase 33 |
| Users run their own MLflow anyway | The central one is missing something they need | Ask them — this is a product problem, not a policy one |
| Postgres failover loses recent runs | Synchronous replication not configured | Check CNPG settings |

---

## 🚫 DO NOT

- **Do not** let tracking failures break training jobs.
- **Do not** store artifacts in the metadata database.
- **Do not** record image tags without digests.
- **Do not** allow promotion to Production without the evaluation gate.
- **Do not** garbage-collect artifacts referenced by registered models.
- **Do not** deploy a model to production that is not in the registry.
- **Do not** delete run metadata — it is small and irreplaceable.
- **Do not** build the developer portal here. Phase 46.

---

## 📤 HANDOFF

`evidence/phase-44/handoff.md` must state:

1. **🎯 An example `lineage.sh` output** for a real deployed model — the proof the chain is complete.
2. **What is captured automatically** and what still depends on the user.
3. **🧪 The fail-open test result** — training continues when tracking is down.
4. **🧪 The Postgres restore verification** (Phase 29 S16 closure).
5. **The promotion gates in force** and any override path.
6. **Retention policy** and current/projected storage.
7. **Any workload type not covered by automatic injection** — the lineage gaps.
8. **Whether teams are using the central instance** or running their own, and why.

---

## ➡️ NEXT

**[PHASE-45 — Full Observability Stack](PHASE-45.md)** — replace the bootstrap monitoring from Phase 11 with the complete metrics, logs, traces, and profiles platform.
