import asyncio, os, sys, tempfile, types
from unittest.mock import MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
class Any_(MagicMock): pass
d = types.ModuleType("discord"); ui = types.ModuleType("discord.ui"); ac = types.ModuleType("discord.app_commands")
class Client:
    def __init__(s, **k): pass
class Base:
    def __init__(s, *a, **k): s.children = []
    def add_item(s, i): s.children.append(i)
    def __init_subclass__(c, **k): pass
for n in ("View", "Modal", "Select", "Button", "TextInput"): setattr(ui, n, type(n, (Base,), {}))
d.Client, d.ui, d.app_commands = Client, ui, ac
for n in ("Intents", "Embed", "SelectOption", "Object", "Activity", "ButtonStyle", "ActivityType", "User", "TextChannel", "Forbidden", "NotFound", "HTTPException"):
    setattr(d, n, MagicMock() if n not in ("Forbidden", "NotFound", "HTTPException") else type(n, (Exception,), {}))
class Emb:
    def __init__(s, **k): s.__dict__.update(k); s.fields = []
    def add_field(s, **k): s.fields.append(k)
    def set_footer(s, **k): pass
d.Embed = Emb
class Tree:
    def __init__(s, c): pass
    def command(s, **k): return lambda f: f
    def add_command(s, c): pass
class Group:
    def __init__(s, **k): pass
    def command(s, **k): return lambda f: f
ac.CommandTree, ac.Group, ac.describe, ac.choices, ac.Choice = Tree, Group, (lambda **k: (lambda f: f)), (lambda **k: (lambda f: f)), MagicMock()
ac.Choice.__class_getitem__ = lambda *a: MagicMock()
sys.modules.update({"discord": d, "discord.ui": ui, "discord.app_commands": ac}); d.app_commands = ac
import core, ops, panel_bot, time
P = F = 0
def ck(n, c, x=""):
    global P, F
    if c: P += 1
    else: F += 1; print("FAIL", n, x)
dd = tempfile.mkdtemp(); cfg = core.load_config(os.path.join(dd, "n.json")); cfg.update(db_path=os.path.join(dd, "p.db"))
bot = panel_bot.PanelBot(cfg); db = bot.db
def mkuser(name, did=None):
    return db.execute("INSERT INTO users(username,discord_id,created_at) VALUES(?,?,?)", (name, did, int(time.time()))).lastrowid
inviter = mkuser("inv", "100"); anon = 0
db.execute("INSERT INTO invite_credits(invitee_id,inviter_id,joined) VALUES('201','100',?)", (int(time.time()),))             # fresh
db.execute("INSERT INTO invite_credits(invitee_id,inviter_id,joined) VALUES('202','100',?)", (int(time.time()) - 5 * 86400,))   # mature
db.execute("INSERT INTO invite_credits(invitee_id,inviter_id,joined) VALUES('203','100',?)", (int(time.time()) - 5 * 86400,))   # mature but left
db.execute("INSERT INTO invite_credits(invitee_id,inviter_id,joined) VALUES('204','555',?)", (int(time.time()) - 5 * 86400,))   # inviter has no account
class G:
    def get_member(s, i): return None if i == 203 else object()
n = bot.credit_invites(G()); ck("one invite credited", n == 1, n)
ck("bolts 200", db.execute("SELECT bolts FROM users WHERE id=?", (inviter,)).fetchone()[0] == 200)
ck("fresh stays pending", db.execute("SELECT credited FROM invite_credits WHERE invitee_id='201'").fetchone()[0] == 0)
ck("left voided", db.execute("SELECT credited FROM invite_credits WHERE invitee_id='203'").fetchone()[0] == 2)
ck("no-account pending", db.execute("SELECT credited FROM invite_credits WHERE invitee_id='204'").fetchone()[0] == 0)
ck("no double credit", bot.credit_invites(G()) == 0)
ck("booster paid", bot.pay_booster(100) is True and db.execute("SELECT bolts FROM users WHERE id=?", (inviter,)).fetchone()[0] == 700)
ck("booster once", bot.pay_booster(100) is False)
ck("booster unknown user", bot.pay_booster(9) is False)
ck("notify queued", db.execute("SELECT COUNT(*) FROM notify WHERE kind IN ('invite','booster')").fetchone()[0] == 2)
# deliver
sent = []
class U:
    async def send(s, embed=None): sent.append(embed.title)
bot.get_user = lambda i: U(); bot.get_channel = lambda i: None
ops.notify(db, inviter, "Hello", "body")
asyncio.run(bot.deliver()); ck("dm delivered", sent.count("Hello") == 1 and len(sent) == 3, sent)
asyncio.run(bot.deliver()); ck("not redelivered", len(sent) == 3)
ck("ops usable from bot", ops.claim_daily(db, inviter)["gained"] == 50)
# ---- new slash command bodies ----
class Resp:
    def __init__(s): s.out = []
    async def send_message(s, embed=None, view=None, ephemeral=False): s.out.append(embed)
