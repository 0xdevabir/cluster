# PHASE 42 — Container Registry & Build Farm

| | |
|---|---|
| **Stage** | 7 — Platform Experience |
| **Estimated effort** | 4–5 hours |
| **Depends on** | 16, 17, 26, 28, 41 |
| **Blocks** | 43, 44, 46, 55 |
| **Risk** | 🟠 Medium-High — the registry is on the critical path of every pod start |
| **Blast radius** | If the registry is down, nothing new can start |
| **Architecture refs** | `ARCHITECTURE.md#l9-platform-services`, `#x1-security-architecture`, ADR-026 |

---

## 🎯 MISSION

Give the cluster a **fast, reliable, private container registry** and a **distributed build farm**, so that building a 12 GB CUDA image takes minutes instead of an hour, images are signed and scanned before they can run, and pulling them onto 100 nodes does not saturate the fabric.

> 💡 **WHY this is Stage 7's first phase.** Every workload in Stages 5 and 6 runs from a container image. Until now those came from public registries — which means an external dependency on the critical path of every pod start, rate limits, no signing guarantee, and a 12 GB pull crossing the internet. A private registry with a pull-through cache removes the external dependency; a build farm removes the "I changed one line of Python and waited 40 minutes" tax that quietly destroys research velocity.

> ⚠️ **The thundering-herd problem is specific to this cluster's shape.** A 64-node training job starting simultaneously pulls the same 12 GB image 64 times = **768 GB in a burst**. On a 100 GbE fabric that is a minute of full saturation competing with everything else, and it can take the registry down. Mitigation is a deliverable, not an optimization.

---

## ✅ PREFLIGHT

```bash
# Storage for the registry (T2 block or T3 object)
kubectl get sc nexus-block
s5cmd ls s3://nexus-registry/ 2>/dev/null || echo "create the bucket"

# Ingress + identity (Phase 17)
kubectl get gateway -A

# Kyverno image verification policy from Phase 16 (currently fail-closed on signatures)
kubectl get cpol require-signed-images

# Talos registry mirror config from Phase 09 — this is where the cache is wired in
grep -A10 "registries" talos/patches/_base.yaml
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/platform/registry/
  harbor-values.yaml                # or zot-values.yaml
  projects.yaml                     # per-tenant projects + quotas
  replication-rules.yaml            # pull-through cache from upstreams
  retention-policies.yaml           # ⚠️ or storage grows without bound
  robot-accounts.yaml               # CI identities
clusters/nexus-prod/platform/build/
  buildkit-daemonset.yaml           # distributed builders with a shared cache
  build-clusterqueue.yaml
  buildjob-template.yaml
talos/patches/
  registry-mirrors.yaml             # ⚠️ point every node at the cache
tools/build/
  build.sh                          # the sanctioned build path
  sign-and-push.sh                  # cosign
  prewarm-image.sh                  # ⚠️ the thundering-herd mitigation
  bench-build.sh                    # 📊 build and pull performance
docs/user/
  container-guide.md
  base-images.md                    # 🎯 curated images so users don't start from scratch
images/base/                        # the curated set
observability/rules/registry-alerts.yaml
evidence/phase-42/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version | Note |
|---|---|---|
| Harbor | `v2.12.2` | Full-featured: projects, scanning, signing, replication |
| Zot | `v2.1.2` | Alternative: minimal, OCI-native, much simpler |
| BuildKit | `v0.19.0` | |
| cosign | `v2.4.1` | Phase 16's signature verification |
| Trivy | `0.58.x` | Scanning (bundled with Harbor) |

> 💡 **Harbor vs. Zot.** Harbor brings projects, RBAC, quotas, replication, scanning, and a UI — at the cost of Postgres, Redis, and six components to operate. Zot is a single binary that does OCI well and little else. **Choose Harbor** if you need per-tenant projects and scanning integrated (this platform does, given Phase 16's policy work). Choose Zot if the team is small and simplicity wins. Record the decision.

---

## 📋 TASKS

### Task 1 — Registry deployment and storage backing

```yaml
# harbor-values.yaml — the decisions that matter
persistence:
  imageChartStorage:
    type: s3                        # ⚠️ object storage, not a PVC
    s3:
      region: us-east-1
      bucket: nexus-registry
      regionendpoint: http://rgw.storage.svc:80
      secure: false
      v4auth: true
  persistentVolumeClaim:
    database: { storageClass: nexus-fast }    # Postgres on T1
    redis:    { storageClass: nexus-fast }
