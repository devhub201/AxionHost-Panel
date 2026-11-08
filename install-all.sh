#!/usr/bin/env bash
# AxionHost: ONE command = QEMU/KVM-X VM engine + Panel (web + worker + Discord bot). Works with or without systemd.
#   bash install-all.sh                 # install / repair / update everything
#   bash install-all.sh TUNNEL_TOKEN    # also (re)start the Cloudflare tunnel
set -u
REPO="https://github.com/devhub201/AxionHost-Panel.git"; DIR="${AXION_DIR:-/opt/axionhost-panel}"; LOGD=/var/log/axionhost; TOK="${1:-}"
say(){ printf '\n\033[1;36m== %s\033[0m\n' "$*"; }; ok(){ printf ' \033[32m✔\033[0m %s\n' "$*"; }; bad(){ printf ' \033[31m✘\033[0m %s\n' "$*"; }
[ "$(id -u)" = 0 ] || { bad "root se chalao: sudo -i"; exit 1; }; mkdir -p "$LOGD"
HAS_SYSTEMD=0; [ -d /run/systemd/system ] && HAS_SYSTEMD=1

say "1/6 Packages (QEMU, tools)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq git curl python3 python3-venv python3-pip qemu-system-x86 qemu-utils genisoimage sshpass iproute2 procps >/dev/null 2>&1
for b in qemu-system-x86_64 qemu-img genisoimage sshpass git python3; do command -v $b >/dev/null && ok "$b" || { bad "$b install nahi hua (apt-get install -y ${b})"; exit 1; }; done
if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then ok "Hardware KVM mila - fast VMs"; MODE="HARDWARE-KVM"
else printf ' \033[33m!\033[0m /dev/kvm nahi - software emulation (TCG), ~6-15x slower. Free/test VPS ke liye theek.\n'; MODE="SOFTWARE-EMULATION (TCG)"; fi
timeout 8 qemu-system-x86_64 -machine accel=tcg -nographic -display none -S -monitor none -serial none -m 64 -no-reboot >/dev/null 2>&1; [ $? -eq 124 ] && ok "QEMU TCG start ho sakta hai" || { bad "QEMU TCG start nahi hua - 'qemu-system-x86_64 -machine accel=tcg -S -display none' chala ke error dekho"; }
command -v kvmx >/dev/null && { ok "KVM-X mila:"; kvmx detect 2>/dev/null | head -3 | sed 's/^/    /'; }

say "2/6 Panel code"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q --ff-only && ok "updated" || bad "git pull fail (local changes?)"
elif [ -f "$DIR/app.py" ]; then ok "existing install (no git) - code as-is"
else git clone -q "$REPO" "$DIR" && ok "cloned to $DIR" || { bad "clone fail"; exit 1; }; fi
cd "$DIR" || exit 1

say "3/6 Python environment"
[ -x venv/bin/python ] || python3 -m venv venv
venv/bin/pip install -q -r requirements-panel.txt && ok "requirements installed" || { bad "pip fail"; exit 1; }

