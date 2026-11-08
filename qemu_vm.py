"""QEMU backend: real VMs (own kernel) via KVM when /dev/kvm is usable, else software emulation (TCG - what KVM-X sets up).
Works inside restricted containers: no root caps, no Docker, no cgroups. Networking = QEMU user-mode with SSH/port hostfwd.
Every command is a fixed argv list (no shell); user input never reaches an argv except validated name/ports/ints."""
import json, re, shlex

IMAGES = {  # image key -> cloud image
    "ubuntu:22.04": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img",
    "ubuntu:24.04": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
    "images:debian/12": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2",
}
DEFAULT_IMAGE = "ubuntu:22.04"

QMP_PY = r'''
import socket,sys,json,time
s=socket.socket(socket.AF_UNIX);s.settimeout(8);s.connect(sys.argv[1]);f=s.makefile("rw")
f.readline();f.write(json.dumps({"execute":"qmp_capabilities"})+"\n");f.flush();f.readline()
f.write(sys.argv[2]+"\n");f.flush()
for _ in range(20):
    l=f.readline()
    if '"return"' in l or '"error"' in l: print(l.strip());break
'''

def user_data(name, password):
    pw = password.replace("'", "")
    return ("#cloud-config\nhostname: %s\nmanage_etc_hosts: true\ndisable_root: false\nssh_pwauth: true\nchpasswd:\n  expire: false\n  users:\n    - {name: root, password: '%s', type: text}\n"
            "write_files:\n  - path: /etc/ssh/sshd_config.d/99-axion.conf\n    content: |\n      PermitRootLogin yes\n      PasswordAuthentication yes\nruncmd:\n  - [ systemctl, restart, ssh ]\n" % (name, pw))