trivy: { enabled: true }
expose:
  type: ingress                     # behind the Phase 17 Gateway, OIDC-backed
```

**Why S3 for layers, T1 for metadata:**

| Data | Backing | Reason |
|---|---|---|
| Image layers | **T3 object** | Large, immutable, cheap; scales without resize |
| Harbor DB | **T1 Mayastor** | Small, transactional, latency-sensitive |
| Redis | T1 | Cache; small |

> ⚠️ **A registry on a fixed-size PVC will fill.** Image layers grow relentlessly — every CI build, every tag, every base-image update. Object storage removes the capacity cliff, and the retention policy (Task 4) removes the cost growth.

---

### Task 2 — 🎯 Pull-through cache and mirror configuration

This is where the external dependency disappears.

```yaml
# talos/patches/registry-mirrors.yaml — applied to EVERY node
machine:
  registries:
    mirrors:
      docker.io:
        endpoints: ["https://registry.nexus.internal/v2/proxy-dockerhub"]
      ghcr.io:
        endpoints: ["https://registry.nexus.internal/v2/proxy-ghcr"]
      quay.io:
        endpoints: ["https://registry.nexus.internal/v2/proxy-quay"]
      nvcr.io:
        endpoints: ["https://registry.nexus.internal/v2/proxy-nvcr"]
      registry.k8s.io:
        endpoints: ["https://registry.nexus.internal/v2/proxy-k8s"]
    config:
      registry.nexus.internal:
        tls: { ca: <step-ca cert from Phase 07> }
```

**What this buys:**

| Before | After |
|---|---|
| Every node pulls from the internet | First pull populates the cache; the rest are local |
| Docker Hub rate limits break deployments | No external rate limit |
| A public registry outage stops the cluster | Cached images keep working |
| 12 GB × 64 nodes crosses the WAN | Once from the WAN, then LAN |

> 💡 **This one configuration change is among the highest-value-per-line in the whole project.** It removes an entire class of outage (upstream registry unavailable, rate-limited, or slow) from the critical path of every pod start.

> ⚠️ **The mirror must be highly available, because it is now on that critical path.** Run ≥ 2 Harbor replicas, spread across racks, and ensure a registry outage degrades to "cannot pull new images" rather than "cannot start anything" — nodes with the image cached locally must continue working. Verify this explicitly (A9).

---

### Task 3 — ⚠️ The thundering herd

**`tools/build/prewarm-image.sh`** and the accompanying mechanism.

**Three mitigations, apply all three:**

| # | Mechanism | Effect |
|---|---|---|
| **1. Pre-warm** | A DaemonSet or Job pulls the image to target nodes *before* the workload is admitted | Removes the burst entirely |
| **2. Lazy loading (SOCI / eStargz)** | Containers start before the full image downloads, fetching layers on demand | Start time drops from minutes to seconds |
| **3. P2P distribution (Dragonfly / Spegel)** | Nodes pull layers from *each other*, not all from the registry | Registry load becomes O(1), fabric load spreads |

> 💡 **Spegel is the highest value-to-complexity option here.** It is a stateless DaemonSet that turns every node's existing containerd image store into a peer in a P2P network — no separate registry, no image rewriting, no changes to manifests. A node that needs a layer another node already has fetches it over the LAN. **Deploy Spegel; treat SOCI as a later optimization.**

**Integrate pre-warming with the scheduler:** when Kueue admits a large gang, trigger a pre-warm of its image on the target nodes before unsuspending. Even a simple version (a pre-warm step in Phase 41's workflow templates) captures most of the benefit.

📊 **Measure it — the numbers justify the work:**
```
64-node pull, cold, no mitigation:      ~6 min, 768 GB across the fabric ⚠️
64-node pull, with Spegel P2P:          ~50 s, ~15 GB from the registry  ✅
64-node pull, pre-warmed:               ~4 s                             ✅
```

---

### Task 4 — Retention, quotas, and cost control

Registries grow without bound unless told not to.

```yaml
# retention-policies.yaml — per project
rules:
  - template: latestPushedK
    params: { latestPushedK: 10 }        # keep the 10 most recent tags
    scopeSelectors: { repository: [{ decoration: repoMatches, pattern: "**" }] }
  - template: nDaysSinceLastPull
    params: { nDaysSinceLastPull: 90 }   # keep anything pulled in 90 days
  # ⚠️ ALWAYS exclude production-tagged images from GC
  - action: retain
    scopeSelectors: { tag: [{ decoration: matches, pattern: "prod-*" }] }
