#!/usr/bin/env python3
"""Parse the raw blob from collect.sh into a schema-valid NodeSpec YAML.

Usage:
    emit-nodespec.py <raw.txt> --name nx-c-r01-05 --archetype compute-gpu \
        --site hq --rack r01 --u 22 --pdu r01-pdu-a:12 --pdu r01-pdu-b:12 \
        [--status planned] > inventory/nodes/nx-c-r01-05.yaml

This is a best-effort parser: raw `dmidecode`/`lspci`/`nvidia-smi`/`lsblk` output is not a
stable machine-readable format. Every parsed value should be spot-checked against the raw
file before commit. Fields the parser cannot find are left absent (not guessed) so schema
validation surfaces the gap instead of silently shipping a wrong number.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def load_matrix(name: str) -> dict:
    with open(HERE / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def section(raw: str, name: str) -> str:
    """Return the text of one `===== NAME =====` section from collect.sh output."""
    m = re.search(rf"={5} {re.escape(name)} ={5}\n(.*?)(?=\n={5} |\Z)", raw, re.S)
    return m.group(1) if m else ""


def parse_numa_nodes(raw: str) -> int:
    m = re.search(r"available:\s*(\d+)\s*nodes", section(raw, "NUMA"))
    return int(m.group(1)) if m else 1


def parse_cpu(raw: str) -> dict:
    cpu_sec = section(raw, "CPU")
    out = {}
    m = re.search(r"Model name:\s*(.+)", cpu_sec)
    if m:
        out["model"] = m.group(1).strip()
    m = re.search(r"Socket\(s\):\s*(\d+)", cpu_sec)
    if m:
        out["sockets"] = int(m.group(1))
    m = re.search(r"Core\(s\) per socket:\s*(\d+)", cpu_sec)
    cores_per_socket = int(m.group(1)) if m else None
    m = re.search(r"CPU\(s\):\s*(\d+)", cpu_sec)
    threads_total = int(m.group(1)) if m else None
    if cores_per_socket and out.get("sockets"):
        out["cores"] = cores_per_socket * out["sockets"]
    if threads_total:
        out["threads"] = threads_total
    m = re.search(r"CPU max MHz:\s*([\d.]+)", cpu_sec)
    if m:
        out["baseClockGHz"] = round(float(m.group(1)) / 1000, 2)
    out["numaNodes"] = parse_numa_nodes(raw)
    return out


def parse_memory(raw: str) -> dict:
    mem_sec = section(raw, "MEMORY")
    out = {}
    m = re.search(r"Mem:\s+(\d+)Gi", mem_sec)
    if m:
        out["totalGB"] = int(m.group(1))
    out["ecc"] = bool(re.search(r"Error Correction Type:\s*(Single|Multi)-bit ECC", mem_sec))
    types = re.findall(r"Type:\s*(DDR\d)", mem_sec)
    if types:
        out["type"] = types[0]
    speeds = re.findall(r"Configured Memory Speed:\s*(\d+)\s*MT/s", mem_sec)
    if speeds:
        out["speedMTs"] = int(speeds[0])
    return out


def parse_gpus(raw: str, capability_matrix: dict) -> list:
    gpu_sec = section(raw, "GPU")
    gpus = []
    csv_block = re.search(r"index, ?name.*(?:\n.+)+", gpu_sec)
    if not csv_block:
        return gpus
    lines = [l for l in csv_block.group(0).splitlines() if l.strip()]
    for line in lines[1:]:
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 11:
            continue
        (idx, name, uuid, mem_total, bus_id, gen_max, width_max,
         gen_cur, width_cur, pw_limit, pw_max) = cols[:11]
        cap = capability_matrix["gpus"].get(name, dict(capability_matrix["default"]))
        gpu = {
            "index": int(idx),
            "model": name,
            "uuid": uuid,
            "vramGB": round(int(re.sub(r"[^\d]", "", mem_total)) / 1024),
            "pcieBusId": bus_id.lower(),
            "pcieGen": int(re.sub(r"[^\d]", "", gen_max)) if gen_max else None,
            "pcieWidth": f"x{re.sub(r'[^\\d]', '', width_max)}" if width_max else None,
            "nvlink": cap.get("nvlink", False),
            "p2pCapable": cap.get("p2pCapable", False),
            "gpudirectRdma": cap.get("gpudirectRdma", False),
            "migCapable": cap.get("migCapable", False),
            "eccSupported": cap.get("eccSupported", False),
        }
        if "computeCapability" in cap:
            gpu["computeCapability"] = cap["computeCapability"]
        if "tdpWatts" in cap:
            gpu["tdpWatts"] = cap["tdpWatts"]
        if cap.get("_requiresManualReview"):
            gpu["_requiresManualReview"] = True
        # numaAffinity is filled from the per-device PCI dump, matched by bus id
        numa_m = re.search(rf"{re.escape(bus_id.lower())}\s+numa_node=(-?\d+)", raw)
        gpu["numaAffinity"] = int(numa_m.group(1)) if numa_m else -1
        gpus.append(gpu)
    return gpus


def parse_nics(raw: str) -> list:
    net_sec = section(raw, "NET")
    nics = []
    for block in re.split(r"\n-- iface (\S+) --\n", net_sec)[1:]:
        pass
    parts = re.split(r"-- iface (\S+) --", net_sec)
    for i in range(1, len(parts), 2):
        name = parts[i]
        body = parts[i + 1]
        if name == "lo":
            continue
        nic = {"name": name}
        mac_m = re.search(r"^([0-9a-f]{2}(?::[0-9a-f]{2}){5})", body.strip(), re.M)
        if mac_m:
            nic["macAddress"] = mac_m.group(1)
        speed_m = re.search(r"Speed:\s*(\d+)\s*Mb/s", body)
        if speed_m:
            nic["speedGbps"] = int(int(speed_m.group(1)) / 1000)
        numa_m = re.search(r"^(-?\d+)$", body.strip(), re.M)
        if numa_m:
            nic["numaAffinity"] = int(numa_m.group(1))
        nic["rdma"] = {"capable": bool(re.search(r"mlx5_\d+", raw))}
        nic["role"] = "unused"
        nics.append(nic)
    return nics


def parse_storage(raw: str, disk_matrix: dict) -> list:
    storage_sec = section(raw, "STORAGE")
    disks = []
    m = re.search(r"\{.*\}\s*$", storage_sec.strip(), re.S)
    if not m:
        return disks
    try:
        lsblk = json.loads(m.group(0))
    except json.JSONDecodeError:
        return disks
    for dev in lsblk.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        model = (dev.get("model") or "").strip()
        cls = disk_matrix["disks"].get(model, dict(disk_matrix["default"]))
        size_bytes = dev.get("size")
        size_gb = None
        if isinstance(size_bytes, (int, float)):
            size_gb = round(size_bytes / 1_000_000_000)
        disk = {
            "device": f"/dev/{dev['name']}",
            "model": model or None,
            "serial": dev.get("serial"),
            "sizeGB": size_gb,
            "class": cls.get("class", "consumer-nvme"),
            "plp": cls.get("plp", False),
            "role": "unused",
            "tier": 0 if cls.get("class") == "consumer-nvme" else 1,
        }
        if "dwpd" in cls:
            disk["dwpd"] = cls["dwpd"]
        if cls.get("_requiresManualReview"):
            disk["_requiresManualReview"] = True
        disks.append(disk)
    return disks


def parse_firmware(raw: str) -> dict:
    fw = {}
    fw["iommu"] = bool(re.search(r"(DMAR: IOMMU enabled|AMD-Vi: Interrupt remapping enabled)", raw))
    wol_m = re.search(r"Wake-on:\s*(\w)", section(raw, "NET"))
    fw["wol"] = bool(wol_m and wol_m.group(1) == "g")
    bios_m = re.search(r"Version:\s*(\S+)", section(raw, "DMI"))
    if bios_m:
        fw["bios"] = bios_m.group(1)
    return fw


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("raw_file")
    ap.add_argument("--name", required=True)
    ap.add_argument("--archetype", required=True,
                     choices=["control", "compute-gpu", "compute-cpu", "storage", "infra"])
    ap.add_argument("--status", default="racked",
                     choices=["planned", "racked", "provisioning", "active", "quarantine", "failed", "retired"])
    ap.add_argument("--site", required=True)
    ap.add_argument("--rack", required=True)
    ap.add_argument("--u", type=int, required=True)
    ap.add_argument("--pdu", action="append", required=True)
    args = ap.parse_args()

    raw = Path(args.raw_file).read_text(encoding="utf-8", errors="replace")
    capability_matrix = load_matrix("capability-matrix.yaml")
    disk_matrix = load_matrix("disk-class-matrix.yaml")

    doc = {
        "apiVersion": "nexus.io/v1",
        "kind": "NodeSpec",
        "metadata": {"name": args.name},
        "spec": {
            "archetype": args.archetype,
            "status": args.status,
            "location": {"site": args.site, "rack": args.rack, "u": args.u, "pdu": args.pdu},
            "cpu": parse_cpu(raw),
            "memory": parse_memory(raw),
            "nics": parse_nics(raw),
            "storage": parse_storage(raw, disk_matrix),
            "firmware": parse_firmware(raw),
        },
    }
    gpus = parse_gpus(raw, capability_matrix)
    if gpus:
        doc["spec"]["gpus"] = gpus

    yaml.safe_dump(doc, sys.stdout, sort_keys=False, default_flow_style=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