say "4/6 Config"
CFG="$DIR/config.panel.json"
if [ ! -f "$CFG" ]; then
  echo "Pehli baar setup - Discord details chahiye (Enter = skip, baad mein config.panel.json mein bharo):"
  read -rp " Public URL (https://panel.tumhara.com): " BASE; read -rp " Discord Client ID: " DID; read -rp " Discord Client Secret: " DSEC
  read -rp " Discord Bot Token: " DTOK; read -rp " Discord Server (guild) ID: " GID; read -rp " Tumhara Discord user ID (owner): " OWN
  BASE=${BASE:-http://localhost:8080}
  AXP_BASE="$BASE" AXP_DID="$DID" AXP_DSEC="$DSEC" AXP_TOK="$DTOK" AXP_GID="$GID" AXP_OWN="$OWN" AXP_DB="$DIR/panel.db" venv/bin/python - <<'PY'
import json,os,secrets
e=os.environ; b=e["AXP_BASE"]
json.dump({"base_url":b,"host":"127.0.0.1","port":8080,"cookie_secure":b.startswith("https"),"trusted_proxy":"cloudflare" if b.startswith("https") else "","db_path":e["AXP_DB"],"secret_key":secrets.token_hex(32),
 "discord_client_id":e["AXP_DID"],"discord_client_secret":e["AXP_DSEC"],"discord_bot_token":e["AXP_TOK"],"discord_guild_id":e["AXP_GID"],"owner_discord_id":e["AXP_OWN"],
 "ssh_port_start":20000,"ssh_port_end":29999,"lxc_storage_pool":"default"},open("config.panel.json","w"),indent=2)
PY
  chmod 600 "$CFG"; ok "config.panel.json bana"
fi
venv/bin/python - <<PY
import json,core
p="$CFG"; c=json.load(open(p)); c["backend"]="qemu"; c.setdefault("qemu_boot_timeout",900); json.dump(c,open(p,"w"),indent=2)
cfg=core.load_config(p); db=core.connect(cfg["db_path"]); core.init_db(db)
PY
chmod 600 "$CFG"; ok "VPS engine = qemu ($MODE)"
PORT=$(venv/bin/python -c "import core;print(core.load_config('$CFG').get('port',8080))"); export AXION_PANEL_CONFIG="$CFG"

say "5/6 Services"
if [ $HAS_SYSTEMD = 1 ]; then
  [ -f /etc/systemd/system/axionhost-web.service ] || bash install-panel.sh install </dev/null >/dev/null 2>&1
  systemctl restart axionhost-web axionhost-worker axionhost-panelbot 2>/dev/null; sleep 3
  for s in web worker panelbot; do systemctl is-active -q axionhost-$s && ok "axionhost-$s" || bad "axionhost-$s (journalctl -u axionhost-$s -n 30)"; done
else
  pkill -f axion-watch-web; pkill -f axion-watch-worker; pkill -f axion-watch-bot; pkill -f "python.* app.py"; pkill -f "python.* worker.py"; pkill -f "python.* panel_bot.py"; sleep 1
  watch(){ local n=$1; shift; setsid nohup bash -c "exec -a axion-watch-$n bash -c 'while true; do \"\$@\" >> $LOGD/$n.log 2>&1; echo \"[\$(date)] $n exited, restarting\" >> $LOGD/$n.log; sleep 3; done' _ \"\$@\"" _ "$@" >/dev/null 2>&1 & }
  watch web venv/bin/python app.py; watch worker venv/bin/python worker.py; [ -f panel_bot.py ] && watch bot venv/bin/python panel_bot.py
  for i in $(seq 1 25); do c=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/ 2>/dev/null); [ "$c" != 000 ] && break; sleep 1; done
  [ "$c" != 000 ] && ok "web chal raha hai (HTTP $c) on 127.0.0.1:$PORT" || { bad "web nahi chala:"; tail -15 $LOGD/web.log; }
  grep -q "install-all.sh" /root/.bashrc 2>/dev/null || echo "pgrep -f axion-watch-web >/dev/null || bash $DIR/install-all.sh </dev/null >/dev/null 2>&1 &" >> /root/.bashrc
fi

say "6/6 Cloudflare tunnel"
if [ -n "$TOK" ] && command -v cloudflared >/dev/null; then
  service cloudflared stop >/dev/null 2>&1; pkill -f "cloudflared.*tunnel" 2>/dev/null; sleep 1
  setsid nohup bash -c "exec -a axion-watch-tunnel bash -c 'while true; do cloudflared tunnel --no-autoupdate run --token $TOK >> $LOGD/tunnel.log 2>&1; sleep 5; done'" >/dev/null 2>&1 &
  sleep 6; grep -qiE "registered tunnel connection" $LOGD/tunnel.log && ok "tunnel connected" || { bad "tunnel log:"; tail -6 $LOGD/tunnel.log; }
else echo " (skip - token do: bash install-all.sh TOKEN)"; fi

printf '\n\033[1;32m🎉 Done.\033[0m Engine: qemu (%s)\n' "$MODE"
echo " Panel: 127.0.0.1:$PORT | Logs: $LOGD/*.log"
echo " Panel → Admin → Nodes → Node 1: 'Save & test' dabao (QEMU ✔ dikhna chahiye)."
echo " Pehla VPS deploy karte waqt Ubuntu image (~600MB) download hoti hai; TCG mein boot 3-10 min leta hai."
