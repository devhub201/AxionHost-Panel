"""AxionHost Panel Bot — manage the panel from Discord (slash commands, premium embeds, persistent buttons).
Shares panel.db with the web app + worker. Invite tracking & booster rewards credit BOLTs automatically.
Run:  python panel_bot.py   (systemd: axionhost-panelbot)"""
import asyncio, logging, random, sys, time
import discord
from discord import app_commands
import core, ops
from core import now, tx, add_bolts, audit, geti, dec

log = logging.getLogger("axp.bot")
WHITE, RED, GREEN, GREY = 0xF4F4F5, 0xE5484D, 0x30A46C, 0x2B2B30
ICON = {"running": "🟢", "stopped": "⚫", "provisioning": "🟡", "deleting": "🟠", "failed": "🔴"}
RANK = {"user": 0, "support": 1, "admin": 2, "owner": 3}

def emb(title, desc="", color=WHITE, fields=(), footer=True):
    e = discord.Embed(title=title, description=desc, color=color)
    for n, v, inl in fields: e.add_field(name=n, value=v, inline=inl)
    if footer: e.set_footer(text="AxionHost • Panel v%s" % core.PANEL_VERSION)
    return e
def bad(msg): return emb("Something went wrong", msg, RED, footer=False)
def good(title, msg=""): return emb(title, msg, GREEN, footer=False)
def ts(t): return "<t:%d:R>" % t

class PanelBot(discord.Client):
    def __init__(self, cfg, db=None):
        intents = discord.Intents.default(); intents.members = True; intents.guilds = True; intents.invites = True
        super().__init__(intents=intents)
        self.cfg = cfg; self.db = db or core.connect(cfg["db_path"]); core.init_db(self.db)
        self.tree = app_commands.CommandTree(self); self.invite_cache = {}; self.started = False
        register(self)

    # ---- helpers ----
    def S(self): return core.get_settings(self.db)
    def user(self, discord_id): return self.db.execute("SELECT * FROM users WHERE discord_id=? AND banned=0", (str(discord_id),)).fetchone()
    def url(self): return self.cfg["base_url"].rstrip("/")
    def set_setting(self, k, v): self.db.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))

    async def setup_hook(self):
        self.add_view(MainPanelView(self)); self.add_view(GiveawayView(self))
        g = self.cfg.get("discord_guild_id")
        try:
            if g:
                obj = discord.Object(id=int(g)); self.tree.copy_global_to(guild=obj); await self.tree.sync(guild=obj)
            else: await self.tree.sync()
            log.info("slash commands synced")
        except discord.Forbidden:
            log.error("Cannot register slash commands: the bot is not in server %s or was invited WITHOUT the 'applications.commands' scope. Re-invite it with scopes 'bot applications.commands'. The bot keeps running (buttons, DMs, rewards still work).", g or "(global)")
        except (discord.HTTPException, ValueError) as e:
            log.error("Slash command sync failed: %s (check discord_guild_id). The bot keeps running.", e)
        self.loop.create_task(self.background())

    async def on_ready(self):
        log.info("panel bot ready as %s", self.user)
        for g in self.guilds: await self.cache_invites(g)
        self.started = True

    # ---- invites / boosts ----
    async def cache_invites(self, guild):
        try: self.invite_cache[guild.id] = {i.code: (i.uses or 0, i.inviter.id if i.inviter else None) for i in await guild.invites()}
        except (discord.Forbidden, discord.HTTPException): self.invite_cache[guild.id] = {}
    async def on_invite_create(self, inv): self.invite_cache.setdefault(inv.guild.id, {})[inv.code] = (inv.uses or 0, inv.inviter.id if inv.inviter else None)
    async def on_invite_delete(self, inv): self.invite_cache.get(inv.guild.id, {}).pop(inv.code, None)

    async def on_member_join(self, m):
        if m.bot: return
        old = self.invite_cache.get(m.guild.id, {}); await self.cache_invites(m.guild); new = self.invite_cache.get(m.guild.id, {})
        inviter = next((new[c][1] for c in new if new[c][0] > old.get(c, (0, None))[0] and new[c][1]), None)
        if inviter and inviter != m.id:
            self.db.execute("INSERT OR IGNORE INTO invite_credits(invitee_id,inviter_id,joined) VALUES(?,?,?)", (str(m.id), str(inviter), now()))

    async def on_member_update(self, before, after):
        if after.premium_since and not before.premium_since: self.pay_booster(after.id)

    def pay_booster(self, discord_id):
        u = self.user(discord_id); amt = geti(self.S(), "booster_bonus")
        if not u or u["booster_paid"] or amt <= 0: return False
        with tx(self.db):
            self.db.execute("UPDATE users SET booster_paid=1 WHERE id=?", (u["id"],))
            add_bolts(self.db, u["id"], amt, "Server booster bonus", earned=True)
            ops.notify(self.db, u["id"], "Booster bonus", "Thanks for boosting! **+%d BOLTs** added." % amt, "booster")
        return True

    def credit_invites(self, guild=None):
        s = self.S(); bpi = geti(s, "bolts_per_invite"); minage = geti(s, "invite_min_age_days") * 86400; n = 0
        for r in self.db.execute("SELECT * FROM invite_credits WHERE credited=0 AND joined<=?", (now() - minage,)).fetchall():
            if guild and not guild.get_member(int(r["invitee_id"])):
                self.db.execute("UPDATE invite_credits SET credited=2 WHERE invitee_id=?", (r["invitee_id"],)); continue    # left before maturity: void
            u = self.user(r["inviter_id"])
            if not u or bpi <= 0: continue                                                    # inviter has no panel account yet: stays pending
            with tx(self.db):
                self.db.execute("UPDATE invite_credits SET credited=1 WHERE invitee_id=?", (r["invitee_id"],))
                add_bolts(self.db, u["id"], bpi, "Invite reward", r["invitee_id"], earned=True)
                ops.notify(self.db, u["id"], "Invite reward", "Someone you invited stayed. **+%d BOLTs**." % bpi, "invite")
            n += 1
        return n

    # ---- delivery + loops ----
    async def deliver(self):
        for n in self.db.execute("SELECT * FROM notify WHERE sent=0 ORDER BY id LIMIT 20").fetchall():
            self.db.execute("UPDATE notify SET sent=1 WHERE id=?", (n["id"],))
            e = emb(n["title"], n["body"], RED if n["kind"] in ("deploy_fail", "suspended", "mining", "staff") else WHITE)
            try:
                if n["to_log"]:
                    ch = self.get_channel(int(self.S().get("log_channel_id") or 0))
                    if ch: await ch.send(embed=e)
                elif n["discord_id"]:
                    user = self.get_user(int(n["discord_id"])) or await self.fetch_user(int(n["discord_id"])); await user.send(embed=e)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException, ValueError): pass

    async def finish_giveaway_message(self, gid, won):
        g = self.db.execute("SELECT * FROM giveaways WHERE id=?", (gid,)).fetchone()
        if not g or not g["message_id"] or not g["channel_id"]: return
        try:
            ch = self.get_channel(int(g["channel_id"])) or await self.fetch_channel(int(g["channel_id"])); msg = await ch.fetch_message(int(g["message_id"]))
            n = self.db.execute("SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id=?", (gid,)).fetchone()[0]
            await msg.edit(embed=gw_embed(g, n, ["<@%s>" % u["discord_id"] for u in won]), view=None)
            if won: await ch.send("🎉 Congratulations " + ", ".join("<@%s>" % u["discord_id"] for u in won) + "! You won **%d BOLTs**." % g["prize"])
        except Exception: log.warning("could not update giveaway message", exc_info=True)

    async def end_giveaways(self):
        for g in self.db.execute("SELECT * FROM giveaways WHERE status='open' AND ends<=?", (now(),)).fetchall():
            await self.finish_giveaway_message(g["id"], draw_giveaway(self.db, g["id"]))

    async def background(self):
        await self.wait_until_ready(); tick = 0
        while not self.is_closed():
            try:
                await self.deliver(); await self.end_giveaways()
                if tick % 120 == 0:
                    for g in self.guilds:
                        self.credit_invites(g)
                        for m in g.premium_subscribers: self.pay_booster(m.id)
                if tick % 60 == 0:
                    c = self.db.execute("SELECT COUNT(*) FROM servers WHERE deleted=0").fetchone()[0]
                    await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="%d servers • AxionHost" % c))
            except Exception: log.exception("background tick failed")
            tick += 1; await asyncio.sleep(5)

