"use strict";
/* AxionHost UI polish: splash screen, top progress bar, rich skeletons, enter animations, count-up numbers. No dependencies. */
(() => {
const D = document, B = D.body, page = B.dataset.page || "";
const $ = (s, r = D) => r.querySelector(s), $$ = (s, r = D) => [...r.querySelectorAll(s)];
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
const safe = f => { try { return f(); } catch (e) { return null; } };

/* ---- splash (once per browser tab session) ---- */
const seen = safe(() => sessionStorage.getItem("axsplash"));
if (!seen && !reduce && page !== "") {
  const s = D.createElement("div"); s.id = "splash";
  s.innerHTML = '<div class="in"><svg viewBox="0 0 100 100"><use href="#A"/></svg><b>AXIONHOST</b><div class="ld"><i></i></div></div>';
  B.appendChild(s);
  const t0 = performance.now(); let gone = false;
  const hide = () => { if (gone) return; gone = true; const wait = Math.max(0, 650 - (performance.now() - t0)); setTimeout(() => { s.classList.add("out"); setTimeout(() => s.remove(), 600); enter(); }, wait); };
  addEventListener("load", hide); setTimeout(hide, 3500);
  safe(() => sessionStorage.setItem("axsplash", "1"));
}

/* ---- top progress bar (navigation + fetch) ---- */
const tp = D.createElement("div"); tp.id = "tp"; B.appendChild(tp);
let busy = 0, w = 0, timer = null, hideT = null;
const step = () => { w += (90 - w) * .12; tp.style.width = w + "%"; };
const start = () => { if (busy++ === 0) { clearTimeout(hideT); w = 8; tp.classList.add("on"); tp.style.width = "8%"; timer = setInterval(step, 220); } };
const done = () => { if (--busy <= 0) { busy = 0; clearInterval(timer); tp.style.width = "100%"; hideT = setTimeout(() => { tp.classList.remove("on"); setTimeout(() => { tp.style.width = "0"; }, 300); }, 220); } };
const of = window.fetch; window.fetch = function (u, o) {
  const quiet = typeof u === "string" && /\/out\?|\/in$|\/resize$/.test(u);            // terminal long-polls must not flicker the bar
  if (!quiet) start();
  return of.apply(this, arguments).finally(() => { if (!quiet) done(); });
};
D.addEventListener("click", e => {
  const a = e.target.closest && e.target.closest("a[href]"); if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || a.target || a.hasAttribute("download")) return;
  const h = a.getAttribute("href"); if (!h || h[0] === "#" || /^(mailto|tel|javascript):/.test(h) || (a.origin && a.origin !== location.origin)) return;
  B.classList.add("leaving"); start();
});
addEventListener("pageshow", e => { if (e.persisted) { B.classList.remove("leaving"); busy = 1; done(); } });

/* ---- rich skeletons ---- */
const blk = (h, cls = "") => `<div class="skb ${cls}" style="height:${h}px"></div>`;
const LAYOUT = {
  server: () => `<div class="skl">${blk(190)}<div class="r4">${blk(96)}${blk(96)}${blk(96)}${blk(96)}</div><div class="rs"><div class="col">${blk(330)}${blk(150)}</div><div class="col">${blk(230)}${blk(170)}${blk(150)}</div></div></div>`,
  dashboard: () => `<div class="skl"><div class="r4">${blk(96)}${blk(96)}${blk(96)}${blk(96)}</div>${blk(110)}${blk(110)}</div>`,
  admin: () => `<div class="skl"><div class="r4">${blk(96)}${blk(96)}${blk(96)}${blk(96)}</div><div class="r2">${blk(210)}${blk(210)}</div></div>`,
  deploy: () => `<div class="skl">${blk(70)}<div class="r2">${blk(180)}${blk(180)}</div>${blk(120)}</div>`,
  def: () => `<div class="skl">${blk(110)}${blk(110)}</div>`
};
const rich = el => {
  if (el.dataset.skd) return; el.dataset.skd = 1;
  const p = el.parentElement; if (!p) return;
  const only = [...p.children].every(c => c.classList.contains("sk"));
  if (only && p.children.length <= 3 && (p.id === "sv" || p.id === "w" || p.id === "pane")) {
    const k = p.id === "sv" ? "server" : p.id === "w" ? "deploy" : "admin"; p.innerHTML = (LAYOUT[k] || LAYOUT.def)(); p.dataset.skd = 1;
  }
};
const scan = () => $$(".sk:not([data-skd])").forEach(rich);

/* ---- enter animations + count-up ---- */
let enterT;
function enter() {
  if (reduce) return; B.classList.add("fx-enter"); clearTimeout(enterT); enterT = setTimeout(() => B.classList.remove("fx-enter"), 1100); stagger(); countUp();
}
function stagger() {
  $$(".card,.tile,.stat,.srv,.crow,.hero").forEach(el => { if (!el.style.getPropertyValue("--i")) { const idx = Math.min(12, [...el.parentElement.children].indexOf(el)); el.style.setProperty("--i", idx); } });
}
function countUp() {
  $$(".stat b,.tile b").forEach(el => {
    if (el.dataset.cu || el.children.length) return; const t = el.textContent.trim(); if (!/^[\d,]+$/.test(t)) return;
    const to = +t.replace(/,/g, ""); if (!to || to > 1e9) return; el.dataset.cu = 1; const t0 = performance.now(), dur = 700;
    const f = now => { const k = Math.min(1, (now - t0) / dur), v = Math.round(to * (1 - Math.pow(1 - k, 3))); el.textContent = v.toLocaleString("en-IN"); if (k < 1) requestAnimationFrame(f); };
    requestAnimationFrame(f);
  });
}

/* ---- observe dynamic content: skeleton swaps, first real render, admin section switches ---- */
let pend = false;
new MutationObserver(() => { if (pend) return; pend = true; requestAnimationFrame(() => { pend = false; scan(); if (B.classList.contains("fx-enter")) { stagger(); countUp(); } }); }).observe(B, { childList: true, subtree: true });
D.addEventListener("click", e => { if (e.target.closest && e.target.closest("#side button,.chip,.tabs a")) { B.classList.add("fx-enter"); clearTimeout(enterT); enterT = setTimeout(() => B.classList.remove("fx-enter"), 1100); } });

/* ---- theme toggle ---- */
(() => {
  const SUN = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>', MOON = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
  const root = D.documentElement, set = v => { if (v === "light") root.setAttribute("data-theme", "light"); else root.removeAttribute("data-theme"); safe(() => localStorage.setItem("axtheme", v)); const m = $("meta[name=theme-color]"); if (m) m.content = v === "light" ? "#ffffff" : "#000000"; b.innerHTML = v === "light" ? MOON : SUN; b.title = v === "light" ? "Dark mode" : "Light mode"; };
  const b = D.createElement("button"); b.id = "thm"; b.type = "button"; b.setAttribute("aria-label", "Toggle theme");
  const host = $(".top .right"); if (!host) return; host.insertBefore(b, host.firstChild);
  set(safe(() => localStorage.getItem("axtheme")) === "light" ? "light" : "dark"); b.onclick = () => set(root.getAttribute("data-theme") === "light" ? "dark" : "light");
})();

/* ---- misc ---- */
addEventListener("scroll", () => B.classList.toggle("scrolled", scrollY > 8), { passive: true });
D.addEventListener("click", e => { const b = e.target.closest && e.target.closest(".btn"); if (b && b.dataset.busy !== undefined) { b.classList.add("busy"); setTimeout(() => b.classList.remove("busy"), 1500); } });
scan();
if (seen || reduce || page === "") { if (D.readyState === "loading") D.addEventListener("DOMContentLoaded", enter); else enter(); }
})();
