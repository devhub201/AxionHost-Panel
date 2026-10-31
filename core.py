"""AxionHost Panel — core: config, database, security helpers, BOLTs ledger.
Original code. Shared by the web app (app.py) and the worker (worker.py)."""
import base64, hashlib, hmac, json, os, re, secrets, sqlite3, time
from contextlib import contextmanager

PANEL_VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG = {
    "secret_key": "", "base_url": "http://localhost:8080", "db_path": "panel.db",
    "bot_db_path": "", "host": "127.0.0.1", "port": 8080, "cookie_secure": False,
    "discord_client_id": "", "discord_client_secret": "", "discord_guild_id": "",
    "discord_bot_token": "", "google_client_id": "", "google_client_secret": "",
    "owner_discord_id": "",
    "trusted_proxy": "none", "ssh_port_start": 20000, "ssh_port_end": 29999, "lxc_storage_pool": "default",
}
DEFAULT_SETTINGS = {
    "site_name": "AxionHost", "discord_invite": "", "announcement": "",
    "maintenance": "0", "registration_open": "1", "email_signup": "1",
    "bolts_per_invite": "200", "daily_bolts": "50", "booster_bonus": "500",
    "signup_bonus": "0", "max_servers_per_user": "3", "grace_days": "3",
    "delete_after_days": "7", "billing_days": "30", "refund_on_fail": "1",
    "min_password": "8", "invite_min_age_days": "3", "maintenance_pages": "", "maintenance_msg": "",
}

def load_config(path=None):
    path = path or os.environ.get("AXION_PANEL_CONFIG") or os.path.join(HERE, "config.panel.json")
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    for k in list(cfg):
        v = os.environ.get("AXP_" + k.upper())
        if v not in (None, ""):
            cfg[k] = type(DEFAULT_CONFIG[k])(v) if not isinstance(DEFAULT_CONFIG[k], bool) else v.lower() in ("1", "true", "yes")
    if not cfg["secret_key"]:
        kp = os.path.join(os.path.dirname(os.path.abspath(path)), ".panel_secret")
        if os.path.exists(kp):
            cfg["secret_key"] = open(kp).read().strip()
        else:
            cfg["secret_key"] = secrets.token_hex(32)
            try:
                with open(kp, "w") as f: f.write(cfg["secret_key"])
                os.chmod(kp, 0o600)
            except OSError: pass
    return cfg

# ---------------- database ----------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, email TEXT UNIQUE, pw_hash TEXT,
  discord_id TEXT UNIQUE, discord_name TEXT, discord_avatar TEXT, google_sub TEXT UNIQUE, google_email TEXT,
  bolts INTEGER NOT NULL DEFAULT 0, role TEXT NOT NULL DEFAULT 'user', banned INTEGER NOT NULL DEFAULT 0,
  ban_reason TEXT DEFAULT '', created_at INTEGER NOT NULL, last_login INTEGER, last_daily INTEGER DEFAULT 0,
  credited_invites INTEGER NOT NULL DEFAULT 0, booster_paid INTEGER NOT NULL DEFAULT 0, lifetime_earned INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, csrf TEXT NOT NULL,
  created INTEGER NOT NULL, expires INTEGER NOT NULL, ip TEXT, ua TEXT);
CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, delta INTEGER NOT NULL,
  balance INTEGER NOT NULL, reason TEXT NOT NULL, ref TEXT DEFAULT '', created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_ledger_user ON ledger(user_id, id);
