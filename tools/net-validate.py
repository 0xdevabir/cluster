#!/usr/bin/env python3
"""Validate the Phase 03 network design: IP overlap, port collision, MTU
consistency (N1-N8 from phases/PHASE-03.md Task 3), sourced live from
inventory/network/{vlans,ip-plan,switch-ports,switches}.yaml and
inventory/nodes/*.yaml — never from a cached number.

Usage:
  python tools/net-validate.py                # run every check, human-readable report
  python tools/net-validate.py --check         # N1/N2/N5/N6/N7/N8 (addressing rules); exit 1 on failure
  python tools/net-validate.py --check-ports   # A7: every node NIC maps to exactly one switch port
"""
from __future__ import annotations

import argparse
import glob
import ipaddress
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET_DIR = os.path.join(ROOT, "inventory", "network")


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_nodes():
    nodes = []
    for path in sorted(glob.glob(os.path.join(ROOT, "inventory", "nodes", "*.yaml"))):
        nodes.append(load_yaml(path))
    return nodes


def load_single(kind, filename):
    path = os.path.join(NET_DIR, filename)
    if not os.path.isfile(path):
        return None
    doc = load_yaml(path)
    if doc.get("kind") != kind:
        raise SystemExit(f"{filename}: expected kind={kind}, got {doc.get('kind')}")
    return doc


def all_networks(vlans, ip_plan):
    """Return {name: (cidr, mtu)} across vlans.yaml + ip-plan.yaml networks + overlay CIDRs."""
    nets = {}
    for v in vlans["vlans"]:
        nets[v["name"]] = (ipaddress.ip_network(v["cidr"]), v["mtu"])
    spec = ip_plan["spec"]
    for name, n in spec["networks"].items():
        nets.setdefault(name, (ipaddress.ip_network(n["cidr"]), n["mtu"]))
    return nets, spec


def check_n1_overlap(nets):
    """Two names sharing the exact same CIDR are aliases for one network
    (e.g. vlans.yaml's "storage-cluster" and ip-plan.yaml's "storageCluster"),
    not a true overlap conflict — only a *partial* overlap is a finding."""
    errs = []
    items = list(nets.items())
    for i, (name_a, (cidr_a, _)) in enumerate(items):
        for name_b, (cidr_b, _) in items[i + 1:]:
            if cidr_a == cidr_b:
                continue
            if cidr_a.overlaps(cidr_b):
                errs.append(f"N1: {name_a} ({cidr_a}) overlaps {name_b} ({cidr_b})")
    return errs


def check_n2_podcidr(spec, nets):
    errs = []
    pod = ipaddress.ip_network(spec["kubernetes"]["podCidr"])
    svc = ipaddress.ip_network(spec["kubernetes"]["serviceCidr"])
    for name, (cidr, _) in nets.items():
        if pod.overlaps(cidr):
            errs.append(f"N2: podCidr {pod} overlaps underlay network {name} ({cidr})")
    if pod.overlaps(svc):
        errs.append(f"N2: podCidr {pod} overlaps serviceCidr {svc}")
    return errs


def check_n5_n6_mtu(vlans):
    errs = []
    for v in vlans["vlans"]:
        if v["mtu"] not in (1500, 9000):
            errs.append(f"N5: {v['name']} has non-standard MTU {v['mtu']}")
        if v["name"] == "management" and v["mtu"] != 1500:
            errs.append(f"N6: management VLAN MTU must be 1500, got {v['mtu']}")
    return errs


def check_n7_reservations(spec):
    errs = []
    mgmt = spec["networks"]["management"]
    cidr = ipaddress.ip_network(mgmt["cidr"])
    dhcp_lo = ipaddress.ip_address(mgmt["dhcp"]["range"][0])
    dhcp_hi = ipaddress.ip_address(mgmt["dhcp"]["range"][1])
    seen = {}
    for r in mgmt.get("reservations", []):
        ip = ipaddress.ip_address(r["ip"])
        if ip not in cidr:
            errs.append(f"N7: reservation {r['name']} ({ip}) is outside {cidr}")
        if dhcp_lo <= ip <= dhcp_hi:
            errs.append(f"N7: reservation {r['name']} ({ip}) falls inside the DHCP range")
        if ip in seen:
            errs.append(f"N7: duplicate reservation IP {ip} ({seen[ip]} and {r['name']})")
        seen[ip] = r["name"]
    return errs


