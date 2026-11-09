#!/usr/bin/env bash
# Boots a test VM and times how long it takes to reach login / SSH. Usage: bash qemu-bootwatch.sh [max_minutes=20]
MAX=${1:-20}; D=/tmp/axboot; B=$(ls /var/lib/axion-vm/_base/ubuntu_22_04.img 2>/dev/null | head -1); [ -n "$B" ] || B=$(ls /var/lib/axion-vm/_base/*.img | head -1)
pkill -f "name axboot" 2>/dev/null; rm -rf $D; mkdir -p $D; cd $D
qemu-img create -q -f qcow2 -F qcow2 -b "$B" disk.qcow2 10G
printf '#cloud-config\nhostname: axboot\ndisable_root: false\nssh_pwauth: true\nchpasswd:\n  expire: false\n  users:\n    - {name: root, password: axtest123, type: text}\nruncmd:\n  - [ systemctl, restart, ssh ]\n' > user-data; printf 'instance-id: axboot\n' > meta-data
genisoimage -quiet -output seed.iso -volid cidata -joliet -rock user-data meta-data
ACC="tcg,thread=multi"; CPU=max; [ -r /dev/kvm ] && [ -w /dev/kvm ] && { ACC=kvm; CPU=host; }
qemu-system-x86_64 -name axboot -machine q35 -accel $ACC -cpu $CPU -smp 2 -m 2048M -drive file=$D/disk.qcow2,if=virtio,format=qcow2 -drive file=$D/seed.iso,if=virtio,media=cdrom,readonly=on \
 -netdev user,id=n0,hostfwd=tcp:127.0.0.1:29998-:22 -device virtio-net-pci,netdev=n0 -device virtio-rng-pci -display none -serial file:$D/console.log -pidfile $D/pid -daemonize || exit 1
echo "accel=$ACC  load: $(cut -d' ' -f1-3 /proc/loadavg)  (max ${MAX} min). Ctrl+C se band."; T0=$(date +%s); L=0
while [ $(( $(date +%s) - T0 )) -lt $((MAX*60)) ]; do
  sleep 10; t=$(( $(date +%s) - T0 )); n=$(wc -l < console.log); kill -0 "$(cat pid)" 2>/dev/null || { echo "❌ qemu mar gaya"; break; }
  B4=$(timeout 3 bash -c 'exec 3<>/dev/tcp/127.0.0.1/29998; head -c 4 <&3' 2>/dev/null)
  printf '%4ss  console lines=%s  last: %s\n' $t $n "$(tail -1 console.log | tr -d '\r' | cut -c1-70)"
  [ "$B4" = "SSH-" ] && { echo "✅ SSH READY after ${t}s -> VM sahi hai, bas boot time itna lagta hai"; break; }
  grep -q "login:" console.log && [ $L = 0 ] && { L=1; echo "   (login prompt aaya ${t}s par - SSH bas aane wala hai)"; }
done
kill -9 "$(cat pid 2>/dev/null)" 2>/dev/null; echo "--- console tail:"; tail -6 console.log | cut -c1-110; rm -rf $D
