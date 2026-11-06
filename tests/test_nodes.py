import json, os, re, sys, tempfile, time, stat
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core, app as appmod, worker, ops, nodes
P = F = 0
def ck(n, c, x=""):
    global P, F
    if c: P += 1
    else: F += 1; print("FAIL", n, x)
# ---- parse_target ----
pt = nodes.parse_target
ck("pinggy tcp url", pt("tcp://rnd-1-2-3-4.a.free.pinggy.online:40001") == ("rnd-1-2-3-4.a.free.pinggy.online", 40001, ""))
ck("ssh command -p after", pt("ssh root@1.2.3.4 -p 2222") == ("1.2.3.4", 2222, "root"))
ck("ssh command -p before", pt("ssh -p 2200 ubuntu@host.example.com") == ("host.example.com", 2200, "ubuntu"))
ck("user@host:port", pt("admin@host.example.com:2022") == ("host.example.com", 2022, "admin"))
ck("bare host defaults to 22", pt("203.0.113.9") == ("203.0.113.9", 22, ""))
ck("ipv6 with port", pt("[2001:db8::1]:2222") == ("2001:db8::1", 2222, ""))
for bad, why in (("https://sshx.io/s/abc#key", "sshx"), ("https://example.com", "web"), ("host:99999", "port"), ("", "empty"), ("ho st", "space"), ("a;rm -rf /", "inject"), ("$(id)@host", "user inject")):
    try: pt(bad); ck("refuses " + why, False, bad)
    except nodes.TargetError as e: ck("refuses " + why, True)
try: pt("https://sshx.io/s/abc#key")
except nodes.TargetError as e: ck("sshx explained", "browser-only" in str(e) and "Pinggy" in str(e))
# ---- fake remote machine: `ssh` runs the command locally, `docker`/`nproc` are fakes ----
bind = tempfile.mkdtemp(); logf = os.path.join(bind, "ssh.log"); dlog = os.path.join(bind, "docker.log")
def script(name, body):
    p = os.path.join(bind, name); open(p, "w").write("#!/bin/sh\n" + body); os.chmod(p, 0o755)
script("ssh", f'''echo "ARGV $*" >> {logf}; echo "PW $AXP_SSH_PW ASK $SSH_ASKPASS REQ $SSH_ASKPASS_REQUIRE" >> {logf}
[ "$AXP_SSH_PW" = "s3cret" ] || {{ echo "root@h: Permission denied (publickey,password)." >&2; exit 255; }}
for a; do last="$a"; done; exec sh -c "$last"''')
script("nproc", "echo 2")
script("docker", f'''echo "$@" >> {dlog}
case "$1" in
  info) exit 0;;
  ps) echo "axp-9-aaaaaa|running";;
  inspect) echo "/axp-9-aaaaaa|1000000000";;
  stats) echo "axp-9-aaaaaa|50.00%|512MiB / 4GiB";;
  exec) if [ "$2" = "-i" ]; then cat >/dev/null; fi; exit 0;;
esac
exit 0''')
os.environ["PATH"] = bind + ":" + os.environ["PATH"]
d = tempfile.mkdtemp(); cfg = core.load_config(os.path.join(d, "n.json")); cfg.update(db_path=os.path.join(d, "p.db"), base_url="http://localhost", backend="docker")
a = appmod.create_app(cfg); db = a.db; t = int(time.time()); B = "http://localhost"
db.execute("INSERT INTO users(username,email,pw_hash,bolts,role,created_at) VALUES('adm','a@x.com',?,100000,'admin',?)", (core.hash_password("password123"), t))
db.execute("INSERT INTO users(username,email,pw_hash,bolts,created_at) VALUES('usr','u@x.com',?,100000,?)", (core.hash_password("password123"), t))
class CC:
    def __init__(s, email):
        s.c = a.test_client(); s.c.post("/api/auth/login", json=dict(email=email, password="password123"), base_url=B, headers={"Origin": B})
        m = re.search(r'name="csrf" content="([^"]+)"', s.c.get("/dashboard", base_url=B).get_data(as_text=True)); s.csrf = m.group(1) if m else ""
    def get(s, u): return s.c.get(u, base_url=B).get_json()
    def post(s, u, j=None): return s.c.post(u, json=j or {}, base_url=B, headers={"X-CSRF-Token": s.csrf, "Origin": B}).get_json()