class QemuMixin:
    def qdir(self, s):
        if not self.cname_ok(s): raise self.JobErr("bad container name")
        return "%s/%s" % (str(self.cfg.get("qemu_dir") or "/var/lib/axion-vm").rstrip("/"), s["container"])

    def cname_ok(self, s):
        import core
        return bool(core.CONT_RE.match(s["container"]))

    def qx(self, *args, input=None, timeout=300, check=True):
        rc, out = self._exec([str(a) for a in args], input, timeout)
        if check and rc != 0: raise self.JobErr("%s failed: %s" % (args[0], out[-300:]))
        return rc, out

    def qmp(self, s, obj, check=False):
        return self.qx("python3", "-c", QMP_PY, self.qdir(s) + "/qmp.sock", json.dumps(obj), check=check, timeout=30)

    def q_running(self, s):
        rc, _ = self.qx("sh", "-c", 'p=$(cat "$1/pid" 2>/dev/null) && [ -n "$p" ] && kill -0 "$p"', "_", self.qdir(s), check=False, timeout=15)
        return rc == 0

    def q_accel(self):
        rc, _ = self.qx("sh", "-c", "[ -r /dev/kvm ] && [ -w /dev/kvm ]", check=False, timeout=10)
        return ("kvm", "host") if rc == 0 else ("tcg,thread=multi", "max")

    def q_hostfwds(self, s):
        la = self.listen_addr_q(s)
        return "hostfwd=tcp:%s:%d-:22" % (la, int(s["ssh_port"]))

    def listen_addr_q(self, s):
        import core
        node = self.db.execute("SELECT listen FROM nodes WHERE id=?", (s["node_id"],)).fetchone()
        la = (node["listen"] if node else "") or "0.0.0.0"
        if not core.LISTEN_RE.match(la) or ":" in la or la in ("0.0.0.0", "::", "[::]"): return ""      # QEMU hostfwd takes IPv4 only; empty = all IPv4 addresses
        return la

    def q_start(self, s):
        d = self.qdir(s)
        if self.q_running(s): return
        accel, cpu = self.q_accel()
        self.qx("rm", "-f", d + "/qmp.sock", d + "/pid", check=False, timeout=15)
        self.qx("qemu-system-x86_64", "-name", s["container"], "-machine", "q35", "-accel", accel, "-cpu", cpu,
                "-smp", int(s["cpu"]), "-m", "%dM" % (int(s["ram"]) * 1024),
                "-drive", "file=%s/disk.qcow2,if=virtio,format=qcow2" % d, "-drive", "file=%s/seed.iso,if=virtio,media=cdrom,readonly=on" % d,
                "-netdev", "user,id=n0," + self.q_hostfwds(s), "-device", "virtio-net-pci,netdev=n0", "-device", "virtio-rng-pci",
                "-display", "none", "-serial", "file:%s/console.log" % d, "-qmp", "unix:%s/qmp.sock,server,nowait" % d,
                "-pidfile", d + "/pid", "-daemonize", timeout=60)

    def q_stop(self, s, wait=45):
        if not self.q_running(s): return
        d = self.qdir(s); self.qmp(s, {"execute": "system_powerdown"})
        for _ in range(wait // 3):
            if not self.q_running(s): return
            self.sleep(3)
        self.qx("sh", "-c", 'kill -9 "$(cat "$1/pid")" 2>/dev/null; true', "_", d, check=False, timeout=15)

    def q_wait_ssh(self, s, timeout=None):
        timeout = int(timeout or self.cfg.get("qemu_boot_timeout", 900))
        for _ in range(max(1, timeout // 10)):
            rc, out = self.qx("timeout", "4", "bash", "-c", 'exec 3<>/dev/tcp/127.0.0.1/$1 && head -c 4 <&3', "_", int(s["ssh_port"]), check=False, timeout=15)
            if rc == 0 and "SSH" in out: return True
            self.sleep(10)
        return False

    def provision_qemu(self, s, image):
        import core
        d = self.qdir(s); key = image if image in IMAGES else DEFAULT_IMAGE; url = IMAGES[key]
        base_dir = d.rsplit("/", 1)[0] + "/_base"; base = "%s/%s.img" % (base_dir, re.sub(r"[^a-z0-9]+", "_", key))
        self.q_stop(s, 9); self.qx("rm", "-rf", d, check=False, timeout=60); self.qx("mkdir", "-p", d, base_dir)
        if self.qx("test", "-s", base, check=False, timeout=10)[0] != 0:
            self.qx("curl", "-fL", "--retry", "3", "-o", base + ".part", url, timeout=3600); self.qx("mv", base + ".part", base)
        self.qx("qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", base, d + "/disk.qcow2", "%dG" % int(s["disk"]))
        pw = self.dec_pw(s)
        self.qx("tee", d + "/user-data", input=user_data(s["container"], pw), timeout=15)
        self.qx("tee", d + "/meta-data", input="instance-id: %s\nlocal-hostname: %s\n" % (s["container"], s["container"]), timeout=15)
        self.qx("sh", "-c", 'cd "$1" && (genisoimage -quiet -output seed.iso -volid cidata -joliet -rock user-data meta-data || xorriso -as mkisofs -quiet -o seed.iso -V cidata -J -r user-data meta-data)', "_", d, timeout=60)
        try:
            self.q_start(s)
            if not self.q_wait_ssh(s): raise self.JobErr("VM did not open SSH in time (TCG boots are slow - raise qemu_boot_timeout). Log: %s/console.log" % d)
        except Exception:
            self.q_stop(s, 6); self.qx("rm", "-rf", d, check=False, timeout=60); raise

    def q_delete(self, s):
        self.q_stop(s, 9); self.qx("rm", "-rf", self.qdir(s), check=False, timeout=120)

    def q_stats(self, s):
        """(cpu% of one core, resident bytes) of the qemu process."""
        rc, out = self.qx("sh", "-c", 'p=$(cat "$1/pid" 2>/dev/null) && ps -o %cpu=,rss= -p "$p"', "_", self.qdir(s), check=False, timeout=15)
        try: c, r = out.split(); return float(c), int(r) * 1024
        except ValueError: return None, None

    def q_resetpw(self, s, pw):
        """Change the guest root password by SSHing in with the current one (sshpass)."""
        old = self.dec_pw(s)
        script = "echo 'root:%s' | chpasswd" % pw.replace("'", "")
        rc, out = self.qx("sshpass", "-p", old, "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10", "-p", int(s["ssh_port"]), "root@127.0.0.1", script, check=False, timeout=40)
        if rc != 0: raise self.JobErr("could not reach the VM over SSH: %s" % out[-150:])

    def q_hostfwd_sync(self, s, rows):
        """Apply extra port forwards live through the QEMU monitor (no VM restart)."""
        la = self.listen_addr_q(s)
        for r in rows:
            if r["proto"] not in ("tcp", "udp"): continue
            spec = "%s:%s:%d-:%d" % (r["proto"], la, int(r["public_port"]), int(r["target_port"]))
            self.qmp(s, {"execute": "human-monitor-command", "arguments": {"command-line": ("hostfwd_remove n0 " if r["status"] == "deleting" else "hostfwd_add n0 ") + spec}})
