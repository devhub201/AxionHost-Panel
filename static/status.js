"use strict";
(() => {
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const col = v => v == null ? "rgba(255,255,255,.1)" : v >= .99 ? "#4ade80" : v > 0 ? "#fbbf24" : "#f87171";
  async function load() {
    let r; try { r = await (await fetch("/api/status")).json(); } catch (e) { return; }
    if (!r || !r.ok) return;
    document.getElementById("sbadge").innerHTML = `<span class="pill ${r.all_up ? "running" : ""}" style="font-size:14px;padding:7px 14px"><i></i>${r.all_up ? "All systems operational" : "Some locations are down"}</span>`;
    document.getElementById("smeta").textContent = r.nodes.length + " location" + (r.nodes.length === 1 ? "" : "s") + " • " + r.running + " servers running • updated " + new Date(r.at * 1000).toLocaleTimeString();
    document.getElementById("sn").innerHTML = r.nodes.map(n => `<div class="card" style="margin-top:14px"><div class="row sp"><b style="font-size:16px">${esc(n.flag)} ${esc(n.name)} <span class="sub">${esc(n.location)}</span></b><span class="pill ${n.up ? "running" : ""}"><i></i>${n.up ? "Operational" : "Down"}</span></div>
      <div style="display:flex;gap:2px;margin:14px 0 8px;height:30px">${n.bars.map(v => `<i style="flex:1;border-radius:3px;background:${col(v)}" title="${v == null ? "no data" : Math.round(v * 100) + "% up"}"></i>`).join("")}</div>
      <div class="row sp sub"><span>24 hours ago</span><span>${n.day == null ? "-" : n.day + "%"} (24h) • ${n.month == null ? "-" : n.month + "%"} (30d)</span><span>now</span></div></div>`).join("") || '<div class="card">No locations yet.</div>';
  }
  load(); setInterval(load, 30000);
})();
