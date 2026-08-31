# PHASE 55 — Security Hardening & Supply Chain (G13)

| | |
|---|---|
| **Stage** | 9 — Operations & Sustainment |
| **Estimated effort** | 5–6 hours |
| **Depends on** | 04, 16, 17, 42, 45, 53 |
| **Blocks** | 56 |
| **Risk** | 🟠 Medium-High — hardening can break working workloads |
| **Blast radius** | Every workload, if a policy is flipped carelessly |
| **Architecture refs** | `ARCHITECTURE.md#x1-security-architecture`, Phase 04's threat model (P1–P12), `ULTIMATE-PLAN.md#13-gates` (G13) |

---

## 🎯 MISSION

Close the security posture. Phase 04 designed the threat model; Phases 10, 16, 17, and 42 implemented parts of it, mostly in Audit mode with documented exceptions. This phase **verifies every control actually works, flips the remaining policies to enforce, hardens the supply chain end to end, and validates the whole thing against the original attack paths.** Then pass **gate G13**.

> 💡 **WHY security is a late phase and not an early one.** Not because it is less important — Law VII says *Security Is a Substrate* — but because a fail-closed policy applied before the platform works blocks everyone and gets disabled. The correct sequence is: design the model early (Phase 04), implement controls as each subsystem is built, run them in Audit while behavior stabilizes, then **enforce once the exception list is real and known**. This phase is that final step, plus the verification that the model actually holds.

> ⚠️ **The danger in this phase is flipping a policy to enforce and breaking production.** Every policy has been in Audit for a reason: to build an accurate exception list. **Review the audit data before every flip**, flip one policy at a time, and have a documented rollback. A security control that gets disabled after an outage protects nothing.

---

## ✅ PREFLIGHT

```bash
# Phase 04's threat model — the thing being validated
grep -c "^| P" evidence/phase-04/threat-model.md

# 📊 Kyverno audit data — the basis for every enforcement decision
kubectl get policyreport -A -o json | jq '[.items[].results[] | select(.result=="fail")] | length'
kubectl get clusterpolicy -o json | jq -r '.items[] | "\(.metadata.name) \(.spec.validationFailureAction)"'

# The privileged workload allow-list (Phase 04's audited list)
cat policies/privileged-allowlist.yaml

# Signing and scanning in place (Phase 42)
cosign verify --key <key> <a production image>
```

---

## 📦 DELIVERABLES

```
security/
  posture-review.md                 # 🎯 every control, verified
  attack-path-validation.md         # 🎯 P1–P12 re-tested
  privileged-workloads.md           # the final audited list
  exception-register.md             # ⚠️ every exception, owner, expiry
  supply-chain.md                   # build → sign → scan → verify → run
policies/
  enforce/                          # policies flipped from Audit
tools/security/
  posture-check.sh                  # continuous verification
  attack-simulation.sh              # 🧪 can we still do P1..P12?
  secret-scan.sh                    # is anything leaked?
  rbac-audit.sh                     # who can do what
  cve-report.sh
docs/operations/
  security-runbook.md
  incident-security.md              # ⚠️ different from an availability incident
gates/G13-security.md
evidence/phase-55/{preflight,acceptance,handoff,deviations,gate-g13}.md
```

---

## 📋 TASKS

### Task 1 — 🎯 Validate the attack paths (test, do not assume)

**`tools/security/attack-simulation.sh`** — re-run Phase 04's P1–P12 against the built cluster. **Each should now fail.**