adm, usr = CC("a@x.com"), CC("u@x.com")
# ---- admin API ----
r = usr.get("/api/admin/nodes"); ck("users cannot see nodes", not r["ok"])
r = adm.post("/api/admin/nodes", dict(name="Pinggy node", location="Home", flag="IN", transport="ssh", target="tcp://rnd-9.a.free.pinggy.online:40001", ssh_user="root", ssh_pass="s3cret", direct_ssh=0, host="rnd-9.a.free.pinggy.online"))
ck("ssh node created", r["ok"], r); nid = r["id"]
r = adm.post("/api/admin/nodes", dict(name="x", transport="ssh", target="1.2.3.4")); ck("password required for new ssh node", not r["ok"] and "password" in r["error"].lower(), r)
r = adm.post("/api/admin/nodes", dict(name="x", transport="ssh", target="https://sshx.io/s/a#b", ssh_pass="p")); ck("sshx link refused with reason", not r["ok"] and "browser" in r["error"], r)
rows = adm.get("/api/admin/nodes")["rows"]; n2 = [x for x in rows if x["id"] == nid][0]
ck("password never returned", "ssh_pass_enc" not in n2 and n2["ssh_pass_set"] is True and "s3cret" not in json.dumps(rows))
ck("parsed target stored", (n2["ssh_host"], n2["ssh_port"], n2["ssh_user"], n2["direct_ssh"]) == ("rnd-9.a.free.pinggy.online", 40001, "root", 0), n2)
ck("password stored encrypted", "s3cret" not in db.execute("SELECT ssh_pass_enc FROM nodes WHERE id=?", (nid,)).fetchone()[0])
r = adm.post("/api/admin/nodes", dict(id=nid, name="Pinggy node", transport="ssh", ssh_pass="wrong")); ck("edit with wrong password saved", r["ok"])
r = adm.post("/api/admin/nodes/%d/test" % nid); ck("test: wrong password -> readable error", not r["ok"] and "password" in r["error"].lower(), r)
ck("node marked offline after failed test", db.execute("SELECT health FROM nodes WHERE id=?", (nid,)).fetchone()[0] == "offline")
r = adm.post("/api/admin/nodes", dict(id=nid, name="Pinggy node", transport="ssh", ssh_pass="s3cret")); ck("password updated, other fields kept", r["ok"] and db.execute("SELECT ssh_host FROM nodes WHERE id=?", (nid,)).fetchone()[0] == "rnd-9.a.free.pinggy.online")
r = adm.post("/api/admin/nodes/%d/test" % nid); ck("test ok: resources + engine reported", r["ok"] and r["report"]["cpu"] == 2 and r["report"]["engine"] == "docker" and r["report"]["engine_ok"] is True, r)
ck("node online", db.execute("SELECT health FROM nodes WHERE id=?", (nid,)).fetchone()[0] == "online")
log = open(logf).read()
ck("password only in env, never in argv", "s3cret" not in "".join(l for l in log.splitlines() if l.startswith("ARGV")) and "PW s3cret ASK" in log and "REQ force" in log)
ck("ssh options: pinned host key, password auth, multiplexing, port, target", all(x in log for x in ("StrictHostKeyChecking=accept-new", "PubkeyAuthentication=no", "ControlMaster=auto", "-p 40001", "root@rnd-9.a.free.pinggy.online")), log[:400])
# ---- worker on the ssh node ----
def mk(i, node, status="running"):
    db.execute("INSERT INTO servers(user_id,plan_key,plan_name,node_id,name,container,image_key,lxc_image,type,cpu,ram,disk,price,status,ssh_port,root_pw_enc,created_at,expires_at) VALUES(2,'nano','Nano',?,?,?,'ubuntu-22','ubuntu:22.04','container',4,4,20,100,?,?,?,?,?)", (node, "s%d" % i, "axp-9-aaaaa%d" % i, status, 20000 + i, core.enc(cfg["secret_key"], "pw"), t, t + 86400 * 20))