# ---------------- UI ----------------
async def need_account(bot, it):
    v = discord.ui.View(); v.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="Sign in with Discord", url=bot.url() + "/login"))
    await it.response.send_message(embed=emb("No panel account yet", "Sign in to the panel **with Discord** once, then come back — your BOLTs, servers and rewards all link automatically."), view=v, ephemeral=True)
def disk_text(r):
    d = core.disk_info(r)
    return "%.1f / %d GB (%s%%)" % (d["used_bytes"] / core.GB, d["total_gb"], d["pct"]) if d["available"] else "%d GB (usage unavailable)" % d["total_gb"]

def server_embed(bot, r):
    n = bot.db.execute("SELECT * FROM nodes WHERE id=?", (r["node_id"],)).fetchone()
    st = "🔴 suspended" if r["suspended"] else "%s %s" % (ICON.get(r["status"], "⚪"), r["status"])
    return emb("%s  •  #%d" % (r["name"], r["id"]), "%s\n`ssh root@%s -p %s`" % (st, (n and n["host"]) or "<node-ip>", r["ssh_port"]),
               fields=[("Plan", r["plan_name"], True), ("Specs", "%s vCPU • %s GB • %s GB" % (r["cpu"], r["ram"], r["disk"]), True), ("Renews", ts(r["expires_at"]), True),
                       ("Location", (n["flag"] + " " + n["name"]) if n else "-", True), ("Disk", disk_text(r), True), ("Price", "%d BOLTs / 30d" % r["price"], True), ("Auto-renew", "on" if r["auto_renew"] else "off", True)])

class ServerView(discord.ui.View):
    def __init__(self, bot, uid, sid):
        super().__init__(timeout=300); self.bot, self.uid, self.sid = bot, uid, sid
        for label, act, style in (("Start", "start", discord.ButtonStyle.success), ("Stop", "stop", discord.ButtonStyle.secondary), ("Restart", "restart", discord.ButtonStyle.secondary)):
            b = discord.ui.Button(label=label, style=style); b.callback = self.mk(act); self.add_item(b)
        b = discord.ui.Button(label="Renew", style=discord.ButtonStyle.primary); b.callback = self.renew; self.add_item(b)
        b = discord.ui.Button(label="Credentials", style=discord.ButtonStyle.secondary); b.callback = self.creds; self.add_item(b)
        b = discord.ui.Button(label="Reset password", style=discord.ButtonStyle.secondary, row=1); b.callback = self.mk("resetpw"); self.add_item(b)
        b = discord.ui.Button(label="Snapshot", style=discord.ButtonStyle.secondary, row=1); b.callback = self.snap; self.add_item(b)
        b = discord.ui.Button(label="Pinggy SSH", style=discord.ButtonStyle.secondary, row=1); b.callback = self.tool("pinggy"); self.add_item(b)
        b = discord.ui.Button(label="SSHX", style=discord.ButtonStyle.secondary, row=1); b.callback = self.tool("sshx"); self.add_item(b)
    async def snap(self, it):
        try: ops.snapshot_create(self.bot.db, self.uid, self.sid, "", "discord")
        except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
        await it.response.send_message(embed=good("Snapshot started", "You will get a DM when it is ready."), ephemeral=True)
    def tool(self, kind):
        async def cb(it):
            try: ops.tool_action(self.bot.db, self.uid, self.sid, kind, "start", "discord")
            except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
            await it.response.send_message(embed=good(kind.upper() + " starting", "Open the panel page of this server in ~20 seconds to get the link."), ephemeral=True)
        return cb
    def mk(self, act):
        async def cb(it):
            try: ops.server_action(self.bot.db, self.uid, self.sid, act, ip="discord")
            except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
            await it.response.send_message(embed=good("Queued", "`%s` is on its way — usually a few seconds." % act), ephemeral=True)
        return cb
    async def renew(self, it):
        try: exp = ops.renew(self.bot.db, self.uid, self.sid, "discord")
        except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
        await it.response.send_message(embed=good("Renewed", "New expiry %s" % ts(exp)), ephemeral=True)
    async def creds(self, it):
        r = ops.owned(self.bot.db, self.uid, self.sid)
        if not r: return await it.response.send_message(embed=bad("Server not found"), ephemeral=True)
        pw = dec(self.bot.cfg["secret_key"], r["root_pw_enc"])
        await it.response.send_message(embed=emb("Login details", "User: `root`\nPassword: ||`%s`||\nSSH: `ssh root@<node-ip> -p %s`" % (pw, r["ssh_port"])), ephemeral=True)

class ServerSelect(discord.ui.Select):
    def __init__(self, bot, uid, rows):
        super().__init__(placeholder="Choose a server…", options=[discord.SelectOption(label=r["name"][:90], value=str(r["id"]), description="%s • %s" % (r["plan_name"], r["status"])) for r in rows[:25]])
        self.bot, self.uid = bot, uid
    async def callback(self, it):
        r = ops.owned(self.bot.db, self.uid, int(self.values[0]))
        if not r: return await it.response.send_message(embed=bad("Server not found"), ephemeral=True)
        await it.response.send_message(embed=server_embed(self.bot, r), view=ServerView(self.bot, self.uid, r["id"]), ephemeral=True)

