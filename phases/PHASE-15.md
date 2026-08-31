# PHASE 15 — GitOps with Argo CD

| | |
|---|---|
| **Stage** | 2 — Kubernetes Substrate |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 12 (13 recommended) |
| **Blocks** | 16, 17 — **and every phase from here to 56** |
| **Risk** | 🟠 High — auto-sync with `prune` can delete resources; a bad commit propagates fleet-wide in minutes |
| **Blast radius** | Everything Argo CD manages |
| **Architecture refs** | `ARCHITECTURE.md#x4--repository-architecture`, ADR-015, Law II, R-13 |

---

## 🎯 MISSION

Install Argo CD and establish the **app-of-apps** structure with sync waves, drift detection, and self-healing — so that from this point forward **the only way to change the cluster is a commit**. Every phase after this one delivers manifests, not `kubectl` commands.

> 💡 **WHY this is the hinge of the whole project:** Phases 00–14 were bootstrap, and bootstrap is necessarily imperative. From Phase 16 onward there are ~40 components to install across 100 nodes. Installing them by hand is a week of work and a permanent inability to answer "what is actually running?" With GitOps, the answer is `git log`. **Law II becomes enforceable at exactly this moment.**

> ⚠️ **DANGER — `prune: true` deletes resources.** If a manifest is removed from Git, Argo CD deletes it from the cluster. That is the correct behavior and it is also how someone deletes a Ceph cluster with a `git rm`. The mitigations in Task 5 are not optional.

---

## ✅ PREFLIGHT

```bash
kubectl get nodes                       # all Ready
bash tools/net/cilium-verify.sh
bash tools/cluster/etcd-health.sh

# Repository is clean and pushed
git status --porcelain                  # empty
git log --oneline -1

# The repo structure from Phase 00 exists
test -d clusters/nexus-prod/{infra,acceleration,storage,scheduling,runtimes,platform,observability,tenants}

# Secrets tooling works (Argo CD needs to read SOPS-encrypted values, or ESO does it)
bash tools/secrets/seal-check.sh
```

---

## 📦 DELIVERABLES

```
bootstrap/argocd-install/
  install.sh                    # the ONE imperative act in this phase
  values.yaml
clusters/nexus-prod/
  root-app.yaml                 # ⚠️ the app-of-apps entry point
  projects/
    infra.yaml  platform.yaml  tenants.yaml  runtimes.yaml
  infra/kustomization.yaml      # + one Application per component
  <each stage dir>/kustomization.yaml
  apps/                         # Application manifests, grouped by sync wave
docs/operations/
  gitops-workflow.md            # how to change the cluster, for everyone
  argocd-runbook.md
  sync-waves.md
tools/gitops/
  app-diff.sh  app-sync.sh  drift-report.sh  validate-apps.sh
evidence/phase-15/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Source |
|---|---|---|
| Argo CD | `2.13.2` | `oci://ghcr.io/argoproj/argo-helm/argo-cd` chart `7.7.11` |
| Argo CD Image Updater | `0.15.2` | optional, Phase 53 |
| KSOPS / sops plugin | `4.3.2` | for SOPS-encrypted manifests |
| Gitea (local mirror) | `1.22.6` | `oci://docker.gitea.com/charts/gitea` — R-13 |

---

## 📋 TASKS

### Task 1 — Install Argo CD (the last imperative act)

```bash
# bootstrap/argocd-install/install.sh — idempotent, recorded, run once
helm upgrade --install argocd \
  oci://ghcr.io/argoproj/argo-helm/argo-cd --version 7.7.11 \
  --namespace argocd --create-namespace \
  --values bootstrap/argocd-install/values.yaml \
  --wait --timeout 10m
```

