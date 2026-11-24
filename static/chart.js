/* 📈 Chart tab: performance across test sessions.
   Data: the saved JSON of every performance (elbencho) run of the open site. Each run is one "test session";
   each benchmark phase (WRITE, READ, ...) is drawn over the wall-clock time it ran, at its AVERAGE throughput
   (elbencho's "last done" value = all bytes / whole phase), like an "Avg" aggregation. No per-second samples
   are recorded, so a phase is a plateau, not a wiggly line.
   Views: Timeline (real clock time) and Compare sessions (grouped bars). The session list shows/hides runs;
   the timeline zooms to the visible ones. Uses globals from app.js: $, $$, esc, api, store, logData, loadResults, CFG. */

const CHART = {
  view: "timeline", metric: "tp", unit: "GB",
  sessions: [],            // [{id, label, status, start, end, clients, phases:[{op,start,end,mib,iops,perClient}]}]
  cache: new Map(),        // run id -> parsed session (runs never change after they are written)
  hiddenSessions: new Set(), hiddenOps: new Set(),
};
// categorical slots in FIXED order (validated: blue/orange pass all checks on the app's light & dark cards)
const OP_SLOTS = ["WRITE", "READ"];
const opSlot = (op) => { const i = OP_SLOTS.indexOf(op); return i >= 0 ? i + 1 : Math.min(8, 3 + [...new Set(CHART.sessions.flatMap((s) => s.phases.map((p) => p.op)))].filter((o) => !OP_SLOTS.includes(o)).sort().indexOf(op)); };
const opColor = (op) => `var(--series-${opSlot(op)})`;
const opName = (op) => op.charAt(0) + op.slice(1).toLowerCase();
const UNIT_DIV = { MB: 1e6, GB: 1e9, TB: 1e12 };
const UNIT_DIGITS = { MB: 0, GB: 2, TB: 4 };

const chartKey = (what) => `chart:${what}:${CFG?.site?.path || ""}`;
function chartLoadPrefs() {
  try { CHART.hiddenSessions = new Set(JSON.parse(store.get(chartKey("hidden")) || "[]")); } catch { CHART.hiddenSessions = new Set(); }
  try { CHART.hiddenOps = new Set(JSON.parse(store.get(chartKey("hiddenOps")) || "[]")); } catch { CHART.hiddenOps = new Set(); }
  CHART.view = store.get("chart:view") || "timeline";
  CHART.metric = store.get("chart:metric") || "tp";
  CHART.unit = store.get("chart:unit") || store.get("perfUnit") || "GB";
}
function chartSavePrefs() {
  store.set(chartKey("hidden"), JSON.stringify([...CHART.hiddenSessions]));
  store.set(chartKey("hiddenOps"), JSON.stringify([...CHART.hiddenOps]));
  store.set("chart:view", CHART.view); store.set("chart:metric", CHART.metric); store.set("chart:unit", CHART.unit);
}

/* ---- data: run JSON -> session with timed phases ---- */
function runToSession(run) {
  const t = (iso) => (iso ? Date.parse(iso) : NaN);
  const clients = (run.clients || []).map((c) => c.client);
  const phases = [];
  const nCmd = Math.max(0, ...(run.clients || []).map((c) => (c.commands || []).length));
  for (let i = 0; i < nCmd; i++) {
    const per = (run.clients || []).map((c) => ({ client: c.client, cmd: (c.commands || [])[i] })).filter((x) => x.cmd);
    const starts = per.map((x) => t(x.cmd.started_at)).filter(Number.isFinite);
    if (!starts.length) continue;
    let cursor = Math.min(...starts);
    const ops = [];   // ops in the order elbencho ran them inside this command (e.g. MKDIRS then WRITE)
    per.forEach((x) => (x.cmd.elbencho || []).forEach((o) => { if (!ops.includes(o.operation)) ops.push(o.operation); }));
    for (const op of ops) {
      const rows = per.map((x) => ({ client: x.client, o: (x.cmd.elbencho || []).find((e) => e.operation === op) })).filter((r) => r.o);
      const m = (r, k) => r.o.metrics?.[k]?.last_done ?? null;
      const dur = Math.max(0, ...rows.map((r) => m(r, "Elapsed time") || 0));
      const perClient = rows.map((r) => ({ client: r.client, mib: m(r, "Throughput MiB/s"), iops: m(r, "IOPS") }));
      const sum = (k) => perClient.reduce((a, c) => a + (c[k] || 0), 0);
      const hasTp = perClient.some((c) => c.mib != null);
      if (hasTp && dur > 0) phases.push({ op, cmdIndex: i + 1, start: cursor, end: cursor + dur * 1000, mib: sum("mib"), iops: sum("iops"), perClient });
      cursor += dur * 1000;   // metadata phases (MKDIRS, RMFILES) still take time before the next one
    }
  }
  const start = phases.length ? Math.min(...phases.map((p) => p.start)) : t(run.started_at);
  const end = phases.length ? Math.max(...phases.map((p) => p.end)) : t(run.finished_at) || start;
  return { id: run.run_id, label: run.label || run.group, status: run.status, start, end, clients, phases };
}

