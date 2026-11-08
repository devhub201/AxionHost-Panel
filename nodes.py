"""AxionHost Panel - remote nodes over SSH (any SSH server: a VPS, a home server, or a Pinggy tunnel address).
The panel runs `docker ...` / `lxc ...` on the remote machine through the OpenSSH client.
 - password is handed to ssh through SSH_ASKPASS (environment of that one process), never on a command line
 - first connection pins the host key (accept-new); a changed key is refused until an admin resets trust
 - one multiplexed connection per node (ControlMaster) so many small commands stay fast, even over Pinggy
 - commands are built with shlex.join from validated argv lists, never from a user-supplied string"""
import os, re, shlex, subprocess

HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]{0,200}[A-Za-z0-9])?$|^\[?[0-9A-Fa-f:]{2,45}\]?$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_\-]{0,31}$")


class TargetError(ValueError): pass


def parse_target(text):
    """Accepts: host | host:port | user@host:port | tcp://host:port | 'ssh user@host -p 2222' | 'ssh -p 2222 user@host'.
    Returns (host, port, user or ''). sshx links are refused with a clear reason."""
    t = (text or "").strip()
    if not t: raise TargetError("Enter an address")
    if "sshx.io" in t.lower() or t.lower().startswith("https://sshx"):
        raise TargetError("sshx links are browser-only terminals and cannot be automated. Use a Pinggy address (tcp://host:port) or any SSH host:port instead.")
    if t.lower().startswith(("http://", "https://")): raise TargetError("That is a web link, not an SSH address")
    port, user = None, ""
    if t.lower().startswith("ssh "):
        parts = shlex.split(t)[1:]; host = ""; i = 0
        while i < len(parts):
            p = parts[i]
            if p == "-p" and i + 1 < len(parts): port = parts[i + 1]; i += 2; continue
            if p.startswith("-p") and len(p) > 2: port = p[2:]; i += 1; continue
            if p == "-l" and i + 1 < len(parts): user = parts[i + 1]; i += 2; continue
            if p.startswith("-"): i += 2 if p in ("-i", "-o", "-J", "-F") else 1; continue
            host = p; i += 1
        t = host
    t = re.sub(r"^(tcp|ssh)://", "", t, flags=re.I).strip().rstrip("/")
    if "@" in t: user, t = t.rsplit("@", 1)
    if t.startswith("["):                                              # [v6]:port
        m = re.match(r"^\[([0-9A-Fa-f:]+)\](?::(\d+))?$", t)
        if not m: raise TargetError("Bad IPv6 address")
        host, port = m.group(1), port or m.group(2)
    elif t.count(":") == 1: host, _, p2 = t.partition(":"); port = port or p2
    else: host = t
    try: port = int(port) if port else 22
    except ValueError: raise TargetError("Bad port")
    if not 1 <= port <= 65535: raise TargetError("Port must be 1-65535")
    host = host.strip()
    if not HOST_RE.match(host): raise TargetError("Bad host name")
    if user and not USER_RE.match(user): raise TargetError("Bad user name")
    return host, port, user


