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
- **OAuth2** → copy *Client ID* + *Client Secret* → Redirect: `https://panel.yourdomain.com/auth/discord/callback`
- **Bot** → copy token → enable **Server Members Intent**
- Invite the bot with `bot applications.commands` + **Manage Server**, Send Messages, Embed Links.

**2. Get the files & run the installer** (root, on your node)
```bash
apt update && apt install -y git
git clone https://github.com/devhub201/AxionHost-Panel.git /opt/axionhost-panel
cd /opt/axionhost-panel && chmod +x install-panel.sh && sudo ./install-panel.sh
```
The installer detects IPv4 / IPv6, asks how people reach the panel, installs everything, starts 3 services and prints a summary.

## 🌐 How it runs — IP, ports, IPv6-only

The panel is **not** exposed directly. The web service listens on **`127.0.0.1:8080`** (internal). A front door sends traffic to it:

| Mode | Needs public IPv4? | Ports to open |
|---|---|---|
| ☁️ **Cloudflare Tunnel** (recommended) | **No** — works IPv6-only / behind NAT | **none** for the web panel |
| 🔒 **Caddy auto-HTTPS** | No (an AAAA record is enough) | **80, 443** |
| 💻 **Local only** | — | none |

**Ports you open on the node (firewall):**
- `20000–29999/tcp` → customer VPS SSH (1 port per VPS; the installer asks the range, ufw is handled for you). Only open what you need, e.g. `20000–20200`.
- `22/tcp` → your own admin SSH.
- `80, 443/tcp` → only in Caddy mode.
- Nothing else. Discord bot & worker only make *outgoing* connections.

**Cloudflare Tunnel setup** (the installer does the server side): Zero Trust → Networks → Tunnels → *Create tunnel* → Cloudflared → copy the token → paste in the installer. Then in the tunnel add **Public Hostname**: `panel.yourdomain.com` → `HTTP` → `localhost:8080`.

**IPv6-only node:** supported. VPS SSH ports listen on `[::]` (IPv6 + IPv4). Users SSH with `ssh root@<your-ipv6-or-hostname> -p <port>` — they need IPv6 (or put a hostname with an AAAA record). GitHub has no IPv6, so on IPv6-only servers the installer offers free NAT64 DNS so `git`/`pip` work.
**Extra nodes over IPv6:** `lxc remote add node2 https://[2001:db8::2]:8443` → Admin → Nodes → LXC remote = `node2`, listen = `[::]`.

## 🧰 Installer commands
`./install-panel.sh` → install • `update` (git pull + restart) • `status` • `doctor` (checks IPv4/IPv6, LXD, services, URL) • `logs` • `restart` • `cloudflare` (change tunnel token) • `show` • `uninstall`

**3. First login:** open your panel URL → *Continue with Discord* → your ID = **owner** → `/admin`.
**4. In Discord:** `/admin setlog #staff-logs` and `/admin postpanel #panel`.

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
| Anything odd | `./install-panel.sh doctor` |

**Backup:** copy `panel.db` and `config.panel.json` regularly.

<div align="center">Made with ♥ by <b>Peter Parker</b> • AxionHost Panel v1.0.0</div>
