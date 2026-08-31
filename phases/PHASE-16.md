# PHASE 16 — Policy, Tenancy & RBAC

| | |
|---|---|
| **Stage** | 2 — Kubernetes Substrate |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 15, 10 |
| **Blocks** | 17, 30, 43 — all multi-tenancy |
| **Risk** | 🔴 High — enforcing policies too early blocks every pod, including platform components |
| **Blast radius** | Every workload admission decision |
| **Architecture refs** | `ARCHITECTURE.md#x1--security-architecture`, `#x3--naming-labeling--namespace-taxonomy`, Phase 04 `policy-baseline.md`, ADR-026 |

---

## 🎯 MISSION

Implement the Phase 04 policy baseline: **Kyverno** for admission control, **Pod Security Admission** for the restricted baseline, **Capsule** for tenant boundaries, **default-deny CiliumNetworkPolicy**, and the RBAC model — rolled out in **audit mode first**, then flipped to enforce with evidence.

> ⚠️ **DANGER — the ordering in this phase is the phase.** Turning on 16 enforcing policies at once, in a cluster that has never had them, blocks the GPU Operator, Rook, Cilium, and every DaemonSet that legitimately needs privileges. The cluster does not fail loudly; it fails as "pods stuck Pending" scattered across namespaces with confusing admission webhook errors. **Audit → review → allow-list → enforce, one group at a time.**

> 💡 **WHY policy before workloads:** every namespace Phase 30 creates and every job Phase 37 runs inherits whatever the admission defaults are. Setting those defaults now means the correct behavior is free; setting them later means retrofitting every workload.

---

## ✅ PREFLIGHT

```bash
argocd app get root                                   # Synced/Healthy
kubectl get nodes                                     # all Ready
test -f docs/security/policy-baseline.md              # Phase 04
test -f docs/security/privileged-workloads.md         # Phase 04 — THE allow-list
test -f docs/security/trust-zones.md

# Cilium policy enforcement is still 'default' (Phase 13)
kubectl -n kube-system get cm cilium-config -o jsonpath='{.data.enable-policy}'

# Inventory the current privileged workloads — you must know what you are about to block
kubectl get pods -A -o json | jq -r '
  .items[] | select(any(.spec.containers[]; .securityContext.privileged == true))
  | "\(.metadata.namespace)/\(.metadata.name)"'
```

**Compare that list to `docs/security/privileged-workloads.md`.** Anything running privileged that is not on the list is either a finding or a missing ADR. Resolve before proceeding.

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/infra/kyverno/
  values.yaml
  application.yaml                    # sync-wave 5
policies/
  README.md
  pod-security/{require-non-root,require-seccomp,disallow-host-namespaces,
                disallow-host-path,disallow-privileged,drop-net-raw}.yaml
  supply-chain/{require-signed-images,require-registry,require-digest-prod}.yaml
  governance/{require-labels,require-resources,require-vram-declaration}.yaml
  defaults/{disable-sa-automount,add-seccomp,inject-tolerations}.yaml
  generation/{default-netpol,default-limitrange,default-resourcequota}.yaml
  exceptions/privileged-allowlist.yaml   # from Phase 04, verbatim
  tests/                                 # kyverno CLI test cases
clusters/nexus-prod/infra/capsule/
  values.yaml
  tenants/team-example.yaml
clusters/nexus-prod/infra/network-policy/
  baseline-deny.yaml
  allow-dns.yaml  allow-platform.yaml  allow-egress-template.yaml
docs/operations/
  policy-runbook.md                      # "my pod was rejected — why?"
  tenant-onboarding.md
evidence/phase-16/
  audit-report.md                        # ⚠️ the audit-mode findings before enforcing
  acceptance.md handoff.md deviations.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Source |
|---|---|---|
| Kyverno | `1.13.2` | `oci://ghcr.io/kyverno/charts/kyverno` |
| Kyverno policies (kyverno-policies chart) | `3.3.2` | same repo — used as a reference, not applied wholesale |
| Capsule | `0.7.3` | `oci://ghcr.io/projectcapsule/charts/capsule` |
| kyverno-cli | `1.13.1` | Phase 00 |
| policy-reporter | `3.2.2` | optional UI for policy reports |

---

## 📋 TASKS

### Task 1 — Install Kyverno (HA, because it fails closed)