class NameModal(discord.ui.Modal, title="Name your server"):
    name = discord.ui.TextInput(label="Server name", placeholder="my-server", min_length=2, max_length=32, default="my-server")
    def __init__(self, view): super().__init__(); self.v = view
    async def on_submit(self, it):
        v = self.v
        try: sid = ops.deploy(v.bot.db, v.bot.cfg, v.u, v.plan, v.node, v.image, str(self.name), "discord")
        except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
        await it.response.send_message(embed=good("Deploying 🚀", "**%s** (#%d) is being created. You'll get a DM when it's ready." % (self.name, sid)), ephemeral=True)

class DeployView(discord.ui.View):
    def __init__(self, bot, u):
        super().__init__(timeout=300); self.bot, self.u = bot, u; self.plan = self.node = self.image = None
        db = bot.db
        for key, ph, opts in (
            ("plan", "1 • Choose a plan", [discord.SelectOption(label="%s — %d BOLTs" % (p["name"], p["bolts"]), value=p["key"], description="%d vCPU • %d GB RAM • %d GB disk" % (p["cpu"], p["ram"], p["disk"])) for p in db.execute("SELECT * FROM plans WHERE enabled=1 ORDER BY sort,bolts LIMIT 25")]),
            ("node", "2 • Choose a location", [discord.SelectOption(label=n["name"], value=str(n["id"]), description=n["location"][:90]) for n in db.execute("SELECT * FROM nodes WHERE enabled=1 LIMIT 25")]),
            ("image", "3 • Choose an OS", [discord.SelectOption(label=i["label"], value=i["key"]) for i in db.execute("SELECT * FROM images WHERE enabled=1 ORDER BY sort LIMIT 25")])):
            s = discord.ui.Select(placeholder=ph, options=opts or [discord.SelectOption(label="(none available)", value="-")]); s.callback = self.setter(key, s); self.add_item(s)
        b = discord.ui.Button(label="Deploy", style=discord.ButtonStyle.success); b.callback = self.go; self.add_item(b)
    def setter(self, key, sel):
        async def cb(it): setattr(self, key, sel.values[0]); await it.response.defer()
        return cb
    async def go(self, it):
        if not (self.plan and self.node and self.image): return await it.response.send_message(embed=bad("Pick a plan, location and OS first."), ephemeral=True)
        await it.response.send_modal(NameModal(self))

class MainPanelView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None); self.bot = bot
        self.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="Open Dashboard", url=bot.url() + "/dashboard"))
        for label, cid, fn in (("Balance", "axp:bal", cmd_balance), ("Daily", "axp:daily", cmd_daily), ("My Servers", "axp:servers", cmd_servers), ("Deploy", "axp:deploy", cmd_deploy)):
            b = discord.ui.Button(label=label, custom_id=cid, style=discord.ButtonStyle.secondary); b.callback = (lambda f: (lambda it: f(bot, it)))(fn); self.add_item(b)

# ---------------- shared command bodies ----------------
async def cmd_balance(bot, it):
    u = bot.user(it.user.id)
    if not u: return await need_account(bot, it)
    n = bot.db.execute("SELECT COUNT(*) FROM servers WHERE user_id=? AND deleted=0", (u["id"],)).fetchone()[0]
    inv = bot.db.execute("SELECT COUNT(*) FROM invite_credits WHERE inviter_id=? AND credited=1", (str(it.user.id),)).fetchone()[0]
    pend = bot.db.execute("SELECT COUNT(*) FROM invite_credits WHERE inviter_id=? AND credited=0", (str(it.user.id),)).fetchone()[0]
    await it.response.send_message(embed=emb("⚡ %d BOLTs" % u["bolts"], "Spend them on a server, or earn more with `/daily`, quests and invites.",
        fields=[("Servers", str(n), True), ("Invites rewarded", str(inv), True), ("Invites pending", str(pend), True), ("Earned all-time", "%d BOLTs" % u["lifetime_earned"], True)]), ephemeral=True)

async def cmd_daily(bot, it):
    u = bot.user(it.user.id)
    if not u: return await need_account(bot, it)
    try: r = ops.claim_daily(bot.db, u["id"])
    except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
    await it.response.send_message(embed=good("Daily reward claimed", "**+%d BOLTs** — balance **%d**" % (r["gained"], r["bolts"])), ephemeral=True)

async def cmd_servers(bot, it):
    u = bot.user(it.user.id)
    if not u: return await need_account(bot, it)
    rows = bot.db.execute("SELECT * FROM servers WHERE user_id=? AND deleted=0 ORDER BY id DESC", (u["id"],)).fetchall()
    if not rows: return await it.response.send_message(embed=emb("No servers yet", "Use `/deploy` to launch your first one."), ephemeral=True)
    v = discord.ui.View(timeout=300); v.add_item(ServerSelect(bot, u["id"], rows))
    desc = "\n".join("%s **%s** — %s • %s" % (ICON.get(r["status"], "⚪") if not r["suspended"] else "🔴", r["name"], r["plan_name"], ts(r["expires_at"])) for r in rows)
    await it.response.send_message(embed=emb("Your servers", desc), view=v, ephemeral=True)

async def cmd_deploy(bot, it):
    u = bot.user(it.user.id)
    if not u: return await need_account(bot, it)
    await it.response.send_message(embed=emb("Deploy a server", "Balance: **%d BOLTs**. Pick a plan, location and OS, then press **Deploy**." % u["bolts"]), view=DeployView(bot, u), ephemeral=True)

def staff(bot, it, level="admin"):
    u = bot.user(it.user.id)
    return u if u and RANK[u["role"]] >= RANK[level] else None

async def deny(it): await it.response.send_message(embed=bad("You don't have permission for that."), ephemeral=True)

# ---------------- more command bodies (module level so they are testable) ----------------
def _mine(bot, u, sid): return bot.db.execute("SELECT * FROM servers WHERE id=? AND user_id=? AND deleted=0", (sid, u["id"])).fetchone()

async def cmd_profile(bot, it, member=None):
    m = member or it.user; u = bot.user(m.id)
    if not u: return await need_account(bot, it) if m.id == it.user.id else await it.response.send_message(embed=bad("That user has no panel account."), ephemeral=True)
    n = bot.db.execute("SELECT COUNT(*) FROM servers WHERE user_id=? AND deleted=0", (u["id"],)).fetchone()[0]
    inv = bot.db.execute("SELECT COUNT(*) FROM invite_credits WHERE inviter_id=? AND credited=1", (str(m.id),)).fetchone()[0]
    rank = bot.db.execute("SELECT COUNT(*)+1 FROM users WHERE banned=0 AND lifetime_earned>?", (u["lifetime_earned"],)).fetchone()[0]
    await it.response.send_message(embed=emb("%s" % u["username"], fields=[("BOLTs", str(u["bolts"]), True), ("Earned all-time", str(u["lifetime_earned"]), True), ("Rank", "#%d" % rank, True),
        ("Servers", str(n), True), ("Invites", str(inv), True), ("Joined", ts(u["created_at"]), True)]), ephemeral=True)

