# PHASE 40 — LLM Inference Serving with vLLM & KServe

| | |
|---|---|
| **Stage** | 6 — Distributed Compute Frameworks |
| **Estimated effort** | 5–6 hours |
| **Depends on** | 17, 19, 26, 28, 36 |
| **Blocks** | 41, 44, 46, 47 |
| **Risk** | 🟠 Medium-High — this is the first *production* workload; users depend on uptime |
| **Blast radius** | Serving availability and latency SLOs |
| **Architecture refs** | `ARCHITECTURE.md#l83-multi-node-llm-inference`, `#w2-life-of-an-inference-request`, `ULTIMATE-PLAN.md#5-target-capability-model` |

---

## 🎯 MISSION

Deliver **production LLM inference**: vLLM behind KServe, with autoscaling, multi-node tensor parallelism via LeaderWorkerSet, an OpenAI-compatible API, KV-cache-aware routing, and latency SLOs that hold under load. This is where the cluster stops being a batch system and becomes a service.

> 💡 **WHY inference is architecturally different from everything before it.** Training jobs are batch: they can queue, be preempted, and restart. Inference is a **service**: a request arrives now and must be answered in milliseconds, or a user-facing product breaks. That difference propagates everywhere — inference workloads must be `nexus-critical` priority, must not be preempted, need anti-affinity for availability rather than affinity for bandwidth, need warm capacity rather than just-in-time scheduling, and need a completely different observability model (p99 latency, not throughput).

> ⚠️ **The dominant constraint: VRAM, and the cold-start cliff.** A 70B model in FP16 needs ~140 GB — six RTX 4090s minimum, spanning two nodes with tensor parallelism over a fabric with no NVLink. And loading a 140 GB model from object storage takes minutes, so **scale-to-zero is not viable for large models**. Sizing, quantization, and model placement decisions dominate; the serving framework is the easy part.

---

## ✅ PREFLIGHT

```bash
# GPU VRAM inventory — determines what can be served at all
kubectl get nodes -L nexus.io/gpu.model,nexus.io/gpu.vram-gb

# 📊 Cross-node bandwidth: tensor parallelism across nodes depends on it
cat benchmarks/baselines/b5-nccl-busbw.json

# Model storage and fast local cache
s5cmd ls s3://nexus-models/ 2>/dev/null || echo "create the model bucket"
kubectl get sc nexus-fast nexus-scratch

# Ingress + identity (Phase 17)
kubectl get gateway,securitypolicy -A
```

---

## 📦 DELIVERABLES

```
clusters/nexus-prod/serving/
  kserve-values.yaml
  lws-controller.yaml               # LeaderWorkerSet for multi-node TP
  servingruntimes/
    vllm-single-gpu.yaml
    vllm-tensor-parallel.yaml       # within a node
    vllm-multinode.yaml             # ⚠️ across nodes via LWS
  gateway-inference-extension.yaml  # KV-cache-aware routing
  inference-clusterqueue.yaml       # nexus-critical, non-preemptible
  model-cache-daemonset.yaml        # pre-pull weights to local NVMe
tools/serving/
  deploy-model.sh                   # the sanctioned path
  bench-serving.sh                  # 📊 B10: latency + throughput under load
  model-sizer.sh                    # "will this model fit, and how?"
  warm-cache.sh
docs/user/
  serving-guide.md
  model-deployment.md
benchmarks/baselines/b10-inference.json
observability/rules/serving-alerts.yaml
dashboards/inference-slo.json
evidence/phase-40/{preflight,acceptance,handoff,deviations}.md
```

---

## 🔧 VERSION PINNING

| Component | Version |
|---|---|
| KServe | `v0.15.0` |
| vLLM | `0.7.2` |
| LeaderWorkerSet | `v0.5.1` |
| Gateway API Inference Extension | `v0.3.0` |
| Envoy Gateway | Phase 17 pin |

---

## 📋 TASKS

### Task 1 — 🎯 Model sizing (do the arithmetic before deploying anything)

**`tools/serving/model-sizer.sh`** implements this and refuses impossible deployments.

