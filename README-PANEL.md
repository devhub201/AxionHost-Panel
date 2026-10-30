<div align="center">

# ⚡ AxionHost Panel
### Premium black & white VPS panel — paid in **BOLTs**
*Web dashboard • Admin panel • Discord bot • LXD worker — all original code*

</div>

---

## ✨ What you get

| | |
|---|---|
| 🌐 **Web panel** | Landing page, sign-in (Discord / Google / email), dashboard, 4-step deploy wizard, server page, Earn BOLTs, Billing, Account settings, 404 + maintenance pages. Mobile-first. |
| 🛠️ **Admin panel** | Users (BOLTs, ban, roles), servers (suspend, extend, delete), **editable plans**, nodes, OS images, promo codes, quests, site settings, jobs, ledger, audit log. |
| 🤖 **Discord bot** | `/panel /balance /daily /servers /deploy /plans /redeem /quests /invites` + staff `/admin …`. Persistent buttons, DM alerts, invite + booster rewards. |
| ⚙️ **Worker** | Creates/starts/stops/reinstalls/deletes LXD containers (or VMs), SSH port-forward, auto-renew, expiry suspend, miner scan, auto-refund if a deploy fails. |

## 💰 BOLTs economy
- Daily reward, quests, promo codes, **200 BOLTs per invite** (must stay N days), server-booster bonus — all editable in **Admin → Site settings**.
- Every change is written to a **ledger** (who, why, balance after).
- Plans seeded from your list (11 plans). Edit price/specs/stock any time in **Admin → Plans**.

## 🚀 Setup (≈10 min)

**1. Discord Developer Portal** → your application
- **OAuth2** → copy *Client ID* + *Client Secret* → add Redirect: `https://panel.yourdomain.com/auth/discord/callback`
- **Bot** → copy token → enable **Server Members Intent**
- Invite the bot with scopes `bot applications.commands` and permissions **Manage Server** (to see invites) + Send Messages + Embed Links.

**2. Upload & unpack** (on your node, as root)
```bash
mkdir -p /opt/axionhost-panel && cd /opt/axionhost-panel
# put panel_setup.py here, then:
python3 panel_setup.py        # recreates every file + folder
```

**3. Install**
```bash
sudo ./install-panel.sh
```
It asks for URL, Discord keys, bot token, server ID, **your Discord ID**, node public IP — then starts 3 services:
`axionhost-web` • `axionhost-worker` • `axionhost-panelbot`

**4. HTTPS (Caddy)**
```bash
apt install -y caddy
echo 'panel.yourdomain.com { reverse_proxy 127.0.0.1:8080 }' > /etc/caddy/Caddyfile
systemctl reload caddy
```
> Keep the web service on `127.0.0.1` (the installer does) — the panel trusts the proxy's `X-Forwarded-For`.

**5. Open the panel → *Continue with Discord*.** Your ID = **owner**. Go to `/admin`.

**6. In Discord:** `/admin setlog #staff-logs` (deploy failures + miner alerts) and `/admin postpanel #panel`.

## 🎮 Discord commands
**Everyone:** `/panel` `/balance` `/daily` `/servers` `/deploy` `/plans` `/redeem <code>` `/quests` `/invites`
**Staff (by panel role):** `/admin stats|lookup|bolts|ban|unban|promo|plan|server|maintenance|announce|setlog|postpanel`

## 🔒 Security
scrypt passwords • httpOnly + SameSite cookies • CSRF token + Origin checks • rate limits • CSP • encrypted root passwords • no shell (only fixed, validated `lxc` commands) • audit log for every sensitive action • miners → auto-suspend. A mining/admin suspension is **never** lifted by renewing.

## 🧪 Honest status
Tested here with mocks: **98 web/worker checks + 13 bot checks pass**, pages screenshot-checked at desktop + phone width. **Not testable here:** real Discord, real LXD, real OAuth. First run on your server → deploy one small server and watch `./install-panel.sh logs`.

## 🧯 Troubleshooting
| Problem | Fix |
|---|---|
| Discord login says *invalid redirect* | Redirect URI in portal must match `base_url` exactly |
| Slash commands missing | Set `discord_guild_id`; restart `axionhost-panelbot` |
| Deploy stuck / failed | **Admin → Jobs** shows the lxc error; BOLTs are auto-refunded |
| Invites not counted | Bot needs **Manage Server**; user must have a panel account (Discord login) |
| Reset | `./install-panel.sh status` / `logs` / `restart` |

**Backup:** copy `panel.db` and `config.panel.json` regularly.

<div align="center">Made with ♥ by <b>Peter Parker</b> • AxionHost Panel v1.0.0</div>