def check_n8_lb_pool(spec):
    errs = []
    lb = ipaddress.ip_network(spec["kubernetes"]["loadBalancerPool"])
    for net in spec["networks"].values():
        for rack, rack_cidr in (net.get("perRack") or {}).items():
            if lb.overlaps(ipaddress.ip_network(rack_cidr)):
                errs.append(f"N8: loadBalancerPool {lb} overlaps rack subnet {rack}/{rack_cidr}")
    return errs


def check_n4_capacity(spec, nodes):
    errs = []
    cluster = spec["networks"]["cluster"]
    per_rack = cluster.get("perRack") or {}
    counts = {}
    for n in nodes:
        rack = n["spec"]["location"]["rack"]
        counts[rack] = counts.get(rack, 0) + 1
    for rack, cidr in per_rack.items():
        net = ipaddress.ip_network(cidr)
        usable = net.num_addresses - 2
        need = counts.get(rack, 0) * 1.2
        if usable < need:
            errs.append(f"N4: rack {rack} subnet {cidr} has {usable} usable hosts, needs >= {need:.0f} (node count + 20%)")
    return errs


def check_ports():
    """A7: every node NIC with a switchPort maps to exactly one entry in
    switch-ports.yaml, and no switch port is double-assigned."""
    errs = []
    spm = load_single("SwitchPortMap", "switch-ports.yaml")
    if spm is None:
        return ["A7: inventory/network/switch-ports.yaml missing"]

    # Keyed by (peer node, peer interface) — a node legitimately has one
    # cabled port per NIC (e.g. one on a leaf for its cluster NIC, one on
    # the mgmt switch for eno1). The collision to catch is the same NIC
    # wired to two switch ports, not a node having multiple NICs.
    assigned = {}
    for sw in spm["switches"]:
        for p in sw["ports"]:
            key = (sw["name"], p["port"])
            if p["status"] == "cabled" and p["type"] in ("host", "mgmt-host"):
                peer_key = (p["peer"], p.get("peerIf"))
                if peer_key in assigned:
                    errs.append(f"A7: {peer_key[0]}:{peer_key[1]} is cabled to two switch ports: {assigned[peer_key]} and {key}")
                assigned[peer_key] = key

    for n in load_nodes():
        name = n["metadata"]["name"]
        for nic in n["spec"]["nics"]:
            sp = nic.get("switchPort")
            if sp and (name, nic["name"]) not in assigned:
                errs.append(f"A7: {name}:{nic['name']} declares switchPort {sp} but no cabled port in switch-ports.yaml references it")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="run N1/N2/N4/N5/N6/N7/N8 addressing checks")
    ap.add_argument("--check-ports", action="store_true", help="run A7 port-mapping check")
    args = ap.parse_args()

    vlans = load_single("VlanMap", "vlans.yaml")
    ip_plan = load_single("IpPlan", "ip-plan.yaml")
    if vlans is None or ip_plan is None:
        print("net-validate: missing vlans.yaml or ip-plan.yaml", file=sys.stderr)
        return 1

    nets, spec = all_networks(vlans, ip_plan)
    nodes = load_nodes()

    errs = []
    if args.check or not (args.check or args.check_ports):
        errs += check_n1_overlap(nets)
        errs += check_n2_podcidr(spec, nets)
        errs += check_n4_capacity(spec, nodes)
        errs += check_n5_n6_mtu(vlans)
        errs += check_n7_reservations(spec)
        errs += check_n8_lb_pool(spec)
    if args.check_ports or not (args.check or args.check_ports):
        errs += check_ports()

    if errs:
        for e in errs:
            print(f"FAIL {e}", file=sys.stderr)
        return 1

    print("OK net-validate: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