```
VRAM needed ≈ model_weights + KV_cache + activations + overhead

model_weights = params × bytes_per_param
    FP16/BF16: 2      INT8: 1      FP8: 1      AWQ/GPTQ 4-bit: 0.5

KV_cache      = 2 × layers × kv_heads × head_dim × seq_len × batch × bytes
    ⚠️ This is what actually limits your throughput, not the weights.
    Llama-3-8B, 8192 ctx, 32 concurrent requests ≈ 16 GB of KV cache alone.

overhead ≈ 10–15 % (CUDA context, fragmentation, activation peaks)
```

**The deployment decision table for 24 GB cards:**

| Model | FP16 weights | Fits? | Recommended |
|---|---|---|---|
| 7–8B | 16 GB | ✅ 1 GPU, but little KV headroom | 1 GPU FP16, or **INT8/AWQ for real throughput** |
| 13B | 26 GB | ❌ | TP=2 within a node, or 4-bit on 1 GPU |
| 34B | 68 GB | ❌ | TP=4 within one node |
| 70B | 140 GB | ❌ | **TP=8 across 2 nodes** ⚠️ or 4-bit → TP=4 on one node ✅ |
| 70B AWQ 4-bit | 35 GB | ❌ | TP=2 within a node ✅ **preferred** |

> 💡 **Quantization is more valuable here than on datacenter GPUs.** Going from FP16 to 4-bit AWQ turns a 70B model from a two-node, fabric-dependent deployment into a two-GPU, single-node deployment — eliminating cross-node tensor parallelism entirely, along with its latency and its failure modes. **Prefer quantization over multi-node TP wherever quality permits**, and measure the quality delta so the choice is informed.

> ⚠️ **Tensor parallelism across nodes is the deployment shape to avoid if possible.** TP performs an `all_reduce` on *every layer* — dozens to hundreds of small collectives per token. Over PCIe-to-NIC-to-fabric with no NVLink or GDR, that is a latency tax on every token generated. If you must do it: keep TP within a node, use pipeline parallelism across nodes, and require single-leaf-domain placement.

---

### Task 2 — ServingRuntimes

**Single-GPU (the common case):**
```yaml
kind: ClusterServingRuntime
metadata: { name: vllm-single }
spec:
  containers:
    - name: kserve-container
      image: vllm/vllm-openai:v0.7.2
      args:
        - --model=/mnt/models
        - --served-model-name={{.Name}}
        - --gpu-memory-utilization=0.90      # ⚠️ leave headroom; 0.95 causes OOM under load
        - --max-model-len=8192
        - --enable-prefix-caching            # ⚠️ big win for shared system prompts
        - --enable-chunked-prefill           # smooths latency under mixed load
        - --disable-log-requests             # 🔒 do not log user prompts by default
      resources:
        claims: [{ name: gpu }]
      volumeMounts:
        - { name: dshm, mountPath: /dev/shm }   # ⚠️ vLLM needs it for TP
```

**Multi-node TP via LeaderWorkerSet:**
```yaml
kind: LeaderWorkerSet
spec:
  replicas: 1
  leaderWorkerTemplate:
    size: 2                                   # 2 pods = one serving unit
    restartPolicy: RecreateGroupOnPodRestart  # ⚠️ MANDATORY — see below
    leaderTemplate:   { ... ray head + vLLM ... }
    workerTemplate:   { ... ray worker ... }
```

> ⚠️ **`RecreateGroupOnPodRestart` is not optional for multi-node TP.** If one worker in a TP group restarts, the remaining pods hold a broken NCCL communicator and serve nothing while appearing healthy. The whole group must recycle together. This is the LWS-specific version of the gang principle from Phase 31.

> ⚠️ **`--gpu-memory-utilization=0.95` is the classic production incident.** vLLM pre-allocates KV cache to this fraction. At 0.95 there is no headroom for activation spikes on a long request, and the server OOMs mid-request under load — after passing every test at low traffic. Use 0.90, and validate under the load Task 5 generates.

---

### Task 3 — ⚠️ Cold start and model caching

The number that dominates the operational experience.

