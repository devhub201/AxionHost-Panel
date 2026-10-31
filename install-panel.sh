#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  AxionHost Panel — installer            Made by Peter Parker         ║
# ║  Works on IPv4, dual-stack AND IPv6-only servers (Cloudflare Tunnel) ║
# ╚══════════════════════════════════════════════════════════════════════╝
# Usage:  sudo ./install-panel.sh [install|update|status|logs|restart|doctor|cloudflare|show|uninstall]
set -u
export LC_ALL=C.UTF-8 2>/dev/null || true
BRAND="AxionHost"; SVC="axionhost"; ACC=255
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$DIR" || exit 1
CFG="$DIR/config.panel.json"; VENV="$DIR/venv"; LOG="/var/log/${SVC}-install.log"
SERVICES="${SVC}-web ${SVC}-worker ${SVC}-panelbot"
# ---------- ui ----------
if [ -t 1 ]; then A=$'\033[1;38;5;'"${ACC}"'m'; G=$'\033[1;32m'; R=$'\033[1;31m'; Y=$'\033[1;33m'; D=$'\033[2m'; B=$'\033[1m'; N=$'\033[0m'; TTY=1
else A=""; G=""; R=""; Y=""; D=""; B=""; N=""; TTY=0; fi
ok()   { printf '  %s✔%s %s\n' "$G" "$N" "$*"; }
bad()  { printf '  %s✘%s %s\n' "$R" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
info() { printf '  %s•%s %s\n' "$D" "$N" "$*"; }
head_() { printf '\n%s━━ %s%s %s━━%s\n' "$A" "$B" "$*" "$A" "$N"; }
banner() {
  [ "$TTY" = 1 ] && clear 2>/dev/null
  printf '%s\n' "$A"
  cat <<'ART'
     ▄▀█ ▀▄▀ █ █▀█ █▄ █   ╔═╗╔═╗╔╗╔╔═╗╦
     █▀█ █ █ █ █▄█ █ ▀█   ╠═╝╠═╣║║║║╣ ║
                          ╩  ╩ ╩╝╚╝╚═╝╩═╝
ART
  printf '%s     %s Panel Installer  %s•  Made by Peter Parker%s\n\n' "$N$B" "$BRAND" "$D" "$N"
}
# spinner: step "label" cmd args...   (output -> $LOG, shows last lines on failure)
step() {
  local label="$1"; shift; local t0=$SECONDS pid i=0 fr='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
  printf '\n### %s\n' "$label" >> "$LOG"
  "$@" >> "$LOG" 2>&1 & pid=$!
  if [ "$TTY" = 1 ]; then
    while kill -0 "$pid" 2>/dev/null; do printf '\r  %s%s%s %s' "$A" "${fr:i++%10:1}" "$N" "$label"; sleep 0.1; done
    printf '\r\033[K'
  else wait "$pid"; fi
  wait "$pid"; local rc=$?
  if [ $rc -eq 0 ]; then ok "$label ${D}($((SECONDS-t0))s)${N}"; else bad "$label"; printf '%s' "$D"; tail -n 12 "$LOG" | sed 's/^/      /'; printf '%s' "$N"; printf '\n  Full log: %s\n' "$LOG"; return $rc; fi
}
ask()  { local p="$1" d="${2:-}" v; read -r -p "  ${A}?${N} $p${d:+ ${D}[$d]${N}}: " v; echo "${v:-$d}"; }
asks() { local p="$1" v; read -r -s -p "  ${A}?${N} $p: " v; echo >&2; echo "$v"; }
yesno() { local a; a=$(ask "$1 (y/n)" "${2:-n}"); case "$a" in y|Y|yes) return 0;; *) return 1;; esac; }
need_root() { [ "$(id -u)" = 0 ] || { bad "Run as root:  sudo ./install-panel.sh"; exit 1; }; }
cfgget() { "$VENV/bin/python" -c "import core;print(core.load_config('$CFG').get('$1',''))" 2>/dev/null; }
# ---------- network detection ----------
HAS4=0; HAS6=0; IP4=""; IP6=""
detect_net() {
  ip -4 addr show scope global 2>/dev/null | grep -q 'inet ' && HAS4=1
  ip -6 addr show scope global 2>/dev/null | grep -q 'inet6 ' && HAS6=1
  [ "$HAS4" = 1 ] && IP4=$(curl -4 -fsS --max-time 5 https://api.ipify.org 2>/dev/null || ip -4 -o addr show scope global | awk '{print $4}' | head -1 | cut -d/ -f1)
  [ "$HAS6" = 1 ] && IP6=$(curl -6 -fsS --max-time 5 https://api64.ipify.org 2>/dev/null || ip -6 -o addr show scope global | awk '{print $4}' | head -1 | cut -d/ -f1)
  return 0
}
reach() { curl -fsS -o /dev/null --max-time 8 -I "$1" 2>/dev/null; }
ipv6_only_fixes() {
  # GitHub has no IPv6. On IPv6-only servers pip/git may fail -> offer NAT64/DNS64 resolvers.
  if [ "$HAS4" = 0 ] && ! reach https://github.com; then
    warn "IPv6-only server: github.com is not reachable (GitHub has no IPv6)."
    if yesno "Use free NAT64/DNS64 resolvers (nat64.net) so git/pip work?" y; then
      if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
        mkdir -p /etc/systemd/resolved.conf.d
        printf '[Resolve]\nDNS=2a00:1098:2c::1 2a01:4f8:c2c:123f::1 2a00:1098:2b::1\nDomains=~.\n' > /etc/systemd/resolved.conf.d/${SVC}-nat64.conf
        systemctl restart systemd-resolved && ok "NAT64 DNS enabled (systemd-resolved)"
      else cp /etc/resolv.conf /etc/resolv.conf.${SVC}.bak 2>/dev/null; printf 'nameserver 2a00:1098:2c::1\nnameserver 2a01:4f8:c2c:123f::1\n' > /etc/resolv.conf; ok "NAT64 DNS written to /etc/resolv.conf (backup: resolv.conf.${SVC}.bak)"; fi
    fi
  fi
}
# ---------- install parts ----------
install_deps() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip curl ca-certificates gnupg iproute2 || return 1
  [ -d "$VENV" ] || python3 -m venv "$VENV" || return 1
  "$VENV/bin/pip" install -q --upgrade pip && "$VENV/bin/pip" install -q -r requirements-panel.txt
}
install_cloudflared() {
  command -v cloudflared >/dev/null 2>&1 && return 0
  mkdir -p /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg || return 1
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg arch=$(dpkg --print-architecture)] https://pkg.cloudflare.com/cloudflared any main" > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -qq && apt-get install -y -qq cloudflared
}
setup_cloudflare() {
  head_ "Cloudflare Tunnel"
  info "No inbound ports, no public IPv4 needed — Cloudflare connects OUT to your server."
  step "Installing cloudflared" install_cloudflared || return 1
  printf '\n  %sIn Cloudflare dashboard → Zero Trust → Networks → Tunnels → Create tunnel → Cloudflared%s\n  Copy the token (the long text after "service install").\n\n' "$B" "$N"
  local tok; tok=$(asks "Paste tunnel token (hidden)")
  tok="${tok##* }"
  [ "${#tok}" -ge 40 ] || { bad "That doesn't look like a tunnel token."; return 1; }
  systemctl list-unit-files 2>/dev/null | grep -q '^cloudflared.service' && step "Removing old tunnel service" cloudflared service uninstall
  step "Starting tunnel service" cloudflared service install "$tok" || return 1
  sleep 3; systemctl is-active --quiet cloudflared && ok "Tunnel is connected" || { bad "cloudflared not running — see: journalctl -u cloudflared -n 30"; return 1; }
  printf '\n  %sLast step (in Cloudflare):%s tunnel → Public Hostname → Add\n    Hostname : %s%s%s\n    Service  : %sHTTP  →  localhost:%s%s\n\n' "$B" "$N" "$A" "$DOMAIN" "$N" "$A" "$PORT" "$N"
}
setup_caddy() {
  head_ "Caddy (automatic HTTPS)"
  if ! command -v caddy >/dev/null 2>&1; then
    step "Adding Caddy repository" bash -c "apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https && curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg && curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list && apt-get update -qq" || true
    step "Installing Caddy" apt-get install -y -qq caddy || return 1
  fi
  [ -f /etc/caddy/Caddyfile ] && cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.${SVC}.bak
  printf '%s {\n\treverse_proxy 127.0.0.1:%s\n}\n' "$DOMAIN" "$PORT" > /etc/caddy/Caddyfile
  step "Reloading Caddy" systemctl reload caddy || step "Restarting Caddy" systemctl restart caddy
}
open_firewall() {
  command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active" || return 0
  yesno "ufw is active — open the needed ports now?" y || return 0
  ufw allow "${SSHA}:${SSHB}/tcp" >/dev/null && ok "ufw: ${SSHA}-${SSHB}/tcp (VPS SSH)"
  [ "$MODE" = caddy ] && { ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ok "ufw: 80,443/tcp (web)"; }
  return 0
}
write_config() {
  AXP_BASE="$BASE" AXP_DID="$DID" AXP_DSEC="$DSEC" AXP_TOK="$TOK" AXP_GID="$GID" AXP_OWN="$OWN" AXP_SEC="$(python3 -c 'import secrets;print(secrets.token_hex(32))')" \
  AXP_SECURE="$SECURE" AXP_DB="$DIR/panel.db" AXP_PORT="$PORT" AXP_TP="$TP" AXP_A="$SSHA" AXP_B="$SSHB" python3 - <<'PY'
import json,os
e=os.environ
json.dump({"base_url":e["AXP_BASE"],"host":"127.0.0.1","port":int(e["AXP_PORT"]),"cookie_secure":e["AXP_SECURE"]=="true","trusted_proxy":e["AXP_TP"],"db_path":e["AXP_DB"],"secret_key":e["AXP_SEC"],
 "discord_client_id":e["AXP_DID"],"discord_client_secret":e["AXP_DSEC"],"discord_bot_token":e["AXP_TOK"],"discord_guild_id":e["AXP_GID"],"owner_discord_id":e["AXP_OWN"],
 "ssh_port_start":int(e["AXP_A"]),"ssh_port_end":int(e["AXP_B"]),"lxc_storage_pool":"default"},open("config.panel.json","w"),indent=2)
PY
  chmod 600 "$CFG"
}
seed_node() {
  "$VENV/bin/python" - <<PY
import core
cfg=core.load_config("$CFG"); db=core.connect(cfg["db_path"]); core.init_db(db)
db.execute("UPDATE nodes SET host=?, listen=? WHERE id=1",("$NODEHOST","$LISTEN"))
PY
}
mk_unit() { # name desc exec hardening
  cat > "/etc/systemd/system/$1.service" <<UNIT
[Unit]
Description=$2
After=network-online.target
Wants=network-online.target
[Service]
WorkingDirectory=$DIR
Environment=AXION_PANEL_CONFIG=$CFG
ExecStart=$3
Restart=always
RestartSec=3
$4
[Install]
WantedBy=multi-user.target
UNIT
}
install_units() {
  local port; port=$(cfgget port)
  mk_unit ${SVC}-web "$BRAND Panel (web)" "$VENV/bin/waitress-serve --listen=127.0.0.1:$port --threads=8 --call app:get_app" $'NoNewPrivileges=true\nPrivateTmp=true'
  mk_unit ${SVC}-worker "$BRAND Panel worker" "$VENV/bin/python worker.py" ""
  mk_unit ${SVC}-panelbot "$BRAND Panel Discord bot" "$VENV/bin/python panel_bot.py" $'NoNewPrivileges=true\nPrivateTmp=true'
  systemctl daemon-reload
  local s="${SVC}-web ${SVC}-worker"; [ -n "$(cfgget discord_bot_token)" ] && s="$s ${SVC}-panelbot"
  systemctl enable --now $s
}
box() { # lines...
  local w=0 l; for l in "$@"; do [ "${#l}" -gt "$w" ] && w=${#l}; done
  printf '\n  %s╔%s╗%s\n' "$A" "$(printf '═%.0s' $(seq 1 $((w+2))))" "$N"
  for l in "$@"; do printf '  %s║%s %s%*s %s║%s\n' "$A" "$N" "$l" $((w-${#l})) "" "$A" "$N"; done
  printf '  %s╚%s╝%s\n' "$A" "$(printf '═%.0s' $(seq 1 $((w+2))))" "$N"
}
# ---------- commands ----------
cmd_install() {
  need_root; mkdir -p "$(dirname "$LOG")"; : > "$LOG"; banner
  for f in app.py core.py ops.py worker.py panel_bot.py requirements-panel.txt; do [ -f "$f" ] || { bad "Missing $f — upload ALL panel files first"; exit 1; }; done
  [ -d templates ] && [ -d static ] || { bad "Missing templates/ or static/ folder"; exit 1; }
  head_ "1 · Checking your server"
  detect_net
  [ "$HAS4" = 1 ] && ok "Public IPv4: ${IP4:-yes}" || warn "No public IPv4 (that's fine — IPv6 / tunnel mode)"
  [ "$HAS6" = 1 ] && ok "Public IPv6: ${IP6:-yes}" || info "No IPv6"
  [ "$HAS4$HAS6" = "00" ] && warn "No public IP at all — Cloudflare Tunnel mode will still work (outbound only)."
  command -v lxc >/dev/null 2>&1 && ok "LXD found" || warn "LXD (lxc) not found — install it: snap install lxd && lxd init --auto"
  ipv6_only_fixes
  head_ "2 · Installing dependencies"
  step "Python, venv & packages" install_deps || exit 1
  head_ "3 · Configuration"
  if [ -f "$CFG" ] && ! yesno "config.panel.json exists — reconfigure?" n; then ok "Keeping existing config"; PORT=$(cfgget port); MODE=keep
  else
    printf '\n  %sHow will people reach your panel?%s\n    1) %sCloudflare Tunnel%s  ← recommended: works with IPv6-only / no public IPv4 / behind NAT, zero open ports\n    2) Caddy auto-HTTPS     needs ports 80+443 and a DNS record (A or AAAA) to this server\n    3) Local only           127.0.0.1, test or use your own proxy\n\n' "$B" "$N" "$A" "$N"
    case "$(ask "Choose" 1)" in 2) MODE=caddy; TP=proxy;; 3) MODE=local; TP=none;; *) MODE=tunnel; TP=cloudflare;; esac
    PORT=$(ask "Internal web port (never exposed publicly)" 8080)
    if [ "$MODE" = local ]; then DOMAIN="localhost:$PORT"; BASE="http://localhost:$PORT"; SECURE=false
    else DOMAIN=$(ask "Panel domain (e.g. panel.example.com)"); [ -n "$DOMAIN" ] || { bad "Domain required"; exit 1; }; BASE="https://$DOMAIN"; SECURE=true; fi
    printf '\n  %sDiscord (Developer Portal → your application)%s\n' "$B" "$N"
    [ "$MODE" != local ] && info "OAuth2 → Redirects → add:  $BASE/auth/discord/callback"
    DID=$(ask "Client ID"); DSEC=$(asks "Client Secret (hidden)"); TOK=$(asks "Bot token (hidden, Enter to skip bot)")
    GID=$(ask "Discord server (guild) ID"); OWN=$(ask "YOUR Discord user ID (you become owner)")
    printf '\n  %sThis node%s\n' "$B" "$N"
    if [ "$HAS6" = 1 ]; then LISTEN="[::]"; info "IPv6 present → VPS SSH ports listen on IPv6 + IPv4 (dual-stack)"; else LISTEN="0.0.0.0"; fi
    NODEHOST=$(ask "Address users SSH to (IP or hostname)" "${IP4:-$IP6}")
    SSHA=$(ask "VPS SSH port range — start" 20000); SSHB=$(ask "VPS SSH port range — end" 29999)
    case "$SSHA$SSHB" in *[!0-9]*) bad "Ports must be numbers"; exit 1;; esac
    write_config; ok "config.panel.json written (chmod 600)"; step "Preparing database" "$VENV/bin/python" -c "import core;c=core.load_config('$CFG');core.init_db(core.connect(c['db_path']))"
    seed_node && ok "Node 1 → $NODEHOST (listen $LISTEN)"
    case "$MODE" in tunnel) setup_cloudflare || warn "Tunnel not finished — run later:  ./install-panel.sh cloudflare";; caddy) setup_caddy || warn "Caddy failed — see $LOG";; esac
    open_firewall
  fi
  head_ "4 · Starting services"
  step "Creating & starting systemd services" install_units || exit 1
  sleep 2; for s in $SERVICES; do systemctl is-enabled --quiet "$s" 2>/dev/null || continue; systemctl is-active --quiet "$s" && ok "$s running" || bad "$s failed — journalctl -u $s -n 30"; done
  local base; base=$(cfgget base_url)
  box "$BRAND Panel is installed!" "" "Panel   : $base" "Login   : open it, Continue with Discord (you = owner)" "Admin   : $base/admin" "" "Web port: 127.0.0.1:${PORT:-8080} (internal only)" "VPS SSH : ${SSHA:-20000}-${SSHB:-29999}/tcp  (open these)" "Logs    : ./install-panel.sh logs" "Check   : ./install-panel.sh doctor"
  printf '\n  %sThen in Discord:%s /admin setlog #staff-logs  and  /admin postpanel #panel\n\n' "$B" "$N"
}
cmd_update() { need_root; mkdir -p "$(dirname "$LOG")"; banner
  [ -d .git ] && step "Pulling latest code" git pull --ff-only || warn "Not a git checkout — upload new files manually"
  step "Updating packages" "$VENV/bin/pip" install -q -r requirements-panel.txt
  step "Restarting services" systemctl restart $SERVICES; }
