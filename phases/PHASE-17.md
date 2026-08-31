# PHASE 17 — Ingress, Certificates & Identity

| | |
|---|---|
| **Stage** | 2 — Kubernetes Substrate (**exit gate**) |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 16 |
| **Blocks** | All of Stage 3; every platform service that needs a URL or a login |
| **Risk** | 🟠 High — OIDC misconfiguration can lock everyone (including you) out of the API |
| **Blast radius** | All external access and authentication |
| **Architecture refs** | `ARCHITECTURE.md#l9--platform-services`, `#x1--security-architecture`, Phase 04 `identity-model.md`, `pki-hierarchy.md`, G3 |

---

## 🎯 MISSION

Give the platform a **front door and an identity**: Gateway API with Envoy for north-south traffic, cert-manager issuing certificates from the Phase 07 CA, Keycloak as the single OIDC provider for Kubernetes and every service, OpenBao deployed and unsealed — and verify **gate G3** (the control plane survives losing any single node with < 30 s of API disruption).

> ⚠️ **DANGER — OIDC lockout.** Adding `--oidc-*` flags to the API server without a working Keycloak, or with a wrong `issuer-url`, means no one can authenticate. **Keep the break-glass kubeconfig (Phase 10) at hand and tested before you touch the API server flags.** Configure and verify Keycloak first; add API-server OIDC last.

> 💡 **WHY one identity provider for everything:** the alternative is nine services with nine password databases and no way to revoke a departing person's access in one action. Every service in `ARCHITECTURE.md#L9` — Argo CD, Grafana, Harbor, Backstage, MLflow, JupyterHub, Ray dashboard, Hubble UI, and `kubectl` itself — authenticates against Keycloak. Offboarding becomes one click.

---

## ✅ PREFLIGHT

```bash
argocd app get root                             # Synced/Healthy
kubectl get cpol                                # Phase 16 policies present

# The CA from Phase 07 is reachable and healthy
curl -fsS https://ca.nexus.internal:9000/health

# 🔒 The break-glass kubeconfig is present AND TESTED
sops --decrypt bootstrap/break-glass-kubeconfig.enc.yaml > "$TMPDIR/bg.yaml"
KUBECONFIG="$TMPDIR/bg.yaml" kubectl get nodes && echo "BREAK-GLASS OK"
shred -u "$TMPDIR/bg.yaml"

# A LoadBalancer IP pool exists (Phase 13)
kubectl get ciliumloadbalancerippools

# DNS can be updated for *.apps.nexus.internal (Phase 07)
dig +short @10.100.0.10 test.apps.nexus.internal
```

**If the break-glass kubeconfig does not work, STOP.** It is the only recovery path from an OIDC misconfiguration.

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/infra/cert-manager/
  values.yaml  clusterissuer-nexus-ca.yaml  clusterissuer-letsencrypt.yaml
clusters/nexus-prod/infra/gateway/
  values.yaml                       # Envoy Gateway
  gatewayclass.yaml  gateway.yaml
  httproute-template.yaml
  securitypolicy-oidc.yaml
