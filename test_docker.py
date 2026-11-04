import json, os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core, app as appmod, worker, ops
P = F = 0
def ck(n, c, x=""):
    global P, F
    if c: P += 1
    else: F += 1; print("FAIL", n, x)
d = tempfile.mkdtemp(); cfg = core.load_config(os.path.join(d, "n.json")); cfg.update(db_path=os.path.join(d, "p.db"), base_url="http://localhost", backend="docker")
a = appmod.create_app(cfg); db = a.db; GB = 10 ** 9; t = int(time.time())
ck("worker image mapping", [worker.docker_image(x) for x in ("ubuntu:22.04", "images:debian/12", "images:alpine/3.20", "images:almalinux/9", "local:foo")] == ["ubuntu:22.04", "debian:12", "alpine:3.20", "almalinux:9", "foo"])
ck("mapped images pass validation", all(core.DOCKER_IMG_RE.match(worker.docker_image(x[2])) for x in core.SEED_IMAGES))
db.execute("INSERT INTO users(username,email,pw_hash,bolts,created_at) VALUES('u','u@x.com',?,100000,?)", (core.hash_password("password123"), t))
import re
B = "http://localhost"
class CC:
    def __init__(s):
        s.c = a.test_client(); s.csrf = ""
        r = s.c.post("/api/auth/login", json=dict(email="u@x.com", password="password123"), base_url=B, headers={"Origin": B})
        m = re.search(r'name="csrf" content="([^"]+)"', s.c.get("/dashboard", base_url=B).get_data(as_text=True)); s.csrf = m.group(1) if m else ""
    def get(s, u, **k): return s.c.get(u, **k)
    def post(s, u, **k): return s.c.post(u, headers={"X-CSRF-Token": s.csrf, "Origin": B}, **k)
c = CC()
cat = c.get("/api/catalog", base_url=B).get_json()
ck("vm plans hidden on docker", all(p["type"] != "vm" for p in cat["plans"]) and len(cat["plans"]) == 10, [p["key"] for p in cat["plans"]])
vm = db.execute("SELECT key FROM plans WHERE type='vm'").fetchone()["key"]
r = c.post("/api/servers", json=dict(plan=vm, node=1, image="ubuntu-22", name="vmtest"), base_url=B).get_json()
ck("vm deploy refused before charging", not r.get("ok") and db.execute("SELECT bolts FROM users").fetchone()[0] == 100000, r)
calls = []; inputs = []; state = {"quota_ok": True}
def fake(args, input=None, timeout=0):
    calls.append(args)
    if input: inputs.append((args, input))
    if args[0] != "docker": return 1, "unexpected"
    v = args[1]
    if v == "run" and "--storage-opt" in args and not state["quota_ok"]: return 125, "Error response from daemon: --storage-opt is supported only for overlay over xfs with 'pquota' mount option"
    if v == "ps": return 0, "axp-1-aaaaaa|running\naxp-1-bbbbbb|exited\nother|running"
    if v == "inspect": return 0, "\n".join("/%s|%d" % (n, (5 if n.endswith("aaaaaa") else 0) * GB) for n in args[5:])
    if v == "top": return 0, "COMMAND\nsshd\nxmrig\n"
    if v == "exec" and args[-1] == "true": return 0, ""
    return 0, ""
w = worker.Worker(cfg, db=core.connect(cfg["db_path"]), run=fake, sleep=lambda x: None)
ck("docker mode on", w.docker_mode)
r = c.post("/api/servers", json=dict(plan="nano", node=1, image="images-debian" if False else "debian-12", name="d1"), base_url=B).get_json(); ck("deploy queued", r.get("ok"), r)
sid = r["id"]; w.process_jobs()
s = db.execute("SELECT * FROM servers WHERE id=?", (sid,)).fetchone(); ck("server running", s["status"] == "running", dict(s))
run = [x for x in calls if x[:2] == ["docker", "run"]][-1]
j = " ".join(run)
ck("docker run flags", "--memory" in run and "--cpus" in run and "--init" in run and "--pids-limit" in run and "--restart" in run, j)
nano = db.execute("SELECT * FROM plans WHERE key='nano'").fetchone()
ck("limits from plan", run[run.index("--memory") + 1] == "%dm" % (nano["ram"] * 1024) and run[run.index("--cpus") + 1] == str(nano["cpu"]) and run[run.index("--storage-opt") + 1] == "size=%d" % (nano["disk"] * GB), j)
ck("ssh port published on node listen address", ("0.0.0.0:%d:22" % s["ssh_port"]) in run, j)
ck("image mapped for docker", "debian:12" in run, j)
ck("password only via stdin, never in argv", any(i[1].startswith("root:") for i in inputs) and not any("root:" in " ".join(x) for x in calls))
ck("no lxc commands at all", all(x[0] == "docker" for x in calls))
bad = {"--privileged", "-v", "--volume", "--mount", "--net", "--network", "--pid", "--cap-add", "--device", "--ipc", "--userns", "--security-opt"}
ck("no privileged/host-mount flags", not (bad & set(run)) and "docker.sock" not in j, j)
# quota unsupported -> falls back, still deploys
calls.clear(); state["quota_ok"] = False
r2 = c.post("/api/servers", json=dict(plan="nano", node=1, image="ubuntu-22", name="d2"), base_url=B).get_json(); w.process_jobs()
ck("fallback when no disk quota support", db.execute("SELECT status FROM servers WHERE id=?", (r2["id"],)).fetchone()[0] == "running" and sum(1 for x in calls if x[:2] == ["docker", "run"]) == 2)
state["quota_ok"] = True
# sshd missing -> explicit error + refund + container removed
def fake2(args, input=None, timeout=0):
    calls.append(args)
    if args[1] == "exec" and "test -x /usr/sbin/sshd" in args[-1]: return 1, ""
    return 0, ""
