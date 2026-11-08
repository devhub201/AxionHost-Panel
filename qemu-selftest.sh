#!/usr/bin/env bash
# Runs the exact VM-creation steps by hand and shows which one fails. Usage: bash qemu-selftest.sh
D=/tmp/axtest; B=$(ls /var/lib/axion-vm/_base/*.img 2>/dev/null | head -1)
ok(){ printf ' \033[32m✔\033[0m %s\n' "$*"; }; bad(){ printf ' \033[31m✘ %s\033[0m\n' "$*"; }
rm -rf $D; mkdir -p $D; cd $D
echo "== 1 tools"; for b in qemu-system-x86_64 qemu-img genisoimage sshpass python3; do command -v $b >/dev/null && ok $b || bad "$b missing"; done
echo "== 2 base image: ${B:-NONE}"; [ -s "$B" ] && ok "$(du -h "$B" | cut -f1)" || bad "image nahi mili"; qemu-img info "$B" 2>&1 | grep -E "file format|virtual size" | sed 's/^/    /'
echo "== 3 disk"; qemu-img create -f qcow2 -F qcow2 -b "$B" disk.qcow2 20G 2>&1 | tail -2 && ok disk
echo "== 4 seed iso"; printf '#cloud-config\nhostname: t\n' > user-data; printf 'instance-id: t\n' > meta-data
genisoimage -quiet -output seed.iso -volid cidata -joliet -rock user-data meta-data 2>&1 | tail -3; [ -s seed.iso ] && ok seed.iso || bad "seed.iso nahi bana"
echo "== 5 qemu start"; ACC="tcg,thread=multi"; CPU=max; [ -r /dev/kvm ] && [ -w /dev/kvm ] && { ACC=kvm; CPU=host; }; echo "   accel=$ACC"
qemu-system-x86_64 -name axtest -machine q35 -accel $ACC -cpu $CPU -smp 2 -m 1024M \
  -drive file=$D/disk.qcow2,if=virtio,format=qcow2 -drive file=$D/seed.iso,if=virtio,media=cdrom,readonly=on \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:29999-:22 -device virtio-net-pci,netdev=n0 -device virtio-rng-pci \
  -display none -serial file:$D/console.log -qmp unix:$D/qmp.sock,server,nowait -pidfile $D/pid -daemonize 2>&1 | head -5
sleep 6; if kill -0 "$(cat $D/pid 2>/dev/null)" 2>/dev/null; then ok "qemu chal raha hai (pid $(cat $D/pid))"; echo "== 6 boot log (6s):"; tail -3 $D/console.log | cut -c1-120
  kill -9 "$(cat $D/pid)"; else bad "qemu start/exit ho gaya - upar error dekho"; fi
echo "== 7 worker log"; tail -4 /var/log/axionhost/worker.log 2>/dev/null | cut -c1-200
rm -rf $D