clusters/nexus-prod/platform/keycloak/
  values.yaml  realm-nexus.yaml     # realm-as-code
  clients/*.yaml  groups.yaml
clusters/nexus-prod/platform/openbao/
  values.yaml  init-job.yaml  policies/  auth-kubernetes.yaml
clusters/nexus-prod/infra/external-secrets/
  values.yaml  clustersecretstore.yaml
talos/patches/archetype-control.yaml   # + oidc flags (applied LAST)
docs/operations/
  identity-runbook.md               # onboarding, offboarding, lockout recovery
  certificate-runbook.md
  ingress-guide.md                  # how to expose a service
tools/identity/
  kubectl-oidc-setup.sh
  verify-sso.sh
evidence/phase-17/
  g3-control-plane-ha.md            # ⚠️ THE GATE EVIDENCE
  acceptance.md handoff.md deviations.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Source |
|---|---|---|
| cert-manager | `1.16.2` | `oci://quay.io/jetstack/charts/cert-manager` |
| Envoy Gateway | `1.2.4` | `oci://docker.io/envoyproxy/gateway-helm` |
| Gateway API CRDs | `1.2.1` | standard channel |
| Keycloak (operator or chart) | `26.0.7` | `oci://registry-1.docker.io/bitnamicharts/keycloak` `24.4.6` |
| CloudNativePG (Keycloak DB) | `1.24.1` | `oci://ghcr.io/cloudnative-pg/charts/cloudnative-pg` |
| OpenBao | `2.1.0` | `oci://ghcr.io/openbao/charts/openbao` `0.10.0` |
| External Secrets Operator | `0.11.0` | `oci://ghcr.io/external-secrets/charts/external-secrets` |
| kubelogin (`kubectl oidc-login`) | `1.31.0` | client-side |

---

## 📋 TASKS

### Task 1 — cert-manager and the ClusterIssuers

**`clusterissuer-nexus-ca.yaml`** — ACME against the Phase 07 step-ca:
```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: { name: nexus-internal-ca }
spec:
  acme:
    server: https://ca.nexus.internal:9000/acme/acme/directory
    email: platform@nexus.internal
    privateKeySecretRef: { name: nexus-ca-acme-account }
    caBundle: <base64 of the NEXUS root CA cert>   # so cert-manager trusts our CA
    solvers:
      - http01: { gatewayHTTPRoute: { parentRefs: [ { name: nexus-gateway, namespace: platform-gateway, kind: Gateway } ] } }
```

Add a Let's Encrypt issuer **only** for hostnames that are genuinely internet-reachable. Internal-only names must use the internal CA — a public CA cannot validate a name that does not resolve publicly, and trying wastes an hour every time.

**Distribute the root CA to every node** so pods and the OS trust internally-issued certificates. Talos does this via `machine.files` (Phase 09 `_base.yaml`); verify it landed.

---

### Task 2 — Gateway API with Envoy

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata: { name: nexus }
spec: { controllerName: gateway.envoyproxy.io/gatewayclass-controller }
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: nexus-gateway
  namespace: platform-gateway
  annotations:
    nexus.io/advertise: "true"        # ⚠️ required for Cilium BGP (Phase 13)
    nexus.io/lb-pool: "default"
spec:
  gatewayClassName: nexus
  listeners:
    - name: https
      protocol: HTTPS
      port: 443
      hostname: "*.apps.nexus.internal"
      tls:
        mode: Terminate
        certificateRefs: [ { name: apps-wildcard-tls } ]
      allowedRoutes:
        namespaces: { from: Selector, selector: { matchLabels: { nexus.io/gateway-access: "true" } } }
    - name: http
      protocol: HTTP
      port: 80
      hostname: "*.apps.nexus.internal"
      # redirects to HTTPS via an HTTPRoute filter
```

> 💡 **`allowedRoutes.namespaces.from: Selector`** means a namespace must be explicitly labeled to attach routes to the shared Gateway. Without it, any tenant could expose any service on the platform's front door.

**The standard exposure pattern** (`docs/operations/ingress-guide.md` — every later phase copies it):
```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata: { name: grafana, namespace: obs-metrics }
spec:
  parentRefs: [ { name: nexus-gateway, namespace: platform-gateway } ]
  hostnames: [ "grafana.apps.nexus.internal" ]
  rules:
    - backendRefs: [ { name: grafana, port: 80 } ]
```

---

### Task 3 — Keycloak

**Deploy with a real database.** Keycloak on an embedded H2 database is a demo, not a platform component — losing it loses every client secret and group mapping.

```yaml
# CloudNativePG cluster for Keycloak
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: { name: keycloak-db, namespace: platform-identity }
spec:
  instances: 3
  storage: { size: 20Gi, storageClass: nexus-fast-block }   # TODO(phase-26): Mayastor
  backup:
    barmanObjectStore: { destinationPath: "s3://nexus-backup/keycloak" }  # TODO(phase-28)
```

**Realm as code** — the realm, clients, groups, and mappers are Git-managed, imported at startup, and reconciled. Do not click through the admin UI; a hand-configured realm is a snowflake that cannot be rebuilt.

**`realm-nexus.yaml`** must define, from Phase 04's identity model:

| Object | Values |
|---|---|
| Realm | `nexus` |
| Groups | `nexus-platform-admins`, `nexus-platform-operators`, `nexus-security`, `nexus-viewers`, `team-<name>-admins`, `team-<name>-members` |
| Group mapper | Adds a `groups` claim to tokens — **required for Kubernetes RBAC** |
| Client: `kubernetes` | Public, PKCE, redirect `http://localhost:8000` and `http://localhost:18000` (kubelogin) |
| Client: `argocd`, `grafana`, `harbor`, `backstage`, `mlflow`, `jupyterhub`, `ray-dashboard`, `hubble-ui` | Confidential; secrets stored in OpenBao and delivered by ESO |
| MFA | Required for every group above `nexus-viewers` |
| Token lifetimes | Access 5 min, refresh 30 min, SSO session 8 h |
| Federation | To the corporate IdP where one exists |

> ⚠️ **The `groups` claim mapper is the single most-forgotten step.** Without it, Kubernetes receives a token with no group information, every RBAC binding on `Group` fails to match, and authenticated users get "forbidden" for everything. Configure it, then verify by decoding an actual token.

---

### Task 4 — Kubernetes OIDC (do this last, with break-glass ready)

```yaml
# talos/patches/archetype-control.yaml — the addition
cluster:
  apiServer:
    extraArgs:
      oidc-issuer-url: "https://keycloak.apps.nexus.internal/realms/nexus"
      oidc-client-id: "kubernetes"
      oidc-username-claim: "preferred_username"
      oidc-username-prefix: "oidc:"      # ⚠️ prevents collision with system: accounts
      oidc-groups-claim: "groups"
      oidc-groups-prefix: "oidc:"
      oidc-ca-file: /etc/ssl/certs/nexus-root-ca.pem
```

**Apply to ONE control node first.** Verify, then roll to the others.

```bash
# 1. Apply to node 1 only
bash tools/talos/apply-config.sh nx-m-r01-01

# 2. Verify OIDC works against that node's API directly
kubectl --server=https://10.200.0.11:6443 --token="$(oidc-token)" get nodes

# 3. Verify break-glass STILL works
KUBECONFIG=bg.yaml kubectl get nodes

# 4. Only then roll to nodes 2 and 3
```

**RBAC bindings for the OIDC groups:**
```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: { name: nexus-platform-operators }
subjects: [ { kind: Group, name: "oidc:nexus-platform-operators", apiGroup: rbac.authorization.k8s.io } ]
roleRef: { kind: ClusterRole, name: view, apiGroup: rbac.authorization.k8s.io }
```

> ⚠️ **No standing `cluster-admin` binding for a human group** (Phase 04). `nexus-platform-admins` gets an elevated but not unlimited role; true cluster-admin is the break-glass certificate, and its use alerts.

**Client setup** — `tools/identity/kubectl-oidc-setup.sh` generates a kubeconfig using `kubelogin`:
```yaml
users:
  - name: oidc
    user:
      exec:
        apiVersion: client.authentication.k8s.io/v1beta1
        command: kubectl
        args:
          - oidc-login, get-token
          - --oidc-issuer-url=https://keycloak.apps.nexus.internal/realms/nexus
          - --oidc-client-id=kubernetes
          - --oidc-extra-scope=groups,email,profile
```

---

### Task 5 — OpenBao

Deploy per the Phase 10 design, with HA Raft storage.

```yaml
server:
  ha: { enabled: true, replicas: 3, raft: { enabled: true } }
  dataStorage: { size: 20Gi, storageClass: nexus-fast-block }   # TODO(phase-26)
  affinity: |
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - topologyKey: nexus.io/rack
          labelSelector: { matchLabels: { app.kubernetes.io/name: openbao } }
```

**Initialization — a ceremony, like the age key:**
```bash
bao operator init -key-shares=5 -key-threshold=3
# 🔒 Five unseal-key shares + a root token.
#    · Distribute the shares to FIVE DIFFERENT people/locations. Never together.
#    · Any three can unseal. No one person can.
#    · REVOKE the root token after configuring OIDC auth: `bao token revoke <root>`
#    · Record the ceremony (participants, date, share custody) in evidence/ — never the shares.
```

> 💡 **Auto-unseal:** without it, every OpenBao restart requires three humans. With a cloud KMS this is solved trivially; on-premises, options are a Transit unseal from a second small OpenBao instance, or a TPM-backed approach. **Decide now** — discovering the manual-unseal requirement during a 3 a.m. restart is a bad time. Record the decision.

**Configure**: Kubernetes auth backend, the path layout from Phase 10, policies, the PKI engine, and the database secrets engine. Then wire ESO's `ClusterSecretStore` and migrate each platform service's credentials from SOPS to Vault.

---

### Task 6 — SSO for every service

**`securitypolicy-oidc.yaml`** — Envoy Gateway enforces OIDC in front of services that lack good native auth (Hubble UI, Ray dashboard, Prometheus):

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata: { name: require-oidc, namespace: platform-gateway }
spec:
  targetRefs: [ { group: gateway.networking.k8s.io, kind: Gateway, name: nexus-gateway } ]
  oidc:
    provider:
      issuer: "https://keycloak.apps.nexus.internal/realms/nexus"
    clientID: "gateway-proxy"
    clientSecret: { name: gateway-oidc-secret }
    scopes: [ openid, profile, email, groups ]
    redirectURL: "https://auth.apps.nexus.internal/oauth2/callback"
    logoutPath: "/logout"
```

**`tools/identity/verify-sso.sh`** — asserts every service actually requires authentication:
```bash
for svc in argocd grafana harbor backstage mlflow jupyterhub ray hubble prometheus; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' "https://$svc.apps.nexus.internal/")
  # 302 (redirect to Keycloak) or 401 = protected.  200 = ⚠️ UNPROTECTED
  [[ "$code" =~ ^(302|401|403)$ ]] && ok "$svc protected ($code)" \
    || { warn "$svc UNPROTECTED ($code)"; fail=1; }
done
```

> ⚠️ **Run this after every new service is exposed.** An unauthenticated Ray dashboard or Prometheus is a full read of the cluster's internals, and it is the most common way a private platform leaks (Phase 04, P4).

---

### Task 7 — 📊 Gate G3: control-plane HA

**G3: the control plane survives the loss of any single control node with < 30 s of API disruption.**

**`evidence/phase-17/g3-control-plane-ha.md`**

```bash
# Continuous API probe from the seed node (out-of-band)
while true; do
  s=$(date +%s.%N)
  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 2 https://api.nexus.internal:6443/healthz)
  printf '%s %s %.3f\n' "$(date -Is)" "$code" "$(echo "$(date +%s.%N) - $s" | bc)"
  sleep 0.5
done | tee evidence/phase-17/g3-probe.log &

# Also: a running workload that must NOT be disturbed
kubectl run g3-witness --image=busybox --restart=Never -- sh -c 'while true; do date; sleep 1; done'

# ── Test 1: graceful shutdown of the VIP holder ──
tools/power/power-ctl.sh off <vip-holder>
#   measure: seconds of non-200 responses

# ── Test 2: hard power cut of a non-VIP member ──
tools/power/power-ctl.sh cycle <other-ctrl>

# ── Test 3: hard power cut of the VIP holder ──
tools/power/power-ctl.sh cycle <vip-holder>
```

**Record:**

| Test | API downtime | etcd quorum | Workload impact | Verdict |
|---|---|---|---|---|
| T1 graceful shutdown, VIP holder | | retained | | |
| T2 hard cut, non-VIP member | | retained | | |
| T3 hard cut, VIP holder | | retained | | |
| T4 all three back online | | restored | | |

**Additional required observations:**
- **`g3-witness` pod logs must show no gap.** A control-plane outage must not disturb a running workload (`ARCHITECTURE.md#0.2` design invariant). If the pod restarted, that is a G3 failure regardless of API timing.
- Did Argo CD, Keycloak, and the Gateway survive? (They run on infra nodes, so they should.)
- How long until the failed node rejoined and etcd was fully healthy again?

---

### Task 8 — Identity runbook

**`docs/operations/identity-runbook.md`**

| Procedure | Steps |
|---|---|
| **Onboard a person** | Create/federate the Keycloak user → add to groups → they run `kubectl-oidc-setup.sh` → verify with `kubectl auth whoami` |
| **Offboard a person** | Disable the Keycloak user (**one action revokes everything**) → revoke active sessions → rotate any shared secret they held → remove from CODEOWNERS and Git access |
| **Grant temporary elevation** | Add to a time-boxed group; a scheduled job removes membership at expiry; the change is audited |
| **🔒 OIDC lockout recovery** | 1. Retrieve break-glass (Phase 10). 2. `KUBECONFIG=bg.yaml kubectl ...`. 3. Fix Keycloak or revert the API-server OIDC flags via `talosctl`. 4. Verify. 5. Incident note. |
| **Keycloak is down** | Existing tokens work until expiry (5 min). Break-glass for anything urgent. Restore Keycloak (its DB is backed up). |
| **Rotate a client secret** | Keycloak → OpenBao → ESO refresh → restart the consuming service |
| **Certificate expiring** | cert-manager renews at 2/3 of lifetime automatically. If it did not: check the Certificate's status conditions and the ACME order. |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | cert-manager issues a certificate from the internal CA | Create a test Certificate | Ready within 60 s |
| **A2** | The issued cert chains to the NEXUS root | `openssl verify` | Valid |
| **A3** | Nodes trust the internal CA | `curl` an internal HTTPS endpoint from a pod without `-k` | Succeeds |
| **A4** | Gateway has a LoadBalancer IP advertised by BGP | `kubectl get gateway`; `cilium bgp routes` | IP assigned and advertised |
| **A5** | An HTTPRoute exposes a service over HTTPS with a valid cert | `curl https://<svc>.apps.nexus.internal` | 200, valid TLS |
| **A6** | HTTP redirects to HTTPS | `curl -I http://...` | 301/308 |
| **A7** | A namespace without the access label cannot attach a route | Try it | Route not accepted |
| **A8** | Keycloak is running with a 3-instance Postgres cluster | `kubectl -n platform-identity get pods` | Healthy |
| **A9** | Realm, groups, and clients are Git-managed and importable | Delete and re-import the realm | Restored identically |
| **A10** | **The `groups` claim is present in issued tokens** | Decode a token | `groups` array present |
| **A11** | `kubectl` authenticates via OIDC | `kubectl auth whoami` | Shows `oidc:<user>` and `oidc:<groups>` |
| **A12** | RBAC binds correctly to OIDC groups | As a `nexus-viewers` member, try to create a pod | Forbidden |
| **A13** | **No human group has standing `cluster-admin`** | `kubectl get clusterrolebindings -o json \| jq` | None |
| **A14** | 🔒 **Break-glass still works after OIDC is enabled** | Test it | Works |
| **A15** | Break-glass use fires an alert | Use it; check | Alert (or the audit rule that will alert in Phase 47) |
| **A16** | OpenBao is initialized, unsealed, HA, root token revoked | `bao status`; `bao token lookup <root>` | 3 nodes, unsealed; root invalid |
| **A17** | Unseal-key ceremony recorded (participants, not shares) | Read the evidence | Present |
| **A18** | Auto-unseal decision recorded and implemented, or the manual procedure documented | Read it | Explicit |
| **A19** | ESO syncs a secret from OpenBao into a namespace | Create an ExternalSecret | Secret appears with the correct value |
| **A20** | **Every exposed service requires authentication** | `verify-sso.sh` | No 200 without auth |
| **A21** | 📊 **G3: single control-node loss < 30 s API disruption, ×3 tests** | `g3-control-plane-ha.md` | All three within budget |
| **A22** | **G3: the witness workload was not disturbed** | Pod logs | No restart, no gap |
| **A23** | Identity runbook covers onboarding, offboarding, and lockout recovery | Read it | Complete |

---

## ↩️ ROLLBACK

```bash
# ⚠️ OIDC lockout — the most likely emergency
sops --decrypt bootstrap/break-glass-kubeconfig.enc.yaml > "$TMPDIR/bg.yaml"
export KUBECONFIG="$TMPDIR/bg.yaml"
# Remove the OIDC flags and re-apply the control-plane config
talosctl --nodes <ctrl-ips> apply-config --file talos/rendered/<node>.yaml

# Gateway rollback
argocd app rollback envoy-gateway

# ⚠️ OpenBao: sealing is safe; losing the unseal shares is not.
bao operator seal
```

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| `kubectl` says "Unauthorized" after enabling OIDC | Issuer URL mismatch (trailing slash), or the API server cannot reach Keycloak | The issuer must match the token's `iss` **exactly**. Check API-server logs via `talosctl logs kube-apiserver`. |
| Authenticated but "forbidden" for everything | The `groups` claim is missing or the prefix does not match | Decode the token; verify the group mapper; verify `oidc-groups-prefix` matches the RBAC subject names |
| cert-manager Certificate stuck `False` | ACME challenge cannot be served | Check the Order and Challenge resources; confirm the HTTP-01 solver route reaches the Gateway |
| Certificate issued but browsers distrust it | Root CA not in the client trust store | Distribute the root CA to workstations; internal CAs are not publicly trusted by design |
| Gateway has no external IP | No LB IP pool match, or the `nexus.io/lb-pool` label is missing | Check `CiliumLoadBalancerIPPool` selectors |
| Service reachable but unauthenticated | `SecurityPolicy` not targeting that route | Target the Gateway (all routes) or the specific HTTPRoute. Re-run `verify-sso.sh`. |
| Keycloak restarts lose configuration | Embedded DB, or realm not imported from Git | Use the Postgres cluster; import the realm at startup |
| OpenBao sealed after every restart | No auto-unseal | Implement Transit unseal or document the manual procedure and who holds shares |
| ESO reports permission denied | Vault policy or Kubernetes auth role mismatch | `bao read auth/kubernetes/role/<role>`; check `bound_service_account_names` |
| G3 shows > 30 s downtime | VIP failover waits on etcd leader election | Check etcd election timeout and `wal_fsync` latency (Phase 12). Slow disk → slow failover. |
| Witness pod restarted during G3 | Eviction timeouts too aggressive | Raise `default-not-ready-toleration-seconds`; this violates the design invariant and must be fixed |

---

## 🚫 DO NOT

- **Do not** add API-server OIDC flags before Keycloak is verified working and break-glass is tested.
- **Do not** apply the OIDC config to all control nodes at once. One, verify, then the rest.
- **Do not** grant a human group standing `cluster-admin`.
- **Do not** run Keycloak on an embedded database.
- **Do not** configure the realm through the admin UI. Realm-as-code, in Git.
- **Do not** keep the OpenBao root token after configuring auth. Revoke it.
- **Do not** store all unseal shares in one place, or with one person.
- **Do not** expose a service without checking `verify-sso.sh`.
- **Do not** use Let's Encrypt for hostnames that do not resolve publicly.
- **Do not** skip G3. Losing a control node is not hypothetical on consumer hardware.

---

## 📤 HANDOFF

`evidence/phase-17/handoff.md` must state:

1. **📊 G3 results** — API downtime per test and confirmation that the witness workload was undisturbed.
2. **The Keycloak issuer URL, realm, groups, and client list** — every later phase's service registers a client here.
3. **The OIDC → RBAC mapping** as implemented.
4. **The Gateway hostname pattern and the exposure recipe** — Phases 42–46 all expose services this way.
5. **The ClusterIssuer names** and which hostnames use internal vs. public certificates.
6. **OpenBao endpoint, auth method, unseal approach, and the ESO ClusterSecretStore name** — every later phase's credentials flow through these.
7. **Which services are now behind SSO**, and the `verify-sso.sh` result.
8. **Break-glass verification result** — confirmed working *after* OIDC was enabled.
9. **Any service that could not be put behind SSO**, and the compensating control.

---

## ➡️ NEXT

**[PHASE-18 — NVIDIA GPU Operator](PHASE-18.md)** — bring the GPUs online with managed drivers, the container toolkit, DCGM telemetry, and power caps. Stage 3 begins.