async def cmd_leaderboard(bot, it, by="earned"):
    col = "bolts" if by == "bolts" else "lifetime_earned"
    rows = bot.db.execute("SELECT username,%s v FROM users WHERE banned=0 AND role!='owner' ORDER BY v DESC LIMIT 10" % col).fetchall()
    medals = ["🥇", "🥈", "🥉"]
    desc = "\n".join("%s **%s** — %d BOLTs" % (medals[i] if i < 3 else "`#%d`" % (i + 1), r["username"], r["v"]) for i, r in enumerate(rows)) or "Nobody yet."
    await it.response.send_message(embed=emb("🏆 Leaderboard — " + ("richest" if col == "bolts" else "top earners"), desc), ephemeral=True)

async def cmd_status(bot, it):
    ns = bot.db.execute("SELECT * FROM nodes WHERE enabled=1 ORDER BY id").fetchall()
    lines = []
    for n in ns:
        c = bot.db.execute("SELECT COUNT(*) FROM servers WHERE node_id=? AND deleted=0", (n["id"],)).fetchone()[0]
        h = n["health"] or "online"
        lines.append("%s **%s %s** — %s • %d/%d servers" % ("🟢" if h == "online" else "🔴", n["flag"], n["name"], h, c, n["max_servers"]))
    await it.response.send_message(embed=emb("Node status", "\n".join(lines) or "No nodes."), ephemeral=True)

async def cmd_renew(bot, it, server_id):
    u = bot.user(it.user.id)
    if not u: return await need_account(bot, it)
    try: exp = ops.renew(bot.db, u["id"], server_id, "discord")
    except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
    await it.response.send_message(embed=good("Renewed", "Server #%d now runs until %s." % (server_id, "<t:%d:f>" % exp)), ephemeral=True)

async def cmd_ssh(bot, it, server_id):
    u = bot.user(it.user.id)
    if not u: return await need_account(bot, it)
    r = _mine(bot, u, server_id)
    if not r: return await it.response.send_message(embed=bad("Server not found."), ephemeral=True)
    n = bot.db.execute("SELECT * FROM nodes WHERE id=?", (r["node_id"],)).fetchone()
    pw = core.dec(bot.cfg["secret_key"], r["root_pw_enc"]) if r["root_pw_enc"] else ""
    direct = bool(n and n["direct_ssh"] and r["ssh_port"])
    cmd = "`ssh root@%s -p %s`" % (n["host"] or "<node-ip>", r["ssh_port"]) if direct else "Direct SSH is not available on this node — open **%s/dashboard** and use the in-browser terminal or the SSHX / Pinggy tools." % bot.url()
    await it.response.send_message(embed=emb("Connect to %s" % r["name"], cmd, fields=[("User", "root", True), ("Password", "||%s||" % pw if pw else "-", True)]), ephemeral=True)

async def cmd_resetpw(bot, it, server_id):
    u = bot.user(it.user.id)
    if not u: return await need_account(bot, it)
    try: ops.server_action(bot.db, u["id"], server_id, "resetpw", None, "discord")
    except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
    await it.response.send_message(embed=good("Password reset queued", "A new root password is being set. Run `/ssh server_id:%d` in ~10 seconds to see it." % server_id), ephemeral=True)

async def cmd_power(bot, it, server_id, act):
    u = bot.user(it.user.id)
    if not u: return await need_account(bot, it)
    if not _mine(bot, u, server_id): return await it.response.send_message(embed=bad("Server not found."), ephemeral=True)
    try: ops.server_action(bot.db, u["id"], server_id, act, None, "discord")
    except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
    await it.response.send_message(embed=good(act.capitalize() + " queued", "Server #%d" % server_id), ephemeral=True)

async def cmd_tool(bot, it, server_id, kind):
    u = bot.user(it.user.id)
    if not u: return await need_account(bot, it)
    try: ops.tool_action(bot.db, u["id"], server_id, kind, "start", "discord")
    except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
    await it.response.send_message(embed=good(kind.upper() + " starting", "It takes ~20 seconds. Run `/tool` again or open the manage page to see the link."), ephemeral=True)

async def cmd_help(bot, it):
    await it.response.send_message(embed=emb("AxionHost commands", fields=[
        ("Account", "`/panel` `/profile` `/balance` `/daily` `/quests` `/redeem` `/invites` `/leaderboard`", False),
        ("Servers", "`/plans` `/deploy` `/servers` `/status` `/renew` `/start` `/stop` `/restart` `/ssh` `/resetpw` `/tool`", False),
        ("Support", "`/ticket new` `/ticket list` `/ticket close`", False),
        ("Staff", "`/admin createvps` `stats` `lookup` `bolts` `giveall` `nodes` `audit` `tickets` `ticketreply` `ticketclose` `server` `ban` `unban` `maintenance` `announce` `promo` `plan` • `/giveaway start` `end`", False)]), ephemeral=True)

async def adm_giveall(bot, it, amount, reason=""):
    me = staff(bot, it, "owner")
    if not me: return await deny(it)
    try: n = ops.give_all(bot.db, me["id"], amount, reason, False, "discord")
    except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
    await it.response.send_message(embed=good("Gift sent", "**+%d BOLTs** to **%d** users." % (amount, n)), ephemeral=True)

async def adm_nodes(bot, it):
    if not staff(bot, it, "support"): return await deny(it)
    rows = bot.db.execute("SELECT * FROM nodes ORDER BY id").fetchall(); out = []
    for n in rows:
        c = bot.db.execute("SELECT COUNT(*) FROM servers WHERE node_id=? AND deleted=0", (n["id"],)).fetchone()[0]
        h = n["health"] or "online"
        out.append("%s **#%d %s** (%s) — %s%s • %d/%d%s" % ("🟢" if h == "online" else "🔴", n["id"], n["name"], n["transport"], h, " • %s vCPU" % n["cpu_total"] if n["cpu_total"] else "", c, n["max_servers"], "" if n["enabled"] else " • disabled"))
    await it.response.send_message(embed=emb("Nodes", "\n".join(out) or "No nodes."), ephemeral=True)

async def adm_audit(bot, it, count=10):
    if not staff(bot, it, "admin"): return await deny(it)
    rows = bot.db.execute("SELECT a.*,u.username FROM audit a LEFT JOIN users u ON u.id=a.actor ORDER BY a.id DESC LIMIT ?", (max(1, min(count, 25)),)).fetchall()
    await it.response.send_message(embed=emb("Audit log", "\n".join("%s **%s** `%s` %s" % (ts(r["created"]), r["username"] or r["actor"], r["action"], r["target"] or "") for r in rows) or "Empty."), ephemeral=True)