cmd_status() { for s in $SERVICES cloudflared caddy; do systemctl list-unit-files 2>/dev/null | grep -q "^$s.service" || continue; if systemctl is-active --quiet "$s"; then ok "$s"; else bad "$s (inactive)"; fi; done; }
cmd_doctor() { banner; head_ "Doctor"; detect_net; [ -f "$CFG" ] && ok "config.panel.json present" || bad "config.panel.json missing"
  [ "$HAS4" = 1 ] && ok "IPv4: ${IP4}" || info "IPv4: none"; [ "$HAS6" = 1 ] && ok "IPv6: ${IP6}" || info "IPv6: none"
  command -v lxc >/dev/null && { lxc info >/dev/null 2>&1 && ok "LXD reachable" || bad "LXD installed but not responding"; lxc storage list --format csv 2>/dev/null | head -3 | sed 's/^/      pool: /'; } || bad "lxc not found"
  cmd_status; local p; p=$(cfgget port); p=${p:-8080}
  curl -fsS --max-time 5 "http://127.0.0.1:$p/healthz" >/dev/null 2>&1 && ok "Web answers on 127.0.0.1:$p" || bad "Web not answering on 127.0.0.1:$p"
  reach https://discord.com && ok "discord.com reachable" || bad "discord.com not reachable"
  local b; b=$(cfgget base_url); case "$b" in https://*) curl -fsS --max-time 10 "$b/healthz" >/dev/null 2>&1 && ok "Public URL works: $b" || warn "Public URL not answering yet: $b (DNS / tunnel hostname?)";; esac
  ls /etc/systemd/system/${SVC}-*.service >/dev/null 2>&1 || warn "services not installed"; echo; }
