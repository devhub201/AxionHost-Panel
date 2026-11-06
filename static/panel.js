"use strict";
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const CSRF = ($('meta[name=csrf]') || {}).content || "";
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = n => Number(n || 0).toLocaleString("en-IN");
const dt = t => new Date(t * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
const dday = t => new Date(t * 1000).toLocaleDateString([], { dateStyle: "medium" });
const left = t => { const d = t - Date.now() / 1000; if (d <= 0) return "expired"; const days = Math.floor(d / 86400); return days >= 1 ? days + "d left" : Math.max(1, Math.floor(d / 3600)) + "h left"; };
function toast(m, bad) { const e = document.createElement("div"); e.className = "toast" + (bad ? " bad" : ""); e.textContent = m; $("#toasts").appendChild(e); setTimeout(() => e.remove(), 3800); }
async function api(method, url, data) {
  let r, j = {};
  try { r = await fetch(url, { method, headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF }, body: data ? JSON.stringify(data) : undefined, credentials: "same-origin" }); j = await r.json(); }
  catch (e) { return { ok: false, error: r && r.status ? "Server error (" + r.status + ")" : "Network error" }; }
  if (r.status === 401 && !location.pathname.startsWith("/login")) location.href = "/login?next=" + encodeURIComponent(location.pathname);
  j.ok = r.ok && j.ok !== false; j.status = r.status; return j;
}
const setBal = b => { $$("#bal,#s-bolts,#have").forEach(e => e.textContent = b); };
function busy(btn, on) { if (btn) { btn.disabled = on; } }
function modal(html, onmount) { const ov = $("#ov") || (() => { const o = document.createElement("div"); o.className = "ov"; o.id = "ov"; o.hidden = true; o.innerHTML = '<div class="modal" id="mod"></div>'; document.body.appendChild(o); return o; })(); const m = $("#mod"); m.innerHTML = html; ov.hidden = false; ov.onclick = e => { if (e.target === ov) ov.hidden = true; }; $$("[data-close]", m).forEach(b => b.onclick = () => ov.hidden = true); if (onmount) onmount(m); return m; }
const closeModal = () => { const o = $("#ov"); if (o) o.hidden = true; };
const gb = b => b >= 1e9 ? (b / 1e9).toFixed(b >= 1e10 ? 1 : 2).replace(/\.0+$/, "") + " GB" : Math.max(0, Math.round(b / 1e6)) + " MB";
const diskLine = d => d.available ? gb(d.used_bytes) + " / " + d.total_gb + " GB used" : d.total_gb + " GB";
const diskCard = d => `<div class="card" style="margin-bottom:16px"><div class="row sp"><h3>Storage</h3><span class="pill">${d.available ? d.pct + "% used" : "usage unavailable"}</span></div><div class="d">Your server's own disk — not the host machine's.</div><div class="bar" style="height:10px;margin-bottom:14px"><i style="width:${d.available ? d.pct : 0}%"></i></div><div class="grid g3 keep"><div><div class="sub">Total</div><b>${d.total_gb} GB</b></div><div><div class="sub">Used</div><b>${d.available ? gb(d.used_bytes) : "—"}</b></div><div><div class="sub">Free</div><b>${d.available ? gb(d.free_bytes) : "—"}</b></div></div>${d.available ? "" : '<div class="sub" style="margin-top:10px">Usage is measured by the node every minute. It appears shortly after the server starts.</div>'}</div>`;
const pill = (st, susp) => { const s = susp ? "suspended" : st; return `<span class="pill ${esc(s)}"><i></i>${esc(s)}</span>`; };

const P = {};
P.home = () => {};
P["404"] = () => { const b = $("#back"); if (b) b.onclick = e => { if (history.length > 1) { e.preventDefault(); history.back(); } }; };
P.maintenance = () => {};

