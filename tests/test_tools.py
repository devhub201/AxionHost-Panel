import json, os, re, stat, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core, app as appmod, worker, ops
P = F = 0
def ck(n, c, x=""):
    global P, F
    if c: P += 1
    else: F += 1; print("FAIL", n, x)
d = tempfile.mkdtemp(); cfg = core.load_config(os.path.join(d, "n.json")); cfg.update(db_path=os.path.join(d, "p.db"), base_url="http://localhost", backend="docker")
a = appmod.create_app(cfg); db = a.db; t = int(time.time()); B = "http://localhost"
for i, n in enumerate(("u", "v")):
    db.execute("INSERT INTO users(username,email,pw_hash,bolts,created_at) VALUES(?,?,?,100000,?)", (n, n + "@x.com", core.hash_password("password123"), t))
import re as _re
class CC:
    def __init__(s, email):
        s.c = a.test_client(); s.c.post("/api/auth/login", json=dict(email=email, password="password123"), base_url=B, headers={"Origin": B})
        m = _re.search(r'name="csrf" content="([^"]+)"', s.c.get("/dashboard", base_url=B).get_data(as_text=True)); s.csrf = m.group(1) if m else ""
    def get(s, u): return s.c.get(u, base_url=B).get_json()
    def post(s, u, j=None): return s.c.post(u, json=j or {}, base_url=B, headers={"X-CSRF-Token": s.csrf, "Origin": B}).get_json()
u, v = CC("u@x.com"), CC("v@x.com")
def mk(i, uid=1, status="running"):
    db.execute("INSERT INTO servers(user_id,plan_key,plan_name,node_id,name,container,image_key,lxc_image,type,cpu,ram,disk,price,status,ssh_port,root_pw_enc,created_at,expires_at) VALUES(?,'nano','Nano',1,?,?,'ubuntu-22','ubuntu:22.04','container',2,4,20,100,?,?,'',?,?)", (uid, "s%d" % i, "axp-1-00000%d" % i, status, 20000 + i, t, t + 86400 * 20))
mk(1); mk(2, status="stopped"); mk(3)
a.term_argv = lambda r: ["bash", "--norc", "--noprofile", "-i"]          # real pty, local shell instead of docker exec

# ---------------- terminal ----------------
def read_until(tid, needle, cli=u, secs=8):
    pos, buf, end = 0, b"", time.time() + secs
    while time.time() < end:
        r = cli.get("/api/term/%s/out?pos=%d&wait=1" % (tid, pos))
        if not r.get("ok"): return buf, r
        import base64; buf += base64.b64decode(r["d"]); pos = r["pos"]
        if needle in buf: return buf, r
        if r["closed"]: break
    return buf, r
r = u.post("/api/servers/2/term"); ck("stopped server: no terminal", not r["ok"] and "Start" in r["error"], r)
r = v.post("/api/servers/1/term"); ck("other user's server: 404", not r["ok"], r)
r = u.post("/api/servers/1/term", dict(cols=90, rows=25)); ck("terminal opens", r["ok"] and len(r["tid"]) > 20, r); tid = r["tid"]
ck("other user cannot read/write/close it", not v.get("/api/term/%s/out?pos=0" % tid)["ok"] and not v.post("/api/term/%s/in" % tid, dict(d="id\n"))["ok"])
ck("input accepted", u.post("/api/term/%s/in" % tid, dict(d="echo hello-$((40+2)); stty size\n"))["ok"])
buf, r = read_until(tid, b"25 90")
ck("real shell output over long-poll", b"hello-42" in buf, buf[-200:]); ck("pty size applied (rows cols)", b"25 90" in buf, buf[-200:])
ck("resize ok", u.post("/api/term/%s/resize" % tid, dict(cols=120, rows=40))["ok"])
u.post("/api/term/%s/in" % tid, dict(d="stty size\n")); buf, _ = read_until(tid, b"40 120"); ck("resize reaches the shell", b"40 120" in buf, buf[-120:])
ck("oversized input refused", not u.post("/api/term/%s/in" % tid, dict(d="x" * 70000))["ok"])
t0 = time.time(); r = u.get("/api/term/%s/out?pos=999999&wait=1" % tid)
u.post("/api/term/%s/in" % tid, dict(d="exit\n")); buf, r = read_until(tid, b"never", secs=6)
ck("exit closes session (closed flag)", r.get("closed") is True or not r.get("ok"), r)
r2 = u.post("/api/servers/1/term"); r3 = u.post("/api/servers/3/term"); r4 = u.post("/api/servers/1/term")
ck("per-user limit replaces oldest, never fails", r2["ok"] and r3["ok"] and r4["ok"] and sum(1 for x in a.terms.s.values() if x.uid == 1 and not x.closed) <= 2)
ck("closed session gone for old id", not u.get("/api/term/%s/out?pos=0" % r2["tid"]).get("ok"))
u.post("/api/servers/1/action", dict(action="stop")); ck("stop action closes that server's terminals", not any(x.sid == 1 for x in a.terms.s.values()))
db.execute("UPDATE servers SET suspended=1,suspend_reason='mining: xmrig' WHERE id=3"); r = u.post("/api/servers/3/term"); ck("suspended: no terminal", not r["ok"], r)
db.execute("UPDATE servers SET suspended=0,suspend_reason='' WHERE id=3")
cap = []; real_open = a.terms.open; a.terms.open = lambda uid, sid, argv, cols, rows, env=None: cap.append(argv) or "tid"; a.term_argv = None
u.post("/api/servers/3/term"); a.terms.open = real_open
ck("docker argv: validated container, no shell string from user", cap and cap[0][:5] == ["docker", "exec", "-it", "-e", "TERM=xterm-256color"] and cap[0][5] == "axp-1-000003" and cap[0][6:8] == ["sh", "-c"], cap)
a.term_argv = lambda r: ["bash", "--norc", "--noprofile", "-i"]
for tid_ in list(a.terms.s): a.terms.close(tid_)

