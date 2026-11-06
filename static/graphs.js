"use strict";
/* Usage history chart (CPU % and RAM %) for the manage-server page. Draws plain SVG - no libraries. */
window.AXG = (() => {
  let sid, mins = 60, cache = null, tip = null;
  const W = 640, H = 170, P = { l: 34, r: 10, t: 10, b: 22 };
  const mb = b => b >= 1073741824 ? (b / 1073741824).toFixed(1) + " GB" : Math.round(b / 1048576) + " MB";
  function path(pts, fn) {
    const d = []; let pen = false;
    pts.forEach((p, i) => { const y = fn(p); if (y == null) { pen = false; return; } const x = P.l + (pts.length < 2 ? 0 : i / (pts.length - 1)) * (W - P.l - P.r); d.push((pen ? "L" : "M") + x.toFixed(1) + " " + (P.t + (1 - y / 100) * (H - P.t - P.b)).toFixed(1)); pen = true; });
    return d.join(" ");
  }
  function draw() {
    const box = document.getElementById("gslot"); if (!box) return;
    const c = cache, pts = (c && c.points) || [], chips = [[60, "1h"], [360, "6h"], [1440, "24h"]].map(([m, l]) => `<button class="chip ${m === mins ? "on" : ""}" data-m="${m}">${l}</button>`).join("");
    let body;
    if (pts.length < 2) body = `<div class="sub" style="padding:34px 0;text-align:center">Collecting data… the chart fills in a minute or two after the server starts.</div>`;
    else {
      const cpu = path(pts, p => p.cpu), ram = path(pts, p => p.mem == null ? null : Math.min(100, p.mem / c.ram * 100));
      const grid = [0, 25, 50, 75, 100].map(v => { const y = P.t + (1 - v / 100) * (H - P.t - P.b); return `<line x1="${P.l}" x2="${W - P.r}" y1="${y}" y2="${y}" stroke="rgba(255,255,255,.07)"/><text x="${P.l - 6}" y="${y + 3}" text-anchor="end" fill="#6b6b75" font-size="10">${v}</text>`; }).join("");
      const t0 = pts[0].t, t1 = pts[pts.length - 1].t, fm = t => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      body = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block;touch-action:none" id="gsvg"><defs><linearGradient id="gc" x1="0" x2="1"><stop offset="0" stop-color="#33d6ff"/><stop offset="1" stop-color="#6b7bff"/></linearGradient></defs>${grid}
        <text x="${P.l}" y="${H - 5}" fill="#6b6b75" font-size="10">${fm(t0)}</text><text x="${W - P.r}" y="${H - 5}" text-anchor="end" fill="#6b6b75" font-size="10">${fm(t1)}</text>
        <path d="${ram}" fill="none" stroke="#a566ff" stroke-width="2" stroke-linejoin="round" opacity=".9"/><path d="${cpu}" fill="none" stroke="url(#gc)" stroke-width="2.2" stroke-linejoin="round"/>
        <line id="gx" y1="${P.t}" y2="${H - P.b}" stroke="rgba(255,255,255,.25)" visibility="hidden"/><rect x="${P.l}" y="${P.t}" width="${W - P.l - P.r}" height="${H - P.t - P.b}" fill="transparent" id="gov"/></svg>
        <div class="row" style="gap:16px;font-size:12.5px;color:var(--mut);margin-top:6px"><span><i style="display:inline-block;width:10px;height:3px;background:#33d6ff;border-radius:2px;margin-right:6px"></i>CPU %</span><span><i style="display:inline-block;width:10px;height:3px;background:#a566ff;border-radius:2px;margin-right:6px"></i>RAM %</span><span id="gtip" style="margin-left:auto;color:#e6e6ea"></span></div>`;
    }
    box.innerHTML = `<div class="card"><div class="row sp"><div><h3>Usage history</h3><div class="d" style="margin:0">CPU and memory, measured inside your server.</div></div><div class="row" style="gap:6px">${chips}</div></div><div style="margin-top:12px">${body}</div></div>`;
    box.querySelectorAll("[data-m]").forEach(b => b.onclick = () => { mins = +b.dataset.m; load(); });
    const ov = box.querySelector("#gov"); if (ov && pts.length > 1) {
      const move = e => { const r = box.querySelector("#gsvg").getBoundingClientRect(), x = ((e.touches ? e.touches[0].clientX : e.clientX) - r.left) / r.width * W; const i = Math.max(0, Math.min(pts.length - 1, Math.round((x - P.l) / (W - P.l - P.r) * (pts.length - 1)))), p = pts[i];
        const gx = box.querySelector("#gx"), px = P.l + i / (pts.length - 1) * (W - P.l - P.r); gx.setAttribute("x1", px); gx.setAttribute("x2", px); gx.setAttribute("visibility", "visible");
        box.querySelector("#gtip").textContent = new Date(p.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) + " • CPU " + (p.cpu == null ? "-" : p.cpu + "%") + " • RAM " + (p.mem == null ? "-" : mb(p.mem)); };
      ov.addEventListener("mousemove", move); ov.addEventListener("touchmove", move, { passive: true }); ov.addEventListener("mouseleave", () => box.querySelector("#gx").setAttribute("visibility", "hidden"));
    }
  }
  async function load() { const r = await api("GET", `/api/servers/${sid}/stats?m=${mins}`); if (r.ok) { cache = r; draw(); } }
  return { mount(id, running) { sid = id; draw(); if (running) load(); else if (!cache) load(); }, refresh() { load(); } };
})();
