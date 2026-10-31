"use strict";
(() => {
const RANK = { user: 0, support: 1, admin: 2, owner: 3 }, ROLE = $("#adm").dataset.role, ME = +$("#adm").dataset.me, canW = RANK[ROLE] >= 2, isOwner = ROLE === "owner";
const pane = $("#pane"), side = $("#side");
const SECTIONS = [["overview", "Overview"], ["users", "Users"], ["servers", "Servers"], ["plans", "Plans"], ["nodes", "Nodes"], ["images", "OS images"], ["promos", "Promo codes"], ["quests", "Quests"], ["settings", "Site settings"], ["jobs", "Jobs"], ["ledger", "BOLTs ledger"], ["audit", "Audit log"]];
side.innerHTML = SECTIONS.map(([k, l]) => `<button data-s="${k}">${l}</button>`).join("");
const hdr = (t, d, extra = "") => `<div class="row sp" style="margin-bottom:16px"><div><h3 style="font-size:20px">${t}</h3><div class="sub">${d || ""}</div></div>${extra}</div>`;
const tbl = (cols, rows) => `<div class="tw"><table><thead><tr>${cols.map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.join("") || `<tr><td colspan="${cols.length}" class="sub">Nothing here yet.</td></tr>`}</tbody></table></div>`;
const yn = v => v ? '<span class="pill running"><i></i>on</span>' : '<span class="pill"><i></i>off</span>';

function show(name) {
  $$("#side button").forEach(b => b.classList.toggle("on", b.dataset.s === name)); if (location.hash.slice(1) !== name) history.replaceState(null, "", "#" + name);
  pane.innerHTML = '<div class="sk"></div>'; (S[name] || S.overview)();
}
side.onclick = e => { const b = e.target.closest("button"); if (b) show(b.dataset.s); };

function form(title, fields, vals, onsave, extraHtml = "") {
  modal(`<h3>${title}</h3>${fields.map(f => { const v = vals[f.k] ?? f.def ?? ""; const ro = f.ro ? "disabled" : ""; return f.t === "bool" ? `<label class="f row sp" style="margin-top:16px">${f.l}<span class="sw"><input type="checkbox" data-k="${f.k}" ${v ? "checked" : ""}><span></span></span></label>` : f.t === "select" ? `<label class="f">${f.l}</label><select class="i" data-k="${f.k}" ${ro}>${f.o.map(o => `<option ${o == v ? "selected" : ""}>${o}</option>`).join("")}</select>` : `<label class="f">${f.l}</label><input class="i" data-k="${f.k}" type="${f.t === "n" ? "number" : "text"}" value="${esc(v)}" ${ro} ${f.ph ? `placeholder="${esc(f.ph)}"` : ""}>`; }).join("")}${extraHtml}<div class="msg" id="fm"></div><div class="row" style="margin-top:14px;justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn w" id="sv">Save</button></div>`, m => {
    $("#sv", m).onclick = async () => { const d = {}; $$("[data-k]", m).forEach(el => { d[el.dataset.k] = el.type === "checkbox" ? (el.checked ? 1 : 0) : el.type === "number" ? Number(el.value) : el.value; }); const r = await onsave(d); if (r && r.ok) { closeModal(); toast("Saved"); } else $("#fm", m).textContent = (r && r.error) || "Failed"; };
  });
}

function editor(name, title, desc, pk, cols, fields, rowFn, defaults = {}) {
  return async () => {
    const r = await api("GET", "/api/admin/" + name); if (!r.ok) { pane.innerHTML = '<div class="card">' + esc(r.error) + "</div>"; return; }
    pane.innerHTML = hdr(title, desc, canW ? '<button class="btn w sm" id="new">+ New</button>' : "") + tbl(cols.concat(canW ? [""] : []), r.rows.map(x => `<tr>${rowFn(x).map(c => `<td>${c}</td>`).join("")}${canW ? `<td style="white-space:nowrap;text-align:right"><button class="btn sm" data-e="${esc(x[pk])}">Edit</button> <button class="btn sm red" data-d="${esc(x[pk])}">Delete</button></td>` : ""}</tr>`));
    const save = old => d => api("POST", "/api/admin/" + name, old ? Object.assign({ [pk]: old[pk] }, d) : d).then(x => { if (x.ok) S[cur](); return x; });
    if (canW) {
      $("#new").onclick = () => form("New " + title.toLowerCase().replace(/s$/, ""), fields, defaults, save(null));
      $$("[data-e]").forEach(b => b.onclick = () => { const old = r.rows.find(x => String(x[pk]) === b.dataset.e); form("Edit " + (old.name || old.title || old.code || old.key), fields.map(f => f.k === pk ? Object.assign({}, f, { ro: true }) : f), old, save(old)); });
      $$("[data-d]").forEach(b => b.onclick = () => modal(`<h3>Delete?</h3><p class="sub">This cannot be undone.</p><div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn red" id="y">Delete</button></div>`, m => $("#y", m).onclick = async () => { const x = await api("POST", `/api/admin/${name}/delete`, { [pk]: isNaN(b.dataset.d) ? b.dataset.d : Number(b.dataset.d) }); closeModal(); toast(x.ok ? "Deleted" : x.error, !x.ok); if (x.ok) S[cur](); }));
    }
  };
}
let cur = "overview";
const S = {};
S.overview = async () => {
  cur = "overview"; const r = await api("GET", "/api/admin/stats"); if (!r.ok) return; const mx = Math.max(1, ...r.days.map(d => d.users + d.servers));
  pane.innerHTML = hdr("Overview", "Live health of the panel.") + `<div class="grid g4 keep" style="margin-bottom:16px">
  <div class="card stat"><div class="l">Users</div><b>${num(r.users)}</b><div class="s">${r.banned} banned</div></div><div class="card stat"><div class="l">Servers</div><b>${num(r.servers)}</b><div class="s">${r.running} running • ${r.suspended} suspended</div></div>
  <div class="card stat"><div class="l">BOLTs in circulation</div><b>${num(r.circulating)}</b><div class="s">${num(r.earned30)} earned • ${num(r.spent30)} spent (30d)</div></div><div class="card stat"><div class="l">Jobs</div><b>${r.queued_jobs}</b><div class="s ${r.failed_jobs ? "neg" : ""}">${r.failed_jobs} failed</div></div></div>
  <div class="grid g2"><div class="card"><h3>Last 14 days</h3><div class="d">New users + new servers</div><div class="chart">${r.days.map(d => `<i style="height:${Math.max(2, (d.users + d.servers) / mx * 100)}%" title="${d.d}: ${d.users} users, ${d.servers} servers"></i>`).join("")}</div></div>
  <div class="card"><h3>Popular plans</h3><div class="d">Active servers by plan</div>${r.plans.map(p => `<div class="row sp" style="margin-bottom:8px"><span>${esc(p.name)}</span><b>${p.n}</b></div>`).join("") || '<div class="sub">No data</div>'}</div></div>`;
};
S.users = async function load(q = "") {
  cur = "users"; const r = await api("GET", "/api/admin/users?q=" + encodeURIComponent(q)); if (!r.ok) return;
  pane.innerHTML = hdr("Users", "Search by name, email, Discord or ID.", '<form id="sq" class="row"><input class="i" id="q" placeholder="Search…" value="' + esc(q) + '" style="width:200px"><button class="btn sm">Search</button></form>') + tbl(["ID", "User", "Login", "BOLTs", "Servers", "Role", ""], r.users.map(u => `<tr><td>#${u.id}</td><td><b>${esc(u.username)}</b>${u.banned ? ' <span class="pill failed"><i></i>banned</span>' : ""}</td><td class="sub">${u.discord ? "Discord " : ""}${u.google ? "Google " : ""}${u.has_password ? esc(u.email) : ""}</td><td>${num(u.bolts)}</td><td>${u.servers}</td><td>${u.role}</td><td style="text-align:right">${canW ? `<button class="btn sm" data-u="${u.id}">Manage</button>` : ""}</td></tr>`));
  $("#sq").onsubmit = e => { e.preventDefault(); load($("#q").value); };
  $$("[data-u]").forEach(b => b.onclick = () => { const u = r.users.find(x => x.id == b.dataset.u);
    modal(`<h3>${esc(u.username)} <span class="sub">#${u.id}</span></h3><dl class="kv" style="margin:12px 0"><dt>BOLTs</dt><dd>${num(u.bolts)}</dd><dt>Email</dt><dd>${esc(u.email || "-")}</dd><dt>Discord</dt><dd>${u.discord ? esc(u.discord.name) + " (" + esc(u.discord.id) + ")" : "-"}</dd><dt>Role</dt><dd>${u.role}</dd></dl>
    <label class="f">Add / remove BOLTs (negative removes)</label><div class="row"><input class="i grow" id="am" type="number" placeholder="e.g. 500 or -200"><input class="i grow" id="rs" placeholder="reason"><button class="btn w sm" id="ab">Apply</button></div>
    ${isOwner && u.id !== ME ? `<label class="f">Role</label><div class="row"><select class="i grow" id="ro">${["user", "support", "admin", "owner"].map(x => `<option ${x === u.role ? "selected" : ""}>${x}</option>`).join("")}</select><button class="btn sm" id="rb">Set role</button></div>` : ""}
    <div class="msg" id="um"></div><div class="row" style="margin-top:10px"><button class="btn sm" id="ki">Sign out all sessions</button>${u.banned ? '<button class="btn sm w" id="ub">Unban</button>' : '<button class="btn sm red" id="bn">Ban user</button>'}<span class="grow"></span><button class="btn sm" data-close>Close</button></div>`, m => {
      const go = async d => { const x = await api("POST", "/api/admin/users/" + u.id, d); $("#um", m).className = "msg" + (x.ok ? " ok" : ""); $("#um", m).textContent = x.ok ? "Done" : x.error; if (x.ok) setTimeout(() => { closeModal(); load(q); }, 500); };
      $("#ab", m).onclick = () => go({ bolts_delta: Number($("#am", m).value), reason: $("#rs", m).value });
      $("#ki", m).onclick = () => go({ kick: 1 }); if ($("#bn", m)) $("#bn", m).onclick = () => { const why = prompt("Ban reason?"); if (why !== null) go({ banned: true, ban_reason: why }); }; if ($("#ub", m)) $("#ub", m).onclick = () => go({ banned: false });
      if ($("#rb", m)) $("#rb", m).onclick = () => go({ role: $("#ro", m).value }); }); });
};
S.servers = async function load(q = "") {
  cur = "servers"; const r = await api("GET", "/api/admin/servers?q=" + encodeURIComponent(q)); if (!r.ok) return;
  pane.innerHTML = hdr("Servers", "Every server on the panel.", '<form id="sq" class="row"><input class="i" id="q" placeholder="Search…" value="' + esc(q) + '" style="width:200px"><button class="btn sm">Search</button></form>') + tbl(["ID", "Server", "Owner", "Plan", "Status", "Expires", ""], r.servers.map(s => `<tr><td>#${s.id}</td><td><b>${esc(s.name)}</b><div class="sub mono" style="border:0;padding:0;background:none">${esc(s.container)}</div></td><td>${esc(s.owner)}</td><td>${esc(s.plan_name)}<div class="sub">${diskLine(s.disk_info)}</div></td><td>${pill(s.status, s.suspended)}</td><td class="sub">${left(s.expires_at)}</td><td style="text-align:right">${canW ? `<button class="btn sm" data-s="${s.id}">Actions</button>` : ""}</td></tr>`));
  $("#sq").onsubmit = e => { e.preventDefault(); load($("#q").value); };
  $$("[data-s]").forEach(b => b.onclick = () => { const s = r.servers.find(x => x.id == b.dataset.s);
    modal(`<h3>${esc(s.name)} <span class="sub">#${s.id}</span></h3><p class="sub">Owner ${esc(s.owner)} • ${esc(s.suspend_reason || s.status)}</p><div class="row" style="margin:14px 0"><button class="btn sm" data-a="start">Start</button><button class="btn sm" data-a="stop">Stop</button><button class="btn sm" data-a="restart">Restart</button></div>
    <label class="f">Suspend / unsuspend</label><div class="row"><input class="i grow" id="rs" placeholder="reason (optional)"><button class="btn sm red" data-a="suspend">Suspend</button><button class="btn sm w" data-a="unsuspend">Unsuspend</button></div>
    <label class="f">Extend (days, negative shortens)</label><div class="row"><input class="i grow" id="dy" type="number" value="30"><button class="btn sm" data-a="extend">Apply</button></div>
    <div class="msg" id="sm"></div><div class="row" style="margin-top:10px"><button class="btn sm red" data-a="delete">Delete server</button><span class="grow"></span><button class="btn sm" data-close>Close</button></div>`, m => $$("[data-a]", m).forEach(x => x.onclick = async () => { if (x.dataset.a === "delete" && !confirm("Delete this server permanently?")) return; const d = await api("POST", "/api/admin/servers/" + s.id, { action: x.dataset.a, reason: $("#rs", m).value, days: Number($("#dy", m).value) }); $("#sm", m).className = "msg" + (d.ok ? " ok" : ""); $("#sm", m).textContent = d.ok ? "Done" : d.error; if (d.ok) setTimeout(() => { closeModal(); load(q); }, 500); })); });
};
S.plans = editor("plans", "Plans", "Edit prices, specs and stock. Changes apply instantly to the shop.", "key", ["Key", "Name", "BOLTs", "Invites", "vCPU", "RAM", "Disk", "Type", "Stock", "Shop"],
  [{ k: "key", l: "Key (a-z, 0-9, -)", t: "t" }, { k: "name", l: "Name", t: "t" }, { k: "bolts", l: "Price (BOLTs / 30 days)", t: "n" }, { k: "invites", l: "Equivalent invites (display)", t: "n" }, { k: "cpu", l: "vCPU", t: "n" }, { k: "ram", l: "RAM (GB)", t: "n" }, { k: "disk", l: "Disk (GB)", t: "n" }, { k: "type", l: "Type", t: "select", o: ["container", "vm"] }, { k: "blurb", l: "Tagline", t: "t" }, { k: "stock", l: "Stock (-1 = unlimited)", t: "n" }, { k: "sort", l: "Sort order", t: "n" }, { k: "enabled", l: "Visible in shop", t: "bool" }],
  x => [`<code>${esc(x.key)}</code>`, `<b>${esc(x.name)}</b>`, num(x.bolts), x.invites, x.cpu, x.ram + " GB", x.disk + " GB", x.type, x.stock < 0 ? "∞" : x.stock, yn(x.enabled)], { type: "container", stock: -1, enabled: 1, invites: 0, sort: 0 });
S.nodes = editor("nodes", "Nodes", "Servers are placed on nodes. Leave LXC remote empty for this machine.", "id", ["ID", "Name", "Location", "Host / IP", "Remote · listen", "Capacity", "On"],
  [{ k: "name", l: "Name", t: "t" }, { k: "location", l: "Location (country/city)", t: "t" }, { k: "flag", l: "Flag code (e.g. IN)", t: "t" }, { k: "host", l: "Public host / IP (shown in SSH command)", t: "t" }, { k: "lxc_remote", l: "LXC remote name (blank = local)", t: "t" }, { k: "listen", l: "SSH listen address (0.0.0.0 = IPv4, [::] = IPv6 + IPv4)", t: "select", o: ["0.0.0.0", "[::]"] }, { k: "max_servers", l: "Max servers", t: "n" }, { k: "enabled", l: "Enabled", t: "bool" }],
  x => [x.id, `<b>${esc(x.name)}</b>`, esc(x.flag + " " + x.location), esc(x.host || "-"), esc(x.lxc_remote || "local") + ' <span class="sub">' + esc(x.listen) + "</span>", x.max_servers, yn(x.enabled)], { max_servers: 50, enabled: 1, listen: "0.0.0.0" });
S.images = editor("images", "OS images", "Images users can pick. Format: ubuntu:22.04 or images:debian/12.", "key", ["Key", "Label", "LXC image", "On"],
  [{ k: "key", l: "Key", t: "t" }, { k: "label", l: "Label", t: "t" }, { k: "lxc_image", l: "LXC image", t: "t", ph: "ubuntu:22.04" }, { k: "sort", l: "Sort order", t: "n" }, { k: "enabled", l: "Enabled", t: "bool" }],
  x => [`<code>${esc(x.key)}</code>`, esc(x.label), `<code>${esc(x.lxc_image)}</code>`, yn(x.enabled)], { enabled: 1, sort: 0 });
S.promos = editor("promos", "Promo codes", "Users redeem these for BOLTs (Discord must be linked).", "code", ["Code", "BOLTs", "Used", "Expires", "On"],
  [{ k: "code", l: "Code", t: "t", ph: "DIWALI" }, { k: "bolts", l: "BOLTs per redeem", t: "n" }, { k: "max_uses", l: "Max uses", t: "n" }, { k: "expires", l: "Expires (unix time, 0 = never)", t: "n" }, { k: "note", l: "Note", t: "t" }, { k: "enabled", l: "Enabled", t: "bool" }],
  x => [`<code>${esc(x.code)}</code>`, "+" + num(x.bolts), x.uses + " / " + x.max_uses, x.expires ? dt(x.expires) : "never", yn(x.enabled)], { bolts: 100, max_uses: 1, expires: 0, enabled: 1 });
S.quests = editor("quests", "Quests", "One-time tasks that pay BOLTs.", "key", ["Key", "Title", "BOLTs", "Condition", "On"],
  [{ k: "key", l: "Key", t: "t" }, { k: "title", l: "Title", t: "t" }, { k: "blurb", l: "Description", t: "t" }, { k: "bolts", l: "Reward", t: "n" }, { k: "kind", l: "Condition", t: "select", o: ["link_discord", "set_email", "first_deploy"] }, { k: "enabled", l: "Enabled", t: "bool" }],
  x => [`<code>${esc(x.key)}</code>`, esc(x.title), "+" + x.bolts, esc(x.kind), yn(x.enabled)], { bolts: 100, kind: "link_discord", enabled: 1 });
const SETLBL = { site_name: "Site name", discord_invite: "Discord invite URL", announcement: "Announcement banner (empty = none)", maintenance: "Maintenance mode (whole site)", maintenance_pages: "Maintenance for pages (comma list: dashboard,deploy,earn,billing,settings)", registration_open: "Registration open", email_signup: "Email sign-up allowed", bolts_per_invite: "BOLTs per invite", daily_bolts: "Daily reward", booster_bonus: "Server booster bonus", signup_bonus: "Sign-up bonus", max_servers_per_user: "Max servers per user", grace_days: "Grace days", delete_after_days: "Delete expired servers after (days, 0 = never)", billing_days: "Billing period (days)", refund_on_fail: "Refund BOLTs if deploy fails", min_password: "Minimum password length", invite_min_age_days: "Invite must stay (days) before paying", maintenance_msg: "Maintenance message" };
const BOOLS = ["maintenance", "registration_open", "email_signup", "refund_on_fail"];
S.settings = async () => {
  cur = "settings"; const r = await api("GET", "/api/admin/settings"); if (!r.ok) return; const s = r.settings;
  pane.innerHTML = hdr("Site settings", "Economy, access and billing rules.") + `<div class="card"><div class="grid g2">${r.keys.filter(k => k !== "maintenance_msg").map(k => BOOLS.includes(k) ? `<label class="row sp card" style="padding:14px 16px;background:var(--s1)"><span>${SETLBL[k] || k}</span><span class="sw"><input type="checkbox" data-k="${k}" ${s[k] === "1" ? "checked" : ""} ${canW ? "" : "disabled"}><span></span></span></label>` : `<div><label class="f" style="margin-top:0">${SETLBL[k] || k}</label><input class="i" data-k="${k}" value="${esc(s[k])}" ${canW ? "" : "disabled"}></div>`).join("")}</div>${canW ? '<div class="msg" id="stm"></div><button class="btn w" id="sv" style="margin-top:8px">Save settings</button>' : ""}</div>`;
  if (canW) $("#sv").onclick = async () => { const d = {}; $$("[data-k]", pane).forEach(el => d[el.dataset.k] = el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value); const x = await api("POST", "/api/admin/settings", d); $("#stm").className = "msg" + (x.ok ? " ok" : ""); $("#stm").textContent = x.ok ? "Saved" : x.error; };
};
S.jobs = async () => {
  cur = "jobs"; const r = await api("GET", "/api/admin/jobs"); if (!r.ok) return;
  pane.innerHTML = hdr("Jobs", "Worker queue. Failed deploys are refunded automatically.") + tbl(["ID", "Server", "Action", "Status", "Result", "When", ""], r.jobs.map(j => `<tr><td>${j.id}</td><td>#${j.server_id}</td><td>${esc(j.action)}</td><td>${j.status === "failed" ? '<span class="pill failed"><i></i>failed</span>' : esc(j.status)}</td><td class="sub">${esc(j.result)}</td><td class="sub">${dt(j.updated)}</td><td>${j.status === "failed" && canW ? `<button class="btn sm" data-r="${j.id}">Retry</button>` : ""}</td></tr>`));
  $$("[data-r]").forEach(b => b.onclick = async () => { const x = await api("POST", `/api/admin/jobs/${b.dataset.r}/retry`); toast(x.ok ? "Re-queued" : x.error, !x.ok); S.jobs(); });
};
S.ledger = async () => { cur = "ledger"; const r = await api("GET", "/api/admin/ledger"); if (!r.ok) return; pane.innerHTML = hdr("BOLTs ledger", "Latest 80 transactions across all users.") + tbl(["When", "User", "Reason", "Amount", "Balance"], r.rows.map(x => `<tr><td class="sub">${dt(x.created)}</td><td>${esc(x.username)}</td><td>${esc(x.reason)}</td><td class="${x.delta >= 0 ? "pos" : "neg"}">${x.delta >= 0 ? "+" : ""}${num(x.delta)}</td><td>${num(x.balance)}</td></tr>`)); };
S.audit = async () => { cur = "audit"; const r = await api("GET", "/api/admin/audit"); if (!r.ok) return; pane.innerHTML = hdr("Audit log", "Every sensitive action, with who and from where.") + tbl(["When", "Actor", "Action", "Target", "Detail", "IP"], r.rows.map(x => `<tr><td class="sub">${dt(x.created)}</td><td>${esc(x.actor_name || "system")}</td><td><code>${esc(x.action)}</code></td><td>${esc(x.target)}</td><td class="sub">${esc(x.detail)}</td><td class="sub">${esc(x.ip)}</td></tr>`)); };
for (const k of ["plans", "nodes", "images", "promos", "quests"]) { const f = S[k]; S[k] = () => { cur = k; return f(); }; }
window.addEventListener("hashchange", () => { const n = location.hash.slice(1); if (S[n]) show(n); });
show((location.hash || "#overview").slice(1));
})();
