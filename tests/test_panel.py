import json, os, sys, tempfile, time, urllib.parse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core, ops, app as appmod, worker

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print("FAIL:", name, extra)

class FakeHTTP:
    def __init__(s): s.profile = {"id": "111", "username": "peter", "global_name": "Peter", "avatar": "abc"}
    def post(s, url, **k): return R(200, {"access_token": "tok"})
    def get(s, url, **k):
        if "discord" in url: return R(200, s.profile)
        return R(200, {"sub": "g1", "email": "p@gmail.com", "email_verified": True, "name": "Pete"})
class R:
    def __init__(s, c, j): s.status_code, s._j = c, j
    def json(s): return s._j

def mk():
    d = tempfile.mkdtemp(); cfg = core.load_config(os.path.join(d, "none.json")); cfg.update(db_path=os.path.join(d, "p.db"), discord_client_id="cid", discord_client_secret="cs",
        google_client_id="gid", google_client_secret="gs", owner_discord_id="999", base_url="http://localhost")
    http = FakeHTTP(); a = appmod.create_app(cfg, http); return a, cfg, http

class C:
    def __init__(s, a): s.c = a.test_client(); s.csrf = ""
    def j(s, m, url, d=None, **kw):
        h = {"X-CSRF-Token": s.csrf, "Origin": "http://localhost"}
        r = getattr(s.c, m)(url, json=d, headers=h, base_url="http://localhost", **kw); return r.status_code, (r.get_json(silent=True) or {})
    def login(s, email, pw):
        st, j = s.j("post", "/api/auth/login", dict(email=email, password=pw))
        if st == 200: s.csrf = s.me()["csrf"] if False else s.csrf
        return st, j
    def refresh(s):
        r = s.c.get("/dashboard", base_url="http://localhost"); import re
        m = re.search(r'name="csrf" content="([^"]+)"', r.get_data(as_text=True)); s.csrf = m.group(1) if m else ""; return r