**`clusters/nexus-prod/infra/kyverno/values.yaml`**
```yaml
# ⚠️ ADR-026: image signature verification fails CLOSED.
#    A Kyverno outage therefore blocks new pods. HA is mandatory, not optional.
admissionController:
  replicas: 3
  podDisruptionBudget: { minAvailable: 2 }
  antiAffinity: { enabled: true }
  topologySpreadConstraints:
    - maxSkew: 1
      topologyKey: nexus.io/rack
      whenUnsatisfiable: DoNotSchedule
      labelSelector: { matchLabels: { app.kubernetes.io/component: admission-controller } }
  resources: { requests: {cpu: 300m, memory: 512Mi}, limits: {memory: 2Gi} }
  # ⚠️ Do NOT set a CPU limit — throttled admission = API latency for everyone

backgroundController:  { replicas: 2 }
cleanupController:     { replicas: 2 }
reportsController:
  replicas: 2
  resources: { limits: { memory: 4Gi } }     # reports are memory-hungry at scale

config:
  # Never intercept these — prevents deadlock during cluster recovery
  resourceFilters:
    - '[*,kube-system,*]'
    - '[*,kube-public,*]'
    - '[*,kube-node-lease,*]'
    - '[*,kyverno,*]'
    - '[Event,*,*]'
    - '[Node,*,*]'
    - '[APIService,*,*]'
    - '[TokenReview,*,*]'
    - '[SubjectAccessReview,*,*]'
    - '[SelfSubjectAccessReview,*,*]'
    - '[Binding,*,*]'
    - '[ReplicaSet,*,*]'
    - '[EndpointSlice,*,*]'
  webhooks:
    - namespaceSelector:
        matchExpressions:
          - { key: kubernetes.io/metadata.name, operator: NotIn, values: [kube-system, kyverno] }

global:
  nodeSelector: { nexus.io/archetype: infra }
  tolerations: [ { key: nexus.io/archetype, operator: Equal, value: infra, effect: NoSchedule } ]
```

> ⚠️ **`resourceFilters` excluding `kube-system` and `kyverno` is a deadlock guard.** If Kyverno's own pods must pass a Kyverno webhook to start, a full cluster restart never recovers. This exclusion is the escape hatch — and it is also why the `kube-system` privileged workloads are governed by the allow-list document and quarterly review rather than by admission control.

---

### Task 2 — Write policies in Audit mode

**Every policy starts at `validationFailureAction: Audit`.** No exceptions.

**`policies/pod-security/disallow-privileged.yaml`**
```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: disallow-privileged
  annotations:
    policies.kyverno.io/severity: critical
    nexus.io/enforce-after: "2026-09-15"        # the planned flip date
spec:
  validationFailureAction: Audit                 # ⚠️ Enforce only after review
  background: true
  rules:
    - name: no-privileged-containers
      match:
        any: [ { resources: { kinds: [Pod] } } ]
      exclude:
        any:
          # ── The audited allow-list from Phase 04, VERBATIM ──
          - resources: { namespaces: [kube-system], selector: { matchLabels: { k8s-app: cilium } } }
          - resources: { namespaces: [gpu-operator] }
          - resources: { namespaces: [storage-ceph],     selector: { matchLabels: { app: rook-ceph-osd } } }
          - resources: { namespaces: [storage-mayastor], selector: { matchLabels: { app: io-engine } } }
          - resources: { namespaces: [network-operator] }
          - resources: { namespaces: [obs-health],       selector: { matchLabels: { app: node-problem-detector } } }
          - resources: { namespaces: [kube-system],      selector: { matchLabels: { app: spegel } } }
      validate:
        message: >-
          Privileged containers are not permitted. If this workload genuinely requires
          privilege, it must be added to docs/security/privileged-workloads.md with an
          ADR and a security review in the same PR.
        pattern:
          spec:
            =(initContainers): [ { =(securityContext): { =(privileged): "false" } } ]
            containers:        [ { =(securityContext): { =(privileged): "false" } } ]
```

**`policies/supply-chain/require-signed-images.yaml`** — ADR-026, the fail-closed one:
```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: require-signed-images
  annotations: { policies.kyverno.io/severity: critical }
spec:
  validationFailureAction: Audit          # TODO(phase-42): flip to Enforce once
                                          # Harbor's signing pipeline exists
  webhookTimeoutSeconds: 30               # signature verification is a network call
  failurePolicy: Fail                     # ⚠️ ADR-026 — fail CLOSED
  rules:
    - name: verify-nexus-images
      match:
        any: [ { resources: { kinds: [Pod] } } ]
      verifyImages:
        - imageReferences: [ "harbor.nexus.internal/*" ]
          attestors:
            - entries:
                - keys: { publicKeys: |-
                    -----BEGIN PUBLIC KEY-----
                    <cosign public key — safe to commit>
                    -----END PUBLIC KEY----- }
          mutateDigest: true              # pin to the verified digest
          verifyDigest: true
          required: true
```