```
Loading a 70B FP16 model:
  From S3 over 100 GbE at ~2 GB/s:      ~70 s download
  + safetensors load into VRAM:         ~40 s
  + CUDA graph capture / warmup:        ~30 s
  ────────────────────────────────────────────────
  TOTAL COLD START:                     ~2.5 minutes  ⚠️
```

**Mitigations, in order:**

| Technique | Effect |
|---|---|
| **`model-cache-daemonset`: pre-pull weights to `/scratch` on serving nodes** | Removes the download: ~70 s → ~5 s |
| Pin models to specific nodes (node affinity) | The cache is always warm where it matters |
| **`minReplicas: 1` for large models — never scale to zero** | Removes cold start from the request path entirely |
| Keep a warm standby replica | Instant failover |
| Quantization | Smaller weights load faster |

> 🚫 **Scale-to-zero is attractive and wrong for large models.** A request arriving at a scaled-to-zero 70B deployment waits 2.5 minutes. Use `minReplicas: 1` for anything a user or product depends on; reserve scale-to-zero for small experimental models where a slow first request is acceptable. **Make this the documented default, because the cost saving looks tempting until the first incident.**

---

### Task 4 — Autoscaling on the right signal

CPU utilization is meaningless for LLM serving. Scale on queue depth.

| Metric | Suitability |
|---|---|
| CPU % | ❌ Useless |
| GPU utilization | ❌ Near 100 % whenever anything runs |
| **`vllm:num_requests_waiting`** | ✅ **The right signal** — direct queueing pressure |
| `vllm:gpu_cache_usage_perc` | ✅ Good secondary — KV cache pressure |
| Time-to-first-token p95 | ✅ SLO-aligned |

```yaml
# KEDA / HPA on the queue depth
metrics:
  - type: Pods
    pods:
      metric: { name: vllm_num_requests_waiting }
      target: { type: AverageValue, averageValue: "4" }
minReplicas: 1                # ⚠️ not 0 for large models
maxReplicas: 8
behavior:
  scaleUp:   { stabilizationWindowSeconds: 30 }
  scaleDown: { stabilizationWindowSeconds: 600 }   # ⚠️ slow down — cold starts are expensive
```

> 💡 **Asymmetric scaling windows are the correct shape here.** Scale up fast (30 s) because queued requests are user-visible pain; scale down slowly (10 min) because a wrong scale-down costs a 2.5-minute cold start on the next burst. The asymmetry is the point.

**KV-cache-aware routing** (Gateway API Inference Extension): route a request to the replica that already holds its prefix in cache. For chat with long shared system prompts this is a large win — the prefill work is already done. Enable it and measure the improvement.

---

### Task 5 — 📊 Benchmark serving (B10)

**`tools/serving/bench-serving.sh`** — load-test with a realistic request distribution, not fixed-length prompts.

| Metric | Target (7B, 1 GPU) | Measured |
|---|---|---|
| **TTFT p50 / p95 / p99** (time to first token) | < 200 / 500 / 800 ms | |
| **TPOT p50 / p95** (time per output token) | < 25 / 50 ms | |
| Throughput at p95 TTFT < 500 ms | ≥ 1,500 tok/s | |
| Max concurrent requests before SLO breach | Record it | |
| **Throughput vs. latency curve** | ⚠️ The most useful artifact | |
| Cold start (cached weights) | < 30 s | |
| Cold start (uncached) | Record it | |
| Prefix caching hit benefit | Measure with/without | |

📊 **The throughput-vs-latency curve is what capacity planning actually needs:**
```
Concurrency  Throughput   TTFT p95   TPOT p95
     1           95 tok/s    120 ms     11 ms
     8          610 tok/s    180 ms     14 ms
    32        1,740 tok/s    410 ms     23 ms   ← the knee
    64        2,050 tok/s  1,180 ms     48 ms   ⚠️ SLO breached
   128        2,110 tok/s  3,400 ms    112 ms   ⚠️ saturated
→ Operating point: 32 concurrent per replica. Scale out beyond that.
```

📊 **Also measure quantization quality vs. speed**, since Task 1 recommends it:
```
Model      Quality (your eval)   Throughput   VRAM   Deployment
FP16          baseline           1,740 tok/s   16 GB  1 GPU, tight
INT8          -0.4 %             2,890 tok/s    9 GB  1 GPU, comfortable
AWQ 4-bit     -1.8 %             3,210 tok/s    5 GB  1 GPU, lots of KV headroom
→ Publish this so model owners choose knowingly.
```