async function loadChart() {
  if (!logData?.logs?.length) await loadResults();
  const runs = (logData.logs || []).filter((l) => l.performance && l.files.some((f) => f.name.endsWith(".json")));
  const out = [];
  for (const l of runs) {
    let s = CHART.cache.get(l.id);
    if (!s) {
      try { s = runToSession(await (await fetch(`/api/results/${encodeURIComponent(l.id)}.json`)).json()); CHART.cache.set(l.id, s); }
      catch { continue; }
    }
    if (s.phases.length) out.push(s);
  }
  CHART.sessions = out.sort((a, b) => a.start - b.start);
  renderChart();
}

/* ---- formatting ---- */
const metricVal = (p) => (CHART.metric === "tp" ? (p.mib * 1048576) / UNIT_DIV[CHART.unit] : p.iops);
const metricLabel = () => (CHART.metric === "tp" ? `${CHART.unit}/s` : "IOPS");
function fmtMetric(v) {
  if (v == null || !Number.isFinite(v)) return "–";
  const d = CHART.metric === "tp" ? UNIT_DIGITS[CHART.unit] : 0;
  return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtAxis(v) {   // clean numbers; compact past 10,000
  if (v >= 10000) return `${+(v / 1000).toFixed(1)}K`;
  return v.toLocaleString(undefined, { maximumFractionDigits: v < 1 ? 3 : v < 10 ? 2 : 0 });
}
const fmtDur = (ms) => { const s = Math.round(ms / 1000); return s >= 60 ? `${Math.floor(s / 60)} m ${s % 60} s` : `${s} s`; };
const fmtClock = (ms, withDate) => new Date(ms).toLocaleString(undefined, withDate
  ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" });
function niceTicks(max, n = 5) {
  if (!(max > 0)) return [0, 1];
  const raw = max / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((x) => x * mag).find((x) => x >= raw);
  const ticks = []; for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(+v.toFixed(10));
  if (ticks[ticks.length - 1] < max) ticks.push(+(ticks[ticks.length - 1] + step).toFixed(10));
  return ticks;
}
function timeTicks(t0, t1, px) {
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440, 2880, 10080].map((m) => m * 60000);
  const want = Math.max(2, Math.floor(px / 110));
  const step = steps.find((s) => (t1 - t0) / s <= want) || steps[steps.length - 1];
  const out = []; for (let v = Math.ceil(t0 / step) * step; v <= t1; v += step) out.push(v);
  return out;
}

/* ---- render ---- */
const svgNS = "http://www.w3.org/2000/svg";
function el(tag, attrs = {}, parent) {
  const n = document.createElementNS(svgNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}
const visibleSessions = () => CHART.sessions.filter((s) => !CHART.hiddenSessions.has(s.id));
const allOps = () => [...new Set(CHART.sessions.flatMap((s) => s.phases.map((p) => p.op)))].sort((a, b) => opSlot(a) - opSlot(b));

function renderChart() {
  if (!$("#t6")) return;
  $("#chartTip").hidden = true;   // a tooltip never outlives the drawing it belongs to
  $$(".chart-filters [data-view]").forEach((b) => { const on = b.dataset.view === CHART.view; b.classList.toggle("primary", on); b.setAttribute("aria-checked", on); });
  $("#chartMetric").value = CHART.metric; $("#chartUnit").value = CHART.unit;
  $("#chartUnitWrap").hidden = CHART.metric !== "tp";
  renderSessionList(); renderLegend();
  const vis = visibleSessions(), empty = $("#chartEmpty"), wrap = $("#chartWrap");
  const nPh = vis.reduce((a, s) => a + s.phases.filter((p) => !CHART.hiddenOps.has(p.op)).length, 0);
  $("#chartInfo").textContent = CHART.sessions.length
    ? `${vis.length} of ${CHART.sessions.length} test sessions shown · ${CHART.view === "timeline" ? `histogram · ${fmtBucket(CHART.bucket || 60000)} buckets, time-weighted average` : "average per phase, side by side"} · ${metricLabel()}`
    : "";
  if (!CHART.sessions.length || !nPh) {
    wrap.hidden = true; empty.hidden = false;
    empty.textContent = !CHART.sessions.length
      ? "No performance runs yet. Run Elbencho Write, Read or Auto Write/Read (Presets → Performance Test) in tab ② and they appear here."
      : "All test sessions or series are hidden. Tick a session on the right (or a series above) to show it.";
    renderChartTable(); return;
  }
  wrap.hidden = false; empty.hidden = true;
  (CHART.view === "compare" ? renderCompare : renderTimeline)(vis);
  $("#chartInfo").textContent = CHART.sessions.length
    ? `${vis.length} of ${CHART.sessions.length} test sessions shown · ${CHART.view === "timeline" ? `histogram · ${fmtBucket(CHART.bucket || 60000)} buckets, time-weighted average` : "average per phase, side by side"} · ${metricLabel()}`
    : "";   // again: the bucket size is known only after drawing
  renderChartTable();
}

function renderLegend() {
  const box = $("#chartLegend");
  box.innerHTML = "";
  for (const op of allOps()) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "lg-item"; b.setAttribute("aria-pressed", String(!CHART.hiddenOps.has(op)));
    b.title = CHART.hiddenOps.has(op) ? `Show ${opName(op)}` : `Hide ${opName(op)}`;
    const key = document.createElement("i"); key.style.background = opColor(op);
    const name = document.createElement("span"); name.textContent = opName(op);
    b.append(key, name);
    b.onclick = () => { CHART.hiddenOps.has(op) ? CHART.hiddenOps.delete(op) : CHART.hiddenOps.add(op); chartSavePrefs(); renderChart(); };
    box.appendChild(b);
  }
}

function renderSessionList() {
  const list = $("#csList");
  list.innerHTML = "";
  if (!CHART.sessions.length) { list.innerHTML = '<div class="muted small cs-none">No test sessions yet.</div>'; return; }
  let day = "";
  for (const s of [...CHART.sessions].reverse()) {      // newest first, like the Logs list
    const d = new Date(s.start).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
    if (d !== day) { day = d; const h = document.createElement("div"); h.className = "cs-day"; h.textContent = d; list.appendChild(h); }
    const row = document.createElement("label");
    row.className = "cs-row" + (s.status === "failed" ? " failed" : "");
    row.dataset.id = s.id;
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = !CHART.hiddenSessions.has(s.id);
    cb.onchange = () => { cb.checked ? CHART.hiddenSessions.delete(s.id) : CHART.hiddenSessions.add(s.id); chartSavePrefs(); renderChart(); };
    const main = document.createElement("span"); main.className = "cs-main";
    const top = document.createElement("span"); top.className = "cs-top";
    const nm = document.createElement("b"); nm.textContent = s.label;
    const tm = document.createElement("span"); tm.className = "muted"; tm.textContent = new Date(s.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    top.append(nm, tm);
    const sub = document.createElement("span"); sub.className = "cs-sub";
    for (const op of [...new Set(s.phases.map((p) => p.op))]) {   // best value per operation in this session
      const best = Math.max(...s.phases.filter((p) => p.op === op).map(metricVal));
      const it = document.createElement("span");
      const k = document.createElement("i"); k.style.background = opColor(op);
      it.append(k, document.createTextNode(`${opName(op)} ${fmtMetric(best)}`));
      sub.appendChild(it);
    }
    const unit = document.createElement("span"); unit.className = "muted"; unit.textContent = metricLabel();
    sub.appendChild(unit);
    const cl = document.createElement("span"); cl.className = "cs-cl muted"; cl.textContent = `${s.clients.length} client${s.clients.length === 1 ? "" : "s"}${s.status === "failed" ? " · failed" : ""}`;
    main.append(top, sub, cl);
    const only = document.createElement("button"); only.type = "button"; only.className = "btn sm ghost cs-only"; only.textContent = "only";
    only.title = "Show only this session";
    only.onclick = (e) => { e.preventDefault(); CHART.hiddenSessions = new Set(CHART.sessions.filter((x) => x.id !== s.id).map((x) => x.id)); chartSavePrefs(); renderChart(); };
    row.append(cb, main, only);
    row.onmouseenter = () => chartHighlight(s.id); row.onmouseleave = () => chartHighlight(null);
    list.appendChild(row);
  }
}
function chartHighlight(id) {
  $$("#perfChart [data-session]").forEach((n) => n.classList.toggle("dim", !!id && n.dataset.session !== id));
}

function chartFrame(svg, width, height) {
  svg.innerHTML = "";
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", width); svg.setAttribute("height", height);
  return { m: { l: 56, r: 16, t: 26, b: 34 } };
}
function yAxis(svg, m, width, height, ticks, y) {
  const g = el("g", { class: "ax" }, svg);
  for (const v of ticks) {
    el("line", { x1: m.l, x2: width - m.r, y1: y(v), y2: y(v), class: v === 0 ? "base" : "grid" }, g);
    const tx = el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: "tick" }, g); tx.textContent = fmtAxis(v);
  }
  const lab = el("text", { x: 14, y: m.t + (height - m.t - m.b) / 2, class: "axlab", transform: `rotate(-90 14 ${m.t + (height - m.t - m.b) / 2})`, "text-anchor": "middle" }, g);
  lab.textContent = metricLabel();
}