async def adm_createvps(bot, it, member, plan, node="auto", os_="", name="", free=True):
    me = staff(bot, it, "admin")
    if not me: return await deny(it)
    try:
        r = ops.admin_deploy(bot.db, bot.cfg, me["id"], member.id, getattr(member, "name", "") or getattr(member, "display_name", ""), plan.strip().lower(), node, os_.strip().lower() or None, name, free, "discord")
    except (ops.OpError, ValueError) as e: return await it.response.send_message(embed=bad(getattr(e, "msg", str(e))), ephemeral=True)
    srv = bot.db.execute("SELECT * FROM servers WHERE id=?", (r["server_id"],)).fetchone()
    nd = bot.db.execute("SELECT * FROM nodes WHERE id=?", (srv["node_id"],)).fetchone()
    pw = core.dec(bot.cfg["secret_key"], srv["root_pw_enc"]) if srv["root_pw_enc"] else ""
    fields = [("Server", "#%d • %s" % (srv["id"], srv["name"]), True), ("Plan", "%s — %s vCPU • %s GB • %s GB" % (srv["plan_name"], srv["cpu"], srv["ram"], srv["disk"]), False),
              ("Location", ((nd["flag"] + " ") if nd["flag"] else "") + nd["name"], True), ("Root password", "||%s||" % pw, True),
              ("SSH", "`ssh root@%s -p %s`" % (nd["host"] or "<node-ip>", srv["ssh_port"]) if nd["direct_ssh"] else "Use the in-browser terminal on the panel", False)]
    if r["new"]:
        fields = [("Panel login (e-mail)", "`%s`" % r["email"], False), ("Panel password", "||%s||" % r["password"], True)] + fields
    desc = ("A server was created for you by staff. It is being set up now (1-3 minutes).\n" + ("**Your panel account was created.** Sign in at %s/login — or press *Continue with Discord* — and change the password in Settings." % bot.url() if r["new"] else "Open %s/dashboard to manage it." % bot.url()))
    sent = True
    try: await member.send(embed=emb("🎉 Your AxionHost server", desc, GREEN, fields))
    except Exception: sent = False
    note = ("%s now has a server (#%d)%s." % (member.mention if hasattr(member, "mention") else "User", srv["id"], " and a new panel account" if r["new"] else "")) + ("\n📩 Login details sent by DM." if sent else "\n⚠️ Could not DM them (DMs closed). Copy the details below and send them yourself.")
    await it.response.send_message(embed=emb("Server created", note, GREEN, fields if not sent else (), footer=False), ephemeral=True)

# ---------------- tickets ----------------
async def cmd_ticket_new(bot, it, subject, message):
    u = bot.user(it.user.id)
    if not u: return await need_account(bot, it)
    try: tid = ops.ticket_open(bot.db, u["id"], subject, message, None, "discord")
    except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
    await it.response.send_message(embed=good("Ticket #%d opened" % tid, "We will reply here by DM. You can also follow it at %s/support" % bot.url()), ephemeral=True)

async def cmd_ticket_list(bot, it):
    u = bot.user(it.user.id)
    if not u: return await need_account(bot, it)
    rows = bot.db.execute("SELECT * FROM tickets WHERE user_id=? ORDER BY updated DESC LIMIT 10", (u["id"],)).fetchall()
    await it.response.send_message(embed=emb("Your tickets", "\n".join("`#%d` **%s** — %s (%s)" % (r["id"], r["subject"], r["status"], ts(r["updated"])) for r in rows) or "No tickets."), ephemeral=True)

async def cmd_ticket_close(bot, it, ticket_id):
    u = bot.user(it.user.id)
    if not u: return await need_account(bot, it)
    try: ops.ticket_close(bot.db, u["id"], ticket_id, False, "discord")
    except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
    await it.response.send_message(embed=good("Ticket #%d closed" % ticket_id), ephemeral=True)

async def adm_tickets(bot, it):
    if not staff(bot, it, "support"): return await deny(it)
    rows = bot.db.execute("SELECT t.*,u.username FROM tickets t LEFT JOIN users u ON u.id=t.user_id WHERE t.status!='closed' ORDER BY t.updated DESC LIMIT 15").fetchall()
    await it.response.send_message(embed=emb("Open tickets", "\n".join("`#%d` **%s** — %s • %s (%s)" % (r["id"], r["subject"], r["username"], r["status"], ts(r["updated"])) for r in rows) or "No open tickets 🎉"), ephemeral=True)

async def adm_ticketreply(bot, it, ticket_id, message):
    me = staff(bot, it, "support")
    if not me: return await deny(it)
    try: ops.ticket_reply(bot.db, me["id"], ticket_id, message, True, "discord")
    except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
    await it.response.send_message(embed=good("Reply sent", "The user was notified by DM."), ephemeral=True)

async def adm_ticketclose(bot, it, ticket_id):
    me = staff(bot, it, "support")
    if not me: return await deny(it)
    try: ops.ticket_close(bot.db, me["id"], ticket_id, True, "discord")
    except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
    await it.response.send_message(embed=good("Ticket #%d closed" % ticket_id), ephemeral=True)

# ---------------- giveaways ----------------
def gw_embed(g, entries=0, winners=None):
    done = g["status"] != "open"
    fields = [("Prize", "%d BOLTs each" % g["prize"], True), ("Winners", str(g["winners"]), True), ("Entries", str(entries), True)]
    if done: return emb("🎉 " + (g["title"] or "Giveaway") + " — ended", ("Winners: " + ", ".join(winners)) if winners else "Nobody entered.", GREEN, fields)
    return emb("🎁 " + (g["title"] or "BOLTs giveaway"), "Press **Enter** to join. You need a panel account (sign in with Discord once).\nEnds %s." % ("<t:%d:R>" % g["ends"]), WHITE, fields)

async def cmd_gw_start(bot, it, prize, winners=1, hours=24.0, title=""):
    me = staff(bot, it, "admin")
    if not me: return await deny(it)
    if not 1 <= prize <= 100000 or not 1 <= winners <= 20 or not 0.05 <= hours <= 336: return await it.response.send_message(embed=bad("Prize 1-100000, winners 1-20, duration 0.05-336 hours."), ephemeral=True)
    t = now(); gid = bot.db.execute("INSERT INTO giveaways(channel_id,prize,winners,title,host_id,ends,created) VALUES(?,?,?,?,?,?,?)", (str(getattr(getattr(it, "channel", None), "id", "") or ""), prize, winners, (title or "")[:60], me["id"], int(t + hours * 3600), t)).lastrowid
    g = bot.db.execute("SELECT * FROM giveaways WHERE id=?", (gid,)).fetchone()
    await it.response.send_message(embed=gw_embed(g), view=GiveawayView(bot))
    try:
        m = await it.original_response(); bot.db.execute("UPDATE giveaways SET message_id=? WHERE id=?", (str(m.id), gid))
    except Exception: log.warning("could not read giveaway message id", exc_info=True)
    audit(bot.db, me["id"], "giveaway.start", gid, "%d x%d" % (prize, winners), "discord")