```

| Control | Setting |
|---|---|
| Per-project storage quota | 500 GB default; request more |
| Retention | 10 latest + anything pulled in 90 days |
| **Never delete** | `prod-*`, `release-*`, anything referenced by a running deployment |
| Garbage collection | Weekly, off-peak |
| Immutable tags | ⚠️ **Enable for `prod-*`** — a production tag must never be overwritten |

> ⚠️ **Immutable tags for production are a correctness control, not a policy nicety.** If `prod-v1.2.3` can be re-pushed, then "we rolled back to v1.2.3" does not mean what anyone thinks it means, and a node that pulls fresh gets different code than one running from cache. Enable tag immutability for release patterns.

> 🚫 **Garbage collection must never delete a layer referenced by a running workload.** Harbor's GC handles this within its own knowledge, but a manually-deleted repository can break a running deployment's ability to restart. Add a pre-GC check against running images.

---

### Task 5 — The build farm

**`buildkit-daemonset.yaml`** — BuildKit on CPU nodes with a shared cache.

```yaml
# Key configuration
- Rootless BuildKit where possible (🔒 avoids privileged builders)
- Shared cache exported to the registry: --export-cache type=registry,ref=...
- Cache imported on every build: --import-cache type=registry,ref=...
- Builders on the CPU pool (Phase 38's rule: don't squat on GPU nodes)
- Kueue-gated via build-clusterqueue
```

**Why a shared remote cache matters more than build parallelism:**
```
Building a CUDA training image:
  Cold, no cache:                  38 min  (apt, pip, CUDA layers, model deps)
  Warm local cache, same builder:   2 min
  Warm REMOTE cache, any builder:   3 min  ← ✅ this is the win
  ⚠️ Without a remote cache, every builder is cold and every build is 38 min.
