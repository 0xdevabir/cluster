# Inventory

`inventory/` is the machine-readable source of truth for physical reality —
every node, rack, network path, and power circuit in the NEXUS fleet. It is
validated on every commit (`task validate:inventory`), which makes malformed
hardware data **impossible to commit**.

## Layout

| Path | Contents | Schema | Owning phase |
|---|---|---|---|
| `nodes/*.yaml` | One file per physical node | `schema/nodespec.schema.json` | 01 |
| `racks.yaml` | Rack inventory | `schema/rack.schema.json` (stub) | 02 |
| `network/ip-plan.yaml`, `vlans.yaml`, `switch-ports.yaml` | Network plan | `schema/network.schema.json` (stub) | 03 |
| `power/pdu-map.yaml`, `circuits.yaml` | Power distribution | `schema/power.schema.json` (stub) | 02 |

`*.example` files are templates, not live data — `task validate:inventory`
skips them. Copy one, fill it in, and drop the `.example` suffix.

## Node naming

`nx-<archetype>-r<rack>-<slot>` — see `ARCHITECTURE.md#x3--naming-labeling--namespace-taxonomy`.
Archetype letters: `c`=compute-gpu, `u`=compute-cpu, `s`=storage, `m`=control, `i`=infra.

## Cross-document integrity checks

Beyond per-file schema validation, `tools/validate-inventory.sh` enforces:

- every node name is unique and matches its filename
- every MAC address is globally unique across the fleet
- every `(rack, u)` location is occupied by at most one node
- every node has exactly one boot device