**`policies/governance/require-vram-declaration.yaml`** — the Phase 19 prerequisite:
```yaml
# Any pod requesting a SHARED GPU must declare its VRAM budget, because
# time-slicing does NOT partition VRAM (ARCHITECTURE.md#L5.1).
# Without this, four pods on a 24 GB card each believe they have 24 GB and OOM each other.
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata: { name: require-vram-declaration }
spec:
  validationFailureAction: Audit
  rules:
    - name: shared-gpu-needs-vram
      match:
        any:
          - resources:
              kinds: [Pod]
              selector: { matchLabels: { "nexus.io/gpu-sharing": "true" } }
      validate:
        message: "Shared-GPU pods must set the nexus.io/vram-request annotation (e.g. '6Gi')."
        pattern:
          metadata: { annotations: { "nexus.io/vram-request": "?*" } }
```

**Mutation and generation policies** (these help users rather than blocking them):

| Policy | Type | Effect |
|---|---|---|
| `disable-sa-automount` | Mutate | Sets `automountServiceAccountToken: false` unless explicitly opted in |
| `add-seccomp` | Mutate | Adds `seccompProfile: RuntimeDefault` |
| `inject-nccl-env` | Mutate | **Phase 22** — injects NCCL env from node-pool labels so users never set them |
| `inject-dataset-mount` | Mutate | **Phase 28** — mounts the JuiceFS cache for pods with `nexus.io/dataset` |
| `default-netpol` | Generate | Creates a default-deny `CiliumNetworkPolicy` in every new namespace |
| `default-limitrange` | Generate | Sensible container defaults per namespace |

---

### Task 3 — Policy tests (before anything reaches the cluster)

Kyverno's CLI tests are the only way to know a policy does what you think.

```yaml
# policies/tests/disallow-privileged/kyverno-test.yaml
apiVersion: cli.kyverno.io/v1alpha1
kind: Test
metadata: { name: disallow-privileged }
policies: [ ../../pod-security/disallow-privileged.yaml ]
resources: [ resources.yaml ]
results:
  - policy: disallow-privileged
    rule: no-privileged-containers
    resources: [ bad-pod ]
    kind: Pod
    result: fail
  - policy: disallow-privileged
    rule: no-privileged-containers
    resources: [ good-pod ]
    kind: Pod
    result: pass
  - policy: disallow-privileged
    rule: no-privileged-containers
    resources: [ cilium-agent ]     # the allow-listed case MUST pass
    kind: Pod
    result: skip
```

```bash
kyverno test policies/tests/                # add to `task validate` and CI
```

> 💡 **Test the exclusions, not just the rejections.** The failure mode that hurts is a policy that correctly blocks bad pods *and* incorrectly blocks the GPU Operator. Every allow-list entry gets a test case proving it is skipped.

---

### Task 4 — The audit period (do not skip)

Deploy every policy in `Audit`, then **wait and read the reports** before enforcing.

```bash
# After ~1 week, or after the full stack has been deployed once:
kubectl get policyreports -A
kubectl get clusterpolicyreports

# Aggregate the failures
kubectl get polr -A -o json | jq -r '
  .items[].results[] | select(.result=="fail")
  | "\(.policy)/\(.rule)\t\(.resources[0].namespace)/\(.resources[0].name)"' \
  | sort | uniq -c | sort -rn | tee evidence/phase-16/audit-report.md
```

**For each failure, decide — and record the decision:**

| Finding | Decision |
|---|---|
| A legitimate platform component needs the privilege | **Add to the allow-list** + ADR + security review |
| A workload is genuinely non-compliant | **Fix the workload** before enforcing |
| The policy is too strict / has a false positive | **Fix the policy**, re-test |
| A large class of user workloads would break | **Stage the flip**: enforce in `platform-*` first, then `prod-*`, then tenants |

