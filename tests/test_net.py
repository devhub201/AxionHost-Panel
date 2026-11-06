import json, os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core, app as appmod, worker
P = F = 0
def ck(n, c, x=""):
    global P, F
    if c: P += 1
    else: F += 1; print("FAIL", n, x)
def mk(**extra):
    d = tempfile.mkdtemp(); cfg = core.load_config(os.path.join(d, "n.json")); cfg.update(db_path=os.path.join(d, "p.db"), base_url="http://localhost", **extra)
    return appmod.create_app(cfg), cfg
# listen validation
for v, good in (("0.0.0.0", 1), ("[::]", 1), ("[2001:db8::1]", 1), ("10.0.0.5", 1), ("::", 0), ("0.0.0.0; rm -rf /", 0), ("", 0), ("localhost", 0)):
    ck("LISTEN_RE %r" % v, bool(core.LISTEN_RE.match(v)) == bool(good))
# old DB migration
import sqlite3
d = tempfile.mkdtemp(); p = os.path.join(d, "o.db"); c = sqlite3.connect(p)
c.executescript(core.SCHEMA.replace(", listen TEXT NOT NULL DEFAULT '0.0.0.0'", "").replace(", disk_used INTEGER, disk_checked INTEGER", "")); c.execute("INSERT INTO nodes(name) VALUES('old')"); c.commit(); c.close()
db = core.connect(p); core.init_db(db); ck("nodes.listen migrated, existing node = IPv4", db.execute("SELECT listen FROM nodes WHERE name='old'").fetchone()[0] == "0.0.0.0")
# worker proxy device uses the node's listen address
a, cfg = mk(); db = a.db; t = int(time.time())
db.execute("INSERT INTO users(username,email,bolts,created_at,role) VALUES('o','o@x.com',9999,?,'owner')", (t,))
db.execute("UPDATE nodes SET listen='[::]' WHERE id=1"); db.execute("INSERT INTO nodes(name,listen) VALUES('v4','0.0.0.0')")
def sv(i, node):
    db.execute("INSERT INTO servers(user_id,plan_key,plan_name,node_id,name,container,image_key,lxc_image,type,cpu,ram,disk,price,status,ssh_port,root_pw_enc,created_at,expires_at) VALUES(1,'nano','N',?,?,?,'ubuntu-22','ubuntu:22.04','container',1,1,10,1,'provisioning',?,?,?,?)", (node, "s%d" % i, "axp-1-00000%d" % i, 20000 + i, core.enc(cfg["secret_key"], "pw"), t, t + 99999))
    db.execute("INSERT INTO jobs(server_id,action,created,updated) VALUES(?,'deploy',?,?)", (i, t, t))
sv(1, 1); sv(2, 2); calls = []
w = worker.Worker(cfg, db=core.connect(cfg["db_path"]), run=lambda a_, i=None, t_=0: (calls.append(a_) or (0, "")), sleep=lambda x: None); w.process_jobs()
adds = {c_[3]: c_ for c_ in calls if c_[1:4] == ["config", "device", "add"] or (len(c_) > 4 and c_[1:3] == ["config", "device"] and c_[3] == "add")}
listens = [x for c_ in calls for x in c_ if str(x).startswith("listen=")]
ck("IPv6 node -> listen=tcp:[::]:20001", "listen=tcp:[::]:20001" in listens, listens); ck("IPv4 node -> listen=tcp:0.0.0.0:20002", "listen=tcp:0.0.0.0:20002" in listens, listens)
# admin API validates listen
c = a.test_client(); c.post("/api/auth/login", json=dict(email="o@x.com", password="x"), base_url="http://localhost")
db.execute("UPDATE users SET pw_hash=? WHERE id=1", (core.hash_password("password123"),)); a.limiter.h.clear()
r = c.post("/api/auth/login", json=dict(email="o@x.com", password="password123"), base_url="http://localhost"); ck("login", r.status_code == 200)
import re; csrf = re.search(r'name="csrf" content="([^"]+)"', c.get("/dashboard", base_url="http://localhost").get_data(as_text=True)).group(1)
post = lambda u, d: c.post(u, json=d, headers={"X-CSRF-Token": csrf, "Origin": "http://localhost"}, base_url="http://localhost")
ck("bad listen rejected", post("/api/admin/nodes", dict(name="x", listen="0.0.0.0;id")).status_code == 400)
ck("ipv6 listen accepted", post("/api/admin/nodes", dict(name="x6", listen="[::]", host="2001:db8::5")).status_code == 200)
nd = c.get("/api/catalog", base_url="http://localhost").get_json()["nodes"]; ck("catalog lists nodes", len(nd) == 3)
# SSH string for IPv6 host
db.execute("UPDATE nodes SET host='2001:db8::5' WHERE id=1"); s = c.get("/api/servers/1", base_url="http://localhost").get_json()["server"]
ck("ssh cmd with ipv6 host", s["ssh"] == "ssh root@2001:db8::5 -p 20001", s["ssh"])
# client IP trust
def clientip(cfgx, hdr):
    a2, _ = mk(**cfgx)
    a2.db.execute("INSERT INTO users(username,email,pw_hash,created_at) VALUES('u','u@x.com',?,?)", (core.hash_password("password123"), t))
    c2 = a2.test_client(); c2.post("/api/auth/login", json=dict(email="u@x.com", password="password123"), headers=hdr, base_url="http://localhost", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    return a2.db.execute("SELECT ip FROM sessions").fetchone()[0]
ck("default ignores spoofed headers", clientip({}, {"X-Forwarded-For": "6.6.6.6", "CF-Connecting-IP": "6.6.6.6"}) == "127.0.0.1")
ck("cloudflare mode uses CF-Connecting-IP (ipv6 ok)", clientip({"trusted_proxy": "cloudflare"}, {"CF-Connecting-IP": "2001:db8::77", "X-Forwarded-For": "6.6.6.6"}) == "2001:db8::77")
ck("proxy mode uses LAST xff entry (spoof-proof)", clientip({"trusted_proxy": "proxy"}, {"X-Forwarded-For": "6.6.6.6, 203.0.113.9"}) == "203.0.113.9")
print("PASS", P, "FAIL", F); sys.exit(1 if F else 0)