CREATE TABLE IF NOT EXISTS plans(key TEXT PRIMARY KEY, name TEXT NOT NULL, bolts INTEGER NOT NULL, invites INTEGER NOT NULL DEFAULT 0,
  cpu INTEGER NOT NULL, ram INTEGER NOT NULL, disk INTEGER NOT NULL, type TEXT NOT NULL DEFAULT 'container',
  blurb TEXT DEFAULT '', stock INTEGER NOT NULL DEFAULT -1, enabled INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS nodes(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, location TEXT DEFAULT '', flag TEXT DEFAULT '',
  host TEXT DEFAULT '', lxc_remote TEXT DEFAULT '', max_servers INTEGER NOT NULL DEFAULT 50, enabled INTEGER NOT NULL DEFAULT 1, listen TEXT NOT NULL DEFAULT '0.0.0.0');
CREATE TABLE IF NOT EXISTS images(key TEXT PRIMARY KEY, label TEXT NOT NULL, lxc_image TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS servers(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, plan_key TEXT NOT NULL, plan_name TEXT NOT NULL,
  node_id INTEGER NOT NULL, name TEXT NOT NULL, container TEXT UNIQUE NOT NULL, image_key TEXT NOT NULL, lxc_image TEXT NOT NULL, type TEXT NOT NULL,
  cpu INTEGER, ram INTEGER, disk INTEGER, price INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'provisioning',
  suspended INTEGER NOT NULL DEFAULT 0, suspend_reason TEXT DEFAULT '', ssh_port INTEGER UNIQUE, root_pw_enc TEXT DEFAULT '',
  auto_renew INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, note TEXT DEFAULT '', disk_used INTEGER, disk_checked INTEGER);
CREATE INDEX IF NOT EXISTS ix_servers_user ON servers(user_id, deleted);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER NOT NULL, action TEXT NOT NULL, payload TEXT DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued', result TEXT DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL, updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS promos(code TEXT PRIMARY KEY, bolts INTEGER NOT NULL, max_uses INTEGER NOT NULL DEFAULT 1, uses INTEGER NOT NULL DEFAULT 0,
  expires INTEGER DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1, created_by INTEGER, created INTEGER NOT NULL, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS promo_uses(code TEXT NOT NULL, user_id INTEGER NOT NULL, at INTEGER NOT NULL, PRIMARY KEY(code, user_id));
CREATE TABLE IF NOT EXISTS quests(key TEXT PRIMARY KEY, title TEXT NOT NULL, blurb TEXT DEFAULT '', bolts INTEGER NOT NULL, kind TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS quest_claims(quest_key TEXT NOT NULL, user_id INTEGER NOT NULL, at INTEGER NOT NULL, PRIMARY KEY(quest_key, user_id));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, actor INTEGER, action TEXT NOT NULL, target TEXT DEFAULT '', detail TEXT DEFAULT '', ip TEXT DEFAULT '', created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notify(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, discord_id TEXT, title TEXT NOT NULL, body TEXT DEFAULT '', kind TEXT DEFAULT 'info',
  created INTEGER NOT NULL, sent INTEGER NOT NULL DEFAULT 0, to_log INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS invite_credits(invitee_id TEXT PRIMARY KEY, inviter_id TEXT NOT NULL, joined INTEGER NOT NULL, credited INTEGER NOT NULL DEFAULT 0);
"""
SEED_IMAGES = [("ubuntu-22", "Ubuntu 22.04 LTS", "ubuntu:22.04"), ("ubuntu-24", "Ubuntu 24.04 LTS", "ubuntu:24.04"),
               ("debian-12", "Debian 12", "images:debian/12"), ("alma-9", "AlmaLinux 9", "images:almalinux/9"),
               ("alpine", "Alpine 3.20", "images:alpine/3.20")]
SEED_QUESTS = [("link_discord", "Link your Discord", "Connect Discord to your account.", 100, "link_discord"),
               ("set_email", "Add an email + password", "Secure your account with email login.", 50, "set_email"),
               ("first_deploy", "Deploy your first server", "Launch any VPS from the deploy wizard.", 150, "first_deploy")]

def now(): return int(time.time())

def connect(path):
    db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA foreign_keys=ON"); db.execute("PRAGMA busy_timeout=30000")
    return db

@contextmanager
def tx(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK"); raise

def init_db(db):
    db.executescript(SCHEMA)
    cols = {r[1] for r in db.execute("PRAGMA table_info(servers)")}          # migrate older databases
    for c in ("disk_used", "disk_checked"):
        if c not in cols: db.execute("ALTER TABLE servers ADD COLUMN %s INTEGER" % c)
    if "listen" not in {r[1] for r in db.execute("PRAGMA table_info(nodes)")}:
        db.execute("ALTER TABLE nodes ADD COLUMN listen TEXT NOT NULL DEFAULT '0.0.0.0'")
    with tx(db):
        for k, v in DEFAULT_SETTINGS.items():
            db.execute("INSERT OR IGNORE INTO settings(k,v) VALUES(?,?)", (k, v))
        if not db.execute("SELECT 1 FROM plans LIMIT 1").fetchone():
            seed = os.path.join(HERE, "plans.seed.json")
            if os.path.exists(seed):
                d = json.load(open(seed, encoding="utf-8"))
                if d.get("bolts_per_invite"):
                    db.execute("UPDATE settings SET v=? WHERE k='bolts_per_invite'", (str(d["bolts_per_invite"]),))
                for i, p in enumerate(d["plans"]):
                    db.execute("INSERT INTO plans(key,name,bolts,invites,cpu,ram,disk,type,blurb,sort) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (p["key"], p["name"], p["bolts"], p.get("invites", 0), p["cpu"], p["ram"], p["disk"], p.get("type", "container"), p.get("blurb", ""), i))
        if not db.execute("SELECT 1 FROM images LIMIT 1").fetchone():
            for i, (k, l, im) in enumerate(SEED_IMAGES):
                db.execute("INSERT INTO images(key,label,lxc_image,sort) VALUES(?,?,?,?)", (k, l, im, i))
        if not db.execute("SELECT 1 FROM nodes LIMIT 1").fetchone():
            db.execute("INSERT INTO nodes(name,location,flag,host) VALUES('Node 1','India','IN','')")
        if not db.execute("SELECT 1 FROM quests LIMIT 1").fetchone():
            for q in SEED_QUESTS: db.execute("INSERT INTO quests(key,title,blurb,bolts,kind) VALUES(?,?,?,?,?)", q)

def get_settings(db):
    s = dict(DEFAULT_SETTINGS)
    for r in db.execute("SELECT k,v FROM settings"): s[r["k"]] = r["v"]
    return s

def geti(s, k):
    try: return int(float(s.get(k, DEFAULT_SETTINGS.get(k, 0))))
    except (TypeError, ValueError): return int(DEFAULT_SETTINGS.get(k, 0) or 0)

# ---------------- security ----------------
def hash_password(pw, n=2**14, r=8, p=1):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=64 * 1024 * 1024)
    return "scrypt$%d$%d$%d$%s$%s" % (n, r, p, base64.b64encode(salt).decode(), base64.b64encode(h).decode())

def verify_password(pw, stored):
    try:
        alg, n, r, p, salt, h = stored.split("$")
        if alg != "scrypt": return False
        calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False

def _fernet(secret):
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("axp-enc|" + secret).encode()).digest()))
def enc(secret, text): return _fernet(secret).encrypt(text.encode()).decode()
def dec(secret, tok):
    try: return _fernet(secret).decrypt(tok.encode()).decode()
    except Exception: return ""

def sha(t): return hashlib.sha256(t.encode()).hexdigest()
def gen_password(n=18):
    al = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(al) for _ in range(n))

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}$")
NAME_RE = re.compile(r"^[A-Za-z0-9 ._\-]{2,32}$")
CONT_RE = re.compile(r"^axp-[0-9]{1,9}-[a-f0-9]{6}$")
IMG_RE = re.compile(r"^(ubuntu|images|local):[A-Za-z0-9._/\-:]{1,60}$")
REMOTE_RE = re.compile(r"^[A-Za-z0-9_\-]{0,32}$")
LISTEN_RE = re.compile(r"^(0\.0\.0\.0|\[::\]|\[[0-9A-Fa-f:]{2,39}\]|(\d{1,3}\.){3}\d{1,3})$")   # IPv4, [::] (dual-stack) or [ipv6]

class RateLimiter:
    def __init__(self): self.h = {}
    def hit(self, key, limit, window):
        t = time.time(); arr = [x for x in self.h.get(key, []) if t - x < window]
        if len(arr) >= limit:
            self.h[key] = arr; return False
        arr.append(t); self.h[key] = arr
        if len(self.h) > 20000: self.h = {k: v for k, v in self.h.items() if v and t - v[-1] < window}
        return True

# ---------------- BOLTs ledger ----------------
class Insufficient(Exception): pass

def add_bolts(db, uid, delta, reason, ref="", earned=False):
    """Must run inside tx(). Atomic balance change + ledger row."""
    row = db.execute("SELECT bolts FROM users WHERE id=?", (uid,)).fetchone()
    if not row: raise ValueError("no such user")
    nb = row["bolts"] + int(delta)
    if nb < 0: raise Insufficient("Not enough BOLTs")
    db.execute("UPDATE users SET bolts=?, lifetime_earned=lifetime_earned+? WHERE id=?", (nb, delta if earned and delta > 0 else 0, uid))
    db.execute("INSERT INTO ledger(user_id,delta,balance,reason,ref,created) VALUES(?,?,?,?,?,?)", (uid, int(delta), nb, reason, ref, now()))
    return nb

def audit(db, actor, action, target="", detail="", ip=""):
    db.execute("INSERT INTO audit(actor,action,target,detail,ip,created) VALUES(?,?,?,?,?,?)", (actor, action, str(target), str(detail)[:500], ip, now()))

def alloc_ssh_port(db, cfg):
    used = {r[0] for r in db.execute("SELECT ssh_port FROM servers WHERE ssh_port IS NOT NULL")}
    for p in range(cfg["ssh_port_start"], cfg["ssh_port_end"] + 1):
        if p not in used: return p
    raise RuntimeError("No free SSH ports")

def create_server_row(db, cfg, user, plan, node, image, name):
    """Debit + insert server + queue deploy job. Run inside tx()."""
    s = get_settings(db)
    cname = "axp-%d-%s" % (user["id"], secrets.token_hex(3))
    t = now()
    pw = gen_password()
    add_bolts(db, user["id"], -plan["bolts"], "Deploy " + plan["name"], cname)
    cur = db.execute("""INSERT INTO servers(user_id,plan_key,plan_name,node_id,name,container,image_key,lxc_image,type,cpu,ram,disk,price,status,ssh_port,root_pw_enc,created_at,expires_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'provisioning',?,?,?,?)""",
        (user["id"], plan["key"], plan["name"], node["id"], name, cname, image["key"], image["lxc_image"], plan["type"], plan["cpu"], plan["ram"], plan["disk"],
         plan["bolts"], alloc_ssh_port(db, cfg), enc(cfg["secret_key"], pw), t, t + geti(s, "billing_days") * 86400))
    sid = cur.lastrowid
    db.execute("INSERT INTO jobs(server_id,action,created,updated) VALUES(?,'deploy',?,?)", (sid, t, t))
    return sid

GB = 10 ** 9      # LXD 'size=NGB' limits are decimal gigabytes (what the worker applies)
def disk_info(s):
    """Storage of ONE server: allocated limit (set at creation) vs usage measured by LXD for that instance.
    Never uses host disk numbers. used/free/pct are None when LXD could not report usage."""
    total = int(s["disk"] or 0) * GB; used = s["disk_used"]
    d = dict(total_bytes=total, total_gb=int(s["disk"] or 0), used_bytes=None, free_bytes=None, pct=None, available=False, checked=s["disk_checked"])
    if used is not None and total > 0:
        used = max(0, int(used)); d.update(used_bytes=used, free_bytes=max(0, total - used), pct=min(100.0, round(used * 100.0 / total, 1)), available=True)
    return d