**`bootstrap/argocd-install/values.yaml`**
```yaml
global:
  domain: argocd.apps.nexus.internal          # exposed in Phase 17, not here

configs:
  params:
    server.insecure: true                     # TLS terminates at the Gateway (Phase 17)
    application.namespaces: "*"                # allow Applications outside argocd ns
    controller.diff.server.side: "true"        # server-side diff — fewer false positives
    controller.sync.timeout.seconds: "600"
    reposerver.parallelism.limit: "10"
  cm:
    timeout.reconciliation: 180s               # 3 min; webhooks make this mostly moot
    application.resourceTrackingMethod: annotation+label
    # Ignore fields that other controllers legitimately mutate — prevents
    # permanent OutOfSync noise that trains people to ignore the dashboard
    resource.customizations.ignoreDifferences.all: |
      jqPathExpressions:
        - '.metadata.annotations."kubectl.kubernetes.io/last-applied-configuration"'
    resource.customizations.ignoreDifferences.apps_Deployment: |
      jsonPointers: [/spec/replicas]           # HPA owns replicas
    resource.exclusions: |
      - apiGroups: ["cilium.io"]
        kinds: ["CiliumIdentity","CiliumEndpoint"]
        clusters: ["*"]
  rbac:
    policy.default: role:readonly              # ⚠️ default read-only
    scopes: "[groups]"
    policy.csv: |
      p, role:nexus-admin, applications, *, */*, allow
      p, role:nexus-admin, clusters, *, *, allow
      p, role:nexus-admin, repositories, *, *, allow
      p, role:nexus-operator, applications, get, */*, allow
      p, role:nexus-operator, applications, sync, */*, allow
      g, nexus-platform-admins, role:nexus-admin
      g, nexus-platform-operators, role:nexus-operator
      # TODO(phase-17): groups come from Keycloak OIDC

controller:
  replicas: 1                                  # sharding only past ~1000 apps
  resources: { requests: {cpu: 500m, memory: 1Gi}, limits: {memory: 4Gi} }
  metrics: { enabled: true, serviceMonitor: { enabled: false } }   # TODO(phase-45)

repoServer:
  replicas: 2
  resources: { requests: {cpu: 250m, memory: 512Mi}, limits: {memory: 2Gi} }

server:
  replicas: 2
applicationSet:
  replicas: 2
redis-ha:
  enabled: true                                # HA Redis — Argo caches heavily

# Run on infra nodes only
global.nodeSelector: { nexus.io/archetype: infra }
global.tolerations:
  - { key: nexus.io/archetype, operator: Equal, value: infra, effect: NoSchedule }
```

> ⚠️ **`policy.default: role:readonly`.** Argo CD's default is `role:readonly` in recent versions, but verify it. An Argo CD with a permissive default is a cluster-admin API with a web UI.

---

### Task 2 — Projects (the blast-radius boundary)

