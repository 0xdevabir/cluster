# PHASE 10 — Secrets & Configuration Management

| | |
|---|---|
| **Stage** | 1 — Bootstrap Infrastructure |
| **Estimated effort** | 3–4 hours |
| **Depends on** | 04, 06 |
| **Blocks** | 15, 16, 17, and every phase that needs a credential |
| **Risk** | 🔴 High — losing the age key makes the cluster unrecoverable; leaking it compromises everything |
| **Blast radius** | Total, in both directions |
| **Architecture refs** | `ARCHITECTURE.md#x1--security-architecture`, Phase 04 `secrets-taxonomy.md`, Rule 9 |

---

## 🎯 MISSION

Establish the **complete secret lifecycle**: the SOPS+age bootstrap chain (encrypt-at-rest in Git for things that must exist before the cluster), the OpenBao deployment plan and path layout, the External Secrets Operator wiring, and the custody procedure for the one key that everything else depends on.

> ⚠️ **DANGER — the age private key is the root of trust.** It decrypts the Talos cluster secrets, which contain every CA private key. Anyone with it owns the cluster. Losing it means you cannot decrypt the cluster secrets, cannot add nodes, and cannot issue certificates — the only recovery is a full rebuild. **Its custody procedure is the most important 20 lines you will write in this project.**

> 💡 **WHY two systems (SOPS *and* Vault) and not one:** a chicken-and-egg problem. Vault runs *on* Kubernetes; Kubernetes needs Talos secrets to exist; those secrets must be stored somewhere before any cluster exists. SOPS+age solves bootstrap (encrypted files in Git, decryptable with one offline key). Vault solves runtime (dynamic, leased, audited, revocable). Each is wrong for the other's job.

---

## ✅ PREFLIGHT

```bash
bash tools/seed-preflight.sh
test -f docs/security/secrets-taxonomy.md          # Phase 04
test -f docs/security/pki-hierarchy.md

command -v sops age age-keygen                     # pinned in Phase 00
gitleaks detect --no-banner                        # repo must be clean before we start

# You must have decided WHERE the age private key will live offline (Phase 04/A12)
```

---

## 📦 DELIVERABLES

```
.sops.yaml                            # creation rules — which key encrypts what
docs/security/
  key-custody.md                      # ⚠️ THE critical document
  secret-rotation-runbook.md
  vault-path-layout.md
  break-glass.md
clusters/nexus-prod/platform/openbao/ # manifests (deployed in Phase 17, defined here)
  README.md
  values.yaml
  policies/*.hcl
  auth-kubernetes.md
clusters/nexus-prod/infra/external-secrets/
  values.yaml
  clustersecretstore.yaml
tools/secrets/
  encrypt.sh  decrypt.sh  rotate-age-key.sh  check-encryption.sh
  seal-check.sh                       # CI gate: nothing plaintext committed
evidence/phase-10/{preflight,acceptance,handoff,deviations}.md
evidence/phase-10/key-ceremony.md     # who, when, where — never the key
```

---

## 🔧 VERSION PINNING

| Component | Version | Source |
|---|---|---|
| SOPS | `3.9.1` | pinned in Phase 00 |
| age | `1.2.0` | pinned in Phase 00 |
| OpenBao (Helm) | `0.10.0` | `oci://ghcr.io/openbao/charts/openbao` |
| External Secrets Operator | `0.11.0` | `oci://ghcr.io/external-secrets/charts/external-secrets` |
| gitleaks | `8.21.2` | CI |

> 💡 **OpenBao vs. HashiCorp Vault:** OpenBao is the Linux Foundation fork of Vault 1.14 under MPL-2.0, API-compatible. Chosen for licence clarity. **If you prefer Vault, everything in this phase applies unchanged except the chart name** — record the substitution in `deviations.md`.

---

## 📋 TASKS

### Task 1 — The key ceremony

⚠️ **Perform this deliberately, once, with a witness. Record the process — never the key.**

```bash
# 1. Generate the age keypair on a machine you trust
age-keygen -o age-nexus-bootstrap.key
# Output:
#   Public key: age1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#   (the file contains the PRIVATE key)

# 2. Record the PUBLIC key — this is safe to commit
grep 'public key' age-nexus-bootstrap.key | awk '{print $NF}' | tee docs/security/age-public-key.txt

# 3. Distribute the PRIVATE key to its custody locations (Task 2)

# 4. Install it for the operator's local use
mkdir -p ~/.config/sops/age
install -m 0600 age-nexus-bootstrap.key ~/.config/sops/age/keys.txt

# 5. Destroy the working copy
shred -u age-nexus-bootstrap.key
```