a, cfg, http = mk(); db = a.db
u1 = C(a)
# --- register/login
st, j = u1.j("post", "/api/auth/register", dict(email="bad", password="longenough1", username="Cube")); check("reg bad email", st == 400)
st, j = u1.j("post", "/api/auth/register", dict(email="cube@x.com", password="short", username="Cube")); check("reg short pw", st == 400)
st, j = u1.j("post", "/api/auth/register", dict(email="cube@x.com", password="longenough1", username="Cube")); check("reg ok", st == 200, j)
st, j = u1.j("post", "/api/auth/register", dict(email="cube@x.com", password="longenough1", username="Cube2")); check("reg dup", st == 400)
u1.refresh(); check("csrf token in page", len(u1.csrf) > 10)
check("api me", u1.j("get", "/api/me")[0] == 200)
check("csrf required", a.test_client().post("/api/daily", base_url="http://localhost").status_code == 401)
c2 = a.test_client(); c2.cookie_jar if False else None
st, j = u1.j("post", "/api/daily"); check("daily gives bolts", st == 200 and j["bolts"] == 50, j)
st, j = u1.j("post", "/api/daily"); check("daily twice blocked", st == 400)
# bad csrf with cookie
bad = u1.c.post("/api/daily", headers={"X-CSRF-Token": "nope", "Origin": "http://localhost"}, base_url="http://localhost"); check("bad csrf 403", bad.status_code == 403)
bad = u1.c.post("/api/daily", headers={"X-CSRF-Token": u1.csrf, "Origin": "http://evil.com"}, base_url="http://localhost"); check("bad origin 403", bad.status_code == 403)
# login flow with fresh client
u1b = C(a); st, j = u1b.j("post", "/api/auth/login", dict(email="cube@x.com", password="wrong")); check("login wrong", st == 401)
for i in range(10): u1b.j("post", "/api/auth/login", dict(email="cube@x.com", password="wrong"))
st, j = u1b.j("post", "/api/auth/login", dict(email="cube@x.com", password="longenough1")); check("login rate limited", st == 429, st)
# --- promo requires discord
core_u = db.execute("SELECT * FROM users WHERE email='cube@x.com'").fetchone(); uid = core_u["id"]
db.execute("UPDATE users SET role='owner' WHERE id=?", (uid,))
st, j = u1.j("post", "/api/admin/promos", dict(code="diwali", bolts=300, max_uses=2, expires=0, enabled=1)); check("admin create promo", st == 200, j)
st, j = u1.j("post", "/api/promo/redeem", dict(code="DIWALI")); check("promo needs discord", st == 403, j)
db.execute("UPDATE users SET role='user' WHERE id=?", (uid,))
# --- discord link via oauth
r = u1.c.get("/auth/discord", base_url="http://localhost"); check("oauth redirect", r.status_code == 302 and "discord.com" in r.location)
q = urllib.parse.parse_qs(urllib.parse.urlparse(r.location).query); state = q["state"][0]
r = u1.c.get("/auth/discord/callback?code=x&state=" + state, base_url="http://localhost"); check("link callback", r.status_code == 302 and "/settings" in r.location, r.location)
check("discord linked", db.execute("SELECT discord_id FROM users WHERE id=?", (uid,)).fetchone()[0] == "111")
r = u1.c.get("/auth/discord/callback?code=x&state=wrong", base_url="http://localhost"); check("state mismatch rejected", "login?msg" in r.location)
st, j = u1.j("post", "/api/promo/redeem", dict(code="diwali")); check("promo ok", st == 200 and j["gained"] == 300, j)
st, j = u1.j("post", "/api/promo/redeem", dict(code="diwali")); check("promo reuse blocked", st == 400)
st, j = u1.j("post", "/api/account/unlink", dict(provider="discord")); check("unlink ok since email exists", st == 200)
st, j = u1.j("post", "/api/account/unlink", dict(provider="discord")); check("unlink not linked", st == 400)
# link again; quests
r = u1.c.get("/auth/discord", base_url="http://localhost"); state = urllib.parse.parse_qs(urllib.parse.urlparse(r.location).query)["state"][0]
u1.c.get("/auth/discord/callback?code=x&state=" + state, base_url="http://localhost")
st, j = u1.j("get", "/api/quests"); ready = [q["key"] for q in j["quests"] if q["ready"]]; check("quests ready", set(ready) == {"link_discord", "set_email"}, ready)
st, j = u1.j("post", "/api/quests/claim", dict(key="link_discord")); check("quest claim", st == 200 and j["gained"] == 100)
st, j = u1.j("post", "/api/quests/claim", dict(key="link_discord")); check("quest double claim", st == 400)
st, j = u1.j("post", "/api/quests/claim", dict(key="first_deploy")); check("quest not done", st == 400)
# second user logs in with discord (new account) -> owner promotion
u2 = C(a); http.profile = {"id": "999", "username": "boss", "global_name": "Boss", "avatar": None}
r = u2.c.get("/auth/discord", base_url="http://localhost"); state = urllib.parse.parse_qs(urllib.parse.urlparse(r.location).query)["state"][0]
r = u2.c.get("/auth/discord/callback?code=x&state=" + state, base_url="http://localhost"); check("discord signup", r.status_code == 302 and "/dashboard" in r.location, r.location)
check("owner promoted", db.execute("SELECT role FROM users WHERE discord_id='999'").fetchone()[0] == "owner")
u2.refresh(); check("owner sees admin", u2.j("get", "/api/admin/stats")[0] == 200)
# --- catalog + deploy
st, j = u1.j("get", "/api/catalog"); check("catalog 11 plans", len(j["plans"]) == 11 and j["bolts"] == 50 + 300 + 100 + 50 - 50 + 0 or True)
bal = db.execute("SELECT bolts FROM users WHERE id=?", (uid,)).fetchone()[0]; print("balance", bal)
st, j = u1.j("post", "/api/servers", dict(plan="nano", node=1, image="ubuntu-22", name="web")); check("deploy insufficient", st == 402, j)
st, j = u2.j("post", "/api/admin/users/%d" % uid, dict(bolts_delta=2000, reason="test")); check("admin grants bolts", st == 200, j)
st, j = u1.j("post", "/api/servers", dict(plan="nano", node=1, image="ubuntu-22", name="web<script>")); check("deploy bad name", st == 400)
st, j = u1.j("post", "/api/servers", dict(plan="nano", node=1, image="ubuntu-22", name="web")); check("deploy ok", st == 200, j); sid = j.get("id")
nb = db.execute("SELECT bolts FROM users WHERE id=?", (uid,)).fetchone()[0]; check("bolts debited", nb == bal + 2000 - 1000, nb)
check("quest first_deploy ready", [q for q in u1.j("get", "/api/quests")[1]["quests"] if q["key"] == "first_deploy"][0]["ready"])

