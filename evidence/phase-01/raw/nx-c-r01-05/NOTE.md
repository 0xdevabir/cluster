# nx-c-r01-05 — discovery status

No physical machine exists yet for this node record. `spec.status: planned`; all fields in
`inventory/nodes/nx-c-r01-05.yaml` are spec-sheet values, not measured facts.

When hardware is racked for this slot, run:

```bash
sudo tools/discover-node.sh --name nx-c-r01-05 --archetype compute-gpu \
    --site hq --rack r01 --u 5 --pdu "r01-pdu-a:5" --pdu "r01-pdu-b:5" \
    > inventory/nodes/nx-c-r01-05.yaml.new
```

then diff against the planned record, reconcile, and replace this NOTE.md with the real
`collect.sh` output (and `probes.txt`, `bios.md` — Phase 01 Tasks 3-5).