| # | Attack path | Mitigation built in | Test |
|---|---|---|---|
| **P1** | Compromised container → host | PSA restricted, no privileged, seccomp | Try to escape; try `hostPath` |
| **P2** | Container → other tenants' data | NetworkPolicy default-deny, RBAC, namespace isolation | Try to reach another tenant's service and PVC |
| **P3** | Steal credentials from the API | RBAC least-privilege, no default SA token automount | Try to read Secrets across namespaces |
| **P4** | Unauthorized image execution | Signature verification (Phase 16/42) | Deploy an unsigned image |
| **P5** | Supply-chain: malicious dependency | SBOM, scanning, pinned digests | Task 4 |
| **P6** | Lateral movement via the network | Cilium policy, identity-based | Try cross-namespace reachability |
| **P7** | Privilege escalation via a workload | No `allowPrivilegeEscalation`, dropped caps | Try |
| **P8** | Data exfiltration | Egress policy, object-store ACLs | Try to reach the internet from a tenant pod |
| **P9** | Denial of service via resource exhaustion | Quotas, limits, Kueue | Try to consume everything |
| **P10** | Access the management network | VLAN separation, firewall | Try to reach PDUs/switches from a pod |
| **P11** | Tamper with the GitOps source | Branch protection, signed commits, Argo RBAC | Try to push directly |
| **P12** | Compromise the secret store | OpenBao policies, audit, unseal custody | Try to read another tenant's secrets |

> 🧪 **These must be actually attempted, not reasoned about.** "We have NetworkPolicy so P6 is mitigated" is an assumption; a pod that tries to reach another tenant's Redis and gets connection-refused is evidence. **Record the exact command and its output for each path** — that record is G13's evidence and the basis for every future security review.

> ⚠️ **Expect at least two of the twelve to still succeed.** That is the value of testing. The common survivors: P8 (egress is rarely fully locked down) and P10 (a management VLAN reachable from somewhere it should not be). Finding them here is the point.

---

### Task 2 — ⚠️ Flip the policies to enforce

Phase 16 deployed Kyverno policies in Audit. Now enforce — carefully.

**The procedure, per policy:**
```
1. Review 30 days of PolicyReport data: what WOULD have been blocked?
2. Categorize every violation:
     · Legitimate → add to the exception register with an owner and expiry
     · Fixable    → fix the workload FIRST, then flip
     · Unclear    → investigate; do not flip until resolved
3. Notify affected teams with a date
4. Flip ONE policy to Enforce
5. Watch for 48 h; be ready to revert
6. Next policy
```

| Policy | Audit findings to expect | Enforce? |
|---|---|---|
| `disallow-privileged` | The audited allow-list (Phase 04) | ✅ Enforce, with the allow-list |
| `require-signed-images` | Third-party images without signatures | ✅ Enforce, with exceptions |
| `require-resource-limits` | Many; users forget | ✅ Enforce after fixing |
| `disallow-host-namespaces` | Rare | ✅ Enforce |
| `require-non-root` | ⚠️ Many legacy images run as root | Enforce per namespace, staged |
| `restrict-volume-types` | hostPath in platform components | ✅ Enforce with the allow-list |
| `require-vram-declaration` (Phase 19) | | ✅ Enforce |
| `default-deny NetworkPolicy` | ⚠️ **Highest breakage risk** | Staged per namespace |

> ⚠️ **The default-deny network policy is the one most likely to cause an outage**, because it breaks things that were silently working — a job that reached a database in another namespace, a webhook, a DNS path. **Roll it namespace by namespace**, starting with a tenant that volunteers, and keep Hubble's flow logs open during each flip so a blocked flow is visible immediately rather than as a mystery timeout.

**`security/exception-register.md`** — every exception, structured:
```yaml
- id: SEC-EX-014
  policy: require-signed-images
  scope: namespace/vendor-tools, image "vendor/tool:*"
  reason: "Vendor does not sign images. Verified by digest pinning + SBOM review."
  compensating_control: "Digest-pinned; network-isolated; no secret access."
  owner: <name>
  approved: 2026-08-20
  expires: 2027-02-20        # ⚠️ MANDATORY
  review_note: "Vendor committed to signing in Q1."
```

> 🚫 **An exception without an expiry is a permanent hole with paperwork.** Every exception expires; expiry alerts; renewal requires re-justification. Otherwise the exception register becomes the real policy within two years.

---

### Task 3 — RBAC and identity audit

**`tools/security/rbac-audit.sh`** — answer "who can do what," and find what should not be.