**Then flip, one policy group at a time, with a soak period between groups:**
```
Week 1: pod-security group     → Enforce.  Soak 3 days.
Week 2: governance group       → Enforce.  Soak 3 days.
Week 3: defaults + generation  → already mutating; verify then Enforce validation.
Phase 42: supply-chain group   → Enforce (needs the signing pipeline first).
```

Record each flip date and the audit evidence that justified it.

---

### Task 5 — Pod Security Admission

Belt and braces alongside Kyverno: PSA is built into the API server and has no webhook dependency, so it keeps working even if Kyverno is down.

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: team-example-dev
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/enforce-version: v1.34
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
```

**Namespace policy by class:**

| Namespace pattern | PSA level | Why |
|---|---|---|
| `team-*`, `prod-*`, `ci-*` | `restricted` | User workloads have no reason to be privileged |
| `platform-*`, `obs-*` | `baseline` | Some need host paths; none need full privilege |
| `kube-system`, `gpu-operator`, `storage-*`, `network-operator` | `privileged` | The audited allow-list |

> 💡 **PSA is the safety net for a Kyverno outage.** With ADR-026's fail-closed webhook, a Kyverno outage blocks new pods entirely — but if you ever set `failurePolicy: Ignore` for availability, PSA is what still stops a privileged pod from starting.

---

### Task 6 — Capsule tenants

**`clusters/nexus-prod/infra/capsule/tenants/team-example.yaml`**
```yaml
apiVersion: capsule.clastix.io/v1beta2
kind: Tenant
metadata: { name: team-vision }
spec:
  owners:
    - kind: Group
      name: team-vision-admins           # ← Keycloak group (Phase 17)
      clusterRoles: [ admin, capsule-namespace-deleter ]
  namespaceOptions:
    quota: 10
    additionalMetadata:
      labels:
        pod-security.kubernetes.io/enforce: restricted
        nexus.io/tenant: team-vision
  resourceQuotas:
    scope: Tenant                        # quota shared across the tenant's namespaces
    items:
      - hard: { requests.cpu: "500", requests.memory: 2Ti, "nvidia.com/gpu": "16",
                persistentvolumeclaims: "50", pods: "500" }
  limitRanges:
    items:
      - limits:
          - type: Container
            default:        { cpu: "1",   memory: 2Gi }
            defaultRequest: { cpu: 500m,  memory: 1Gi }
            max:            { cpu: "64",  memory: 512Gi }
  networkPolicies:
    items:
      - podSelector: {}
        policyTypes: [Ingress, Egress]
        ingress:
          - from:
              - namespaceSelector: { matchLabels: { nexus.io/tenant: team-vision } }
        egress:
          - to:
              - namespaceSelector: { matchLabels: { nexus.io/tenant: team-vision } }
              - namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: kube-system } }
                podSelector: { matchLabels: { k8s-app: kube-dns } }
  containerRegistries:
    allowed: [ "harbor.nexus.internal" ]
  nodeSelector:
    nexus.io/pool: general               # tenants cannot target arbitrary nodes
  additionalRoleBindings:
    - clusterRoleName: nexus-tenant-member
      subjects: [ { kind: Group, name: team-vision-members } ]
```

> 💡 **Capsule's `ResourceQuota` is a hard cap; Kueue's quota (Phase 30) is a fair-share scheduling budget.** They serve different purposes and you want both: Capsule stops a runaway namespace from consuming the cluster; Kueue decides who gets scheduled when demand exceeds supply, with borrowing and preemption. Set Capsule's cap generously above the Kueue nominal quota.

---

### Task 7 — Default-deny networking

Implements the Phase 04 trust-zone matrix.

**`baseline-deny.yaml`**
```yaml
apiVersion: cilium.io/v2
kind: CiliumClusterwideNetworkPolicy
metadata: { name: default-deny-tenants }
spec:
  description: "Default deny for all tenant namespaces (Phase 04 trust zones)"
  endpointSelector:
    matchExpressions:
      - { key: "k8s:io.kubernetes.pod.namespace", operator: In, values: [] }   # populated by generation
  ingress: [ {} ]      # empty rule = deny all
  egress:  [ {} ]
```

**`allow-dns.yaml`** — always required, or nothing resolves:
```yaml
apiVersion: cilium.io/v2
kind: CiliumClusterwideNetworkPolicy
metadata: { name: allow-dns }
spec:
  endpointSelector: {}
  egress:
    - toEndpoints:
        - matchLabels:
            io.kubernetes.pod.namespace: kube-system
            k8s-app: kube-dns
      toPorts:
        - ports: [ { port: "53", protocol: UDP }, { port: "53", protocol: TCP } ]
          rules: { dns: [ { matchPattern: "*" } ] }     # L7 DNS visibility in Hubble
