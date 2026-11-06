import os, re, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core, app as appmod, ops
P = F = 0
def ck(n, c, x=""):
    global P, F
    if c: P += 1
    else: F += 1; print("FAIL", n, x)
d = tempfile.mkdtemp(); cfg = core.load_config(os.path.join(d, "n.json")); cfg.update(db_path=os.path.join(d, "p.db"), base_url="http://localhost", backend="docker")
a = appmod.create_app(cfg); db = a.db; t = int(time.time()); B = "http://localhost"
for n, e, role, b in (("ann", "a@x.com", "user", 5000), ("sup", "s@x.com", "support", 0), ("own", "o@x.com", "owner", 0), ("zed", "z@x.com", "user", 5000)):
    db.execute("INSERT INTO users(username,email,pw_hash,bolts,role,created_at) VALUES(?,?,?,?,?,?)", (n, e, core.hash_password("password123"), b, role, t))
class CC:
    def __init__(s, email=None):
        s.c = a.test_client(); s.csrf = ""
        if email: s.login(email)
    def login(s, email):
        s.c.post("/api/auth/login", json=dict(email=email, password="password123"), base_url=B, headers={"Origin": B}); s.tok()
    def tok(s):
        m = re.search(r'name="csrf" content="([^"]+)"', s.c.get("/dashboard", base_url=B).get_data(as_text=True)); s.csrf = m.group(1) if m else ""
    def get(s, u): return s.c.get(u, base_url=B).get_json()
    def post(s, u, j=None): return s.c.post(u, json=j or {}, base_url=B, headers={"X-CSRF-Token": s.csrf, "Origin": B}).get_json()