**`evidence/phase-10/key-ceremony.md`** records: date, participants and roles, the **public** key, the fingerprint, custody locations (described, not addressed), the witness, and the next scheduled rotation.

---

### Task 2 — Key custody (the most important document in the phase)

**`docs/security/key-custody.md`**

| Aspect | Decision |
|---|---|
| **Copies** | Exactly **three**: two offline, one on the seed node (operational) |
| **Offline copy A** | Encrypted USB in a physical safe, on-site |
| **Offline copy B** | Encrypted USB in a **different building** — a fire that destroys one must not destroy both |
| **Operational copy** | `~/.config/sops/age/keys.txt` on the seed node, mode `0600`, on an encrypted filesystem |
| **Passphrase** | The offline copies are separately passphrase-protected; the passphrase is held by a **different person** than the one holding the media (split custody) |
| **Never** | On a laptop, in cloud storage, in a password manager that syncs, in a chat message, in a screenshot, or in this repository |
| **Access log** | Every retrieval of an offline copy is recorded in `evidence/` with who, when, why |
| **Rotation** | Annually, or immediately on suspected exposure or an operator departure |
| **Recovery test** | **Quarterly** — retrieve an offline copy and prove it decrypts `talos/secrets/secrets.enc.yaml` |

> ⚠️ **An untested backup is not a backup.** The quarterly recovery test is an acceptance criterion (A14), not a suggestion. The failure mode you are guarding against is discovering, during a disaster, that the USB is corrupt or the passphrase is wrong.

**Include the printed recovery card** — a physical sheet stored with each offline copy:
```
NEXUS BOOTSTRAP KEY — RECOVERY
1. Decrypt the media with the passphrase held by <role>.
2. Install:  mkdir -p ~/.config/sops/age && install -m600 keys.txt ~/.config/sops/age/
3. Verify:   sops --decrypt talos/secrets/secrets.enc.yaml | head -5
4. If step 3 fails, use the other offline copy.
5. Record this retrieval in evidence/key-access.log.
Public key fingerprint: age1xxxx...
```

---

### Task 3 — SOPS creation rules

**`.sops.yaml`** — declares which files are encrypted, with which key, and which fields.

```yaml
creation_rules:
  # Talos cluster secrets — the whole file
  - path_regex: talos/secrets/.*\.enc\.yaml$
    age: age1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

  # Bootstrap-time credentials (PDU SNMP, switch AAA, registry pull secrets)
  - path_regex: bootstrap/.*\.enc\.yaml$
    age: age1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

  # Kubernetes Secrets committed before Vault exists — encrypt VALUES only,
  # so that `kubectl diff` and code review remain useful.
  - path_regex: clusters/.*-secret\.enc\.yaml$
    encrypted_regex: '^(data|stringData)$'
    age: age1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

  # Everything else: refuse to encrypt (fail loudly rather than silently)
```

```bash
# Encrypt
sops --encrypt --in-place bootstrap/pdu-credentials.enc.yaml
# Edit in place (decrypts to a tmpfs, re-encrypts on save)
sops bootstrap/pdu-credentials.enc.yaml
# Decrypt to stdout — NEVER to a file in the repo
sops --decrypt talos/secrets/secrets.enc.yaml | kubectl apply -f -
```

> 💡 **`encrypted_regex: '^(data|stringData)$'`** encrypts only the secret values, leaving `metadata`, `kind`, and `namespace` readable. That keeps diffs reviewable — you can see *that* a secret's name or namespace changed without decrypting it.

---

### Task 4 — The CI encryption gate

**`tools/secrets/seal-check.sh`** — the mechanical enforcement of Rule 9.

```bash
#!/usr/bin/env bash
# Fail if anything that should be encrypted is not, or if a plaintext secret appears.
source "$(dirname "$0")/../lib/common.sh"
fail=0

# 1. Every *.enc.yaml must actually be SOPS-encrypted
while IFS= read -r -d '' f; do
  if ! grep -q '^sops:' "$f"; then warn "NOT ENCRYPTED: $f"; fail=1; else ok "$f"; fi
done < <(find "$ROOT" -name '*.enc.yaml' -not -path '*/.git/*' -print0)

# 2. No unencrypted Kubernetes Secret manifests anywhere
while IFS= read -r -d '' f; do
  if grep -qE '^kind:[[:space:]]*Secret' "$f" && ! grep -q '^sops:' "$f"; then
    # ExternalSecret and SecretStore are fine — they contain no secret material
    grep -qE '^kind:[[:space:]]*(External|Cluster)?Secret(Store)?$' "$f" || {
      warn "PLAINTEXT SECRET: $f"; fail=1; }
  fi
done < <(find "$ROOT/clusters" "$ROOT/charts" -name '*.yaml' -print0 2>/dev/null)

# 3. Known secret-shaped filenames must not exist in plaintext
for pat in 'secrets.yaml' 'kubeconfig' 'talosconfig' '*.key' 'age.key' '*.pem'; do
  find "$ROOT" -name "$pat" -not -path '*/.git/*' -not -name '*.enc.yaml' \
    -not -name 'ca.pem' -print | grep . && { warn "unencrypted: $pat"; fail=1; }
done

# 4. gitleaks over the full history
gitleaks detect --no-banner --redact || fail=1

[[ $fail -eq 0 ]] && ok "no plaintext secrets" || die "SECRET LEAK CHECK FAILED"
```

