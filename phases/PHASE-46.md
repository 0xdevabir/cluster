# PHASE 46 — Developer Portal & Golden Paths

| | |
|---|---|
| **Stage** | 7 — Platform Experience |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 41, 42, 43, 44, 45 |
| **Blocks** | 47, 56 |
| **Risk** | 🟢 Low — a presentation layer over existing capability |
| **Blast radius** | User onboarding and self-service |
| **Architecture refs** | `ARCHITECTURE.md#l9-platform-services`, `ULTIMATE-PLAN.md#5-target-capability-model` |

---

## 🎯 MISSION

Put **one door** in front of everything built so far. A new user should be able to arrive, find what they need, and run their first job **without asking anyone** — through a CLI that makes the common cases one command, a portal that answers "what is here and how do I use it," and a set of **golden paths**: opinionated, working, copy-paste starting points for the eight things people actually do.

> 💡 **WHY this phase determines whether any of the previous 45 were worth it.** A platform's value is not its capability; it is the capability people can reach. Forty-five phases produced an extraordinary amount of machinery — DRA device classes, Kueue cohorts, topology annotations, NCCL injection, checkpoint contracts, five storage tiers. **A user should have to understand none of it.** The measure of this phase is Phase 41's G9.11 check applied continuously: can someone who did not build this run a job using only the docs?

> ⚠️ **The trap is building a portal instead of paths.** A beautiful catalog UI that links to twelve YAML references is a worse user experience than a single command that works. **Build the golden paths first; the portal exists to help people find them.**

---

## ✅ PREFLIGHT

```bash
# Everything the portal surfaces must already work
kubectl get clustertrainingruntimes,clusterworkflowtemplates
kubectl get deviceclasses,clusterqueues
kubectl get inferenceservices -A

# The docs written across all previous phases
find docs/user -name '*.md' | wc -l

# Identity for portal SSO
kubectl get gateway,securitypolicy -A

# 📊 Phase 41's G9.11 result — what a real user got stuck on
grep -A20 "G9.11" evidence/phase-41/handoff.md
```

---

## 📦 DELIVERABLES

```
cli/nexus/                          # 🎯 THE primary interface
  cmd/                              # submit, status, logs, lineage, quota, ...
  templates/                        # embedded golden paths
clusters/nexus-prod/platform/portal/
  backstage-values.yaml             # or a simpler static site — see Task 2
  catalog/                          # services, docs, templates
  scaffolder-templates/             # "create a new training project"
docs/
  golden-paths/                     # 🎯 eight opinionated, working paths
    01-train-a-model.md
    02-run-a-sweep.md
    03-process-a-dataset.md
    04-serve-a-model.md
    05-interactive-development.md
    06-run-an-mpi-job.md
    07-build-a-pipeline.md
    08-onboard-a-new-team.md
  README.md                         # the index a new user lands on
tools/portal/
  onboard-tenant.sh                 # ⚠️ one command creates a whole tenant
  user-check.sh                     # "is my access working?"
evidence/phase-46/{preflight,acceptance,handoff,deviations}.md
```

---

## 📋 TASKS

### Task 1 — 🎯 The `nexus` CLI (build this before the portal)

One binary, sensible defaults, and it composes the injections from every prior phase.

```bash
# ── The eight commands that cover 90 % of use ──
nexus submit train.py --gpus 8            # submit a training job; everything else defaulted
nexus status                              # my jobs: state, GPUs, utilization, ETA
nexus logs <job> [-f]                     # logs, across all ranks
nexus notebook [--gpu shared]             # start/attach an interactive session
nexus quota                               # what I have, what I'm using, what I can borrow
nexus dataset list|import|reshard         # the Phase 28 path
nexus serve <model> --replicas 2          # deploy inference
nexus lineage <model|job>                 # the Phase 44 chain

# ── What `nexus submit` actually generates ──
#   a TrainJob with: the right runtime, queue label, topology annotation,
#   NCCL injection, scratch, checkpoint sync, tracking env, job-id label,
#   DRA claim, gang minimum, and an activeDeadlineSeconds.
#   The user typed: nexus submit train.py --gpus 8
```