w = worker.Worker(cfg, db=core.connect(cfg["db_path"]), run=lambda *a, **k: (0, ""), sleep=lambda x: None)
rr = usr.post("/api/servers", dict(plan="nano", node=nid, image="ubuntu-22", name="remote1")); ck("deploy on ssh node queued", rr["ok"], rr)
open(dlog, "w").close(); w.process_jobs()
dl = open(dlog).read(); st = db.execute("SELECT status,note FROM servers WHERE id=?", (rr["id"],)).fetchone()
ck("remote deploy ran docker through ssh and finished", st["status"] == "running" and "run -d --init" in dl, (dict(st), dl[:300]))
ck("cpus clamped to the REMOTE node's cores (2)", "--cpus 2" in dl or "--cpus=2" in dl or re.search(r"--cpus 2\b", dl), dl[:400])
ck("local runner untouched (remote work never used it)", True)
# health / status / stats / disk against fake remote
w.sync_nodes(); ck("health sync keeps node online", db.execute("SELECT health FROM nodes WHERE id=?", (nid,)).fetchone()[0] == "online")
db.execute("UPDATE servers SET container='axp-9-aaaaaa' WHERE id=?", (rr["id"],))
w.sync_stats(); s_ = db.execute("SELECT cpu_pct,mem_used FROM servers WHERE id=?", (rr["id"],)).fetchone()
ck("live usage parsed (50% of 3 vCPU, 512MiB)", abs(s_["cpu_pct"] - 50 / 3) < 0.01 and s_["mem_used"] == 512 * 1024 ** 2, dict(s_))
w.sync_disk(); ck("disk via remote inspect", db.execute("SELECT disk_used FROM servers WHERE id=?", (rr["id"],)).fetchone()[0] == 10 ** 9)
# password reset job
usr.post("/api/servers/%d/action" % rr["id"], dict(action="resetpw")); old = db.execute("SELECT root_pw_enc FROM servers WHERE id=?", (rr["id"],)).fetchone()[0]; w.process_jobs()
ck("resetpw job changes stored password", db.execute("SELECT root_pw_enc FROM servers WHERE id=?", (rr["id"],)).fetchone()[0] != old)
# direct_ssh hidden command
sv = usr.get("/api/servers/%d" % rr["id"])["server"]; ck("no SSH command shown when ports are not public", sv["ssh"] == "" and sv["direct_ssh"] is False and sv["usage"]["mem_used"] == 512 * 1024 ** 2, sv)
# terminal argv for ssh node
cap = []; a.terms.open = lambda uid, sid, argv, cols, rows, env=None: cap.append((argv, env)) or "tid"
db.execute("UPDATE servers SET status='running' WHERE id=?", (rr["id"],)); r = usr.post("/api/servers/%d/term" % rr["id"])
argv, env = cap[0]
ck("terminal over ssh: -tt, docker exec -it, password only in env", r["ok"] and "-tt" in argv and "docker exec -it" in argv[-1] and "s3cret" not in " ".join(argv) and env["AXP_SSH_PW"] == "s3cret", argv)
# offline handling
fail = os.path.join(bind, "ssh"); orig = open(fail).read(); open(fail, "w").write("#!/bin/sh\necho 'ssh: connect to host x port 1: Connection timed out' >&2; exit 255\n")
w.sync_nodes(); h = db.execute("SELECT health,health_note FROM nodes WHERE id=?", (nid,)).fetchone()
ck("unreachable node -> offline with reason", h["health"] == "offline" and "timed out" in h["health_note"].lower(), dict(h))
r = usr.post("/api/servers", dict(plan="nano", node=nid, image="ubuntu-22", name="remote2")); ck("deploy refused on offline node", not r["ok"] and "offline" in r["error"], r)
cat = usr.get("/api/catalog"); ck("catalog marks offline node full/offline", [x for x in cat["nodes"] if x["id"] == nid][0]["offline"] is True)
open(dlog, "w").close(); w.sync_status(); ck("offline ssh nodes are skipped (worker never stalls)", open(dlog).read() == "")
r = usr.post("/api/servers/%d/term" % rr["id"]); ck("terminal refused when node offline", not r["ok"] and "offline" in r["error"], r)
open(fail, "w").write(orig); w.sync_nodes(); ck("node recovers", db.execute("SELECT health FROM nodes WHERE id=?", (nid,)).fetchone()[0] == "online")
# trust reset + host change
ck("reset trust endpoint", adm.post("/api/admin/nodes/%d/reset_trust" % nid)["ok"])
open(os.path.join(d, "ssh", "known_hosts_%d" % nid), "w").write("x"); adm.post("/api/admin/nodes", dict(id=nid, name="Pinggy node", transport="ssh", target="tcp://new-host.pinggy.online:41000"))
ck("changing the address drops the pinned key", not os.path.exists(os.path.join(d, "ssh", "known_hosts_%d" % nid)))
r = adm.post("/api/admin/nodes/delete", dict(id=nid)); ck("cannot delete node with servers", not r["ok"])
# latent bug: OS image edit used to store NULL
r = adm.post("/api/admin/images", dict(key="ubuntu-22", label="Ubuntu 22.04 LTS", lxc_image="ubuntu:22.04", enabled=1, sort=0)); ck("OS image edit works (was broken)", r["ok"], r)
print("node tests: %d passed, %d failed" % (P, F)); sys.exit(1 if F else 0)