P.login = () => {
  const f = $("#f"); if (!f) return; let mode = f.dataset.mode === "register" ? "register" : "login";
  const set = m => { mode = m; $("#tl").classList.toggle("on", m === "login"); $("#tr").classList.toggle("on", m === "register"); $("#uw").classList.toggle("hide", m === "login"); $("#go").textContent = m === "login" ? "Sign in" : "Create account"; $("#ttl").textContent = m === "login" ? "Welcome back" : "Create your account"; $("#sub").textContent = m === "login" ? "Sign in to manage your servers" : "Start deploying in under a minute"; $("#pw").autocomplete = m === "login" ? "current-password" : "new-password"; $("#msg").textContent = ""; };
  $("#tl").onclick = () => set("login"); $("#tr").onclick = () => set("register"); set(mode);
  f.onsubmit = async e => {
    e.preventDefault(); const b = $("#go"); busy(b, true); $("#msg").textContent = "";
    const d = { email: $("#em").value, password: $("#pw").value, next: f.dataset.next }; if (mode === "register") d.username = $("#un").value;
    const r = await api("POST", "/api/auth/" + mode, d); busy(b, false);
    if (r.ok) location.href = r.next || "/dashboard"; else $("#msg").textContent = r.error || "Failed";
  };
};

function dailyBtn() {
  const b = $("#daily"); if (!b) return;
  b.onclick = async () => { busy(b, true); const r = await api("POST", "/api/daily"); busy(b, false); const m = $("#dmsg"); if (r.ok) { toast("+" + r.gained + " BOLTs claimed"); setBal(r.bolts); if (m) m.textContent = "Claimed! Come back in 24h"; } else { if (m) m.textContent = r.error; toast(r.error, true); } };
}

P.dashboard = () => {
  dailyBtn(); let timer;
  async function load() {
    const r = await api("GET", "/api/servers"); if (!r.ok) return; const s = r.servers;
    $("#s-srv").textContent = s.length; $("#s-run").textContent = s.filter(x => x.status === "running" && !x.suspended).length + " running";
    $("#list").innerHTML = s.length ? s.map(x => `<a class="card srv" href="/servers/${x.id}"><div class="grow"><div class="n">${esc(x.name)}</div><div class="m">${esc(x.plan_name)} • ${x.cpu} vCPU • ${x.ram} GB RAM • ${diskLine(x.disk_info)} • ${esc((x.node || {}).name || "")}</div><div class="m">${left(x.expires_at)} • renews ${dday(x.expires_at)}</div></div><div class="row">${pill(x.status, x.suspended)}<span class="btn sm w">Manage</span></div></a>`).join("")
      : '<div class="card empty"><b>No servers yet</b>Deploy your first server with BOLTs.<div style="margin-top:16px"><a class="btn w" href="/deploy">Deploy a server</a></div></div>';
    clearTimeout(timer); if (s.some(x => ["provisioning", "deleting"].includes(x.status))) timer = setTimeout(load, 4000);
  }
  load();
};