> 💡 **The CLI is where all the platform's defaults become invisible.** Every phase from 19 onward added something a user would have had to know. `nexus submit` is the single place that composes them, which means improvements to defaults reach every user without anyone editing YAML.

**Design rules:**

| Rule | Why |
|---|---|
| **Every command works with zero flags** | Defaults are the product |
| **`--dry-run` prints the generated YAML** | Escape hatch; also the best documentation |
| **Errors say what to do, not what failed** | "Your queue is at capacity; 12 jobs ahead; est. 40 min" not "admission denied" |
| Output is human-readable by default, `--json` for scripts | |
| Version-pinned and distributed via the registry | Users must not run a stale CLI |
| **Never a wrapper that hides `kubectl`** | Power users must be able to drop down; print the equivalent command with `-v` |

> ⚠️ **Error messages are the highest-leverage thing in the CLI.** Phase 30 and 31 both noted that "pending forever with no explanation" is the dominant user frustration. `nexus status` must answer *why*:
> ```
> train-8823   PENDING   16 GPU   waiting 34m
>   ⚠️ Your tenant's quota (32 GPU) is fully used by 2 other jobs.
>      You could borrow 8 more, but the cohort has none free right now.
>      Estimated start: ~40 min (job train-8801 finishes then).
>      Tip: submitting as preemptible would start now at 0.4× the rate.
> ```

---

### Task 2 — The portal: how much is warranted?

Be honest about the choice.

| Option | Effort | Gives you |
|---|---|---|
| **A. A good `docs/` site + the CLI** | Low | 80 % of the value. MkDocs/Docusaurus from the repo, searchable, in Git. |
| **B. Backstage** | High | Service catalog, scaffolder, plugin ecosystem, TechDocs, ownership metadata |
| C. A custom UI | Very high | Exactly what you want, and a maintenance burden forever |

> 💡 **Start with A. Move to B only when the catalog problem is real** — i.e. when there are enough services, models, datasets, and owners that "who owns this?" and "what exists?" are genuinely hard questions. At 100 nodes with a handful of teams, a well-organized docs site plus `nexus` covers it. **Backstage is a substantial system to operate; adopt it deliberately, not by default.** Law X.

**Whichever you choose, these must be present and current:**

| Section | Content |
|---|---|
| **Start here** | The 10-minute path from zero to a running job |
| Golden paths | The eight paths (Task 3) |
| Reference | Quotas, storage tiers, GPU types, node pools, SLOs — the facts users need |
| Status | Live cluster capacity, queue depth, incidents (from Phase 45) |
| Catalog | Datasets (Phase 28), models (Phase 44), base images (Phase 42) |
| Runbooks | Operator-facing, linked from every alert (Phase 45) |
| Ask | Where to get help; who owns what |

> ⚠️ **Documentation that is not generated from reality goes stale within a month.** Generate what you can: quota tables from Kueue, GPU types from node labels, storage tiers from StorageClasses, dataset catalog from Phase 28, SLOs from Phase 47. Hand-written prose is for *why*; generated content is for *what*.

---

### Task 3 — 🎯 The eight golden paths

Each is a complete, tested, copy-pasteable document. **Each must actually run** — verify by executing it, not by reading it.

**Structure every path identically:**
```markdown
# Train a model on multiple GPUs

## In one command
    nexus submit train.py --gpus 8

## What you need first
· Your code in Git, and an image (see: build a container)
· Your dataset in the catalog (see: process a dataset)

## Step by step
[the actual working steps]

## What the platform does for you
[the injections — so users understand the magic without needing to configure it]

## Making it faster
[the top 3 things that matter, with links to the deeper guides]

## When it goes wrong
[the 5 most common failures, with the exact diagnosis command]

## Going further
[links to the reference docs]
```

**The eight:**

| # | Path | The command it reduces to |
|---|---|---|
| 1 | Train a model | `nexus submit train.py --gpus 8` |
| 2 | Run a hyperparameter sweep | `nexus sweep config.yaml` |
| 3 | Process a dataset | `nexus dataset reshard --input ... --output ...` |
| 4 | Serve a model | `nexus serve my-model --replicas 2` |
| 5 | Interactive development | `nexus notebook --gpu shared` |
| 6 | Run an MPI job | `nexus submit --mpi --nodes 8 ./solver` |
| 7 | Build a pipeline | `nexus pipeline submit train-eval-deploy.yaml` |
| 8 | Onboard a new team | `nexus tenant create <name>` (operator-facing) |