// Histogram over clock time: even time buckets; each column = the time-weighted average of the phases that
// ran in that bucket (a bucket half-filled by a 5 GB/s write shows 2.5 GB/s). Write and Read sit side by side.
const BUCKETS = [10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 21600, 43200, 86400].map((s) => s * 1000);
function bucketize(phases, t0, t1, ops, maxCols) {
  const size = BUCKETS.find((b) => (t1 - t0) / b <= maxCols) || BUCKETS[BUCKETS.length - 1];
  const start = Math.floor(t0 / size) * size, n = Math.ceil((t1 - start) / size);
  const cols = [];
  for (let i = 0; i < n; i++) {
    const b0 = start + i * size, b1 = b0 + size;
    const vals = {}, by = {};
    for (const op of ops) {
      let acc = 0; const parts = [];
      for (const p of phases) {
        if (p.op !== op) continue;
        const ov = Math.min(b1, p.end) - Math.max(b0, p.start);
        if (ov <= 0) continue;
        acc += metricVal(p) * (ov / size);
        parts.push({ p, frac: ov / size });
      }
      if (parts.length) { vals[op] = acc; by[op] = parts; }
    }
    if (Object.keys(vals).length) cols.push({ b0, b1, vals, by });
  }
  return { size, start, n, cols };
}
const fmtBucket = (ms) => (ms >= 3600000 ? `${ms / 3600000} h` : ms >= 60000 ? `${ms / 60000} min` : `${ms / 1000} s`);

