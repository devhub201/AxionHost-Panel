"""AxionHost Panel — business operations shared by the web app and the Discord bot.
Every function takes a db connection and raises OpError(msg, http_code) on user-facing failure."""
import json, re, secrets, time
import core
from core import now, tx, add_bolts, audit, geti, Insufficient, NAME_RE

class OpError(Exception):
    def __init__(self, msg, code=400): super().__init__(msg); self.msg, self.code = msg, code

def claim_daily(db, uid):
    s = core.get_settings(db); amt = geti(s, "daily_bolts")
    with tx(db):
        row = db.execute("SELECT last_daily FROM users WHERE id=?", (uid,)).fetchone()
        wait = (row["last_daily"] or 0) + 86400 - now()
        if wait > 0: raise OpError("Come back in %dh %dm" % (wait // 3600, wait % 3600 // 60))
        db.execute("UPDATE users SET last_daily=? WHERE id=?", (now(), uid))
        nb = add_bolts(db, uid, amt, "Daily reward", earned=True)
    return dict(bolts=nb, gained=amt)

def redeem_promo(db, user, code):
    code = (code or "").strip().upper()[:40]
    if not user["discord_id"]: raise OpError("Link your Discord account first to redeem promo codes", 403)
    if not code: raise OpError("Enter a code")
    with tx(db):
        p = db.execute("SELECT * FROM promos WHERE code=? AND enabled=1", (code,)).fetchone()
        if not p or (p["expires"] and p["expires"] < now()) or p["uses"] >= p["max_uses"]: raise OpError("Invalid or expired code")
        if db.execute("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, user["id"])).fetchone(): raise OpError("You already used this code")
        db.execute("INSERT INTO promo_uses(code,user_id,at) VALUES(?,?,?)", (code, user["id"], now()))
        db.execute("UPDATE promos SET uses=uses+1 WHERE code=?", (code,))
        nb = add_bolts(db, user["id"], p["bolts"], "Promo " + code, code, earned=True)
    return dict(bolts=nb, gained=p["bolts"])

def quest_done(db, q, u):
    k = q["kind"]
    if k == "link_discord": return bool(u["discord_id"])
    if k == "set_email": return bool(u["email"] and u["pw_hash"])
    if k == "first_deploy": return bool(db.execute("SELECT 1 FROM servers WHERE user_id=?", (u["id"],)).fetchone())
    return False

def list_quests(db, u):
    out = []
    for q in db.execute("SELECT * FROM quests WHERE enabled=1"):
        claimed = bool(db.execute("SELECT 1 FROM quest_claims WHERE quest_key=? AND user_id=?", (q["key"], u["id"])).fetchone())
        out.append(dict(key=q["key"], title=q["title"], blurb=q["blurb"], bolts=q["bolts"], claimed=claimed, ready=(not claimed) and quest_done(db, q, u)))
    return out

def claim_quest(db, u, key):
    q = db.execute("SELECT * FROM quests WHERE key=? AND enabled=1", (key,)).fetchone()
    if not q: raise OpError("Unknown quest", 404)
    if not quest_done(db, q, u): raise OpError("Quest not completed yet")
    with tx(db):
        if db.execute("SELECT 1 FROM quest_claims WHERE quest_key=? AND user_id=?", (key, u["id"])).fetchone(): raise OpError("Already claimed")
        db.execute("INSERT INTO quest_claims(quest_key,user_id,at) VALUES(?,?,?)", (key, u["id"], now()))
        nb = add_bolts(db, u["id"], q["bolts"], "Quest: " + q["title"], key, earned=True)
    return dict(bolts=nb, gained=q["bolts"])

def pick_node(db, cfg=None):
    """Best node for a new server: enabled, not offline, not full - least loaded first (servers/capacity, then most free disk)."""
    best = None
    for n in db.execute("SELECT * FROM nodes WHERE enabled=1").fetchall():
        if n["health"] == "offline": continue
        c = db.execute("SELECT COUNT(*) FROM servers WHERE node_id=? AND deleted=0", (n["id"],)).fetchone()[0]
        if c >= n["max_servers"]: continue
        key = (c / max(1, n["max_servers"]), -(n["disk_free"] or 0))
        if best is None or key < best[0]: best = (key, n["id"])
    if not best: raise OpError("No node has free capacity right now")
    return best[1]

def ensure_discord_user(db, discord_id, discord_name, discord_avatar=None, ip="bot"):
    """Panel account for a Discord user. Returns (user_row, password_or_None). Created accounts get a random password and a
    login e-mail, are linked to the Discord id (so 'Continue with Discord' works too) and get the normal signup bonus."""
    did = str(discord_id)
    u = db.execute("SELECT * FROM users WHERE discord_id=?", (did,)).fetchone()
    if u:
        if u["banned"]: raise OpError("That user is banned from the panel")
        return u, None
    slug = re.sub(r"[^a-z0-9]", "", (discord_name or "").lower())[:16] or "user"
    name = re.sub(r"[^A-Za-z0-9 ._\-]", "", discord_name or "").strip()[:32] or "user" + secrets.token_hex(2)
    if len(name) < 2: name = "user" + secrets.token_hex(2)
    pw = core.gen_password(14); s = core.get_settings(db)
    with tx(db):
        for _ in range(5):
            email = "%s-%s@axionhost.user" % (slug, secrets.token_hex(2))
            if not db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone(): break
        uid = db.execute("INSERT INTO users(username,email,pw_hash,discord_id,discord_name,discord_avatar,role,created_at) VALUES(?,?,?,?,?,?,'user',?)",
                         (name, email, core.hash_password(pw), did, discord_name, discord_avatar, now())).lastrowid
        if geti(s, "signup_bonus") > 0: add_bolts(db, uid, geti(s, "signup_bonus"), "Welcome bonus", earned=True)
        audit(db, uid, "user.register", uid, "created by staff via discord", ip)
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(), pw

def admin_deploy(db, cfg, actor, discord_id, discord_name, plan_key, node_id, image_key, name, free=True, ip="bot"):
    """Staff creates a server for a Discord user. Existing account -> reuse it; no account -> create one (caller DMs the login).
    free=True pays the plan price from a ledger-visible staff grant, so renewals still bill the user normally."""
    plan = db.execute("SELECT * FROM plans WHERE key=? AND enabled=1", (plan_key,)).fetchone()
    if not plan: raise OpError("Unknown or disabled plan `%s`" % plan_key)
    img = db.execute("SELECT * FROM images WHERE key=? AND enabled=1", (image_key,)).fetchone() if image_key else db.execute("SELECT * FROM images WHERE enabled=1 ORDER BY sort LIMIT 1").fetchone()
    if not img: raise OpError("Unknown or disabled OS image")
    nid = pick_node(db, cfg) if str(node_id or "auto") == "auto" else int(node_id)
    user, pw = ensure_discord_user(db, discord_id, discord_name)
    try:
        if free:
            with tx(db):
                have = db.execute("SELECT bolts FROM users WHERE id=?", (user["id"],)).fetchone()[0]
                if have < core.plan_price(plan): add_bolts(db, user["id"], core.plan_price(plan) - have, "Staff grant: server %s" % plan["name"], "grant:%s" % actor)
        sid = deploy(db, cfg, dict(user), plan["key"], nid, img["key"], name or (re.sub(r"[^A-Za-z0-9._-]", "", discord_name or "")[:20] or "my") + "-vps", ip)
    except OpError:
        raise
    audit(db, actor, "server.admin_deploy", sid, "for user %s%s" % (user["id"], " (new account)" if pw else ""), ip)
    return dict(server_id=sid, user=db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone(), password=pw, email=user["email"], new=pw is not None)

def deploy(db, cfg, user, plan_key, node_id, image_key, name, ip=""):
    s = core.get_settings(db); name = (name or "").strip() or "my-server"
    if not NAME_RE.match(name): raise OpError("Server name: 2-32 letters, numbers, space . _ -")
    with tx(db):
        u = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        plan = db.execute("SELECT * FROM plans WHERE key=? AND enabled=1", (plan_key,)).fetchone()
        if str(node_id) == "auto": node_id = pick_node(db, cfg)
        node = db.execute("SELECT * FROM nodes WHERE id=? AND enabled=1", (node_id,)).fetchone()
        img = db.execute("SELECT * FROM images WHERE key=? AND enabled=1", (image_key,)).fetchone()
        if not (plan and node and img): raise OpError("Pick a valid plan, location and OS")
        if plan["type"] == "vm" and str(cfg.get("backend", "lxd")).lower() == "docker": raise OpError("This plan is not available on this node")
        if node["health"] == "offline": raise OpError("That location is offline right now. Pick another or try again shortly.")
        lim = geti(s, "max_servers_per_user")
        if db.execute("SELECT COUNT(*) FROM servers WHERE user_id=? AND deleted=0", (u["id"],)).fetchone()[0] >= lim: raise OpError("Server limit reached (%d)" % lim)
        if db.execute("SELECT COUNT(*) FROM servers WHERE node_id=? AND deleted=0", (node["id"],)).fetchone()[0] >= node["max_servers"]: raise OpError("That location is full. Pick another.")
        if plan["stock"] >= 0 and db.execute("SELECT COUNT(*) FROM servers WHERE plan_key=? AND deleted=0", (plan["key"],)).fetchone()[0] >= plan["stock"]: raise OpError("This plan is sold out")
        try: sid = core.create_server_row(db, cfg, u, dict(plan), dict(node), dict(img), name)
        except Insufficient: raise OpError("Not enough BOLTs. You need %d, you have %d." % (core.plan_price(plan), u["bolts"]), 402)
        audit(db, u["id"], "server.deploy", sid, plan["key"], ip)
    try: referral_reward(db, u["id"])
    except Exception: pass
    return sid

def owned(db, uid, sid):
    return db.execute("SELECT * FROM servers WHERE id=? AND user_id=? AND deleted=0", (sid, uid)).fetchone()

def queue(db, sid, act, payload=None):
    db.execute("INSERT INTO jobs(server_id,action,payload,created,updated) VALUES(?,?,?,?,?)", (sid, act, json.dumps(payload or {}), now(), now()))

def server_action(db, uid, sid, act, image_key=None, ip=""):
    r = owned(db, uid, sid)
    if not r: raise OpError("Server not found", 404)
    if act not in ("start", "stop", "restart", "reinstall", "delete", "resetpw"): raise OpError("Unknown action")
    if r["status"] in ("provisioning", "deleting"): raise OpError("Server is busy, try again shortly")
    if r["suspended"] and act != "delete": raise OpError("Server is suspended: " + (r["suspend_reason"] or "contact support"), 403)
    if act == "resetpw" and r["status"] != "running": raise OpError("Start the server first")
    if db.execute("SELECT 1 FROM jobs WHERE server_id=? AND status IN ('queued','running')", (sid,)).fetchone(): raise OpError("Another action is still running")
    payload = {}
    if act == "reinstall":
        img = db.execute("SELECT * FROM images WHERE key=? AND enabled=1", (image_key or r["image_key"],)).fetchone()
        if not img: raise OpError("Pick a valid OS")
        payload = dict(image=img["lxc_image"], image_key=img["key"])
    with tx(db):
        queue(db, sid, act, payload)
        if act in ("reinstall", "delete"): db.execute("UPDATE servers SET status=? WHERE id=?", ("provisioning" if act == "reinstall" else "deleting", sid))
        audit(db, uid, "server." + act, sid, "", ip)

TOOLS = ("sshx", "pinggy")

def tool_action(db, uid, sid, kind, act, ip=""):
    """Start/stop a helper inside the VPS: sshx (web terminal link) or pinggy (public SSH address). Runs as a worker job."""
    if kind not in TOOLS or act not in ("start", "stop"): raise OpError("Unknown tool action")
    r = owned(db, uid, sid)
    if not r: raise OpError("Server not found", 404)
    if r["suspended"]: raise OpError("Server is suspended: " + (r["suspend_reason"] or "contact support"), 403)
    if r["status"] != "running": raise OpError("Start the server first")
    if db.execute("SELECT 1 FROM jobs WHERE server_id=? AND status IN ('queued','running')", (sid,)).fetchone(): raise OpError("Another action is still running")
    with tx(db):
        db.execute("INSERT INTO server_tools(server_id,kind,status,url,note,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(server_id,kind) DO UPDATE SET status=excluded.status,url=CASE WHEN excluded.status='starting' THEN '' ELSE url END,note='',updated=excluded.updated",
                   (sid, kind, "starting" if act == "start" else "stopping", "", "", now()))
        queue(db, sid, "tool_" + act, dict(kind=kind))
        audit(db, uid, "server.tool_" + act, sid, kind, ip)

def renew(db, uid, sid, ip=""):
    s = core.get_settings(db)
    with tx(db):
        r = owned(db, uid, sid)
        if not r: raise OpError("Server not found", 404)
        if r["status"] in ("provisioning", "deleting"): raise OpError("Server is busy")
        if r["suspend_reason"] and not r["suspend_reason"].startswith("billing"): raise OpError("Server is suspended: " + r["suspend_reason"], 403)
        try: add_bolts(db, uid, -r["price"], "Renew " + r["name"], r["container"])
        except Insufficient: raise OpError("Not enough BOLTs (need %d)" % r["price"], 402)
        new_exp = max(r["expires_at"], now()) + geti(s, "billing_days") * 86400
        db.execute("UPDATE servers SET expires_at=?,suspended=0,suspend_reason='' WHERE id=?", (new_exp, sid))
        if r["suspended"]: queue(db, sid, "start")
        audit(db, uid, "server.renew", sid, "", ip)
    return new_exp

def adjust_bolts(db, actor, uid, delta, reason="", ip=""):
    if not isinstance(delta, int) or delta == 0 or abs(delta) > 10_000_000: raise OpError("Invalid amount")
    with tx(db):
        try: nb = add_bolts(db, uid, delta, "Admin: " + (reason or "adjustment")[:80], "admin:%s" % actor, earned=True)
        except Insufficient: raise OpError("User would go negative")
        except ValueError: raise OpError("No such user", 404)
        audit(db, actor, "user.bolts", uid, "%+d %s" % (delta, reason), ip)
    return nb

def give_all(db, actor, amount, reason="", only_with_discord=False, ip=""):
    """Credit every active (not banned) account. One transaction, one ledger row each."""
    if not isinstance(amount, int) or amount <= 0 or amount > 1_000_000: raise OpError("Amount must be 1 - 1,000,000")
    q = "SELECT id FROM users WHERE banned=0" + (" AND discord_id IS NOT NULL AND discord_id!=''" if only_with_discord else "")
    with tx(db):
        ids = [r[0] for r in db.execute(q).fetchall()]
        for uid in ids: add_bolts(db, uid, amount, "Gift: " + ((reason or "from staff")[:80]), "gift:%s" % actor)
        audit(db, actor, "users.giveall", len(ids), "%+d %s" % (amount, reason), ip)
    return len(ids)

def set_ban(db, actor, uid, banned, reason="", ip=""):
    with tx(db):
        if not db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone(): raise OpError("No such user", 404)
        db.execute("UPDATE users SET banned=?,ban_reason=? WHERE id=?", (1 if banned else 0, reason[:200], uid))
        if banned: db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        audit(db, actor, "user.ban" if banned else "user.unban", uid, reason, ip)

def admin_server(db, actor, sid, act, reason="", days=0, ip=""):
    r = db.execute("SELECT * FROM servers WHERE id=? AND deleted=0", (sid,)).fetchone()
    if not r: raise OpError("Server not found", 404)
    with tx(db):
        if act == "suspend":
            db.execute("UPDATE servers SET suspended=1,suspend_reason=? WHERE id=?", ("admin: " + (reason or "policy")[:100], sid)); queue(db, sid, "stop")
        elif act == "unsuspend":
            db.execute("UPDATE servers SET suspended=0,suspend_reason='' WHERE id=?", (sid,)); queue(db, sid, "start")
        elif act == "extend":
            if not -365 <= days <= 3650 or days == 0: raise OpError("Invalid days")
            db.execute("UPDATE servers SET expires_at=expires_at+? WHERE id=?", (days * 86400, sid))
        elif act in ("delete", "start", "stop", "restart"):
            queue(db, sid, act)
            if act == "delete": db.execute("UPDATE servers SET status='deleting' WHERE id=?", (sid,))
        else: raise OpError("Unknown action")
        audit(db, actor, "admin.server." + act, sid, reason or days, ip)
    return r

def staff_alert(db, title, body, kind="staff"):
    """Post to the staff log channel (bot delivers it)."""
    db.execute("INSERT INTO notify(user_id,title,body,kind,created,to_log) VALUES(NULL,?,?,?,?,1)", (title, body[:900], kind, now()))

def notify(db, user_id, title, body, kind="info"):
    """Queue a Discord DM/log message for the bot to deliver."""
    u = db.execute("SELECT discord_id FROM users WHERE id=?", (user_id,)).fetchone()
    db.execute("INSERT INTO notify(user_id,discord_id,title,body,kind,created) VALUES(?,?,?,?,?,?)", (user_id, u["discord_id"] if u else None, title, body, kind, now()))


# ---------------- snapshots ----------------
SNAP_RE = re.compile(r"^[A-Za-z0-9 ._\-]{2,32}$")

def _idle(db, sid):
    if db.execute("SELECT 1 FROM jobs WHERE server_id=? AND status IN ('queued','running')", (sid,)).fetchone(): raise OpError("Another action is still running")

def snapshot_create(db, uid, sid, name, ip=""):
    r = owned(db, uid, sid)
    if not r: raise OpError("Server not found", 404)
    if r["suspended"]: raise OpError("Server is suspended: " + (r["suspend_reason"] or "contact support"), 403)
    if r["status"] not in ("running", "stopped"): raise OpError("Server is busy, try again shortly")
    name = (name or "").strip() or time.strftime("snap-%d%b-%H%M")
    if not SNAP_RE.match(name): raise OpError("Snapshot name: 2-32 letters, numbers, space . _ -")
    lim = geti(core.get_settings(db), "snapshots_per_server")
    _idle(db, sid)
    with tx(db):
        if db.execute("SELECT COUNT(*) FROM snapshots WHERE server_id=? AND status!='failed'", (sid,)).fetchone()[0] >= lim: raise OpError("Snapshot limit reached (%d). Delete one first." % lim)
        snid = db.execute("INSERT INTO snapshots(server_id,name,status,created) VALUES(?,?,'creating',?)", (sid, name, now())).lastrowid
        queue(db, sid, "snapshot", dict(id=snid)); audit(db, uid, "snapshot.create", sid, name, ip)
    return snid

def snapshot_restore(db, uid, sid, snid, ip=""):
    r = owned(db, uid, sid)
    if not r: raise OpError("Server not found", 404)
    if r["suspended"]: raise OpError("Server is suspended", 403)
    if r["status"] not in ("running", "stopped"): raise OpError("Server is busy, try again shortly")
    sn = db.execute("SELECT * FROM snapshots WHERE id=? AND server_id=?", (snid, sid)).fetchone()
    if not sn or sn["status"] != "ready": raise OpError("Snapshot is not ready")
    _idle(db, sid)
    with tx(db):
        db.execute("UPDATE servers SET status='provisioning' WHERE id=?", (sid,)); queue(db, sid, "restore", dict(id=snid)); audit(db, uid, "snapshot.restore", sid, sn["name"], ip)

def snapshot_delete(db, uid, sid, snid, ip=""):
    r = owned(db, uid, sid)
    if not r: raise OpError("Server not found", 404)
    sn = db.execute("SELECT * FROM snapshots WHERE id=? AND server_id=?", (snid, sid)).fetchone()
    if not sn: raise OpError("Snapshot not found", 404)
    if sn["status"] in ("creating", "restoring"): raise OpError("Snapshot is busy")
    _idle(db, sid)
    with tx(db):
        db.execute("UPDATE snapshots SET status='deleting' WHERE id=?", (snid,)); queue(db, sid, "snapdel", dict(id=snid)); audit(db, uid, "snapshot.delete", sid, sn["name"], ip)

# ---------------- extra public ports ----------------
def port_add(db, uid, sid, target, proto="tcp", ip=""):
    r = owned(db, uid, sid)
    if not r: raise OpError("Server not found", 404)
    if r["suspended"]: raise OpError("Server is suspended", 403)
    if r["status"] != "running": raise OpError("Start the server first")
    try: target = int(target)
    except (TypeError, ValueError): raise OpError("Port must be a number")
    if not 1 <= target <= 65535: raise OpError("Port must be 1-65535")
    if proto not in ("tcp", "udp"): raise OpError("Protocol must be tcp or udp")
    s = core.get_settings(db); lim = geti(s, "ports_per_server")
    m = re.match(r"^(\d{2,5})-(\d{2,5})$", s.get("port_range") or "")
    lo, hi = (int(m.group(1)), int(m.group(2))) if m else (30000, 39999)
    with tx(db):
        if db.execute("SELECT COUNT(*) FROM port_forwards WHERE server_id=? AND status!='deleting'", (sid,)).fetchone()[0] >= lim: raise OpError("Port limit reached (%d). Remove one first." % lim)
        if db.execute("SELECT 1 FROM port_forwards WHERE server_id=? AND target_port=? AND proto=? AND status!='deleting'", (sid, target, proto)).fetchone(): raise OpError("That port is already forwarded")
        used = {x[0] for x in db.execute("SELECT public_port FROM port_forwards")} | {x[0] for x in db.execute("SELECT ssh_port FROM servers WHERE ssh_port IS NOT NULL")}
        free = [p for p in range(lo, hi + 1) if p not in used]
        if not free: raise OpError("No public ports left on this panel")
        pub = secrets.choice(free)
        fid = db.execute("INSERT INTO port_forwards(server_id,public_port,target_port,proto,created) VALUES(?,?,?,?,?)", (sid, pub, target, proto, now())).lastrowid
        queue(db, sid, "ports", {}); audit(db, uid, "port.add", sid, "%s %d->%d" % (proto, pub, target), ip)
    return dict(id=fid, public_port=pub)

def port_remove(db, uid, sid, fid, ip=""):
    if not owned(db, uid, sid): raise OpError("Server not found", 404)
    p = db.execute("SELECT * FROM port_forwards WHERE id=? AND server_id=?", (fid, sid)).fetchone()
    if not p: raise OpError("Port not found", 404)
    with tx(db):
        db.execute("UPDATE port_forwards SET status='deleting' WHERE id=?", (fid,)); queue(db, sid, "ports", {}); audit(db, uid, "port.remove", sid, str(p["public_port"]), ip)


# ---------------- referrals ----------------
def ref_code(db, uid):
    u = db.execute("SELECT ref_code FROM users WHERE id=?", (uid,)).fetchone()
    if u and u["ref_code"]: return u["ref_code"]
    for _ in range(8):
        code = secrets.token_hex(4)
        if not db.execute("SELECT 1 FROM users WHERE ref_code=?", (code,)).fetchone():
            db.execute("UPDATE users SET ref_code=? WHERE id=?", (code, uid)); return code
    raise OpError("Could not create a referral code")

def referral_apply(db, new_uid, code):
    """Called at signup. The new user gets a small bonus now; the referrer is paid after the friend's first server (see referral_reward)."""
    code = (code or "").strip().lower()
    if not re.match(r"^[0-9a-f]{8}$", code): return False
    ref = db.execute("SELECT id FROM users WHERE ref_code=? AND banned=0", (code,)).fetchone()
    if not ref or ref["id"] == new_uid: return False
    s = core.get_settings(db)
    with tx(db):
        db.execute("UPDATE users SET referred_by=? WHERE id=? AND referred_by IS NULL", (ref["id"], new_uid))
        if geti(s, "referral_new") > 0: add_bolts(db, new_uid, geti(s, "referral_new"), "Referral welcome bonus", "ref:%d" % ref["id"], earned=True)
    return True

def referral_reward(db, uid):
    """First server of a referred user pays the referrer (once, capped)."""
    u = db.execute("SELECT referred_by,ref_paid FROM users WHERE id=?", (uid,)).fetchone()
    if not u or not u["referred_by"] or u["ref_paid"]: return
    s = core.get_settings(db); amt = geti(s, "referral_reward")
    paid = db.execute("SELECT COUNT(*) FROM users WHERE referred_by=? AND ref_paid=1", (u["referred_by"],)).fetchone()[0]
    with tx(db):
        db.execute("UPDATE users SET ref_paid=1 WHERE id=?", (uid,))
        if amt > 0 and paid < geti(s, "referral_max"):
            add_bolts(db, u["referred_by"], amt, "Referral reward", "ref:%d" % uid, earned=True)
            notify(db, u["referred_by"], "Referral reward", "A friend you referred launched their first server. **+%d BOLTs**." % amt, "invite")

def referral_stats(db, uid):
    code = ref_code(db, uid)
    return dict(code=code, joined=db.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (uid,)).fetchone()[0], paid=db.execute("SELECT COUNT(*) FROM users WHERE referred_by=? AND ref_paid=1", (uid,)).fetchone()[0],
                earned=db.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE user_id=? AND reason='Referral reward'", (uid,)).fetchone()[0])


# ---------------- support tickets ----------------
def ticket_open(db, uid, subject, body, server_id=None, ip=""):
    subject = (subject or "").strip(); body = (body or "").strip()
    if not 3 <= len(subject) <= 80: raise OpError("Subject: 3-80 characters")
    if not 5 <= len(body) <= 4000: raise OpError("Message: 5-4000 characters")
    if db.execute("SELECT COUNT(*) FROM tickets WHERE user_id=? AND status!='closed'", (uid,)).fetchone()[0] >= 3: raise OpError("You already have 3 open tickets. Close one first.")
    if server_id and not owned(db, uid, server_id): server_id = None
    t = now()
    with tx(db):
        tid = db.execute("INSERT INTO tickets(user_id,subject,status,server_id,created,updated) VALUES(?,?,'open',?,?,?)", (uid, subject, server_id, t, t)).lastrowid
        db.execute("INSERT INTO ticket_msgs(ticket_id,user_id,staff,body,created) VALUES(?,?,0,?,?)", (tid, uid, body, t))
        audit(db, uid, "ticket.open", tid, subject[:60], ip)
        staff_alert(db, "New ticket #%d" % tid, "**%s**\n%s" % (subject, body[:300]), "ticket")
    return tid

def ticket_reply(db, actor, tid, body, staff=False, ip=""):
    body = (body or "").strip()
    if not 1 <= len(body) <= 4000: raise OpError("Message: 1-4000 characters")
    tk = db.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not tk or (not staff and tk["user_id"] != actor): raise OpError("Ticket not found", 404)
    if tk["status"] == "closed": raise OpError("This ticket is closed")
    t = now()
    with tx(db):
        db.execute("INSERT INTO ticket_msgs(ticket_id,user_id,staff,body,created) VALUES(?,?,?,?,?)", (tid, actor, 1 if staff else 0, body, t))
        db.execute("UPDATE tickets SET status=?,updated=? WHERE id=?", ("answered" if staff else "open", t, tid))
        audit(db, actor, "ticket.reply", tid, "staff" if staff else "user", ip)
        if staff: notify(db, tk["user_id"], "Reply to ticket #%d" % tid, "**%s**\n%s" % (tk["subject"], body[:900]), "ticket")
        else: staff_alert(db, "Ticket #%d: new reply" % tid, "**%s**\n%s" % (tk["subject"], body[:300]), "ticket")

def ticket_close(db, actor, tid, staff=False, ip=""):
    tk = db.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not tk or (not staff and tk["user_id"] != actor): raise OpError("Ticket not found", 404)
    with tx(db):
        db.execute("UPDATE tickets SET status='closed',updated=? WHERE id=?", (now(), tid)); audit(db, actor, "ticket.close", tid, "", ip)
        if staff and tk["user_id"] != actor: notify(db, tk["user_id"], "Ticket #%d closed" % tid, "**%s** was closed by staff. Open a new ticket if you still need help." % tk["subject"], "ticket")

def ticket_thread(db, tid):
    return [dict(id=m["id"], staff=bool(m["staff"]), body=m["body"], at=m["created"], who=m["username"] or "user") for m in db.execute("SELECT m.*,u.username FROM ticket_msgs m LEFT JOIN users u ON u.id=m.user_id WHERE m.ticket_id=? ORDER BY m.id", (tid,))]
