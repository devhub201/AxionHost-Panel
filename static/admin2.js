"use strict";
/* Admin extras: custom SSH nodes, node health on Overview, give-BOLTs-to-all, admin console into any VPS. */
(() => {
const A = window.AXADM; if (!A) return;
const { S, pane, hdr, tbl, yn, canW, isOwner } = A;
const gb = b => b ? (b / 1073741824).toFixed(b < 1e10 ? 1 : 0) + " GB" : "-";
const bar = (p, t) => `<div style="height:6px;border-radius:4px;background:rgba(255,255,255,.08);overflow:hidden;margin-top:4px"><i style="display:block;height:100%;width:${Math.max(2, Math.min(100, p))}%;background:${p > 90 ? "#ff5d5d" : p > 70 ? "#f5b942" : "#4ade80"}"></i></div>`;
const hp = h => `<span class="pill ${h === "online" ? "running" : ""}"><i></i>${esc(h || "unknown")}</span>`;

/* ---------- Overview: wrap the original and add node health + give-all ---------- */
const ov = S.overview;
S.overview = async () => {
  await ov(); const r = await api("GET", "/api/admin/stats"); if (!r.ok || A.pane.querySelector("#nh")) return;
  const nodes = r.nodes || [];
  const box = document.createElement("div"); box.id = "nh"; box.style.marginTop = "16px";
  box.innerHTML = `<div class="card"><div class="row sp"><div><h3>Nodes</h3><div class="d">Health and capacity</div></div><div class="row" style="gap:8px">${canW ? '<button class="btn sm" id="sl">🏷 Run a sale</button>' : ""}${isOwner ? '<button class="btn w sm" id="ga">🎁 Give BOLTs to everyone</button>' : ""}</div></div>
   <div class="grid g2" style="margin-top:12px">${nodes.map(n => { const used = n.disk ? (n.disk - (n.free || 0)) / n.disk * 100 : 0, sp = n.cap ? n.servers / n.cap * 100 : 0;
     return `<div class="card" style="background:rgba(255,255,255,.03)"><div class="row sp"><b>${esc(n.flag || "")} ${esc(n.name)}</b>${hp(n.health)}</div><div class="sub">${n.transport === "ssh" ? "SSH node" : "This machine"} • ${n.cpu || "?"} vCPU • ${gb(n.ram)} RAM${n.load != null ? " • load " + n.load : ""}</div>
     <div class="sub" style="margin-top:8px">Servers ${n.servers}/${n.cap}</div>${bar(sp)}<div class="sub" style="margin-top:8px">Disk ${gb(n.disk - (n.free || 0))} / ${gb(n.disk)}</div>${bar(used)}</div>`; }).join("") || '<div class="sub">No nodes</div>'}</div></div>`;
  A.pane.appendChild(box);
  const sl = $("#sl"); if (sl) sl.onclick = () => modal(`<h3>Run a sale</h3><p class="sub">Discounts the first month of new deploys. Set 0% to end a sale.</p><label class="f">Discount %</label><input class="i" id="slp" type="number" min="0" max="90" value="20"><label class="f">Lasts (hours)</label><input class="i" id="slh" type="number" min="1" value="48"><div class="msg" id="slm"></div><div class="row" style="margin-top:14px;justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn w" id="slok">Apply to all plans</button></div>`, m => $("#slok", m).onclick = async () => { const x = await api("POST", "/api/admin/sale", { pct: +$("#slp", m).value, hours: +$("#slh", m).value, plan: "all" }); if (x.ok) { closeModal(); toast("Sale updated on " + x.plans + " plans"); } else { $("#slm", m).textContent = x.error; $("#slm", m).className = "msg"; } });
  const g = $("#ga"); if (g) g.onclick = () => modal(`<h3>Give BOLTs to everyone</h3><p class="sub">Credits every non-banned account. Recorded in the ledger and audit log. Max 1,000,000 each.</p>
    <label class="f">Amount each</label><input class="i" id="gam" type="number" min="1" placeholder="e.g. 50"><label class="f">Reason (shown in ledger)</label><input class="i" id="grs" placeholder="Diwali gift">
    <label class="f row sp" style="margin-top:14px">Only users with Discord linked<span class="sw"><input type="checkbox" id="gdo"><i></i></span></label><div class="msg" id="gm"></div>
    <div class="row" style="margin-top:14px;justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn w" id="gok">Send</button></div>`, m => {
      $("#gok", m).onclick = async () => { const amt = parseInt($("#gam", m).value, 10); if (!(amt > 0)) { $("#gm", m).textContent = "Enter an amount"; $("#gm", m).className = "msg"; return; }
        if (!confirm("Give " + amt + " BOLTs to every user?")) return;
        const x = await api("POST", "/api/admin/giveall", { amount: amt, reason: $("#grs", m).value, discord_only: $("#gdo", m).checked });
        $("#gm", m).className = "msg" + (x.ok ? " ok" : ""); $("#gm", m).textContent = x.ok ? "Sent to " + x.users + " users" : x.error; };
    });
};

/* ---------- Nodes (custom: local + any SSH / Pinggy host with password) ---------- */
function nodeForm(old, reload) {
  const o = old || { transport: "ssh", max_servers: 20, enabled: 1, direct_ssh: 1, listen: "0.0.0.0", ssh_port: 22, ssh_user: "root" };
  const tgt = old && old.ssh_host ? `${old.ssh_host}:${old.ssh_port}` : "";
  modal(`<h3>${old ? "Edit node" : "Add node"}</h3>
  <label class="f">Name</label><input class="i" id="nn" value="${esc(o.name || "")}" placeholder="Mumbai-1">
  <div class="grid g2"><div><label class="f">Location</label><input class="i" id="nl" value="${esc(o.location || "")}" placeholder="India"></div><div><label class="f">Flag code</label><input class="i" id="nf" value="${esc(o.flag || "")}" placeholder="IN"></div></div>
  <label class="f">Connection</label><select class="i" id="nt"><option value="ssh" ${o.transport === "ssh" ? "selected" : ""}>SSH (remote machine / Pinggy)</option><option value="local" ${o.transport !== "ssh" ? "selected" : ""}>This machine</option></select>
  <div id="sshf">
    <label class="f">SSH address</label><input class="i" id="ntg" value="${esc(tgt)}" placeholder="host:port  •  tcp://abc.a.pinggy.link:12345  •  ssh root@1.2.3.4 -p 2222" autocapitalize="off" autocomplete="off">
    <div class="sub" style="margin-top:4px">Paste a Pinggy address (run <code>ssh -p 443 -R0:localhost:22 tcp@free.pinggy.io</code> on the node) or any SSH host. sshx links cannot be used here.</div>
    <div class="grid g2"><div><label class="f">SSH user</label><input class="i" id="nu" value="${esc(o.ssh_user || "root")}" autocapitalize="off"></div><div><label class="f">SSH password ${old && old.ssh_pass_set ? "(saved – leave blank to keep)" : ""}</label><input class="i" id="np" type="password" autocomplete="new-password"></div></div>
    <label class="f row sp" style="margin-top:12px">Users can SSH straight into VPS here<span class="sw"><input type="checkbox" id="nd" ${o.direct_ssh ? "checked" : ""}><i></i></span></label>
    <div class="sub">Turn off for Pinggy nodes – their VPS SSH ports are not reachable from outside.</div></div>
  <div class="grid g2"><div><label class="f">Public host / IP</label><input class="i" id="nh2" value="${esc(o.host || "")}" placeholder="shown to users"></div><div><label class="f">Max servers</label><input class="i" id="nm" type="number" value="${o.max_servers || 20}"></div></div>
  <label class="f row sp" style="margin-top:12px">Enabled<span class="sw"><input type="checkbox" id="ne" ${o.enabled ? "checked" : ""}><i></i></span></label>
  <div class="msg" id="nmsg"></div>
  <div class="row" style="margin-top:14px;justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn w" id="nsv">Save & test</button></div>`, m => {
    const msg = (t, ok) => { $("#nmsg", m).textContent = t; $("#nmsg", m).className = "msg" + (ok ? " ok" : ""); };
    const sync = () => $("#sshf", m).hidden = $("#nt", m).value !== "ssh"; $("#nt", m).onchange = sync; sync();
    $("#nsv", m).onclick = async () => {
      const ssh = $("#nt", m).value === "ssh", d = { id: old ? old.id : undefined, name: $("#nn", m).value, location: $("#nl", m).value, flag: $("#nf", m).value, host: $("#nh2", m).value,
        max_servers: +$("#nm", m).value || 20, enabled: $("#ne", m).checked ? 1 : 0, transport: ssh ? "ssh" : "local", direct_ssh: ssh && !$("#nd", m).checked ? 0 : 1 };
      if (ssh) { d.target = $("#ntg", m).value; d.ssh_user = $("#nu", m).value; if ($("#np", m).value) d.ssh_pass = $("#np", m).value; }
      msg("Saving…", true); const r = await api("POST", "/api/admin/nodes", d); if (!r.ok) return msg(r.error);
      msg("Saved. Testing connection…", true); const t = await api("POST", `/api/admin/nodes/${r.id}/test`);
      if (!t.ok) return msg("Saved, but the test failed: " + t.error);
      const p = t.report; msg(`Connected ✔  ${p.cpu} vCPU • ${gb(p.ram)} RAM • ${gb(p.free)} free • ${p.engine} ${p.engine_ok ? "ready" : "NOT installed"}`, p.engine_ok);
      setTimeout(() => { if (p.engine_ok) { closeModal(); } reload(); }, 1400);
    };
  });
}
S.nodes = async () => {
  A.cur("nodes"); const r = await api("GET", "/api/admin/nodes"); if (!r.ok) { pane.innerHTML = '<div class="card">' + esc(r.error) + "</div>"; return; }
  pane.innerHTML = hdr("Nodes", "Where VPS run. Add any SSH machine (or a Pinggy tunnel) with a password.", canW ? '<button class="btn w sm" id="new">+ Add node</button>' : "") +
    tbl(["ID", "Node", "Connection", "Resources", "Servers", "Status", ""], r.rows.map(n => `<tr><td>${n.id}</td><td><b>${esc((n.flag || "") + " " + n.name)}</b><div class="sub">${esc(n.location || "")}</div></td>
      <td>${n.transport === "ssh" ? `<code>${esc(n.ssh_user)}@${esc(n.ssh_host)}:${n.ssh_port}</code>` : "this machine"}</td><td class="sub">${n.cpu_total || "?"} vCPU<br>${gb(n.ram_total)} RAM<br>${gb(n.disk_free)} free</td>
      <td>${n.servers}/${n.max_servers}</td><td>${hp(n.health)}${n.health_note ? `<div class="sub" title="${esc(n.health_note)}">${esc(n.health_note.slice(0, 40))}</div>` : ""}${n.enabled ? "" : '<div class="sub">disabled</div>'}</td>
      <td style="white-space:nowrap;text-align:right">${canW ? `<button class="btn sm" data-t="${n.id}">Test</button> <button class="btn sm" data-e="${n.id}">Edit</button>${n.transport === "ssh" ? ` <button class="btn sm" data-r="${n.id}" title="Forget the saved host key">Reset trust</button>` : ""} <button class="btn sm red" data-d="${n.id}">✕</button>` : ""}</td></tr>`));
  if (!canW) return;
  $("#new").onclick = () => nodeForm(null, S.nodes);
  $$("[data-e]", pane).forEach(b => b.onclick = () => nodeForm(r.rows.find(x => x.id == b.dataset.e), S.nodes));
  $$("[data-t]", pane).forEach(b => b.onclick = async () => { b.disabled = true; b.textContent = "…"; const x = await api("POST", `/api/admin/nodes/${b.dataset.t}/test`); toast(x.ok ? (x.report.engine_ok ? "Node online" : "Connected, but " + x.report.engine + " is missing") : x.error, !x.ok || !x.report.engine_ok); S.nodes(); });
  $$("[data-r]", pane).forEach(b => b.onclick = async () => { if (!confirm("Forget the saved SSH host key? Only do this if the server was reinstalled or the Pinggy address changed.")) return; const x = await api("POST", `/api/admin/nodes/${b.dataset.r}/reset_trust`); toast(x.ok ? "Trust reset – test the node again" : x.error, !x.ok); });
  $$("[data-d]", pane).forEach(b => b.onclick = () => modal(`<h3>Delete node?</h3><p class="sub">Only possible when it has no servers.</p><div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn red" id="y">Delete</button></div>`, m => $("#y", m).onclick = async () => { const x = await api("POST", "/api/admin/nodes/delete", { id: +b.dataset.d }); if (x.ok) { closeModal(); S.nodes(); } else toast(x.error, true); }));
};


/* ---------- Tickets ---------- */
const when = t => new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
S.tickets = async (filter) => {
  A.cur("tickets"); filter = filter || "open"; const r = await api("GET", "/api/admin/tickets?status=" + filter); if (!r.ok) { pane.innerHTML = '<div class="card">' + esc(r.error) + "</div>"; return; }
  pane.innerHTML = hdr("Tickets", "User support requests. Replies are sent to the user by Discord DM.", `<select class="i" id="tf" style="width:auto"><option value="open" ${filter === "open" ? "selected" : ""}>Open</option><option value="all" ${filter === "all" ? "selected" : ""}>All</option><option value="closed" ${filter === "closed" ? "selected" : ""}>Closed</option></select>`) +
    tbl(["#", "Subject", "User", "Status", "Updated", ""], r.tickets.map(t => `<tr><td>${t.id}</td><td>${esc(t.subject)}</td><td>${esc(t.user || "")}</td><td>${hp(t.status === "answered" ? "online" : t.status === "closed" ? "offline" : "unknown").replace(/online|offline|unknown/, t.status)}</td><td class="sub">${when(t.updated)}</td><td style="text-align:right"><button class="btn sm" data-o="${t.id}">Open</button></td></tr>`));
  $("#tf").onchange = e => S.tickets(e.target.value);
  $$("[data-o]", pane).forEach(b => b.onclick = async () => {
    const id = +b.dataset.o, x = await api("GET", "/api/admin/tickets/" + id); if (!x.ok) return toast(x.error, true);
    const closed = x.ticket.status === "closed";
    modal(`<h3>#${id} ${esc(x.ticket.subject)} <span class="sub">${esc(x.ticket.user || "")}</span></h3><div id="amsgs" style="display:flex;flex-direction:column;gap:8px;margin:12px 0;max-height:50vh;overflow:auto">${x.messages.map(m => `<div style="align-self:${m.staff ? "flex-end" : "flex-start"};max-width:88%;background:${m.staff ? "rgba(107,123,255,.14)" : "rgba(255,255,255,.07)"};border:1px solid var(--line);border-radius:12px;padding:9px 12px"><div class="sub" style="font-size:11.5px">${esc(m.who)}${m.staff ? " (staff)" : ""} • ${when(m.at)}</div><div style="white-space:pre-wrap;word-break:break-word">${esc(m.body)}</div></div>`).join("")}</div>
      ${closed ? '<div class="sub">Closed.</div>' : '<textarea class="i" id="ar" rows="3" maxlength="4000" style="width:100%" placeholder="Reply to the user…"></textarea><div class="msg" id="am"></div><div class="row" style="margin-top:12px;justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn red" id="acl">Close</button><button class="btn w" id="asd">Reply</button></div>'}`, m => {
      const box = $("#amsgs", m); box.scrollTop = box.scrollHeight; if (closed) return;
      $("#asd", m).onclick = async () => { const y = await api("POST", `/api/admin/tickets/${id}/reply`, { body: $("#ar", m).value }); if (y.ok) { closeModal(); toast("Reply sent"); S.tickets(filter); } else { $("#am", m).textContent = y.error; $("#am", m).className = "msg"; } };
      $("#acl", m).onclick = async () => { const y = await api("POST", `/api/admin/tickets/${id}/close`); if (y.ok) { closeModal(); S.tickets(filter); } else toast(y.error, true); };
    });
  });
};

/* ---------- Admin console into any VPS (adds a button to the server modal) ---------- */
function console_(sid, name) {
  const u8 = b => Uint8Array.from(atob(b), c => c.charCodeAt(0));
  const m = modal(`<h3>Console · ${esc(name)} <span class="sub">#${sid}</span></h3><div class="sub">Admin access – logged in the audit log.</div><div id="ac" style="height:380px;margin-top:10px;background:#050507;border-radius:10px;padding:6px"></div>
    <div class="row" style="margin-top:10px;justify-content:flex-end"><button class="btn" data-close>Close</button></div>`);
  const T = new XTermBundle.Terminal({ cursorBlink: true, fontSize: 13, theme: { background: "#050507" } }), F = new XTermBundle.FitAddon(); T.loadAddon(F); T.open($("#ac", m));
  let tid = null, pos = 0, q = "", sending = false, live = true;
  const stop = () => { live = false; if (tid) api("POST", `/api/term/${tid}/close`); tid = null; };
  $("#ov").addEventListener("click", () => { if ($("#ov").hidden) stop(); });
  $$("[data-close]", m).forEach(b => b.addEventListener("click", stop));
  setTimeout(async () => {
    try { F.fit(); } catch (e) {}
    const r = await api("POST", `/api/admin/servers/${sid}/term`, { cols: T.cols, rows: T.rows }); if (!r.ok) { T.write("\r\n" + r.error + "\r\n"); return; }
    tid = r.tid; T.focus();
    T.onData(async d => { q += d; if (sending) return; sending = true; while (q && tid) { const c = q.slice(0, 8000); q = q.slice(8000); await api("POST", `/api/term/${tid}/in`, { d: c }); } sending = false; });
    while (live && tid) { const x = await api("GET", `/api/term/${tid}/out?pos=${pos}&wait=1`); if (!x.ok) { T.write("\r\n[session ended]\r\n"); break; } if (x.d) T.write(u8(x.d)); pos = x.pos; if (x.closed) { T.write("\r\n[session closed]\r\n"); break; } }
  }, 60);
}
if (canW) new MutationObserver(() => {
  const mod = document.getElementById("mod"); if (!mod || mod.querySelector("#acb")) return;
  const h = mod.querySelector("h3"), st = mod.querySelector("[data-a=start]"); if (!h || !st) return;
  const id = +(h.textContent.match(/#(\d+)/) || [])[1]; if (!id) return;
  const b = document.createElement("button"); b.className = "btn sm w"; b.id = "acb"; b.textContent = "Console"; b.onclick = () => console_(id, h.firstChild.textContent.trim()); st.parentNode.insertBefore(b, st);
}).observe(document.body, { childList: true, subtree: true });
})();
