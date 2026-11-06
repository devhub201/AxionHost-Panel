import os, re, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core, app as appmod, worker, ops
P = F = 0
def ck(n, c, x=""):
    global P, F
    if c: P += 1
    else: F += 1; print("FAIL", n, x)
os.cpu_count = lambda: 64
d = tempfile.mkdtemp(); cfg = core.load_config(os.path.join(d, "n.json")); cfg.update(db_path=os.path.join(d, "p.db"), base_url="http://localhost", backend="docker")
a = appmod.create_app(cfg); db = a.db; t = int(time.time()); B = "http://localhost"; GB = 10 ** 9
db.execute("INSERT INTO users(username,email,pw_hash,bolts,created_at) VALUES('u','u@x.com',?,100000,?)", (core.hash_password("password123"), t))
log_ = []
state = dict(cpu="99.00%", health="NPROC 4\nMEM 8000000000\nDISK 100000000000 50000000000\nLOAD 0.5\nDOCKER ok\nLXD missing\n", fail=False)
def fake(args, input=None, timeout=0):
    if args[:2] == ["sh", "-s"]: return (255, "ssh: connect timed out") if state["fail"] else (0, state["health"])
    v = args[1] if args[0] == "docker" else ""
    if v == "stats": return 0, "axp-1-000001|%s|512MiB / 4GiB" % state["cpu"]
    if args[0] == "docker": log_.append(args[1:])
    if v == "inspect": return 0, "172.17.0.5"
    if v == "image" and args[2] == "inspect": return 0, "123456789"
    if v == "run" and state.get("fail_run"): return 125, "boom"
    return 0, ""
w = worker.Worker(cfg, db=core.connect(cfg["db_path"]), run=fake, sleep=lambda x: None)
cols = [r[1] for r in db.execute("PRAGMA table_info(servers)")]
vals = dict(user_id=1, plan_key="nano", plan_name="Nano", node_id=1, name="s", container="axp-1-000001", image_key="u", lxc_image="ubuntu:22.04", type="container", cpu=1, ram=4, disk=10, price=100, status="running", created_at=t, expires_at=t + 99999)
sid = db.execute("INSERT INTO servers(%s) VALUES(%s)" % (",".join(vals), ",".join("?" * len(vals))), list(vals.values())).lastrowid
alerts = lambda: [r["title"] for r in db.execute("SELECT title FROM notify WHERE to_log=1 ORDER BY id")]
# ---- stat history ----
w.sync_stats(); h = db.execute("SELECT * FROM stat_hist WHERE server_id=?", (sid,)).fetchall()
ck("history row written", len(h) == 1 and h[0]["cpu"] == 99.0 and h[0]["mem"] == 512 * 1024 * 1024, [dict(x) for x in h])
for i in range(1, 6): db.execute("INSERT INTO stat_hist(server_id,t,cpu,mem) VALUES(?,?,?,?)", (sid, t - i * 60, 10 + i, 1000 * i))
db.execute("INSERT INTO stat_hist(server_id,t,cpu,mem) VALUES(?,?,?,?)", (sid, t - 90000, 1, 1)); w.sync_stats()
ck("old history pruned", db.execute("SELECT COUNT(*) FROM stat_hist WHERE t<?", (t - 86400,)).fetchone()[0] == 0)
B_ = "http://localhost"
class CC:
    def __init__(s, e):
        s.c = a.test_client(); s.c.post("/api/auth/login", json=dict(email=e, password="password123"), base_url=B, headers={"Origin": B})
    def get(s, u): return s.c.get(u, base_url=B).get_json()