async def gw_enter(bot, it):
    g = bot.db.execute("SELECT * FROM giveaways WHERE message_id=?", (str(getattr(getattr(it, "message", None), "id", "")),)).fetchone()
    if not g or g["status"] != "open" or g["ends"] <= now(): return await it.response.send_message(embed=bad("This giveaway has ended."), ephemeral=True)
    u = bot.user(it.user.id)
    if not u: return await need_account(bot, it)
    if u["banned"]: return await it.response.send_message(embed=bad("Your account is banned."), ephemeral=True)
    cur = bot.db.execute("INSERT OR IGNORE INTO giveaway_entries(giveaway_id,discord_id) VALUES(?,?)", (g["id"], str(it.user.id)))
    n = bot.db.execute("SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id=?", (g["id"],)).fetchone()[0]
    await it.response.send_message(embed=good("You're in!" if cur.rowcount else "Already entered", "%d entries so far. Good luck!" % n), ephemeral=True)

def draw_giveaway(db, gid):
    """Pick winners among entrants that have an active panel account, pay them, mark the giveaway ended. Returns [(user_row)]."""
    g = db.execute("SELECT * FROM giveaways WHERE id=?", (gid,)).fetchone()
    if not g or g["status"] != "open": return []
    pool = [r for r in (db.execute("SELECT u.* FROM giveaway_entries e JOIN users u ON u.discord_id=e.discord_id WHERE e.giveaway_id=? AND u.banned=0", (gid,)).fetchall())]
    won = random.sample(pool, min(g["winners"], len(pool)))
    with tx(db):
        for u in won:
            add_bolts(db, u["id"], g["prize"], "Giveaway win: " + (g["title"] or "#%d" % gid), "gw:%d" % gid)
            ops.notify(db, u["id"], "🎉 You won a giveaway!", "**+%d BOLTs** were added to your balance." % g["prize"], "giveaway")
        db.execute("UPDATE giveaways SET status='ended',result=? WHERE id=?", (",".join(str(u["id"]) for u in won), gid))
    return won

async def adm_gw_end(bot, it, giveaway_id):
    me = staff(bot, it, "admin")
    if not me: return await deny(it)
    won = draw_giveaway(bot.db, giveaway_id)
    await bot.finish_giveaway_message(giveaway_id, won)
    await it.response.send_message(embed=good("Giveaway ended", "%d winner(s) paid." % len(won)), ephemeral=True)

class GiveawayView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None); self.bot = bot
        b = discord.ui.Button(label="Enter", emoji="🎁", style=discord.ButtonStyle.success, custom_id="axp:gw:enter"); b.callback = lambda it: gw_enter(bot, it); self.add_item(b)