| Check | Look for |
|---|---|
| **Cluster-admin bindings** | ⚠️ Should be a very short list of named humans + break-glass |
| ServiceAccounts with cluster-wide read on Secrets | Almost never justified |
| **Wildcard verbs or resources in Roles** | `*` on anything |
| `escalate` / `bind` / `impersonate` verbs | Privilege-escalation primitives |
| Default SA with any binding | Should have none |
| **Token automount enabled where not needed** | Default is true — turn it off |
| Stale bindings for departed users | Cross-check against the identity provider |
| Bindings granted "temporarily" | Check dates |

> ⚠️ **`escalate` and `bind` are how a limited role becomes cluster-admin.** A ServiceAccount that can create RoleBindings can grant itself anything its principal can grant. Audit for them explicitly; they are easy to miss in a review that only looks at resource names.

**Identity hygiene (Phase 17):**
- OIDC group mapping correct; no long-lived static tokens
- Break-glass credentials: sealed, audited, and **tested** (Phase 53's certificate rotation touched this)
- Service accounts for CI use short-lived, scoped credentials
- ⚠️ **Departed-user offboarding is a documented procedure** — and someone has actually run it

---

### Task 4 — 🎯 Supply chain, end to end

**`security/supply-chain.md`** — every link, with the control at each.

```
SOURCE      → Git, branch protection, signed commits, required review
   ↓            ⚠️ Can anyone push directly to main? Test it (P11).
DEPENDENCIES→ Pinned versions, lockfiles, SBOM at build (Phase 42)
   ↓            ⚠️ Are transitive deps scanned, not just direct?
BUILD       → BuildKit, isolated, reproducible-ish, provenance recorded
   ↓            ⚠️ Can a build inject arbitrary code? Who can trigger a build?
ARTIFACT    → Signed (cosign), SBOM attested, scanned (Trivy), digest-pinned
   ↓
REGISTRY    → Private, tag immutability on prod-*, RBAC, retention (Phase 42)
   ↓
ADMISSION   → Signature verified, policy enforced (Phase 16)
   ↓            ⚠️ Fail-closed: an unverifiable image must NOT run
RUNTIME     → PSA restricted, seccomp, no privileged, network policy
   ↓
OBSERVE     → Runtime detection (below), audit logs, image drift detection
```

**Runtime security detection** — the layer that is easy to skip and hard to add later:

| Option | Verdict |
|---|---|
| **Falco** | ✅ Mature, eBPF-based, good rule set. ⚠️ Needs tuning or it is pure noise. |
| Tetragon (Cilium) | ✅ Already have Cilium; eBPF; policy enforcement too |
| Nothing | ⚠️ Honest option for a private research cluster — but say so explicitly in the posture review |

> 💡 **If you deploy runtime detection, budget the tuning time.** Falco's default rules on a Kubernetes cluster running arbitrary research code will produce hundreds of alerts a day, most of them a researcher legitimately compiling something. **Untuned runtime detection is worse than none** — it trains people to ignore security alerts, which is the exact failure Phase 47's alert hygiene section warns about. Deploy it in audit-only, tune for a month, then enable.

**`tools/security/secret-scan.sh`** — scan for leaked credentials:
```
· Git history (gitleaks) — ⚠️ including deleted files and old commits
· Container images (layers often contain build-time secrets)
· ConfigMaps and environment variables in the live cluster
· Logs (⚠️ Loki — a leaked token in a log is a leaked token)
· ⚠️ If found: rotate FIRST, then remove. Removing without rotating does nothing.
```

---

### Task 5 — Vulnerability management (make it sustainable)

| Severity | SLA | Applies to |
|---|---|---|
| **Critical, exploitable, exposed** | **24 h** | Internet-facing or high-privilege |
| Critical, not exposed | 7 days | Internal only |
| High | 30 days | |
| Medium | 90 days | |
| Low / no fix available | Track; review quarterly | |

> ⚠️ **"Exploitable and exposed" matters more than the CVSS score.** A critical CVE in a library that is present but never called, in a pod with no network access, is less urgent than a medium in the ingress path. **Prioritize by reachability and exposure**, not by score alone — otherwise the team spends its time patching things that cannot be attacked while the real path stays open.

**The CUDA-image reality:** NVIDIA container images routinely carry unfixable CVEs in bundled libraries. Phase 42 established the exception process; formalize it here with quarterly review, and document the compensating controls (network isolation, no secret access, restricted PSA).

---

### Task 6 — 🚪 GATE G13 — Security

**`gates/G13-security.md`**

| # | Check | Evidence | Pass |
|---|---|---|---|
| G13.1 | 🧪 **All 12 attack paths P1–P12 tested with recorded output** | `attack-path-validation.md` | ☐ |
| G13.2 | Every path that still succeeds has a fix with an owner and date | Register | ☐ |
| G13.3 | All Kyverno policies in Enforce, or an exception is registered | `kubectl get cpol` | ☐ |
| G13.4 | 🔒 **`require-signed-images` fail-closed and verified** | 🧪 Deploy unsigned | ☐ |
| G13.5 | Default-deny NetworkPolicy in every tenant namespace | Audit | ☐ |
| G13.6 | 🧪 Cross-tenant network access blocked | Test | ☐ |
| G13.7 | **Privileged workload list matches reality and is justified** | Compare | ☐ |
| G13.8 | Cluster-admin bindings: short, named, justified | `rbac-audit.sh` | ☐ |
| G13.9 | No wildcard verbs/resources outside the platform | Audit | ☐ |
| G13.10 | No `escalate`/`bind`/`impersonate` outside platform controllers | Audit | ☐ |
| G13.11 | Default SA token automount disabled where not needed | Audit | ☐ |
| G13.12 | 🧪 Departed-user offboarding executed for a test account | Test | ☐ |
| G13.13 | 🔒 **No secrets in Git history, images, ConfigMaps, or logs** | `secret-scan.sh` | ☐ |
| G13.14 | Every secret is SOPS-encrypted or in OpenBao | Audit | ☐ |
| G13.15 | 🔒 Offline key custody verified (Phase 29's S2/S3/S4/S17) | Physical check | ☐ |
| G13.16 | Supply chain documented end to end with a control at each link | `supply-chain.md` | ☐ |
| G13.17 | 🧪 **P11: direct push to main is blocked** | Test | ☐ |
| G13.18 | SBOM present and verifiable for every production image | `cosign verify-attestation` | ☐ |
| G13.19 | Vulnerability SLAs defined; no Critical past SLA | `cve-report.sh` | ☐ |
| G13.20 | **Exception register complete; every entry has an owner and expiry** | Register | ☐ |
| G13.21 | Expired-exception alerting works | 🧪 Backdate one | ☐ |
| G13.22 | 🔒 API audit logging on, 1-year retention, access-restricted | Phase 45 | ☐ |
| G13.23 | Runtime detection deployed and tuned, or its absence is documented | Either | ☐ |
| G13.24 | Security incident runbook exists and differs from the availability one | Read | ☐ |
| G13.25 | `posture-check.sh` runs continuously and alerts on drift | Scheduled | ☐ |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | 🧪 **Every attack path attempted, with the command and output recorded** | Read the record | All 12 |
| **A2** | Paths that still succeed are recorded as findings, not omitted | Register | Recorded |
| **A3** | Audit data reviewed before every policy flip | Records | Reviewed |
| **A4** | Policies flipped one at a time with a 48 h watch | Records | One at a time |
| **A5** | 🧪 **Default-deny rolled out namespace by namespace without an outage** | Records | No outage |
| **A6** | Hubble flow logs used during each network policy flip | Records | Used |
| **A7** | 🔒 An unsigned image is rejected | 🧪 Test | Rejected |
| **A8** | 🧪 A privileged pod outside the allow-list is rejected | Test | Rejected |
| **A9** | Every exception has an owner and a mandatory expiry | Register | 100 % |
| **A10** | 🧪 Expired exceptions alert | Backdate one | Alerts |
| **A11** | RBAC audit found and removed unnecessary permissions | Diff | Removed |
| **A12** | 🧪 A test account offboarded end to end | Test | Works |
| **A13** | 🔒 **Secret scan clean across Git history, images, cluster, and logs** | `secret-scan.sh` | Clean |
| **A14** | Any secret found was **rotated before removal** | Records | Rotated |
| **A15** | 🧪 P11: pushing directly to main is blocked | Test | Blocked |
| **A16** | SBOMs verifiable for production images | Test | Verifiable |
| **A17** | CVE report generated; no Critical past SLA | Report | Clean |
| **A18** | CUDA image exceptions documented with compensating controls | Register | Documented |
| **A19** | Runtime detection tuned to a sustainable alert rate, or absent by decision | Either | Either |
| **A20** | 🔒 Audit log retention and access restriction verified | Check | Verified |
| **A21** | Security incident runbook distinct from availability | Read | Distinct |
| **A22** | `posture-check.sh` detects an injected drift | 🧪 Inject | Detects |
| **A23** | 🚪 **Gate G13 passes** | `gates/G13-security.md` | All ☑ |

---

## ↩️ ROLLBACK

```bash
# Revert ONE policy to Audit — the correct response to a breakage
kubectl patch cpol <name> --type merge \
  -p '{"spec":{"validationFailureAction":"Audit"}}'
# ⚠️ Record WHY, and set a date to re-enforce. An indefinite Audit is a disabled control.

# Revert a network policy rollout for one namespace
kubectl delete cnp default-deny -n <namespace>
```

> ⚠️ **Reverting to Audit must be temporary and tracked.** The failure mode is a policy that was flipped to Audit "for now" three years ago and everyone forgot. Add every reverted policy to the exception register with an expiry, exactly like any other exception.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| Workloads break after a policy flip | Audit data was incomplete or not reviewed | Revert that policy; review; fix workloads; re-flip |
| Network policy breaks a working path | An undocumented dependency | Hubble flow logs show the blocked flow; add an explicit rule |
| Signed image still rejected | Key mismatch or the wrong policy key | Compare the policy's key to the signing key |
| Falco alerts flooding | Untuned default rules | Audit-only until tuned; this is expected |
| Secret found in Git history | Committed at some point | ⚠️ **Rotate first.** History rewriting alone does not help — assume it was seen. |
| Attack path still succeeds | The control does not do what was assumed | The whole point of A1 — record and fix |
| CVE scanner blocks everything | CUDA base images | The exception process; do not disable scanning |
| RBAC audit shows huge permission sets for operators | Operators genuinely need them | Verify against the operator's docs; document as reviewed |
| Users request cluster-admin | Usually a narrower need | Find the specific verb; grant that |
| Exception register growing | Real controls not being met | Review in the monthly security review; escalate |

---

## 🚫 DO NOT

- **Do not** flip a policy to Enforce without reviewing its audit data.
- **Do not** flip more than one policy at a time.
- **Do not** roll out default-deny cluster-wide in one step.
- **Do not** create an exception without an owner and an expiry.
- **Do not** leave a policy in Audit indefinitely without tracking it.
- **Do not** remove a leaked secret without rotating it first.
- **Do not** deploy untuned runtime detection.
- **Do not** prioritize CVEs by score alone — use reachability and exposure.
- **Do not** claim an attack path is mitigated without testing it.

---

## 📤 HANDOFF

`evidence/phase-55/handoff.md` must state:

1. **🚪 The G13 gate result.**
2. **🧪 The attack-path validation record** — all twelve, with commands and outputs. **Any path that still succeeds, with an owner and a date.**
3. **Which policies are enforced**, which remain in Audit, and why.
4. **The exception register** — every entry with owner and expiry.
5. **🔒 The secret scan result**, and anything rotated.
6. **The RBAC changes made** — what was removed.
7. **The supply-chain diagram** with the control at each link, and any link without one.
8. **The runtime-detection decision** and its alert rate.
9. **Outstanding security risks** with owners — the honest list.

---

## ➡️ NEXT

**[PHASE-56 — Capacity Planning, Documentation & Handover](PHASE-56.md)** — the final phase. Plan the next year, complete the documentation, and hand the platform over.