P.deploy = () => {
  let C, step = 0, sel = { plan: null, node: null, image: null, name: "" }; const W = $("#w");
  const titles = ["Plan", "OS & Location", "Credentials", "Review & Launch"];
  function bar() { $$("#steps div").forEach((d, i) => { d.className = i === step ? "on" : i < step ? "done" : ""; }); }
  const plan = () => C.plans.find(p => p.key === sel.plan);
  function nav(ok) { return `<div class="sum"><div class="row sp"><div class="m sub">${plan() ? esc(plan().name) + " • " + num(plan().price) + " BOLTs" : "Select a plan to continue"}</div><div class="row">${step > 0 ? '<button class="btn" id="bk">Back</button>' : ""}<button class="btn w" id="nx" ${ok ? "" : "disabled"}>${step === 3 ? "Launch server" : "Continue"}</button></div></div></div>`; }
  function render() {
    bar(); let h = "", ok = false;
    if (step === 0) { ok = !!sel.plan; h = `<div class="plans">${C.plans.map(p => { const out = p.stock >= 0 && p.left === 0; return `<div class="card pc ${sel.plan === p.key ? "sel" : ""} ${out ? "dis" : ""}" data-p="${esc(p.key)}"><h4>${esc(p.name)} ${p.type === "vm" ? '<span class="pill">KVM</span>' : ""}</h4><div class="p">${p.sale_pct ? `<s style="font-size:15px;color:var(--mut);font-weight:500">${num(p.bolts)}</s> ` : ""}${num(p.price)} <small>BOLTs${p.sale_pct ? " first month" : " / mo"}</small>${p.sale_pct ? ` <span class="pill running" style="vertical-align:middle"><i></i>-${p.sale_pct}%</span>` : ""}</div><div class="alt">${p.invites ? "or " + p.invites + " invites" : "&nbsp;"}${out ? " • sold out" : p.stock >= 0 ? " • " + p.left + " left" : ""}</div><ul><li>${p.cpu} vCPU</li><li>${p.ram} GB RAM</li><li>${p.disk} GB storage</li></ul></div>`; }).join("")}</div>`; }
    if (step === 1) { ok = !!(sel.node && sel.image); h = `<div class="card"><h3>Operating system</h3><div class="d">Pick the image your server boots with.</div><div class="opts">${C.images.map(i => `<div class="opt ${sel.image === i.key ? "sel" : ""}" data-i="${esc(i.key)}">${esc(i.label)}</div>`).join("")}</div></div><div class="card" style="margin-top:16px"><h3>Location</h3><div class="d">Closer to your users means lower latency.</div><div class="opts">${C.nodes.map(n => `<div class="opt ${sel.node === n.id ? "sel" : ""} ${n.full ? "dis" : ""}" data-n="${n.id}">${esc(n.flag)} ${esc(n.name)}<small>${esc(n.location)} ${n.full ? "• full" : ""}</small></div>`).join("")}</div></div>`; }
    if (step === 2) { ok = /^[A-Za-z0-9 ._-]{2,32}$/.test(sel.name); h = `<div class="card"><h3>Server name</h3><div class="d">Just a label so you can find it later.</div><input class="i" id="nm" maxlength="32" placeholder="my-server" value="${esc(sel.name)}"><div class="msg" id="nmsg">${sel.name && !ok ? "2-32 letters, numbers, space . _ -" : ""}</div><dl class="kv" style="margin-top:14px"><dt>Login</dt><dd>root</dd><dt>Password</dt><dd>Generated securely — revealed on the server page</dd><dt>Access</dt><dd>SSH on a dedicated port</dd></dl></div>`; }
    if (step === 3) { const p = plan(), n = C.nodes.find(x => x.id === sel.node), i = C.images.find(x => x.key === sel.image), enough = C.bolts >= p.price; ok = enough; h = `<div class="card"><h3>Review</h3><dl class="kv" style="margin-top:12px"><dt>Plan</dt><dd>${esc(p.name)} — ${p.cpu} vCPU • ${p.ram} GB • ${p.disk} GB</dd><dt>OS</dt><dd>${esc(i.label)}</dd><dt>Location</dt><dd>${esc(n.flag)} ${esc(n.name)}</dd><dt>Name</dt><dd>${esc(sel.name)}</dd><dt>Price</dt><dd><b>${num(p.price)} BOLTs</b>${p.sale_pct ? ` <s class="sub">${num(p.bolts)}</s> (sale -${p.sale_pct}%)` : ""} — then ${num(p.bolts)} / 30 days (auto-renews)</dd><dt>Balance after</dt><dd class="${enough ? "" : "neg"}">${num(C.bolts - p.price)} BOLTs</dd></dl>${enough ? "" : `<div class="msg">You need ${num(p.price - C.bolts)} more BOLTs. <a href="/earn" style="text-decoration:underline">Earn BOLTs</a></div>`}</div>`; }
    W.innerHTML = h + nav(ok);
    $$("[data-p]").forEach(e => e.onclick = () => { const p = C.plans.find(x => x.key === e.dataset.p); if (p.stock >= 0 && p.left === 0) return; sel.plan = p.key; render(); });
    $$("[data-i]").forEach(e => e.onclick = () => { sel.image = e.dataset.i; render(); });
    $$("[data-n]").forEach(e => e.onclick = () => { const n = C.nodes.find(x => x.id == e.dataset.n); if (n.full) return; sel.node = n.id; render(); });
    const nm = $("#nm"); if (nm) nm.oninput = () => { sel.name = nm.value.trim(); const v = /^[A-Za-z0-9 ._-]{2,32}$/.test(sel.name); $("#nx").disabled = !v; $("#nmsg").textContent = sel.name && !v ? "2-32 letters, numbers, space . _ -" : ""; };
    if ($("#bk")) $("#bk").onclick = () => { step--; render(); };
    $("#nx").onclick = async () => {
      if (step < 3) { step++; if (step === 2 && !sel.name) sel.name = (plan().name.replace(/^VPS /, "") + "-server").toLowerCase(); render(); window.scrollTo(0, 0); return; }
      const b = $("#nx"); busy(b, true); b.textContent = "Launching…";
      const r = await api("POST", "/api/servers", { plan: sel.plan, node: sel.node, image: sel.image, name: sel.name });
      if (r.ok) { toast("Deploying your server"); location.href = "/servers/" + r.id; } else { toast(r.error, true); busy(b, false); b.textContent = "Launch server"; }
    };
  }
  api("GET", "/api/catalog").then(r => { if (!r.ok) { W.innerHTML = '<div class="card empty"><b>Could not load plans</b>' + esc(r.error) + "</div>"; return; } C = r; setBal(r.bolts); render(); });
};

