"""AxionHost Panel — worker. Runs queued jobs (deploy/start/stop/...) with LXD, syncs status,
bills servers, scans for miners. Only whitelisted, validated lxc commands are ever executed (no shell).
Run:  python worker.py   (systemd: axionhost-worker)"""
import json, logging, subprocess, time
import core, ops
from core import now, tx, add_bolts, audit, geti, dec, enc, CONT_RE, IMG_RE, REMOTE_RE

log = logging.getLogger("axp.worker")
MINERS = ("xmrig", "minerd", "cpuminer", "ethminer", "nbminer", "t-rex", "trex", "phoenixminer", "lolminer", "gminer", "cgminer", "bfgminer", "nicehash", "kawpowminer", "xmr-stak", "ccminer")
SSH_SETUP = r"""
set -e
if ! command -v sshd >/dev/null 2>&1 && [ ! -x /usr/sbin/sshd ]; then
  if command -v apt-get >/dev/null 2>&1; then export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq openssh-server;
  elif command -v apk >/dev/null 2>&1; then apk add --no-cache openssh && rc-update add sshd default 2>/dev/null || true;
  elif command -v dnf >/dev/null 2>&1; then dnf install -y -q openssh-server; fi
fi
mkdir -p /etc/ssh /run/sshd; ssh-keygen -A >/dev/null 2>&1 || true
touch /etc/ssh/sshd_config
sed -i '/^#\?PasswordAuthentication/d;/^#\?PermitRootLogin/d' /etc/ssh/sshd_config
printf 'PasswordAuthentication yes\nPermitRootLogin yes\n' >> /etc/ssh/sshd_config
if command -v systemctl >/dev/null 2>&1; then systemctl enable --now ssh 2>/dev/null || systemctl enable --now sshd 2>/dev/null || true; systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
elif command -v rc-service >/dev/null 2>&1; then rc-service sshd restart || rc-service sshd start; else /usr/sbin/sshd; fi
"""

class JobError(Exception): pass

def real_run(args, input=None, timeout=300):
    try:
        p = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired: return 124, "timeout"
    except FileNotFoundError: return 127, "lxc not found"