---

### Task 6 — Production concerns

**Placement — the opposite of training:**
```yaml
# Training wants affinity (pack tight). Serving wants ANTI-affinity (spread).
affinity:
  podAntiAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      - topologyKey: topology.kubernetes.io/rack     # replicas in different racks
```

**Priority and preemption:**
```yaml
priorityClassName: nexus-critical         # Phase 30
nexus.io/preemptible: "false"             # Phase 33 — and pay the 1.5× rate
```
> 💡 **Inference paying 1.5× under Phase 33's rate card is correct, not a bug.** Non-preemptible capacity genuinely costs the cluster flexibility. Making that visible ensures teams deploy serving replicas deliberately rather than leaving experiments running as "services."

**Security (Phase 04/17):**

| Control | Requirement |
|---|---|
| Authentication | OIDC or API key via the Gateway SecurityPolicy — never an open endpoint |
| Rate limiting | Per-tenant, per-key |
| 🔒 Prompt logging | **Off by default.** Prompts may contain sensitive data. Opt-in, with retention limits. |
| Model provenance | Signed images; models from the registry only (Phase 44) |
| Input size limits | Cap `max_tokens` and prompt length to prevent resource exhaustion |

**Alerts:**

| Alert | Threshold |
|---|---|
| `InferenceSLOBreach` | TTFT p95 > target for 5 min |
| `InferenceQueueDeep` | `num_requests_waiting` > 20 for 5 min |
| `InferenceKVCacheFull` | Cache usage > 95 % |
| `InferenceReplicaDown` | Below `minReplicas` |
| `InferenceColdStartSlow` | Startup > 2× the cached baseline |
| `InferenceErrorRate` | 5xx > 1 % |
| `LWSGroupDegraded` | A TP group with a missing pod |
| `ModelCacheStale` | A serving node lacks a pinned model |

---

## 🧪 ACCEPTANCE CRITERIA

| # | Criterion | Verification | Pass |
|---|---|---|---|
| **A1** | KServe healthy; a single-GPU model serves | Deploy 7B | Serves |
| **A2** | OpenAI-compatible API works (`/v1/chat/completions`) | curl + SDK | Works |
| **A3** | TP within a node works (13B/34B) | Deploy | Serves |
| **A4** | **Multi-node TP via LWS works** | Deploy 70B FP16 | Serves |
| **A5** | **`RecreateGroupOnPodRestart` set; killing one worker recycles the group** | 🧪 Kill a worker | Group recycles |
| **A6** | `model-sizer.sh` correctly predicts fit/no-fit | 🧪 5 models | Correct |
| **A7** | `gpu-memory-utilization` ≤ 0.90 | Read the runtime | Set |
| **A8** | 🧪 **No OOM under sustained max load** | Load test 30 min | No OOM |
| **A9** | Model cache DaemonSet pre-pulls weights | Inspect `/scratch` | Present |
| **A10** | 📊 Cold start with warm cache < 30 s | Measure | Met |
| **A11** | `minReplicas: 1` enforced for large models | Policy test | Enforced |
| **A12** | Autoscaling triggers on `num_requests_waiting` | 🧪 Load | Scales |
| **A13** | Scale-down window ≥ 10 min | Read config | Set |
| **A14** | KV-cache-aware routing improves shared-prefix workloads | 📊 A/B measure | Improved |
| **A15** | Prefix caching measurably helps | 📊 With/without | Measured |
| **A16** | 📊 **B10 recorded: TTFT/TPOT percentiles + the latency-throughput curve** | `bench-serving.sh` | Complete |
| **A17** | 📊 Operating point (concurrency at SLO) identified | Curve | Identified |
| **A18** | 📊 **Quantization quality-vs-speed table produced** | Benchmark | Complete |
| **A19** | Replicas anti-affine across racks | Inspect | Spread |
| **A20** | Serving is `nexus-critical` and non-preemptible | Inspect | Set |
| **A21** | 🧪 A training job cannot preempt an inference replica | Try | Cannot |
| **A22** | Endpoint requires authentication | Unauthenticated request | Rejected |
| **A23** | Rate limiting works per tenant | 🧪 Exceed | Limited |
| **A24** | 🔒 Prompt logging off by default | Check logs | Not logged |
| **A25** | 🧪 A node failure under a replica recovers within the SLO | Kill a node | Recovers |
| **A26** | All alerts fire | Induce | Fire |
| **A27** | SLO dashboard shows TTFT/TPOT/errors/saturation | Open | Renders |

