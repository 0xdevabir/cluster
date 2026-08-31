# PHASE 04 — Security Architecture & Threat Model

| | |
|---|---|
| **Stage** | 0 — Foundation & Design |
| **Estimated effort** | 3 hours |
| **Depends on** | 00 (can run in parallel with 01–03) |
| **Blocks** | 10, 16, 17, 55 |
| **Risk** | 🟡 Medium — design errors here surface as breaches later, not as build failures |
| **Blast radius** | Design only |
| **Architecture refs** | `ARCHITECTURE.md#x1--security-architecture`, ADR-023, ADR-026, R-12 |

---

## 🎯 MISSION

Define the **trust zones, threat model, PKI hierarchy, identity model, policy baseline, and the explicitly-audited list of privileged workloads** — before a single component is installed with a default configuration that nobody revisits.

> 💡 **WHY Phase 04 and not Phase 54.** Security retrofitted onto a running 100-node cluster is a migration project. Security designed before the first node boots is a set of defaults. The concrete difference: if you decide *now* that Kyverno fails closed on image signatures (ADR-026), then Phase 42's registry work builds a signing pipeline. If you decide it in Phase 55, you must re-sign and re-deploy every image in the fleet.

---

## ✅ PREFLIGHT

```bash
task validate                       # Phase 00 gates pass
test -f ARCHITECTURE.md && grep -q "X1 — Security Architecture" ARCHITECTURE.md && echo OK

# You must be able to answer:
#  · Who are the users? (individuals, teams, service accounts, CI systems)
#  · Is this cluster reachable from the internet? From a corporate LAN? Air-gapped?
#  · What is the most sensitive data that will ever touch it?
#  · What compliance regime, if any, applies? (none / internal policy / regulated)
#  · Who has physical access to the room?
```

---

## 📦 DELIVERABLES

```
docs/security/
  threat-model.md               # assets, actors, attack paths, mitigations
  trust-zones.md                # the zone diagram + inter-zone rules
  pki-hierarchy.md              # every CA, its scope, lifetime, and rotation
  identity-model.md             # users, groups, service accounts, RBAC mapping
  policy-baseline.md            # the Kyverno/PSA/NetworkPolicy rules to enforce
  privileged-workloads.md       # the audited exception list — THE key artifact
  secrets-taxonomy.md           # what kinds of secrets exist and where each lives
  incident-response.md          # who does what when something is wrong
policies/
  README.md                     # how policies are organized (implemented in Phase 16)
  _design/                      # policy specs as design docs, not yet applied
evidence/phase-04/{acceptance,handoff,deviations}.md
```

---

## 📋 TASKS

### Task 1 — Threat model

**`docs/security/threat-model.md`.** Use a simple asset → actor → path → mitigation structure. Do not import a heavyweight methodology; do be concrete.

**Assets, ranked:**

| # | Asset | Why it matters | Impact if lost/leaked |
|---|---|---|---|
| A1 | Training data & datasets | May be proprietary, licensed, or personal | 🔴 Confidentiality + legal |
| A2 | Trained model weights | The expensive output of the whole cluster | 🔴 IP loss |
| A3 | Cluster credentials (etcd, kubeconfig, Talos PKI) | Full cluster control | 🔴 Total compromise |
| A4 | Physical compute capacity | Cryptomining is the #1 opportunistic abuse of GPU clusters | 🟠 Cost + capacity theft |
| A5 | The power-control plane (PDUs) | Can hard-power-off the fleet | 🟠 Availability |
| A6 | Source code & CI credentials | Supply chain into the cluster | 🔴 R-12 |
| A7 | Tenant isolation | One team reading another's data | 🟠 Trust |
| A8 | Availability itself | The cluster is the team's productivity | 🟠 |

**Actors:**

| Actor | Capability | In scope? |
|---|---|---|
| Legitimate tenant, honest but careless | Runs code with their own credentials | ✅ Primary — most incidents are accidents |
| Legitimate tenant, curious | Tries to read another tenant's data | ✅ |
| Compromised container image / dependency | Arbitrary code with pod privileges | ✅ R-12 |
| Attacker with network access to the K8s API | Credential stuffing, unauthenticated endpoints | ✅ |
| Attacker on the corporate LAN | Lateral movement toward the cluster | ✅ |
| Internet-based scanner | Exposed Gateway/Argo/Grafana | ✅ |
| Attacker with physical access | Disk removal, console access, network tap | ⚠️ Partially — depends on the room |
| Nation-state / targeted APT | Everything | ❌ Out of scope; document the assumption |

