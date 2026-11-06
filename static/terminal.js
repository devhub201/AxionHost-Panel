"use strict";
/* AxionHost Panel - manage-page terminal (xterm.js) + SSHX / Pinggy tools card.
   The terminal element lives outside the page's periodic re-render and is moved into #tslot after every render,
   so an open session is never torn down by the auto-refresh. */
window.AXT = (() => {
  let sid = 0, reload = () => {}, T = null, cur = null;
  const KEYS = [["Esc", "\x1b"], ["Tab", "\t"], ["Ctrl", "ctrl"], ["↑", "\x1b[A"], ["↓", "\x1b[B"], ["←", "\x1b[D"], ["→", "\x1b[C"], ["|", "|"], ["/", "/"], ["-", "-"], ["~", "~"], ["Home", "\x1b[H"], ["End", "\x1b[F"], ["PgUp", "\x1b[5~"], ["PgDn", "\x1b[6~"], ["^C", "\x03"], ["^D", "\x04"]];
  const u8 = b => Uint8Array.from(atob(b), c => c.charCodeAt(0));
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const copy = (t, m) => { if (navigator.clipboard) navigator.clipboard.writeText(t).then(() => toast(m || "Copied"), () => toast("Copy failed", true)); };

  function build() {
    const el = document.createElement("div"); el.className = "card tcard"; el.id = "tm";
    el.innerHTML = `<div class="thead"><span class="tdots"><i></i><i></i><i></i></span><span class="ttl" id="tt">terminal</span><span class="tst" id="ts"><i></i><b>Offline</b></span><button class="btn sm ic" id="tfs" title="Fullscreen" aria-label="Fullscreen">⤢</button><button class="btn sm" id="tx" hidden>Disconnect</button></div>
<div class="tbody" id="tb"><div class="tempty" id="te"><svg viewBox="0 0 24 24" class="tic"><path d="M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Zm3 4 3 3-3 3m5 0h4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg><div class="pr">root@<span id="tn">vps</span>:~# <u class="caret"></u></div><div class="sub" id="tm2">Open a root shell in your VPS, right here in the browser.</div><button class="btn w" id="to">Open terminal</button></div></div>
<div class="tkeys" id="tk">${KEYS.map((k, i) => `<button type="button" data-i="${i}">${k[0]}</button>`).join("")}</div>`;
    return el;
  }

  function setState(st, label) {
    T.state = st; const s = $("#ts", T.el); s.className = "tst " + (st === "live" ? "live" : st === "wait" ? "wait" : ""); $("b", s).textContent = label;
    $("#tx", T.el).hidden = st !== "live"; $("#te", T.el).hidden = st === "live" || st === "wait";
  }
  function ensureTerm() {
    if (T.term) return;
    T.term = new XTermBundle.Terminal({ cursorBlink: true, fontSize: window.innerWidth < 600 ? 13 : 14, scrollback: 5000, allowProposedApi: true, convertEol: false,
      fontFamily: 'ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace',
      theme: { background: "#050507", foreground: "#e6e6ea", cursor: "#ffffff", cursorAccent: "#050507", selectionBackground: "rgba(255,255,255,.28)", black: "#1a1a1f", brightBlack: "#6b6b75" } });
    T.fit = new XTermBundle.FitAddon(); T.term.loadAddon(T.fit); T.term.open($("#tb", T.el));
    T.term.onData(d => {
      if (T.ctrl && d.length === 1) { const c = d.toUpperCase().charCodeAt(0); if (c >= 64 && c <= 95) d = String.fromCharCode(c - 64); setCtrl(false); }
      send(d);
    });
    let t; const ro = new ResizeObserver(() => { clearTimeout(t); t = setTimeout(fitNow, 150); }); ro.observe($("#tb", T.el));
  }
  function fitNow() {
    if (!T.term || !T.el.isConnected || !$("#tb", T.el).offsetWidth) return;
    try { T.fit.fit(); } catch (e) { return; }
    const k = T.term.cols + "x" + T.term.rows;
    if (T.tid && k !== T.size) { T.size = k; api("POST", `/api/term/${T.tid}/resize`, { cols: T.term.cols, rows: T.term.rows }); }
  }
  function setCtrl(on) { T.ctrl = on; const b = $$("#tk button", T.el)[KEYS.findIndex(k => k[1] === "ctrl")]; b && b.classList.toggle("on", on); }
  function send(d) { if (!T.tid) return; T.q += d; if (!T.sending) flush(); }
  async function flush() {
    T.sending = true;
    while (T.q && T.tid) {
      const d = T.q.slice(0, 8000); T.q = T.q.slice(8000);
      const r = await api("POST", `/api/term/${T.tid}/in`, { d });
      if (!r.ok && [401, 403, 404].includes(r.status)) break;
    }
    T.sending = false;
  }
  async function connect() {
    if (T.state === "live" || T.state === "wait") return;
    setState("wait", "Connecting"); ensureTerm(); T.term.reset();
    await sleep(30); try { T.fit.fit(); } catch (e) {}
    const r = await api("POST", `/api/servers/${sid}/term`, { cols: T.term.cols, rows: T.term.rows });
    if (!r.ok) { setState("off", "Offline"); $("#tm2", T.el).textContent = r.error || "Could not open a terminal"; return; }
    T.tid = r.tid; T.pos = 0; T.q = ""; T.size = T.term.cols + "x" + T.term.rows; T.run = true;
    setState("live", "Connected"); T.term.focus(); pump(r.tid);
  }
  async function pump(tid) {
    let fails = 0;
    while (T.run && T.tid === tid) {
      const r = await api("GET", `/api/term/${tid}/out?pos=${T.pos}&wait=1`);
      if (!T.run || T.tid !== tid) return;
      if (!r.ok) {
        if ([401, 403, 404].includes(r.status) || ++fails > 6) return ended(r.status === 404 ? "Session ended" : "Connection lost");
        await sleep(900 * fails); continue;
      }
      fails = 0; if (r.d) T.term.write(u8(r.d)); T.pos = r.pos;
      if (r.closed) return ended("Session closed");
    }
  }
  function ended(msg) {
    T.run = false; T.tid = null; T.q = ""; setState("off", "Closed");
    $("#tm2", T.el).textContent = msg + ". Open a new terminal any time."; $("#to", T.el).textContent = "Reconnect";
  }
  async function disconnect() { const id = T.tid; T.run = false; T.tid = null; setState("off", "Closed"); $("#to", T.el).textContent = "Reconnect"; $("#tm2", T.el).textContent = "Disconnected."; if (id) api("POST", `/api/term/${id}/close`); }
  function fullscreen() {
    const on = T.el.classList.toggle("full"); document.body.classList.toggle("tfull", on); $("#tfs", T.el).textContent = on ? "✕" : "⤢";
    setTimeout(() => { fitNow(); T.term && T.term.focus(); }, 60);
  }

  function init(id, onReload) {
    sid = id; reload = onReload;
    if (T) return;
    T = { el: build(), term: null, fit: null, tid: null, pos: 0, run: false, q: "", sending: false, ctrl: false, state: "off", size: "" };
    setState("off", "Offline");
    $("#to", T.el).onclick = connect; $("#tx", T.el).onclick = disconnect; $("#tfs", T.el).onclick = fullscreen;
    $$("#tk button", T.el).forEach(b => b.onmousedown = e => e.preventDefault());              // keep the keyboard open on phones
    $$("#tk button", T.el).forEach(b => b.onclick = () => {
      const k = KEYS[+b.dataset.i][1];
      if (k === "ctrl") setCtrl(!T.ctrl); else if (T.tid) send(k);
      T.term && T.term.focus();
    });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && T.el.classList.contains("full")) fullscreen(); });
    window.addEventListener("pagehide", () => { if (T.tid) fetch(`/api/term/${T.tid}/close`, { method: "POST", keepalive: true, credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF }, body: "{}" }); });
  }

  /* ---------------- tools: sshx + pinggy ---------------- */
  const IC = {
    sshx: '<svg viewBox="0 0 24 24"><path d="M3 5h18v14H3zM3 9h18M7 13l2 2-2 2m5 0h4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    pinggy: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>'
  };
  function toolCard(kind, t, can) {
    const st = (t && t.status) || "stopped", url = (t && t.url) || "", note = (t && t.note) || "";
    const head = (title, tag) => `<div class="row" style="gap:12px;margin-bottom:12px"><span class="tic2">${IC[kind]}</span><div class="grow"><h3 style="margin:0">${title}</h3><div class="sub" style="font-size:12.5px">${tag}</div></div><span class="tchip ${st}">${st === "starting" ? '<i class="spin"></i>' : "<i></i>"}${esc(st === "running" ? "live" : st)}</span></div>`;
    const btn = (a, label, cls) => `<button class="btn sm ${cls || ""}" data-tool="${kind}" data-act="${a}" ${can ? "" : "disabled"}>${label}</button>`;
    let body = "";
    if (kind === "sshx") {
      body = head("SSHX", "Web terminal link · sshx.io");
      if (st === "running" && url) body += `<div class="tbox"><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a></div><div class="row" style="margin-top:12px"><a class="btn sm w" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open</a><button class="btn sm" data-copy="${esc(url)}">Copy link</button>${btn("stop", "Stop", "red")}</div><div class="tnote">Anyone with this link can control your VPS. Stop it when you are done.</div>`;
      else if (st === "starting") body += '<div class="d">Installing sshx and creating your link. This can take up to a minute…</div><div class="bar"><i style="width:60%;animation:pulse 1.2s infinite"></i></div>';
      else if (st === "stopping") body += '<div class="d">Stopping…</div>';
      else body += `<div class="d">Get a shareable terminal link that opens in any browser. No SSH client needed.</div>${st === "failed" ? `<div class="tnote bad">${esc(note || "Could not start.")}</div>` : ""}<div class="row">${btn("start", st === "failed" ? "Try again" : "Start SSHX", "w")}</div>`;
    } else {
      body = head("Pinggy SSH", "Public SSH address · works without IPv4");
      const m = /^tcp:\/\/([A-Za-z0-9.-]+):(\d+)$/.exec(url);
      if (st === "running" && m) { const cmd = `ssh root@${m[1]} -p ${m[2]}`; body += `<div class="tbox"><code>${esc(cmd)}</code></div><div class="row" style="margin-top:12px"><button class="btn sm w" data-copy="${esc(cmd)}">Copy command</button>${btn("stop", "Stop", "red")}</div><dl class="kv" style="margin-top:14px;font-size:13px"><dt>Host</dt><dd>${esc(m[1])}</dd><dt>Port</dt><dd>${esc(m[2])}</dd><dt>User</dt><dd>root</dd></dl><div class="tnote">Free Pinggy tunnels last about an hour, then you can start a new one.</div>`; }
      else if (st === "starting") body += '<div class="d">Installing the SSH client and opening a tunnel…</div><div class="bar"><i style="width:60%;animation:pulse 1.2s infinite"></i></div>';
      else if (st === "stopping") body += '<div class="d">Stopping…</div>';
      else body += `<div class="d">Get a public SSH address for this VPS, even when the node has no public IPv4 or you are behind NAT.</div>${st === "failed" ? `<div class="tnote bad">${esc(note || "Could not start.")}</div>` : ""}<div class="row">${btn("start", st === "failed" ? "Try again" : "Start tunnel", "w")}</div>`;
    }
    return `<div class="card tool">${body}</div>`;
  }
  async function toolAct(kind, act) {
    const r = await api("POST", `/api/servers/${sid}/tools`, { kind, action: act });
    toast(r.ok ? (act === "start" ? "Starting…" : "Stopping…") : r.error, !r.ok); if (r.ok) reload();
  }

  function usageCard(s) {
    const u = s.usage; if (!u || s.status !== "running") return "";
    const cpu = u.cpu_pct == null ? null : u.cpu_pct, used = u.mem_used, total = s.ram * 1073741824, ram = used == null ? null : Math.min(100, used / total * 100);
    if (cpu == null && ram == null) return "";
    const fmt = b => b >= 1073741824 ? (b / 1073741824).toFixed(1) + " GB" : Math.round(b / 1048576) + " MB";
    const row = (l, p, txt) => `<div><div class="row sp" style="margin-bottom:6px"><span class="sub">${l}</span><b style="font-size:13px">${txt}</b></div><div class="bar" style="height:8px"><i style="width:${Math.max(1, p)}%"></i></div></div>`;
    return `<div class="card" style="margin-bottom:16px"><div class="row sp"><h3>Live usage</h3><span class="pill running"><i></i>live</span></div><div class="d">Measured inside your server, updated every minute.</div><div class="grid g2">${cpu == null ? "" : row("CPU", cpu, cpu.toFixed(0) + "% of " + s.cpu + " vCPU")}${ram == null ? "" : row("Memory", ram, fmt(used) + " / " + s.ram + " GB")}</div></div>`;
  }
  function update(s, tools) {
    cur = s; if (!T) return;
    const slot = $("#tslot"); if (slot && T.el.parentNode !== slot) { slot.appendChild(T.el); setTimeout(fitNow, 50); }
    const can = s.status === "running" && !s.suspended;
    $("#tt", T.el).textContent = "root@" + s.name; $("#tn", T.el).textContent = s.name.toLowerCase().replace(/[^a-z0-9-]+/g, "-");
    $("#to", T.el).disabled = !can;
    if (T.state === "off") $("#tm2", T.el).textContent = can ? ($("#to", T.el).textContent === "Reconnect" ? "Open a new terminal any time." : "Open a root shell in your VPS, right here in the browser.") : (s.suspended ? "Server is suspended." : "Start your server to use the terminal.");
    const box = $("#tools"); if (!box) return;
    box.innerHTML = `<div class="grid g2">${toolCard("sshx", tools.sshx, can)}${toolCard("pinggy", tools.pinggy, can)}</div>
<div class="card" style="margin-top:16px"><div class="row sp"><div class="grow"><h3>Root password</h3><div class="d" style="margin:0">Generate a new random root password for this server. Open sessions stay connected.</div></div><button class="btn sm" id="rpw" ${can ? "" : "disabled"}>Reset password</button></div></div>`;
    $("#rpw", box).onclick = () => modal(`<h3>Reset root password?</h3><p class="sub">A new random password is set inside the server. You can see it under Connect → Show root password.</p><div class="row" style="margin-top:18px;justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn w" id="y">Reset</button></div>`, m => $("#y", m).onclick = async () => { closeModal(); const r = await api("POST", `/api/servers/${sid}/action`, { action: "resetpw" }); toast(r.ok ? "Resetting password…" : r.error, !r.ok); if (r.ok) reload(); });
    const us = $("#uslot"); if (us) us.innerHTML = usageCard(s);
    $$("[data-tool]", box).forEach(b => b.onclick = () => toolAct(b.dataset.tool, b.dataset.act));
    $$("[data-copy]", box).forEach(b => b.onclick = () => copy(b.dataset.copy));
  }
  return { init, update };
})();
