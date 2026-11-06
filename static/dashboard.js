"use strict";
/* Dashboard overview: stats, search + filters, live CPU/RAM per server, recent activity.
   Replaces P.dashboard from panel.js (loaded after it, only on the dashboard page). */
P.dashboard = () => {
  dailyBtn(); let timer, all = [], q = "", f = "all";
  const soon = x => x.expires_at - Date.now() / 1000 < 3 * 86400 && x.expires_at > Date.now() / 1000;
  const bar = p => `<div class="mini"><i style="width:${Math.max(2, Math.min(100, p))}%"></i></div>`;
  const usage = x => {
    const u = x.usage; if (!u || x.status !== "running") return "";
    const cpu = u.cpu_pct == null ? null : u.cpu_pct, ram = u.mem_used == null ? null : Math.min(100, u.mem_used / (x.ram * 1073741824) * 100);
    if (cpu == null && ram == null) return "";
    return `<div class="usg">${cpu == null ? "" : `<div><span>CPU ${cpu.toFixed(0)}%</span>${bar(cpu)}</div>`}${ram == null ? "" : `<div><span>RAM ${ram.toFixed(0)}%</span>${bar(ram)}</div>`}</div>`;
  };
  const card = x => `<a class="card srv" href="/servers/${x.id}"><div class="grow"><div class="n">${esc(x.name)}</div><div class="m">${esc(x.plan_name)} • ${x.cpu} vCPU • ${x.ram} GB RAM • ${diskLine(x.disk_info)} • ${esc((x.node || {}).name || "")}</div><div class="m">${left(x.expires_at)} • renews ${dday(x.expires_at)}</div>${usage(x)}</div><div class="row">${pill(x.status, x.suspended)}<span class="btn sm w">Manage</span></div></a>`;
  function draw() {
    const s = all.filter(x => (f === "all" || (f === "running" && x.status === "running" && !x.suspended) || (f === "stopped" && (x.status === "stopped" || x.suspended)) || (f === "soon" && soon(x))) && (!q || (x.name + " " + x.plan_name + " " + x.container).toLowerCase().includes(q)));
    $("#list").innerHTML = s.length ? s.map(card).join("")
      : all.length ? '<div class="card empty"><b>No servers match</b>Try a different search or filter.</div>'
      : '<div class="card empty"><b>No servers yet</b>Deploy your first server with BOLTs.<div style="margin-top:16px"><a class="btn w" href="/deploy">Deploy a server</a></div></div>';
  }
  async function load() {
    const r = await api("GET", "/api/servers"); if (!r.ok) return; all = r.servers;
    $("#s-srv").textContent = all.length; $("#s-run").textContent = all.filter(x => x.status === "running" && !x.suspended).length + " running";
    const n = all.filter(soon).length; $("#s-exp").textContent = n; $("#s-expm").textContent = n ? "within 3 days" : "all good";
    $("#fbar").hidden = all.length < 1; draw();
    clearTimeout(timer); timer = setTimeout(load, all.some(x => ["provisioning", "deleting"].includes(x.status)) ? 4000 : 30000);
  }
  $("#fq").oninput = e => { q = e.target.value.trim().toLowerCase(); draw(); };
  $$("#fbar [data-f]").forEach(b => b.onclick = () => { f = b.dataset.f; $$("#fbar [data-f]").forEach(x => x.classList.toggle("on", x === b)); draw(); });
  (async () => {
    const r = await api("GET", "/api/ledger"); if (!r.ok) return;
    $("#act").innerHTML = r.rows.slice(0, 6).map(x => `<div class="arow"><span class="adot ${x.delta >= 0 ? "pos" : "neg"}">${x.delta >= 0 ? "+" : "−"}</span><div class="grow"><div>${esc(x.reason)}</div><div class="sub">${dt(x.created)}</div></div><b class="${x.delta >= 0 ? "pos" : "neg"}">${x.delta >= 0 ? "+" : ""}${num(x.delta)}</b></div>`).join("") || '<div class="sub">Nothing yet. Claim your daily reward to get started.</div>';
  })();
  load();
};