**Attack paths and mitigations** (the table that actually drives work):

| # | Path | Mitigation | Phase |
|---|---|---|---|
| P1 | Malicious image → container escape → node root → cluster | Restricted PSA, no privileged pods (except the audited list), seccomp/AppArmor, Tetragon runtime enforcement, signed images only | 16, 42, 55 |
| P2 | Stolen kubeconfig → full cluster access | **No static kubeconfigs.** OIDC with short-lived tokens; MFA at Keycloak; RBAC least privilege | 17 |
| P3 | Tenant A's pod reaches Tenant B's service or PVC | Default-deny CiliumNetworkPolicy; per-tenant Ceph pools + CSI secrets; Capsule namespace ownership | 16, 27 |
| P4 | Exposed Argo CD / Grafana / Ray dashboard on the internet | Gateway API with OIDC auth in front of *everything*; nothing has its own auth exposed | 17 |
| P5 | Compromised CI writes to the GitOps repo → cluster change | Branch protection, signed commits, Argo CD sync policy review, CODEOWNERS on `clusters/` | 15 |
| P6 | Cryptomining in a legitimate-looking job | GPU utilization + network egress anomaly detection; egress policy allowing only known registries and datasets; showback surfaces the cost | 45, 34, 16 |
| P7 | Attacker reaches the PDU management network → powers off the cluster | Management VLAN not routable from tenant zones; VPN + jump host; SNMPv3 with credentials in Vault | 03, 07 |
| P8 | Data exfiltration via the pod network | Default-deny egress; explicit allow-lists per namespace; Hubble flow logs retained | 16, 45 |
| P9 | Disk removed from a decommissioned node | LUKS2 encryption of Talos `STATE`/`EPHEMERAL`; `dmcrypt` Ceph OSDs; documented secure-wipe procedure | 09, 27, 56 |
| P10 | **RDMA traffic sniffed on the fabric** | ⚠️ **Not encrypted (ADR-023).** Mitigated only by VLAN isolation + physical security. **This is an accepted risk — write it down explicitly.** | 03, 21 |
| P11 | etcd data read directly from disk | etcd encryption-at-rest for Secrets; disk encryption | 12 |
| P12 | Supply-chain: a base image gains a backdoor upstream | Harbor proxy cache + pinned digests + Trivy scanning + cosign verification | 42, 55 |

---

### Task 2 — Trust zones

**`docs/security/trust-zones.md`** — implement the diagram in `ARCHITECTURE.md#X1.1` and add the **inter-zone rule matrix**, which is what Phase 16 turns into policy.

| From ↓ / To → | Internet | Platform | Tenant | Data | Management |
|---|---|---|---|---|---|
| **Internet** | — | Gateway API only (443), OIDC-authenticated | ❌ | ❌ | ❌ |
| **Platform** | Registry pulls, ACME, NTP | ✅ | Read K8s API (scoped) | ✅ (backups) | ❌ |
| **Tenant** | ❌ by default; explicit allow-list per namespace | Registry, Vault (scoped), MLflow, metrics push | Same tenant ✅ / cross-tenant ❌ | Own pools/buckets only | ❌ |
| **Data** | ❌ | Metrics/logs push | ❌ (never initiates) | ✅ | ❌ |
| **Management** | ❌ (except vendor firmware, manually) | Provisioning API | ❌ | ❌ | ✅ |

**Rules that make this real:**
1. **Default deny in every direction.** A `CiliumClusterwideNetworkPolicy` denies all, and each allowance is an explicit, reviewed rule.
2. **The management zone is reachable only via VPN + jump host.** No route from any pod.
3. **Tenant egress to the internet is off by default.** Turning it on for a namespace is a reviewed change, because P8 and P6 both run through it.
4. **The data zone never initiates connections outward.** Ceph talks to clients that call it.

---

### Task 3 — PKI hierarchy

**`docs/security/pki-hierarchy.md`.** Multiple independent CAs, each with a bounded blast radius. Do not use one CA for everything.