w2 = worker.Worker(cfg, db=core.connect(cfg["db_path"]), run=fake2, sleep=lambda x: None)
bal = db.execute("SELECT bolts FROM users").fetchone()[0]; calls.clear()
r3 = c.post("/api/servers", json=dict(plan="nano", node=1, image="ubuntu-22", name="d3"), base_url=B).get_json(); w2.process_jobs()
row = db.execute("SELECT * FROM servers WHERE id=?", (r3["id"],)).fetchone()
ck("sshd failure surfaces the real reason, refunds, cleans up", row["deleted"] == 1 and "sshd" in row["note"] and db.execute("SELECT bolts FROM users").fetchone()[0] == bal and any(x[:3] == ["docker", "rm", "-f"] for x in calls), dict(row))
# status / disk / miners / actions / delete
db.execute("UPDATE servers SET container='axp-1-aaaaaa' WHERE id=?", (sid,)); db.execute("UPDATE servers SET container='axp-1-bbbbbb',status='running' WHERE id=?", (r2["id"],))
calls.clear(); w.sync_status()
ck("status synced from docker ps", db.execute("SELECT status FROM servers WHERE id=?", (r2["id"],)).fetchone()[0] == "stopped" and db.execute("SELECT status FROM servers WHERE id=?", (sid,)).fetchone()[0] == "running")
w.sync_disk()
g = lambda i: c.get("/api/servers/%d" % i, base_url=B).get_json()["server"]["disk_info"]
d1 = g(sid); ck("disk: own container only (5GB of plan disk), host never shown", d1["used_bytes"] == 5 * GB and d1["total_gb"] == nano["disk"] and d1["free_bytes"] == (nano["disk"] - 5) * GB, d1)
ck("single batched inspect, no df", sum(1 for x in calls if x[1] == "inspect") == 1 and not any("df" in x for x in calls))
w.scan_miners()
ck("miner detected via docker top -> suspended", db.execute("SELECT suspended,suspend_reason FROM servers WHERE id=?", (sid,)).fetchone()[1] == "mining: xmrig")
calls.clear(); w.process_jobs()
for act, verb in (("stop", "stop"), ("start", "start"), ("restart", "restart")):
    ops.queue(db, sid, act); calls.clear(); w.process_jobs(); ck("action " + act, any(x[1] == verb and x[-1] == "axp-1-aaaaaa" for x in calls), calls)
ops.queue(db, sid, "delete"); calls.clear(); w.process_jobs()
ck("delete removes container", any(x[:4] == ["docker", "rm", "-f", "-v"] for x in calls) and db.execute("SELECT deleted FROM servers WHERE id=?", (sid,)).fetchone()[0] == 1)
# LXD mode untouched
cfg2 = dict(cfg, backend="lxd"); w3 = worker.Worker(cfg2, db=core.connect(cfg["db_path"]), run=lambda *a, **k: (0, ""), sleep=lambda x: None); ck("lxd mode default path", not w3.docker_mode)
# remote node refused on docker
db.execute("UPDATE nodes SET lxc_remote='r1' WHERE id=1")
try: w.cname(dict(node_id=1, container="axp-1-aaaaaa")); ck("remote refused", False)
except worker.JobError: ck("remote refused on docker", True)
print("docker tests: %d passed, %d failed" % (P, F)); sys.exit(1 if F else 0)
