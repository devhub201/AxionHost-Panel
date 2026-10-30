"""AxionHost Panel — business operations shared by the web app and the Discord bot.
Every function takes a db connection and raises OpError(msg, http_code) on user-facing failure."""
import json
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

def deploy(db, cfg, user, plan_key, node_id, image_key, name, ip=""):
    s = core.get_settings(db); name = (name or "").strip() or "my-server"
    if not NAME_RE.match(name): raise OpError("Server name: 2-32 letters, numbers, space . _ -")
    with tx(db):
        u = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        plan = db.execute("SELECT * FROM plans WHERE key=? AND enabled=1", (plan_key,)).fetchone()
        node = db.execute("SELECT * FROM nodes WHERE id=? AND enabled=1", (node_id,)).fetchone()
        img = db.execute("SELECT * FROM images WHERE key=? AND enabled=1", (image_key,)).fetchone()
        if not (plan and node and img): raise OpError("Pick a valid plan, location and OS")
        lim = geti(s, "max_servers_per_user")
        if db.execute("SELECT COUNT(*) FROM servers WHERE user_id=? AND deleted=0", (u["id"],)).fetchone()[0] >= lim: raise OpError("Server limit reached (%d)" % lim)
        if db.execute("SELECT COUNT(*) FROM servers WHERE node_id=? AND deleted=0", (node["id"],)).fetchone()[0] >= node["max_servers"]: raise OpError("That location is full. Pick another.")
        if plan["stock"] >= 0 and db.execute("SELECT COUNT(*) FROM servers WHERE plan_key=? AND deleted=0", (plan["key"],)).fetchone()[0] >= plan["stock"]: raise OpError("This plan is sold out")
        try: sid = core.create_server_row(db, cfg, u, dict(plan), dict(node), dict(img), name)
        except Insufficient: raise OpError("Not enough BOLTs. You need %d, you have %d." % (plan["bolts"], u["bolts"]), 402)
        audit(db, u["id"], "server.deploy", sid, plan["key"], ip)
    return sid

def owned(db, uid, sid):
    return db.execute("SELECT * FROM servers WHERE id=? AND user_id=? AND deleted=0", (sid, uid)).fetchone()

def queue(db, sid, act, payload=None):
    db.execute("INSERT INTO jobs(server_id,action,payload,created,updated) VALUES(?,?,?,?,?)", (sid, act, json.dumps(payload or {}), now(), now()))

def server_action(db, uid, sid, act, image_key=None, ip=""):
    r = owned(db, uid, sid)
    if not r: raise OpError("Server not found", 404)
    if act not in ("start", "stop", "restart", "reinstall", "delete"): raise OpError("Unknown action")
    if r["status"] in ("provisioning", "deleting"): raise OpError("Server is busy, try again shortly")
    if r["suspended"] and act != "delete": raise OpError("Server is suspended: " + (r["suspend_reason"] or "contact support"), 403)
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

def notify(db, user_id, title, body, kind="info"):
    """Queue a Discord DM/log message for the bot to deliver."""
    u = db.execute("SELECT discord_id FROM users WHERE id=?", (user_id,)).fetchone()
    db.execute("INSERT INTO notify(user_id,discord_id,title,body,kind,created) VALUES(?,?,?,?,?,?)", (user_id, u["discord_id"] if u else None, title, body, kind, now()))