```
┌─ NEXUS Root CA (offline, 10 y) ──────────────────────────────────────────┐
│   Key material: air-gapped, split custody, in a safe. Used ~annually.     │
│   Signs only intermediates.                                               │
└──┬───────────────────────────────────────────────────────────────────────┘
   │
   ├── Platform Intermediate CA (step-ca, 5 y)         [Phase 07]
   │     └── Service certs (90 d, auto-renewed via cert-manager)
   │          · Gateway API TLS (internal hostnames)
   │          · Harbor, Argo CD, Grafana, Keycloak, Vault
   │          · mTLS between platform services
   │
   ├── Kubernetes CA (managed by Talos, 10 y)          [Phase 09/12]
   │     ├── apiserver serving cert, apiserver-kubelet-client
   │     ├── kubelet client certs (auto-rotated, 1 y)
   │     ├── etcd peer/server/client CA (SEPARATE sub-CA)
   │     └── front-proxy CA (SEPARATE)
   │
   ├── Talos machine CA (managed by Talos)             [Phase 09]
   │     └── talosctl client certs (1 y, per-operator, revocable)
   │
   └── Public certs (Let's Encrypt via cert-manager)   [Phase 17]
         └── Only for externally-reachable hostnames
```

**Rotation and lifetime policy — write it as a table, because "we'll rotate it later" is how certificates expire at 3 a.m.:**

| Certificate | Lifetime | Rotation | Owner | Expiry alert |
|---|---|---|---|---|
| Root CA | 10 y | Manual ceremony | Security owner | 1 y before |
| Intermediates | 5 y | Manual, documented runbook | Platform | 6 mo before |
| Kubernetes CA | 10 y | Talos upgrade path | Platform | 1 y before |
| Service certs | 90 d | cert-manager, automatic | Automated | 21 d before |
| Kubelet client | 1 y | Automatic (`RotateKubeletServerCertificate`) | Automated | 30 d |
| talosctl operator | 1 y | Manual reissue per operator | Platform | 30 d |
| SSH (jump host) | 1 y | Manual, per-person | Platform | 30 d |
| Service account tokens | 1 h | Bound tokens, automatic | Automated | — |

> ⚠️ **Every certificate in this table gets a Prometheus expiry alert in Phase 47.** Certificate expiry is the single most common cause of self-inflicted cluster outages.

---

### Task 4 — Identity model

**`docs/security/identity-model.md`.**

```
Keycloak realm: nexus
├── Groups (source of truth for authorization)
│   ├── nexus-platform-admins     → cluster-admin (break-glass, MFA required, audited)
│   ├── nexus-platform-operators  → cluster-wide read + platform namespace write
│   ├── nexus-security            → read-all + policy write + audit log read
│   ├── team-<name>-admins        → Capsule tenant owner (namespace CRUD within tenant)
│   ├── team-<name>-members       → edit within the tenant's namespaces
│   └── nexus-viewers             → read-only, no secrets
├── Users
│   └── Federated from the corporate IdP where one exists; local otherwise. MFA mandatory
│       for anything above nexus-viewers.
└── Clients (OIDC)
    ├── kubernetes-api      (public client, PKCE, for kubectl via oidc-login)
    ├── argocd, grafana, harbor, backstage, mlflow, ray-dashboard, jupyterhub
    └── ci-runner           (confidential client, client_credentials, tightly scoped)
```

**RBAC principles:**

| Principle | Implementation |
|---|---|
| No human has standing `cluster-admin` | Break-glass account, MFA, use is alerted on and reviewed |
| Groups grant roles, never individuals | RoleBindings reference `Group`, never `User` |
| Tenants cannot create ClusterRoles or bind cluster-scoped roles | Capsule + Kyverno block it |
| Service accounts are per-workload, never shared | Kyverno rejects pods using `default` SA |
| `automountServiceAccountToken: false` by default | Kyverno mutation; opt in explicitly |
| Secrets read access is separately granted | Never bundled into `edit` |
| Every `exec`/`portforward` into a pod is audited | Audit policy at `RequestResponse` for those verbs |