P.server = () => {
  const sid = $("#sv").dataset.sid, V = $("#sv"); let me = null, timer;
  window.AXT && AXT.init(sid, () => setTimeout(load, 400));
  async function load() {
    const r = await api("GET", "/api/servers/" + sid);
    if (!r.ok) { V.innerHTML = '<div class="card empty"><b>Server not found</b><a class="btn" href="/dashboard">Back to dashboard</a></div>'; return; }
    const s = r.server, busyNow = ["provisioning", "deleting"].includes(s.status), off = s.suspended || busyNow;
    const running = s.status === "running", nd = s.node || {}, daysLeft = Math.max(0, (s.expires_at - Date.now() / 1000) / 86400), pctLeft = Math.min(100, daysLeft / 30 * 100);
    const crow = (l, v, id) => `<div class="crow"><span>${l}</span><code>${esc(v)}</code><button data-copy="${esc(v)}" ${id ? 'id="' + id + '"' : ""}>Copy</button></div>`;
    V.innerHTML = `<div class="hero"><div class="top2"><div><a class="sub" href="/dashboard">← All servers</a><h1>${esc(s.name)}</h1>
      <div class="meta">${pill(s.status, s.suspended)}<span><b>${esc(s.plan_name)}</b></span><span>${esc((nd.flag || "") + " " + (nd.name || ""))}</span><span>${esc(s.image_key || "")}</span><span>#${s.id}</span></div></div>
      <div class="meta" style="text-align:right"><div><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase">Renews</div><b style="font-size:18px">${left(s.expires_at)}</b></div></div></div>
      <div class="acts"><button class="btn w" data-a="start" ${off || running ? "disabled" : ""}>▶ Start</button><button class="btn" data-a="restart" ${off || !running ? "disabled" : ""}>⟳ Restart</button><button class="btn" data-a="stop" ${off || !running ? "disabled" : ""}>■ Stop</button><button class="btn" id="cr" ${busyNow ? "disabled" : ""}>🔑 Root password</button></div></div>
    ${s.suspended ? `<div class="card" style="border-color:rgba(248,113,113,.4);margin-bottom:16px"><b>Suspended</b><div class="sub">${esc(s.suspend_reason)}${s.suspend_reason.startsWith("billing") ? " — renew to restore it." : ""}</div></div>` : ""}
    ${s.status === "provisioning" ? '<div class="card" style="margin-bottom:16px"><b>Setting up your server…</b><div class="sub">Pulling the image and installing SSH. Usually 1–3 minutes. This page updates automatically.</div><div class="bar" style="margin-top:12px"><i style="width:60%;animation:pulse 1.2s infinite"></i></div></div>' : ""}
    <div class="sp-tiles"><div class="tile"><div class="l">CPU</div><b>${s.cpu}<small>vCPU</small></b><div class="s">dedicated limit</div></div><div class="tile"><div class="l">Memory</div><b>${s.ram}<small>GB</small></b><div class="s">RAM, no swap</div></div>
      <div class="tile"><div class="l">Storage</div><b>${s.disk}<small>GB</small></b><div class="s">${s.disk_info && s.disk_info.available ? s.disk_info.pct + "% used" : "SSD"}</div></div><div class="tile"><div class="l">Price</div><b>${num(s.price)}<small>BOLTs</small></b><div class="s">per 30 days</div></div></div>
    <div class="sgrid"><div>
      <div id="tslot"></div><div id="uslot"></div><div id="gslot"></div><div id="xslot"></div><div id="tools"></div>
      ${diskCard(s.disk_info)}
      ${r.jobs.length ? `<div class="card"><h3>Recent activity</h3><div class="tl" style="margin-top:8px">${r.jobs.slice(0, 8).map(j => `<div class="${esc(j.status)}"><i></i><span>${esc(j.action)}${j.status === "failed" ? ' <span class="sub">— ' + esc(j.result) + "</span>" : ""}</span><em>${esc(j.status)} • ${dt(j.created)}</em></div>`).join("")}</div></div>` : ""}
    </div><div>
      <div class="card"><h3>Connect</h3><div class="d">Log in as <b>root</b>. Your login screen shows these specs and the AxionHost banner.</div><div class="conn">
        ${s.ssh ? crow("SSH", s.ssh, "cp") + crow("Host", nd.host || "-") + crow("Port", String(s.ssh_port || "-")) : (s.direct_ssh === false ? '<div class="sub">Direct SSH ports are not open on this node. Use the browser terminal, or start Pinggy / SSHX below.</div>' : "")}
        ${crow("User", "root")}</div></div>
      <div class="card"><h3>Billing</h3><div class="d">${num(s.price)} BOLTs every 30 days.</div><div class="exp ${daysLeft < 3 ? "low" : ""}"><i style="width:${pctLeft}%"></i></div><div class="row sp"><span class="sub">${dt(s.expires_at)}</span><b>${left(s.expires_at)}</b></div>
        <div class="row sp" style="margin-top:14px"><button class="btn w sm" id="rn" ${busyNow ? "disabled" : ""}>Renew now (+30 days)</button><label class="row" style="gap:8px;font-size:13px;color:var(--mut)">Auto-renew <span class="sw"><input type="checkbox" id="ar" ${s.auto_renew ? "checked" : ""}><span></span></span></label></div></div>
      <div class="card"><h3>Details</h3><dl class="kvl"><dt>Container</dt><dd><code>${esc(s.container)}</code></dd><dt>Image</dt><dd>${esc(s.image_key || "-")}</dd><dt>Type</dt><dd>${esc(s.type)}</dd><dt>Created</dt><dd>${dday(s.created_at)}</dd></dl></div>
      <div class="card"><h3>Manage</h3><div class="d">Rename, reinstall or delete.</div><div class="row"><input class="i grow" id="rn2" value="${esc(s.name)}" maxlength="32"><button class="btn sm" id="rns">Save</button></div>
        <div class="row" style="margin-top:12px"><button class="btn sm" id="re" ${busyNow || (s.suspended && !s.suspend_reason.startsWith("billing")) ? "disabled" : ""}>Reinstall</button><button class="btn sm red" id="del" ${busyNow ? "disabled" : ""}>Delete</button></div></div>
    </div></div>`;
    $$("[data-a]").forEach(b => b.onclick = () => act(b.dataset.a));
    window.AXT && AXT.update(s, r.tools || {}); window.AXG && AXG.mount(sid, s.status === "running"); window.AXE && AXE.mount(sid, s);
    $$("[data-copy]").forEach(b => b.onclick = () => { navigator.clipboard && navigator.clipboard.writeText(b.dataset.copy).then(() => toast("Copied")); });
    $("#cr").onclick = creds; $("#rn").onclick = renew; $("#del").onclick = del; $("#re").onclick = reinstall;
    $("#ar").onchange = async e => { const x = await api("POST", `/api/servers/${sid}/autorenew`, { on: e.target.checked }); toast(x.ok ? "Auto-renew " + (e.target.checked ? "on" : "off") : x.error, !x.ok); };
    $("#rns").onclick = async () => { const x = await api("POST", `/api/servers/${sid}/rename`, { name: $("#rn2").value }); toast(x.ok ? "Saved" : x.error, !x.ok); if (x.ok) load(); };
    clearTimeout(timer); timer = setTimeout(load, busyNow || r.jobs.some(j => ["queued", "running"].includes(j.status)) ? 3500 : 20000);
    if (s.status === "deleting" || s.status === "deleted") timer = setTimeout(() => api("GET", "/api/servers/" + sid).then(x => { if (!x.ok) location.href = "/dashboard"; }), 4000);
  }
  async function act(a, extra) { const r = await api("POST", `/api/servers/${sid}/action`, Object.assign({ action: a }, extra)); toast(r.ok ? "Queued: " + a : r.error, !r.ok); if (r.ok) setTimeout(load, 700); }
  async function renew() { const r = await api("POST", `/api/servers/${sid}/renew`); toast(r.ok ? "Renewed for 30 days" : r.error, !r.ok); if (r.ok) { const m = await api("GET", "/api/me"); if (m.ok) setBal(m.user.bolts); load(); } }
  function del() { modal(`<h3>Delete server?</h3><p class="sub">This permanently destroys the server and all its data. No refund.</p><div class="row" style="margin-top:18px;justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn red" id="y">Delete forever</button></div>`, m => $("#y", m).onclick = () => { closeModal(); act("delete"); }); }
  async function reinstall() { const c = await api("GET", "/api/catalog"); modal(`<h3>Reinstall server</h3><p class="sub">Wipes everything and installs a fresh OS. A new root password is generated.</p><label class="f">Operating system</label><select class="i" id="os">${c.images.map(i => `<option value="${esc(i.key)}">${esc(i.label)}</option>`).join("")}</select><div class="row" style="margin-top:18px;justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn red" id="y">Reinstall</button></div>`, m => $("#y", m).onclick = () => { const os = $("#os", m).value; closeModal(); act("reinstall", { image: os }); }); }
  async function creds() {
    if (!me) { const m = await api("GET", "/api/me"); me = m.user; }
    modal(`<h3>Root credentials</h3>${me.has_password ? '<p class="sub">Confirm your account password to reveal.</p><label class="f">Account password</label><input class="i" id="pw" type="password" autocomplete="current-password">' : '<p class="sub">Keep these private.</p>'}<div id="out"></div><div class="msg" id="mm"></div><div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn" data-close>Close</button><button class="btn w" id="y">Reveal</button></div>`, m => $("#y", m).onclick = async () => {
      const r = await api("POST", `/api/servers/${sid}/credentials`, { password: me.has_password ? $("#pw", m).value : "" });
      if (!r.ok) { $("#mm", m).textContent = r.error; return; } $("#mm", m).textContent = "";
      $("#out", m).innerHTML = `<dl class="kv" style="margin-top:14px"><dt>User</dt><dd>root</dd><dt>Password</dt><dd><code id="pwv">${esc(r.password)}</code></dd><dt>SSH</dt><dd><code>${esc(r.ssh)}</code></dd></dl>`; $("#y", m).remove(); });
  }
  load();
};

