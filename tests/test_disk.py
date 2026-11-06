import json, os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core, app as appmod, worker
P = F = 0
def ck(n, c, x=""):
    global P, F
    if c: P += 1
    else: F += 1; print("FAIL", n, x)
d = tempfile.mkdtemp(); cfg = core.load_config(os.path.join(d, "n.json")); cfg.update(db_path=os.path.join(d, "p.db"), base_url="http://localhost")
a = appmod.create_app(cfg); db = a.db; GIB = 10 ** 9
# old-schema migration: drop the new columns' presence by creating a DB without them
old = os.path.join(d, "old.db"); import sqlite3
c = sqlite3.connect(old); c.executescript(core.SCHEMA.replace(", disk_used INTEGER, disk_checked INTEGER", "")); c.close()
odb = core.connect(old); core.init_db(odb); ck("migration adds columns", {"disk_used", "disk_checked"} <= {r[1] for r in odb.execute("PRAGMA table_info(servers)")})
t = int(time.time())
db.execute("INSERT INTO users(username,email,pw_hash,bolts,created_at) VALUES('u','u@x.com',?,0,?)", (core.hash_password("password123"), t))
def mk(i, plan_disk, status="running"):
    db.execute("INSERT INTO servers(user_id,plan_key,plan_name,node_id,name,container,image_key,lxc_image,type,cpu,ram,disk,price,status,ssh_port,root_pw_enc,created_at,expires_at) VALUES(1,'nano','Nano',1,?,?,'ubuntu-22','ubuntu:22.04','container',2,4,?,100,?,?,'',?,?)", ("s%d" % i, "axp-1-00000%d" % i, plan_disk, status, 20000 + i, t, t + 86400 * 20))
mk(1, 20); mk(2, 100); mk(3, 40); mk(4, 10); mk(5, 10, "stopped")
usage = {"axp-1-000001": 5 * GIB, "axp-1-000002": 37 * GIB, "axp-1-000003": None, "axp-1-000004": 12 * GIB, "axp-1-000005": 0}
calls = []
def fake(args, input=None, timeout=0):
    calls.append(args)
    if args[1] == "query":
        name = args[2].split("/")[3]; u = usage[name]
        if name == "axp-1-000003": return 1, "Error: not found"
        return 0, json.dumps({"status": "Running", "disk": {"root": {"usage": u, "total": 500 * GIB}}, "memory": {}})
    if args[1] == "list": return 0, "[]"
    return 0, ""
w = worker.Worker(cfg, db=core.connect(cfg["db_path"]), run=fake, sleep=lambda x: None)
c = a.test_client(); c.post("/api/auth/login", json=dict(email="u@x.com", password="password123"), base_url="http://localhost")
g = lambda sid: c.get("/api/servers/%d" % sid, base_url="http://localhost").get_json()["server"]["disk_info"]
d1 = g(1); ck("before first measurement: unavailable, not fake", d1["available"] is False and d1["used_bytes"] is None and d1["free_bytes"] is None and d1["pct"] is None and d1["total_gb"] == 20, d1)
w.sync_disk()
d1, d2, d3, d4, d5 = (g(i) for i in range(1, 6))
ck("vps1 20GB/5GB/15GB/25%", d1["total_gb"] == 20 and d1["used_bytes"] == 5 * GIB and d1["free_bytes"] == 15 * GIB and d1["pct"] == 25.0, d1)
ck("vps2 own numbers", d2["total_gb"] == 100 and d2["used_bytes"] == 37 * GIB and d2["free_bytes"] == 63 * GIB and d2["pct"] == 37.0, d2)
ck("vps3 missing -> safe unavailable", d3["available"] is False and d3["used_bytes"] is None and d3["total_gb"] == 40, d3)
ck("vps4 over limit clamps", d4["free_bytes"] == 0 and d4["pct"] == 100.0, d4)
ck("stopped vps measured too", d5["available"] and d5["used_bytes"] == 0 and d5["pct"] == 0.0, d5)
ck("no host disk 500GB leaks into any total", all(x["total_gb"] != 500 and x["total_bytes"] != 500 * GIB for x in (d1, d2, d3, d4, d5)))
ck("only instance-scoped lxc query used, no df / host reads", all(c_[1] in ("query", "list") for c_ in calls) and all("df" not in c_ for c_ in calls) and all(c_[2].startswith("/1.0/instances/axp-1-") for c_ in calls if c_[1] == "query"))
body = json.dumps(c.get("/api/servers", base_url="http://localhost").get_json()); ck("list API carries disk_info per server", body.count("disk_info") == 5)
# update dynamically
usage["axp-1-000001"] = 10 * GIB; w.sync_disk(); ck("updates dynamically", g(1)["used_bytes"] == 10 * GIB and g(1)["pct"] == 50.0)
# measurement failure later -> back to unavailable (no stale fake)
usage["axp-1-000002"] = None; db2 = db
w2 = worker.Worker(cfg, db=core.connect(cfg["db_path"]), run=lambda a, i=None, t=0: (1, "err") if a[1] == "query" else (0, "[]"), sleep=lambda x: None); w2.sync_disk()
ck("failed query clears stale value", g(2)["available"] is False)
# remote node targeting
db.execute("INSERT INTO nodes(name,lxc_remote) VALUES('N2','far')"); db.execute("UPDATE servers SET node_id=2 WHERE id=4"); calls.clear(); w.sync_disk()
ck("remote node query scoped", any(c_[2] == "far:/1.0/instances/axp-1-000004/state" for c_ in calls if c_[1] == "query"), [c_ for c_ in calls if c_[1] == "query"])
# other fields untouched
s = c.get("/api/servers/1", base_url="http://localhost").get_json()["server"]
ck("existing fields intact", s["disk"] == 20 and s["cpu"] == 2 and s["ram"] == 4 and s["ssh_port"] == 20001 and "ssh" in s and s["node"] is not None)
# page serves
ck("server page renders", c.get("/servers/1", base_url="http://localhost").status_code == 200)
# deploy/reinstall reset stale usage
db.execute("UPDATE servers SET disk_used=999 WHERE id=1")
print("PASS", P, "FAIL", F); sys.exit(1 if F else 0)