class Mem:
    def __init__(s, i): s.id = i
class It:
    def __init__(s, i): s.user = Mem(i); s.response = Resp()
    def last(s): e = s.out()[-1]; return e
def run(fn, did, *a, **k):
    it = It(did); asyncio.run(fn(bot, it, *a, **k)); return it.response.out[-1]
def txt(e): return " ".join([str(getattr(e, "title", "")), str(getattr(e, "description", ""))] + [str(f.get("name")) + str(f.get("value")) for f in e.fields])
t0 = int(time.time()); bot.cfg["secret_key"] = bot.cfg.get("secret_key") or "k" * 44
bot.cfg["base_url"] = "http://x"
own = mkuser("boss", "900"); db.execute("UPDATE users SET role='owner' WHERE id=?", (own,))
adm_ = mkuser("mod", "901"); db.execute("UPDATE users SET role='admin' WHERE id=?", (adm_,))
pl = db.execute("SELECT * FROM plans LIMIT 1").fetchone()
db.execute("UPDATE users SET bolts=1000,lifetime_earned=400 WHERE id=?", (inviter,))
sid = db.execute("INSERT INTO servers(user_id,plan_key,plan_name,node_id,name,container,image_key,lxc_image,type,cpu,ram,disk,price,status,ssh_port,root_pw_enc,created_at,expires_at) VALUES(?,?,?,1,'box','axp-1-aaaaaa','u','ubuntu:22.04','container',1,1,10,100,'running',20001,?,?,?)", (inviter, pl["key"], pl["name"], core.enc(bot.cfg["secret_key"], "Pa55word"), t0, t0 + 86400)).lastrowid
e = run(panel_bot.cmd_profile, 100); ck("profile shows rank", "Rank" in txt(e) and "#1" in txt(e), txt(e))
e = run(panel_bot.cmd_profile, 7777); ck("profile no account", "No panel account" in txt(e))
e = run(panel_bot.cmd_leaderboard, 100); ck("leaderboard lists user, hides owner", "inv" in txt(e) and "boss" not in txt(e), txt(e))
e = run(panel_bot.cmd_status, 100); ck("status lists node", "servers" in txt(e), txt(e))
e = run(panel_bot.cmd_ssh, 100, sid); ck("ssh shows password spoiler", "||Pa55word||" in txt(e) and "20001" in txt(e), txt(e))
e = run(panel_bot.cmd_ssh, 900, sid); ck("ssh other user's server hidden", "not found" in txt(e).lower() or "No panel" in txt(e), txt(e))
db.execute("UPDATE nodes SET direct_ssh=0 WHERE id=1"); e = run(panel_bot.cmd_ssh, 100, sid); ck("ssh pinggy node points to terminal", "ssh root@" not in txt(e) and "terminal" in txt(e), txt(e)); db.execute("UPDATE nodes SET direct_ssh=1 WHERE id=1")
e = run(panel_bot.cmd_resetpw, 100, sid); ck("resetpw queued", "queued" in txt(e), txt(e))
ck("resetpw job exists", db.execute("SELECT COUNT(*) FROM jobs WHERE server_id=? AND action='resetpw'", (sid,)).fetchone()[0] == 1)
db.execute("UPDATE jobs SET status='done'")
e = run(panel_bot.cmd_power, 100, sid, "restart"); ck("restart queued", "queued" in txt(e), txt(e)); db.execute("UPDATE jobs SET status='done'")
e = run(panel_bot.cmd_power, 900, sid, "stop"); ck("cannot stop someone else's", "queued" not in txt(e))
e = run(panel_bot.cmd_tool, 100, sid, "pinggy"); ck("tool start queued", "starting" in txt(e).lower(), txt(e)); db.execute("UPDATE jobs SET status='done'")
b0 = db.execute("SELECT bolts FROM users WHERE id=?", (inviter,)).fetchone()[0]
e = run(panel_bot.cmd_renew, 100, sid); ck("renew charges", "Renewed" in txt(e) and db.execute("SELECT bolts FROM users WHERE id=?", (inviter,)).fetchone()[0] == b0 - 100, txt(e))
e = run(panel_bot.cmd_help, 100); ck("help lists commands", "/resetpw" in txt(e))
e = run(panel_bot.adm_giveall, 901, 10); ck("admin cannot giveall", "permission" in txt(e))
e = run(panel_bot.adm_giveall, 900, 10, "hi"); ck("owner giveall", "Gift sent" in txt(e), txt(e))
e = run(panel_bot.adm_giveall, 900, -3); ck("giveall bad amount", "Gift sent" not in txt(e))
e = run(panel_bot.adm_nodes, 100); ck("nodes: user denied", "permission" in txt(e))
e = run(panel_bot.adm_nodes, 901); ck("nodes: admin sees", "Nodes" in txt(e))
e = run(panel_bot.adm_audit, 901, 5); ck("audit shows giveall", "users.giveall" in txt(e), txt(e))
print("PASS", P, "FAIL", F); sys.exit(1 if F else 0)
