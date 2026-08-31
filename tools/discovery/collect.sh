#!/usr/bin/env bash
# NEXUS hardware discovery — READ ONLY. Writes nothing to the target.
# Usage:  sudo ./collect.sh > /tmp/$(hostname)-raw.txt
set -uo pipefail   # NOT -e: a missing tool must not abort the whole collection

section() { printf '\n===== %s =====\n' "$1"; }
try()     { printf '\n--- %s ---\n' "$*"; "$@" 2>&1 || echo "(command failed: $*)"; }

section HOSTNAME
hostname
cat /etc/machine-id 2>/dev/null

section DMI
try dmidecode -t system
try dmidecode -t baseboard
try dmidecode -t bios
try dmidecode -t processor
try dmidecode -t memory

section CPU
try lscpu
try cat /proc/cpuinfo
try lscpu -e                       # per-CPU topology: NUMA, socket, core, cache

section NUMA
try numactl --hardware
try lstopo-no-graphics --of console
try cat /sys/devices/system/node/node*/meminfo

section MEMORY
try free -h
try dmidecode -t 17                # per-DIMM: size, speed, ECC, rank
try grep -i ecc /sys/devices/system/edac/mc/mc*/ce_count

section PCI
try lspci -nnvv                    # LnkCap/LnkSta are here — CRITICAL
try lspci -tv                      # topology tree: which root complex
for d in /sys/bus/pci/devices/*; do
  printf '%s numa_node=%s\n' "$(basename "$d")" "$(cat "$d/numa_node" 2>/dev/null)"
done

section IOMMU
try dmesg -T
try ls /sys/kernel/iommu_groups/

section GPU
try nvidia-smi -q                  # full query: UUID, VBIOS, power limits, PCIe
try nvidia-smi topo -m             # GPU<->GPU and GPU<->NIC affinity matrix
try nvidia-smi --query-gpu=index,name,uuid,memory.total,pci.bus_id,pcie.link.gen.max,pcie.link.width.max,pcie.link.gen.current,pcie.link.width.current,power.limit,power.max_limit --format=csv
try nvidia-smi nvlink -s

section NET
for d in /sys/class/net/*; do
  i="$(basename "$d")"
  [ "$i" = "lo" ] && continue
  printf '\n-- iface %s --\n' "$i"
  cat "/sys/class/net/$i/address" 2>/dev/null
  cat "/sys/class/net/$i/device/numa_node" 2>/dev/null
  readlink -f "/sys/class/net/$i/device" 2>/dev/null
  ethtool "$i" 2>&1 | head -30
  ethtool -i "$i" 2>&1
  ethtool -k "$i" 2>&1 | head -20
done
try ibv_devinfo -v                 # RDMA verbs devices
try rdma link show
try ls /sys/class/infiniband/

section STORAGE
try lsblk -O -J                    # JSON, includes rota/model/serial/size
try nvme list
for n in /dev/nvme[0-9]n[0-9]; do
  [ -e "$n" ] || continue
  try nvme id-ctrl "$n"
  try nvme smart-log "$n"
done
try smartctl --scan
while read -r dev _; do
  [ -n "$dev" ] || continue
  try smartctl -a "$dev"
done < <(smartctl --scan 2>/dev/null)

section FIRMWARE
try efibootmgr -v
try mokutil --sb-state

section KERNEL
try uname -a
try cat /proc/cmdline

section POWER
try ls /sys/class/hwmon
try sensors
try turbostat --num_iterations 1 --interval 1