def register(bot):
    t = bot.tree

    @t.command(name="panel", description="Open the AxionHost control panel")
    async def panel(it): await it.response.send_message(embed=emb("AxionHost Panel", "Deploy and manage VPS servers with BOLTs.\nUse the buttons below or the slash commands."), view=MainPanelView(bot), ephemeral=True)
    @t.command(name="balance", description="Your BOLTs balance and stats")
    async def balance(it): await cmd_balance(bot, it)
    @t.command(name="daily", description="Claim your daily BOLTs")
    async def daily(it): await cmd_daily(bot, it)
    @t.command(name="servers", description="Manage your servers")
    async def servers(it): await cmd_servers(bot, it)
    @t.command(name="deploy", description="Deploy a new server with BOLTs")
    async def deploy(it): await cmd_deploy(bot, it)

    @t.command(name="plans", description="See all plans and prices")
    async def plans(it):
        rows = bot.db.execute("SELECT * FROM plans WHERE enabled=1 ORDER BY sort,bolts").fetchall(); bpi = geti(bot.S(), "bolts_per_invite")
        await it.response.send_message(embed=emb("Plans", "\n".join("**%s** — %d vCPU • %d GB • %d GB — **%d BOLTs** (%d invites)%s" % (p["name"], p["cpu"], p["ram"], p["disk"], p["bolts"], p["bolts"] // max(bpi, 1), " • KVM" if p["type"] == "vm" else "") for p in rows)), ephemeral=True)

    @t.command(name="redeem", description="Redeem a promo code (Discord must be linked)")
    @app_commands.describe(code="Promo code")
    async def redeem(it, code: str):
        u = bot.user(it.user.id)
        if not u: return await need_account(bot, it)
        try: r = ops.redeem_promo(bot.db, u, code)
        except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
        await it.response.send_message(embed=good("Code redeemed", "**+%d BOLTs** — balance **%d**" % (r["gained"], r["bolts"])), ephemeral=True)

    @t.command(name="quests", description="See and claim quest rewards")
    async def quests(it):
        u = bot.user(it.user.id)
        if not u: return await need_account(bot, it)
        out = []
        for q in ops.list_quests(bot.db, u):
            if q["ready"]:
                try: ops.claim_quest(bot.db, u, q["key"]); q["claimed"] = True; out.append("✅ **%s** +%d (just claimed!)" % (q["title"], q["bolts"])); continue
                except ops.OpError: pass
            out.append(("✅" if q["claimed"] else "⬜") + " **%s** +%d — %s" % (q["title"], q["bolts"], q["blurb"]))
        await it.response.send_message(embed=emb("Quests", "\n".join(out)), ephemeral=True)

    @t.command(name="invites", description="Your invite progress")
    async def invites(it):
        rows = bot.db.execute("SELECT credited,COUNT(*) c FROM invite_credits WHERE inviter_id=? GROUP BY credited", (str(it.user.id),)).fetchall(); d = {r["credited"]: r["c"] for r in rows}
        s = bot.S()
        await it.response.send_message(embed=emb("Invites", "Each invite that stays **%d days** pays **%d BOLTs**." % (geti(s, "invite_min_age_days"), geti(s, "bolts_per_invite")),
            fields=[("Rewarded", str(d.get(1, 0)), True), ("Pending", str(d.get(0, 0)), True), ("Left early", str(d.get(2, 0)), True)]), ephemeral=True)

    @t.command(name="profile", description="Your (or someone's) profile and rank")
    async def profile(it, member: discord.User = None): await cmd_profile(bot, it, member)
    @t.command(name="leaderboard", description="Top earners")
    @app_commands.choices(by=[app_commands.Choice(name="Top earners (all-time)", value="earned"), app_commands.Choice(name="Richest now", value="bolts")])
    async def leaderboard(it, by: app_commands.Choice[str] = None): await cmd_leaderboard(bot, it, by.value if by else "earned")
    @t.command(name="status", description="Node status and capacity")
    async def status(it): await cmd_status(bot, it)
    @t.command(name="renew", description="Renew a server for another billing period")
    async def renew(it, server_id: int): await cmd_renew(bot, it, server_id)
    @t.command(name="ssh", description="SSH command and root password of your server")
    async def ssh(it, server_id: int): await cmd_ssh(bot, it, server_id)
    @t.command(name="resetpw", description="Set a new random root password on your server")
    async def resetpw(it, server_id: int): await cmd_resetpw(bot, it, server_id)
    @t.command(name="start", description="Start your server")
    async def start(it, server_id: int): await cmd_power(bot, it, server_id, "start")
    @t.command(name="stop", description="Stop your server")
    async def stop(it, server_id: int): await cmd_power(bot, it, server_id, "stop")
    @t.command(name="restart", description="Restart your server")
    async def restart(it, server_id: int): await cmd_power(bot, it, server_id, "restart")
    @t.command(name="tool", description="Start SSHX (web terminal) or Pinggy (public SSH) on your server")
    @app_commands.choices(kind=[app_commands.Choice(name="sshx", value="sshx"), app_commands.Choice(name="pinggy", value="pinggy")])
    async def tool(it, server_id: int, kind: app_commands.Choice[str]): await cmd_tool(bot, it, server_id, kind.value)
    @t.command(name="help", description="List all commands")
    async def help_(it): await cmd_help(bot, it)

    tk = app_commands.Group(name="ticket", description="Support tickets")
    @tk.command(name="new", description="Open a support ticket")
    async def tk_new(it, subject: str, message: str): await cmd_ticket_new(bot, it, subject, message)
    @tk.command(name="list", description="Your tickets")
    async def tk_list(it): await cmd_ticket_list(bot, it)
    @tk.command(name="close", description="Close one of your tickets")
    async def tk_close(it, ticket_id: int): await cmd_ticket_close(bot, it, ticket_id)
    t.add_command(tk)

    gw = app_commands.Group(name="giveaway", description="BOLTs giveaways (staff)")
    @gw.command(name="start", description="Start a giveaway in this channel")
    async def gw_start(it, prize: int, winners: int = 1, hours: float = 24.0, title: str = ""): await cmd_gw_start(bot, it, prize, winners, hours, title)
    @gw.command(name="end", description="End a giveaway now and draw winners")
    async def gw_end(it, giveaway_id: int): await adm_gw_end(bot, it, giveaway_id)
    t.add_command(gw)

    # ---- staff ----
    adm = app_commands.Group(name="admin", description="AxionHost staff tools")

    @adm.command(name="stats", description="Panel statistics")
    async def a_stats(it):
        if not staff(bot, it, "support"): return await deny(it)
        c = lambda q: bot.db.execute(q).fetchone()[0]
        await it.response.send_message(embed=emb("Panel stats", fields=[("Users", str(c("SELECT COUNT(*) FROM users")), True), ("Servers", "%d (%d running)" % (c("SELECT COUNT(*) FROM servers WHERE deleted=0"), c("SELECT COUNT(*) FROM servers WHERE deleted=0 AND status='running'")), True),
            ("Suspended", str(c("SELECT COUNT(*) FROM servers WHERE deleted=0 AND suspended=1")), True), ("BOLTs in circulation", str(c("SELECT COALESCE(SUM(bolts),0) FROM users")), True),
            ("Queued jobs", str(c("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')")), True), ("Failed jobs", str(c("SELECT COUNT(*) FROM jobs WHERE status='failed'")), True)]), ephemeral=True)

    @adm.command(name="lookup", description="Look up a user")
    async def a_lookup(it, user: discord.User):
        if not staff(bot, it, "support"): return await deny(it)
        u = bot.user(user.id) or bot.db.execute("SELECT * FROM users WHERE discord_id=?", (str(user.id),)).fetchone()
        if not u: return await it.response.send_message(embed=bad("No panel account for that user."), ephemeral=True)
        srv = bot.db.execute("SELECT * FROM servers WHERE user_id=? AND deleted=0", (u["id"],)).fetchall()
        await it.response.send_message(embed=emb("%s (#%d)" % (u["username"], u["id"]), fields=[("BOLTs", str(u["bolts"]), True), ("Role", u["role"], True), ("Banned", "yes: " + u["ban_reason"] if u["banned"] else "no", True),
            ("Servers", "\n".join("#%d %s — %s" % (s["id"], s["name"], s["status"]) for s in srv) or "none", False)]), ephemeral=True)

    @adm.command(name="bolts", description="Add or remove BOLTs (negative to remove)")
    async def a_bolts(it, user: discord.User, amount: int, reason: str = "adjustment"):
        me = staff(bot, it, "admin")
        if not me: return await deny(it)
        t_ = bot.db.execute("SELECT * FROM users WHERE discord_id=?", (str(user.id),)).fetchone()
        if not t_: return await it.response.send_message(embed=bad("No panel account for that user."), ephemeral=True)
        try: nb = ops.adjust_bolts(bot.db, me["id"], t_["id"], amount, reason, "discord")
        except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
        await it.response.send_message(embed=good("Done", "%s now has **%d BOLTs** (%+d)." % (user.mention, nb, amount)), ephemeral=True)

    @adm.command(name="ban", description="Ban a user from the panel")
    async def a_ban(it, user: discord.User, reason: str = "policy"):
        me = staff(bot, it, "admin"); t_ = bot.db.execute("SELECT * FROM users WHERE discord_id=?", (str(user.id),)).fetchone()
        if not me: return await deny(it)
        if not t_ or RANK[t_["role"]] >= RANK[me["role"]]: return await it.response.send_message(embed=bad("Can't ban that user."), ephemeral=True)
        ops.set_ban(bot.db, me["id"], t_["id"], True, reason, "discord"); await it.response.send_message(embed=good("Banned", user.mention), ephemeral=True)

    @adm.command(name="unban", description="Unban a user")
    async def a_unban(it, user: discord.User):
        me = staff(bot, it, "admin"); t_ = bot.db.execute("SELECT * FROM users WHERE discord_id=?", (str(user.id),)).fetchone()
        if not me: return await deny(it)
        if not t_: return await it.response.send_message(embed=bad("No panel account."), ephemeral=True)
        ops.set_ban(bot.db, me["id"], t_["id"], False, "", "discord"); await it.response.send_message(embed=good("Unbanned", user.mention), ephemeral=True)

    @adm.command(name="promo", description="Create a promo code")
    @app_commands.describe(code="Code", bolts="BOLTs per redeem", uses="Max uses", days="Valid for days (0 = forever)")
    async def a_promo(it, code: str, bolts: int, uses: int = 1, days: int = 0):
        me = staff(bot, it, "admin")
        if not me: return await deny(it)
        code = "".join(ch for ch in code.upper() if ch.isalnum() or ch in "_-")[:30]
        if not code or bolts <= 0 or uses <= 0 or days < 0: return await it.response.send_message(embed=bad("Invalid values."), ephemeral=True)
        try:
            with tx(bot.db):
                bot.db.execute("INSERT INTO promos(code,bolts,max_uses,expires,created_by,created) VALUES(?,?,?,?,?,?)", (code, bolts, uses, now() + days * 86400 if days else 0, me["id"], now())); audit(bot.db, me["id"], "promos.create", code, "discord", "discord")
        except Exception: return await it.response.send_message(embed=bad("That code already exists."), ephemeral=True)
        await it.response.send_message(embed=good("Promo created", "`%s` → **%d BOLTs**, %d use(s)%s" % (code, bolts, uses, ", expires in %dd" % days if days else "")), ephemeral=True)

    @adm.command(name="plan", description="Edit a plan: price, stock, enable/disable")
    @app_commands.describe(key="Plan key e.g. nano", bolts="New price", stock="Stock (-1 = unlimited)", enabled="Show in shop")
    async def a_plan(it, key: str, bolts: int = 0, stock: int = -2, enabled: bool = None):
        me = staff(bot, it, "admin")
        if not me: return await deny(it)
        p = bot.db.execute("SELECT * FROM plans WHERE key=?", (key.lower(),)).fetchone()
        if not p: return await it.response.send_message(embed=bad("Unknown plan key."), ephemeral=True)
        with tx(bot.db):
            if bolts > 0: bot.db.execute("UPDATE plans SET bolts=? WHERE key=?", (bolts, p["key"]))
            if stock >= -1: bot.db.execute("UPDATE plans SET stock=? WHERE key=?", (stock, p["key"]))
            if enabled is not None: bot.db.execute("UPDATE plans SET enabled=? WHERE key=?", (1 if enabled else 0, p["key"]))
            audit(bot.db, me["id"], "plans.edit", p["key"], "discord", "discord")
        await it.response.send_message(embed=good("Plan updated", "`%s`" % p["key"]), ephemeral=True)

    @adm.command(name="server", description="Server action: suspend / unsuspend / extend / delete / start / stop / restart")
    @app_commands.choices(action=[app_commands.Choice(name=a, value=a) for a in ("suspend", "unsuspend", "extend", "delete", "start", "stop", "restart")])
    async def a_server(it, server_id: int, action: app_commands.Choice[str], days: int = 0, reason: str = ""):
        me = staff(bot, it, "admin")
        if not me: return await deny(it)
        try: ops.admin_server(bot.db, me["id"], server_id, action.value, reason, days, "discord")
        except ops.OpError as e: return await it.response.send_message(embed=bad(e.msg), ephemeral=True)
        await it.response.send_message(embed=good("Done", "`%s` on server #%d." % (action.value, server_id)), ephemeral=True)

    @adm.command(name="maintenance", description="Turn site-wide maintenance on/off")
    async def a_maint(it, on: bool):
        me = staff(bot, it, "admin")
        if not me: return await deny(it)
        bot.set_setting("maintenance", "1" if on else "0"); audit(bot.db, me["id"], "settings.edit", "maintenance", str(on), "discord")
        await it.response.send_message(embed=good("Maintenance " + ("ON" if on else "OFF")), ephemeral=True)

    @adm.command(name="announce", description="Set the panel announcement banner (empty to clear)")
    async def a_announce(it, text: str = ""):
        me = staff(bot, it, "admin")
        if not me: return await deny(it)
        bot.set_setting("announcement", text[:300]); await it.response.send_message(embed=good("Announcement updated"), ephemeral=True)

    @adm.command(name="setlog", description="Where staff alerts (deploy fails, miners) are posted")
    async def a_setlog(it, channel: discord.TextChannel):
        me = staff(bot, it, "owner")
        if not me: return await deny(it)
        bot.set_setting("log_channel_id", channel.id); await it.response.send_message(embed=good("Log channel set", channel.mention), ephemeral=True)

    @adm.command(name="postpanel", description="Post the permanent panel message in a channel")
    async def a_post(it, channel: discord.TextChannel):
        me = staff(bot, it, "admin")
        if not me: return await deny(it)
        await channel.send(embed=emb("AxionHost Panel", "Deploy and manage VPS servers with **BOLTs**.\nEarn BOLTs with daily rewards, quests, invites and boosts."), view=MainPanelView(bot))
        await it.response.send_message(embed=good("Posted", channel.mention), ephemeral=True)

    @adm.command(name="giveall", description="Give BOLTs to every user (owner)")
    async def a_giveall(it, amount: int, reason: str = "gift"): await adm_giveall(bot, it, amount, reason)
    @adm.command(name="nodes", description="Node list and health")
    async def a_nodes(it): await adm_nodes(bot, it)
    @adm.command(name="audit", description="Recent audit log entries")
    async def a_audit(it, count: int = 10): await adm_audit(bot, it, count)

    @adm.command(name="createvps", description="Create a server for a Discord user (makes their panel account + DMs login if needed)")
    @app_commands.describe(member="Discord user", plan="Plan key, e.g. nano", node="Node id or 'auto'", os="OS image key (default first)", name="Server name", free="Staff pays the BOLTs (default yes)")
    async def a_createvps(it, member: discord.User, plan: str, node: str = "auto", os: str = "", name: str = "", free: bool = True): await adm_createvps(bot, it, member, plan, node, os, name, free)

    @adm.command(name="tickets", description="Open support tickets")
    async def a_tickets(it): await adm_tickets(bot, it)
    @adm.command(name="ticketreply", description="Reply to a ticket (DMs the user)")
    async def a_tkreply(it, ticket_id: int, message: str): await adm_ticketreply(bot, it, ticket_id, message)
    @adm.command(name="ticketclose", description="Close a ticket")
    async def a_tkclose(it, ticket_id: int): await adm_ticketclose(bot, it, ticket_id)

    t.add_command(adm)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = core.load_config()
    if not cfg["discord_bot_token"]: raise SystemExit("Set discord_bot_token in config.panel.json")
    try:
        PanelBot(cfg).run(cfg["discord_bot_token"], log_handler=None)
    except discord.LoginFailure:
        print("FATAL: Discord rejected the bot token. Copy it again from Developer Portal -> Bot -> Reset Token, then: ./install-panel.sh reconfigure-bot", file=sys.stderr); sys.exit(78)
    except discord.PrivilegedIntentsRequired:
        print("FATAL: enable 'Server Members Intent' in Developer Portal -> Bot -> Privileged Gateway Intents, then restart the bot.", file=sys.stderr); sys.exit(78)
