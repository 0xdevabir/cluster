# PHASE 43 — Notebooks & Interactive Development

| | |
|---|---|
| **Stage** | 7 — Platform Experience |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 17, 19, 27, 34, 42 |
| **Blocks** | 46, 47 |
| **Risk** | 🟡 Medium — the largest source of idle GPU allocation in every research cluster |
| **Blast radius** | Interactive user experience; GPU utilization |
| **Architecture refs** | `ARCHITECTURE.md#l9-platform-services`, `ULTIMATE-PLAN.md#10-multi-tenancy`, Phase 34's waterfall |

---

## 🎯 MISSION

Give researchers a **fast, comfortable interactive environment** — JupyterLab, VS Code in the browser, or a remote SSH target — with GPUs, the shared filesystem, and the dataset catalog all present. Then solve the problem interactive computing always creates: **idle GPUs held by forgotten sessions**, without making users feel policed.

> 💡 **WHY notebooks deserve a full phase.** For most researchers, the notebook *is* the cluster. It is where they explore data, debug a training script, and decide what to run at scale. If it takes four minutes to start, loses state on disconnect, or cannot see their data, they will run on their laptop instead — and every subsequent phase's careful work goes unused. **A good interactive experience is what makes the batch system get used.**

> ⚠️ **And yet: Phase 34's waterfall almost certainly named "allocated but idle" as the largest addressable loss bucket, and notebooks are almost certainly its largest contributor.** A researcher opens a notebook with a GPU on Monday, goes to a conference, and returns Friday. That is 96 GPU-hours for maybe two hours of work. The fix is not a stricter policy; it is **fractional GPUs, fast restarts, and preserved state**, so that giving up an idle GPU costs the user nothing.

---

## ✅ PREFLIGHT

```bash
# Home directories on CephFS (Phase 27)
kubectl get sc nexus-home

# Fractional GPU DeviceClasses (Phase 19) — the key to cheap interactive GPUs
kubectl get deviceclasses | grep mps

# 📊 Idle allocation data from Phase 34 — sizes the problem
bash tools/accounting/waterfall.sh --last-month | grep -i idle

# Identity and ingress
kubectl get gateway,securitypolicy -A

# Curated base images (Phase 42) — notebooks start from these
crane ls registry.nexus.internal/nexus/base
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/platform/notebooks/
  jupyterhub-values.yaml            # or Kubeflow Notebooks
  profiles.yaml                     # 🎯 the sizes users choose from
  culling-policy.yaml               # ⚠️ suspend, don't destroy
  code-server-template.yaml         # VS Code in the browser
  ssh-gateway.yaml                  # remote dev from a local IDE
images/notebooks/
  Dockerfile.base                   # FROM the Phase 42 curated images
  Dockerfile.pytorch
  Dockerfile.rapids
tools/notebooks/
  notebook-report.sh                # who has what, and is it idle?
  bench-notebook.sh                 # 📊 startup time
docs/user/
  notebooks-guide.md
  interactive-development.md        # 🎯 including "when to stop using a notebook"
observability/rules/notebook-alerts.yaml
evidence/phase-43/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version |
|---|---|
| JupyterHub (Zero to JupyterHub) | `4.1.0` |
| JupyterLab | `4.3.x` |
| code-server | `4.96.x` |
| jupyter-server-proxy | pin |

---

## 📋 TASKS

### Task 1 — 🎯 Profiles: make the cheap option the obvious one

The profile list is the single highest-leverage design decision in this phase.

| Profile | Resources | GPU | Accounted rate | Default? |
|---|---|---|---|---|
| **CPU only** | 4 CPU, 16 Gi | None | ~free | ✅ **Default** |
| **Small GPU (shared)** | 8 CPU, 32 Gi | **MPS quarter, 6 GB VRAM** | 0.25 GPU-hr | ✅ Recommended for dev |
| Standard GPU | 16 CPU, 64 Gi | 1 full GPU | 1.0 GPU-hr | Requires a reason |
| Large GPU | 32 CPU, 128 Gi | 2 GPUs | 2.0 GPU-hr | Approval |
| High memory | 16 CPU, 256 Gi | None | | Data exploration |

> 💡 **The MPS-quarter profile is the centrepiece.** Phase 19 built fractional GPUs with hard VRAM budgets; this is where they earn their keep. Most interactive work — writing code, testing a forward pass, plotting, debugging a data loader — needs a GPU to be *present*, not to be *fast*. A quarter GPU serves that at a quarter the cost, and four researchers share one card instead of holding four.

> ⚠️ **Order the profile list with CPU-only first and the full GPU below the fold.** Users pick the first plausible option. Defaults are policy.

**The message next to each profile matters too:**
```
Small GPU (shared)     6 GB VRAM · fine for development and debugging · 0.25 GPU-hour
Standard GPU (full)    24 GB VRAM · use only when you need the full card · 1.0 GPU-hour
                       ⚠️ If you're training for more than an hour, submit a batch
                          job instead — it's cheaper and it won't be idle overnight.