Wire it into `task validate` and the CI pipeline, and add it to `.pre-commit-config.yaml`.

---

### Task 5 — OpenBao path layout

Define it now; deploy in Phase 17 (it needs storage and ingress). Implements the Phase 04 taxonomy.

**`docs/security/vault-path-layout.md`**
```
bao/
├── kv/                                   # KV v2 — static secrets
│   ├── platform/
│   │   ├── harbor          {admin_password, robot_token}
│   │   ├── keycloak        {admin_password, db_password}
│   │   ├── grafana         {admin_password, oidc_client_secret}
│   │   ├── argocd          {oidc_client_secret, webhook_secret}
│   │   └── mlflow          {db_password, s3_credentials}
│   ├── infrastructure/                   # platform-admins ONLY
│   │   ├── pdu/r01-pdu-a   {snmpv3_user, auth_key, priv_key}
│   │   ├── switches/r01-leaf-a {aaa_user, aaa_password}
│   │   └── pikvm/r01       {username, password}
│   └── tenants/<team>/     {team-managed, team-scoped policy}
│
├── database/                             # DYNAMIC — leased, auto-revoked
│   ├── config/postgres-platform
│   └── roles/{mlflow,keycloak,harbor}    # TTL 1h, max 24h
│
├── pki-platform/                         # short-lived service certs
│   ├── issue/nexus-service               # TTL 90d
│   └── roles/nexus-service
│
├── transit/                              # encryption-as-a-service
│   └── keys/tenant-data                  # apps encrypt without holding a key
│
├── auth/
│   ├── kubernetes/                       # ServiceAccount → Vault role
│   └── oidc/                             # humans, via Keycloak
│
└── sys/audit/                            # file + syslog sink; append-only
```

**Policy design principles:**

| Principle | Implementation |
|---|---|
| Least privilege | One policy per consumer; `read` on exactly the paths it needs |
| No wildcards at the top | `path "kv/data/*"` is never granted to anything but a break-glass role |
| Dynamic over static | Databases and PKI issue leased credentials; static KV only where dynamic is impossible |
| Short TTLs | Default 1 h, max 24 h. A leaked credential expires on its own. |
| Tenant isolation | `path "kv/data/tenants/{{identity.entity.aliases.<accessor>.metadata.team}}/*"` — templated, so one policy serves all tenants |
| Audit everything | `sys/audit` enabled before any secret is written; audit failure = Vault seals |

**Example policy — `policies/harbor.hcl`:**
```hcl
path "kv/data/platform/harbor" { capabilities = ["read"] }
path "database/creds/harbor"   { capabilities = ["read"] }
# Nothing else. Not even list on the parent.
```

---

### Task 6 — External Secrets Operator wiring

ESO is what turns a Vault path into a Kubernetes `Secret` without any credential passing through Git.

**`clusters/nexus-prod/infra/external-secrets/clustersecretstore.yaml`**
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata: { name: openbao }
spec:
  provider:
    vault:
      server: "https://openbao.platform-secrets.svc:8200"
      path: "kv"
      version: v2
      caProvider:
        type: Secret
        name: openbao-ca
        namespace: platform-secrets
        key: ca.crt
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "external-secrets"
          serviceAccountRef: { name: external-secrets, namespace: platform-secrets }
```

**Consumption pattern every later phase copies:**
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata: { name: harbor-admin, namespace: platform-registry }
spec:
  refreshInterval: 1h
  secretStoreRef: { name: openbao, kind: ClusterSecretStore }
  target:
    name: harbor-admin
    creationPolicy: Owner        # ESO owns it; deleting the ES deletes the Secret
  data:
    - secretKey: HARBOR_ADMIN_PASSWORD
      remoteRef: { key: platform/harbor, property: admin_password }
```