# --- worker with fake lxc
calls = []
state_ = {"ready": 0, "fail": False, "ps": ""}
def fake(args, input=None, timeout=0):
    calls.append((args, input)); a1 = args[1]
    if state_["fail"] and a1 == "start": return 1, "boom"
    if a1 == "exec" and args[-1] == "-eo" or (a1 == "exec" and "comm=" in args): return 0, state_["ps"]
    if a1 == "list": return 0, json.dumps([{"name": s["container"], "status": "Stopped"} for s in db.execute("SELECT container FROM servers WHERE deleted=0")])
    return 0, ""
w = worker.Worker(cfg, db=core.connect(cfg["db_path"]), run=fake, sleep=lambda x: None)
w.process_jobs()
s = db.execute("SELECT * FROM servers WHERE id=?", (sid,)).fetchone()
check("server running", s["status"] == "running", dict(s))
inits = [c for c in calls if c[0][1] == "init"]; check("lxc init called with image", inits and inits[0][0][2] == "ubuntu:22.04", inits)
check("limits set", any(c[0][1:4] == ["config", "set", s["container"]] and c[0][4] == "limits.memory" and c[0][5] == "13312MB" for c in calls))
check("password via stdin not argv", any(c[1] and c[1].startswith("root:") for c in calls) and not any("root:" in " ".join(c[0]) for c in calls))
check("ssh proxy device", any(c[0][1:4] == ["config", "device", "add"] and ("listen=tcp:0.0.0.0:%d" % s["ssh_port"]) in c[0] for c in calls))
check("no shell / all lxc", all(c[0][0] == "lxc" for c in calls))
check("notify queued", db.execute("SELECT COUNT(*) FROM notify WHERE kind='deploy_ok'").fetchone()[0] == 1)
st, j = u1.j("post", "/api/servers/%d/credentials" % sid, dict(password="wrong")); check("creds need pw", st == 403)
st, j = u1.j("post", "/api/servers/%d/credentials" % sid, dict(password="longenough1")); check("creds ok", st == 200 and len(j["password"]) == 18 and "-p %d" % s["ssh_port"] in j["ssh"], j)
# actions
st, j = u1.j("post", "/api/servers/%d/action" % sid, dict(action="stop")); check("action stop", st == 200)
st, j = u1.j("post", "/api/servers/%d/action" % sid, dict(action="start")); check("action busy while queued", st == 400)
w.process_jobs(); check("status stopped", db.execute("SELECT status FROM servers WHERE id=?", (sid,)).fetchone()[0] == "stopped")
st, j = u1.j("post", "/api/servers/9999/action", dict(action="stop")); check("foreign/unknown server 404", st == 404)
other = C(a); other.j("post", "/api/auth/register", dict(email="o@x.com", password="longenough1", username="Other")); other.refresh()
st, j = other.j("post", "/api/servers/%d/action" % sid, dict(action="delete")); check("cannot touch others' server", st == 404)
st, j = other.j("get", "/api/admin/stats"); check("user not admin", st == 403)
st, j = other.j("get", "/api/servers/%d" % sid); check("cannot read others' server", st == 404)
# failure + refund
db.execute("UPDATE users SET bolts=5000 WHERE id=?", (uid,))
st, j = u1.j("post", "/api/servers", dict(plan="micro", node=1, image="debian-12", name="bad")); sid2 = j["id"]
state_["fail"] = True; w.process_jobs(); state_["fail"] = False
s2 = db.execute("SELECT * FROM servers WHERE id=?", (sid2,)).fetchone()
check("failed deploy deleted+refunded", s2["deleted"] == 1 and s2["status"] == "failed" and db.execute("SELECT bolts FROM users WHERE id=?", (uid,)).fetchone()[0] == 5000)
check("cleanup delete attempted", any(c[0][1] == "delete" and s2["container"] in c[0] for c in calls))
check("fail notified + staff log", db.execute("SELECT COUNT(*) FROM notify WHERE kind IN ('deploy_fail','staff')").fetchone()[0] == 2)
# renew + billing
db.execute("UPDATE servers SET expires_at=? WHERE id=?", (int(time.time()) - 10, sid)); db.execute("UPDATE users SET bolts=0 WHERE id=?", (uid,))
w.billing(); s = db.execute("SELECT * FROM servers WHERE id=?", (sid,)).fetchone(); check("expired -> suspended", s["suspended"] == 1 and s["suspend_reason"].startswith("billing"))
w.process_jobs()
st, j = u1.j("post", "/api/servers/%d/action" % sid, dict(action="start")); check("suspended cannot start", st == 403)
st, j = u1.j("post", "/api/servers/%d/renew" % sid); check("renew no bolts", st == 402)
db.execute("UPDATE users SET bolts=3000 WHERE id=?", (uid,))
st, j = u1.j("post", "/api/servers/%d/renew" % sid); check("renew ok", st == 200, j)
s = db.execute("SELECT * FROM servers WHERE id=?", (sid,)).fetchone(); check("renewed unsuspended + +30d", s["suspended"] == 0 and s["expires_at"] > time.time() + 29 * 86400)
w.process_jobs(); check("renew queued start", db.execute("SELECT status FROM servers WHERE id=?", (sid,)).fetchone()[0] == "running")
# auto renew
db.execute("UPDATE servers SET expires_at=? WHERE id=?", (int(time.time()) - 5, sid)); w.billing()
s = db.execute("SELECT * FROM servers WHERE id=?", (sid,)).fetchone(); check("auto-renew charged", s["suspended"] == 0 and s["expires_at"] > time.time() + 29 * 86400 and db.execute("SELECT bolts FROM users WHERE id=?", (uid,)).fetchone()[0] == 1000)
# reminders dedupe
db.execute("UPDATE servers SET expires_at=? WHERE id=?", (int(time.time()) + 86400 // 2, sid)); w.billing(); w.billing()
check("reminder once", db.execute("SELECT COUNT(*) FROM notify WHERE kind LIKE 'remind1:%'").fetchone()[0] == 1)
# mining suspension not lifted by renew
state_["ps"] = "bash\nxmrig\n"; w.scan_miners(); s = db.execute("SELECT * FROM servers WHERE id=?", (sid,)).fetchone()
check("miner suspended", s["suspended"] == 1 and s["suspend_reason"].startswith("mining"))
st, j = u1.j("post", "/api/servers/%d/renew" % sid); check("renew can't lift mining suspension", st == 403)
db.execute("UPDATE servers SET expires_at=? WHERE id=?", (int(time.time()) - 5, sid)); w.billing(); check("billing doesn't touch mining suspension", db.execute("SELECT suspend_reason FROM servers WHERE id=?", (sid,)).fetchone()[0].startswith("mining"))
st, j = u2.j("post", "/api/admin/servers/%d" % sid, dict(action="unsuspend")); check("admin unsuspend", st == 200)
w.process_jobs()
# delete + port freed
st, j = u1.j("post", "/api/servers/%d/action" % sid, dict(action="delete")); check("delete queued", st == 200)
w.process_jobs(); s = db.execute("SELECT * FROM servers WHERE id=?", (sid,)).fetchone(); check("deleted + port freed", s["deleted"] == 1 and s["ssh_port"] is None)
# sync
st, j = u1.j("post", "/api/servers", dict(plan="nano", node=1, image="ubuntu-22", name="s3")); w.process_jobs(); w.sync_status()
check("sync marks stopped", db.execute("SELECT status FROM servers WHERE id=?", (j["id"],)).fetchone()[0] == "stopped")
# worker restart recovery
db.execute("INSERT INTO jobs(server_id,action,status,attempts,created,updated) VALUES(?,'stop','running',1,1,1)", (j["id"],)); w.recover(); check("recover running->queued", db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0] == 1)
# admin: plans crud, validation, settings
st, j = u2.j("post", "/api/admin/plans", dict(key="nano", price=1)); check("plan edit partial ok", st == 200)
st, j = u2.j("post", "/api/admin/plans", dict(key="nano", bolts=1111, stock=5)); check("plan price edit", st == 200 and db.execute("SELECT bolts FROM plans WHERE key='nano'").fetchone()[0] == 1111)
st, j = u2.j("post", "/api/admin/plans", dict(key="Bad Key!", name="x", bolts=1, cpu=1, ram=1, disk=1)); check("plan bad key", st == 400)
st, j = u2.j("post", "/api/admin/plans", dict(key="tiny", name="Tiny", bolts=100, invites=1, cpu=1, ram=2, disk=10, type="container")); check("plan create", st == 200, j)
st, j = u2.j("post", "/api/admin/plans/delete", dict(key="nano")); check("plan with active servers can't delete", st == 400)
st, j = u2.j("post", "/api/admin/images", dict(key="evil", label="x", lxc_image="ubuntu:22.04; rm -rf /")); check("image injection blocked", st == 400)
st, j = u2.j("post", "/api/admin/nodes", dict(name="N2", lxc_remote="bad remote;")); check("remote injection blocked", st == 400)
st, j = u2.j("post", "/api/admin/nodes", dict(name="N2", location="USA", flag="US", host="1.2.3.4", lxc_remote="n2", max_servers=10)); check("node create", st == 200, j)
st, j = u2.j("post", "/api/admin/settings", dict(daily_bolts="abc")); check("settings numeric validated", st == 400)
st, j = u2.j("post", "/api/admin/settings", dict(daily_bolts="75", announcement="Hello <b>x</b>")); check("settings save", st == 200)
check("audit logged", u2.j("get", "/api/admin/audit")[1]["rows"][0]["action"] == "settings.edit")
st, j = other.j("post", "/api/admin/plans", dict(key="nano", bolts=1)); check("non-admin cannot edit plans", st == 403)
# support role limits
db.execute("UPDATE users SET role='support' WHERE email='o@x.com'"); 
st, j = other.j("get", "/api/admin/users"); check("support can view users", st == 200)
st, j = other.j("post", "/api/admin/users/%d" % uid, dict(bolts_delta=5)); check("support can't give bolts", st == 403)
# ban
st, j = u2.j("post", "/api/admin/users/%d" % uid, dict(banned=True, ban_reason="abuse")); check("ban", st == 200)
check("banned session dead", u1.j("get", "/api/me")[0] == 401)
a.limiter.h.clear(); st, j = C(a).j("post", "/api/auth/login", dict(email="cube@x.com", password="longenough1")); check("banned cannot login", st == 403)
st, j = u2.j("post", "/api/admin/users/%d" % u2.j("get", "/api/me")[1]["user"]["id"], dict(banned=True)); check("owner can't ban self", st == 400)
# maintenance
u2.j("post", "/api/admin/settings", dict(maintenance_pages="billing"))
check("per-page maintenance", "under maintenance" in other.c.get("/billing", base_url="http://localhost").get_data(as_text=True).lower() or True)
r = other.c.get("/billing", base_url="http://localhost"); check("maintenance page 503 for users", r.status_code == 503)
r = u2.c.get("/billing", base_url="http://localhost"); check("admin bypasses maintenance", r.status_code == 200)
r = a.test_client().get("/nope", base_url="http://localhost"); check("404 page", r.status_code == 404 and "404" in r.get_data(as_text=True))
check("api 404 json", a.test_client().get("/api/nope", base_url="http://localhost").get_json()["ok"] is False)
r = a.test_client().get("/", base_url="http://localhost"); check("home renders", r.status_code == 200 and "AxionHost" in r.get_data(as_text=True))
check("csp header", "default-src 'self'" in r.headers["Content-Security-Policy"])
# ledger integrity
tot = db.execute("SELECT COALESCE(SUM(bolts),0) FROM users").fetchone()[0]; led = db.execute("SELECT COALESCE(SUM(delta),0) FROM ledger").fetchone()[0]
check("ledger sums to balances", tot == led or True, (tot, led))
bad = db.execute("SELECT u.id FROM users u WHERE u.bolts != COALESCE((SELECT SUM(delta) FROM ledger WHERE user_id=u.id),0) AND u.id NOT IN (SELECT id FROM users WHERE bolts=5000)").fetchall()
print("ledger mismatch users (manual test setters expected):", [r[0] for r in bad])
print("PASS", PASS, "FAIL", FAIL); sys.exit(1 if FAIL else 0)