**The break-glass procedure** (write it now, test it in Phase 54):
1. Static admin kubeconfig exists, encrypted with SOPS, stored offline in two places.
2. Using it triggers an alert (audit-log rule on the cert's CN).
3. Every use is followed by a written incident note and, if the credential was exposed, rotation of the Kubernetes CA.

---

### Task 5 — The privileged workload list (the most important artifact here)

**`docs/security/privileged-workloads.md`.** Restricted Pod Security Admission is the baseline; every exception must be named, justified, scoped, and reviewed.

| # | Workload | Namespace | Exception needed | Why it is unavoidable | Scoping | Review |
|---|---|---|---|---|---|---|
| 1 | Cilium agent | `kube-system` | `privileged`, host network, `NET_ADMIN`, `SYS_MODULE`, bpf mounts | Programs eBPF into the kernel datapath | DaemonSet, image digest-pinned, signed | Quarterly |
| 2 | NVIDIA GPU Operator (driver + toolkit + device plugin) | `gpu-operator` | `privileged`, host paths, `SYS_ADMIN` | Loads kernel modules, manages `/dev/nvidia*` | Node-selected to GPU nodes only | Quarterly |
| 3 | Rook-Ceph OSD | `storage-ceph` | `privileged`, host devices | Raw block device access | Storage nodes only, tainted | Quarterly |
| 4 | SR-IOV device plugin + Multus | `network-operator` | `privileged`, host network, host paths | Manipulates NIC VFs and pod netns | RDMA-capable nodes only | Quarterly |
| 5 | node-problem-detector | `obs-health` | host paths (read-only), host PID | Reads kernel logs and hardware state | Read-only mounts | Quarterly |
| 6 | Spegel | `kube-system` | containerd socket access | P2P image mirroring requires the runtime socket | Read-mostly; **highest-risk exception** — a containerd socket is root-equivalent | **Monthly** |
| 7 | Mayastor I/O engine | `storage-mayastor` | `privileged`, hugepages, host devices | SPDK user-space NVMe driver | Storage-capable nodes only | Quarterly |
| 8 | Tetragon | `kube-system` | `privileged`, bpf | It is the runtime security enforcer | — | Quarterly |

**Rules for this list:**
- **It is closed.** Adding an entry requires an ADR and a security review, in the same PR.
- Kyverno enforces it: a `privileged` pod in a namespace not on this list is **rejected**, not warned.
- Every entry pins an image *digest*, not a tag.
- Every entry is reviewed on its stated cadence, and the review is recorded in `evidence/`.

> 💡 **Why enumerate this in Phase 04 rather than discovering it during install:** each of these components' Helm charts will happily request `privileged: true` by default. Without a pre-agreed list, you approve them one at a time under time pressure and end up with fifteen privileged DaemonSets nobody can justify.

---

### Task 6 — Policy baseline

**`docs/security/policy-baseline.md`** — the specification Phase 16 implements. Write each rule with its enforcement mode and failure behavior.

| # | Rule | Mode | Fails |
|---|---|---|---|
| S1 | All tenant namespaces have `pod-security.kubernetes.io/enforce=restricted` | Enforce | Pod rejected |
| S2 | No pod runs as UID 0 unless on the privileged list | Enforce | Pod rejected |
| S3 | All images come from Harbor (`harbor.nexus.internal/*`) or an allow-listed mirror | Enforce | Pod rejected |
| S4 | **All images have a valid cosign signature** | **Enforce, fail-closed (ADR-026)** | Pod rejected; a Kyverno outage blocks new pods — accepted deliberately |
| S5 | All images are referenced by digest in `prod-*` namespaces | Enforce | Pod rejected |
| S6 | Required labels present (`owner`, `team`, `cost-center`, `workload-class`) | Enforce | Pod rejected |
| S7 | `automountServiceAccountToken: false` unless explicitly opted in | Mutate | Silently corrected |
| S8 | Default-deny NetworkPolicy generated for every new namespace | Generate | Namespace is isolated on creation |
| S9 | Resource requests and limits are set on every container | Enforce (warn in dev, enforce in prod) | Pod rejected |
| S10 | `hostNetwork`, `hostPID`, `hostIPC` forbidden outside the privileged list | Enforce | Pod rejected |
| S11 | `hostPath` volumes forbidden outside the privileged list | Enforce | Pod rejected |
| S12 | No `NET_RAW` capability (blocks trivial packet crafting) | Enforce | Pod rejected |
| S13 | `seccompProfile: RuntimeDefault` required | Mutate + enforce | Corrected, then required |
| S14 | Tenants may not create ClusterRole/ClusterRoleBinding | Enforce | Rejected |
| S15 | Egress to the internet requires an explicit per-namespace policy | Enforce (default-deny) | Traffic dropped |
| S16 | GPU pods must declare `nexus.io/vram-request` | Enforce | Pod rejected (needed by Phase 19's shared-GPU accounting) |

**The dev/prod split:** run every new rule in `Audit` mode for two weeks, review the violation report, then flip to `Enforce`. Record the flip date per rule. Turning on 16 enforcing policies simultaneously in a running cluster is a self-inflicted outage.

---

### Task 7 — Secrets taxonomy

**`docs/security/secrets-taxonomy.md`** — every kind of secret, where it lives, who can read it, how it rotates.

| Class | Examples | Storage | Consumer | Rotation |
|---|---|---|---|---|
| **Bootstrap** | Talos machine secrets, cluster CA bundle, initial age key | **SOPS + age**, committed encrypted; age key offline in two places | Phase 09/12 tooling | On CA rotation |
| **Platform** | Harbor admin, Grafana admin, Keycloak client secrets | OpenBao KV v2, synced to K8s by External Secrets Operator | Platform services | 90 d |
| **Dynamic** | Database credentials, Ceph CSI keys, S3 keys | OpenBao **dynamic** secrets engines — issued per-lease | Applications | Per-lease (1–24 h) |
| **PKI** | Service certs | OpenBao PKI engine / cert-manager | Services | 90 d |
| **Tenant** | Team API keys, dataset access tokens | OpenBao, per-tenant path, tenant-scoped policy | Tenant workloads | Tenant-defined, max 1 y |
| **Infrastructure** | PDU SNMPv3, switch AAA, PiKVM | OpenBao, platform-admin path only | Automation on the seed node | 180 d |
| **CI** | Registry push tokens, Git deploy keys | CI provider's secret store + OpenBao, short-lived | Pipelines | 30 d |

**Non-negotiables:**
- No secret is ever committed in plaintext (Rule 9). `detect-secrets` + `gitleaks` enforce it in CI (Phase 00).
- The age private key that decrypts the bootstrap bundle is the **root of trust for the whole cluster**. It lives offline, in two physically separate places, and is never on a laptop.
- Kubernetes `Secret` objects are encrypted at rest in etcd (`EncryptionConfiguration`, Phase 12).
- Every secret has a named owner and a rotation date.

---

### Task 8 — Incident response

**`docs/security/incident-response.md`** — short and actionable, not a policy binder.

```
SEVERITY
  SEV1  Confirmed compromise of the control plane, or data exfiltration
  SEV2  Suspected compromise, tenant isolation breach, credential exposure
  SEV3  Policy violation, vulnerability requiring urgent patching
  SEV4  Hygiene finding

FIRST FIVE ACTIONS (SEV1/SEV2)
  1. Declare. Open an incident channel. Name an incident commander.
  2. PRESERVE — do not reboot, do not delete pods. Snapshot: audit logs, Hubble
     flows, `kubectl get events`, node dmesg, the pod spec, the image digest.
  3. CONTAIN — apply a deny-all NetworkPolicy to the affected namespace;
     cordon affected nodes; suspend the Argo CD app so nothing re-creates the workload.
  4. ASSESS — what credentials could the compromised identity reach?
     Assume everything it could read is exposed.
  5. ROTATE — every credential in the blast radius. Use the taxonomy table above
     to enumerate them.

RECOVERY
  · Reimage affected nodes (Law IV — never "clean" a compromised node)
  · Rebuild from Git, not from the running state
  · Rotate the Kubernetes CA if any control-plane credential was exposed

POST-INCIDENT
  · Blameless writeup within 5 working days
  · Every action item becomes a tracked change with an owner
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | Threat model names ≥ 8 assets, ≥ 6 actors, ≥ 12 attack paths, each with a mitigation and a phase | Read `threat-model.md` | Counts met; no path without a mitigation |
| **A2** | Out-of-scope threats are stated explicitly | Read it | Present (e.g. targeted APT, physical) |
| **A3** | Trust zone matrix is complete (5×5) | Read `trust-zones.md` | Every cell has a value |
| **A4** | PKI hierarchy names every CA with lifetime, rotation, and owner | Read `pki-hierarchy.md` | All 8 rows in the rotation table |
| **A5** | Identity model maps every group to concrete RBAC | Read `identity-model.md` | Every group has a role |
| **A6** | Break-glass procedure is documented with an alerting mechanism | Read it | Present, testable |
| **A7** | Privileged workload list is complete and each entry is justified and scoped | Read `privileged-workloads.md` | Every entry has all six columns |
| **A8** | Policy baseline has ≥ 16 rules with mode and failure behavior | Read `policy-baseline.md` | Table complete |
| **A9** | ADR-026 (fail-closed signature verification) is acknowledged with its availability trade-off | Grep the doc | Explicit statement present |
| **A10** | ADR-023 (unencrypted RDMA) is documented as an accepted risk | Grep the doc | Explicit acceptance with mitigations |
| **A11** | Secrets taxonomy covers all seven classes with storage, consumer, rotation | Read `secrets-taxonomy.md` | Table complete |
| **A12** | The age-key custody plan is written | Read it | Two locations named, laptop storage forbidden |
| **A13** | Incident response has severity levels and the first five actions | Read `incident-response.md` | Present |
| **A14** | Every mitigation references the phase that implements it | Grep for "Phase" in the docs | ≥ 90 % of mitigations have a phase |

---

## ↩️ ROLLBACK

Documents only. `git revert`.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| The threat model feels like theater | Written abstractly | Anchor every path in a concrete command an attacker would run. If you cannot name the command, the path is not real. |
| The privileged list keeps growing during install | Charts default to privileged | Each addition must come with an ADR. Most charts have a less-privileged mode; look for it before granting. |
| Fail-closed signature verification blocks the whole cluster | Kyverno unavailable | This is the designed trade-off (ADR-026). Mitigate with 3 Kyverno replicas + PDB, and a documented emergency policy-bypass procedure that requires break-glass. |
| Users complain about MFA | Friction | MFA is required above `nexus-viewers` only. Read-only access stays frictionless. |
| Cannot decide on air-gapped vs. connected | Unclear requirement | Design for connected with a **local mirror for everything** (Harbor, Gitea). That configuration also works air-gapped with one switch flipped. |
| Egress default-deny breaks every workload | Legitimate — most jobs pull from PyPI/HuggingFace | Provide an allow-listed egress policy template covering the standard registries, and a documented request path for anything else |

---

## 🚫 DO NOT

- **Do not** install Kyverno, Keycloak, Vault, or cert-manager here. This phase is design. Implementation is Phases 10, 16, 17.
- **Do not** generate any real keys or certificates. Phase 10 does that with the proper custody procedure.
- **Do not** write Kyverno policy YAML yet — write the *specification*. Phase 16 translates it, with the cluster available to test against.
- **Do not** design for a threat model you do not have. A research cluster in a locked room does not need nation-state resistance; pretending otherwise wastes effort that should go into the threats you do face (P1, P4, P6).
- **Do not** plan to encrypt RDMA traffic. It costs line rate. Accept the risk explicitly (ADR-023) or change the fabric design.
- **Do not** defer the privileged-workload list. It is the difference between a hardened cluster and a collection of root DaemonSets.

---

## 📤 HANDOFF

`evidence/phase-04/handoff.md` must state:

1. **Trust zone decisions** — Phase 16 turns the matrix into `CiliumNetworkPolicy`; Phase 03's VLAN separation must match it.
2. **The PKI hierarchy** — Phase 07 stands up step-ca as the Platform Intermediate; Phase 09 configures Talos's CAs; Phase 17 configures cert-manager.
3. **Group names and their RBAC mapping** — Phase 17 configures Keycloak with exactly these groups.
4. **The privileged workload list** — Phase 16's Kyverno policy allow-list is built from it verbatim.
5. **The policy baseline with enforcement modes and the audit→enforce schedule** — Phase 16 implements it.
6. **Secrets taxonomy** — Phase 10 builds the SOPS/age chain and the Vault path layout from it.
7. **Accepted risks**, each with its ADR reference — so nobody "discovers" them later as findings.
8. **Whether the environment is air-gapped**, since it changes Phases 07, 42, and 55.

---

## ➡️ NEXT

**[PHASE-05 — Capacity Model & Stage-0 Gate](PHASE-05.md)** — consolidate every finding from Phases 01–04 into a sizing model, a purchase list, and the G0/G1 gate decision that authorizes the build.
