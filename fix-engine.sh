#!/usr/bin/env bash
# One-shot Docker engine setup for servers WITHOUT systemd. Usage: bash fix-engine.sh
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; [ -f "$DIR/core.py" ] || DIR=/opt/axionhost-panel; cd "$DIR" || exit 1
CFG="$DIR/config.panel.json"; LOGD=/var/log/axionhost; mkdir -p $LOGD
say(){ printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
[ "$(id -u)" = 0 ] || { echo "❌ root se chalao (sudo -i)"; exit 1; }

say "1/5 Docker install"
if ! command -v docker >/dev/null; then apt-get update -qq && apt-get install -y -qq docker.io iptables >/dev/null 2>&1; fi
command -v dockerd >/dev/null || { echo "❌ Docker install fail. Dekho: apt-get install -y docker.io"; exit 1; }
echo "✅ $(docker --version)"

say "2/5 Docker daemon start (bina systemd)"
if docker info >/dev/null 2>&1; then echo "✅ Daemon pehle se chal raha hai"; else
  update-alternatives --set iptables /usr/sbin/iptables-legacy >/dev/null 2>&1; update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy >/dev/null 2>&1
  pkill -f axion-watch-dockerd 2>/dev/null; pkill dockerd 2>/dev/null; rm -f /var/run/docker.pid
  start_d(){ setsid nohup bash -c "exec -a axion-watch-dockerd bash -c 'while true; do dockerd $1 >> $LOGD/dockerd.log 2>&1; echo \"[\$(date)] dockerd exited\" >> $LOGD/dockerd.log; sleep 4; done'" >/dev/null 2>&1 & }
  : > $LOGD/dockerd.log; start_d ""
  for i in $(seq 1 25); do docker info >/dev/null 2>&1 && break; sleep 1; done
  if ! docker info >/dev/null 2>&1; then
    echo "⚠ Default driver fail — vfs driver try kar raha hoon"; pkill -f axion-watch-dockerd; pkill dockerd; sleep 2; start_d "--storage-driver=vfs"
    for i in $(seq 1 25); do docker info >/dev/null 2>&1 && break; sleep 1; done
  fi
fi
if ! docker info >/dev/null 2>&1; then
  echo; echo "❌ Docker daemon nahi chala. Log:"; tail -15 $LOGD/dockerd.log; echo
  if grep -qiE "permitted|permission denied|operation not|mount|cgroup|iptables|netlink" $LOGD/dockerd.log; then
    echo "👉 Wajah: yeh machine ek restricted CONTAINER hai (privileged nahi). Isme Docker/VPS host nahi ho sakta."
    echo "   Solution: asli VM par panel chalao (GCP Compute Engine VM, Ubuntu 22.04) — wahan 'bash install-panel.sh' seedha chalega."
  fi; exit 1; fi
echo "✅ Docker daemon chal raha hai"

say "3/5 Test container"
if docker run --rm alpine:3.20 echo axion-ok 2>&1 | grep -q axion-ok; then echo "✅ Container chal gaya"; else docker run --rm alpine:3.20 echo x 2>&1 | tail -5; echo "❌ Container run nahi hua (upar error dekho)"; exit 1; fi

say "4/5 Panel config → docker"
if [ -f "$CFG" ]; then PYB="$DIR/venv/bin/python"; [ -x "$PYB" ] || PYB=python3
  $PYB -c "import json;p='$CFG';c=json.load(open(p));c['backend']='docker';json.dump(c,open(p,'w'),indent=2)" && chmod 600 "$CFG" && echo "✅ backend = docker"; fi

say "5/5 Worker restart"
pkill -f "python.* worker.py" 2>/dev/null; sleep 4; pgrep -f "worker.py" >/dev/null && echo "✅ Worker chal raha hai" || echo "⚠ Worker nahi chala — bash fix-host.sh chalao"
grep -q "fix-engine.sh" /root/.bashrc 2>/dev/null || echo "docker info >/dev/null 2>&1 || bash $DIR/fix-engine.sh >/dev/null 2>&1 &" >> /root/.bashrc
echo; echo "🎉 Ab panel mein Node → Save & test dabao. 'NOT installed' hat jana chahiye."