ann, sup, own, zed = CC("a@x.com"), CC("s@x.com"), CC("o@x.com"), CC("z@x.com")
bal = lambda n: db.execute("SELECT bolts FROM users WHERE username=?", (n,)).fetchone()[0]
# ---- tickets ----
r = ann.post("/api/tickets", dict(subject="Cannot SSH", body="Connection refused on port 20001")); ck("ticket created", r["ok"], r); tid = r["id"]
ck("validation", not ann.post("/api/tickets", dict(subject="x", body="hi"))["ok"])
ck("support alerted", db.execute("SELECT COUNT(*) FROM notify WHERE to_log=1 AND kind='ticket'").fetchone()[0] == 1)
ck("owner sees own list", len(ann.get("/api/tickets")["tickets"]) == 1)
ck("other user cannot read", not zed.get("/api/tickets/%d" % tid)["ok"]); ck("other user cannot reply", not zed.post("/api/tickets/%d/reply" % tid, dict(body="hack"))["ok"])
ck("user cannot use admin api", not ann.get("/api/admin/tickets")["ok"])
ck("staff lists open tickets", len(sup.get("/api/admin/tickets")["tickets"]) == 1)
ck("staff replies", sup.post("/api/admin/tickets/%d/reply" % tid, dict(body="Checking now"))["ok"])
th = ann.get("/api/tickets/%d" % tid); ck("thread has both messages, staff flagged", len(th["messages"]) == 2 and th["messages"][1]["staff"] and th["ticket"]["status"] == "answered", th)
ck("user DM queued for staff reply", db.execute("SELECT COUNT(*) FROM notify WHERE user_id=1 AND title LIKE 'Reply to ticket%'").fetchone()[0] == 1)
ck("user replies -> reopens", ann.post("/api/tickets/%d/reply" % tid, dict(body="Still failing"))["ok"] and ann.get("/api/tickets/%d" % tid)["ticket"]["status"] == "open")
for i in range(2): ann.post("/api/tickets", dict(subject="Another %d" % i, body="some details here"))
ck("max 3 open tickets", not ann.post("/api/tickets", dict(subject="Fourth one", body="some details here"))["ok"])
ck("user closes", ann.post("/api/tickets/%d/close" % tid)["ok"]); ck("closed: no reply", not ann.post("/api/tickets/%d/reply" % tid, dict(body="x y z"))["ok"])
ck("support page renders", ann.c.get("/support", base_url=B).status_code == 200)
# ---- referral ----
info = ann.get("/api/referral"); ck("referral link", info["ok"] and "/r/" in info["link"], info); code = info["link"].rsplit("/", 1)[1]
ck("code stable", ann.get("/api/referral")["link"].endswith(code))
c2 = a.test_client(); r = c2.get("/r/" + code, base_url=B); ck("landing sets cookie + redirects to signup", r.status_code == 302 and "axref" in r.headers.get("Set-Cookie", "") and "register" in r.headers["Location"])
b0 = bal("ann"); r = c2.post("/api/auth/register", json=dict(email="new@x.com", username="newbie", password="password123"), base_url=B, headers={"Origin": B}).get_json(); ck("register ok", r["ok"], r)
nb = db.execute("SELECT * FROM users WHERE username='newbie'").fetchone(); sb = core.geti(core.get_settings(db), "signup_bonus")
ck("friend: referred_by set + welcome bonus", nb["referred_by"] == 1 and nb["bolts"] == sb + core.geti(core.get_settings(db), "referral_new"), dict(nb))
ck("referrer not paid yet", bal("ann") == b0)
c3 = a.test_client(); c3.get("/r/" + "deadbeef", base_url=B); c3.post("/api/auth/register", json=dict(email="x2@x.com", username="nocode", password="password123"), base_url=B, headers={"Origin": B}); ck("unknown code ignored", db.execute("SELECT referred_by FROM users WHERE username='nocode'").fetchone()[0] is None)
c4 = a.test_client(); c4.get("/r/" + code, base_url=B)
nbcli = CC(); nbcli.c = c2; nbcli.tok(); db.execute("UPDATE users SET bolts=5000 WHERE id=?", (nb["id"],))
node = db.execute("SELECT id FROM nodes LIMIT 1").fetchone()[0]; img = db.execute("SELECT key FROM images LIMIT 1").fetchone()[0]
r = nbcli.post("/api/servers", dict(plan="nano", node=node, image=img, name="friend-vps")); ck("friend deploys", r["ok"], r)
ck("referrer rewarded after friend's first server", bal("ann") == b0 + core.geti(core.get_settings(db), "referral_reward"), bal("ann"))
nbcli.post("/api/servers", dict(plan="nano", node=node, image=img, name="friend-2")); ck("reward only once", bal("ann") == b0 + core.geti(core.get_settings(db), "referral_reward"))
st = ann.get("/api/referral"); ck("stats", st["joined"] == 1 and st["paid"] == 1 and st["earned"] == core.geti(core.get_settings(db), "referral_reward"), st)
ck("self referral blocked", ops.referral_apply(db, 1, code) is False)
# ---- sale ----
cat = ann.get("/api/catalog"); p0 = next(p for p in cat["plans"] if p["key"] == "nano"); ck("no sale by default", p0["price"] == p0["bolts"] and not p0["sale_pct"])
ck("admin (support) cannot start sale", not sup.post("/api/admin/sale", dict(pct=20, hours=2))["ok"])
ck("bad pct refused", not own.post("/api/admin/sale", dict(pct=95, hours=2))["ok"])
r = own.post("/api/admin/sale", dict(pct=25, hours=2, plan="all")); ck("sale started", r["ok"] and r["plans"] >= 1, r)
p1 = next(p for p in ann.get("/api/catalog")["plans"] if p["key"] == "nano"); ck("catalog shows sale price", p1["sale_pct"] == 25 and p1["price"] == max(1, round(p1["bolts"] * .75)), p1)
b1 = bal("zed"); r = zed.post("/api/servers", dict(plan="nano", node=node, image=img, name="sale-vps")); ck("sale price charged", r["ok"] and b1 - bal("zed") == p1["price"], (b1, bal("zed")))
ck("server keeps regular price for renewals", db.execute("SELECT price FROM servers WHERE id=?", (r["id"],)).fetchone()[0] == p1["bolts"])
db.execute("UPDATE plans SET sale_until=? WHERE key='nano'", (t - 5,)); p2 = next(p for p in ann.get("/api/catalog")["plans"] if p["key"] == "nano"); ck("expired sale ends", not p2["sale_pct"] and p2["price"] == p2["bolts"])
own.post("/api/admin/sale", dict(pct=0, hours=0)); ck("pct 0 clears", db.execute("SELECT MAX(sale_pct) FROM plans").fetchone()[0] == 0)
# ---- auto placement ----
db.execute("INSERT INTO nodes(name,location,flag,host,max_servers,enabled) VALUES('B','x','IN','1.2.3.4',50,1)"); db.execute("UPDATE nodes SET health='online'")
nodes = ann.get("/api/catalog")["nodes"]; ck("auto option first", nodes[0]["id"] == "auto", nodes[:1])
n1 = db.execute("SELECT id FROM nodes ORDER BY id").fetchall(); db.execute("UPDATE nodes SET health='offline' WHERE id=?", (n1[0][0],))
ck("pick_node skips offline", ops.pick_node(db) == n1[1][0])
db.execute("UPDATE nodes SET health='online'"); db.execute("UPDATE nodes SET max_servers=1 WHERE id=?", (n1[0][0],))
ck("pick_node skips full", ops.pick_node(db) == n1[1][0], ops.pick_node(db))
r = ann.post("/api/servers", dict(plan="nano", node="auto", image=img, name="auto-vps")); ck("deploy with auto node", r["ok"] and db.execute("SELECT node_id FROM servers WHERE id=?", (r["id"],)).fetchone()[0] == n1[1][0], r)
db.execute("UPDATE nodes SET health='offline'"); ck("auto with all offline refused", not ann.post("/api/servers", dict(plan="nano", node="auto", image=img, name="auto-2"))["ok"])
db.execute("UPDATE nodes SET health='online'")
# ---- status page ----
now_ = core.now(); db.execute("INSERT INTO status_hist(node_id,t,up) VALUES(?,?,1)", (n1[0][0], now_ // 60 * 60)); db.execute("INSERT INTO status_hist(node_id,t,up) VALUES(?,?,0)", (n1[0][0], now_ // 60 * 60 - 60))
s = a.test_client().get("/api/status", base_url=B).get_json(); ck("status public (no login)", s["ok"] and len(s["nodes"]) == 2, s)
ck("status hides hosts/ips", "1.2.3.4" not in str(s) and "host" not in s["nodes"][0])
nd = s["nodes"][0]; ck("uptime math + 48 bars", nd["day"] == 50.0 and len(nd["bars"]) == 48, nd["day"])
ck("status page renders anonymously", a.test_client().get("/status", base_url=B).status_code == 200)
print("features tests: %d passed, %d failed" % (P, F)); sys.exit(1 if F else 0)
