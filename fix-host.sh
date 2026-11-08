#!/usr/bin/env bash
# AxionHost one-shot fixer for servers WITHOUT systemd (containers). Usage: bash fix-host.sh [CLOUDFLARE_TUNNEL_TOKEN]
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; [ -f "$DIR/app.py" ] || DIR=/opt/axionhost-panel; cd "$DIR" || exit 1
export AXION_PANEL_CONFIG="$DIR/config.panel.json"; PY="$DIR/venv/bin/python"; [ -x "$PY" ] || PY=$(command -v python3)
LOGD=/var/log/axionhost; mkdir -p $LOGD
PORT=$($PY -c "import core;print(core.load_config('$AXION_PANEL_CONFIG').get('port',8080))" 2>/dev/null); PORT=${PORT:-8080}
say(){ printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
[ -f "$AXION_PANEL_CONFIG" ] || { echo "❌ config.panel.json nahi mila in $DIR"; exit 1; }

say "Purane processes band kar raha hoon"
pkill -f "axion-watch" 2>/dev/null; pkill -f "$DIR/app.py|python.* app.py|python.* worker.py|python.* panel_bot.py" 2>/dev/null
service cloudflared stop >/dev/null 2>&1; pkill -f "cloudflared.*tunnel" 2>/dev/null; sleep 1

# watchdog: process crash ho to 3 sec mein restart
watch(){ local name=$1; shift; setsid nohup bash -c "exec -a axion-watch-$name bash -c 'while true; do \"\$@\" >> $LOGD/$name.log 2>&1; echo \"[\$(date)] $name exited, restarting\" >> $LOGD/$name.log; sleep 3; done' _ \"\$@\"" _ "$@" >/dev/null 2>&1 & }

say "Web + worker + bot start"
watch web $PY app.py; watch worker $PY worker.py
[ -f panel_bot.py ] && watch bot $PY panel_bot.py
for i in $(seq 1 20); do c=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/ 2>/dev/null); [ "$c" != "000" ] && break; sleep 1; done
if [ "$c" = "000" ] || [ -z "$c" ]; then echo "❌ Web start nahi hua. Error:"; tail -25 $LOGD/web.log; else echo "✅ Web chal raha hai (HTTP $c) on 127.0.0.1:$PORT"; fi

say "Cloudflare tunnel"
TOK="$1"
[ -z "$TOK" ] && TOK=$(grep -rhoE "(--token|service install)[ =]+[A-Za-z0-9_=-]{60,}" /etc/init.d/cloudflared /etc/default/cloudflared /etc/systemd/system/cloudflared.service /var/log/axionhost-install.log "$DIR" 2>/dev/null | grep -oE "[A-Za-z0-9_=-]{60,}" | head -1)
[ -z "$TOK" ] && TOK=$(grep -rhoE "eyJ[A-Za-z0-9_=-]{50,}" /etc/init.d/cloudflared /etc/default/cloudflared 2>/dev/null | head -1)
if command -v cloudflared >/dev/null; then
  if [ -n "$TOK" ]; then
    watch tunnel cloudflared tunnel --no-autoupdate run --token "$TOK"; sleep 6
    if grep -qiE "Registered tunnel connection|Connection .* registered" $LOGD/tunnel.log; then echo "✅ Tunnel connected"; else echo "⚠ Tunnel log:"; tail -8 $LOGD/tunnel.log; fi
  else echo "⚠ Tunnel token nahi mila. Cloudflare → Zero Trust → Tunnels → apna tunnel → token copy karo, phir chalao:  bash fix-host.sh TOKEN"; fi
else echo "⚠ cloudflared installed nahi hai"; fi

say "Auto-start (container restart ke baad)"
for f in /etc/rc.local; do :; done
grep -q "fix-host.sh" /root/.bashrc 2>/dev/null || echo "pgrep -f axion-watch-web >/dev/null || bash $DIR/fix-host.sh >/dev/null 2>&1 &" >> /root/.bashrc
echo "Logs: tail -f $LOGD/web.log | worker.log | bot.log | tunnel.log"
echo; echo "Final check:"; curl -s -o /dev/null -w "local: HTTP %{http_code}\n" http://127.0.0.1:$PORT/