```

**⚠️ Roll out network policy in Cilium's audit mode first:**
```yaml
# Set policyAuditMode temporarily so denied flows are LOGGED, not dropped
kubectl -n kube-system exec ds/cilium -- cilium-dbg config PolicyAuditMode=Enable
# Observe what WOULD be dropped:
hubble observe --verdict AUDIT --last 500
# Only when the audit log is clean of legitimate traffic:
kubectl -n kube-system exec ds/cilium -- cilium-dbg config PolicyAuditMode=Disable
```

Then set `policyEnforcementMode: always` in the Cilium values (Phase 13's TODO) **after** the baseline allow policies exist.

---

### Task 8 — The policy runbook

**`docs/operations/policy-runbook.md`** — the document users will actually need.

```
MY POD WAS REJECTED. WHY?

1. Read the error. Kyverno messages name the policy and the rule.
     kubectl describe pod <pod>            # events
     kubectl get events -n <ns> --sort-by=.lastTimestamp

2. Check the policy report for your namespace:
     kubectl get polr -n <ns> -o yaml

3. Test your manifest against the policies BEFORE applying:
     kyverno apply policies/ --resource my-pod.yaml

COMMON REJECTIONS AND FIXES
  "Privileged containers are not permitted"
      → You almost certainly do not need privilege. Use specific capabilities.
        If you truly do: ADR + security review + allow-list PR.
  "runAsNonRoot must be true"
      → Set securityContext.runAsNonRoot: true and runAsUser: <non-zero>.
        If the image requires root, rebuild it (Phase 42 base images do not).
  "Images must come from harbor.nexus.internal"
      → Mirror it: Harbor's proxy cache pulls upstream on first request.
  "Required label missing: nexus.io/owner"
      → Add owner/team/cost-center/workload-class. These drive paging and showback.
  "Shared-GPU pods must set nexus.io/vram-request"
      → Time-slicing does not partition VRAM. Declare your budget.
  "Resource limits are required"
      → Set requests and limits. Unbounded pods break the Topology Manager.

REQUESTING AN EXCEPTION
  PolicyException CR + justification + expiry date + security review.
  Exceptions without an expiry are not granted.
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Kyverno runs with 3 replicas spread across racks | `kubectl -n kyverno get pods -o wide` | 3, distinct racks |
| **A2** | Kyverno has no CPU limit and a PDB | Inspect the Deployment | Confirmed |
| **A3** | `resourceFilters` exclude kube-system and kyverno | Read the config | Present |
| **A4** | All policies pass their CLI tests | `kyverno test policies/tests/` | All pass |
| **A5** | **Allow-list exclusions are tested and skip correctly** | Test cases for all 8 entries | All `skip` |
| **A6** | Policies deployed in Audit mode first | `kubectl get cpol -o json \| jq -r '.items[].spec.validationFailureAction'` | All `Audit` initially |
| **A7** | **Audit report generated and every finding triaged** | `evidence/phase-16/audit-report.md` | Every finding has a decision |
| **A8** | Policies flipped to Enforce in groups, with dates recorded | Read the evidence | Staged, dated |
| **A9** | A privileged pod in a tenant namespace is rejected | Apply one | Rejected with a clear message |
| **A10** | The GPU Operator's privileged DaemonSet still runs | `kubectl -n gpu-operator get pods` | Running (exclusion works) |
| **A11** | PSA `restricted` is set on all tenant namespaces | Query labels | Present |
| **A12** | Mutation adds `seccompProfile: RuntimeDefault` | Create a pod without it | Present after admission |
| **A13** | Mutation sets `automountServiceAccountToken: false` | Same | Present |
| **A14** | A new namespace gets a default-deny NetworkPolicy generated | Create one | Policy appears |
| **A15** | Capsule tenant restricts namespace creation to its quota | Create 11 namespaces (quota 10) | 11th rejected |
| **A16** | Tenant cannot create cluster-scoped resources | Attempt a ClusterRole | Rejected |
| **A17** | Cross-tenant pod-to-pod traffic is denied | Curl from tenant A to tenant B | Denied; visible in `hubble observe --verdict DROPPED` |
| **A18** | DNS still works under default-deny | Resolve from a tenant pod | Works |
| **A19** | **Cilium policy audit mode was used before enforcement** | Evidence of the audit log review | Recorded |
| **A20** | `policyEnforcementMode: always` set only after baseline allows exist | Check the ordering in Git history | Correct order |
| **A21** | Required-label policy rejects a pod missing `nexus.io/owner` | Apply one | Rejected |
| **A22** | Policy runbook exists and covers the six common rejections | Read it | Complete |
| **A23** | PolicyException requires an expiry | Attempt one without | Rejected or flagged in review |