> 💡 **The whole point:** `ExternalSecret` manifests are safe to commit. They name *where* a secret lives, never *what* it is. This is what lets the entire cluster live in Git (Law II) without violating Rule 9.

---

### Task 7 — Rotation runbook

**`docs/security/secret-rotation-runbook.md`** — one procedure per class from the Phase 04 taxonomy.

| Secret | Cadence | Procedure | Blast radius if botched |
|---|---|---|---|
| **age bootstrap key** | Annual / on exposure | `rotate-age-key.sh`: generate new → re-encrypt every `*.enc.yaml` with **both** keys → verify → remove the old key → update custody | Total — cannot decrypt cluster secrets |
| Talos cluster CA | On compromise only | `talosctl` CA rotation; rolling node config apply | Cluster-wide restart |
| Vault unseal keys / root token | Post-init: revoke the root token, use OIDC | Standard Vault procedure | Vault inaccessible |
| Platform service passwords | 90 d | Update in Vault → ESO refreshes within `refreshInterval` → restart consumers if they cache | Service auth failure |
| Database credentials | Automatic (leased) | None — Vault rotates | — |
| PKI service certs | 90 d, automatic | cert-manager | TLS failure at expiry |
| PDU / switch credentials | 180 d | Change on the device → update Vault → verify `power-ctl.sh status` | Loss of remote power control |
| Registry robot tokens | 90 d | Harbor → Vault → ESO | Image pull failure |

**`tools/secrets/rotate-age-key.sh`** — the highest-risk procedure, so it must be scripted and tested:
```bash
#!/usr/bin/env bash
# Rotate the age bootstrap key. MUST be tested on a copy before running for real.
source "$(dirname "$0")/../lib/common.sh"
NEW_PUB="${1:?new age public key}"

# 1. Add the new recipient to .sops.yaml (both keys present temporarily)
# 2. Re-encrypt every file with BOTH keys — so a failure mid-run is recoverable
find "$ROOT" -name '*.enc.yaml' -not -path '*/.git/*' -print0 |
  while IFS= read -r -d '' f; do
    sops updatekeys --yes "$f" && ok "rekeyed $f" || die "FAILED on $f — STOP, do not remove the old key"
  done
# 3. VERIFY decryption with the NEW key alone, on a scratch copy
# 4. Only then remove the old recipient from .sops.yaml and re-run updatekeys
# 5. Update all three custody copies
# 6. Record the rotation in evidence/
```

---

### Task 8 — Break-glass

**`docs/security/break-glass.md`** — implements the Phase 04 procedure concretely.

```
WHAT IT IS
  A static admin kubeconfig + the age key, usable when OIDC/Keycloak/Vault are down.

WHERE IT LIVES
  · kubeconfig: SOPS-encrypted, committed at bootstrap/break-glass-kubeconfig.enc.yaml
  · age key:    the offline custody copies (Task 2)

HOW USE IS DETECTED
  · The break-glass client cert has a distinct CN: `break-glass-admin`
  · An audit-log alert fires on any request from that CN (Phase 47)
  · Alertmanager pages the security owner

PROCEDURE
  1. Declare an incident. You are about to bypass every access control.
  2. Retrieve the age key from offline custody; log the retrieval.
  3. sops --decrypt bootstrap/break-glass-kubeconfig.enc.yaml > $TMPDIR/kubeconfig
  4. Do the minimum necessary. Record every command.
  5. shred the kubeconfig copy.
  6. Write an incident note within 24 h.
  7. If the credential may have been exposed: rotate the Kubernetes CA.

QUARTERLY TEST
  Verify the break-glass kubeconfig still authenticates (a read-only command),
  and that the alert fires. An untested break-glass is not a break-glass.
```

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass condition |
|---|---|---|---|
| **A1** | age keypair generated; **public** key committed, private key absent from the repo | `grep age1 docs/security/age-public-key.txt`; `gitleaks detect` | Public present, no private key |
| **A2** | Three custody copies exist in the designated locations | `key-custody.md` + `key-ceremony.md` | Documented with witness |
| **A3** | Custody document forbids laptop/cloud/password-manager storage explicitly | Read it | Present |
| **A4** | `.sops.yaml` covers all three file classes | Read it | Three creation rules |
| **A5** | Kubernetes Secret rule encrypts values only | Encrypt a test Secret, inspect | `metadata` readable, `data` encrypted |
| **A6** | Talos cluster secrets are encrypted and decrypt correctly | `sops --decrypt talos/secrets/secrets.enc.yaml \| head -5` | Valid YAML |
| **A7** | `seal-check.sh` catches an unencrypted `*.enc.yaml` | Create one deliberately | Fails; **delete the test file** |
| **A8** | `seal-check.sh` catches a plaintext K8s Secret | Same | Fails |
| **A9** | `seal-check.sh` catches a private key file | Same | Fails |
| **A10** | The check is wired into `task validate`, pre-commit, and CI | Run each | Present in all three |
| **A11** | `gitleaks` over full history is clean | `gitleaks detect` | No findings |
| **A12** | Vault path layout documents all seven secret classes with an owning policy | Read `vault-path-layout.md` | All present |
| **A13** | Each policy grants only named paths — no top-level wildcards | Review `policies/*.hcl` | No `kv/data/*` outside break-glass |
| **A14** | **Offline key recovery tested** — retrieve a copy and decrypt | Perform it; record | Succeeded; logged |
| **A15** | Key rotation script tested on a copy of the repo | Run against a scratch clone | Completes; new key decrypts |
| **A16** | Rotation runbook covers all eight secret classes | Read it | Complete |
| **A17** | Break-glass procedure documented with detection and a quarterly test | Read it | Present |
| **A18** | ESO `ClusterSecretStore` and one example `ExternalSecret` exist and are commit-safe | Read them; run `seal-check.sh` | No secret material |