class Worker:
    def __init__(self, cfg, db=None, run=real_run, sleep=time.sleep):
        self.cfg, self.run, self.sleep = cfg, run, sleep
        self.db = db or core.connect(cfg["db_path"]); core.init_db(self.db)
        self.last = dict(sync=0, bill=0, scan=0, disk=0)

    # ---- lxc helpers (validated) ----
    def target(self, s):
        node = self.db.execute("SELECT * FROM nodes WHERE id=?", (s["node_id"],)).fetchone()
        rem = (node["lxc_remote"] if node else "") or ""
        if not REMOTE_RE.match(rem): raise JobError("bad remote")
        if not CONT_RE.match(s["container"]): raise JobError("bad container name")
        return (rem + ":" if rem else "") + s["container"]

    def lxc(self, *args, input=None, timeout=300, check=True):
        rc, out = self.run(["lxc", *[str(a) for a in args]], input, timeout)
        if check and rc != 0: raise JobError("lxc %s failed: %s" % (args[0], out[-300:]))
        return rc, out

    # ---- jobs ----
    def recover(self):
        for j in self.db.execute("SELECT id,attempts FROM jobs WHERE status='running'").fetchall():
            self.db.execute("UPDATE jobs SET status=?,updated=? WHERE id=?", ("queued" if j["attempts"] < 3 else "failed", now(), j["id"]))

    def process_jobs(self, limit=20):
        n = 0
        for j in self.db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT ?", (limit,)).fetchall():
            cur = self.db.execute("UPDATE jobs SET status='running',attempts=attempts+1,updated=? WHERE id=? AND status='queued'", (now(), j["id"]))
            if cur.rowcount != 1: continue
            s = self.db.execute("SELECT * FROM servers WHERE id=?", (j["server_id"],)).fetchone()
            try:
                if not s: raise JobError("server missing")
                res = getattr(self, "job_" + j["action"])(s, json.loads(j["payload"] or "{}")) or "ok"
                self.db.execute("UPDATE jobs SET status='done',result=?,updated=? WHERE id=?", (str(res)[:300], now(), j["id"]))
            except Exception as e:
                log.exception("job %s failed", j["id"])
                self.db.execute("UPDATE jobs SET status='failed',result=?,updated=? WHERE id=?", (str(e)[:300], now(), j["id"]))
                if s and j["action"] in ("deploy", "reinstall"): self.fail_provision(s, j["action"], str(e))
            n += 1
        return n

    def provision(self, s, image):
        t = self.target(s)
        if not IMG_RE.match(image): raise JobError("bad image")
        self.lxc("delete", t, "--force", check=False, timeout=120)                    # idempotent re-run
        self.lxc("init", image, t, "-s", self.cfg["lxc_storage_pool"], *(["--vm"] if s["type"] == "vm" else []), timeout=900)
        try:
            self.lxc("config", "set", t, "limits.memory", "%dMB" % (int(s["ram"]) * 1024))
            self.lxc("config", "set", t, "limits.cpu", int(s["cpu"]))
            rc, _ = self.lxc("config", "device", "set", t, "root", "size=%dGB" % int(s["disk"]), check=False)
            if rc != 0: self.lxc("config", "device", "override", t, "root", "size=%dGB" % int(s["disk"]))
            if s["type"] != "vm":
                for k, v in (("security.nesting", "true"), ("security.syscalls.intercept.mknod", "true"), ("security.syscalls.intercept.setxattr", "true")):
                    self.lxc("config", "set", t, k, v, check=False)
            self.lxc("start", t, timeout=180)
            for _ in range(45):                                                       # wait until the guest answers
                if self.lxc("exec", t, "--", "true", check=False, timeout=20)[0] == 0: break
                self.sleep(2)
            else: raise JobError("guest did not become ready")
            pw = dec(self.cfg["secret_key"], s["root_pw_enc"])
            self.lxc("exec", t, "--", "chpasswd", input="root:%s\n" % pw)
            self.lxc("exec", t, "--", "sh", "-c", "bash -s 2>/dev/null || sh -s", input=SSH_SETUP, timeout=420, check=False)
            self.lxc("config", "device", "remove", t, "ssh", check=False)
            self.lxc("config", "device", "add", t, "ssh", "proxy", "listen=tcp:0.0.0.0:%d" % int(s["ssh_port"]), "connect=tcp:127.0.0.1:22")
        except Exception:
            self.lxc("delete", t, "--force", check=False, timeout=120); raise

    def job_deploy(self, s, p):
        self.provision(s, s["lxc_image"])
        self.db.execute("UPDATE servers SET status='running',disk_used=NULL,disk_checked=NULL WHERE id=?", (s["id"],))
        n = self.db.execute("SELECT host FROM nodes WHERE id=?", (s["node_id"],)).fetchone()
        ops.notify(self.db, s["user_id"], "Your server is ready", "**%s** (%s) is live.\n`ssh root@%s -p %s`\nPassword: panel → Server → Credentials." % (s["name"], s["plan_name"], (n and n["host"]) or "<node-ip>", s["ssh_port"]), "deploy_ok")
        return "deployed"

    def job_reinstall(self, s, p):
        self.db.execute("UPDATE servers SET root_pw_enc=? WHERE id=?", (enc(self.cfg["secret_key"], core.gen_password()), s["id"]))
        s = self.db.execute("SELECT * FROM servers WHERE id=?", (s["id"],)).fetchone()
        self.provision(s, p.get("image") or s["lxc_image"])
        self.db.execute("UPDATE servers SET status='running',disk_used=NULL,disk_checked=NULL,lxc_image=?,image_key=? WHERE id=?", (p.get("image") or s["lxc_image"], p.get("image_key") or s["image_key"], s["id"]))
        ops.notify(self.db, s["user_id"], "Reinstall finished", "**%s** was reinstalled. The new root password is in the panel." % s["name"], "reinstall_ok")
        return "reinstalled"

    def fail_provision(self, s, action, why):
        s = self.db.execute("SELECT * FROM servers WHERE id=?", (s["id"],)).fetchone()
        if not s or s["deleted"]: return
        refund = geti(core.get_settings(self.db), "refund_on_fail") == 1 and action == "deploy"
        with tx(self.db):
            if refund:
                add_bolts(self.db, s["user_id"], s["price"], "Refund: deploy failed", s["container"])
                self.db.execute("UPDATE servers SET status='failed',deleted=1,ssh_port=NULL,note=? WHERE id=?", (why[:200], s["id"]))
            else:
                self.db.execute("UPDATE servers SET status='failed',note=? WHERE id=?", (why[:200], s["id"]))
            audit(self.db, None, "server.%s_failed" % action, s["id"], why[:200])
            ops.notify(self.db, s["user_id"], "Deployment failed", "**%s** could not be created.%s" % (s["name"], (" Your **%d BOLTs** were refunded." % s["price"]) if refund else " Contact support."), "deploy_fail")
            self.db.execute("INSERT INTO notify(user_id,title,body,kind,created,to_log) VALUES(?,?,?,?,?,1)", (s["user_id"], "Deploy failed", "Server #%d: %s" % (s["id"], why[:300]), "staff", now()))

    def simple(self, s, verb, status):
        self.lxc(verb, self.target(s), timeout=120); self.db.execute("UPDATE servers SET status=? WHERE id=?", (status, s["id"])); return verb

    def job_start(self, s, p): return self.simple(s, "start", "running")
    def job_stop(self, s, p): return self.simple(s, "stop", "stopped")
    def job_restart(self, s, p): return self.simple(s, "restart", "running")

    def job_delete(self, s, p):
        self.lxc("delete", self.target(s), "--force", check=False, timeout=180)
        self.db.execute("UPDATE servers SET deleted=1,status='deleted',ssh_port=NULL WHERE id=?", (s["id"],)); return "deleted"

    # ---- periodic ----
    def sync_status(self):
        for n in self.db.execute("SELECT * FROM nodes WHERE enabled=1").fetchall():
            rem = n["lxc_remote"] or ""
            if not REMOTE_RE.match(rem): continue
            rc, out = self.lxc("list", *([rem + ":"] if rem else []), "--format", "json", check=False, timeout=60)
            if rc != 0: continue
            try: live = {c["name"]: c["status"].lower() for c in json.loads(out)}
            except Exception: continue
            for s in self.db.execute("SELECT id,container,status FROM servers WHERE node_id=? AND deleted=0 AND status IN ('running','stopped')", (n["id"],)).fetchall():
                st = live.get(s["container"])
                if st in ("running", "stopped") and st != s["status"]: self.db.execute("UPDATE servers SET status=? WHERE id=?", (st, s["id"]))

    def sync_disk(self):
        """Per-instance disk usage from LXD (instance state), stored per server. Host filesystem is never read."""
        for s in self.db.execute("SELECT id,container,node_id FROM servers WHERE deleted=0 AND status IN ('running','stopped')").fetchall():
            try: t = self.target(s)
            except JobError: continue
            rem, _, name = t.rpartition(":")
            rc, out = self.lxc("query", (rem + ":" if rem else "") + "/1.0/instances/" + name + "/state", check=False, timeout=30)
            used = None
            if rc == 0:
                try:
                    u = ((json.loads(out).get("disk") or {}).get("root") or {}).get("usage")
                    used = int(u) if u is not None and int(u) >= 0 else None
                except (ValueError, TypeError, AttributeError): used = None
            self.db.execute("UPDATE servers SET disk_used=?,disk_checked=? WHERE id=?", (used, now(), s["id"]))

    def billing(self):
        s = core.get_settings(self.db); t = now(); bd = geti(s, "billing_days") * 86400
        for r in self.db.execute("SELECT * FROM servers WHERE deleted=0 AND status IN ('running','stopped')").fetchall():
            left = r["expires_at"] - t
            for days in (1, 3):
                if 0 < left <= days * 86400:
                    kind = "remind%d:%d:%d" % (days, r["id"], r["expires_at"])
                    if not self.db.execute("SELECT 1 FROM notify WHERE kind=?", (kind,)).fetchone():
                        ops.notify(self.db, r["user_id"], "Server expires soon", "**%s** expires in about %d day(s). Renew it in the panel to keep it running." % (r["name"], days), kind)
                    break
            if left > 0: continue
            owner = self.db.execute("SELECT bolts FROM users WHERE id=?", (r["user_id"],)).fetchone()
            if r["suspended"] and not r["suspend_reason"].startswith("billing"): continue   # admin / mining suspensions are never auto-lifted
            if r["auto_renew"] and owner and owner["bolts"] >= r["price"]:
                try:
                    with tx(self.db):
                        add_bolts(self.db, r["user_id"], -r["price"], "Auto-renew " + r["name"], r["container"])
                        self.db.execute("UPDATE servers SET expires_at=?,suspended=0,suspend_reason='' WHERE id=?", (max(r["expires_at"], t) + bd, r["id"]))
                        if r["suspended"]: ops.queue(self.db, r["id"], "start")
                        ops.notify(self.db, r["user_id"], "Server auto-renewed", "**%s** was renewed for %d BOLTs." % (r["name"], r["price"]), "renewed")
                    continue
                except core.Insufficient: pass
            if not r["suspended"]:
                with tx(self.db):
                    self.db.execute("UPDATE servers SET suspended=1,suspend_reason='billing: expired' WHERE id=?", (r["id"],)); ops.queue(self.db, r["id"], "stop")
                    audit(self.db, None, "server.billing_suspend", r["id"], "")
                    ops.notify(self.db, r["user_id"], "Server suspended", "**%s** expired and was suspended. Renew within %d day(s) or it will be deleted." % (r["name"], geti(s, "delete_after_days")), "suspended")
            elif geti(s, "delete_after_days") > 0 and t > r["expires_at"] + geti(s, "delete_after_days") * 86400:
                with tx(self.db):
                    ops.queue(self.db, r["id"], "delete"); self.db.execute("UPDATE servers SET status='deleting' WHERE id=?", (r["id"],))
                    audit(self.db, None, "server.expired_delete", r["id"], "")
                    ops.notify(self.db, r["user_id"], "Server deleted", "**%s** was deleted after staying expired." % r["name"], "deleted")

    def scan_miners(self):
        for r in self.db.execute("SELECT * FROM servers WHERE deleted=0 AND suspended=0 AND status='running' AND type='container'").fetchall():
            try: rc, out = self.lxc("exec", self.target(r), "--", "ps", "-eo", "comm=", check=False, timeout=30)
            except JobError: continue
            if rc != 0: continue
            hit = sorted({l.strip().lower() for l in out.splitlines()} & set(MINERS))
            if hit:
                with tx(self.db):
                    self.db.execute("UPDATE servers SET suspended=1,suspend_reason=? WHERE id=?", ("mining: " + hit[0], r["id"])); ops.queue(self.db, r["id"], "stop")
                    audit(self.db, None, "server.miner_detected", r["id"], ",".join(hit))
                    ops.notify(self.db, r["user_id"], "Server suspended — mining detected", "**%s** was suspended for running `%s`. Crypto-mining is not allowed." % (r["name"], hit[0]), "mining")
                    self.db.execute("INSERT INTO notify(user_id,title,body,kind,created,to_log) VALUES(?,?,?,?,?,1)", (r["user_id"], "Miner detected", "Server #%d (%s) user %d: `%s`" % (r["id"], r["container"], r["user_id"], hit[0]), "staff", now()))

    def tick(self):
        n = self.process_jobs(); t = now()
        for name, every, fn in (("sync", 30, self.sync_status), ("disk", 60, self.sync_disk), ("bill", 60, self.billing), ("scan", 300, self.scan_miners)):
            if t - self.last[name] >= every:
                self.last[name] = t
                try: fn()
                except Exception: log.exception("%s failed", name)
        return n

    def loop(self):
        self.recover(); log.info("worker started")
        while True:
            try: busy = self.tick()
            except Exception: log.exception("tick failed"); busy = 0
            self.sleep(1 if busy else 3)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Worker(core.load_config()).loop()
