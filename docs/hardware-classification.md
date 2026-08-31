# Hardware Classification Rules

How a discovered (or planned) machine is assigned an `archetype` and `pool`. Applied in
order — the first matching rule wins. Reproducible, not vibes: re-running these rules
against the same `inventory/nodes/*.yaml` facts must always produce the same archetype.

```text
IF  has >=1 NVIDIA GPU  AND  NIC >= 25 Gbps with rdma.capable
    -> compute-gpu, pool=training

ELIF has >=1 NVIDIA GPU  AND  NIC >= 10 Gbps
    -> compute-gpu, pool=general        # cannot join a distributed training gang

ELIF has >=1 NVIDIA GPU  AND  NIC < 10 Gbps
    -> compute-gpu, pool=inference|interactive   # single-node work only
    -> RAISE FINDING: this node needs a NIC upgrade before it can train (R-01)

ELIF >=6 data disks  AND  >=1 enterprise-nvme with plp=true  AND  memory.ecc=true
    -> storage

ELIF memory.ecc=true  AND  >=1 enterprise disk  AND  no GPU  AND  high single-thread CPU
    -> control      (need exactly 3 or 5 of these)

ELIF no GPU
    -> compute-cpu  (or infra, if 2-3 are needed for platform services)
```

## Post-conditions that MUST hold across the fleet

- `count(control) ∈ {3, 5}`
- `count(storage) ≥ 3` at M1, `≥ 5` at M3 (Ceph needs ≥3 failure domains)
- every `compute-gpu` in `pool=training` has `topology.aligned == true` — its highest-bandwidth
  GPU and its highest-speed RDMA-capable NIC share the same `numaAffinity`
- no two control nodes share a rack (once ≥3 racks exist)

## Applying a rule

1. Read the node's `spec.gpus`, `spec.nics`, `spec.storage`, `spec.memory.ecc` from its
   NodeSpec (measured facts if discovered, spec-sheet facts if `status: planned`).
2. Walk the rules top to bottom; the first match sets `spec.archetype` and the
   `pool` label in `metadata.labels`.
3. Re-run `task validate:inventory` — the schema's `allOf` conditionals
   (`compute-gpu` requires `gpus`, `control` requires `memory.ecc == true`) catch a
   misclassification immediately.
4. Any node that fails a post-condition is a finding for `inventory/fleet-summary.md`,
   not a silently-accepted exception.

## Applied to the M1 pilot fleet (`ULTIMATE-PLAN.md §9`)

| Node | GPU | NIC | Disks | ECC | Rule matched | Archetype / pool |
|---|---|---|---|---|---|---|
| nx-m-r01-01..03 | none | 1 GbE mgmt + 25 GbE cluster | 1x enterprise NVMe (etcd) | true | control rule | `control` |
| nx-c-r01-04..07 | 1x RTX 4090 | ConnectX-6 100 GbE, RDMA-capable | 1x consumer NVMe scratch + boot | false | `>=1 GPU AND NIC>=25Gbps+RDMA` | `compute-gpu`, `pool=training` |
| nx-s-r01-08 | none | 25 GbE storage | 6x enterprise NVMe (PLP) | true | storage rule | `storage` |

All eight are `status: planned` — no physical unit exists yet. See
`evidence/phase-01/deviations.md` for why Tasks 3-5 (physical discovery, runtime probes,
BIOS pass) are deferred until hardware is racked.