```

---

### Task 2 — Startup time (the number that decides adoption)

A notebook that takes four minutes to start will not be used interactively.

```
Startup budget:
  Kueue admission                     < 5 s    (Phase 30)
  Pod scheduling                      < 3 s
  Image pull (cached — Phase 42)      < 10 s   ⚠️ uncached: minutes
  Home directory mount (CephFS)       < 5 s
  JupyterLab start                    < 10 s
  ──────────────────────────────────────────
  TARGET TOTAL                        < 35 s
```

**The mitigations:**

| Technique | Effect |
|---|---|
| **Pre-warmed notebook images on interactive nodes** (Phase 42) | Removes the pull |
| A dedicated interactive node pool | No queue wait behind batch jobs |
| **Small reserved quota for interactive work** | A notebook never queues behind a 64-GPU training job |
| Warm pod pool (a few idle pods ready to claim) | Sub-10 s, at the cost of some standing capacity |

> ⚠️ **Reserve a small interactive quota in Kueue.** If notebooks compete with batch training in one queue, a busy cluster means a four-hour wait to open a notebook — and users go elsewhere. A dedicated ClusterQueue with a small nominal quota and high lending limit gives interactive work fast access without stranding capacity: it lends everything it is not using.

---

### Task 3 — ⚠️ Culling: suspend, do not destroy

This is where trust is won or lost.

| Time idle | Action | User experience |
|---|---|---|
| 30 min | **Notify** — "Your notebook has been idle 30 min. It will be suspended at 60 min. [Keep alive]" | A warning, with an out |
| 60 min | **Suspend**: stop the pod, **release the GPU**, keep home directory and (where possible) kernel state | "Your notebook is paused. Click to resume." |
| Resume | Restart the pod, remount home, restore | < 30 s |
| 7 days suspended | Notify about cleanup | |
| 30 days suspended | Delete the pod spec; **home directory is never deleted** | Recoverable |

> 🚫 **Never delete a user's work as part of culling.** The home directory on CephFS survives everything. Culling releases *compute*, not *data*. If a user loses a file to an automated policy, the platform loses their trust permanently and they go back to their laptop — which costs far more capacity than the GPU you reclaimed.

> 💡 **"Idle" must be defined carefully.** A notebook running a 3-hour training cell is not idle even though nobody has typed. Use:
> ```
> idle = no kernel execution AND no terminal activity AND no HTTP requests
>        AND SM_ACTIVE < 5 %      ← the GPU check is what prevents false positives
> ```
> All four conditions, not any one. Phase 34's `idle-reclaim.sh` warning applies: killing a legitimate long-running cell destroys trust.

**Culling exemptions:** allow a user to mark a notebook `nexus.io/no-cull: "true"` with a required reason and an expiry (max 7 days). Visible in the Phase 34 report so it is accounted, not hidden.

---

### Task 4 — What a notebook has access to

The environment must feel complete or users will fight it.

| Mount / integration | Path | Backing |
|---|---|---|
| Home directory | `/home/jovyan` | CephFS `nexus-home`, quota'd, snapshotted |
| Shared team space | `/shared/<tenant>` | CephFS |
| Datasets (read-only) | `/datasets` | T4 cache over T3 (Phase 28) |
| Scratch | `/scratch` | T0 local NVMe (Phase 25) |
| Object storage | via `s5cmd`/`boto3` | Credentials from ESO, scoped to the tenant |
| Cluster access | `kubectl`, `nexus` CLI | The user's own RBAC, **not** a privileged SA |
| Git | pre-configured | With the user's identity |
| The frameworks | Ray, PyTorch, Spark clients | Version-matched to the cluster (Phase 36/38 pinning) |

> 🔒 **The notebook's ServiceAccount must be the user's, with the tenant's RBAC.** A notebook is arbitrary code execution by definition; if it carries a privileged token, every notebook is a cluster-admin shell. This is the same rule as Phase 41's workflow steps, and it belongs in the Phase 04 threat model.

> ⚠️ **Version-match the client libraries to the cluster.** A notebook with Ray 2.35 cannot connect to a 2.40 cluster (Phase 36's A12). Build notebook images `FROM` the Phase 42 curated bases so the versions cannot drift.

---

### Task 5 — Beyond notebooks: VS Code and SSH

Not everyone wants Jupyter. Support the two other common modes.

| Mode | Mechanism | For |
|---|---|---|
| **code-server** | VS Code in the browser, same pod shape as a notebook | Users who want an IDE, not cells |
| **SSH into a dev pod** | An SSH gateway; the user's local VS Code / PyCharm connects remotely | Users with a configured local environment |
| Jupyter | JupyterHub | Exploration, teaching, plots |

All three share: the same profiles, the same culling policy, the same mounts, the same accounting. **Differing only in the front end** keeps the operational surface small.

> 💡 **The SSH remote-development path is often the most productive** and the most overlooked. A researcher with a tuned local editor connecting to a cluster pod gets the best of both. Use the Phase 17 identity (SSH certificates from step-ca, or OIDC-brokered keys) — never long-lived authorized_keys managed by hand.

---

### Task 6 — 📊 Measure, and tell users when to stop

**`tools/notebooks/bench-notebook.sh`:**

| Metric | Target | Measured |
|---|---|---|
| Cold start (image cached) | < 35 s | |
| Cold start (image not cached) | Record | |
| Resume from suspend | < 30 s | |
| Home directory first-access latency | < 2 s | |
| Dataset read throughput from `/datasets` | Matches Phase 28's cache | |
| Notebook responsiveness under a loaded node | Subjective + p95 kernel round-trip | |

📊 **The utilization report that changes behavior** (`notebook-report.sh`, feeding Phase 34):
```
NOTEBOOK USAGE — research-vision, last 30 days
  Sessions:                  84
  GPU-hours allocated:      1,240
  GPU-hours SM_ACTIVE:        118    (9.5 %)   ⚠️
  Suspended by culling:        61    (saved ~890 GPU-hours)
  Longest-lived session:  9 days (user: ...)
  Recommendation: 71 % of GPU notebook sessions never exceeded 6 GB VRAM.
                  Those users would be equally served by the shared-GPU profile.
