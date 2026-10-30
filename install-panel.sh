#!/usr/bin/env bash
# AxionHost Panel installer — web + worker + Discord bot as systemd services.
# Usage: sudo ./install-panel.sh            (interactive install)
#        ./install-panel.sh status|logs|restart|show
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"; cd "$DIR"
CFG="$DIR/config.panel.json"; VENV="$DIR/venv"
c() { printf '\033[1;37m%s\033[0m\n' "$*"; }; ok() { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }; er() { printf '\033[1;31m✘ %s\033[0m\n' "$*"; }
ask() { local p="$1" d="${2:-}" v; read -r -p "$p${d:+ [$d]}: " v; echo "${v:-$d}"; }
case "${1:-install}" in
  status)  systemctl --no-pager status axionhost-web axionhost-worker axionhost-panelbot | grep -E "●|Active:"; exit 0;;
  logs)    journalctl -u axionhost-web -u axionhost-worker -u axionhost-panelbot -f -n 40; exit 0;;
  restart) systemctl restart axionhost-web axionhost-worker axionhost-panelbot && ok "restarted"; exit 0;;
  show)    python3 - <<PY
import json;d=json.load(open("$CFG"))
for k,v in d.items(): print(k.ljust(24), ("*"*8 if ("secret" in k or "token" in k) and v else v))
PY
           exit 0;;
esac
[ "$(id -u)" = 0 ] || { er "Run as root (sudo ./install-panel.sh)"; exit 1; }
c "AxionHost Panel — installer"
for f in app.py core.py ops.py worker.py panel_bot.py requirements-panel.txt; do [ -f "$f" ] || { er "Missing $f (upload all panel files first)"; exit 1; }; done
[ -d templates ] && [ -d static ] || { er "Missing templates/ or static/ folders"; exit 1; }
command -v python3 >/dev/null || { er "python3 not found"; exit 1; }
apt-get install -y -qq python3-venv python3-pip >/dev/null 2>&1 || true
[ -d "$VENV" ] || python3 -m venv "$VENV" || { er "venv failed"; exit 1; }
"$VENV/bin/pip" install -q --upgrade pip >/dev/null 2>&1
"$VENV/bin/pip" install -q -r requirements-panel.txt || { er "pip install failed"; exit 1; }; ok "dependencies installed"
if [ ! -f "$CFG" ]; then
  c "Configuration (press Enter to skip optional items)"
  BASE=$(ask "Panel URL (https://panel.yourdomain.com)" "http://localhost:8080")
  DID=$(ask "Discord application Client ID")
  DSEC=$(ask "Discord application Client Secret")
  TOK=$(ask "Discord BOT token")
  GID=$(ask "Discord server (guild) ID")
  OWN=$(ask "YOUR Discord user ID (becomes panel owner)")
  HOST=$(ask "This node's public IP (shown in SSH commands)")
  SEC=$(python3 -c "import secrets;print(secrets.token_hex(32))")
  SECURE=false; case "$BASE" in https://*) SECURE=true;; esac
  AXP_BASE="$BASE" AXP_DID="$DID" AXP_DSEC="$DSEC" AXP_TOK="$TOK" AXP_GID="$GID" AXP_OWN="$OWN" AXP_SEC="$SEC" AXP_SECURE="$SECURE" AXP_DB="$DIR/panel.db" python3 - <<'PY'
import json,os
e=os.environ
json.dump({"base_url":e["AXP_BASE"],"host":"127.0.0.1","port":8080,"cookie_secure":e["AXP_SECURE"]=="true","db_path":e["AXP_DB"],"secret_key":e["AXP_SEC"],
 "discord_client_id":e["AXP_DID"],"discord_client_secret":e["AXP_DSEC"],"discord_bot_token":e["AXP_TOK"],"discord_guild_id":e["AXP_GID"],"owner_discord_id":e["AXP_OWN"],
 "ssh_port_start":20000,"ssh_port_end":29999,"lxc_storage_pool":"default"},open("config.panel.json","w"),indent=2)
PY
  chmod 600 "$CFG"; ok "config.panel.json written (permissions 600)"
  # first-node public host
  "$VENV/bin/python" - <<PY
import core
cfg=core.load_config("$CFG"); db=core.connect(cfg["db_path"]); core.init_db(db)
h="$HOST"
if h: db.execute("UPDATE nodes SET host=? WHERE id=1",(h,))
PY
  echo; c "Add this Redirect URI in the Discord Developer Portal → OAuth2:"; echo "   ${BASE%/}/auth/discord/callback"
else ok "using existing config.panel.json"; fi
mk() { cat > "/etc/systemd/system/$1.service" <<UNIT
[Unit]
Description=$2
After=network.target
[Service]
WorkingDirectory=$DIR
Environment=AXION_PANEL_CONFIG=$CFG
ExecStart=$3
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT
}
PORT=$("$VENV/bin/python" -c "import core;print(core.load_config('$CFG')['port'])")
mk axionhost-web "AxionHost Panel (web)" "$VENV/bin/waitress-serve --listen=127.0.0.1:$PORT --threads=8 --call app:get_app"
mk axionhost-worker "AxionHost Panel worker" "$VENV/bin/python worker.py"
mk axionhost-panelbot "AxionHost Panel Discord bot" "$VENV/bin/python panel_bot.py"
systemctl daemon-reload && systemctl enable --now axionhost-web axionhost-worker axionhost-panelbot >/dev/null 2>&1
sleep 2
for s in axionhost-web axionhost-worker axionhost-panelbot; do systemctl is-active --quiet $s && ok "$s running" || er "$s failed — see: journalctl -u $s -n 30"; done
echo; c "Next: put HTTPS in front (Caddy):"; echo "   apt install -y caddy && echo 'panel.yourdomain.com { reverse_proxy 127.0.0.1:$PORT }' > /etc/caddy/Caddyfile && systemctl reload caddy"
c "Then open your panel URL and sign in with Discord — you become the owner."
# end of install-panel.sh
