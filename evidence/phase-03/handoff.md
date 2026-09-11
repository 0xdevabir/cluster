# Phase 03 — Handoff

Date: 2026-09-11
Operator: agent (session_01883KZe15yE4EHBCxZ8aNgj), for 0xdevabir

1. **Fabric class chosen:** RoCEv2 over Ethernet, single fabric (ADR-006). `pool=training` (4 compute-gpu nodes) at 100 Gbps; `pool=control`/`pool=storage` at 25 Gbps. All real hardware, no aspirational upgrade assumed.

2. **The full IP plan** — `inventory/network/ip-plan.yaml`. Phase 07 (DHCP/DNS), Phase 09 (Talos static IPs), Phase 13 (Cilium), Phase 21 (SR-IOV VF addressing), and Phase 27 (Ceph networks) all read it. **`spec.upstream` is still `<REPLACE-ME>`** — pending Phase 02's facility survey; do not let a later phase silently invent a gateway.

3. **VLAN IDs:** 100 (management, 1500 MTU), 200 (cluster, 9000), 300 (storage, 9000), 301 (storage-cluster, 9000), 400 (rdma, 9000, lossless). Needed by Phase 09 (Talos interface config) and Phase 21 (Multus NetworkAttachmentDefinitions).

4. **BGP ASNs:** spine 65000 (unused, reserved), `r01` leaves 65101, `r01` hosts 65201. **Not operationally enabled at M1** — no spine exists. Phase 13's `CiliumBGPClusterConfig` should target these ASNs but the BGP session itself does not need to come up until M3. Unnumbered (RFC 5549), ECMP max-paths 8, BFD 300ms×3.

5. **The RoCE contract** — `docs/network/roce-contract.md`. Phase 21 implements it on both ends; Phase 22 validates it. **Open finding:** the racked SN2410 leaves have 16 MB buffer vs. a computed ≥ 36 MB target at full 32-port population — not a risk today (8/32 ports used), but treat "≥ 32 MB shared buffer" as a hard M2+ leaf-switch purchasing criterion (Phase 05).

6. **Switch models and buffer sizes** — `inventory/network/switches.yaml`. Both `r01-leaf-a`/`r01-leaf-b` are NVIDIA SN2410, 32 ports, 16 MB buffer each, MLAG pair. `r01-mgmt` model is still `<REPLACE-ME>` (48-port 1GbE, exact make/model not yet purchased).

7. **Nodes excluded from the training pool on network grounds:** none. All 4 `pool=training` nodes have 100 Gbps RoCEv2-capable NICs.

8. **The cable run list** — generate with `task network:cable-list` (`tools/gen-cable-list.py`). At M1, every run is intra-rack DAC (r01 only); hand the CSV to whoever racks the hardware. 24 cabled runs currently defined (8 host-to-leaf, 8 host-to-mgmt, 2 leaf-to-leaf MLAG peer-link, 2 leaf-to-mgmt, 2 PDU-to-mgmt, 2 KVM-to-mgmt).

9. **M1 topology deviates from the phase file's own staged table** (single leaf at M1, MLAG at M2) because Phase 02 already racked an MLAG pair — see `deviations.md` D1. **Open gap this creates:** no host is dual-homed yet, so losing `r01-leaf-a` today isolates all 8 nodes despite the MLAG pair existing. This is not fixed by this phase (host NICs are single-port by inventory — Phase 01) and should be revisited if dual-homing becomes a requirement before M2.

10. **`inventory/schema/network.schema.json`** is now a real discriminated-union schema (was a Phase 00 stub) covering `VlanMap`, `IpPlan`, `SwitchPortMap`, and `SwitchInventory` — all four files in `inventory/network/` validate against it via the existing `validate_dir "inventory/network" "network.schema.json"` call in `tools/validate-inventory.sh` (no changes needed there).

11. **3 control nodes' `switchPort` fields were added** (`inventory/nodes/nx-m-r01-0{1,2,3}.yaml`, cluster NIC only) to match the convention the compute/storage nodes already used. Their management NICs (`eno1`) do not carry a `switchPort` field — that wiring lives only in `inventory/network/switch-ports.yaml` (`r01-mgmt`), since `nodespec.schema.json`'s `switchPort` pattern (`r[0-9]{2}-(leaf|mgmt)-[ab]:...`) doesn't match the actual `r01-mgmt` switch name (no `-a`/`-b` suffix) — flagged here rather than silently worked around by editing a Phase 01-owned schema out of scope.