---

## ↩️ ROLLBACK

```bash
# Scale down a model without deleting it (keeps config and cache)
kubectl scale inferenceservice <name> --replicas=0

# Roll back to a previous model version — KServe canary
kubectl patch isvc <name> --type merge \
  -p '{"spec":{"predictor":{"canaryTrafficPercent":0}}}'

# ⚠️ Removing KServe takes down live services. Announce it; drain traffic first.
```

> 💡 **Serving is the first workload with real users**, so rollback is a traffic-management problem, not just a deletion problem. Use KServe's canary traffic splitting for every model update: 5 % → 25 % → 100 %, with the SLO dashboard as the gate.

---

## 🔧 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| OOM under load, fine when idle | `gpu-memory-utilization` too high | A7 — use 0.90 |
| First request very slow, rest fast | Cold start | Warm cache; `minReplicas: 1` |
| Multi-node TP much slower than expected | Cross-node all_reduce per layer | Prefer quantization + single-node TP |
| TP group serves nothing but looks healthy | One pod restarted, NCCL broken | A5 — `RecreateGroupOnPodRestart` |
| Autoscaler never triggers | Scaling on CPU or GPU util | Use `num_requests_waiting` |
| Constant scale up/down flapping | Symmetric stabilization windows | Asymmetric: 30 s up, 600 s down |
| TTFT good, TPOT bad | Batch too large; or memory-bandwidth-bound decode | Tune `max_num_seqs`; consider quantization |
| Throughput plateaus early | KV cache exhausted | Check `gpu_cache_usage_perc`; reduce `max_model_len` |
| Quality regression after quantization | Expected trade | A18's table — pick knowingly |
| Model loads but produces garbage | Wrong chat template or tokenizer | Verify against the model card |
| Requests time out at the gateway | Gateway timeout < generation time | Raise it for streaming endpoints |
| Inference replica preempted | Priority/preemptibility not set | A20/A21 |

---

## 🚫 DO NOT

- **Do not** use `gpu-memory-utilization` above 0.90.
- **Do not** scale large models to zero.
- **Do not** autoscale on CPU or GPU utilization.
- **Do not** deploy multi-node TP without verifying quantization cannot avoid it.
- **Do not** omit `RecreateGroupOnPodRestart` on LWS.
- **Do not** expose an inference endpoint without authentication.
- **Do not** log user prompts by default.
- **Do not** let inference be preemptible.
- **Do not** update a production model without canary traffic splitting.
- **Do not** build the workflow orchestration layer here. Phase 41.

---

## 📤 HANDOFF

`evidence/phase-40/handoff.md` must state:

1. **📊 B10 results** — TTFT/TPOT percentiles, the latency-throughput curve, and the identified operating point per model class.
2. **📊 The quantization quality-vs-speed table** — the basis for every future model deployment decision.
3. **📊 Cold-start times**, cached and uncached, and the caching strategy in force.
4. **Which models are deployed**, their parallelism shape, and why (especially any multi-node TP and its justification).
5. **The autoscaling configuration** and observed behavior under load.
6. **🧪 The failure-recovery result** — node loss under a live replica.
7. **The SLOs committed to** — feeds Phase 47's SLO and on-call work.
8. **Capacity consumed by serving** — non-preemptible GPUs unavailable to batch, for Phase 34's accounting and Phase 56's planning.
9. **Security controls in force** on the endpoints.

---

## ➡️ NEXT

**[PHASE-41 — Workflow Orchestration & Compute Gate (G6/G9)](PHASE-41.md)** — tie the frameworks together into pipelines, and close Stage 6.