c = CC("u@x.com"); r = c.get("/api/servers/%d/stats?m=60" % sid)
ck("stats api returns points + ram", r["ok"] and len(r["points"]) >= 5 and r["ram"] == 4 * 1073741824, r)
db.execute("INSERT INTO users(username,email,pw_hash,created_at) VALUES('o','o@x.com',?,?)", (core.hash_password("password123"), t))
ck("other user cannot read stats", not CC("o@x.com").get("/api/servers/%d/stats" % sid)["ok"])
# ---- abuse ----
db.execute("UPDATE settings SET v='3' WHERE k='abuse_minutes'")
db.execute("UPDATE servers SET abuse_streak=0,abuse_flag=0 WHERE id=?", (sid,))
w.last = w.last if hasattr(w, "last") else {}
for i in range(2): w.abuse_check(sid, 99.0)
ck("no alert before threshold", "High CPU abuse" not in alerts())
w.abuse_check(sid, 99.0); ck("alert at threshold", alerts().count("High CPU abuse") == 1, alerts())
w.abuse_check(sid, 99.0); w.abuse_check(sid, 99.0); ck("alert only once", alerts().count("High CPU abuse") == 1)
ck("alert-only mode does not suspend", db.execute("SELECT suspended FROM servers WHERE id=?", (sid,)).fetchone()[0] == 0)
w.abuse_check(sid, 10.0); ck("streak resets when load drops", db.execute("SELECT abuse_streak,abuse_flag FROM servers WHERE id=?", (sid,)).fetchone()[:] == (0, 0))
db.execute("UPDATE settings SET v='suspend' WHERE k='abuse_action'")
for i in range(3): w.abuse_check(sid, 100.0)
r = db.execute("SELECT suspended,suspend_reason FROM servers WHERE id=?", (sid,)).fetchone()
ck("suspend mode suspends + queues stop", r[0] == 1 and r[1].startswith("abuse") and db.execute("SELECT COUNT(*) FROM jobs WHERE server_id=? AND action='stop'", (sid,)).fetchone()[0] == 1, tuple(r))
ck("user notified of suspension", db.execute("SELECT COUNT(*) FROM notify WHERE user_id=1 AND kind='suspended'").fetchone()[0] == 1)
# ---- node alerts ----
db.execute("UPDATE notify SET sent=1"); w.sync_nodes(); ck("healthy node: no alert", not [x for x in alerts() if x.startswith("Node")], alerts())
state["fail"] = True; w.sync_nodes(); ck("offline alert", any("offline" in x for x in alerts()), alerts())
n_ = len([x for x in alerts() if x.startswith("Node")]); w.sync_nodes(); ck("no repeat while still offline", len([x for x in alerts() if x.startswith("Node")]) == n_)
state["fail"] = False; state["health"] = state["health"].replace("50000000000", "4000000000"); w.sync_nodes()
ck("recover + disk-low alerts fire", any("disk" in x for x in alerts()), alerts())
state["health"] = "NPROC 4\nMEM 8000000000\nDISK 100000000000 50000000000\nLOAD 9.0\nDOCKER ok\nLXD missing\n"; w.sync_nodes(); ck("load alert", any(x.endswith("load") for x in alerts()), alerts())
state["health"] = "NPROC 4\nMEM 8000000000\nDISK 100000000000 50000000000\nLOAD 0.5\nDOCKER ok\nLXD missing\n"; w.sync_nodes(); ck("recovered message", any("recovered" in x for x in alerts()), alerts())
# ---- snapshots ----
db.execute("UPDATE servers SET ssh_port=20001,suspended=0,suspend_reason='' WHERE id=?", (sid,)); db.execute("UPDATE jobs SET status='done'")
u = CC("u@x.com")
def post(url, j=None): return u.c.post(url, json=j or {}, base_url=B, headers={"X-CSRF-Token": csrf, "Origin": B}).get_json()
csrf = re.search(r'name="csrf" content="([^"]+)"', u.c.get("/dashboard", base_url=B).get_data(as_text=True)).group(1)
r = post("/api/servers/%d/snapshots" % sid, dict(name="before update")); ck("snapshot queued", r["ok"], r)
ck("second snapshot blocked while job pending", not post("/api/servers/%d/snapshots" % sid, dict(name="x"))["ok"])
w.process_jobs(); sn = db.execute("SELECT * FROM snapshots").fetchone()
ck("snapshot ready with ref + size", sn["status"] == "ready" and sn["ref"] == "axs-%d:%d" % (sid, sn["id"]) and sn["size"] == 123456789, dict(sn))
ck("docker commit called", any(c[0] == "commit" and c[-1] == sn["ref"] for c in log_), log_[-5:])
ex = u.get("/api/servers/%d/extras" % sid); ck("extras api lists snapshot + limits", ex["ok"] and ex["snapshots"][0]["status"] == "ready" and ex["limits"]["snapshots"] == 2, ex)
ck("bad name refused", not post("/api/servers/%d/snapshots" % sid, dict(name="a/b;rm"))["ok"])
post("/api/servers/%d/snapshots" % sid, dict(name="two")); w.process_jobs(); r = post("/api/servers/%d/snapshots" % sid, dict(name="three")); ck("snapshot limit enforced", not r["ok"] and "limit" in r["error"], r)
log_.clear(); r = post("/api/servers/%d/snapshots/%d/restore" % (sid, sn["id"])); ck("restore queued + server busy", r["ok"] and db.execute("SELECT status FROM servers WHERE id=?", (sid,)).fetchone()[0] == "provisioning", r)
w.process_jobs(); cmds = [c[0] for c in log_]
ck("restore: old renamed, new run from snapshot image, old removed", "rename" in cmds and "run" in cmds and any(c[0] == "run" and sn["ref"] in c for c in log_) and log_[-1][0] in ("rm", "update", "exec", "start") , cmds)
ck("restore ends running", db.execute("SELECT status FROM servers WHERE id=?", (sid,)).fetchone()[0] == "running")
state["fail_run"] = True; log_.clear(); post("/api/servers/%d/snapshots/%d/restore" % (sid, sn["id"])); w.process_jobs()
ck("failed restore rolls back (old container renamed back, server usable)", any(c[0] == "rename" and c[-1] == "axp-1-000001" for c in log_) and db.execute("SELECT status FROM servers WHERE id=?", (sid,)).fetchone()[0] == "running", [c[:3] for c in log_][-6:])
state["fail_run"] = False
other = CC("o@x.com"); ck("other user cannot touch snapshots", not other.c.post("/api/servers/%d/snapshots/%d/delete" % (sid, sn["id"]), json={}, base_url=B, headers={"X-CSRF-Token": csrf, "Origin": B}).get_json()["ok"])
post("/api/servers/%d/snapshots/%d/delete" % (sid, sn["id"])); w.process_jobs(); ck("snapshot deleted (+ docker rmi)", not db.execute("SELECT 1 FROM snapshots WHERE id=?", (sn["id"],)).fetchone() and any(c[0] == "rmi" for c in log_))
# ---- ports ----
r = post("/api/servers/%d/ports" % sid, dict(port=25565, proto="tcp")); ck("port added in range", r["ok"] and 30000 <= r["public_port"] <= 39999, r)
pub = r["public_port"]; scripts = []; real = w._exec
w._exec = lambda args, input=None, timeout=0: (scripts.append(input) or (0, "")) if args[:2] == ["sh", "-s"] and input and "axp-pf" in input else real(args, input, timeout)
w.process_jobs(); sc = "\n".join(x for x in scripts if x)
ck("iptables DNAT rule with container ip", "--dport %d" % pub in sc and "--to-destination 172.17.0.5:25565" in sc and "DOCKER-USER" in sc, sc[-300:])
ck("rules tagged for cleanup", "grep axp-pf-" in sc and "-j DNAT" in sc)
ck("duplicate port refused", not post("/api/servers/%d/ports" % sid, dict(port=25565, proto="tcp"))["ok"])
for bad in (0, 70000, "x"): ck("bad port %r" % bad, not post("/api/servers/%d/ports" % sid, dict(port=bad))["ok"])
ck("bad proto", not post("/api/servers/%d/ports" % sid, dict(port=80, proto="icmp"))["ok"])
post("/api/servers/%d/ports" % sid, dict(port=80, proto="udp")); post("/api/servers/%d/ports" % sid, dict(port=8080)); r = post("/api/servers/%d/ports" % sid, dict(port=9000)); ck("port limit enforced", not r["ok"], r)
fid = db.execute("SELECT id FROM port_forwards WHERE target_port=25565").fetchone()[0]; scripts.clear(); post("/api/servers/%d/ports/%d/delete" % (sid, fid)); w.process_jobs()
ck("port removed from rules + table", not db.execute("SELECT 1 FROM port_forwards WHERE id=?", (fid,)).fetchone() and "--dport %d" % pub not in "\n".join(x for x in scripts if x), scripts[-1:] )
db.execute("UPDATE servers SET status='stopped' WHERE id=?", (sid,)); ck("ports need a running server", not post("/api/servers/%d/ports" % sid, dict(port=22))["ok"])
print("ops2 tests: %d passed, %d failed" % (P, F)); sys.exit(1 if F else 0)