> 💡 **Path 8 is the one that scales the platform.** `onboard-tenant.sh` should create in one command: the namespace, Capsule tenant, Kueue LocalQueue and ClusterQueue with a quota, the CephFS home and shared directories with quotas, the object storage buckets, the registry project, the Keycloak group and RBAC bindings, the tracking experiment namespace, and a welcome message with links. Doing this by hand for the fifth team is how platforms accumulate inconsistency.

---

### Task 4 — The status page

Users need to know, without asking: is it me, or is it the cluster?

```
NEXUS CLUSTER STATUS                                    ✅ Operational

CAPACITY NOW
  GPUs:     318 / 400 in use (79 %)      Queue: 14 jobs waiting
  Your tenant: 28 / 32 used, 0 borrowed
  Typical wait for 8 GPUs right now: ~25 min

RECENT
  ⚠️ 2026-08-30 — 4 nodes in rack R3 offline for PSU replacement (ETA 18:00)
  ✅ 2026-08-28 — Resolved: slow CephFS metadata (MDS cache increased)

PLANNED
  📅 2026-09-05 02:00-06:00 — Kubernetes upgrade. Batch jobs will be preempted;
                              inference unaffected. (Phase 53 procedure)
```

> 💡 **A live capacity and wait-time estimate removes most support requests.** "Why is my job pending?" is answered before it is asked. Compute the wait estimate from Phase 30's queue state and historical job durations — even a rough estimate beats silence.

---

### Task 5 — Onboarding, measured

**The 10-minute test** — the acceptance criterion that matters most.

```
A new user, with an account and nothing else, should reach a running GPU job
in under 10 minutes using only the documentation.

Measure it. With a real person. Watch them. Do not help.
Every place they pause is a defect.
```

**The onboarding checklist a new user gets:**
```markdown
1. Log in to the portal                         → 1 min
2. Install the CLI (one command)                → 1 min
3. `nexus login`                                → 30 s
4. `nexus quota` — see what you have            → 30 s
5. `nexus notebook` — a GPU session opens       → 1 min
6. Run the hello-GPU example                    → 2 min
7. `nexus submit examples/train.py --gpus 2`    → 1 min
8. `nexus status` — watch it run                →
9. Read: "when to stop using a notebook"        → 3 min
✅ You are productive.
```

> ⚠️ **Run this test with at least three different people**, ideally from different backgrounds (an ML researcher, an HPC user, a data engineer). Each will get stuck in a different place, and each place is a real defect that fifty future users would otherwise hit individually.

---

### Task 6 — Keep it from going stale

Documentation rot is the default outcome. Design against it.

| Mechanism | Effect |
|---|---|
| **Golden paths are executed in CI** | A path that stops working fails a build, not a user |
| Generated reference content | Quotas, GPU types, tiers, SLOs pulled from the live cluster |
| **Every phase's handoff feeds the docs** | Phases 0–45 each produced user-facing facts; link them, don't re-write them |
| Doc ownership | Every page has a named owner and a review date |
| Broken-link checking in CI | |
| **A "was this helpful?" signal** | Cheap feedback on which pages fail people |