```

**`docs/user/interactive-development.md`** must include the section nobody writes:

```markdown
## When to stop using a notebook

Notebooks are for exploring. They are a bad place to run real training because:
 · Your session holds a GPU whether or not it's computing
 · A disconnect can kill a long-running cell
 · Nothing is checkpointed, so a node failure loses everything
 · You can't scale past one node

The moment your experiment works, move it:
    nexus notebook export train.ipynb --to trainjob
This converts the notebook into a TrainJob you can submit. Your job then:
 · gets checkpointed and survives preemption
 · scales to multiple nodes
 · runs overnight without you
 · costs less (preemptible rate)

Rule of thumb: if it runs longer than an hour, it belongs in a batch job.
```

> 💡 **`nexus notebook export` is worth building even in a crude form.** The barrier to moving from notebook to batch job is friction, not ignorance. Removing that friction converts idle interactive GPU-hours into productive batch GPU-hours — which is exactly what Phase 34's waterfall asked for.

---

### Task 7 — Alerts

| Alert | Threshold |
|---|---|
| `NotebookStartupSlow` | p95 start > 60 s |
| `NotebookHubDown` | JupyterHub unavailable |
| `NotebookIdleGPUHigh` | > 20 % of GPU notebook-hours at < 5 % SM_ACTIVE |
| `NotebookCullingDisabled` | Culling suspended or failing |
| `NotebookNoCullAbuse` | A no-cull exemption past its expiry |
| `NotebookHomeQuotaFull` | A user at their home quota |
| `NotebookLongLived` | A session running > 7 days |
| `NotebookImageDrift` | Notebook client versions differ from cluster versions |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | JupyterHub healthy; a user can log in via OIDC | Test | Works |
| **A2** | All profiles launch successfully | Test each | Work |
| **A3** | **CPU-only is the default and first-listed profile** | Open the spawner | First |
| **A4** | **MPS-quarter profile works with an enforced VRAM budget** | 🧪 Try to exceed 6 GB | Blocked |
| **A5** | 📊 **Cold start < 35 s with a cached image** | `bench-notebook.sh` | Met |
| **A6** | Interactive quota reserved; a notebook does not queue behind batch | 🧪 Load the cluster, start a notebook | Starts promptly |
| **A7** | Home directory persists across restarts | 🧪 Write, restart, read | Persists |
| **A8** | Home quota enforced | Exceed | Enforced |
| **A9** | `/datasets` readable at cache speed | 📊 Measure | Matches Phase 28 |
| **A10** | `/scratch` present and fast | Test | Works |
| **A11** | 🔒 **Notebook uses the user's ServiceAccount, not a privileged one** | `kubectl auth can-i` inside | User's RBAC |
| **A12** | 🔒 A notebook cannot access another tenant's data | 🧪 Try | Denied |
| **A13** | Client library versions match the cluster | `ray.__version__` etc. | Matched |
| **A14** | Idle notification sent at 30 min | 🧪 Idle test | Sent |
| **A15** | **Suspension at 60 min releases the GPU** | 🧪 Idle test | Released |
| **A16** | **Suspension does NOT delete home directory data** | 🧪 Verify after suspend | Intact |
| **A17** | 📊 Resume from suspend < 30 s | Measure | Met |
| **A18** | **A notebook running a long cell is NOT culled** | 🧪 Run a 90-min cell | Not culled |
| **A19** | The 4-condition idle definition is implemented | Read the code | All four |
| **A20** | No-cull exemption requires a reason and expires | Test | Enforced |
| **A21** | code-server profile works with the same mounts and policy | Test | Works |
| **A22** | SSH remote development works with OIDC-brokered credentials | Test | Works |
| **A23** | No long-lived static SSH keys | Audit | None |
| **A24** | 📊 Notebook utilization report generated | `notebook-report.sh` | Generated |
| **A25** | `nexus notebook export` produces a runnable TrainJob | 🧪 Export and submit | Runs |
| **A26** | All alerts fire | Induce | Fire |
| **A27** | The "when to stop using a notebook" guidance is published | Read | Published |

---

## ↩️ ROLLBACK

```bash
# Disable culling (⚠️ GPU utilization will fall) while keeping notebooks working
kubectl patch cm jupyterhub-config -n notebooks --type merge \
  -p '{"data":{"cull_idle_timeout":"0"}}'