```

> 💡 **Curated base images are the other half of the build-time problem.** If every team builds CUDA + PyTorch + their deps from scratch, everyone pays the 38 minutes. Publish a small set of maintained base images (`nexus/pytorch:2.6.0-cu126`, `nexus/ray:2.40.0-cu126`, `nexus/spark:3.5.4`) built weekly by the platform, and user builds become a 90-second `FROM` + `pip install`.

**`docs/user/base-images.md`** documents the set, what is in each, the update cadence, and the support policy.

---

### Task 6 — 🔒 Signing and scanning (closing Phase 16's loop)

Phase 16 deployed `require-signed-images` as a **fail-closed** policy. This phase makes it satisfiable.

```bash
# tools/build/sign-and-push.sh
buildctl build ... --output type=image,name=$IMAGE,push=true
cosign sign --key <key-from-OpenBao> $IMAGE
cosign attest --predicate sbom.json --type spdx $IMAGE     # attach the SBOM
```

| Control | Requirement |
|---|---|
| **Signing** | Every image that runs in production is cosign-signed |
| Key custody | Signing key in OpenBao (Phase 17); CI uses a short-lived credential |
| **Scanning** | Trivy scan on push; block on Critical CVEs in `prod-*` |
| SBOM | Generated at build, attached as an attestation |
| Provenance | SLSA provenance where the build system supports it |
| Base image freshness | Alert when a base image has unpatched Critical CVEs |

> ⚠️ **Blocking on Critical CVEs will block things you need.** CUDA images routinely carry unfixable CVEs in bundled libraries. Establish an **exception process with an owner and an expiry date** — not a blanket bypass, and not a policy everyone disables in week two. Record exceptions in Git, review them monthly.

> 🔒 **The scanning result is only meaningful if the image cannot change after scanning.** This is why tag immutability (Task 4) and signature verification (Phase 16) work together: scan → sign → verify at admission. Breaking any link makes the other two theater.

---

### Task 7 — 📊 Benchmarks and alerts

**`tools/build/bench-build.sh`:**

| Metric | Target | Measured |
|---|---|---|
| Cold build, CUDA training image | Record baseline | |
| Warm build (remote cache hit) | < 4 min | |
| Build from a curated base image | < 2 min | |
| Single-node image pull, 12 GB, cold | Record | |
| **64-node simultaneous pull, no mitigation** | Record — the problem | |
| **64-node with Spegel P2P** | < 90 s | |
| 64-node pre-warmed | < 10 s | |
| Registry throughput under load | ≥ 2 GB/s aggregate | |
| Push throughput | | |

**Alerts:**

| Alert | Threshold |
|---|---|
| `RegistryDown` | Any replica unavailable |
| `RegistryStorageHigh` | Bucket > 80 % of quota |
| `RegistryPullLatencyHigh` | p95 pull > 2× baseline |
| `ImagePullFailureRate` | > 1 % of pod starts |
| `RegistryQuotaExceeded` | A project at quota |
| `UnsignedImageBlocked` | Kyverno rejection (expected sometimes; track the rate) |
| `CriticalCVEInProdImage` | Trivy finding on a running image |
| `BaseImageStale` | Curated base not rebuilt in 14 days |
| `BuildQueueDeep` | Builds waiting > 15 min |
| `GCDeletedRunningImage` | 🔴 Should never happen |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | Registry healthy, ≥ 2 replicas across racks | `kubectl get pods -o wide` | Spread |
| **A2** | Layers on object storage; DB on T1 | Inspect config | Correct |
| **A3** | Push and pull work via CLI and from a pod | Test both | Work |
| **A4** | Registry behind OIDC; robot accounts for CI | Test | Works |
| **A5** | **Every node's mirror config points at the cache** | `talosctl read /etc/cri/conf.d/...` on 3 nodes | Configured |
| **A6** | 🧪 **Pulling `docker.io/library/busybox` goes through the cache** | Pull; check registry logs | Cached |
| **A7** | Upstream registries are reachable only through the proxy | Network policy test | Enforced |
| **A8** | Second pull of the same upstream image is served from cache | 📊 Measure both | Faster |
| **A9** | 🧪 **Registry outage does not stop pods with cached images** | Scale registry to 0; restart a pod | Starts |
| **A10** | Spegel deployed; nodes pull layers from peers | 🧪 64-node pull; watch registry load | P2P active |
| **A11** | 📊 **64-node pull time with and without mitigation recorded** | `bench-build.sh` | Recorded |
| **A12** | Pre-warm mechanism works | 🧪 Pre-warm then start | < 10 s |
| **A13** | Retention policy runs; old tags removed | Wait/trigger | Removed |
| **A14** | **`prod-*` tags excluded from retention** | Verify | Excluded |
| **A15** | **Tag immutability enforced for `prod-*`** | 🧪 Try to overwrite | Rejected |
| **A16** | Per-project quota enforced | Exceed | Blocked |
| **A17** | 🧪 GC never deletes a layer referenced by a running workload | Test | Never |
| **A18** | BuildKit builds an image end to end | Build | Works |
| **A19** | 📊 **Remote cache reduces build time as targeted** | Cold vs. warm on different builders | Met |
| **A20** | Builders run on the CPU pool, not GPU nodes | Inspect placement | CPU pool |
| **A21** | 🔒 Builders are rootless where possible | Inspect securityContext | Rootless or ADR'd |
| **A22** | Curated base images published and documented | `base-images.md` + registry | Present |
| **A23** | Base images rebuilt on a schedule | CronJob/workflow | Scheduled |
| **A24** | 🔒 **Images are cosign-signed; Phase 16's policy now passes** | Deploy a signed and an unsigned image | Signed passes, unsigned blocked |
| **A25** | 🔒 Trivy scans on push; Critical CVEs block `prod-*` | Push a vulnerable image | Blocked |
| **A26** | CVE exception process exists with owners and expiry | Read | Documented |
| **A27** | SBOM attached as an attestation | `cosign verify-attestation` | Present |
| **A28** | All alerts fire | Induce | Fire |

---

## ↩️ ROLLBACK

```bash
# Revert mirror config so nodes pull directly from upstream (degraded, functional)
# Apply the Talos patch with mirrors removed — requires a node config apply, not a reboot