P.earn = () => {
  dailyBtn();
  async function q() {
    const r = await api("GET", "/api/quests"); if (!r.ok) return;
    $("#quests").innerHTML = r.quests.map(x => `<div class="row sp"><div class="grow"><b style="font-size:14px">${esc(x.title)}</b><div class="sub">${esc(x.blurb)}</div></div><button class="btn sm ${x.ready ? "w" : ""}" data-q="${esc(x.key)}" ${x.ready ? "" : "disabled"}>${x.claimed ? "Claimed ✓" : x.ready ? "Claim +" + x.bolts : "+" + x.bolts}</button></div>`).join("") || '<div class="sub">No quests right now.</div>';
    $$("[data-q]").forEach(b => b.onclick = async () => { const x = await api("POST", "/api/quests/claim", { key: b.dataset.q }); toast(x.ok ? "+" + x.gained + " BOLTs" : x.error, !x.ok); if (x.ok) { setBal(x.bolts); q(); } });
  }
  q(); promoForm(); ref();
};
async function ref() { const r = await api("GET", "/api/referral"); if (!r.ok) return; $("#reflink").textContent = r.link; $("#refd").textContent = `Friends get +${r.new_bonus} BOLTs on signup. You get +${r.reward} BOLTs after their first server.`; $("#refst").innerHTML = `<span><b style="color:#fff">${r.joined}</b> joined</span><span><b style="color:#fff">${r.paid}</b> launched a server</span><span><b style="color:#fff">${r.earned}</b> BOLTs earned</span>`; $("#refcp").onclick = () => navigator.clipboard && navigator.clipboard.writeText(r.link).then(() => toast("Link copied")); }
function promoForm() {
  const f = $("#promo"); if (!f) return;
  f.onsubmit = async e => { e.preventDefault(); const r = await api("POST", "/api/promo/redeem", { code: $("#code").value }); const m = $("#pmsg"); m.className = "msg" + (r.ok ? " ok" : ""); m.textContent = r.ok ? "+" + r.gained + " BOLTs added!" : r.error; if (r.ok) { setBal(r.bolts); $("#code").value = ""; } };
}

