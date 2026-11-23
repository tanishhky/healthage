/* healthage charts: small hand-written SVG helpers, no dependencies.
   Marks follow the dataviz rules used for this project: 2px lines, recessive
   grid, one y axis, direct end labels for up to four series plus a legend,
   hover crosshair with a tooltip on every chart. */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";

  function el(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs || {}) if (attrs[k] !== undefined && attrs[k] !== null) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function text(parent, x, y, str, attrs) {
    const t = el("text", Object.assign({ x, y }, attrs || {}), parent);
    t.textContent = str;
    return t;
  }
  function scale(d0, d1, r0, r1) {
    const k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0);
    return (v) => r0 + (v - d0) * k;
  }
  function niceTicks(min, max, n) {
    if (min === max) { min -= 1; max += 1; }
    const span = max - min, raw = span / Math.max(1, n);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
    const out = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(+v.toFixed(6));
    return out;
  }
  function fresh(container) {
    container.innerHTML = "";
    const w = Math.max(40, container.clientWidth), h = Math.max(16, container.clientHeight);
    const svg = el("svg", { width: w, height: h, viewBox: `0 0 ${w} ${h}` }, container);
    return { svg, w, h };
  }

  // --- tooltip ---------------------------------------------------------------
  const tip = {
    node: null,
    show(html, cx, cy) {
      this.node = this.node || document.getElementById("tip");
      const n = this.node;
      n.innerHTML = html;
      n.hidden = false;
      const r = n.getBoundingClientRect();
      let x = cx + 16, y = cy + 14;
      if (x + r.width > window.innerWidth - 8) x = cx - r.width - 16;
      if (y + r.height > window.innerHeight - 8) y = cy - r.height - 14;
      n.style.left = x + "px";
      n.style.top = y + "px";
    },
    hide() { if (this.node) this.node.hidden = true; },
  };
  function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  // --- line chart ------------------------------------------------------------
  // cfg: {series:[{id,label,color,values,dash,width}], cloud:[[...]], xLabels:[..],
  //       ref:{y,label}, yFmt, xTitle, pad, endLabels}
  function line(container, cfg) {
    const { svg, w, h } = fresh(container);
    const pad = Object.assign({ l: 44, r: cfg.endLabels ? 178 : 14, t: 10, b: 26 }, cfg.pad || {});
    const vis = cfg.series.filter((s) => !s.hidden);
    const all = [];
    vis.forEach((s) => s.values.forEach((v) => v != null && all.push(v)));
    (cfg.cloud || []).forEach((c) => c.forEach((v) => all.push(v)));
    if (cfg.ref) all.push(cfg.ref.y);
    if (!all.length) return;
    let lo = Math.min(...all), hi = Math.max(...all);
    const m = (hi - lo) * 0.08 || 0.5;
    lo -= m; hi += m;
    const n = Math.max(...cfg.series.map((s) => s.values.length));
    const x = scale(0, n - 1, pad.l, w - pad.r), y = scale(lo, hi, h - pad.b, pad.t);
    const yFmt = cfg.yFmt || ((v) => v.toFixed(1));

    const g = el("g", { class: "axis" }, svg);
    niceTicks(lo, hi, 5).forEach((t) => {
      el("line", { x1: pad.l, x2: w - pad.r, y1: y(t), y2: y(t), class: "gridline" }, g);
      text(g, pad.l - 8, y(t) + 3.5, yFmt(t), { "text-anchor": "end" });
    });
    const xl = cfg.xLabels || [...Array(n).keys()];
    const every = Math.ceil(n / Math.max(2, Math.floor((w - pad.l - pad.r) / 46)));
    xl.forEach((lab, i) => { if ((n - 1 - i) % every === 0) text(g, x(i), h - 8, lab, { "text-anchor": "middle" }); });
    if (cfg.xTitle) text(g, w - pad.r, h - 8, "", {});

    if (cfg.ref) {
      el("line", { x1: pad.l, x2: w - pad.r, y1: y(cfg.ref.y), y2: y(cfg.ref.y), stroke: "rgba(236,235,243,.28)", "stroke-dasharray": "2 4" }, svg);
      text(svg, pad.l + 6, y(cfg.ref.y) - 5, cfg.ref.label, { class: "lbl" });
    }
    const path = (vals) => vals.map((v, i) => (v == null ? "" : `${i && vals[i - 1] != null ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`)).join("");
    (cfg.cloud || []).forEach((c) => el("path", { d: path(c), fill: "none", stroke: "var(--cloud)", "stroke-width": 1 }, svg));
    vis.forEach((s) => el("path", {
      d: path(s.values), fill: "none", stroke: s.color, "stroke-width": s.width || 2,
      "stroke-dasharray": s.dash || null, "stroke-linejoin": "round", "stroke-linecap": "round",
    }, svg));

    if (cfg.endLabels) {
      const ends = vis.map((s) => ({ s, v: s.values[s.values.length - 1] })).filter((e) => e.v != null)
        .sort((a, b) => y(a.v) - y(b.v));
      let last = -1e9;
      ends.forEach((e) => {
        let yy = Math.max(y(e.v), last + 15);
        last = yy;
        el("circle", { cx: x(n - 1), cy: y(e.v), r: 3.5, fill: e.s.color, stroke: "var(--surface)", "stroke-width": 2 }, svg);
        el("line", { x1: x(n - 1) + 5, y1: y(e.v), x2: x(n - 1) + 14, y2: yy, stroke: e.s.color }, svg);
        text(svg, x(n - 1) + 18, yy + 3.5, `${e.s.short || e.s.label}  ${yFmt(e.v)}`, { class: "endlbl" });
      });
    }

    // hover layer
    const cross = el("line", { y1: pad.t, y2: h - pad.b, class: "crosshair", visibility: "hidden" }, svg);
    const dots = vis.map((s) => el("circle", { r: 4, fill: s.color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" }, svg));
    const hit = el("rect", { x: pad.l, y: pad.t, width: Math.max(0, w - pad.l - pad.r), height: Math.max(0, h - pad.t - pad.b), fill: "transparent" }, svg);
    hit.addEventListener("mousemove", (ev) => {
      const r = svg.getBoundingClientRect();
      const i = Math.max(0, Math.min(n - 1, Math.round((ev.clientX - r.left - pad.l) / Math.max(1e-6, (w - pad.l - pad.r)) * (n - 1))));
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      let rows = "";
      vis.forEach((s, k) => {
        const v = s.values[i];
        if (v == null) { dots[k].setAttribute("visibility", "hidden"); return; }
        dots[k].setAttribute("cx", x(i)); dots[k].setAttribute("cy", y(v)); dots[k].setAttribute("visibility", "visible");
        rows += `<div class="tr"><span><i style-color="${s.color}"></i>${esc(s.label)}</span><b>${yFmt(v)}</b></div>`;
      });
      tip.show(`<div class="th">${esc(cfg.tipTitle ? cfg.tipTitle(i) : xl[i])}</div>${rows}`, ev.clientX, ev.clientY);
      paintSwatches(vis.map((s) => s.color));
    });
    hit.addEventListener("mouseleave", () => {
      cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); tip.hide();
    });
  }
  // CSP forbids inline style attributes; color tooltip swatches via CSSOM.
  function paintSwatches(colors) {
    const n = document.getElementById("tip");
    n.querySelectorAll("i[style-color]").forEach((i) => { i.style.background = i.getAttribute("style-color"); });
  }

  // --- diverging bar inside a fixed-width row --------------------------------
  function divBar(container, value, maxAbs) {
    const { svg, w, h } = fresh(container);
    const mid = w / 2, half = w / 2 - 2, bh = 8, yy = (h - bh) / 2;
    el("rect", { x: 0, y: h / 2 - 0.5, width: w, height: 1, fill: "rgba(236,235,243,.08)" }, svg);
    el("rect", { x: mid - 0.5, y: 1, width: 1, height: h - 2, fill: "rgba(236,235,243,.35)" }, svg);
    if (value == null || Math.abs(value) < 1e-9) return;
    const len = Math.max(2, Math.min(1, Math.abs(value) / maxAbs) * half);
    const pos = value > 0;
    el("rect", { x: pos ? mid + 1 : mid - 1 - len, y: yy, width: len, height: bh, rx: 2, fill: pos ? "#d55181" : "#c98500" }, svg);
  }

  // --- weekly bars -------------------------------------------------------------
  // items: [{label, value, partial, html}]
  function bars(container, items, opts) {
    const { svg, w, h } = fresh(container);
    if (!items.length) return;
    const pad = { l: 40, r: 10, t: 10, b: 26 };
    const vals = items.map((d) => d.value);
    const lim = Math.max(1, ...vals.map(Math.abs)) * 1.1;
    const y = scale(-lim, lim, h - pad.b, pad.t);
    const g = el("g", { class: "axis" }, svg);
    niceTicks(-lim, lim, 4).forEach((t) => {
      el("line", { x1: pad.l, x2: w - pad.r, y1: y(t), y2: y(t), class: "gridline" }, g);
      text(g, pad.l - 8, y(t) + 3.5, (t > 0 ? "+" : "") + t, { "text-anchor": "end" });
    });
    el("line", { x1: pad.l, x2: w - pad.r, y1: y(0), y2: y(0), stroke: "rgba(236,235,243,.35)" }, svg);
    const bw = (w - pad.l - pad.r) / items.length;
    const every = Math.ceil(items.length / Math.max(2, Math.floor((w - pad.l - pad.r) / 54)));
    items.forEach((d, i) => {
      const x0 = pad.l + i * bw + Math.max(1, bw * 0.16), wid = Math.max(2, bw * 0.68);
      const top = Math.min(y(0), y(d.value)), ht = Math.max(1.5, Math.abs(y(d.value) - y(0)));
      const r = el("rect", { x: x0, y: top, width: wid, height: ht, rx: 3, fill: d.value > 0 ? "#d55181" : "#c98500",
        "fill-opacity": d.partial ? 0.35 : 0.95, stroke: d.partial ? (d.value > 0 ? "#d55181" : "#c98500") : null,
        "stroke-dasharray": d.partial ? "2 2" : null }, svg);
      if ((items.length - 1 - i) % every === 0) text(g, x0 + wid / 2, h - 8, d.label, { "text-anchor": "middle" });
      const hit = el("rect", { x: pad.l + i * bw, y: pad.t, width: bw, height: h - pad.t - pad.b, fill: "transparent" }, svg);
      hit.addEventListener("mousemove", (ev) => { r.setAttribute("fill-opacity", 1); tip.show(d.html, ev.clientX, ev.clientY); });
      hit.addEventListener("mouseleave", () => { r.setAttribute("fill-opacity", d.partial ? 0.35 : 0.95); tip.hide(); });
    });
  }

  // --- sparkline -----------------------------------------------------------------
  function spark(container, points, color, fmt) {
    const { svg, w, h } = fresh(container);
    const pts = points.filter((p) => p.v != null);
    if (pts.length < 2) { text(svg, 4, h / 2, "not enough data", { class: "lbl" }); return; }
    const vals = pts.map((p) => p.v);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (lo === hi) { lo -= 1; hi += 1; }
    const x = scale(0, points.length - 1, 2, w - 2), y = scale(lo, hi, h - 3, 3);
    // 7-day rolling mean drawn over faint daily points
    points.forEach((p, i) => { if (p.v != null) el("circle", { cx: x(i), cy: y(p.v), r: 1.3, fill: color, "fill-opacity": 0.35 }, svg); });
    const roll = points.map((p, i) => {
      const win = points.slice(Math.max(0, i - 6), i + 1).map((q) => q.v).filter((v) => v != null);
      return win.length >= 3 ? win.reduce((a, b) => a + b, 0) / win.length : null;
    });
    let d = "";
    roll.forEach((v, i) => { if (v != null) d += `${d && roll[i - 1] != null ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; });
    el("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round" }, svg);
    const hit = el("rect", { x: 0, y: 0, width: w, height: h, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", (ev) => {
      const r = svg.getBoundingClientRect();
      const i = Math.max(0, Math.min(points.length - 1, Math.round((ev.clientX - r.left) / w * (points.length - 1))));
      const p = points[i];
      tip.show(`<div class="th">${esc(p.label)}</div><div class="tr"><span>day</span><b>${p.v == null ? "no data" : fmt(p.v)}</b></div>` +
        (roll[i] != null ? `<div class="tr"><span>7-day mean</span><b>${fmt(roll[i])}</b></div>` : ""), ev.clientX, ev.clientY);
    });
    hit.addEventListener("mouseleave", () => tip.hide());
  }

  // --- sleep clock strip ------------------------------------------------------------
  // points: [{label, mid}] where mid = minutes after local midnight
  function clock(container, points, color) {
    const { svg, w, h } = fresh(container);
    const pts = points.filter((p) => p.mid != null);
    if (pts.length < 3) { text(svg, 8, h / 2, "Sleep timing needs a few nights with start and end times.", { class: "lbl" }); return; }
    const ang = pts.map((p) => (p.mid / 1440) * 2 * Math.PI);
    const mean = Math.atan2(ang.reduce((a, b) => a + Math.sin(b), 0), ang.reduce((a, b) => a + Math.cos(b), 0));
    const center = Math.round(((((mean / (2 * Math.PI)) * 1440) + 1440) % 1440) / 30) * 30;
    const dev = (m) => { let d = m - center; while (d > 720) d -= 1440; while (d < -720) d += 1440; return d; };
    const devs = pts.map((p) => dev(p.mid));
    const lim = Math.min(720, Math.max(180, ...devs.map(Math.abs)) + 30);
    const pad = { l: 52, r: 12, t: 10, b: 24 };
    const x = scale(0, points.length - 1, pad.l, w - pad.r), y = scale(-lim, lim, h - pad.b, pad.t);
    const g = el("g", { class: "axis" }, svg);
    const hhmm = (m) => { m = ((Math.round(m) % 1440) + 1440) % 1440; return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0"); };
    const step = lim > 400 ? 240 : 120;
    for (let t = -Math.floor(lim / step) * step; t <= lim; t += step) {
      el("line", { x1: pad.l, x2: w - pad.r, y1: y(t), y2: y(t), class: "gridline" }, g);
      text(g, pad.l - 8, y(t) + 3.5, hhmm(center + t), { "text-anchor": "end" });
    }
    el("rect", { x: pad.l, y: y(30), width: w - pad.l - pad.r, height: Math.max(1, y(-30) - y(30)), fill: "rgba(201,133,0,.10)" }, svg);
    text(svg, w - pad.r - 4, y(30) - 4, "30 min band", { class: "lbl", "text-anchor": "end" });
    el("line", { x1: pad.l, x2: w - pad.r, y1: y(0), y2: y(0), stroke: "rgba(201,133,0,.55)", "stroke-dasharray": "3 3" }, svg);
    const every = Math.ceil(points.length / Math.max(2, Math.floor((w - pad.l - pad.r) / 60)));
    points.forEach((p, i) => { if ((points.length - 1 - i) % every === 0) text(g, x(i), h - 8, p.short, { "text-anchor": "middle" }); });
    points.forEach((p, i) => {
      if (p.mid == null) return;
      const d = dev(p.mid);
      const c = el("circle", { cx: x(i), cy: y(d), r: 3.4, fill: color, "fill-opacity": Math.abs(d) > 60 ? 0.95 : 0.6,
        stroke: "var(--surface)", "stroke-width": 1 }, svg);
      c.addEventListener("mousemove", (ev) => tip.show(`<div class="th">${esc(p.label)}</div><div class="tr"><span>midpoint</span><b>${hhmm(p.mid)}</b></div><div class="tr"><span>off usual by</span><b>${Math.round(Math.abs(d))} min</b></div>`, ev.clientX, ev.clientY));
      c.addEventListener("mouseleave", () => tip.hide());
    });
  }

  // --- hero ruler: chronological vs health age -------------------------------------
  function ruler(container, age, ha) {
    const { svg, w } = fresh(container);
    const lo = Math.floor(Math.min(age, ha) - 3), hi = Math.ceil(Math.max(age, ha) + 3);
    const x = scale(lo, hi, 8, w - 8), yy = 22;
    for (let v = lo; v <= hi; v++) {
      const major = v % 5 === 0;
      el("line", { x1: x(v), x2: x(v), y1: yy - (major ? 7 : 4), y2: yy + (major ? 7 : 4), stroke: "rgba(236,235,243,.22)" }, svg);
      if (major) text(svg, x(v), yy + 20, String(v), { class: "lbl", "text-anchor": "middle" });
    }
    const older = ha > age;
    el("rect", { x: Math.min(x(age), x(ha)), y: yy - 3, width: Math.max(2, Math.abs(x(ha) - x(age))), height: 6, rx: 3,
      fill: older ? "#d55181" : "#c98500", "fill-opacity": 0.85 }, svg);
    el("circle", { cx: x(age), cy: yy, r: 6, fill: "#0d0c14", stroke: "#ecebf3", "stroke-width": 2 }, svg);
    el("circle", { cx: x(ha), cy: yy, r: 6, fill: older ? "#d55181" : "#c98500", stroke: "#0d0c14", "stroke-width": 2 }, svg);
    const left = x(age) < x(ha);
    text(svg, x(age) + (left ? -10 : 10), yy - 12, `age ${age.toFixed(1)}`, { class: "lbl", "text-anchor": left ? "end" : "start" });
    text(svg, x(ha) + (left ? 10 : -10), yy - 12, `health ${ha.toFixed(1)}`, { class: "endlbl", "text-anchor": left ? "start" : "end" });
  }

  window.HC = { line, divBar, bars, spark, clock, ruler, tip, esc };
})();