---

## ↩️ ROLLBACK

```bash
# Flip a single policy back to Audit (the safe, surgical move)
kubectl patch cpol <name> --type=merge -p '{"spec":{"validationFailureAction":"Audit"}}'
# Emergency: disable all Kyverno admission (⚠️ removes all enforcement)
kubectl delete validatingwebhookconfiguration kyverno-resource-validating-webhook-cfg
# Cilium policy back to audit
kubectl -n kube-system exec ds/cilium -- cilium-dbg config PolicyAuditMode=Enable
```
> ⚠️ Deleting the webhook configuration is a break-glass action. Argo CD will restore it on the next sync — which is correct, but means you must also suspend the Application if you need a longer window.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| **Everything is stuck Pending after enabling a policy** | An enforcing policy blocks a platform component | Flip that policy to Audit immediately; read the policy report; add the exclusion; re-test |
| Kyverno webhook timeouts cause API latency | Signature verification is a network call | Raise `webhookTimeoutSeconds`; ensure Harbor is reachable and fast; **this is the cost of ADR-026** |
| Cluster cannot recover after a full restart | Kyverno needs Kyverno to start | The `resourceFilters` exclusion prevents this. Verify it is present. |
| Policy report is empty | `background: true` not set, or the reports controller is unhealthy | Set it; check the controller |
| Mutation does not apply to existing pods | Mutations act at admission only | Restart the workload; use a background mutation policy for existing resources |
| Generated NetworkPolicy blocks legitimate traffic | The generated default is deny-all | Every namespace needs its explicit allow rules. Provide a template (`allow-egress-template.yaml`). |
| Tenant users see other tenants' namespaces | Capsule proxy not used, or RBAC too broad | Route tenant `kubectl` through the Capsule proxy; verify RoleBindings are namespace-scoped |
| Cilium policy drops traffic that should be allowed | Policy selector too narrow, or the identity is unexpected | `hubble observe --verdict DROPPED`; `cilium-dbg endpoint get <id>` to see the identity labels |
| Users cannot pull from Docker Hub | `require-registry` policy | Correct — use Harbor's proxy cache. Document the mirror path. |

---

## 🚫 DO NOT

- **Do not** deploy any policy directly in `Enforce`. Audit first, always.
- **Do not** flip all policy groups on the same day. One group, then soak.
- **Do not** add a privileged exclusion without an ADR and a security review in the same PR.
- **Do not** remove the `kube-system`/`kyverno` resource filters. That is the recovery deadlock guard.
- **Do not** set `policyEnforcementMode: always` before the baseline allow policies exist. It drops all traffic.
- **Do not** grant a PolicyException without an expiry date.
- **Do not** set a CPU limit on the Kyverno admission controller.
- **Do not** enforce `require-signed-images` before Phase 42 builds the signing pipeline — nothing would be signed and nothing would start.
- **Do not** create Kueue quotas here. Phase 30. Capsule's ResourceQuota is a different, complementary control.

---

## 📤 HANDOFF

`evidence/phase-16/handoff.md` must state:

1. **Which policies are enforcing and which are still auditing**, with the planned flip dates.
2. **The final privileged allow-list as implemented** — every later phase that adds a privileged component must amend it via ADR.
3. **The audit-report findings and their resolutions.**
4. **The tenant model** — Capsule tenant names, their groups, quotas, and node-pool restrictions. Phase 17 wires the Keycloak groups; Phase 30 sets the Kueue quotas.
5. **The default network policy posture** and the egress-allow template users need.
6. **Whether `policyEnforcementMode: always` is on**, and the audit evidence that preceded it.
7. **Mutation policies in place** — later phases add NCCL env injection (22) and dataset mounts (28) here.
8. **Any exception granted**, with its expiry.

---

## ➡️ NEXT

**[PHASE-17 — Ingress, Certificates & Identity](PHASE-17.md)** — expose the platform through Gateway API with real certificates and single sign-on, and verify gate G3.