`AppProject` restricts what an Application may deploy, where, and from which repo. **This is the control that stops a compromised or mistaken Application from touching the whole cluster.**

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata: { name: infra, namespace: argocd }
spec:
  description: "Cluster infrastructure — CNI, DNS, policy, certs"
  sourceRepos: [ "https://git.nexus.internal/nexus/cluster.git" ]   # ⚠️ ONE repo
  destinations:
    - { server: https://kubernetes.default.svc, namespace: "kube-system" }
    - { server: https://kubernetes.default.svc, namespace: "cert-manager" }
    - { server: https://kubernetes.default.svc, namespace: "kyverno" }
    - { server: https://kubernetes.default.svc, namespace: "nfd" }
  clusterResourceWhitelist:
    - { group: "*", kind: "*" }                 # infra legitimately needs CRDs, CRBs
  namespaceResourceWhitelist:
    - { group: "*", kind: "*" }
  orphanedResources: { warn: true }
---
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata: { name: tenants, namespace: argocd }
spec:
  description: "Tenant workloads — deliberately restricted"
  sourceRepos: [ "https://git.nexus.internal/nexus/cluster.git" ]
  destinations:
    - { server: https://kubernetes.default.svc, namespace: "team-*" }
  # ⚠️ Tenants may NOT create cluster-scoped resources
  clusterResourceBlacklist:
    - { group: "*", kind: "*" }
  namespaceResourceBlacklist:
    - { group: "rbac.authorization.k8s.io", kind: "ClusterRole" }
    - { group: "rbac.authorization.k8s.io", kind: "ClusterRoleBinding" }
```

---

### Task 3 — Sync waves

Ordering is not cosmetic. Cert-manager must exist before anything requests a Certificate; CRDs must exist before their custom resources; storage must exist before anything claims a PVC.

**`docs/operations/sync-waves.md`**

| Wave | Contents | Why here |
|---|---|---|
| **-10** | Namespaces, CRDs, PriorityClasses | Everything else references them |
| **-5** | cert-manager, External Secrets Operator, Kyverno CRDs | Certificates and secrets must be issuable before consumers start |
| **0** | Cilium config, CoreDNS, NFD, node-labeler, metrics-server | Core cluster function |
| **5** | Kyverno policies, Capsule, RBAC | ⚠️ Policies **after** the components they govern exist, so nothing is blocked mid-install |
| **10** | Storage: local-path, Mayastor, Rook operator | Must precede any PVC |
| **15** | Rook CephCluster, StorageClasses, JuiceFS | Depends on the operator |
| **20** | GPU Operator, DRA driver, SR-IOV, Network Operator | Depends on nodes being labeled |
| **25** | Kueue, scheduler-plugins, KEDA, Descheduler | Depends on device plugins publishing capacity |
| **30** | KubeRay, Trainer, MPI Operator, KServe, LWS | Depends on the scheduler |
| **35** | Harbor, Keycloak, OpenBao, Gateway API, Backstage | Platform services (need storage) |
| **40** | Prometheus, Mimir, Grafana, Loki, Tempo, Parca, Kepler | Observability last, so it can scrape everything |
| **50** | Tenant namespaces, quotas, LocalQueues | The users arrive |

```yaml
metadata:
  annotations:
    argocd.argoproj.io/sync-wave: "10"
```

> 💡 **Why policies are wave 5, not wave -5:** if Kyverno's enforcing policies exist before the components they govern, a chicken-and-egg deadlock occurs — the GPU Operator's privileged DaemonSet is rejected by the policy that has not yet been told it is on the allow-list. Install components, then policies, then verify. Phase 16 handles the ordering carefully.

---

### Task 4 — The app-of-apps

**`clusters/nexus-prod/root-app.yaml`** — the single Application that manages all others.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: root
  namespace: argocd
  finalizers: [ resources-finalizer.argocd.argoproj.io ]
spec:
  project: infra
  source:
    repoURL: https://git.nexus.internal/nexus/cluster.git
    targetRevision: main
    path: clusters/nexus-prod/apps
  destination: { server: https://kubernetes.default.svc, namespace: argocd }
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
      allowEmpty: false          # ⚠️ refuse to sync an empty source — guards a bad path
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
      - PruneLast=true           # delete removed resources AFTER creating new ones
      - RespectIgnoreDifferences=true
    retry:
      limit: 5
      backoff: { duration: 30s, factor: 2, maxDuration: 10m }
```

**Per-component Application template** (every later phase writes one of these):
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: cilium
  namespace: argocd
  annotations: { argocd.argoproj.io/sync-wave: "0" }
  finalizers: [ resources-finalizer.argocd.argoproj.io ]
spec:
  project: infra
  sources:
    - repoURL: https://helm.cilium.io/
      chart: cilium
      targetRevision: 1.17.1                  # ⚠️ pinned, always (Rule 4)
      helm:
        valueFiles: [ $values/clusters/nexus-prod/infra/cilium/values.yaml ]
    - repoURL: https://git.nexus.internal/nexus/cluster.git
      targetRevision: main
      ref: values
  destination: { server: https://kubernetes.default.svc, namespace: kube-system }
  syncPolicy:
    automated: { prune: true, selfHeal: true }
    syncOptions: [ ServerSideApply=true ]
```

> 💡 **The two-source pattern** (upstream chart + values from your Git) is the cleanest way to keep values under review without vendoring charts. Every component in this project uses it.

**Adopt the already-installed components.** Cilium (Phase 13) and NFD (Phase 14) were installed with Helm. Write their Applications and let Argo CD adopt them — with `ServerSideApply=true` and matching values, the sync should be a no-op. **Verify with `argocd app diff` before enabling auto-sync.**

---

### Task 5 — Guardrails against destructive sync

`prune: true` + `selfHeal: true` is powerful and dangerous. Six mitigations:

| # | Guardrail | Implementation |
|---|---|---|
| **G1** | Branch protection on `main` | Required review, required CI, no force-push, signed commits |
| **G2** | CODEOWNERS on high-risk paths | `clusters/**/storage/`, `talos/`, `policies/` require a second approver |
| **G3** | CI renders and validates every Application before merge | `tools/gitops/validate-apps.sh` — `helm template` + `kubeconform` |
| **G4** | **Prune protection on stateful resources** | `argocd.argoproj.io/sync-options: Prune=false` on `CephCluster`, PVCs, and anything holding data |
| **G5** | `allowEmpty: false` | An Application whose path yields nothing refuses to sync (and therefore refuses to prune everything) |
| **G6** | Drift and prune alerting | `drift-report.sh` + Prometheus alert on `argocd_app_info{sync_status="OutOfSync"}` and on prune events |

**Apply G4 to every data-holding resource:**
```yaml
metadata:
  annotations:
    argocd.argoproj.io/sync-options: Prune=false
    # A human must delete this deliberately. `git rm` is not enough.
```

> ⚠️ **Test G5 deliberately.** Point an Application at a nonexistent path and confirm it refuses rather than pruning its entire destination. This is a five-minute test that prevents a very bad afternoon.

---

### Task 6 — The local Git mirror (R-13)

If Argo CD's only source is GitHub and GitHub is unreachable, you cannot deploy, cannot roll back, and cannot recover.

```bash
# Gitea on the cluster, mirroring the upstream repo every 5 minutes
helm upgrade --install gitea oci://docker.gitea.com/charts/gitea --version 10.6.0 \
  --namespace platform-git --create-namespace \
  --set gitea.config.server.ROOT_URL=https://git.nexus.internal/ \
  --set persistence.storageClass=<T2>       # TODO(phase-27): needs Ceph
```

**Point Argo CD at the mirror, not at the upstream.** Developers push to upstream; the mirror pulls; Argo CD reads the mirror. That way an upstream outage stops new changes but does not stop reconciliation or rollback.

Document the failure modes in `argocd-runbook.md`:

| Failure | Effect | Response |
|---|---|---|
| Upstream Git unreachable | Mirror serves the last sync; Argo CD keeps reconciling | None urgent |
| Mirror unreachable | Argo CD cannot sync; **running workloads unaffected** | Point Argo CD at upstream temporarily |
| Both unreachable | No new syncs; cluster keeps running | Restore Git; nothing is lost |
| Argo CD itself down | No reconciliation; **running workloads unaffected** | Restart; it converges |

---

### Task 7 — The workflow everyone follows

**`docs/operations/gitops-workflow.md`** — short, and enforced.

```
TO CHANGE THE CLUSTER
  1. git checkout -b <type>/<short-description>
  2. Edit the manifest under clusters/nexus-prod/<stage>/
  3. task validate                     # local gates
  4. tools/gitops/app-diff.sh <app>    # see exactly what will change in the cluster
  5. git commit  (conventional commit; reference the phase)
  6. Open a PR. CI must pass. CODEOWNERS review where required.
  7. Merge. Argo CD syncs within 3 minutes (or immediately via webhook).
  8. Verify: argocd app get <app>; check the component's own health.

TO ROLL BACK
  git revert <sha> && push        ← ALWAYS PREFER THIS
  argocd app rollback <app> <id>  ← emergency only; creates drift until Git catches up

NEVER
  kubectl apply / edit / delete / scale / patch against a managed resource.
  selfHeal will revert it within ~3 minutes, and you will have learned nothing
  except that the dashboard is now lying to you.

EMERGENCY BYPASS (incidents only)
  argocd app set <app> --sync-policy none    # stop reconciliation
  <do the emergency thing>
  <IMMEDIATELY commit the change to Git>
  argocd app set <app> --sync-policy automated
  Record it in evidence/ as an incident.
```

**`tools/gitops/drift-report.sh`** — runs daily, and is the evidence for gate G12:
```bash
argocd app list -o json | jq -r '
  .[] | select(.status.sync.status != "Synced" or .status.health.status != "Healthy")
      | "\(.metadata.name)\t\(.status.sync.status)\t\(.status.health.status)"'
# Any output is a finding. Investigate: is it a controller mutating a field
# (fix with ignoreDifferences), or did someone touch the cluster by hand?
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Argo CD is healthy with HA replicas | `kubectl -n argocd get pods` | All Running; server/repo ≥ 2 |
| **A2** | Argo CD runs on infra nodes only | `kubectl -n argocd get pods -o wide` | All on `archetype=infra` |
| **A3** | Root app-of-apps is Synced and Healthy | `argocd app get root` | Synced/Healthy |
| **A4** | Cilium and NFD adopted with no drift | `argocd app diff cilium`; `argocd app diff nfd` | Empty diff |
| **A5** | Sync waves are respected | Delete a namespace's apps; re-sync; watch order | Lower waves complete first |
| **A6** | AppProject restricts tenant apps to `team-*` namespaces | Create a tenant Application targeting `kube-system` | Rejected |
| **A7** | AppProject blocks tenant cluster-scoped resources | Attempt a ClusterRole from the tenants project | Rejected |
| **A8** | **`allowEmpty: false` prevents a mass prune** | Point a test app at a nonexistent path | Refuses to sync; nothing pruned |
| **A9** | **`Prune=false` protects a stateful resource** | Annotate a test PVC; remove it from Git; sync | PVC survives; app reports OutOfSync |
| **A10** | selfHeal reverts a manual change | `kubectl scale` a managed Deployment | Reverted within 3 min |
| **A11** | Drift is detectable and reportable | `drift-report.sh` after the A10 change | Reports the drift |
| **A12** | Branch protection is active on `main` | Attempt a direct push | Rejected |
| **A13** | CI validates rendered manifests | Open a PR with an invalid manifest | CI fails |
| **A14** | CODEOWNERS enforced on storage/talos/policies paths | PR touching `clusters/**/storage/` | Requires the named reviewer |
| **A15** | Rollback via `git revert` works end-to-end | Make a benign change, revert, observe | Cluster returns to the prior state |
| **A16** | Gitea mirror is running and syncing | `curl https://git.nexus.internal/...` | Repo present, current |
| **A17** | Argo CD reads from the mirror | `argocd app get root -o json \| jq .spec.source.repoURL` | Points at the mirror |
| **A18** | Cluster keeps reconciling with upstream Git blocked | Block upstream at the firewall; make no changes | No errors |
| **A19** | `argocd app diff` shows changes before merge | Run against a branch | Accurate preview |
| **A20** | GitOps workflow is documented and the "NEVER" section is explicit | Read it | Present |

---

## ↩️ ROLLBACK

```bash
# Stop reconciliation for one app (emergency)
argocd app set <app> --sync-policy none
# Roll back an app to a previous sync
argocd app history <app>; argocd app rollback <app> <id>
# Remove Argo CD entirely (⚠️ leaves managed resources in place, unmanaged)
helm -n argocd uninstall argocd
```
> ⚠️ Uninstalling Argo CD does **not** delete what it manages — unless the `resources-finalizer` runs. Remove finalizers from Applications first if you want the workloads to survive.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| App stuck `OutOfSync` on a field you never set | Another controller mutates it (HPA replicas, webhook-injected fields) | Add an `ignoreDifferences` entry. **Do not** silence it by disabling selfHeal. |
| App `Progressing` forever | Health check does not understand a custom resource | Add a custom health check (Lua) in the Argo CD ConfigMap |
| Sync fails: "resource already exists" | The resource was created out-of-band | `ServerSideApply=true` usually adopts it; otherwise add the tracking annotation manually once |
| CRD-dependent resources fail on first sync | CRD not yet established | Correct sync wave (CRDs at -10), plus `SkipDryRunOnMissingResource=true` |
| Prune deleted something important | Missing `Prune=false` | Restore from backup; **add the annotation to every stateful resource now** (G4) |
| Repo server OOM on a big chart | Memory limit too low | Raise it; consider `reposerver.parallelism.limit` |
| Sync is very slow | Reconciliation timeout too high with no webhook | Configure a Git webhook to Argo CD for instant sync |
| Argo CD cannot read SOPS-encrypted values | No decryption plugin | Prefer **External Secrets Operator** over in-Argo decryption — it keeps the age key out of the cluster entirely |
| Two Applications manage the same resource | Overlapping paths/selectors | Fix the paths. Shared ownership causes a sync fight loop. |

---

## 🚫 DO NOT

- **Do not** use `kubectl apply` against a managed resource after this phase. That is the entire point.
- **Do not** enable `prune: true` without `allowEmpty: false` and `Prune=false` on stateful resources.
- **Do not** put the age private key into the cluster so Argo CD can decrypt SOPS. Use ESO instead.
- **Do not** give Applications an unrestricted AppProject. Projects are the blast-radius boundary.
- **Do not** point Argo CD directly at a public Git host as its only source (R-13).
- **Do not** install Kyverno *policies* in an early sync wave. Wave 5, after the components they govern.
- **Do not** use `targetRevision: HEAD` or a branch for charts. Pin versions (Rule 4).
- **Do not** disable selfHeal to stop drift alerts. Fix the cause.
- **Do not** configure OIDC for Argo CD yet — Keycloak arrives in Phase 17.

---

## 📤 HANDOFF

`evidence/phase-15/handoff.md` must state:

1. **The Argo CD endpoint** and how to reach it before Phase 17 exposes it properly (port-forward).
2. **The repository structure and the app-of-apps entry point** — every later phase adds an Application here.
3. **The sync-wave table** — every later phase must pick the correct wave.
4. **The AppProject that each stage's components belong to.**
5. **The two-source Application template** — the pattern every later phase copies.
6. **The Git mirror URL** and the mirroring interval.
7. **Which components are now Argo-managed** (Cilium, NFD, …) and which are still imperative (nothing should be).
8. **The guardrails in place** (G1–G6), and evidence that G5 and G9 were actually tested.
9. **A restatement, prominently: from now on, all changes are commits.**

---

## ➡️ NEXT

**[PHASE-16 — Policy, Tenancy & RBAC](PHASE-16.md)** — implement the Phase 04 policy baseline with Kyverno, Pod Security Admission, Capsule tenants, and default-deny networking.