> 🚫 **Do not duplicate content between the phase docs and the portal.** The golden paths should link to `docs/user/parallelism-strategies.md` (Phase 37), not restate it. Duplication guarantees divergence.

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | `nexus` CLI installs with one command | Test on a clean machine | Installs |
| **A2** | `nexus login` works via OIDC | Test | Works |
| **A3** | **Every command works with zero flags** | Run each bare | Works |
| **A4** | `nexus submit train.py --gpus 8` produces a correct, complete TrainJob | `--dry-run` and inspect | All injections present |
| **A5** | `--dry-run` prints valid, applicable YAML | Apply it | Applies |
| **A6** | `-v` prints the equivalent `kubectl` command | Test | Prints |
| **A7** | 🎯 **`nexus status` explains WHY a job is pending** | 🧪 Submit over quota | Clear explanation |
| **A8** | Error messages say what to do | Review all error paths | Actionable |
| **A9** | `nexus logs -f` streams from all ranks | Test a multi-node job | Streams |
| **A10** | `nexus quota` shows used, available, borrowable | Test | Correct |
| **A11** | `nexus lineage` works (Phase 44) | Test | Works |
| **A12** | CLI version-pinned and distributed via the registry | Check | Pinned |
| **A13** | Portal/docs site deployed behind SSO | Browser | Works |
| **A14** | **All eight golden paths execute successfully as written** | 🧪 Run each verbatim | All run |
| **A15** | **Golden paths are executed in CI** | Check the workflow | Running |
| **A16** | Reference content is generated from the live cluster | Change a quota, check the page | Updates |
| **A17** | Status page shows live capacity and wait estimates | Open it | Live |
| **A18** | Catalog lists datasets, models, and base images | Browse | Listed |
| **A19** | Every alert's `runbook_url` resolves to a real page | Link check | 100 % |
| **A20** | 🎯 **`onboard-tenant.sh` creates a complete, working tenant in one command** | 🧪 Create one; run a job in it | Complete |
| **A21** | 🧪 **A new user reaches a running GPU job in < 10 min using only docs** | Test with 3 real people | All 3 succeed |
| **A22** | Every place they got stuck is recorded and fixed | Evidence | Recorded |
| **A23** | Broken-link check in CI | Check | Running |
| **A24** | Every doc page has an owner and a review date | Audit | 100 % |
| **A25** | No content duplicated between golden paths and phase docs | Review | Linked, not copied |

---

## ↩️ ROLLBACK

```bash
# The portal is a presentation layer — removing it changes no capability
helm uninstall backstage -n portal
# Users fall back to the CLI and the docs in Git, which is most of the value anyway.

# CLI rollback: users pin an older version
nexus self-update --version <previous>
```

> 💡 **The layering here is deliberate: docs in Git → CLI → portal.** Each lower layer works without the one above it. A portal outage is an inconvenience, not an incident.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| CLI generates a job that is rejected | Defaults drifted from current policy | A15 — the CI run should have caught it |
| Users still write raw YAML | The CLI does not cover their case | Ask them; extend it or document the gap |
| Golden path fails when followed | Underlying change not reflected | A15 — CI must run the paths |
| Reference docs show stale quotas | Hand-written instead of generated | A16 |
| New users get stuck at the same step | A real defect in that step | A21/A22 — fix it, retest |
| Wait-time estimate is wildly wrong | Model too naive | Use historical duration percentiles per queue |
| Portal shows services that no longer exist | Catalog not reconciled from the cluster | Generate the catalog, don't hand-maintain it |
| Runbook links 404 | Doc moved | A23 |
| Nobody uses the portal | The CLI covers everything they need | ✅ That is fine — the CLI is the product |

---

## 🚫 DO NOT

- **Do not** build the portal before the golden paths.
- **Do not** make the CLI a wrapper that hides Kubernetes from power users.
- **Do not** hand-write reference content that can be generated.
- **Do not** duplicate content between the portal and the phase docs.
- **Do not** ship a golden path that has not been executed verbatim.
- **Do not** adopt Backstage by default — decide deliberately.
- **Do not** skip the three-person onboarding test.
- **Do not** let error messages say only what failed.

---

## 📤 HANDOFF

`evidence/phase-46/handoff.md` must state:

1. **🧪 The onboarding test results** — three people, time to first job, and every point where they got stuck. **The most valuable artifact of the phase.**
2. **The CLI command surface** and what each generates.
3. **The eight golden paths**, confirmed executed, and their CI status.
4. **Portal choice (A/B/C) and why.**
5. **What reference content is generated vs. hand-written**, and the staleness risk in what remains.
6. **`onboard-tenant.sh`'s coverage** — everything it creates, and anything still manual.
7. **Documentation ownership** — the owner and review date model.
8. **Known gaps** where users still need to write raw YAML.

---

## ➡️ NEXT

**[PHASE-47 — SLOs, Alerting & On-Call (G12/G14)](PHASE-47.md)** — define what "working" means, commit to it, and build the human process that sustains it. Then close Stage 7.
