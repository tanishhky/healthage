/* healthage UI controller. Talks only to the local server (same origin). */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = window.HC.esc;

  const OPT_COLORS = ["#c98500", "#9085e9", "#199e70"];
  const OPT_TEXT = ["#e7b04a", "#b3a9ff", "#3fc79a"];
  const SQ = "#e8e6f0", MAXRED = "#8a8796";

  let STATE = null, REPORT = null, MEASURE = "core", HIDDEN = new Set(), SELECTED = 0, simTimer = null, pollTimer = null;

  async function api(path, body) {
    const opt = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Healthage": "1" }, body: JSON.stringify(body),
    };
    const r = await fetch(path, opt);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  }

  // ---- formatting ---------------------------------------------------------------
  const yrs = (v, nd = 1) => (v > 0.0499 ? "+" : v < -0.0499 ? "-" : "") + Math.abs(v).toFixed(nd);
  const int = (v) => (v == null ? "n/a" : Math.round(v).toLocaleString("en-US"));
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const short = (iso) => { const [, m, d] = iso.split("-").map(Number); return `${MONTHS[m - 1]} ${d}`; };
  function ago(iso) {
    if (!iso) return "never synced";
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 90) return "synced just now";
    if (s < 3600) return `synced ${Math.round(s / 60)} min ago`;
    if (s < 86400 * 2) return `synced ${Math.round(s / 3600)} h ago`;
    return `synced ${Math.round(s / 86400)} days ago`;
  }
  function toast(msg, ms) {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms || 3200);
  }
  function notice(lines) {
    const n = $("#notice");
    if (!lines || !lines.length) { n.hidden = true; return; }
    n.innerHTML = lines.map((l) => `<p>${esc(l)}</p>`).join("");
    n.hidden = false;
  }

  // ---- state + top bar --------------------------------------------------------------
  async function refreshState() {
    STATE = await api("/api/state");
    document.documentElement.dataset.theme = STATE.theme || "hud";
    $$("[data-theme-set]").forEach((b) => b.classList.toggle("on", b.dataset.themeSet === document.documentElement.dataset.theme));
    const chip = $("#src-chip");
    chip.className = "chip";
    if (STATE.demo) { chip.classList.add("demo"); chip.textContent = "demo data"; }
    else if (STATE.connected && STATE.has_data) { chip.classList.add("live"); chip.textContent = "live / google health"; }
    else if (STATE.has_data) { chip.textContent = "stored data / reconnect"; }
    else { chip.textContent = "not connected"; }
    $("#asof").textContent = STATE.demo ? "synthetic person, not you" : ago(STATE.synced_at);
    $("#btn-connect").hidden = STATE.connected;
    $("#btn-sync").hidden = !STATE.connected;
    $("#btn-demo").textContent = STATE.demo ? "Use my data" : "Demo";
    $("#btn-demo").hidden = !STATE.demo && !STATE.has_data && !STATE.connected ? false : false;
    $("#home-path").textContent = STATE.home;
    $("#home-path-2").textContent = STATE.home;
    $("#redirect-uri").textContent = STATE.redirect_uri;
    $("#client-state").textContent = STATE.has_client ? "client saved" : "";
    $("#sl-right").textContent = `local only / ${STATE.home}`;
    if (STATE.sync && STATE.sync.running) pollSync();
  }

  async function load() {
    if (!STATE.demo && !STATE.has_data) {
      $("#empty").hidden = false; $("#dash").hidden = true; notice(null);
      $("#sl-left").textContent = `healthage v${STATE.version} / waiting for data`;
      return;
    }
    $("#empty").hidden = true;
    try {
      REPORT = await api("/api/report");
    } catch (e) { notice([e.message]); return; }
    if (!REPORT.ok) {
      $("#dash").hidden = true;
      notice([REPORT.message]);
      if (REPORT.reason === "profile") openDrawer();
      return;
    }
    $("#dash").hidden = false;
    render();
  }

  // ---- render -------------------------------------------------------------------------
  function render() {
    const R = REPORT;
    notice(R.warnings);
    $("#crumb-who").textContent = R.demo ? "demo" : "you";
    $("#sl-left").textContent = `healthage v${STATE.version} / ${R.demo ? "demo data" : "google health"} / as of ${R.as_of}`;
    renderHero(); renderMoves(); renderDomains(); renderFutures(); renderOptions();
    initSim(); renderWeekly(); renderSignals(); renderClock(); renderLevers(); renderCoverage();
  }

  function renderHero() {
    const R = REPORT, m = MEASURE === "core" ? R.core : R.extended;
    $("#ha-num").textContent = m.health_age.toFixed(1);
    const d = $("#ha-delta");
    d.textContent = `${yrs(m.delta)} y`;
    d.className = "delta " + (m.delta > 0.05 ? "older" : "younger");
    $("#ha-sub").textContent = `chronological age ${R.age.toFixed(1)}, data to ${short(R.as_of)}`;
    $("#ha-note").textContent = MEASURE === "core"
      ? "Core: resting heart rate, steps, main sleep and Active Zone Minutes, the four domains every wearable measures."
      : "Extended: core plus whichever optional domains you have: sleep timing, insulin resistance (labs), VO2 max.";
    window.HC.ruler($("#ruler"), R.age, m.health_age);
    $$("[data-measure]").forEach((b) => b.classList.toggle("on", b.dataset.measure === MEASURE));
  }

  function targetTags(t, base) {
    const tags = [];
    if (Math.abs(t.steps - base.steps) > 1) tags.push(`${int(t.steps)} steps a day`);
    if (Math.abs(t.cardio_week - base.cardio_week) > 1) tags.push(`${int(t.cardio_week)} cardio min a week`);
    if (Math.abs(t.sleep_h - base.sleep_h) > 0.01) tags.push(`${t.sleep_h.toFixed(1)} h sleep`);
    return tags.map((x) => `<span class="tag">${esc(x)}</span>`).join("");
  }

  function renderMoves() {
    const R = REPORT, ol = $("#moves");
    if (!R.actions.length) {
      ol.innerHTML = `<li class="move"><span class="rank">--</span><div><div class="t">Nothing clears the 0.1 year bar</div><div class="d">Your current habits already sit near the model's ceiling.</div></div><div class="y"></div></li>`;
      return;
    }
    ol.innerHTML = R.actions.map((a, i) => {
      let t = a.title, d = "", y = `-${a.years.toFixed(1)}`, s = "years";
      if (a.kind === "plan") {
        t = `Follow the ${a.title.replace("Best plan: ", "")} plan`;
        d = `Projected by week 12 if it holds.<div class="tags">${targetTags(a.targets, R.baseline)}</div>`;
        s = "by week 12";
      } else if (a.kind === "sleep_timing") {
        t = "Keep sleep timing steady";
        d = `Your sleep midpoint moves ${int(R.baseline.sleep_mid_sd)} min night to night. Same wake time every day brings it toward 60.`;
        s = "extended";
      } else if (a.kind === "labs") {
        t = "Measure insulin resistance";
        d = `Your labs flag it (surrogate). Ask for ${esc(a.detail)}. Diet and weight move it; wearables do not.`;
        y = a.years.toFixed(1); s = "at stake";
      }
      return `<li class="move"><span class="rank">${String(i + 1).padStart(2, "0")}</span><div><div class="t">${esc(t)}</div><div class="d">${d}</div></div><div class="y">${y}<small>${s}</small></div></li>`;
    }).join("");
  }

  function renderDomains() {
    const R = REPORT, box = $("#domains");
    const maxAbs = Math.max(1, ...R.domains.map((d) => Math.abs(d.years || 0)));
    const val = (d) => {
      if (d.key === "ir") return d.flag ? "flagged" : "not flagged";
      if (d.value == null) return "no data";
      if (d.key === "steps") return `${int(d.value)} a day`;
      if (d.key === "azm") return `${int(d.value)} a week`;
      if (d.key === "vo2") return `${d.value} vs ${d.expected} expected`;
      return `${d.value} ${d.unit}`;
    };
    box.innerHTML = R.domains.map((d, i) => {
      const y = d.years, cls = y == null ? "zero" : y > 0.005 ? "pos" : y < -0.005 ? "neg" : "zero";
      return `<div class="dom"><div class="name">${esc(d.label)}${d.optional ? '<span class="opt">OPT</span>' : ""}<small>${esc(val(d))}</small></div>
        <div class="bar" id="db-${i}"></div><div class="yrs ${cls}">${y == null ? "missing" : yrs(y, 2)}</div></div>`;
    }).join("");
    R.domains.forEach((d, i) => {
      const host = $(`#db-${i}`);
      host.style.height = "18px";
      window.HC.divBar(host, d.years, maxAbs);
      host.title = `reference: ${d.reference}`;
    });
  }

  function futuresSeries() {
    const F = REPORT.futures;
    const s = [{ id: "sq", label: "Status quo", short: "now", color: SQ, values: F.status_quo.map((p) => p.health_age) }];
    F.options.forEach((o, i) => s.push({
      id: `o${i}`, label: `Option ${i + 1}: ${o.pathway}`, short: `${i + 1}. ${o.pathway}`, color: OPT_COLORS[i],
      values: o.path.map((p) => p.health_age), width: i === SELECTED ? 3 : 2,
    }));
    if (F.max_reduction) s.push({ id: "max", label: "Max reduction", short: "max", color: MAXRED, dash: "6 5",
      values: F.max_reduction.path.map((p) => p.health_age) });
    return s;
  }

  function renderFutures() {
    const series = futuresSeries();
    $("#futures-legend").innerHTML = series.map((s) =>
      `<span class="lg ${HIDDEN.has(s.id) ? "off" : ""}" data-sid="${s.id}"><i data-c="${s.color}"></i>${esc(s.label)}</span>`).join("")
      + `<span class="lg off" title="other plans that clear 0.1 years"><i data-c="rgba(169,166,184,.4)"></i>${REPORT.futures.n_feasible} other feasible plans</span>`;
    $$("#futures-legend i[data-c]").forEach((i) => (i.style.background = i.dataset.c));
    $$("#futures-legend [data-sid]").forEach((n) => n.addEventListener("click", () => {
      const id = n.dataset.sid;
      HIDDEN.has(id) ? HIDDEN.delete(id) : HIDDEN.add(id);
      renderFutures();
    }));
    series.forEach((s) => (s.hidden = HIDDEN.has(s.id)));
    window.HC.line($("#futures"), {
      series, cloud: REPORT.futures.cloud, endLabels: true,
      xLabels: REPORT.futures.status_quo.map((p, i) => (i === 0 ? "now" : `w${i}`)),
      tipTitle: (i) => (i === 0 ? "now" : `week ${i}`),
      ref: { y: REPORT.age, label: `your age ${REPORT.age.toFixed(1)}` },
    });
  }

  function renderOptions() {
    const R = REPORT, box = $("#options"), B = R.baseline;
    const opts = R.futures.options;
    const maxE = Math.max(1, ...opts.map((o) => o.effort));
    let html = opts.map((o, i) => {
      const end = o.path[o.path.length - 1], t = o.targets;
      return `<article class="panel opt-card ${i === SELECTED ? "sel" : ""}" data-i="${i}" data-c="${OPT_COLORS[i]}" data-c2="${OPT_TEXT[i]}">
        <span class="bar"></span>
        <p class="k">option ${i + 1}</p><p class="p">${esc(o.pathway)}</p>
        <div class="ben">-${o.benefit.toFixed(2)}<small> y by week 12</small></div>
        <p class="fine">pessimistic band -${(o.benefit_low ?? o.benefit).toFixed(2)} y</p>
        <dl>
          <dt>Steps/day</dt><dd>${int(t.steps)}${Math.abs(t.steps - B.steps) > 1 ? ` <span class="muted">from ${int(B.steps)}</span>` : ""}</dd>
          <dt>Cardio/wk</dt><dd>${int(t.cardio_week)} min${Math.abs(t.cardio_week - B.cardio_week) > 1 ? ` <span class="muted">from ${int(B.cardio_week)}</span>` : ""}</dd>
          <dt>Sleep</dt><dd>${t.sleep_h.toFixed(1)} h</dd>
          <dt>RHR wk 12</dt><dd>${end.rhr.toFixed(1)} bpm</dd>
          <dt>Effort</dt><dd>${o.effort.toFixed(1)}</dd>
        </dl>
        <div class="meter"><i data-w="${Math.max(4, (o.effort / maxE) * 100)}"></i></div>
        <div class="mini" id="mini-${i}"></div>
      </article>`;
    }).join("");
    for (let i = opts.length; i < 3; i++) {
      html += `<article class="panel opt-card empty-opt"><p class="k">option ${i + 1}</p><p class="p muted">No other pathway clears 0.1 years</p><p class="fine">Plans whose extra targets add nothing are pruned.</p></article>`;
    }
    box.innerHTML = html;
    $$(".opt-card[data-i]", box).forEach((c) => {
      c.style.setProperty("--c", c.dataset.c);
      c.style.setProperty("--c2", c.dataset.c2);
      const m = $(".meter i", c); if (m) m.style.width = m.dataset.w + "%";
      const i = +c.dataset.i, o = opts[i];
      window.HC.line($(`#mini-${i}`, c), {
        series: [
          { id: "sq", label: "Status quo", color: "rgba(232,230,240,.55)", width: 1.5, values: R.futures.status_quo.map((p) => p.health_age) },
          { id: "o", label: `Option ${i + 1}`, color: OPT_COLORS[i], values: o.path.map((p) => p.health_age) },
        ],
        pad: { l: 34, r: 16, t: 6, b: 20 },
        xLabels: o.path.map((_, k) => (k === 0 ? "now" : k % 4 === 0 ? `w${k}` : "")),
        tipTitle: (k) => (k === 0 ? "now" : `week ${k}`),
      });
      c.addEventListener("click", () => {
        SELECTED = +c.dataset.i;
        renderOptions(); renderFutures();
        setSim(REPORT.futures.options[SELECTED].targets, REPORT.baseline.sleep_mid_sd);
      });
    });
  }

  // ---- simulator ---------------------------------------------------------------------
  function setSim(t, sd) {
    $("#s-steps").value = t.steps; $("#s-cardio").value = t.cardio_week; $("#s-sleep").value = t.sleep_h;
    if (sd != null) $("#s-sd").value = Math.min(180, sd);
    simChanged(true);
  }
  function initSim() {
    const B = REPORT.baseline;
    $("#l-sd").hidden = B.sleep_mid_sd == null;
    const o = REPORT.futures.options[SELECTED] || REPORT.futures.options[0];
    setSim(o ? o.targets : { steps: B.steps, cardio_week: B.cardio_week, sleep_h: B.sleep_h }, B.sleep_mid_sd);
  }
  function simChanged(now) {
    const v = { steps: +$("#s-steps").value, cardio_week: +$("#s-cardio").value, sleep_h: +$("#s-sleep").value };
    $("#o-steps").textContent = int(v.steps);
    $("#o-cardio").textContent = `${v.cardio_week} min`;
    $("#o-sleep").textContent = `${v.sleep_h.toFixed(1)} h`;
    if (!$("#l-sd").hidden) { v.sleep_mid_sd = +$("#s-sd").value; $("#o-sd").textContent = `${v.sleep_mid_sd} min`; }
    clearTimeout(simTimer);
    simTimer = setTimeout(() => runSim(v), now ? 0 : 140);
  }
  async function runSim(v) {
    let r;
    try { r = await api("/api/simulate", v); } catch (e) { return; }
    const core = r.core_path[r.core_path.length - 1], ext = r.extended_path[r.extended_path.length - 1];
    $("#sim-core").textContent = core.toFixed(1);
    $("#sim-ext").textContent = ext.toFixed(1);
    $("#sim-benefit").textContent = `${r.benefit >= 0 ? "-" : "+"}${Math.abs(r.benefit).toFixed(2)} y`;
    $("#sim-effort").textContent = r.effort.toFixed(1);
    window.HC.line($("#sim-chart"), {
      series: [
        { id: "c", label: "core", short: "core", color: "#c98500", values: r.core_path },
        { id: "e", label: "extended", short: "extended", color: "#9085e9", values: r.extended_path },
      ],
      endLabels: true, pad: { l: 40, r: 110, b: 22 },
      xLabels: r.core_path.map((_, i) => (i === 0 ? "now" : `w${i}`)), tipTitle: (i) => (i === 0 ? "now" : `week ${i}`),
      ref: { y: REPORT.core.health_age, label: "core now" },
    });
  }

  // ---- history, signals, clock, levers, coverage ------------------------------------
  function renderWeekly() {
    const items = REPORT.weekly.map((w) => ({
      label: short(w.week), value: w.delta, partial: w.partial || w.complete_days < 3,
      html: `<div class="th">week of ${esc(short(w.week))}${w.partial ? " (partial)" : ""}</div>
        <div class="tr"><span>Health Age minus age</span><b>${yrs(w.delta, 2)}</b></div>
        <div class="tr"><span>steps a day</span><b>${int(w.steps)}</b></div>
        <div class="tr"><span>resting HR</span><b>${w.rhr ?? "n/a"}</b></div>
        <div class="tr"><span>AZM a week</span><b>${int(w.azm_week)}</b></div>
        <div class="tr"><span>sleep h</span><b>${w.sleep_h ?? "n/a"}</b></div>
        <div class="tr"><span>complete days</span><b>${w.complete_days}</b></div>`,
    }));
    window.HC.bars($("#weekly"), items);
  }

  function renderSignals() {
    const D = REPORT.daily, B = REPORT.baseline;
    const tiles = [
      { k: "steps a day", key: "steps", color: "#c98500", fmt: (v) => int(v), base: int(B.steps) },
      { k: "resting heart rate", key: "rhr", color: "#d55181", fmt: (v) => v.toFixed(1), base: `${B.rhr} bpm` },
      { k: "active zone min a day", key: "azm", color: "#199e70", fmt: (v) => v.toFixed(0), base: `${int(B.azm_week)} a week` },
      { k: "main sleep, hours", key: "sleep_h", color: "#9085e9", fmt: (v) => v.toFixed(1), base: `${B.sleep_h} h` },
    ];
    $("#signals").innerHTML = tiles.map((t, i) => {
      const vals = D.map((d) => d[t.key]).filter((v) => v != null).slice(-7);
      const last7 = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
      return `<div class="sig"><div class="sk">${esc(t.k)}</div><div class="sv">${last7 == null ? "n/a" : t.fmt(last7)}<small> 7-day / base ${esc(t.base)}</small></div><div id="sp-${i}"></div></div>`;
    }).join("");
    tiles.forEach((t, i) => {
      const host = $(`#sp-${i}`); host.style.height = "44px";
      window.HC.spark(host, D.map((d) => ({ label: short(d.date), v: d[t.key] })), t.color, t.fmt);
    });
  }

  function renderClock() {
    window.HC.clock($("#clock"), REPORT.daily.map((d) => ({ label: short(d.date), short: short(d.date), mid: d.sleep_mid })), "#9085e9");
  }

  function renderLevers() {
    const R = REPORT, box = $("#levers");
    const reg = R.domains.find((d) => d.key === "regularity");
    if (!reg) { box.innerHTML = `<p class="muted small">Needs 7 nights with sleep start and end times in the last 28 days.</p>`; return; }
    const rows = [`<tr class="now"><td>${int(R.baseline.sleep_mid_sd)} min (now)</td><td>${yrs(reg.years, 2)}</td><td>${R.extended.health_age.toFixed(1)}</td></tr>`]
      .concat(R.levers.map((l) => `<tr><td>${l.sd} min</td><td>${yrs(l.years, 2)}</td><td>${l.extended.toFixed(1)}</td></tr>`));
    box.innerHTML = `<p class="small muted">The paper's optimizer holds sleep timing fixed; this extension shows what steadier timing is worth, all else equal.</p>
      <table class="t"><thead><tr><th>Midpoint spread</th><th>Years</th><th>Extended age</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
  }

  function renderCoverage() {
    const R = REPORT, n = R.coverage.n, w = R.coverage.window, M = R.model;
    const win = (k) => (w[k] ? `${n[k]} of ${w[k]} days` : "none");
    const rows = [
      ["Data through", R.as_of],
      ["Steps", win("steps")], ["Resting heart rate", win("rhr")], ["Main sleep", win("sleep")],
      ["Active Zone Minutes", win("azm")], ["Nights with sleep timing (28 days)", `${n.sleep_mid}`],
      ["Fat-burn minutes per 1,000 steps", M.fatburn_n >= 14 ? `${M.fatburn_per_1k} (fitted on ${M.fatburn_n} days)` : "not fitted (needs 14 days)"],
      ["Your week-to-week range (effort scale)", `${int(M.sigma.steps)} steps, ${M.sigma.sleep_min} sleep min, ${M.sigma.cardio_min_day} cardio min a day`],
      ["Resting HR response to 150 cardio min a week", `-${(M.rhr_effect * 100).toFixed(0)}% (pessimistic -${(M.rhr_effect_low * 100).toFixed(1)}%)`],
      ["Insulin resistance input", R.insulin_resistance_source],
    ];
    $("#coverage").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  }

  // ---- sync ------------------------------------------------------------------------------
  async function startSync() {
    try {
      await api("/api/sync", { days: +$("#sync-days").value || 120 });
      pollSync();
    } catch (e) { toast(e.message, 6000); }
  }
  function pollSync() {
    const bar = $("#syncbar");
    bar.hidden = false;
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      let s;
      try { s = await api("/api/sync"); } catch (e) { return; }
      $("i", bar).style.width = `${Math.round(s.progress * 100)}%`;
      $("span", bar).textContent = s.running ? `syncing ${s.stage}` : "";
      if (!s.running) {
        clearInterval(pollTimer);
        setTimeout(() => (bar.hidden = true), 600);
        if (s.error) { notice([s.error]); toast("Sync failed", 4000); }
        else toast("Synced");
        await refreshState();
        if (!s.error && !(await api("/api/profile")).dob) openDrawer();
        await load();
      }
    }, 700);
  }

  // ---- modal, drawer, settings -----------------------------------------------------------
  function openConnect() { $("#m-connect").hidden = false; }
  function openDrawer() { $("#drawer").hidden = false; fillProfile(); }
  function closeAll() { $("#m-connect").hidden = true; $("#drawer").hidden = true; }

  async function fillProfile() {
    const p = await api("/api/profile");
    const f = $("#f-profile");
    f.dob.value = p.dob || "";
    f.sex.value = p.sex || "male";
    const L = p.labs || {};
    f.labs_date.value = L.date || ""; f.hba1c_pct.value = L.hba1c_pct ?? ""; f.triglycerides_mg_dl.value = L.triglycerides_mg_dl ?? "";
    f.hdl_mg_dl.value = L.hdl_mg_dl ?? "";
    f.insulin_resistant.value = p.insulin_resistant == null ? "auto" : String(p.insulin_resistant);
    f.max_cardio_min_week.value = p.max_cardio_min_week || 180;
  }

  function bind() {
    $("#btn-connect").addEventListener("click", openConnect);
    $$("[data-open=connect]").forEach((b) => b.addEventListener("click", () => { closeAll(); openConnect(); }));
    $("#btn-settings").addEventListener("click", openDrawer);
    $$("[data-close]").forEach((b) => b.addEventListener("click", closeAll));
    [$("#m-connect"), $("#drawer")].forEach((m) => m.addEventListener("click", (e) => { if (e.target === m) closeAll(); }));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeAll(); });

    $("#btn-sync").addEventListener("click", startSync);
    const toggleDemo = async () => {
      if (STATE.demo && !STATE.has_data) { openConnect(); return; }
      await api("/api/mode", { demo: !STATE.demo });
      SELECTED = 0; HIDDEN.clear();
      await refreshState(); await load();
    };
    $("#btn-demo").addEventListener("click", toggleDemo);
    $("#btn-demo-2").addEventListener("click", async () => { await api("/api/mode", { demo: true }); await refreshState(); await load(); });

    $$("[data-theme-set]").forEach((b) => b.addEventListener("click", async () => {
      document.documentElement.dataset.theme = b.dataset.themeSet;
      $$("[data-theme-set]").forEach((x) => x.classList.toggle("on", x === b));
      api("/api/mode", { theme: b.dataset.themeSet }).catch(() => {});
      if (REPORT && REPORT.ok) render();
    }));
    $$("[data-measure]").forEach((b) => b.addEventListener("click", () => { MEASURE = b.dataset.measure; renderHero(); }));

    $("#client-file").addEventListener("change", async (e) => {
      const f = e.target.files[0]; if (f) $("#client-json").value = await f.text();
    });
    $("#btn-save-client").addEventListener("click", async () => {
      try { await api("/api/client", { json: $("#client-json").value }); $("#client-state").textContent = "client saved"; await refreshState(); }
      catch (e) { $("#client-state").textContent = e.message; }
    });
    $("#btn-go-google").addEventListener("click", async () => {
      try {
        if ($("#client-json").value.trim()) await api("/api/client", { json: $("#client-json").value });
        const r = await api("/api/connect", {});
        window.location.href = r.url;
      } catch (e) { $("#client-state").textContent = e.message; }
    });

    $("#f-profile").addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = Object.fromEntries(new FormData(e.target).entries());
      try { await api("/api/profile", body); $("#profile-state").textContent = "saved"; toast("Profile saved"); await refreshState(); await load(); }
      catch (err) { $("#profile-state").textContent = err.message; }
    });
    $("#btn-disconnect").addEventListener("click", async () => { await api("/api/disconnect", {}); toast("Disconnected"); await refreshState(); });
    const del = $("#btn-delete");
    del.addEventListener("click", async () => {
      if (!del.dataset.armed) { del.dataset.armed = "1"; del.textContent = "Click again to delete everything"; setTimeout(() => { delete del.dataset.armed; del.textContent = "Delete all local data"; }, 4000); return; }
      await api("/api/delete", {}); delete del.dataset.armed; del.textContent = "Delete all local data";
      closeAll(); toast("Local data deleted"); await refreshState(); await load();
    });

    ["#s-steps", "#s-cardio", "#s-sleep", "#s-sd"].forEach((s) => $(s).addEventListener("input", () => simChanged(false)));
    $("#sim-reset").addEventListener("click", () => {
      const B = REPORT.baseline;
      setSim({ steps: B.steps, cardio_week: B.cardio_week, sleep_h: B.sleep_h }, B.sleep_mid_sd);
    });

    let rt;
    window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => REPORT && REPORT.ok && render(), 150); });
  }

  async function handleHash() {
    const h = location.hash;
    if (!h) return;
    history.replaceState(null, "", location.pathname);
    if (h === "#connected") { toast("Connected to Google Health. First sync running.", 4000); await startSync(); }
    else if (h === "#connect") openConnect();
    else if (h === "#settings") openDrawer();
    else if (h.startsWith("#error=")) notice([`Google sign-in did not finish: ${decodeURIComponent(h.slice(7))}`]);
  }

  async function boot() {
    bind();
    await refreshState();
    await handleHash();
    await load();
  }
  boot().catch((e) => notice([e.message]));
})();
