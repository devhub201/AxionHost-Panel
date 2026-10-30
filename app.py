"""AxionHost Panel — web app (Flask). Original code.  Run: python app.py  (dev)  |  waitress/gunicorn app:app (prod)"""
import json, os, re, secrets, time, urllib.parse
from functools import wraps
import requests
from flask import Flask, g, jsonify, make_response, redirect, render_template, request
from itsdangerous import BadSignature, URLSafeTimedSerializer
import core, ops
from core import (now, tx, add_bolts, audit, geti, Insufficient, hash_password, verify_password, enc, dec, sha,
                  EMAIL_RE, NAME_RE, IMG_RE, REMOTE_RE)

ROLES = {"user": 0, "support": 1, "admin": 2, "owner": 3}
COOKIE = "axp_s"
SESSION_DAYS = 14

def create_app(cfg=None, http=None):
    cfg = cfg or core.load_config()
    app = Flask(__name__, template_folder=os.path.join(core.HERE, "templates"), static_folder=os.path.join(core.HERE, "static"))
    app.config["CFG"] = cfg
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
    http = http or requests
    db = core.connect(cfg["db_path"]); core.init_db(db)
    limiter = core.RateLimiter()
    oauth_ser = URLSafeTimedSerializer(cfg["secret_key"], salt="axp-oauth")
    app.db = db; app.limiter = limiter

    # ---------------- helpers ----------------
    def ip(): return (request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0] or "").strip()[:45]
    def err(msg, code=400): return jsonify(ok=False, error=msg), code
    def ok(**kw): return jsonify(ok=True, **kw)
    def S(): return core.get_settings(db)

    def current():
        if hasattr(g, "_u"): return g._u
        g._u = None; g._sess = None
        tok = request.cookies.get(COOKIE)
        if tok:
            s = db.execute("SELECT * FROM sessions WHERE token_hash=? AND expires>?", (sha(tok), now())).fetchone()
            if s:
                u = db.execute("SELECT * FROM users WHERE id=?", (s["user_id"],)).fetchone()
                if u and not u["banned"]: g._u, g._sess = u, s
        return g._u

    def start_session(uid, resp):
        tok = secrets.token_urlsafe(32); t = now()
        db.execute("INSERT INTO sessions(token_hash,user_id,csrf,created,expires,ip,ua) VALUES(?,?,?,?,?,?,?)",
                   (sha(tok), uid, secrets.token_urlsafe(24), t, t + SESSION_DAYS * 86400, ip(), (request.headers.get("User-Agent") or "")[:200]))
        db.execute("UPDATE users SET last_login=? WHERE id=?", (t, uid))
        db.execute("DELETE FROM sessions WHERE expires<?", (t,))
        resp.set_cookie(COOKIE, tok, max_age=SESSION_DAYS * 86400, httponly=True, samesite="Lax", secure=cfg["cookie_secure"], path="/")
        return resp

    def origin_ok():
        o = request.headers.get("Origin")
        if not o: return request.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")
        return urllib.parse.urlparse(o).netloc == request.host

    def api(role=None, auth=True, rate=None):
        def deco(fn):
            @wraps(fn)
            def w(*a, **kw):
                if request.method != "GET":
                    if not origin_ok(): return err("Bad origin", 403)
                if rate and not limiter.hit("%s:%s" % (fn.__name__, ip()), rate[0], rate[1]): return err("Too many attempts. Slow down.", 429)
                if auth:
                    u = current()
                    if not u: return err("Please sign in", 401)
                    if request.method != "GET" and not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), g._sess["csrf"]): return err("CSRF check failed", 403)
                    if role and ROLES[u["role"]] < ROLES[role]: return err("Not allowed", 403)
                    if S()["maintenance"] == "1" and ROLES[u["role"]] < 2 and request.method != "GET": return err("Panel is in maintenance mode", 503)
                return fn(*a, **kw)
            return w
        return deco

    def body():
        d = request.get_json(silent=True)
        return d if isinstance(d, dict) else {}

    def methods(u): return [m for m, v in (("email", u["email"] and u["pw_hash"]), ("discord", u["discord_id"]), ("google", u["google_sub"])) if v]

    def pub_user(u):
        return dict(id=u["id"], username=u["username"], email=u["email"], has_password=bool(u["pw_hash"]), bolts=u["bolts"], role=u["role"],
                    discord=dict(id=u["discord_id"], name=u["discord_name"], avatar=u["discord_avatar"]) if u["discord_id"] else None,
                    google=dict(email=u["google_email"]) if u["google_sub"] else None, created_at=u["created_at"], last_daily=u["last_daily"],
                    lifetime_earned=u["lifetime_earned"])

    def srv_dict(r, reveal=False):
        n = db.execute("SELECT name,location,flag,host FROM nodes WHERE id=?", (r["node_id"],)).fetchone()
        d = {k: r[k] for k in ("id", "name", "plan_key", "plan_name", "container", "image_key", "type", "cpu", "ram", "disk", "price", "status",
                               "suspended", "suspend_reason", "ssh_port", "auto_renew", "created_at", "expires_at")}
        d["disk_info"] = core.disk_info(r)
        d["node"] = dict(n) if n else None
        d["ssh"] = ("ssh root@%s -p %s" % (n["host"] or "<node-ip>", r["ssh_port"])) if n and r["ssh_port"] else ""
        return d

    def new_user(username, email=None, pw=None, discord=None, google=None):
        s = S(); t = now()
        role = "owner" if discord and cfg["owner_discord_id"] and discord["id"] == str(cfg["owner_discord_id"]) else "user"
        with tx(db):
            cur = db.execute("INSERT INTO users(username,email,pw_hash,discord_id,discord_name,discord_avatar,google_sub,google_email,role,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (username, email, hash_password(pw) if pw else None, discord and discord["id"], discord and discord["name"], discord and discord["avatar"],
                 google and google["sub"], google and google["email"], role, t))
            uid = cur.lastrowid
            if geti(s, "signup_bonus") > 0: add_bolts(db, uid, geti(s, "signup_bonus"), "Welcome bonus", earned=True)
            audit(db, uid, "user.register", uid, ",".join(x for x in ("email" if email else "", "discord" if discord else "", "google" if google else "") if x), ip())
        return uid

    def clean_name(n):
        n = re.sub(r"[^A-Za-z0-9 ._\-]", "", (n or "").strip())[:32].strip()
        return n if len(n) >= 2 else "user" + secrets.token_hex(2)

    # ---------------- security headers ----------------
    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"; resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: https://cdn.discordapp.com https://*.googleusercontent.com; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if cfg["cookie_secure"]: resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.path.startswith("/api/"): resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---------------- pages ----------------
    def page(tpl, need=None, key=None, **kw):
        u = current(); s = S()
        if need and not u: return redirect("/login?next=" + urllib.parse.quote(request.path))
        if need and ROLES[u["role"]] < ROLES[need]: return render_template("404.html", **ctx()), 404
        staff = bool(u and ROLES[u["role"]] >= 2)
        down = s["maintenance"] == "1" or (key and key in [x.strip() for x in s["maintenance_pages"].split(",")])
        if down and not staff and tpl != "login.html": return render_template("maintenance.html", **ctx()), 503
        return render_template(tpl, **ctx(), **kw)

    def ctx():
        u = current(); s = S()
        return dict(user=pub_user(u) if u else None, csrf=g._sess["csrf"] if u else "", site=s, version=core.PANEL_VERSION,
                    providers=dict(discord=bool(cfg["discord_client_id"]), google=bool(cfg["google_client_id"])), staff=bool(u and ROLES[u["role"]] >= 1))

    @app.get("/")
    def home():
        plans = [dict(r) for r in db.execute("SELECT * FROM plans WHERE enabled=1 ORDER BY sort,bolts")]
        return render_template("home.html", **ctx(), plans=plans)
    @app.get("/login")
    def login_page():
        if current(): return redirect("/dashboard")
        return page("login.html", mode=request.args.get("mode", "login"), next=request.args.get("next", "/dashboard"), msg=request.args.get("msg", ""))
    @app.get("/dashboard")
    def dashboard(): return page("dashboard.html", "user", key="dashboard")
    @app.get("/deploy")
    def deploy_page(): return page("deploy.html", "user", key="deploy")
    @app.get("/servers/<int:sid>")
    def server_page(sid): return page("server.html", "user", key="dashboard", sid=sid)
    @app.get("/earn")
    def earn_page(): return page("earn.html", "user", key="earn")
    @app.get("/billing")
    def billing_page(): return page("billing.html", "user", key="billing")
    @app.get("/settings")
    def settings_page(): return page("settings.html", "user", key="settings")
    @app.get("/admin")
    def admin_page(): return page("admin.html", "support")
    @app.get("/healthz")
    def healthz(): return ok(version=core.PANEL_VERSION)
    @app.errorhandler(404)
    def nf(e):
        if request.path.startswith("/api/"): return err("Not found", 404)
        return render_template("404.html", **ctx()), 404
    @app.errorhandler(405)
    def na(e): return err("Method not allowed", 405)
    @app.errorhandler(413)
    def big(e): return err("Request too large", 413)

    # ---------------- auth ----------------
    @app.post("/api/auth/register")
    @api(auth=False, rate=(8, 600))
    def register():
        d = body(); s = S()
        if s["registration_open"] != "1" or s["email_signup"] != "1": return err("Registration with email is closed")
        email = (d.get("email") or "").strip().lower(); pw = d.get("password") or ""; name = (d.get("username") or "").strip()
        if not EMAIL_RE.match(email): return err("Enter a valid email")
        if len(pw) < geti(s, "min_password") or len(pw) > 200: return err("Password must be at least %d characters" % geti(s, "min_password"))
        if not NAME_RE.match(name): return err("Username: 2-32 letters, numbers, space . _ -")
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone(): return err("That email is already registered")
        uid = new_user(name, email, pw)
        return start_session(uid, ok(next="/dashboard"))

    @app.post("/api/auth/login")
    @api(auth=False, rate=(15, 600))
    def login():
        d = body(); email = (d.get("email") or "").strip().lower()
        if not limiter.hit("login:" + email, 8, 600): return err("Too many attempts for this account. Try later.", 429)
        u = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not u or not u["pw_hash"] or not verify_password(d.get("password") or "", u["pw_hash"]): return err("Wrong email or password", 401)
        if u["banned"]: return err("Account banned: " + (u["ban_reason"] or "contact staff"), 403)
        if S()["maintenance"] == "1" and ROLES[u["role"]] < 2: return err("Panel is in maintenance mode", 503)
        return start_session(u["id"], ok(next=d.get("next") if str(d.get("next", "")).startswith("/") and not str(d.get("next")).startswith("//") else "/dashboard"))

    @app.post("/api/auth/logout")
    @api()
    def logout():
        db.execute("DELETE FROM sessions WHERE token_hash=?", (g._sess["token_hash"],))
        r = ok(); r.delete_cookie(COOKIE); return r

    def oauth_cfg(p):
        if p == "discord":
            return dict(auth="https://discord.com/oauth2/authorize", token="https://discord.com/api/oauth2/token", id=cfg["discord_client_id"], secret=cfg["discord_client_secret"], scope="identify email")
        return dict(auth="https://accounts.google.com/o/oauth2/v2/auth", token="https://oauth2.googleapis.com/token", id=cfg["google_client_id"], secret=cfg["google_client_secret"], scope="openid email profile")

    @app.get("/auth/<provider>")
    def oauth_start(provider):
        if provider not in ("discord", "google"): return redirect("/login")
        o = oauth_cfg(provider)
        if not o["id"]: return redirect("/login?msg=" + urllib.parse.quote(provider.title() + " login is not configured"))
        u = current(); nonce = secrets.token_urlsafe(16)
        state = oauth_ser.dumps(dict(p=provider, n=nonce, uid=u["id"] if u else 0, next=request.args.get("next", "/dashboard")))
        q = dict(client_id=o["id"], redirect_uri=cfg["base_url"].rstrip("/") + "/auth/%s/callback" % provider, response_type="code", scope=o["scope"], state=nonce)
        r = redirect(o["auth"] + "?" + urllib.parse.urlencode(q))
        r.set_cookie("axp_oauth", state, max_age=600, httponly=True, samesite="Lax", secure=cfg["cookie_secure"], path="/auth")
        return r

    def fetch_identity(provider, code):
        o = oauth_cfg(provider)
        tr = http.post(o["token"], data=dict(client_id=o["id"], client_secret=o["secret"], grant_type="authorization_code", code=code,
                       redirect_uri=cfg["base_url"].rstrip("/") + "/auth/%s/callback" % provider), timeout=15)
        if tr.status_code != 200: raise RuntimeError("token exchange failed")
        at = tr.json()["access_token"]
        if provider == "discord":
            ur = http.get("https://discord.com/api/users/@me", headers={"Authorization": "Bearer " + at}, timeout=15)
            if ur.status_code != 200: raise RuntimeError("profile fetch failed")
            j = ur.json(); av = ("https://cdn.discordapp.com/avatars/%s/%s.png?size=128" % (j["id"], j["avatar"])) if j.get("avatar") else ""
            return dict(id=str(j["id"]), name=j.get("global_name") or j.get("username") or "discord", avatar=av)
        ur = http.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": "Bearer " + at}, timeout=15)
        if ur.status_code != 200: raise RuntimeError("profile fetch failed")
        j = ur.json()
        if not j.get("email_verified"): raise RuntimeError("Google email is not verified")
        return dict(sub=str(j["sub"]), email=j.get("email", ""), name=j.get("name") or (j.get("email") or "user").split("@")[0])

    @app.get("/auth/<provider>/callback")
    def oauth_cb(provider):
        def bad(m): return redirect("/login?msg=" + urllib.parse.quote(m))
        if provider not in ("discord", "google"): return redirect("/login")
        try: st = oauth_ser.loads(request.cookies.get("axp_oauth", ""), max_age=600)
        except BadSignature: return bad("Login session expired. Try again.")
        if st.get("p") != provider or not secrets.compare_digest(st.get("n", ""), request.args.get("state", "")): return bad("Invalid login state")
        if request.args.get("error") or not request.args.get("code"): return bad("Login was cancelled")
        try: ident = fetch_identity(provider, request.args["code"])
        except Exception as e: return bad("%s login failed: %s" % (provider.title(), e))
        col = "discord_id" if provider == "discord" else "google_sub"; key = ident["id"] if provider == "discord" else ident["sub"]
        found = db.execute("SELECT * FROM users WHERE %s=?" % col, (key,)).fetchone()
        nxt = st.get("next") if str(st.get("next", "")).startswith("/") and not str(st.get("next")).startswith("//") else "/dashboard"
        cu = current()
        if cu and st.get("uid") == cu["id"]:               # LINK mode
            if found and found["id"] != cu["id"]: return redirect("/settings?msg=" + urllib.parse.quote("That %s account is already linked to another user" % provider.title()))
            with tx(db):
                if provider == "discord":
                    db.execute("UPDATE users SET discord_id=?,discord_name=?,discord_avatar=? WHERE id=?", (ident["id"], ident["name"], ident["avatar"], cu["id"]))
                else:
                    db.execute("UPDATE users SET google_sub=?,google_email=? WHERE id=?", (ident["sub"], ident["email"], cu["id"]))
                audit(db, cu["id"], "account.link", provider, "", ip())
            return redirect("/settings?msg=" + urllib.parse.quote(provider.title() + " linked"))
        if found:
            if found["banned"]: return bad("Account banned: " + (found["ban_reason"] or "contact staff"))
            if provider == "discord": db.execute("UPDATE users SET discord_name=?,discord_avatar=? WHERE id=?", (ident["name"], ident["avatar"], found["id"]))
            uid = found["id"]
        else:
            if S()["registration_open"] != "1": return bad("Registration is closed")
            uid = new_user(clean_name(ident["name"]), discord=ident if provider == "discord" else None, google=ident if provider == "google" else None)
        r = redirect(nxt); r.delete_cookie("axp_oauth", path="/auth")
        return start_session(uid, r)

    # ---------------- account ----------------
    @app.get("/api/me")
    @api()
    def me():
        u = current(); s = S()
        sess = [dict(id=x["token_hash"][:10], ip=x["ip"], ua=x["ua"], created=x["created"], current=x["token_hash"] == g._sess["token_hash"])
                for x in db.execute("SELECT * FROM sessions WHERE user_id=? AND expires>? ORDER BY created DESC", (u["id"], now()))]
        return ok(user=pub_user(u), sessions=sess, methods=methods(u), settings=dict(bolts_per_invite=geti(s, "bolts_per_invite"), daily=geti(s, "daily_bolts")))

    @app.post("/api/account/username")
    @api()
    def set_username():
        n = (body().get("username") or "").strip()
        if not NAME_RE.match(n): return err("Username: 2-32 letters, numbers, space . _ -")
        db.execute("UPDATE users SET username=? WHERE id=?", (n, current()["id"])); return ok()

    @app.post("/api/account/credentials")
    @api(rate=(10, 600))
    def set_credentials():
        d = body(); u = current(); s = S(); email = (d.get("email") or u["email"] or "").strip().lower(); pw = d.get("password") or ""
        if not EMAIL_RE.match(email): return err("Enter a valid email")
        if len(pw) < geti(s, "min_password") or len(pw) > 200: return err("Password must be at least %d characters" % geti(s, "min_password"))
        if u["pw_hash"] and not verify_password(d.get("current") or "", u["pw_hash"]): return err("Current password is wrong", 403)
        o = db.execute("SELECT id FROM users WHERE email=? AND id!=?", (email, u["id"])).fetchone()
        if o: return err("That email belongs to another account")
        first = not u["pw_hash"]
        with tx(db):
            db.execute("UPDATE users SET email=?,pw_hash=? WHERE id=?", (email, hash_password(pw), u["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash!=?", (u["id"], g._sess["token_hash"]))
            audit(db, u["id"], "account.password" if not first else "account.email_set", "", "", ip())
        return ok()

    @app.post("/api/account/unlink")
    @api()
    def unlink():
        p = body().get("provider"); u = current()
        if p not in ("discord", "google"): return err("Unknown provider")
        if p not in methods(u): return err("Not linked")
        if len(methods(u)) < 2: return err("You can't remove your only sign-in method. Link another first.")
        if p == "discord": db.execute("UPDATE users SET discord_id=NULL,discord_name=NULL,discord_avatar=NULL WHERE id=?", (u["id"],))
        else: db.execute("UPDATE users SET google_sub=NULL,google_email=NULL WHERE id=?", (u["id"],))
        audit(db, u["id"], "account.unlink", p, "", ip()); return ok()

    @app.post("/api/account/sessions/revoke")
    @api()
    def revoke_sessions():
        db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash!=?", (current()["id"], g._sess["token_hash"])); return ok()

    # ---------------- earn ----------------
    def run(fn, *a, **k):
        try: return ok(**(fn(*a, **k) or {}))
        except ops.OpError as e: return err(e.msg, e.code)

    @app.post("/api/daily")
    @api()
    def daily(): return run(ops.claim_daily, db, current()["id"])

    @app.get("/api/quests")
    @api()
    def quests(): return ok(quests=ops.list_quests(db, current()))

    @app.post("/api/quests/claim")
    @api()
    def quest_claim(): return run(ops.claim_quest, db, current(), body().get("key"))

    @app.post("/api/promo/redeem")
    @api(rate=(10, 600))
    def redeem(): return run(ops.redeem_promo, db, current(), body().get("code"))

    @app.get("/api/ledger")
    @api()
    def ledger():
        off = max(0, int(request.args.get("offset", 0) or 0))
        rows = [dict(r) for r in db.execute("SELECT delta,balance,reason,created FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 50 OFFSET ?", (current()["id"], off))]
        return ok(rows=rows)

    # ---------------- catalog + servers ----------------
    @app.get("/api/catalog")
    @api()
    def catalog():
        plans = []
        for p in db.execute("SELECT * FROM plans WHERE enabled=1 ORDER BY sort,bolts"):
            d = dict(p)
            if d["stock"] >= 0:
                used = db.execute("SELECT COUNT(*) FROM servers WHERE plan_key=? AND deleted=0", (p["key"],)).fetchone()[0]
                d["left"] = max(0, d["stock"] - used)
            plans.append(d)
        nodes = []
        for n in db.execute("SELECT * FROM nodes WHERE enabled=1"):
            c = db.execute("SELECT COUNT(*) FROM servers WHERE node_id=? AND deleted=0", (n["id"],)).fetchone()[0]
            nodes.append(dict(id=n["id"], name=n["name"], location=n["location"], flag=n["flag"], full=c >= n["max_servers"], load=c, cap=n["max_servers"]))
        imgs = [dict(key=i["key"], label=i["label"]) for i in db.execute("SELECT * FROM images WHERE enabled=1 ORDER BY sort")]
        return ok(plans=plans, nodes=nodes, images=imgs, bolts=current()["bolts"], bpi=geti(S(), "bolts_per_invite"))

    @app.get("/api/servers")
    @api()
    def servers():
        return ok(servers=[srv_dict(r) for r in db.execute("SELECT * FROM servers WHERE user_id=? AND deleted=0 ORDER BY id DESC", (current()["id"],))])

    def own(sid):
        r = db.execute("SELECT * FROM servers WHERE id=? AND user_id=? AND deleted=0", (sid, current()["id"])).fetchone()
        return r

    @app.get("/api/servers/<int:sid>")
    @api()
    def server_get(sid):
        r = own(sid)
        if not r: return err("Server not found", 404)
        jobs = [dict(action=j["action"], status=j["status"], result=j["result"], created=j["created"]) for j in db.execute("SELECT * FROM jobs WHERE server_id=? ORDER BY id DESC LIMIT 8", (sid,))]
        return ok(server=srv_dict(r), jobs=jobs)

    @app.post("/api/servers")
    @api(rate=(10, 300))
    def deploy():
        d = body()
        return run(lambda: dict(id=ops.deploy(db, cfg, current(), d.get("plan"), d.get("node"), d.get("image"), d.get("name"), ip())))

    @app.post("/api/servers/<int:sid>/action")
    @api(rate=(40, 60))
    def server_action(sid):
        d = body(); return run(ops.server_action, db, current()["id"], sid, d.get("action"), d.get("image"), ip())

    @app.post("/api/servers/<int:sid>/credentials")
    @api(rate=(20, 300))
    def server_creds(sid):
        u = current(); r = own(sid)
        if not r: return err("Server not found", 404)
        if u["pw_hash"] and not verify_password(body().get("password") or "", u["pw_hash"]): return err("Wrong account password", 403)
        return ok(username="root", password=dec(cfg["secret_key"], r["root_pw_enc"]), ssh=srv_dict(r)["ssh"])

    @app.post("/api/servers/<int:sid>/rename")
    @api()
    def server_rename(sid):
        n = (body().get("name") or "").strip()
        if not own(sid): return err("Server not found", 404)
        if not NAME_RE.match(n): return err("Server name: 2-32 letters, numbers, space . _ -")
        db.execute("UPDATE servers SET name=? WHERE id=?", (n, sid)); return ok()

    @app.post("/api/servers/<int:sid>/autorenew")
    @api()
    def server_autorenew(sid):
        if not own(sid): return err("Server not found", 404)
        db.execute("UPDATE servers SET auto_renew=? WHERE id=?", (1 if body().get("on") else 0, sid)); return ok()

    @app.post("/api/servers/<int:sid>/renew")
    @api(rate=(10, 300))
    def server_renew(sid): return run(lambda: dict(expires_at=ops.renew(db, current()["id"], sid, ip())))

    # ---------------- public ----------------
    @app.get("/api/public/stats")
    @api(auth=False)
    def pub_stats():
        return ok(users=db.execute("SELECT COUNT(*) FROM users").fetchone()[0], servers=db.execute("SELECT COUNT(*) FROM servers WHERE deleted=0").fetchone()[0])

    # ---------------- admin ----------------
    def A(role="admin"): return api(role=role)

    @app.get("/api/admin/stats")
    @A("support")
    def a_stats():
        c = lambda q, *a: db.execute(q, a).fetchone()[0]
        t = now(); days = []
        for i in range(13, -1, -1):
            a = t - (i + 1) * 86400; b = t - i * 86400
            days.append(dict(d=time.strftime("%d %b", time.gmtime(b)), users=c("SELECT COUNT(*) FROM users WHERE created_at>=? AND created_at<?", a, b),
                             servers=c("SELECT COUNT(*) FROM servers WHERE created_at>=? AND created_at<?", a, b)))
        return ok(users=c("SELECT COUNT(*) FROM users"), banned=c("SELECT COUNT(*) FROM users WHERE banned=1"),
                  servers=c("SELECT COUNT(*) FROM servers WHERE deleted=0"), running=c("SELECT COUNT(*) FROM servers WHERE deleted=0 AND status='running'"),
                  suspended=c("SELECT COUNT(*) FROM servers WHERE deleted=0 AND suspended=1"),
                  circulating=c("SELECT COALESCE(SUM(bolts),0) FROM users"), spent30=-c("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE delta<0 AND created>?", t - 30 * 86400),
                  earned30=c("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE delta>0 AND created>?", t - 30 * 86400),
                  failed_jobs=c("SELECT COUNT(*) FROM jobs WHERE status='failed'"), queued_jobs=c("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')"), days=days,
                  plans=[dict(r) for r in db.execute("SELECT p.name, COUNT(s.id) n FROM plans p LEFT JOIN servers s ON s.plan_key=p.key AND s.deleted=0 GROUP BY p.key ORDER BY n DESC LIMIT 6")])

    @app.get("/api/admin/users")
    @A("support")
    def a_users():
        q = "%" + (request.args.get("q") or "").strip() + "%"; off = max(0, int(request.args.get("offset", 0) or 0))
        rows = db.execute("SELECT * FROM users WHERE username LIKE ? OR email LIKE ? OR discord_name LIKE ? OR discord_id LIKE ? OR CAST(id AS TEXT)=? ORDER BY id DESC LIMIT 40 OFFSET ?",
                          (q, q, q, q, (request.args.get("q") or "").strip(), off)).fetchall()
        out = []
        for r in rows:
            d = pub_user(r); d.update(banned=r["banned"], ban_reason=r["ban_reason"], servers=db.execute("SELECT COUNT(*) FROM servers WHERE user_id=? AND deleted=0", (r["id"],)).fetchone()[0]); out.append(d)
        return ok(users=out)

    @app.post("/api/admin/users/<int:uid>")
    @A("admin")
    def a_user_edit(uid):
        d = body(); me_ = current(); t = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not t: return err("No such user", 404)
        if ROLES[t["role"]] >= ROLES[me_["role"]] and t["id"] != me_["id"]: return err("You can't edit someone of equal or higher rank", 403)
        try:
            if "bolts_delta" in d:
                try: dl = int(d["bolts_delta"])
                except (TypeError, ValueError): return err("Invalid amount")
                ops.adjust_bolts(db, me_["id"], uid, dl, str(d.get("reason") or "adjustment"), ip())
            if "banned" in d:
                if uid == me_["id"]: return err("You can't ban yourself")
                ops.set_ban(db, me_["id"], uid, bool(d["banned"]), str(d.get("ban_reason") or ""), ip())
            if "role" in d:
                if me_["role"] != "owner" or d["role"] not in ROLES or uid == me_["id"]: return err("Only the owner can change roles", 403)
                with tx(db):
                    db.execute("UPDATE users SET role=? WHERE id=?", (d["role"], uid)); audit(db, me_["id"], "user.role", uid, d["role"], ip())
            if d.get("kick"):
                db.execute("DELETE FROM sessions WHERE user_id=?", (uid,)); audit(db, me_["id"], "user.kick", uid, "", ip())
        except ops.OpError as e: return err(e.msg, e.code)
        return ok()

    def crud(table, pk, fields, name, intf=(), role="admin"):
        """Generic admin CRUD. fields: {col: caster}."""
        def clean(d):
            out = {}
            for col, cast in fields.items():
                if col in d:
                    try: out[col] = cast(d[col])
                    except (TypeError, ValueError): raise ValueError("Invalid " + col)
            return out
        @app.get("/api/admin/%s" % name, endpoint="a_list_" + name)
        @A("support")
        def lst(): return ok(rows=[dict(r) for r in db.execute("SELECT * FROM %s ORDER BY %s" % (table, "sort,%s" % pk if "sort" in fields else pk))])
        @app.post("/api/admin/%s" % name, endpoint="a_save_" + name)
        @A(role)
        def save():
            d = body()
            try: v = clean(d)
            except ValueError as e: return err(str(e))
            key = d.get(pk) if pk in d else None
            if pk in fields and key not in (None, ""):
                try: key = fields[pk](key)
                except ValueError: return err("Invalid " + pk)
            if pk != "id" and not key: return err("Missing " + pk)
            v.pop(pk, None)
            ex = db.execute("SELECT 1 FROM %s WHERE %s=?" % (table, pk), (key,)).fetchone() if key not in (None, "") else None
            with tx(db):
                if ex:
                    if v: db.execute("UPDATE %s SET %s WHERE %s=?" % (table, ",".join(c + "=?" for c in v), pk), (*v.values(), key))
                else:
                    cols = list(v) + ([pk] if pk != "id" else []); vals = list(v.values()) + ([key] if pk != "id" else [])
                    if table == "promos": cols += ["created", "created_by"]; vals += [now(), current()["id"]]
                    db.execute("INSERT INTO %s(%s) VALUES(%s)" % (table, ",".join(cols), ",".join("?" * len(cols))), vals)
                audit(db, current()["id"], "%s.%s" % (name, "edit" if ex else "create"), key or "", json.dumps(v)[:300], ip())
            return ok()
        @app.post("/api/admin/%s/delete" % name, endpoint="a_del_" + name)
        @A(role)
        def delete():
            key = body().get(pk)
            if table == "plans" and db.execute("SELECT 1 FROM servers WHERE plan_key=? AND deleted=0", (key,)).fetchone(): return err("Plan has active servers. Disable it instead.")
            if table == "nodes" and db.execute("SELECT 1 FROM servers WHERE node_id=? AND deleted=0", (key,)).fetchone(): return err("Node has active servers. Disable it instead.")
            db.execute("DELETE FROM %s WHERE %s=?" % (table, pk), (key,)); audit(db, current()["id"], name + ".delete", key, "", ip()); return ok()

    slug = lambda v: (lambda x: x if re.match(r"^[a-z0-9\-]{2,24}$", x) else (_ for _ in ()).throw(ValueError("bad key")))(str(v).lower())
    pos = lambda v: (lambda x: x if x >= 0 else (_ for _ in ()).throw(ValueError("negative")))(int(v))
    nz = lambda v: (lambda x: x if x > 0 else (_ for _ in ()).throw(ValueError("must be > 0")))(int(v))
    txt = lambda n: (lambda v: str(v).strip()[:n])
    b01 = lambda v: 1 if v in (1, True, "1", "true") else 0
    def oneof(*o): return lambda v: v if v in o else (_ for _ in ()).throw(ValueError("bad value"))
    def lxcimg(v):
        if not IMG_RE.match(str(v)): raise ValueError("Invalid image (use ubuntu:22.04 or images:debian/12)")
        return str(v)
    def remote(v):
        if not REMOTE_RE.match(str(v)): raise ValueError("Invalid remote")
        return str(v)
    crud("plans", "key", dict(key=slug, name=txt(40), bolts=nz, invites=pos, cpu=nz, ram=nz, disk=nz, type=oneof("container", "vm"), blurb=txt(140), stock=int, enabled=b01, sort=int), "plans")
    crud("nodes", "id", dict(name=txt(40), location=txt(40), flag=txt(4), host=txt(120), lxc_remote=remote, max_servers=nz, enabled=b01), "nodes")
    crud("images", "key", dict(key=slug, label=txt(40), lxc_image=lxcimg, enabled=b01, sort=int), "images")
    crud("promos", "code", dict(code=lambda v: re.sub(r"[^A-Z0-9_\-]", "", str(v).upper())[:30] or (_ for _ in ()).throw(ValueError("bad code")), bolts=nz, max_uses=nz, expires=pos, enabled=b01, note=txt(100)), "promos")
    crud("quests", "key", dict(key=slug, title=txt(60), blurb=txt(140), bolts=nz, kind=oneof("link_discord", "set_email", "first_deploy"), enabled=b01), "quests")

    @app.get("/api/admin/servers")
    @A("support")
    def a_servers():
        q = (request.args.get("q") or "").strip(); like = "%" + q + "%"
        rows = db.execute("SELECT s.*, u.username owner FROM servers s JOIN users u ON u.id=s.user_id WHERE s.deleted=0 AND (s.name LIKE ? OR s.container LIKE ? OR u.username LIKE ? OR CAST(s.id AS TEXT)=?) ORDER BY s.id DESC LIMIT 60", (like, like, like, q)).fetchall()
        return ok(servers=[dict(srv_dict(r), owner=r["owner"], user_id=r["user_id"]) for r in rows])

    @app.post("/api/admin/servers/<int:sid>")
    @A("admin")
    def a_server_act(sid):
        d = body()
        try: days = int(d.get("days") or 0)
        except (TypeError, ValueError): return err("Invalid days")
        return run(lambda: (ops.admin_server(db, current()["id"], sid, d.get("action"), str(d.get("reason") or ""), days, ip()), {})[1])

    @app.get("/api/admin/jobs")
    @A("support")
    def a_jobs():
        return ok(jobs=[dict(r) for r in db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 60")])

    @app.post("/api/admin/jobs/<int:jid>/retry")
    @A("admin")
    def a_job_retry(jid):
        j = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not j or j["status"] != "failed": return err("Only failed jobs can be retried")
        db.execute("UPDATE jobs SET status='queued',attempts=0,result='',updated=? WHERE id=?", (now(), jid))
        audit(db, current()["id"], "job.retry", jid, "", ip()); return ok()

    @app.get("/api/admin/settings")
    @A("support")
    def a_settings_get(): return ok(settings=S(), keys=list(core.DEFAULT_SETTINGS))

    @app.post("/api/admin/settings")
    @A("admin")
    def a_settings_set():
        d = body(); ch = {}
        with tx(db):
            for k, v in d.items():
                if k in core.DEFAULT_SETTINGS:
                    v = str(v)[:300]
                    if k not in ("site_name", "discord_invite", "announcement", "maintenance_pages", "maintenance_msg") and not re.match(r"^-?\d+$", v): return err("%s must be a number" % k)
                    db.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v)); ch[k] = v
            audit(db, current()["id"], "settings.edit", "", json.dumps(ch)[:300], ip())
        return ok()

    @app.get("/api/admin/audit")
    @A("support")
    def a_audit():
        rows = db.execute("SELECT a.*, u.username actor_name FROM audit a LEFT JOIN users u ON u.id=a.actor ORDER BY a.id DESC LIMIT 100").fetchall()
        return ok(rows=[dict(r) for r in rows])

    @app.get("/api/admin/ledger")
    @A("support")
    def a_ledger():
        rows = db.execute("SELECT l.*, u.username FROM ledger l JOIN users u ON u.id=l.user_id ORDER BY l.id DESC LIMIT 80").fetchall()
        return ok(rows=[dict(r) for r in rows])

    return app

app = None
def get_app():
    global app
    if app is None: app = create_app()
    return app

if __name__ == "__main__":
    c = core.load_config(); a = create_app(c)
    a.run(host=c["host"], port=c["port"])