# Stop new notebook launches; existing sessions continue
kubectl patch clusterqueue interactive-queue --type merge -p '{"spec":{"stopPolicy":"Hold"}}'

# ⚠️ Never delete the home-directory PVCs as part of a rollback.
```

> 💡 **Disabling culling is a legitimate temporary measure** if the idle detection is producing false positives. It costs capacity, not trust. Fix the detection, then re-enable — the same discipline as Phase 23's dry-run.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Notebook takes minutes to start | Image not cached on interactive nodes | Pre-warm (Phase 42) |
| Notebook queues for hours | No reserved interactive quota | A6 |
| A long-running cell was killed | Idle detection missing the GPU/kernel condition | A18/A19 — **fix immediately**, it destroys trust |
| User lost files | Culling deleted a PVC | 🔴 This must never happen — A16 |
| GPU OOM in the shared profile | MPS budget too small for the work | Move to a full GPU, or reduce batch size |
| Ray/Spark client cannot connect | Version drift | A13 — build from the curated base |
| `kubectl` in the notebook has too much access | Privileged SA | A11 — security defect |
| Notebook slow but GPU idle | CPU or I/O bound; or a noisy neighbor | Check node load; Phase 20's isolation |
| Suspended notebook will not resume | Home PVC unavailable, or the node pool changed | Check CephFS; check the profile still exists |
| Users bypass the platform, run locally | Startup too slow, or the environment is missing something | A5 + ask them — this is the real failure |

---

## 🚫 DO NOT

- **Do not** delete user data as part of culling.
- **Do not** cull a session with an actively executing kernel.
- **Do not** default the profile list to a full GPU.
- **Do not** give notebooks a privileged ServiceAccount.
- **Do not** let notebook client library versions drift from the cluster.
- **Do not** let notebooks compete with batch jobs in the same queue.
- **Do not** manage SSH access with hand-maintained `authorized_keys`.
- **Do not** ship culling without the 30-minute warning.

---

## 📤 HANDOFF

`evidence/phase-43/handoff.md` must state:

1. **📊 Startup and resume times** — the numbers that determine whether users adopt this.
2. **📊 The notebook utilization report** — GPU-hours allocated vs. SM_ACTIVE, and hours reclaimed by culling.
3. **The profile set** and how many users chose each — evidence for whether the fractional-GPU default is working.
4. **The idle definition** as implemented, and any false positives found.
5. **The interactive quota reserved** and its lending behavior.
6. **🔒 The notebook identity model**, added to the Phase 04 threat model.
7. **Reduction in Phase 34's "allocated but idle" bucket** attributable to this phase — the before/after.
8. **Which access modes are deployed** (Jupyter / code-server / SSH) and their relative use.

---

## ➡️ NEXT

**[PHASE-44 — Experiment Tracking & Model Registry](PHASE-44.md)** — make research reproducible and models traceable from data to deployment.