P.billing = () => {
  let off = 0;
  api("GET", "/api/servers").then(r => { if (!r.ok) return; const s = r.servers; $("#mc").textContent = num(s.reduce((a, x) => a + x.price, 0));
    $("#ren").innerHTML = s.length ? s.map(x => `<div class="row sp"><div><b>${esc(x.name)}</b><div class="sub">${dday(x.expires_at)} • ${left(x.expires_at)}</div></div><div class="row"><span class="pill">${num(x.price)} BOLTs</span><a class="btn sm" href="/servers/${x.id}">Manage</a></div></div>`).join("") : '<div class="sub">No servers yet.</div>'; });
  async function more() { const r = await api("GET", "/api/ledger?offset=" + off); if (!r.ok) return; off += r.rows.length;
    $("#led").insertAdjacentHTML("beforeend", r.rows.map(x => `<tr><td class="sub">${dt(x.created)}</td><td>${esc(x.reason)}</td><td class="${x.delta >= 0 ? "pos" : "neg"}">${x.delta >= 0 ? "+" : ""}${num(x.delta)}</td><td>${num(x.balance)}</td></tr>`).join(""));
    if (r.rows.length < 50) $("#more").classList.add("hide"); if (!off) $("#led").innerHTML = '<tr><td colspan="4" class="sub">No transactions yet.</td></tr>'; }
  $("#more").onclick = more; more();
};