# Relax signature enforcement if it blocks legitimate work (⚠️ security regression)
kubectl patch cpol require-signed-images --type merge \
  -p '{"spec":{"validationFailureAction":"Audit"}}'

# ⚠️ Do NOT delete the registry with running workloads that may need to restart.
```

> ⚠️ **The mirror rollback is the risky one**, because removing the mirror while upstream is unreachable leaves nodes with no source at all. Verify upstream reachability before reverting.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| `ImagePullBackOff` cluster-wide | Registry down, or mirror misconfigured | A9 — check whether cached images still start |
| Pulls slow from every node | Registry saturated by a thundering herd | A10/A12 — Spegel + pre-warm |
| Docker Hub rate limit errors | Mirror not configured on that node | A5 |
| Builds always cold | Remote cache not being imported/exported | Check the `--import-cache` ref and credentials |
| Build OOM | BuildKit memory limits | Increase; or split the Dockerfile |
| Signed image still rejected | Key mismatch, or the policy references a different key | Check the Kyverno policy's key against the signing key |
| Registry storage grows despite retention | GC not running, or the policy excludes everything | Check GC logs; review scope selectors |
| Deleted a repository and a running pod cannot restart | GC/deletion without the running-image check | A17 — this is why the check exists |
| `prod-` tag was overwritten | Immutability not enabled | A15 |
| Trivy blocks every CUDA image | Unfixable bundled CVEs | The exception process — A26 |
| Base images stale | Rebuild job failing silently | `BaseImageStale` alert |

---

## 🚫 DO NOT

- **Do not** leave nodes pulling directly from public registries.
- **Do not** run the registry on a fixed-size PVC.
- **Do not** allow `prod-*` tags to be mutable.
- **Do not** run garbage collection without a running-image check.
- **Do not** let a 64-node job pull cold without a mitigation.
- **Do not** grant a blanket CVE bypass — use owned, expiring exceptions.
- **Do not** run builders on GPU nodes.
- **Do not** store the signing key in the cluster's own registry or in Git.
- **Do not** build the notebook or portal experience here. Phases 43 and 46.

---

## 📤 HANDOFF

`evidence/phase-42/handoff.md` must state:

1. **📊 The 64-node pull table** — no mitigation vs. Spegel vs. pre-warmed. **The most operationally important number in the phase.**
2. **📊 Build times** — cold, remote-cache-warm, and from a curated base.
3. **🧪 The registry-outage test result** — proof that a registry failure degrades rather than halts.
4. **The curated base image set**, contents, and rebuild cadence.
5. **🔒 Signing and scanning as implemented**, and confirmation that Phase 16's fail-closed policy now passes.
6. **The CVE exception list** with owners and expiry dates.
7. **Retention and quota settings**, and projected storage growth (feeds Phase 56).
8. **Harbor vs. Zot decision** and why.

---

## ➡️ NEXT

**[PHASE-43 — Notebooks & Interactive Development](PHASE-43.md)** — the interface most users will actually touch, and the largest source of idle GPU allocation.