function renderTimeline(vis) {
  const svg = $("#perfChart"), wrap = $("#chartWrap");
  const width = Math.max(480, wrap.clientWidth), height = 380;
  const { m } = chartFrame(svg, width, height);
  const ops = allOps().filter((o) => !CHART.hiddenOps.has(o));
  const phases = vis.flatMap((s) => s.phases.filter((p) => !CHART.hiddenOps.has(p.op)).map((p) => ({ ...p, s })));
  let t0 = Math.min(...phases.map((p) => p.start)), t1 = Math.max(...phases.map((p) => p.end));
  const pad = Math.max(30000, (t1 - t0) * 0.02); t0 -= pad; t1 += pad;
  // ~1 column per 10px (and at most 120), so each bar stays thin but readable
  const H = bucketize(phases, t0, t1, ops, Math.min(120, Math.floor((width - m.l - m.r) / 10)));
  t0 = H.start; t1 = H.start + H.n * H.size;
  const ticks = niceTicks(Math.max(...H.cols.flatMap((c) => Object.values(c.vals))) * 1.08);
  const ymax = ticks[ticks.length - 1];
  const x = (t) => m.l + ((t - t0) / (t1 - t0)) * (width - m.l - m.r);
  const y = (v) => height - m.b - (v / ymax) * (height - m.t - m.b);
  yAxis(svg, m, width, height, ticks, y);
  const multiDay = new Date(t0).toDateString() !== new Date(t1).toDateString();
  const gx = el("g", { class: "ax" }, svg);
  for (const tt of timeTicks(t0, t1, width - m.l - m.r)) {
    const tx = el("text", { x: x(tt), y: height - m.b + 18, "text-anchor": "middle", class: "tick" }, gx); tx.textContent = fmtClock(tt, multiDay);
  }
  // sessions: faint band + name (direct label only when there is room)
  const gs = el("g", {}, svg);
  for (const s of vis) {
    const sp = phases.filter((p) => p.s === s); if (!sp.length) continue;
    const a = x(Math.min(...sp.map((p) => p.start))), b = x(Math.max(...sp.map((p) => p.end)));
    el("rect", { x: a, y: m.t, width: Math.max(1, b - a), height: height - m.t - m.b, class: "sband", "data-session": s.id }, gs);
    if (b - a > 90) { const tl = el("text", { x: a + 4, y: m.t - 8, class: "slabel", "data-session": s.id }, gs); tl.textContent = s.label; }
  }
  // columns: one slot per bucket; ops side by side; 2px surface gap; rounded 4px top, square at the baseline
  const slotW = (width - m.l - m.r) / H.n, gap = 2;
  const gb = el("g", {}, svg);
  for (const c of H.cols) {
    // split the bucket only between the operations that ran in it; columns keep a 2px surface gap
    const here = ops.filter((op) => c.vals[op] > 0);
    const barW = Math.max(1, Math.min(24, (slotW - gap * here.length) / here.length));
    const sx = x(c.b0) + (slotW - (barW * here.length + gap * (here.length - 1))) / 2;
    here.forEach((op, oi) => {
      const v = c.vals[op];
      const bx = sx + oi * (barW + gap), top = y(v), base = y(0), r = Math.min(4, barW / 2, base - top);
      const sid = c.by[op][0].p.s.id;
      el("path", { d: `M${bx},${base}V${top + r}Q${bx},${top} ${bx + r},${top}H${bx + barW - r}Q${bx + barW},${top} ${bx + barW},${top + r}V${base}Z`,
        fill: opColor(op), class: "bar", "data-session": sid, "data-col": c.b0 }, gb);
    });
  }
  // crosshair + tooltip snap to the bucket under the pointer (or the nearest bucket with data)
  const cross = el("rect", { y: m.t, height: height - m.t - m.b, width: slotW, class: "cross-band", visibility: "hidden" }, svg);
  svg.insertBefore(cross, gb);
  const hit = el("rect", { x: m.l, y: m.t, width: width - m.l - m.r, height: height - m.t - m.b, fill: "transparent", tabindex: 0, "aria-label": "Chart: move the pointer, or use the arrow keys, for values" }, svg);
  const tip = $("#chartTip");
  const showCol = (c) => {
    cross.setAttribute("x", x(c.b0)); cross.setAttribute("visibility", "visible");
    tip.innerHTML = "";
    const h = document.createElement("div"); h.className = "tip-h";
    h.textContent = `${new Date(c.b0).toLocaleTimeString()} – ${new Date(c.b1).toLocaleTimeString()} · ${fmtBucket(H.size)} bucket`;
    tip.appendChild(h);
    for (const op of ops) {
      if (!(op in c.vals)) continue;
      const r = document.createElement("div"); r.className = "tip-row";
      const k = document.createElement("i"); k.style.background = opColor(op);
      const v = document.createElement("b"); v.textContent = `${fmtMetric(c.vals[op])} ${metricLabel()}`;
      const names = [...new Set(c.by[op].map((x) => x.p.s.label))].join(", ");
      const full = c.by[op].every((x) => x.frac > 0.999);
      const n = document.createElement("span"); n.textContent = `${opName(op)} · ${names}${full ? "" : " · part of the bucket"}`;
      r.append(k, v, n); tip.appendChild(r);
    }
    tip.hidden = false;
    const cx = x(c.b0) + slotW;
    tip.style.left = `${Math.min(Math.max(8, cx + 10), wrap.clientWidth - tip.offsetWidth - 8)}px`; tip.style.top = `${m.t + 6}px`;
    chartHighlight(c.by[ops.find((o) => c.by[o])][0].p.s.id);
  };
  const colAt = (px) => {
    const t = t0 + ((px - m.l) / (width - m.l - m.r)) * (t1 - t0);
    return H.cols.find((c) => c.b0 <= t && t < c.b1) || H.cols.reduce((a, c) => (Math.abs(c.b0 - t) < Math.abs(a.b0 - t) ? c : a));
  };
  const hide = () => { cross.setAttribute("visibility", "hidden"); tip.hidden = true; chartHighlight(null); };
  hit.addEventListener("pointermove", (e) => { const r = svg.getBoundingClientRect(); showCol(colAt(((e.clientX - r.left) / r.width) * width)); });
  hit.addEventListener("pointerleave", hide);
  let ki = -1;   // keyboard: ← / → step through the columns that have data
  hit.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    ki = Math.max(0, Math.min(H.cols.length - 1, ki + (e.key === "ArrowRight" ? 1 : -1)));
    showCol(H.cols[ki]);
  });
  hit.addEventListener("blur", hide);
  CHART.bucket = H.size;
}