---

## ↩️ ROLLBACK

```bash
# Un-encrypt a file (⚠️ only in a scratch directory, never in the repo)
sops --decrypt file.enc.yaml > /tmp/scratch/file.yaml
# Revert .sops.yaml changes
git checkout -- .sops.yaml
```
⚠️ **If you have already re-keyed files with a new age key and lost the old one, there is no rollback.** This is why the rotation script keeps both keys until verification passes.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| `sops: no matching creation rules` | Filename does not match a `path_regex` | Rename to `*.enc.yaml` or add a rule. **Do not** encrypt with an ad-hoc command — the rule is the record of intent. |
| `Failed to get the data key` on decrypt | The age key is not at `~/.config/sops/age/keys.txt`, or the file was encrypted to a different recipient | Check `SOPS_AGE_KEY_FILE`; inspect the `sops.age[].recipient` field in the encrypted file |
| SOPS re-encrypts the whole file on every edit, producing noisy diffs | No `encrypted_regex` | Add it for Kubernetes Secrets so only values change |
| Pre-commit rejects a legitimate file | Detect-secrets false positive | `detect-secrets audit .secrets.baseline`, mark it, commit the baseline |
| `updatekeys` fails partway through rotation | A file has a stale or unreachable recipient | **Stop. Do not remove the old key.** Fix that file, re-run. |
| ESO reports `permission denied` | Vault policy missing, or the Kubernetes auth role does not bind the ServiceAccount | `bao read auth/kubernetes/role/<role>`; verify `bound_service_account_names` |
| Secret updates in Vault do not reach pods | `refreshInterval` not elapsed, or the app caches the value at startup | Lower the interval; use Reloader (or a restart annotation) to roll the deployment |
| An operator cannot decrypt after joining | They were never added as a recipient | Add their age public key to `.sops.yaml` and run `updatekeys`. **Prefer this over sharing one key.** |

---

## 🚫 DO NOT

- **Do not** commit any private key, in any form, encrypted-by-something-else included.
- **Do not** keep the age private key in a password manager that syncs to a cloud service.
- **Do not** decrypt a secret to a file inside the repository, even temporarily. Pipe to stdout.
- **Do not** deploy OpenBao here. It needs storage (Phase 26) and ingress (Phase 17). This phase defines it.
- **Do not** grant a `kv/data/*` wildcard to any policy except break-glass.
- **Do not** use static database passwords where a dynamic engine exists.
- **Do not** skip the offline-recovery test (A14). It is the whole point of having offline copies.
- **Do not** remove the old age key during rotation before verifying the new one works standalone.

---

## 📤 HANDOFF

`evidence/phase-10/handoff.md` must state:

1. **The age public key**, and the custody procedure (described, never located precisely in a committed file).
2. **What is currently SOPS-encrypted** and where.
3. **The Vault path layout and policy names** — Phase 17 deploys against this exact structure.
4. **The ESO pattern** — every later phase that needs a credential copies the `ExternalSecret` example.
5. **Rotation schedule and owners** per secret class.
6. **Break-glass location and detection mechanism** — Phase 47 builds the alert.
7. **Results of the offline recovery test**, with date.
8. **Any operator age public keys added** as additional recipients.

---

## ➡️ NEXT

**[PHASE-11 — Bootstrap Observability & Gate G2](PHASE-11.md)** — instrument the provisioning pipeline and prove a node goes from bare metal to Ready in under 15 minutes with zero human touch.
