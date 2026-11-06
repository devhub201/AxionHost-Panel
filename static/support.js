"use strict";
P.support = () => {
  let cur = null;
  const when = t => new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const pill_ = s => `<span class="pill ${s === "answered" ? "running" : ""}"><i></i>${s === "answered" ? "answered" : s}</span>`;
  async function list() {
    const r = await api("GET", "/api/tickets"); if (!r.ok) return;
    $("#tl").innerHTML = r.tickets.length ? r.tickets.map(t => `<div class="card" data-t="${t.id}" style="cursor:pointer;margin-bottom:10px;${cur === t.id ? "border-color:var(--line2)" : ""}"><div class="row sp"><b>#${t.id} ${esc(t.subject)}</b>${pill_(t.status)}</div><div class="sub">updated ${when(t.updated)}</div></div>`).join("") : '<div class="card"><div class="sub">No tickets yet.</div></div>';
    $$("[data-t]").forEach(e => e.onclick = () => open_(+e.dataset.t));
  }
  async function open_(id) {
    cur = id; const r = await api("GET", "/api/tickets/" + id); if (!r.ok) return toast(r.error, true);
    const t = r.ticket, closed = t.status === "closed";
    $("#tv").innerHTML = `<div class="card"><div class="row sp"><h3>#${t.id} ${esc(t.subject)}</h3>${pill_(t.status)}</div><div id="msgs" style="display:flex;flex-direction:column;gap:10px;margin:14px 0;max-height:52vh;overflow:auto">${r.messages.map(m => `<div style="align-self:${m.staff ? "flex-start" : "flex-end"};max-width:86%;background:${m.staff ? "rgba(107,123,255,.14)" : "rgba(255,255,255,.07)"};border:1px solid var(--line);border-radius:14px;padding:10px 13px"><div class="sub" style="font-size:11.5px">${m.staff ? "Support" : "You"} • ${when(m.at)}</div><div style="white-space:pre-wrap;word-break:break-word">${esc(m.body)}</div></div>`).join("")}</div>
      ${closed ? '<div class="sub">This ticket is closed.</div>' : `<textarea class="i" id="rb" rows="3" maxlength="4000" placeholder="Write a reply…" style="width:100%"></textarea><div class="msg" id="rm"></div><div class="row" style="justify-content:flex-end;margin-top:10px"><button class="btn" id="rc">Close ticket</button><button class="btn w" id="rs">Send</button></div>`}</div>`;
    const m = $("#msgs"); m.scrollTop = m.scrollHeight;
    if (!closed) {
      $("#rs").onclick = async () => { const x = await api("POST", `/api/tickets/${id}/reply`, { body: $("#rb").value }); if (x.ok) { open_(id); list(); } else { $("#rm").textContent = x.error; $("#rm").className = "msg"; } };
      $("#rc").onclick = async () => { if (!confirm("Close this ticket?")) return; const x = await api("POST", `/api/tickets/${id}/close`); if (x.ok) { open_(id); list(); } else toast(x.error, true); };
    }
    list();
  }
  $("#tnew").onclick = () => modal(`<h3>New ticket</h3><label class="f">Subject</label><input class="i" id="ts" maxlength="80" placeholder="Short summary"><label class="f">Describe the problem</label><textarea class="i" id="tb" rows="5" maxlength="4000" style="width:100%" placeholder="What happened? Include the server name or ID."></textarea><div class="msg" id="tm"></div><div class="row" style="margin-top:14px;justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn w" id="tok">Send</button></div>`, m => $("#tok", m).onclick = async () => {
    const x = await api("POST", "/api/tickets", { subject: $("#ts", m).value, body: $("#tb", m).value }); if (x.ok) { closeModal(); toast("Ticket sent"); await list(); open_(x.id); } else { $("#tm", m).textContent = x.error; $("#tm", m).className = "msg"; } });
  list();
};