function renderCompare(vis) {
  const svg = $("#perfChart"), wrap = $("#chartWrap");
  const ops = allOps().filter((o) => !CHART.hiddenOps.has(o));
  const groups = vis.map((s) => ({ s, bars: ops.map((op) => {
    const ps = s.phases.filter((p) => p.op === op);
    return ps.length ? { op, v: Math.max(...ps.map(metricVal)), p: ps.reduce((a, p) => (metricVal(p) > metricVal(a) ? p : a)) } : null;
  }).filter(Boolean) })).filter((g) => g.bars.length);
  const barW = 24, gap = 2, groupPad = 36;
  const groupW = ops.length * barW + (ops.length - 1) * gap;
  const height = 380, m = { l: 56, r: 16, t: 26, b: 52 };
  const width = Math.max(wrap.clientWidth, m.l + m.r + groups.length * (groupW + groupPad));
  chartFrame(svg, width, height);
  const ticks = niceTicks(Math.max(...groups.flatMap((g) => g.bars.map((b) => b.v))) * 1.08);
  const ymax = ticks[ticks.length - 1];
  const y = (v) => height - m.b - (v / ymax) * (height - m.t - m.b);
  yAxis(svg, m, width, height, ticks, y);
  const slot = (width - m.l - m.r) / Math.max(1, groups.length);
  const tip = $("#chartTip");
  groups.forEach((g, gi) => {
    const cx = m.l + slot * gi + slot / 2, x0 = cx - groupW / 2;
    const name = el("text", { x: cx, y: height - m.b + 16, "text-anchor": "middle", class: "tick strong" }, svg); name.textContent = g.s.label.replace(/^Elbencho /, "");
    const when = el("text", { x: cx, y: height - m.b + 30, "text-anchor": "middle", class: "tick" }, svg); when.textContent = fmtClock(g.s.start, true);
    ops.forEach((op, oi) => {
      const b = g.bars.find((bb) => bb.op === op); if (!b) return;
      const bx = x0 + oi * (barW + gap), top = y(b.v), base = y(0), r = Math.min(4, base - top);
      // 4px rounded data-end, square at the baseline
      el("path", { d: `M${bx},${base}V${top + r}Q${bx},${top} ${bx + r},${top}H${bx + barW - r}Q${bx + barW},${top} ${bx + barW},${top + r}V${base}Z`, fill: opColor(op), class: "bar", "data-session": g.s.id }, svg);
      const hit = el("rect", { x: bx - 1, y: m.t, width: barW + 2, height: base - m.t, fill: "transparent", tabindex: 0, "aria-label": `${g.s.label} ${opName(op)} ${fmtMetric(b.v)} ${metricLabel()}` }, svg);
      const show = () => {
        tip.innerHTML = "";
        const h = document.createElement("div"); h.className = "tip-h"; h.textContent = `${g.s.label} · ${new Date(g.s.start).toLocaleString()}`;
        const rr = document.createElement("div"); rr.className = "tip-row";
        const k = document.createElement("i"); k.style.background = opColor(op);
        const v = document.createElement("b"); v.textContent = `${fmtMetric(b.v)} ${metricLabel()}`;
        const n = document.createElement("span"); n.textContent = `${opName(op)} · ${fmtDur(b.p.end - b.p.start)}`;
        rr.append(k, v, n); tip.append(h, rr);
        for (const c of b.p.perClient) { const cr = document.createElement("div"); cr.className = "tip-client muted"; cr.textContent = `${c.client}: ${fmtMetric(CHART.metric === "tp" ? (c.mib * 1048576) / UNIT_DIV[CHART.unit] : c.iops)}`; tip.appendChild(cr); }
        tip.hidden = false;
        tip.style.left = `${Math.min(Math.max(8, bx + barW + 10 - wrap.scrollLeft), wrap.clientWidth - tip.offsetWidth - 8)}px`; tip.style.top = `${Math.max(m.t, top - 10)}px`;
        chartHighlight(g.s.id);
      };
      const hide = () => { tip.hidden = true; chartHighlight(null); };
      hit.addEventListener("pointerenter", show); hit.addEventListener("pointerleave", hide);
      hit.addEventListener("focus", show); hit.addEventListener("blur", hide);
    });
  });
}

