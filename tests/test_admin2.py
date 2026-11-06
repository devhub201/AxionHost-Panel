import os, re, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core, app as appmod
P = F = 0
def ck(n, c, x=""):
    global P, F
    if c: P += 1
    else: F += 1; print("FAIL", n, x)
d = tempfile.mkdtemp(); cfg = core.load_config(os.path.join(d, "n.json")); cfg.update(db_path=os.path.join(d, "p.db"), base_url="http://localhost", backend="docker")
a = appmod.create_app(cfg); db = a.db; t = int(time.time()); B = "http://localhost"
for n, e, role, bolts, ban in (("own", "o@x.com", "owner", 10, 0), ("adm", "a@x.com", "admin", 10, 0), ("u1", "u1@x.com", "user", 5, 0), ("u2", "u2@x.com", "user", 0, 1)):
    db.execute("INSERT INTO users(username,email,pw_hash,bolts,role,banned,created_at) VALUES(?,?,?,?,?,?,?)", (n, e, core.hash_password("password123"), bolts, role, ban, t))
class CC:
    def __init__(s, email):
        s.c = a.test_client(); s.c.post("/api/auth/login", json=dict(email=email, password="password123"), base_url=B, headers={"Origin": B})
        m = re.search(r'name="csrf" content="([^"]+)"', s.c.get("/dashboard", base_url=B).get_data(as_text=True)); s.csrf = m.group(1) if m else ""
    def get(s, u): return s.c.get(u, base_url=B).get_json()
    def post(s, u, j=None): return s.c.post(u, json=j or {}, base_url=B, headers={"X-CSRF-Token": s.csrf, "Origin": B}).get_json()
own, adm, u1 = CC("o@x.com"), CC("a@x.com"), CC("u1@x.com")
bal = lambda n: db.execute("SELECT bolts FROM users WHERE username=?", (n,)).fetchone()[0]
ck("admin cannot giveall", not adm.post("/api/admin/giveall", dict(amount=5))["ok"])
ck("user cannot giveall", not u1.post("/api/admin/giveall", dict(amount=5))["ok"])
for bad in (0, -5, 2000000, "x", None): ck("bad amount %r" % (bad,), not own.post("/api/admin/giveall", dict(amount=bad))["ok"])
r = own.post("/api/admin/giveall", dict(amount=50, reason="Diwali")); ck("giveall ok, banned skipped", r["ok"] and r["users"] == 3, r)
ck("balances", bal("own") == 60 and bal("u1") == 55 and bal("u2") == 0, (bal("own"), bal("u1"), bal("u2")))
ck("ledger row", db.execute("SELECT COUNT(*) FROM ledger WHERE reason LIKE 'Gift: Diwali%'").fetchone()[0] == 3)
ck("audited", db.execute("SELECT COUNT(*) FROM audit WHERE action='users.giveall'").fetchone()[0] == 1)
st = adm.get("/api/admin/stats"); ck("stats has nodes", st["ok"] and isinstance(st["nodes"], list) and st["nodes"] and "health" in st["nodes"][0], st)
# admin console
a.term_argv = lambda r: ["sh", "-c", "echo hello-from-vps; sleep 5"]
db.execute("INSERT INTO servers(user_id,name,container,plan_key,status,node_id,created_at,expires_at) VALUES(3,'v','axp-3-aaaaaa','p','running',1,?,?)", (t, t + 99999)) if False else None
cols = [r[1] for r in db.execute("PRAGMA table_info(servers)")]
vals = dict(user_id=3, name="v", container="axp-3-aaaaaa", status="running", node_id=1, deleted=0)
vals = {k: v for k, v in vals.items() if k in cols}
for c, v in (("plan_key", "x"), ("plan_name", "Plan"), ("image_key", "u"), ("lxc_image", "ubuntu:22.04"), ("type", "container"), ("price", 1), ("created_at", t), ("expires_at", t + 9999)):
    if c in cols: vals[c] = v
sid = db.execute("INSERT INTO servers(%s) VALUES(%s)" % (",".join(vals), ",".join("?" * len(vals))), list(vals.values())).lastrowid
ck("user cannot admin-term", not u1.post("/api/admin/servers/%d/term" % sid)["ok"])
r = adm.post("/api/admin/servers/%d/term" % sid, dict(cols=80, rows=24)); ck("admin opens console", r["ok"] and r["tid"], r)
time.sleep(0.6); o = adm.get("/api/term/%s/out?pos=0" % r["tid"]); import base64
ck("console output", o["ok"] and b"hello-from-vps" in base64.b64decode(o["d"]), o)
ck("other user cannot read it", not u1.get("/api/term/%s/out?pos=0" % r["tid"])["ok"])
ck("console audited", db.execute("SELECT COUNT(*) FROM audit WHERE action='server.admin_console'").fetchone()[0] == 1)
db.execute("UPDATE servers SET status='stopped' WHERE id=?", (sid,)); ck("stopped server refused", not adm.post("/api/admin/servers/%d/term" % sid)["ok"])
ck("missing server 404", not adm.post("/api/admin/servers/9999/term")["ok"])
adm.post("/api/term/%s/close" % r["tid"])
print("admin2 tests: %d passed, %d failed" % (P, F)); sys.exit(1 if F else 0)