class SSHNode:
    def __init__(self, node, password, datadir):
        self.id, self.host, self.port, self.user = node["id"], node["ssh_host"], int(node["ssh_port"] or 22), node["ssh_user"] or "root"
        if not HOST_RE.match(self.host or "") or not USER_RE.match(self.user): raise ValueError("bad ssh settings")
        self.password = password or ""
        self.dir = os.path.join(datadir, "ssh"); os.makedirs(self.dir, mode=0o700, exist_ok=True)
        self.known = os.path.join(self.dir, "known_hosts_%d" % self.id)
        self.ctl = self._ctl_dir()
        self.askpass = os.path.join(self.dir, "askpass.sh")
        if not os.path.exists(self.askpass) or "AXP_SSH_PW" not in open(self.askpass).read():
            open(self.askpass, "w").write("#!/bin/sh\nprintf '%s\\n' \"$AXP_SSH_PW\"\n")
        os.chmod(self.askpass, 0o700)

    def _ctl_dir(self):
        for base in ("/run/axp-ssh", os.path.join(self.dir, "ctl")):
            try: os.makedirs(base, mode=0o700, exist_ok=True); os.chmod(base, 0o700); return base
            except OSError: continue
        return self.dir

    def env(self):
        e = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ")}
        e.update(SSH_ASKPASS=self.askpass, SSH_ASKPASS_REQUIRE="force", DISPLAY=os.environ.get("DISPLAY", ":0"), AXP_SSH_PW=self.password)
        return e

    def base(self, tty=False):
        a = ["ssh", "-p", str(self.port), "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=" + self.known,
             "-o", "PreferredAuthentications=password,keyboard-interactive", "-o", "PubkeyAuthentication=no", "-o", "NumberOfPasswordPrompts=1",
             "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=3", "-o", "LogLevel=ERROR",
             "-o", "ControlMaster=auto", "-o", "ControlPath=" + os.path.join(self.ctl, "%C"), "-o", "ControlPersist=120"]
        if tty: a.append("-tt")
        return a + ["%s@%s" % (self.user, self.host)]

    def argv(self, args, tty=False):
        """Full local argv that runs `args` on the node (used for pty terminals)."""
        return self.base(tty) + [shlex.join([str(x) for x in args])]

    def run(self, args, input=None, timeout=300):
        try:
            p = subprocess.run(self.argv(args), input=input, capture_output=True, text=True, timeout=timeout, env=self.env())
        except subprocess.TimeoutExpired: return 124, "timeout"
        except FileNotFoundError: return 127, "ssh client not found on the panel server (apt install openssh-client)"
        out = (p.stdout + p.stderr).strip()
        if p.returncode == 255: return 255, nice_error(out)
        return p.returncode, out

    def reset_trust(self):
        for f in (self.known, self.known + ".old"):
            try: os.remove(f)
            except OSError: pass
        subprocess.run(["ssh", "-o", "ControlPath=" + os.path.join(self.ctl, "%C"), "-O", "exit", "%s@%s" % (self.user, self.host), "-p", str(self.port)], capture_output=True, env=self.env(), timeout=10)


def nice_error(out):
    o = out.lower()
    if "permission denied" in o: return "Wrong SSH user or password (permission denied)"
    if "host key verification failed" in o or "identification has changed" in o: return "Host key changed - if you moved the server or the Pinggy address was reused, press 'Reset trust' and test again"
    if "connection refused" in o: return "Connection refused (is SSH running on that port?)"
    if "timed out" in o or "timeout" in o: return "Connection timed out (wrong address/port, or the server is offline)"
    if "could not resolve" in o or "name or service not known" in o: return "Host name could not be resolved"
    if "no route to host" in o or "network is unreachable" in o: return "No route to host (IPv6-only/IPv4-only mismatch?)"
    return (out or "SSH failed")[-300:]


# Collected on the node by the health check (read-only, fixed script).
HEALTH_SCRIPT = r"""
echo "NPROC $(nproc 2>/dev/null || echo 0)"
echo "MEM $(awk '/MemTotal/{print $2*1024}' /proc/meminfo 2>/dev/null)"
echo "DISK $(df -B1 --output=size,avail / 2>/dev/null | tail -1)"
echo "LOAD $(cut -d' ' -f1 /proc/loadavg 2>/dev/null)"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then echo "DOCKER ok"; else echo "DOCKER missing"; fi
if command -v qemu-system-x86_64 >/dev/null 2>&1 && command -v qemu-img >/dev/null 2>&1; then echo "QEMU ok"; else echo "QEMU missing"; fi
if command -v lxc >/dev/null 2>&1 && lxc info >/dev/null 2>&1; then echo "LXD ok"; else echo "LXD missing"; fi
"""


def parse_health(out):
    d = {}
    for l in out.splitlines():
        k, _, v = l.partition(" ")
        d[k] = v.strip()
    def num(s, f=int):
        try: return f(s)
        except (TypeError, ValueError): return None
    disk = (d.get("DISK") or "").split()
    return dict(cpu_total=num(d.get("NPROC")), ram_total=num(d.get("MEM")), disk_total=num(disk[0]) if len(disk) == 2 else None,
                disk_free=num(disk[1]) if len(disk) == 2 else None, load1=num(d.get("LOAD"), float), docker=d.get("DOCKER") == "ok", lxd=d.get("LXD") == "ok", qemu=d.get("QEMU") == "ok")