P.settings = () => {
  promoForm();
  async function load() {
    const r = await api("GET", "/api/me"); if (!r.ok) return; const u = r.user, L = $("#links"), has = r.methods.length;
    const row = (name, icon, linked, label, href, key, avail) => `<div class="row sp card" style="padding:14px 16px;background:var(--s1)"><div class="row"><svg width="20" height="20"><use href="#${icon}"/></svg><div><b style="font-size:14px">${name}</b><div class="sub">${esc(label)}</div></div></div>${linked ? `<button class="btn sm red" data-un="${key}" ${has < 2 ? "disabled title='Link another method first'" : ""}>Unlink</button>` : avail ? `<a class="btn sm w" href="${href}">Link</a>` : '<span class="sub">Not set up</span>'}</div>`;
    L.innerHTML = row("Discord", "dc", !!u.discord, u.discord ? u.discord.name : "Not linked", "/auth/discord?next=/settings", "discord", L.dataset.d === "1")
      + row("Google", "gg", !!u.google, u.google ? u.google.email : "Not linked", "/auth/google?next=/settings", "google", L.dataset.g === "1")
      + `<div class="row sp card" style="padding:14px 16px;background:var(--s1)"><div><b style="font-size:14px">Email &amp; password</b><div class="sub">${u.has_password ? esc(u.email) : "Not set"}</div></div><span class="pill">${u.has_password ? "Active" : "Add below"}</span></div>`;
    $$("[data-un]").forEach(b => b.onclick = async () => { const x = await api("POST", "/api/account/unlink", { provider: b.dataset.un }); $("#m2").textContent = x.ok ? "" : x.error; if (x.ok) load(); });
    $("#sess").innerHTML = r.sessions.map(s => `<div class="row sp"><div><b style="font-size:13.5px">${esc((s.ua || "Unknown device").slice(0, 60))}</b><div class="sub">${esc(s.ip)} • ${dt(s.created)}</div></div>${s.current ? '<span class="pill">This device</span>' : ""}</div>`).join("");
  }
  $("#sun").onclick = async () => { const r = await api("POST", "/api/account/username", { username: $("#un").value }); const m = $("#m1"); m.className = "msg" + (r.ok ? " ok" : ""); m.textContent = r.ok ? "Saved" : r.error; };
  $("#cred").onsubmit = async e => { e.preventDefault(); const r = await api("POST", "/api/account/credentials", { email: $("#e").value, password: $("#np").value, current: $("#cp") ? $("#cp").value : "" }); const m = $("#m3"); m.className = "msg" + (r.ok ? " ok" : ""); m.textContent = r.ok ? "Saved. Other devices were signed out." : r.error; if (r.ok) setTimeout(() => location.reload(), 900); };
  $("#rev").onclick = async () => { const r = await api("POST", "/api/account/sessions/revoke"); toast(r.ok ? "Other devices signed out" : r.error, !r.ok); load(); };
  $("#out").onclick = async () => { await api("POST", "/api/auth/logout"); location.href = "/"; };
  load();
};

document.addEventListener("DOMContentLoaded", () => { const f = P[document.body.dataset.page]; if (f) f(); });