function renderChartTable() {
  const box = $("#chartTable");
  box.hidden = !$("#chartTableToggle").checked;
  if (box.hidden) return;
  const rows = visibleSessions().flatMap((s) => s.phases.filter((p) => !CHART.hiddenOps.has(p.op)).map((p) => ({ s, p })));
  const tbl = document.createElement("table"); tbl.className = "logtable";
  tbl.innerHTML = `<thead><tr><th>Session</th><th>Started</th><th>Operation</th><th class="num">${esc(metricLabel())} (avg)</th><th class="num">Duration</th><th>Clients</th></tr></thead>`;
  const tb = document.createElement("tbody");
  for (const { s, p } of rows) {
    const tr = document.createElement("tr");
    for (const [txt, cls] of [[s.label], [new Date(p.start).toLocaleString()], [opName(p.op)], [fmtMetric(metricVal(p)), "num"], [fmtDur(p.end - p.start), "num"], [p.perClient.map((c) => c.client).join(", ")]]) {
      const td = document.createElement("td"); td.textContent = txt; if (cls) td.className = cls; tr.appendChild(td);
    }
    tb.appendChild(tr);
  }
  tbl.appendChild(tb);
  box.innerHTML = ""; box.appendChild(tbl);
}

/* ---- controls ---- */
$$(".chart-filters [data-view]").forEach((b) => (b.onclick = () => { CHART.view = b.dataset.view; chartSavePrefs(); renderChart(); }));
$("#chartMetric").onchange = (e) => { CHART.metric = e.target.value; chartSavePrefs(); renderChart(); };
$("#chartUnit").onchange = (e) => { CHART.unit = e.target.value; chartSavePrefs(); renderChart(); };
$("#chartTableToggle").onchange = renderChart;
$("#chartRefresh").onclick = (e) => busy(e.target, async () => { await loadResults(); await loadChart(); });
$("#csAll").onclick = () => { CHART.hiddenSessions.clear(); chartSavePrefs(); renderChart(); };
$("#csNone").onclick = () => { CHART.hiddenSessions = new Set(CHART.sessions.map((s) => s.id)); chartSavePrefs(); renderChart(); };
let chartResizeT;
window.addEventListener("resize", () => { clearTimeout(chartResizeT); chartResizeT = setTimeout(() => { if ($("#t6").classList.contains("active")) renderChart(); }, 150); });
// open the tab -> (re)load; a run that finishes elsewhere shows up on the next open or ↻ Refresh
function openChartTab() { chartLoadPrefs(); loadChart(); }
