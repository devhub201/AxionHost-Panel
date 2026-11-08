import os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core, worker, qemu_vm
P = F = 0
def ck(n, c, x=""):
    global P, F
    if c: P += 1
    else: F += 1; print("FAIL", n, x)
d = tempfile.mkdtemp(); cfg = core.load_config(os.path.join(d, "n.json")); cfg.update(db_path=os.path.join(d, "p.db"), backend="qemu", secret_key="k" * 32, qemu_boot_timeout=20)
LOG = []; STATE = {"up": False, "kvm": False}
def run(args, input=None, timeout=300):
    LOG.append((list(args), input)); a = args[0]
    if a == "sh" and "kill -0" in args[2]: return (0, "") if STATE["up"] else (1, "")
    if a == "sh" and "/dev/kvm" in args[2]: return (0, "") if STATE["kvm"] else (1, "")
    if a == "qemu-system-x86_64": STATE["up"] = True
    if a == "python3" and "system_powerdown" in args[-1]: STATE["up"] = False
    if a == "test": return 1, ""
    if a == "timeout": return 0, "SSH-"
    if a == "sh" and "ps -o" in args[2]: return 0, " 12.5 2097152"
    return 0, ""
w = worker.Worker(cfg, run=run, sleep=lambda s: None); db = w.db
db.execute("INSERT INTO users(username,email,pw_hash,bolts,created_at) VALUES('u','u@x.com','x',1,?)", (int(time.time()),))
node = db.execute("SELECT id FROM nodes LIMIT 1").fetchone()
cols = [c[1] for c in db.execute("PRAGMA table_info(servers)")]
s = {"container": "axp-7-abc123", "ssh_port": 23001, "cpu": 2, "ram": 4, "disk": 20, "node_id": node[0] if node else 1, "root_pw_enc": core.enc(cfg["secret_key"], "pw'x"), "id": 7}
w.provision_qemu(s, "ubuntu:22.04")
cmds = [" ".join(map(str, c[0])) for c in LOG]
q = next(c for c in LOG if c[0][0] == "qemu-system-x86_64")[0]
ck("tcg when no kvm", "-accel" in q and q[q.index("-accel") + 1].startswith("tcg"))
ck("ram/cpu", "-m" in q and q[q.index("-m") + 1] == "4096M" and q[q.index("-smp") + 1] == "2")
ck("ssh hostfwd", any("hostfwd=tcp:0.0.0.0:23001-:22" in x for x in q))
ck("qcow2 disk size", any(c[:2] == ["qemu-img", "create"] and "20G" in c for c, _ in LOG))
ck("image downloaded", any(c[0] == "curl" and "jammy" in " ".join(c) for c, _ in LOG))
ck("password has no quote", "pw'x" not in next(i for c, i in LOG if c[0] == "tee" and i and "chpasswd" in i) and "pwx" in next(i for c, i in LOG if c[0] == "tee" and i and "chpasswd" in i))
ck("no shell interpolation of name", all("axp-7-abc123" not in c[2] for c, _ in LOG if c[0] == "sh" and len(c) > 2 and c[1] == "-c"))
STATE["kvm"] = True; w.q_stop(s); LOG.clear(); w.q_start(s); q = LOG[-1][0]
ck("kvm when available", q[q.index("-accel") + 1] == "kvm" and q[q.index("-cpu") + 1] == "host")
ck("stop powers down", STATE["up"] is True)
w.q_stop(s); ck("graceful stop", STATE["up"] is False)
ck("stats parse", w.q_stats(s) == (12.5, 2097152 * 1024))
STATE["up"] = True; w.q_delete(s); ck("delete removes dir", any(c[0] == "rm" and "-rf" in c and c[-1].endswith("/axp-7-abc123") for c, _ in LOG))
import nodes
ck("health qemu", nodes.parse_health("QEMU ok\nDOCKER missing")["qemu"] is True)
try: w.qdir({"container": "../etc"}); ck("bad name rejected", False)
except worker.JobError: ck("bad name rejected", True)
print("qemu tests: %d passed, %d failed" % (P, F)); sys.exit(1 if F else 0)