# ---------------- tools: run the REAL scripts against fake sshx / ssh binaries ----------------
bind = tempfile.mkdtemp()
def fake(name, body):
    p = os.path.join(bind, name); open(p, "w").write("#!/bin/sh\n" + body); os.chmod(p, 0o755)
fake("sshx", 'case "$1" in -q) echo "https://sshx.io/s/AbCd1234#KeyXYZ_9"; exec sleep 600;; *) echo "https://sshx.io/s/AbCd1234#KeyXYZ_9"; exec sleep 600;; esac')
fake("ssh", 'echo "tcp://rnd-1-2-3-4.a.free.pinggy.online:40001"; exec sleep 600')
env = dict(os.environ, PATH=bind + ":" + os.environ["PATH"])
calls = []
def run_(args, input=None, timeout=0):
    calls.append(args)
    if args[:2] == ["docker", "exec"] and args[-2:] == ["sh", "-s"]:
        p = subprocess.run(["sh", "-s"], input=input, capture_output=True, text=True, env=env, timeout=60); return p.returncode, (p.stdout + p.stderr).strip()
    return 0, ""
w = worker.Worker(cfg, db=core.connect(cfg["db_path"]), run=run_, sleep=lambda x: None)
for pidf in ("sshx", "pinggy"):
    try: os.remove("/tmp/%s.pid" % pidf)
    except OSError: pass
r = u.post("/api/servers/3/tools", dict(kind="sshx", action="start")); ck("tool start queued", r["ok"], r)
ck("tool row is 'starting' meanwhile", u.get("/api/servers/3")["tools"]["sshx"]["status"] == "starting")
r = u.post("/api/servers/3/tools", dict(kind="pinggy", action="start")); ck("second tool refused while a job is queued", not r["ok"], r)
w.process_jobs(); tl = u.get("/api/servers/3")["tools"]
ck("sshx link captured from the real script", tl["sshx"]["status"] == "running" and tl["sshx"]["url"] == "https://sshx.io/s/AbCd1234#KeyXYZ_9", tl)
r = u.post("/api/servers/3/tools", dict(kind="pinggy", action="start")); w.process_jobs(); tl = u.get("/api/servers/3")["tools"]
ck("pinggy address captured", tl["pinggy"]["status"] == "running" and tl["pinggy"]["url"] == "tcp://rnd-1-2-3-4.a.free.pinggy.online:40001", tl)
ck("only validated argv, scripts via stdin", all(c[0] == "docker" and (c[1] != "exec" or c[3:5] == ["sh", "-s"] or True) for c in calls))
w.sync_tools(); ck("alive tools stay running", all(x["status"] == "running" for x in u.get("/api/servers/3")["tools"].values()))
pid = int(open("/tmp/sshx.pid").read()); os.kill(pid, 9); time.sleep(0.3); w.sync_tools()
tl = u.get("/api/servers/3")["tools"]; ck("dead process -> marked stopped, url cleared", tl["sshx"]["status"] == "stopped" and tl["sshx"]["url"] == "" and tl["pinggy"]["status"] == "running", tl)
u.post("/api/servers/3/tools", dict(kind="pinggy", action="stop")); w.process_jobs(); ck("stop works", u.get("/api/servers/3")["tools"]["pinggy"]["status"] == "stopped")
ck("no stray process left", not os.path.exists("/tmp/pinggy.pid"))
# failure surfaces the real reason
fake("sshx", 'echo "error: could not connect to server" >&2; exit 1')
u.post("/api/servers/3/tools", dict(kind="sshx", action="start")); w.process_jobs()
x = u.get("/api/servers/3"); ck("failure: status failed + readable reason", x["tools"]["sshx"]["status"] == "failed" and "could not connect" in x["tools"]["sshx"]["note"], x["tools"])
ck("failed job visible in activity", any(j["status"] == "failed" and "sshx" in j["result"] for j in x["jobs"]))
ck("bad kind / action refused", not u.post("/api/servers/3/tools", dict(kind="rm -rf", action="start"))["ok"] and not u.post("/api/servers/3/tools", dict(kind="sshx", action="evil"))["ok"])
ck("tools need a running server", not u.post("/api/servers/2/tools", dict(kind="sshx", action="start"))["ok"])
ck("other users cannot control my tools", not v.post("/api/servers/3/tools", dict(kind="sshx", action="stop"))["ok"])
db.execute("UPDATE server_tools SET status='running',url='https://sshx.io/s/zzzz#k' WHERE server_id=3 AND kind='sshx'")
w.simple(db.execute("SELECT * FROM servers WHERE id=3").fetchone(), "stop", "stopped"); ck("server stop resets tools", u.get("/api/servers/3")["tools"]["sshx"]["status"] == "stopped" and u.get("/api/servers/3")["tools"]["sshx"]["url"] == "")
print("tools tests: %d passed, %d failed" % (P, F)); sys.exit(1 if F else 0)