cmd_show() { "$VENV/bin/python" - <<PY
import json;d=json.load(open("$CFG"))
for k,v in d.items(): print("  "+k.ljust(24), ("*"*8 if ("secret" in k or "token" in k) and v else v))
PY
}
cmd_uninstall() { need_root; yesno "Stop & remove the $BRAND services? (data in $DIR is kept)" n || exit 0
  systemctl disable --now $SERVICES 2>/dev/null; rm -f /etc/systemd/system/${SVC}-*.service; systemctl daemon-reload; ok "Services removed. Data kept: panel.db, config.panel.json"; }
cmd_cloudflare() { need_root; mkdir -p "$(dirname "$LOG")"; PORT=$(cfgget port); PORT=${PORT:-8080}; local b; b=$(cfgget base_url); DOMAIN=${b#https://}; setup_cloudflare; }
main() { case "${1:-install}" in
  install) cmd_install;; update) cmd_update;; status) cmd_status;; doctor) cmd_doctor;; show) cmd_show;; uninstall) cmd_uninstall;; cloudflare) cmd_cloudflare;;
  logs) journalctl -u ${SVC}-web -u ${SVC}-worker -u ${SVC}-panelbot -f -n 40;; restart) systemctl restart $SERVICES && ok "restarted";;
  *) echo "Usage: $0 [install|update|status|logs|restart|doctor|cloudflare|show|uninstall]";; esac; }
[ "${AXP_SOURCE_ONLY:-0}" = 1 ] || main "$@"
# end of install-panel.sh
