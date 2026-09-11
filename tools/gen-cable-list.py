#!/usr/bin/env python3
"""Generate the physical cable run list from inventory/network/switch-ports.yaml
and inventory/racks.yaml (Phase 03 Task 6). Prints CSV to stdout: every
`cabled` port pair gets a length estimate (U-distance within a rack -> DAC/AOC
per docs/network/cabling-guide.md) and a color code from its VLAN/role.

Usage:
  python tools/gen-cable-list.py > docs/network/cable-run-list.csv
"""
from __future__ import annotations

import csv
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COLOR_BY_TYPE = {
    "host": "varies by node role (see role column)",
    "mgmt-host": "grey",
    "mgmt-infra": "grey",
    "mlag-peer": "yellow",
    "uplink": "yellow",
}
COLOR_BY_ROLE = {
    "cluster": "blue",
    "rdma": "red",
    "storage": "green",
    "management": "grey",
}
# meters per U, plus vertical run to top-of-rack switches — a conservative
# estimate for an M1 single-rack, all-intra-rack cable plan.
M_PER_U = 0.05
BASE_SLACK_M = 0.3


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def node_role_by_name():
    roles = {}
    for path in glob.glob(os.path.join(ROOT, "inventory", "nodes", "*.yaml")):
        n = load_yaml(path)
        name = n["metadata"]["name"]
        for nic in n["spec"]["nics"]:
            roles[(name, nic["name"])] = nic["role"]
    return roles


def node_u(name, racks):
    for path in glob.glob(os.path.join(ROOT, "inventory", "nodes", "*.yaml")):
        n = load_yaml(path)
        if n["metadata"]["name"] == name:
            return n["spec"]["location"]["rack"], n["spec"]["location"]["u"]
    return None, None


def switch_u(sw_name, racks):
    for rack in racks["racks"]:
        for sw in rack.get("switches", []):
            if sw["name"] == sw_name:
                return rack["name"], sw["u"]
        for kv in rack.get("kvm", []):
            if kv["name"] == sw_name:
                return rack["name"], kv["u"]
    return None, None


def media_for_length(m, speed):
    if speed == "1G":
        return "Cat6"
    if m <= 3.0:
        return "DAC"
    if m <= 30.0:
        return "AOC"
    return "fiber+optics"


def main():
    spm_path = os.path.join(ROOT, "inventory", "network", "switch-ports.yaml")
    racks_path = os.path.join(ROOT, "inventory", "racks.yaml")
    if not os.path.isfile(spm_path):
        print("gen-cable-list: missing inventory/network/switch-ports.yaml", file=sys.stderr)
        return 1

    spm = load_yaml(spm_path)
    racks = load_yaml(racks_path)
    roles = node_role_by_name()

    writer = csv.writer(sys.stdout)
    writer.writerow(["switch", "port", "peer", "peer_if", "speed", "role", "length_m", "media", "color", "label"])

    seen_pairs = set()
    for sw in spm["switches"]:
        sw_rack, sw_u = switch_u(sw["name"], racks)
        for p in sw["ports"]:
            if p["status"] != "cabled":
                continue
            pair_key = tuple(sorted([f"{sw['name']}:{p['port']}", f"{p['peer']}:{p.get('peerIf', '')}"]))
            if p["type"] in ("mlag-peer",) and pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            if p["type"] in ("host", "mgmt-host"):
                _, peer_u = node_u(p["peer"], racks)
                role = roles.get((p["peer"], p["peerIf"]), "unknown")
                color = COLOR_BY_ROLE.get(role, "grey")
            else:
                _, peer_u = switch_u(p["peer"], racks)
                role = p["type"]
                color = COLOR_BY_TYPE.get(p["type"], "yellow")

            if sw_u is not None and peer_u is not None:
                length_m = round(BASE_SLACK_M + abs(sw_u - peer_u) * M_PER_U + 0.3, 2)
            else:
                length_m = None

            speed = p.get("speed", "")
            media = media_for_length(length_m, speed) if length_m is not None else (p.get("media") or "TBD")
            label = f"{sw['name']}:{p['port']} <-> {p['peer']}:{p.get('peerIf', '')}"
            writer.writerow([sw["name"], p["port"], p["peer"], p.get("peerIf", ""), p.get("speed", ""), role, length_m, media, color, label])

    return 0


if __name__ == "__main__":
    sys.exit(main())
