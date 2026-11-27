/* VAST Test Bench - frontend (no build step) */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
// tenant no longer on the VMS: "deleted" (🧹 Cleanup) or "vms record deleted" (🗑 Delete site, local records kept)
const tGone = (t) => t.status === "deleted" || t.status === "vms record deleted";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pretty = (o) => esc(typeof o === "string" ? o : JSON.stringify(o, null, 2));
const clone = (o) => JSON.parse(JSON.stringify(o));

async function api(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (r.status === 409 && data.code === "no_site") { showSites(); throw new Error("Open or create a test site first"); }
  if (!r.ok) {
    const d = data.detail;
    const err = new Error(d?.errors ? d.errors.join("\n") : (typeof d === "string" ? d : `HTTP ${r.status}`));
    err.errors = d?.errors;
    throw err;
  }
  return data;
}

async function busy(btn, fn) {
  btn.disabled = true;
  try { return await fn(); } catch (e) { alert(e.message); } finally { btn.disabled = false; }
}

/* In-page dialogs. Native confirm()/alert() are suppressed in some embedded browsers
   (confirm() returns false at once), which silently cancelled every guarded action. */
function uiConfirm({ title, html = "", ok = "OK", cancel = "Cancel", danger = false, wide = false }) {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "modal-backdrop";
    wrap.innerHTML = `<div class="modal${wide ? " modal-mid" : ""}" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
      <h3 id="modalTitle">${esc(title)}</h3><div class="modal-body">${html}</div>
      <div class="btn-row modal-actions">
        ${cancel ? `<button class="btn ghost" data-r="0">${esc(cancel)}</button>` : ""}
        <button class="btn ${danger ? "danger-solid" : "primary"}" data-r="1">${esc(ok)}</button></div></div>`;
    const done = (v) => { wrap.remove(); document.removeEventListener("keydown", onKey); resolve(v); };
    const onKey = (e) => { if (e.key === "Escape") done(false); };
    wrap.addEventListener("click", (e) => {
      if (e.target === wrap) return done(false);
      const r = e.target.closest("[data-r]");
      if (r) done(r.dataset.r === "1");
    });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(wrap);
    wrap.querySelector('[data-r="0"], [data-r="1"]').focus();
  });
}
function toast(msg, kind = "err", ms = 7000) {
  let box = $("#toasts");
  if (!box) { box = document.createElement("div"); box.id = "toasts"; document.body.appendChild(box); }
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.textContent = String(msg);
  t.onclick = () => t.remove();
  box.appendChild(t);
  setTimeout(() => t.remove(), ms);
}
window.alert = (msg) => toast(msg, "err");

let CFG = null;

/* ---------------- theme ---------------- */
const THEMES = ["auto", "light", "dark"];
const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };
let theme = store.get("theme") || "auto";
const isDark = () => theme === "dark" || (theme === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
const TERM_DARK = { background: "#0f1420", foreground: "#d8dee9", cursor: "#60a5fa", selectionBackground: "#334155" };
const TERM_LIGHT = { background: "#fbfbfd", foreground: "#1d2330", cursor: "#2563eb", selectionBackground: "#bfdbfe",
  black: "#1d2330", red: "#c81e1e", green: "#13803b", yellow: "#9a6700", blue: "#1d4ed8", magenta: "#a21caf", cyan: "#0e7490", white: "#6b7385",
  brightBlack: "#4b5563", brightRed: "#dc2626", brightGreen: "#16a34a", brightYellow: "#b45309", brightBlue: "#2563eb", brightMagenta: "#c026d3", brightCyan: "#0891b2", brightWhite: "#374151" };
const termTheme = () => (isDark() ? TERM_DARK : TERM_LIGHT);
function applyTheme() {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  $("#themeBtn").textContent = { auto: "◐ Auto", light: "☀ Light", dark: "☾ Dark" }[theme];
  consoles.forEach((c) => (c.term.options.theme = termTheme()));
}
$("#themeBtn").onclick = () => { theme = THEMES[(THEMES.indexOf(theme) + 1) % 3]; store.set("theme", theme); applyTheme(); };
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

/* ---------------- warning banner (closeable) ----------------
   Closing it is remembered per test site. It comes back only when a warning appears that wasn't
   there when it was closed (e.g. a new unset environment variable). */
const warnKey = () => `dismissedWarnings:${CFG?.site?.path || CFG?.path || ""}`;
function renderWarnings() {
  const w = $("#warnings"), list = CFG.warnings || [];
  let dismissed = [];
  try { dismissed = JSON.parse(store.get(warnKey()) || "[]"); } catch {}
  const fresh = list.filter((x) => !dismissed.includes(x));
  w.hidden = !fresh.length;
  if (!fresh.length) { w.innerHTML = ""; return; }
  w.innerHTML = `<button class="dismiss" title="Close" aria-label="Close">✕</button>` +
    `<div>${list.map((x) => "⚠ " + esc(x)).join("<br>")}</div>`;
  $(".dismiss", w).onclick = () => { store.set(warnKey(), JSON.stringify(list)); w.hidden = true; };
}

/* inline notes (e.g. after Save) get a ✕ too */
document.addEventListener("click", (e) => {
  const b = e.target.closest(".warn-note > .dismiss");
  if (b) b.parentElement.remove();
});

/* ---------------- tabs ---------------- */
function showTab(id) {
  $$(".tab").forEach((x) => x.classList.toggle("active", x.dataset.tab === id));
  $$(".tabpanel").forEach((p) => p.classList.toggle("active", p.id === id));
  if (id === "t2") consoles.forEach((c) => c.fit());
  if (id === "t3" && !cfgEdit.loaded) loadRawConfig();
  if (id === "t3" && !cc.loaded) loadCustomCmds();
  if (id === "t3") loadReportTemplate();
  if (id === "t0") renderSites();
  if (id === "t5") { loadResults(); requestAnimationFrame(fitLogsSplit); }
  if (id === "t6" && typeof openChartTab === "function") openChartTab();
  store.set("tab", id);
}
$$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

/* ---------------- config ---------------- */
async function loadConfig(reload = false) {
  CFG = await api(reload ? "POST" : "GET", reload ? "/api/config/reload" : "/api/config");
  $("#cfgPath").textContent = CFG.site ? `${CFG.site.path}` : CFG.path;
  $("#siteName").textContent = CFG.site?.name || "no site";
  loadVipSettings();
  renderWarnings();
  renderVms();
  renderVars();
  renderSteps();
  renderClients();
  renderPresets();
  await refreshProvision();
}
$("#reloadCfg").onclick = (e) => busy(e.target, async () => { await loadConfig(true); if (cfgEdit.loaded && !cfgEdit.dirty) await loadRawConfig(); });

/* ================= TAB 1 ================= */
/* ---- VMS connection (editable) ---- */
function vmsFormValues() {
  const out = {};
  for (const el of $("#vmsForm").elements) {
    if (!el.name) continue;
    out[el.name] = el.type === "checkbox" ? el.checked : el.value;
  }
  return out;
}
function vmsAuthVisibility() {
  const t = $("#vmsForm [name=auth_type]").value;
  $$("#vmsForm .auth-token").forEach((x) => (x.hidden = t !== "api_token"));
  $$("#vmsForm .auth-user").forEach((x) => (x.hidden = t === "api_token"));
}
async function renderVms() {
  const v = await api("GET", "/api/vms/settings");
  for (const el of $("#vmsForm").elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") el.checked = !!v[el.name];
    else el.value = v[el.name] ?? (el.name === "auth_type" ? "api_token" : "");
  }
  vmsAuthVisibility();
  $("#vmsDirty").hidden = true;
}
for (const ev of ["input", "change"]) $("#vmsForm").addEventListener(ev, () => { $("#vmsDirty").hidden = false; vmsAuthVisibility(); });
$("#vmsForm").addEventListener("click", (e) => {
  if (!e.target.classList.contains("eye")) return;
  const inp = e.target.previousElementSibling;
  inp.classList.toggle("shown");   // masked text field, not type=password: Chrome's breach check only runs on password fields
});
$("#vmsTest").onclick = (e) =>
  busy(e.target, async () => {
    $("#vmsResult").innerHTML = '<span class="pill running">testing</span>';
    const r = await api("POST", "/api/vms/test", { vms: vmsFormValues() });
    // short status line; the full JSON response is in a collapsed toggle (hidden by default)
    const json = r.summary ? `<details class="vmsjson"><summary>Show response</summary><pre class="mono">${esc(JSON.stringify(r.summary, null, 2))}</pre></details>` : "";
    const s = r.summary || {};
    $("#vmsResult").innerHTML = r.ok
      ? `<span class="pill passed">ok</span> HTTP ${r.status} · connected to <b>${esc(s.name || "VMS")}</b>` +
        (s.sw_version ? ` · VAST ${esc(s.sw_version.split(".").slice(0, 4).join("."))}` : "") +
        (s.state ? ` · ${esc(s.state)}` : "") +
        `${$("#vmsDirty").hidden ? "" : ' <span class="dirty">— not saved yet</span>'}${json}`
      : `<span class="pill failed">failed</span> ${esc(r.error || "HTTP " + r.status)}` +
        (r.summary && r.summary.detail ? ` — <b>${esc(r.summary.detail)}</b>` : "") +
        ` <code>${esc(r.url)}</code>` +
        (/expired|invalid.*token/i.test(r.summary?.detail || "")
          ? `<div class="muted">Create a new API token (VMS <code>POST /apitokens/</code> as a cluster admin, e.g. <code>{"name": "vast-test-bench", "expiry_date": "90D"}</code>), paste it in <b>Auth token</b> and click 💾 Save.</div>` : "") + json;
  });
$("#vmsSave").onclick = (e) =>
  busy(e.target, async () => {
    const r = await api("PUT", "/api/vms/settings", { vms: vmsFormValues() });
    await loadConfig();
    $("#vmsResult").innerHTML = `<span class="okmsg">✓ Saved to the config file.</span>` +
      (r.backup ? ` <span class="muted">Backup: <code>${esc(r.backup)}</code></span>` : "") +
      envNotes(r.warnings);
  });

/* Unset ${env:...} references elsewhere in config.yaml. They don't stop a save, so show them as notes. */
const ENV_HINTS = {
  TENANT_ADMIN_PASSWORD: "fine to leave unset: a random tenant admin password is generated at run time",
  SSH_PASSWORD: "only needed for the client SSH consoles (Tab ②)",
};
function envNotes(warnings) {
  if (!warnings?.length) return "";
  const rows = warnings.map((w) => {
    const name = /variable (\w+)/.exec(w)?.[1];
    return `⚠ ${esc(w)}${ENV_HINTS[name] ? ` <span class="muted">— ${esc(ENV_HINTS[name])}</span>` : ""}`;
  });
  return `<div class="warn-note"><button class="dismiss" title="Close" aria-label="Close">✕</button>${rows.join("<br>")}<div class="muted small">These are other settings in config.yaml, not the VMS connection. To set one, run <code>export NAME=…</code> before <code>./run.sh</code>, or put a value in ⚙ Configuration.</div></div>`;
}

/* ---- unit-aware values: "500 MB/s", "1 GB/s", "10 TB" ---- */
const BW_RE = /^\s*(\d+(?:\.\d+)?)\s*(MB\/s|GB\/s)\s*$/i;
const SIZE_RE = /^\s*(\d+(?:\.\d+)?)\s*(MB|GB|TB)\s*$/i;
function unitControl(value, onChange) {
  const bw = typeof value === "string" && value.match(BW_RE);
  const sz = !bw && typeof value === "string" && value.match(SIZE_RE);
  if (!bw && !sz) return null;
  const units = bw ? ["MB/s", "GB/s"] : ["MB", "GB", "TB"];
  const m = bw || sz;
  const cur = units.find((u) => u.toLowerCase() === m[2].toLowerCase());
  const wrap = document.createElement("span");
  wrap.className = "unit";
  wrap.innerHTML = `<input type="number" min="0" step="any" value="${esc(m[1])}">
    <select>${units.map((u) => `<option ${u === cur ? "selected" : ""}>${u}</option>`).join("")}</select>`;
  const emit = () => onChange(`${wrap.querySelector("input").value || 0} ${wrap.querySelector("select").value}`);
  wrap.querySelector("input").addEventListener("change", emit);
  wrap.querySelector("select").addEventListener("change", emit);
  return wrap;
}
function unitHint(k) {
  const r = CFG.resolved_vars || {};
  if (r[k + "_mbps"] != null) {
    const v = r[k + "_mbps"];
    if (v > 0 && v < 100) return { text: `⚠ ${v} MB/s is below the VMS minimum of 100 MB/s (0 = unlimited)`, bad: true };
    return { text: v === 0 ? "= unlimited" : `= ${v.toLocaleString()} MB/s sent to VMS` };
  }
  if (r[k + "_bytes"] != null) {
    const v = r[k + "_bytes"];
    return { text: v === 0 ? "= none" : `= ${v.toLocaleString()} bytes` };
  }
  return null;
}

/* Variables are shown in boxes per category, in onboarding order; each box shows its steps' status. */
const VAR_GROUPS = [
  { title: "Tenant", icon: "🏢", steps: ["tenant"], keys: ["tenant_name"] },
  { title: "Users", icon: "👤", steps: ["admin_role", "tenant_admin", "s3_user", "s3_keys"],
    keys: ["tenant_admin_username", "tenant_admin_password", "tenant_admin_role", "s3_user_name"] },
  { title: "VIP Pool", icon: "🌐", steps: ["vip", "vippool"], keys: ["vip_pool_name"], vip: true },
  { title: "Quota", icon: "💾", steps: ["quota"], keys: ["quota_name", "quota_hard_limit", "quota_soft_limit"] },
  { title: "QoS", icon: "🚦", steps: ["qos"], keys: ["qos_name", "qos_read_limit", "qos_write_limit"] },
  { title: "View Policy", icon: "🛡", steps: ["view_policy"], keys: ["view_policy_name", "view_policy_flavor"] },
  { title: "View", icon: "📂", steps: ["view"], keys: ["view_name", "view_path", "view_protocols", "bucket_name"] },
];
const VAR_LABELS = {   // short: the card title already says what object it is
  tenant_name: "Tenant name", tenant_admin_username: "Admin username", tenant_admin_password: "Admin password",
  tenant_admin_role: "Admin role", s3_user_name: "S3 user", vip_pool_name: "Pool name",
  quota_name: "Quota name", quota_hard_limit: "Hard limit", quota_soft_limit: "Soft limit",
  qos_name: "Policy name", qos_read_limit: "Read limit", qos_write_limit: "Write limit",
  view_policy_name: "Policy name", view_policy_flavor: "Security flavor", view_name: "View name",
  view_path: "Path", view_protocols: "Protocols", bucket_name: "S3 bucket",
};
// view policy security flavors: VMS API value -> label shown in the VMS UI (same order as VMS)
const FLAVORS = { NFS: "NFS", SMB: "SMB", S3_NATIVE: "S3 Native", BLOCK: "Block", MIXED_LAST_WINS: "Mixed Last Wins" };
const PROTOCOLS = ["NFS", "S3"];

// Short help shown next to a label (instead of conversion text under every field)
const VAR_NOTES = {
  tenant_admin_password: "empty = auto-generated",
  quota_soft_limit: "0 = none",
  qos_read_limit: "0 = unlimited · min 100 MB/s",
  qos_write_limit: "0 = unlimited · min 100 MB/s",
};
const isTemplate = (v) => typeof v === "string" && v.includes("${");
const NAME_KEYS = new Set(["tenant_admin_username", "tenant_admin_role", "s3_user_name", "vip_pool_name", "quota_name",
  "qos_name", "view_policy_name", "view_name", "view_path", "bucket_name"]);

/* One settings row: label (+ short note) on the left, the control on the right.
   Names built from the tenant name show only the real name; ✎ reveals the template to customise it. */
function varField(k, v) {
  const secret = /password|secret|token/i.test(k);
  const row = document.createElement("div");
  row.className = "vrow";
  row.dataset.key = k;
  row.title = `vars.${k}`;
  const note = VAR_NOTES[k] ? `<small class="vnote">${esc(VAR_NOTES[k])}</small>` : "";
  row.innerHTML = `<div class="vlabel">${esc(VAR_LABELS[k] || k)}${note}</div><div class="vctl"></div>`;
  const ctl = row.querySelector(".vctl");
  const save = async (val) => {
    const r = await api("PUT", "/api/vars", { vars: { [k]: val } }).catch((err) => alert(err.message));
    if (r) { CFG.vars = r.vars; CFG.resolved_vars = r.resolved_vars; updateVarHints(); }
  };
  const unit = unitControl(v, save);
  if (unit) {
    ctl.appendChild(unit);
  } else if (k === "view_policy_flavor" && v in FLAVORS) {
    ctl.innerHTML = `<select>${Object.entries(FLAVORS).map(([val, text]) =>
      `<option value="${val}" ${val === v ? "selected" : ""}>${text}</option>`).join("")}</select>`;
    ctl.querySelector("select").onchange = (e) => save(e.target.value);
  } else if (k === "view_protocols" && Array.isArray(v)) {
    ctl.innerHTML = `<span class="checks">${PROTOCOLS.map((p) =>
      `<label><input type="checkbox" value="${p}" ${v.includes(p) ? "checked" : ""}> ${p}</label>`).join("")}</span>`;
    ctl.querySelectorAll("input").forEach((cb) => (cb.onchange = () => {
      const sel = [...ctl.querySelectorAll("input:checked")].map((x) => x.value);
      if (!sel.length) { cb.checked = true; return alert("Pick at least one protocol"); }
      save(JSON.stringify(sel));
    }));
  } else if (!secret && (isTemplate(v) || NAME_KEYS.has(k))) {
    // read view: the real name; edit view: the template (e.g. ${vars.tenant_name}-admin)
    ctl.innerHTML = `<div class="vname"><span class="vtext mono"></span><span class="vtag" hidden>custom</span>
        <button type="button" class="icon-btn" data-edit title="Customise this name">✎</button></div>
      <div class="vedit" hidden><input type="text" spellcheck="false" value="${esc(v)}">
        <small class="vnote">Use <code>\${vars.tenant_name}</code> to follow the tenant name · Enter to apply, Esc to cancel</small></div>`;
    const view = ctl.querySelector(".vname"), edit = ctl.querySelector(".vedit"), inp = edit.querySelector("input");
    const close = () => { edit.hidden = true; view.hidden = false; };
    ctl.querySelector("[data-edit]").onclick = () => { view.hidden = true; edit.hidden = false; inp.value = CFG.vars[k]; inp.focus(); inp.select(); };
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); inp.blur(); }
      if (e.key === "Escape") { inp.value = CFG.vars[k]; close(); }
    });
    inp.addEventListener("blur", async () => { if (inp.value !== CFG.vars[k]) await save(inp.value); close(); });
  } else {
    const shown = typeof v === "object" && v !== null ? JSON.stringify(v) : v;
    ctl.innerHTML = `<input type="text"${secret ? ' class="masked" autocomplete="off" spellcheck="false" data-lpignore="true" data-1p-ignore' : ""} value="${esc(shown)}" placeholder="${secret ? "auto-generated" : ""}">`;
    ctl.querySelector("input").addEventListener("change", (e) => save(e.target.value));
  }
  ctl.insertAdjacentHTML("beforeend", '<div class="vwarn" hidden></div>');
  return row;
}

function renderVars() {
  const box = $("#vars");
  $("#vipHolder").append($("#vipSettings"), $("#vipPick"), $("#vipSettingsMsg")); // keep the VIP form alive across re-renders
  box.innerHTML = "";
  // 1) the tenant name: the one value most tests change; everything else is named after it
  if ("tenant_name" in CFG.vars) {
    const hero = document.createElement("section");
    hero.className = "tenant-hero";
    hero.dataset.steps = "tenant";
    hero.innerHTML = `<div class="hero-main"></div>
      <div class="hero-side"><span class="vbox-status"></span>
        <span class="muted small">The admin, VIP pool, quota, QoS policy, view policy and view are named after it.</span></div>`;
    const f = varField("tenant_name", CFG.vars.tenant_name);
    f.classList.add("hero-field");
    hero.querySelector(".hero-main").appendChild(f);
    box.appendChild(hero);
  }
  // 2) one card per object, in onboarding order
  const grid = document.createElement("div");
  grid.className = "vargroups";
  const groups = VAR_GROUPS.filter((g) => !g.keys.includes("tenant_name"));
  const others = Object.keys(CFG.vars).filter((k) => !VAR_GROUPS.some((g) => g.keys.includes(k)));
  if (others.length) groups.push({ title: "Other", icon: "⋯", steps: [], keys: others });
  for (const g of groups) {
    const keys = g.keys.filter((k) => k in CFG.vars);
    if (!keys.length && !g.vip) continue;
    const sec = document.createElement("section");
    sec.className = "vbox";
    sec.dataset.steps = g.steps.join(",");
    sec.innerHTML = `<div class="vbox-head"><h3><span class="vbox-icon" aria-hidden="true">${g.icon}</span>${esc(g.title)}</h3><span class="vbox-status"></span></div><div class="vbox-body"></div>`;
    const body = sec.querySelector(".vbox-body");
    for (const k of keys) body.appendChild(varField(k, CFG.vars[k]));
    if (g.vip) body.append($("#vipSettings"), $("#vipPick"), $("#vipSettingsMsg"));
    grid.appendChild(sec);
  }
  box.appendChild(grid);
  updateVarHints();
  updateGroupStatus();
}

// Plain-language status per object: not created / 2 of 4 created / created / failed / running
function updateGroupStatus() {
  $$("#vars [data-steps]").forEach((sec) => {
    const steps = (sec.dataset.steps || "").split(",").filter(Boolean);
    const el = sec.querySelector(".vbox-status");
    if (!el) return;
    if (!steps.length) { el.innerHTML = ""; return; }
    const st = steps.map((s) => provResults[s]?.status || "pending");
    const done = st.filter((x) => x === "passed" || x === "reused").length;
    const [cls, text] = st.includes("failed") ? ["bad", "✕ failed"]
      : st.includes("running") ? ["run", "… running"]
      : done === steps.length ? ["ok", "✓ created"]
      : done ? ["part", `${done} of ${steps.length} created`]
      : ["none", "not created yet"];
    el.innerHTML = `<span class="status-chip ${cls}" title="${esc(steps.map((s, i) => `${s}: ${st[i]}`).join("\n"))}">${text}</span>`;
  });
}

$("#varsSave").onclick = (e) => busy(e.target, async () => {
  const r = await api("POST", "/api/vars/save");
  await applyVipSettings(true, true);
  await loadConfig();
  $("#varsMsg").innerHTML = `<span class="okmsg">✓ Saved to this test site's config.yaml${r.changed.length ? ` (${r.changed.length} value${r.changed.length > 1 ? "s" : ""} changed)` : ""}.</span>` +
    (r.backup ? ` <span class="muted">Backup <code>${esc(r.backup)}</code></span>` : "");
});
function updateVarHints() {
  $$("#vars .vrow[data-key]").forEach((row) => {
    const k = row.dataset.key;
    const raw = CFG.vars[k], res = CFG.resolved_vars?.[k];
    const text = row.querySelector(".vtext");
    if (text) {   // a name: show the real one, mark it if it no longer follows the tenant name
      const shown = typeof res === "object" ? JSON.stringify(res) : String(res ?? raw ?? "");
      text.textContent = shown;
      text.title = isTemplate(raw) ? `${shown}\n(template: ${raw})` : shown;
      row.querySelector(".vtag").hidden = isTemplate(raw);
    }
    const warn = row.querySelector(".vwarn");
    const u = unitHint(k);
    warn.hidden = !u?.bad;   // conversions only matter when they are a problem (e.g. below the VMS minimum)
    warn.textContent = u?.bad ? u.text : "";
    const unit = row.querySelector(".unit");
    if (unit) unit.title = u ? u.text.replace(/^= /, "Sent to the VMS as ") : "";
  });
}

/* ---- VIP pool settings: number of IPs, subnet CIDR, VLAN ---- */
async function loadVipSettings() {
  const v = await api("GET", "/api/vip/settings").catch(() => null);
  if (!v) return;
  const f = $("#vipSettings");
  for (const k of ["ip_count", "subnet_cidr", "vlan"]) f[k].value = v[k];
  f.auto_select.checked = v.auto_select !== false;
  renderVipPick();
  loadVipCurrent();
}
async function applyVipSettings(save, fromSaveAll = false) {
  const f = $("#vipSettings");
  const body = { ip_count: f.ip_count.value, subnet_cidr: f.subnet_cidr.value, vlan: f.vlan.value,
                 auto_select: f.auto_select.checked, save };
  try {
    const r = await api("PUT", "/api/vip/settings", body);
    $("#vipSettingsMsg").innerHTML = save
      ? `<span class="okmsg">✓ Saved as site default.</span>`
      : `<span class="muted">Using ${r.ip_count} IPs · /${r.subnet_cidr} · VLAN ${r.vlan || "none"} · ${r.auto_select ? "auto selection" : "manual selection"} for this session.</span>`;
    if (!fromSaveAll && vipData) { vipData = await api("POST", "/api/vip/check").catch(() => vipData); renderVip(); }
    renderVipPick();
    loadTenants();
  } catch (err) {
    $("#vipSettingsMsg").innerHTML = `<span class="err">${esc(err.message)}</span>`;
    if (fromSaveAll) throw err;
  }
}
$("#vipSettings").addEventListener("change", (e) => {
  if (e.target.name === "auto_select" && e.target.checked) api("POST", "/api/vip/select", { start: null }).catch(() => {});
  applyVipSettings(false);
});

/* ---- VIP plan ---- */
let vipData = null;
function renderVip() {
  const d = vipData;
  if (!d) return;
  const p = d.proposal;
  const ranges = d.candidate_ranges.map((r) => `${r.start} – ${r.end}`).join(", ");
  $("#vipSummary").innerHTML = `Candidate ranges <b>${esc(ranges)}</b> · ${d.candidates} IPs, ${d.used_in_candidates} in use ·
    block size <b>${d.ip_count}</b>${d.warnings.length ? `<div class="err">${d.warnings.map(esc).join("<br>")}</div>` : ""}`;
  // one block per /24 (x.x.x.0 - .255), 32 IPs per row, row label = first octet of the row
  const own = new Set(vipCur?.pool?.ips || []);   // once the pool exists, its IPs are highlighted, not a proposal
  const cellHtml = (c0) => {
    const c = !own.size ? c0 : own.has(c0.ip) ? { ...c0, state: "proposed", holder: "this tenant's VIP pool" }
      : c0.state === "proposed" ? { ...c0, state: "free" } : c0;
    const last = c.ip.split(".").pop();
    const blk = c.state === "free" ? vipBlockFor(c.ip) : null;
    const nofit = c.state === "free" && !blk;   // free, but in a run shorter than the block size
    const what = c.holder ? c.holder
      : c.state === "proposed" ? "proposed"
      : c.state === "outside" ? "outside the candidate ranges (vip_pool.ranges)"
      : nofit ? `free, but fewer than ${d.ip_count} free IPs in a row here - can't hold a block`
      : `free - click to choose ${n2ip(blk[0])} – ${n2ip(blk[1])}`;
    return `<button class="ip ${c.state}${nofit ? " nofit" : ""}${c.holder === "answers ping" ? " ping" : ""}${c.in_range === false ? " out" : ""}" data-ip="${c.ip}"
      title="${esc(c.ip)} — ${esc(what)}">${last}</button>`;
  };
  const nets = [];
  d.cells.forEach((c, i) => { if (i % 256 === 0) nets.push([]); nets[nets.length - 1].push(c); });
  const cells = nets.map((net) => {
    const base = net[0].ip.split(".").slice(0, 3).join(".");
    let rows = "";
    for (let r = 0; r < net.length; r += 32) {
      rows += `<span class="iprow">.${net[r].ip.split(".").pop()}</span>${net.slice(r, r + 32).map(cellHtml).join("")}`;
    }
    return `<div class="vipnet"><div class="vipnet-head mono">${esc(base)}.0/24</div><div class="vipgrid">${rows}</div></div>`;
  }).join("");
  const blocks = d.free_blocks.slice(0, 12).map((b) => `${b.start}–${b.end.split(".").pop()} (${b.size})`).join(" · ");
  $("#vipMap").innerHTML = `
    <div class="proposal">${own.size ? `VIP pool <b>${esc(vipCur.pool.name || "")}</b> on the VMS: <b>${esc(vipCur.pool.ips[0])} – ${esc(vipCur.pool.ips[own.size - 1])}</b> (${own.size} IPs, highlighted)`
      : p ? `${d.auto === false ? "Chosen" : "Auto-selected"} VIP range: <b>${esc(p.start)} – ${esc(p.end)}</b> (${p.count} IPs)`
      : d.auto === false ? `<span class="warn-note">No range chosen yet. Click a free IP to start a block of ${d.ip_count} there.</span>`
      : `<span class="err">No block of ${d.ip_count} free consecutive IPs in the candidate ranges.</span>`}</div>
    <div class="legend"><span><i style="background:var(--ip-free)"></i>free (click: the block of ${d.ip_count} around it)</span>
      <span><i class="nofit-swatch"></i>free, gap too small</span><span><i style="background:var(--ip-prop)"></i>${own.size ? "this VIP pool" : d.auto === false ? "chosen" : "proposed"}</span>
      <span><i style="background:var(--ip-used)"></i>used (VIP pool / compute cluster)</span><span><i style="background:var(--ip-ping)"></i>answers ping</span>
      <span><i class="out-swatch"></i>outside candidate ranges</span></div>
    <div class="vipmap">${cells}</div>
    ${d.truncated ? '<div class="muted small">Map shows the first 8 /24 subnets (2048 IPs).</div>' : ""}
    <div class="blocks">Free blocks ≥ ${d.ip_count}: ${blocks || "none"}${d.free_blocks.length > 12 ? " …" : ""}</div>
    <details class="small"><summary class="muted">IP holders on the cluster (${d.holders.length})</summary>
      ${d.holders.map((h) => `<div>${esc(h.kind)} <b>${esc(h.name)}</b>: <span class="mono">${esc(h.ranges.map((r) => r.join("–")).join(", "))}</span></div>`).join("")}</details>`;
}
$("#vipCheck").onclick = (e) => busy(e.target, async () => { vipData = await api("POST", "/api/vip/check"); renderVip(); renderVipPick(); });
$("#vipClear").onclick = (e) => busy(e.target, async () => {
  await api("POST", "/api/vip/select", { start: null });
  $("#vipSettings").auto_select.checked = true;
  await applyVipSettings(false, true);
  vipData = await api("POST", "/api/vip/check");
  renderVip(); renderVipPick();
});
// The block a click on `ip` chooses: inside a free run that is big enough, the block containing the IP
// (near the end of the run it starts earlier). null = the IP is in a gap too small for a block.
const ip2n = (ip) => ip.split(".").reduce((a, o) => a * 256 + Number(o), 0);
const n2ip = (n) => [n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255].join(".");
function vipBlockFor(ip) {
  if (!vipData) return null;
  const n = ip2n(ip), size = vipData.ip_count;
  const run = (vipData.free_blocks || []).map((b) => [ip2n(b.start), ip2n(b.end)]).find(([a, b]) => a <= n && n <= b && b - a + 1 >= size);
  if (!run) return null;
  const start = Math.min(n, run[1] - size + 1);
  return [start, start + size - 1];
}
function vipPreview(ip) {
  $$("#vipMap .ip.preview").forEach((x) => x.classList.remove("preview"));
  const blk = ip && vipBlockFor(ip);
  if (!blk) return;
  $$("#vipMap .ip").forEach((x) => { const n = ip2n(x.dataset.ip); if (n >= blk[0] && n <= blk[1]) x.classList.add("preview"); });
}
$("#vipMap").addEventListener("mouseover", (e) => vipPreview(e.target.closest(".ip.free")?.dataset.ip));
$("#vipMap").addEventListener("mouseleave", () => vipPreview(null));
$("#vipMap").addEventListener("click", async (e) => {
  const cell = e.target.closest(".ip");
  const ip = cell?.dataset.ip;
  if (!ip || cell.classList.contains("used") || cell.classList.contains("outside")) return;
  if (cell.classList.contains("nofit")) {
    toast(`${ip} is free, but there aren't ${vipData.ip_count} free IPs in a row there. Pick an IP in one of the bright green runs.`, "err", 6000);
    return;
  }
  const blk = vipBlockFor(ip);
  if (vipCur?.pool) {   // the pool exists: clicking only fills the range fields; "Apply to VMS pool…" changes it
    const f = $("#vipEdit"), s0 = blk ? n2ip(blk[0]) : ip;
    f.start.value = s0; f.end.value = n2ip(ip2n(s0) + (vipCur.pool.ips.length || vipData.ip_count) - 1);
    vipEditCount(); $("#vipEditMsg").innerHTML = '<span class="muted">Check the range, then click <b>Apply to VMS pool…</b></span>';
    f.scrollIntoView({ block: "nearest" });
    return;
  }
  try {
    vipData = await api("POST", "/api/vip/select", { start: blk ? n2ip(blk[0]) : ip }); // the server switches to manual selection
    $("#vipSettings").auto_select.checked = false;
    renderVip(); renderVipPick();
  } catch (err) { alert(err.message); }
});

/* VIP Pool box: one line saying which range step 1 will use, and the modal with the IP map */
function renderVipPick() {
  const auto = $("#vipSettings").auto_select.checked;
  const p = vipData?.proposal;
  const range = p ? `<b class="mono">${esc(p.start)} – ${esc(p.end)}</b> (${p.count} IPs)` : "";
  $("#vipChoiceLine").innerHTML = auto
    ? `Auto: the first free block is picked when step 1 runs${p ? ` · right now ${range}` : ""}.`
    : p ? `Manual: ${range}`
    : `<span class="warn-note">Manual: no range chosen yet. Open the map and click a free IP.</span>`;
  if (vipCur?.pool) $("#vipChoiceLine").innerHTML = "";
  if (vipCur && !vipCur.pool) { vipCur.auto = auto; if (p && !auto) vipCur.choice = { start: p.start, end: p.end, count: p.count }; }
  renderVipIps();
}
/* The VIP IPs on the VIP Pool card: the pool on the VMS once it exists (read live), else the chosen range */
let vipCur = null;
const vipChips = (ips) => {
  const max = 16;
  return `<div class="vip-chips">${ips.slice(0, max).map((ip) => `<span class="vip-chip mono">${esc(ip)}</span>`).join("")}${ips.length > max ? `<span class="muted small">+${ips.length - max} more</span>` : ""}</div>`;
};
const vipRangeIps = (start, end) => { const out = []; for (let n = ip2n(start); n <= ip2n(end) && out.length < 1024; n++) out.push(n2ip(n)); return out; };
async function loadVipCurrent() {
  vipCur = await api("GET", "/api/vip/current").catch(() => null);
  renderVipIps();
}
function renderVipIps() {
  const box = $("#vipIps");
  if (!box) return;
  const c = vipCur, pool = c?.pool;
  $("#vipOpen").textContent = pool ? "🗺 Change VIP range…" : "🗺 Choose VIP range…";
  if (c) {   // the line next to the button, from the same source as the chips below it
    const auto = $("#vipSettings").auto_select.checked;
    $("#vipChoiceLine").innerHTML = pool ? ""
      : auto ? "Auto: the first free block is picked when step 1 runs."
      : c.choice || vipData?.proposal ? "Manual: step 1 uses the range below."
      : `<span class="warn-note">Manual: no range chosen yet. Open the map and click a free IP, or type a range there.</span>`;
  }
  let html = "";
  if (pool) {
    const r = pool.ips.length ? `<b class="mono">${esc(pool.ips[0])} – ${esc(pool.ips[pool.ips.length - 1])}</b> (${pool.ips.length} IPs)` : "<i>no IPs</i>";
    html = `<div class="vip-ips-head"><span class="pill passed">on VMS</span> VIP pool <b>${esc(pool.name || "")}</b>: ${r}
      ${pool.error ? `<span class="err"> · ${esc(pool.error)} - showing the recorded range</span>` : ""}</div>${pool.ips.length ? vipChips(pool.ips) : ""}`;
  } else {
    const p = !c?.auto && c?.choice ? c.choice : vipData?.proposal;
    if (p) html = `<div class="vip-ips-head"><span class="pill">${c?.auto ? "proposed" : "will be used"}</span> VIP IPs for step 1: <b class="mono">${esc(p.start)} – ${esc(p.end)}</b> (${p.count} IPs)</div>${vipChips(vipRangeIps(p.start, p.end))}`;
  }
  box.innerHTML = html;
  box.hidden = !html;
  fillVipEdit();
}
function fillVipEdit(force = false) {
  const f = $("#vipEdit");
  if (!f || (!force && (f.contains(document.activeElement)))) return;
  const pool = vipCur?.pool;
  const cur = pool?.ips?.length ? { start: pool.ips[0], end: pool.ips[pool.ips.length - 1] }
    : !vipCur?.auto && vipCur?.choice ? vipCur.choice : vipData?.proposal;
  if (cur) { f.start.value = cur.start; f.end.value = cur.end; }
  $("#vipEditTitle").textContent = pool ? `Change the IP range of VIP pool “${pool.name || ""}” on the VMS` : "VIP pool IP range for step 1";
  $("#vipEditHint").textContent = pool ? "Type a new start and end, or click a free IP on the map below." : "Type a start and end, or click a free IP on the map below.";
  $("#vipEditGo").textContent = pool ? "Apply to VMS pool…" : "Use this range";
  $("#vipEditGo").classList.toggle("danger-solid", !!pool); $("#vipEditGo").classList.toggle("primary", !pool);
  vipEditCount();
}
function vipEditCount() {
  const f = $("#vipEdit");
  const ok = (v) => /^\d{1,3}(\.\d{1,3}){3}$/.test(v.trim()) && v.trim().split(".").every((o) => +o <= 255);
  const out = $("#vipEditCount");
  if (!ok(f.start.value) || !ok(f.end.value)) { out.textContent = ""; return null; }
  const n = ip2n(f.end.value.trim()) - ip2n(f.start.value.trim()) + 1;
  out.innerHTML = n < 1 ? '<span class="err">end is before start</span>' : n < 5 ? `<span class="err">${n} IPs - at least 5</span>` : `= ${n} IPs`;
  return n;
}
$("#vipEdit").addEventListener("input", () => { vipEditCount(); $("#vipEditMsg").innerHTML = ""; });
$("#vipEdit").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target, start = f.start.value.trim(), end = f.end.value.trim();
  const pool = vipCur?.pool;
  if (pool) {
    const old = pool.ips.length ? `${pool.ips[0]} – ${pool.ips[pool.ips.length - 1]}` : "none";
    const ok = await uiConfirm({ title: `Change VIP pool “${pool.name}” on the VMS?`, ok: "Change VIP pool", danger: true,
      html: `<p>From <b class="mono">${esc(old)}</b> to <b class="mono">${esc(start)} – ${esc(end)}</b>.</p>
        <p class="small">Clients mounted through the old VIPs lose their connection - unmount them first and mount again with the new VIPs.
        The app checks that no other VIP pool or compute cluster uses the new IPs.</p>` });
    if (!ok) return;
  }
  await busy($("#vipEditGo"), async () => {
    try {
      const r = await api("POST", "/api/vip/range", { start, end });
      vipCur = { auto: r.auto, count: r.count, pool: r.pool, choice: r.choice };
      if (r.check) vipData = r.check;
      else vipData = await api("POST", "/api/vip/check").catch(() => vipData);
      if (!r.changed_on_vms) { $("#vipSettings").auto_select.checked = false; $("#vipSettings").ip_count.value = r.count; }
      renderVipPick(); renderVipIps(); renderVip(); fillVipEdit(true);
      $("#vipEditMsg").innerHTML = `<span class="okmsg">✓ ${r.changed_on_vms ? `VIP pool changed on the VMS: ${esc(start)} – ${esc(end)}` : `Step 1 will use ${esc(start)} – ${esc(end)} (${r.count} IPs)`}.</span>`;
      if (r.changed_on_vms) loadTenants();
    } catch (err) { $("#vipEditMsg").innerHTML = `<span class="err">${esc(err.message)}</span>`; }
  });
};

function closeVipModal() { $("#vipModal").hidden = true; document.removeEventListener("keydown", vipModalKey); $("#vipOpen").focus(); }
function vipModalKey(e) { if (e.key === "Escape") closeVipModal(); }
$("#vipOpen").onclick = async () => {
  $("#vipModal").hidden = false;
  $("#vipEditMsg").innerHTML = "";
  fillVipEdit(true);
  document.addEventListener("keydown", vipModalKey);
  $("#vipClose").focus();
  if (!vipData) $("#vipCheck").click();
};
$("#vipClose").onclick = closeVipModal;
$("#vipModal").addEventListener("click", (e) => { if (e.target.id === "vipModal") closeVipModal(); });

/* ---- steps ---- */
let provResults = {};
const openDetails = new Set();

function renderSteps() {
  const tb = $("#steps");
  tb.innerHTML = "";
  CFG.steps.forEach((s, i) => {
    const r = provResults[s.id] || { status: "pending" };
    const req = s.type === "vip_check" ? "VIP check (GET /vippools/, /computeclusters/)" : `${s.method} ${r.request?.url || s.endpoint}`;
    const tr = document.createElement("tr");
    tr.className = `step-${r.status}`;
    tr.innerHTML = `<td>${i + 1}</td>
      <td><span class="pill ${esc(r.status)}">${r.status === "running" ? '<span class="spin"></span>' : ""}${esc(r.status)}</span></td>
      <td>${esc(s.name)}${s.has_verify ? '<span class="tag">verify</span>' : ""}${s.has_cleanup ? '<span class="tag">cleanup</span>' : ""}
          ${r.detail ? `<div class="small muted">${esc(r.detail)}</div>` : ""}
          ${r.error ? `<div class="small check-bad">${esc(r.error)}</div>` : ""}</td>
      <td class="req">${esc(req)}</td>
      <td class="small">${r.response ? r.response.duration_s + "s" : r.status === "running" ? elapsed(r.started_at) : ""}</td>
      <td class="acts"><button class="btn sm" data-run="${esc(s.id)}">Run</button>
          <button class="btn sm ghost" data-det="${esc(s.id)}">${openDetails.has(s.id) ? "▾" : "▸"}</button></td>`;
    tb.appendChild(tr);
    if (openDetails.has(s.id)) {
      const d = document.createElement("tr");
      d.className = "detail";
      const checks = (r.verify?.checks || [])
        .map((c) => `<div class="${c.passed ? "check-ok" : "check-bad"}">${c.passed ? "✓" : "✗"} ${esc(c.field)}: expected <code>${pretty(c.expected)}</code> · actual <code>${pretty(c.actual)}</code></div>`)
        .join("");
      d.innerHTML = `<td></td><td colspan="5">
        ${r.vip_check ? `<pre class="json">${pretty(r.vip_check)}</pre>` : ""}
        ${r.request ? `<div class="small muted">Request body</div><pre class="json">${pretty(r.request.body ?? "(none)")}</pre>` : (r.vip_check ? "" : '<div class="muted small">Not run yet.</div>')}
        ${r.response ? `<div class="small muted">Response · HTTP ${r.response.status}</div><pre class="json">${pretty(r.response.body)}</pre>` : ""}
        ${r.verify ? `<div class="small muted">Verify · GET ${esc(r.verify.url)} → HTTP ${r.verify.status}</div>${checks || ""}` : ""}
      </td>`;
      tb.appendChild(d);
    }
  });
  const vals = CFG.steps.map((s) => provResults[s.id]?.status);
  const count = (x) => vals.filter((v) => v === x).length;
  updateGroupStatus();
  $("#provSummary").textContent = vals.some(Boolean)
    ? `${count("passed")} passed · ${count("reused")} reused · ${count("failed")} failed · ${CFG.steps.length} steps` : "";
}

$("#steps").addEventListener("click", async (e) => {
  const run = e.target.dataset.run, det = e.target.dataset.det;
  if (det) { openDetails.has(det) ? openDetails.delete(det) : openDetails.add(det); renderSteps(); }
  if (run) {
    provResults[run] = { ...(provResults[run] || {}), status: "running", started_at: new Date().toISOString(), response: null };
    renderSteps();
    const ticker = setInterval(renderSteps, 500);
    try { provResults[run] = await api("POST", `/api/provision/step/${run}`); } catch (err) { alert(err.message); }
    finally { clearInterval(ticker); }
    if (provResults[run].status === "failed") openDetails.add(run);
    renderSteps();
    loadTenants();
    if (run === "vip") $("#vipCheck").click();
  }
});

async function refreshProvision() {
  const st = await api("GET", "/api/provision/state");
  provResults = st.results;
  renderSteps();
  loadVipCurrent();
}

/* ---- live progress while "Create Tenant" (all steps) is going: poll the server's per-step state ---- */
function elapsed(iso) {
  const t = Date.parse(iso || "");
  return Number.isNaN(t) ? "" : `${Math.max(0, (Date.now() - t) / 1000).toFixed(1)}s…`;
}

function renderProgress(active) {
  const box = $("#provProgress");
  if (!active) { box.hidden = true; box.innerHTML = ""; return; }
  const total = CFG.steps.length;
  const done = CFG.steps.filter((s) => ["passed", "reused", "failed", "skipped"].includes(provResults[s.id]?.status)).length;
  const i = CFG.steps.findIndex((s) => provResults[s.id]?.status === "running");
  const cur = i >= 0 ? CFG.steps[i] : null;
  box.hidden = false;
  box.innerHTML = `<div class="run-progress-line"><span class="spin"></span>
      ${cur ? `Step ${i + 1}/${total} · <b>${esc(cur.name)}</b> · ${elapsed(provResults[cur.id].started_at)}`
            : `Working… ${done}/${total} steps done`}</div>
    <div class="run-bar"><div style="width:${Math.round((done / total) * 100)}%"></div></div>`;
}

function watchProvision() {
  let stopped = false, timer;
  const tick = async () => {
    if (stopped) return;
    try {
      const st = await api("GET", "/api/provision/state");
      if (stopped) return;
      CFG.steps.forEach((s) => { if (st.results[s.id]) provResults[s.id] = st.results[s.id]; });
      renderSteps();
      renderProgress(true);
    } catch { /* keep trying; the run's own request reports real errors */ }
    if (!stopped) timer = setTimeout(tick, 500);
  };
  renderProgress(true);
  tick();
  return () => { stopped = true; clearTimeout(timer); };
}

$("#runAll").onclick = (e) =>
  busy(e.target, async () => {
    CFG.steps.forEach((s) => (provResults[s.id] = { status: "queued" }));
    renderSteps();
    const label = e.target.textContent;
    e.target.innerHTML = '<span class="spin"></span>Running…';
    const stop = watchProvision();
    let r;
    try { r = await api("POST", "/api/provision/run-all"); } finally { stop(); e.target.textContent = label; }
    r.results.forEach((x) => { provResults[x.id] = x; if (x.status === "failed") openDetails.add(x.id); });
    renderSteps();
    renderProgress(false);
    await loadTenants();
    if (r.tenant) {
      $("#provSummary").insertAdjacentHTML("beforeend",
        ` · <a href="#" id="gotoTenant">saved to tenants.yaml → view in 🏢 Tenants</a>`);
      $("#gotoTenant").onclick = (ev) => { ev.preventDefault(); showTab("t4"); };
    }
    vipData = await api("POST", "/api/vip/check").catch(() => vipData);
    renderVip();
  });

$("#showSecrets").onclick = (e) => busy(e.target, async () => {
  const box = $("#secrets");
  if (box.innerHTML) { box.innerHTML = ""; return; }
  const s = await api("GET", "/api/provision/secrets");
  box.innerHTML = `<div class="secrets">
    <span class="muted">Tenant admin</span><code>${esc(CFG.resolved_vars?.tenant_admin_username || "")}</code>
    <span class="muted">Admin password</span><code>${esc(s.tenant_admin_password || "—")}</code>
    <span class="muted">S3 access key</span><code>${esc(s.s3_access_key || "— (run the S3 key step)")}</code>
    <span class="muted">S3 secret key</span><code>${esc(s.s3_secret_key || "—")}</code></div>`;
});

$("#cleanup").onclick = async (e) => {
  if (!(await uiConfirm({ title: "Clean up on the VMS?", danger: true, ok: "Delete on VMS",
    html: "Deletes the objects created by the onboarding steps on the VMS, in reverse order. Objects that were reused are left in place." }))) return;
  busy(e.target, async () => {
    const r = await api("POST", "/api/provision/cleanup");
    $("#cleanupOut").innerHTML = "<h3>Cleanup</h3>" + (r.results.length
      ? r.results.map((x) => `<div><span class="pill ${x.status}">${x.status}</span> ${esc(x.name)} <span class="muted small">${esc(x.url || x.error || "")} ${x.status_code ? "HTTP " + x.status_code : ""}</span></div>`).join("")
      : '<div class="muted small">Nothing to clean up.</div>');
    await refreshProvision();
    await loadTenants();
  });
};
$("#resetProv").onclick = async (e) => {
  if (!(await uiConfirm({ title: "Reset onboarding state?", ok: "Reset",
    html: "Forgets the created objects in this app. Nothing is deleted on the VMS; the tenant stays in the 🏢 Tenants tab." }))) return;
  busy(e.target, async () => { await api("POST", "/api/provision/reset"); await refreshProvision(); });
};
$("#exportProv").onclick = (e) =>
  busy(e.target, async () => {
    const r = await api("POST", "/api/provision/export");
    $("#provSummary").innerHTML += ` · exported ${r.files.map((f) => `<a href="/api/results/${encodeURIComponent(f)}">${esc(f)}</a>`).join(", ")}`;
    loadResults();
  });

/* ================= TAB 2 ================= */
const consoles = new Map(); // session id -> console object
const selectedIds = () => [...consoles.values()].filter((c) => c.selected()).map((c) => c.id);

function renderClients() {
  const sel = $("#clientSelect");
  sel.innerHTML = CFG.clients.map((c) => `<option value="${esc(c.name)}">${esc(c.name)} — ${esc(c.username)}@${esc(c.host)}</option>`).join("") +
    '<option value="__custom">Custom host…</option>';
  sel.onchange = () => ($("#customForm").hidden = sel.value !== "__custom");
  $("#customForm").hidden = sel.value !== "__custom";
  renderClientList();
}

/* Saved SSH clients (config.yaml clients:), one row each with Connect / Remove */
function renderClientList() {
  const box = $("#clientList");
  if (!CFG.clients.length) {
    box.innerHTML = '<div class="muted small">No saved clients yet. Choose <b>Custom host…</b>, fill in the host, and leave <b>💾 Save to client list</b> ticked.</div>';
    return;
  }
  box.innerHTML = `<table class="clienttable small"><thead><tr><th>Saved clients</th><th>Connection</th><th>Password</th><th></th></tr></thead><tbody>
    ${CFG.clients.map((c) => `<tr>
      <td><b>${esc(c.name)}</b></td>
      <td class="mono">${esc(c.username)}@${esc(c.host)}${c.port && c.port !== 22 ? ":" + c.port : ""}</td>
      <td class="muted">${c.password_saved ? "🔑 saved" : "from config"}</td>
      <td class="acts"><button class="btn sm" data-connect="${esc(c.name)}">Connect</button>
        <button class="btn sm ghost" data-ctest="${esc(c.name)}" title="Check that the app can reach and log in to this client">🔌 Test</button>
        <button class="btn sm ghost" data-cedit="${esc(c.name)}" title="Change name, host, port, user or password">✎ Edit</button>
        <button class="btn sm ghost" data-forget="${esc(c.name)}" title="Remove from this site's client list">Remove</button></td></tr>`).join("")}
    </tbody></table>`;
}
$("#clientList").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.connect) return busy(b, () => openConsole({ client: b.dataset.connect }));
  if (b.dataset.cedit) return clientModal(b.dataset.cedit, false);
  if (b.dataset.ctest) return clientModal(b.dataset.ctest, true);
  if (b.dataset.forget) {
    const name = b.dataset.forget;
    const ok = await uiConfirm({ title: `Remove client “${name}”?`, ok: "Remove", danger: true,
      html: "<p>Removes it from this site's client list (config.yaml) and deletes its saved password. Open consoles stay connected.</p>" });
    if (!ok) return;
    await busy(b, async () => { CFG.clients = (await api("DELETE", `/api/clients/${encodeURIComponent(name)}`)).clients; renderClients(); });
  }
});

$("#addConsole").onclick = (e) => {
  const v = $("#clientSelect").value;
  if (v === "__custom") { $("#customForm").hidden = false; $("#customForm [name=host]").focus(); return; }
  busy(e.target, () => openConsole({ client: v }));
};
$("#openAll").onclick = (e) => busy(e.target, async () => { for (const c of CFG.clients) await openConsole({ client: c.name }); });
// Disconnect every live console (the same as each console's ⏏ Disconnect: the console and its output stay)
$("#disconnectAll").onclick = async (e) => {
  const live = [...consoles.values()].filter((c) => c.status === "connected" || c.status === "connecting");
  if (!live.length) return;
  const names = live.map((c) => c.info?.name || c.id);
  if (!(await uiConfirm({ title: `Disconnect ${live.length} console${live.length === 1 ? "" : "s"}?`, ok: "Disconnect all",
    html: `<p>Closes the SSH connection of: ${names.map(esc).join(", ")}.</p><p class="muted small">The consoles and their output stay; use ↻ Reconnect on a console to connect it again.</p>` }))) return;
  busy(e.target, async () => {
    const res = await Promise.allSettled(live.map((c) => api("POST", `/api/sessions/${c.id}/disconnect`)));
    const failed = res.map((r, i) => (r.status === "rejected" ? `${names[i]}: ${r.reason?.message || r.reason}` : null)).filter(Boolean);
    if (failed.length) alert(`Not disconnected:\n${failed.join("\n")}`);
  });
};
$("#customForm").onsubmit = async (e) => {
  e.preventDefault();
  const form = e.target;
  const custom = Object.fromEntries(new FormData(form).entries());
  const save = form.save.checked;
  delete custom.save;
  try {
    await openConsole({ custom, save });
    form.reset();
    if (save) {
      CFG.clients = (await api("GET", "/api/config")).clients;
      renderClients();
      $("#clientSelect").value = custom.name?.trim() || custom.host.trim();
      $("#customForm").hidden = true;
    }
  } catch (err) { alert(err.message); }
};

/* ✎ Edit a saved client / 🔌 Test connection. testOnly: just run the check for the saved values. */
const CT_STEPS = [["dns", "Look up the host name"], ["tcp", "Reach the SSH port"], ["auth", "Log in over SSH"],
  ["cmd", "Run a command"], ["sudo", "Check sudo"]];
function clientModal(name, testOnly) {
  const c = CFG.clients.find((x) => x.name === name);
  if (!c) return;
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop ds-backdrop";
  wrap.innerHTML = `<div class="modal ds-modal vf-modal ct-modal" role="dialog" aria-modal="true" aria-labelledby="ctTitle">
    <div class="ds-head"><h3 id="ctTitle">${testOnly ? `🔌 Test connection - ${esc(name)}` : `✎ Edit client “${esc(name)}”`}</h3>
      <button class="btn ghost sm ds-x" title="Close (Esc)" aria-label="Close">✕</button></div>
    ${testOnly ? "" : `<form class="ct-form" autocomplete="off">
      <label><span>Name</span><input name="name" value="${esc(c.name)}" spellcheck="false"></label>
      <label><span>Host / IP</span><input name="host" value="${esc(c.host)}" required spellcheck="false"></label>
      <label class="ct-port"><span>Port</span><input name="port" value="${esc(c.port || 22)}" inputmode="numeric"></label>
      <label><span>User</span><input name="username" value="${esc(c.username || "")}" spellcheck="false"></label>
      <label class="ct-pw"><span>Password</span><input name="password" type="text" class="masked" autocomplete="off" spellcheck="false" autocapitalize="off" data-lpignore="true" data-1p-ignore
        placeholder="${c.password_saved ? "unchanged (saved password)" : "unchanged (from config)"}"></label>
      ${c.password_saved ? `<label class="ct-check"><input type="checkbox" name="clear_password"> Forget the saved password (use the default from config)</label>` : ""}
      <div class="small muted ct-note">Open consoles keep their current connection - reconnect them to use the new settings.</div>
    </form>`}
    <div class="ct-test" ${testOnly ? "" : "hidden"}></div>
    <div class="btn-row modal-actions ds-actions"></div></div>`;
  const form = wrap.querySelector(".ct-form"), box = wrap.querySelector(".ct-test"), acts = wrap.querySelector(".ds-actions");
  let running = false;
  const close = () => { if (running) return; wrap.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) close(); });
  wrap.querySelector(".ds-x").onclick = close;
  document.addEventListener("keydown", onKey);
  document.body.appendChild(wrap);
  const values = () => {
    if (!form) return {};
    const v = Object.fromEntries(new FormData(form).entries());
    v.clear_password = !!form.querySelector("[name=clear_password]")?.checked;
    return v;
  };
  const buttons = () => {
    acts.innerHTML = testOnly
      ? `<button class="btn ghost" data-a="edit">✎ Edit</button><button class="btn ghost" data-a="test">↻ Test again</button><button class="btn primary" data-a="close">Close</button>`
      : `<button class="btn ghost" data-a="cancel">Cancel</button><button class="btn" data-a="test">🔌 Test connection</button><button class="btn primary" data-a="save">💾 Save</button>`;
  };
  acts.onclick = async (e) => {
    const a = e.target.closest("[data-a]")?.dataset.a;
    if (!a || running) return;
    if (a === "cancel" || a === "close") close();
    if (a === "edit") { close(); clientModal(name, false); }
    if (a === "test") runTest();
    if (a === "save") {
      if (!form.reportValidity()) return;
      await busy(e.target, async () => {
        try {
          const r = await api("PUT", `/api/clients/${encodeURIComponent(name)}`, values());
          CFG.clients = r.clients; renderClients();
          const sel = $("#clientSelect"); if ([...sel.options].some((o) => o.value === r.client.name)) sel.value = r.client.name;
          toast(`Saved client “${r.client.name}”.`, "ok", 3500);
          close();
        } catch (err) { box.hidden = false; box.innerHTML = `<div class="err ds-err">${esc(err.message)}</div>`; }
      });
    }
  };
  async function runTest() {
    if (form && !form.reportValidity()) return;
    running = true; box.hidden = false;
    wrap.querySelector(".ds-x").hidden = true;
    acts.querySelectorAll("button").forEach((b) => (b.disabled = true));
    box.innerHTML = `<div class="ds-progress"><div class="ds-bar"><i style="width:0%"></i></div>
        <div class="ds-now small"><span class="ds-spin sm"></span> <span class="ds-nowtxt">Starting…</span></div></div>
      <ol class="ds-timeline ct-list">${CT_STEPS.map(([id, t], i) => `<li class="ds-row pending" data-row="${id}" style="--i:${i}">
        <span class="ds-dot" aria-hidden="true"></span><div class="ds-rmain"><div class="ds-rhead"><b>${t}</b><span class="ds-state small muted">waiting</span></div>
        <div class="small muted vf-sub"></div></div></li>`).join("")}</ol>`;
    const bar = box.querySelector(".ds-bar i"), now = box.querySelector(".ds-nowtxt");
    const pace = dsReduced() ? 0 : 250;
    let done = 0, result = null, target = "";
    const setRow = (id, cls, txt, sub) => {
      const el = box.querySelector(`[data-row="${id}"]`);
      el.classList.remove("pending", "active", "done", "failed", "warn"); el.classList.add(cls);
      el.querySelector(".ds-state").textContent = txt;
      if (sub != null) el.querySelector(".vf-sub").textContent = sub;
    };
    const onEvent = async (ev) => {
      if (ev.t === "start") {
        target = `${ev.username}@${ev.host}:${ev.port}`;
        box.querySelector('[data-row="dns"] .vf-sub').textContent = ev.host;
        box.querySelector('[data-row="auth"] .vf-sub').textContent = `${ev.username} · ${ev.auth === "keys" ? "SSH key / agent (no password)" : ev.auth === "typed" ? "the password typed above" : ev.auth === "saved" ? "saved password" : "password from config"}`;
      } else if (ev.t === "step") {
        const label = CT_STEPS.find(([id]) => id === ev.id)[1];
        if (ev.s === "running") { setRow(ev.id, "active", "…"); now.textContent = `${label}…`; return; }
        const cls = ev.s === "ok" ? "done" : ev.s === "warn" ? "warn" : "failed";
        setRow(ev.id, cls, (ev.s === "ok" ? "ok" : ev.s === "warn" ? "warning" : "failed") + (ev.ms != null ? ` · ${ev.ms} ms` : ""), ev.detail || "");
        done++; bar.style.width = `${Math.round((done / CT_STEPS.length) * 100)}%`;
        await dsSleep(pace);
      } else if (ev.t === "done") result = ev;
    };
    let err = null;
    try { await streamNdjson("/api/clients/test", { saved: name, ...values() }, onEvent); }
    catch (e) { err = e.message; }
    running = false;
    wrap.querySelector(".ds-x").hidden = false;
    box.querySelectorAll(".ds-row.pending").forEach((r) => { r.classList.add("vf-skip"); r.querySelector(".ds-state").textContent = "not checked"; });
    bar.style.width = "100%";
    const nowBox = box.querySelector(".ds-now");
    if (!err && result?.ok) {
      bar.classList.add("good");
      const warn = box.querySelector(".ds-row.warn");
      nowBox.innerHTML = `<svg class="ds-check vf-check" viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="24"/><path d="M15 27 l8 8 l15 -17"/></svg>
        <b>Connected to ${esc(target)}.</b>${warn ? ' <span class="muted">Works - see the sudo note.</span>' : ""}`;
    } else {
      bar.classList.add("bad");
      nowBox.innerHTML = `<span class="ds-xmark">✕</span> <b>${err ? esc(err) : "Can't connect."}</b>${err ? "" : ' <span class="muted">The red step shows why.</span>'}`;
    }
    buttons();
  }
  buttons();
  if (testOnly) runTest();
  else setTimeout(() => form.querySelector("[name=host]").focus(), 50);
}

async function openConsole(req) {
  const info = await api("POST", "/api/sessions", req);
  return mountConsole(info, req);
}

/* Show one server-side SSH session in this page. Sessions outlive the page: reloading or closing the
   page only detaches it, and on load the page mounts every open session again (restoreConsoles). */
function mountConsole(info, req = null) {
  const el = document.createElement("div");
  el.className = "console";
  el.innerHTML = `<div class="console-head">
      <label title="Include in preset runs"><input type="checkbox" class="sel" ${info.selected === false ? "" : "checked"}> ✓</label>
      <span class="name">${esc(info.name)}</span><span class="host">${esc(info.username)}@${esc(info.host)}</span>
      <span class="waitbadge" hidden>✋ input needed</span>
      <span class="pill connecting st">connecting</span>
      <button class="btn sm disconnect" title="Close the SSH connection but keep this console and its output">⏏ Disconnect</button>
      <button class="btn sm retry" title="Connect again in this console" hidden>↻ Reconnect</button>
      <button class="btn sm ghost export" title="Save the console output as a Markdown file">⤓ .md</button>
      <button class="btn sm ghost zoom" title="Open in a large window">⤢</button>
      <button class="btn sm ghost close" title="Disconnect and remove this console">✕</button></div>
    <div class="term"></div>`;
  $("#consoles").appendChild(el);
  $("#noConsoles").hidden = true;

  const term = new Terminal({ fontSize: 13, fontFamily: "ui-monospace, Menlo, monospace", theme: termTheme(), scrollback: 20000, cursorBlink: true });
  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open($(".term", el));
  // WebGL renderer: draws text on a canvas instead of thousands of DOM elements. Heavy output then
  // barely touches the DOM, which keeps the page light for browser extensions that watch or serialize
  // pages (one such extension ran out of memory on a long driver build). ?renderer=dom forces the old one.
  c_renderer: {
    if (new URLSearchParams(location.search).get("renderer") === "dom" || !window.WebglAddon) break c_renderer;
    try {
      const gl = new WebglAddon.WebglAddon();
      gl.onContextLoss(() => gl.dispose()); // GPU reset or too many contexts: fall back to the DOM renderer
      term.loadAddon(gl);
    } catch { /* no WebGL: keep the DOM renderer */ }
  }
  fitAddon.fit();

  const c = {
    id: info.id, info, req: req || (info.origin ? { client: info.origin } : null), el, term,
    ws: null, status: "connecting", waiting: null, lastSize: "", closing: false, retries: 0,
    fit: () => {
      try {
        fitAddon.fit();
        const size = `${term.cols}x${term.rows}`;
        // only tell the server when the size really changed (avoids resize storms)
        if (size !== c.lastSize && c.ws?.readyState === 1) {
          c.lastSize = size;
          c.ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
        }
      } catch {}
    },
    selected: () => $(".sel", el).checked && c.status === "connected",
  };
  const setStatus = (st) => {
    c.status = st; const p = $(".st", el); p.className = `pill st ${st}`; p.textContent = st;
    const live = st === "connected" || st === "connecting";
    $(".retry", el).hidden = live;
    $(".disconnect", el).hidden = !live;
    updatePresetsVisibility();
  };
  const setWaiting = (prompt) => {
    c.waiting = prompt;
    el.classList.toggle("waiting", !!prompt);
    $(".waitbadge", el).hidden = !prompt;
    $(".waitbadge", el).title = prompt || "";
    if (prompt) { term.focus(); el.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
  };

  // Coalesce output: one term.write per ~frame instead of one per WebSocket message. A timer, not
  // requestAnimationFrame, so a tab in the background keeps flushing instead of piling output up.
  let pending = "", flushQueued = false;
  const queueWrite = (data) => {
    pending += data;
    if (flushQueued) return;
    flushQueued = true;
    setTimeout(() => { flushQueued = false; const d = pending; pending = ""; term.write(d); }, 16);
  };

  const removeUi = () => {
    c.closing = true;
    try { c.ws?.close(); } catch {}
    if (zoomed?.c === c) closeConsoleModal();
    term.dispose(); el.remove(); consoles.delete(c.id);
    delete pkgStatus[c.id];
    $("#noConsoles").hidden = consoles.size > 0;
    renderPackages();
    updatePresetsVisibility();
  };

  // The WebSocket only shows the session; if it drops (laptop sleep, server busy) attach again.
  const attach = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/terminal/${c.id}?cols=${term.cols}&rows=${term.rows}`);
    c.ws = ws; c.lastSize = `${term.cols}x${term.rows}`;
    ws.onopen = () => { c.retries = 0; c.fit(); };
    ws.onmessage = (m) => {
      if (c.ws !== ws) return;
      const msg = JSON.parse(m.data);
      if (msg.type === "output") queueWrite(msg.data);
      else if (msg.type === "replay") { pending = ""; term.reset(); term.write(msg.data); }  // full history
      else if (msg.type === "waiting") setWaiting(msg.prompt);
      else if (msg.type === "status") setStatus(msg.status === "closed" ? "disconnected" : msg.status);
      else if (msg.type === "removed") removeUi();   // closed from another page
      else if (msg.type === "gone") c.gone = true;   // the server no longer has this session
    };
    ws.onclose = () => {
      if (c.ws !== ws || c.closing) return;
      setWaiting(null);
      if (c.gone) {
        setStatus("closed");
        term.write("\r\n\x1b[2m── this SSH session no longer exists (server restarted?) · ↻ Reconnect opens a new one ──\x1b[0m\r\n");
        return;
      }
      setStatus("offline");
      if (c.retries++ < 30) setTimeout(() => { if (!c.closing && c.ws === ws) attach(); }, Math.min(1000 * c.retries, 5000));
    };
  };

  term.onData((d) => c.ws?.readyState === 1 && c.ws.send(JSON.stringify({ type: "input", data: d })));
  let fitQueued = false;
  new ResizeObserver(() => {
    if (fitQueued) return;
    fitQueued = true;
    requestAnimationFrame(() => { fitQueued = false; c.fit(); });
  }).observe($(".term", el));

  $(".sel", el).onchange = (e) => { api("PUT", `/api/sessions/${c.id}`, { selected: e.target.checked }).catch(() => {}); renderPackages(); };
  $(".disconnect", el).onclick = (e) => busy(e.target, () => api("POST", `/api/sessions/${c.id}/disconnect`));
  $(".close", el).onclick = () => { api("DELETE", `/api/sessions/${c.id}`).catch(() => {}); removeUi(); };
  $(".retry", el).onclick = (e) => busy(e.target, async () => {
    if (!c.gone) {
      try { await api("POST", `/api/sessions/${c.id}/reconnect`); if (c.ws?.readyState !== 1) attach(); return; }
      catch (err) { if (!/Unknown session/.test(err.message)) throw err; }
    }
    // the session is gone (server restart): open a new one in this console
    if (!c.req) throw new Error("This console was opened with an unsaved custom host. Open it again from Custom host…");
    const next = await api("POST", "/api/sessions", c.req);
    consoles.delete(c.id); delete pkgStatus[c.id];
    c.id = next.id; c.info = next; c.gone = false;
    consoles.set(c.id, c);
    attach();
  });
  $(".export", el).onclick = (e) => busy(e.target, () => exportConsole(c));
  $(".zoom", el).onclick = () => (el.classList.contains("zoomed") ? closeConsoleModal() : openConsoleModal(c));

  setStatus(info.status === "new" ? "connecting" : (info.status === "closed" ? "disconnected" : info.status));
  if (info.waiting) setWaiting(info.waiting);
  attach();
  consoles.set(c.id, c);
  return c;
}

/* On load / site open: show every console the server still has, and resume runs still in progress */
async function restoreConsoles() {
  const list = await api("GET", "/api/sessions").catch(() => []);
  for (const info of list) if (!consoles.has(info.id)) mountConsole(info);
  if (list.length) loadPkgStatus();
  const runs = await api("GET", "/api/runs").catch(() => []);
  for (const r of runs) trackRun(r.run_id, r.label, r.group.startsWith("packages") ? loadPkgStatus : null);
  return list.length;
}

/* Console text, with wrapped lines joined back together */
function consoleText(term) {
  const b = term.buffer.active, lines = [];
  for (let i = 0; i < b.length; i++) {
    const line = b.getLine(i);
    if (!line) continue;
    const t = line.translateToString(true);
    if (line.isWrapped && lines.length) lines[lines.length - 1] += t;
    else lines.push(t);
  }
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  return lines.join("\n");
}
async function exportConsole(c) {
  const r = await api("POST", "/api/consoles/export",
    { name: c.info.name, host: c.info.host, username: c.info.username, text: consoleText(c.term) });
  const a = document.createElement("a");
  a.href = `/api/results/${encodeURIComponent(r.file)}`; a.download = r.file;
  document.body.appendChild(a); a.click(); a.remove();
  toast(`Saved ${r.file} to the site's results folder`, "ok", 5000);
  loadResults();
}

/* Large view: move the console itself into a modal (same terminal, same session), and back */
let zoomed = null; // { c, placeholder, backdrop }
function openConsoleModal(c) {
  closeConsoleModal();
  const placeholder = document.createComment("console");
  c.el.before(placeholder);
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop console-modal";
  backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop) closeConsoleModal(); });
  document.body.appendChild(backdrop);
  backdrop.appendChild(c.el);
  c.el.classList.add("zoomed");
  $(".zoom", c.el).textContent = "⤡"; $(".zoom", c.el).title = "Back to the grid";
  zoomed = { c, placeholder, backdrop };
  document.addEventListener("keydown", zoomKey);
  requestAnimationFrame(() => { c.fit(); c.term.focus(); });
}
function closeConsoleModal() {
  if (!zoomed) return;
  const { c, placeholder, backdrop } = zoomed;
  zoomed = null;
  document.removeEventListener("keydown", zoomKey);
  if (placeholder.parentNode) placeholder.replaceWith(c.el); else $("#consoles").appendChild(c.el);
  backdrop.remove();
  c.el.classList.remove("zoomed");
  $(".zoom", c.el).textContent = "⤢"; $(".zoom", c.el).title = "Open in a large window";
  requestAnimationFrame(() => c.fit());
}
// Esc closes the large view only when the focus is outside the terminal: inside it, Esc belongs to the
// remote program (vim, less, ...). The ⤡ button or a click outside always closes it.
function zoomKey(e) {
  if (e.key === "Escape" && zoomed && !zoomed.c.el.querySelector(".term").contains(document.activeElement)) closeConsoleModal();
}

/* ---- packages ---- */
let pkgStatus = {};
function renderPackages() {
  const ids = Object.keys(pkgStatus).filter((id) => consoles.has(id))
    .sort((a, b) => String(pkgStatus[a].client).localeCompare(String(pkgStatus[b].client), undefined, { numeric: true }));
  if (!ids.length) return;
  const names = CFG.packages;
  // one row per package, one column per client: stays narrow however many packages the config lists
  const info = (n) => (CFG.package_info || {})[n] || {};
  const act = (a, n, sid, label, title) =>
    `<button class="pk-btn${a === "uninstall" ? " danger" : ""}" data-pact="${a}" data-pkg="${esc(n)}"${sid ? ` data-sid="${esc(sid)}"` : ""} title="${esc(title)}">${label}</button>`;
  const noUn = (n) => !info(n).uninstall;
  const cell = (id, n) => {
    const p = pkgStatus[id].packages[n] || {};
    const who = pkgStatus[id].client;
    if (!p.installed) return `<td><span class="pk-miss">✗ missing</span>${p.detail ? `<span class="ver" title="${esc(p.detail)}">${esc(p.detail)}</span>` : ""}
      <span class="pk-acts">${act("install", n, id, "Install", `Install ${n} on ${who}`)}</span></td>`;
    const upd = p.update_available ? ` <span class="pk-upd" title="update available">⬆</span>` : "";
    return `<td><span class="pk-ok">✓</span>${upd}<span class="ver" title="${esc(p.version)}">${esc(p.version)}</span>
      <span class="pk-acts">${act("reinstall", n, id, "Reinstall", `Reinstall ${n} on ${who}`)}${noUn(n) ? "" : act("uninstall", n, id, "Uninstall", `Uninstall ${n} from ${who}`)}</span></td>`;
  };
  // row buttons act on every ticked client the action applies to; shown only when it applies to at least one
  const ticked = selectedIds().filter((id) => ids.includes(id));
  const has = (n, inst) => ticked.some((id) => !!pkgStatus[id].packages[n]?.installed === inst);
  const rowActs = (n) => ticked.length < 2 ? "" : `<span class="pk-acts pk-row">${has(n, false) ? act("install", n, "", "Install on all", `Install ${n} on the ticked clients where it is missing`) : ""}${has(n, true) ? act("reinstall", n, "", "Reinstall on all", `Reinstall ${n} on the ticked clients`) : ""}${has(n, true) && !noUn(n) ? act("uninstall", n, "", "Uninstall from all", `Uninstall ${n} from the ticked clients where it is installed`) : ""}</span>`;
  const head = `<tr><th>Package</th>${ids.map((id) => `<th><b>${esc(pkgStatus[id].client)}</b> <span class="muted">${esc(pkgStatus[id].pm || "?")}</span></th>`).join("")}</tr>`;
  const rows = names.map((n) => `<tr><td><b>${esc(n)}</b>${info(n).system ? ` <span class="muted small">system</span>` : ""}${rowActs(n)}</td>${ids.map((id) => cell(id, n)).join("")}</tr>`).join("");
  const missing = ids.reduce((a, id) => a + Object.values(pkgStatus[id].packages).filter((p) => !p.installed).length, 0);
  const upd = ids.reduce((a, id) => a + Object.values(pkgStatus[id].packages).filter((p) => p.update_available).length, 0);
  $("#pkgTable").innerHTML = `<table class="pkgs"><thead>${head}</thead><tbody>${rows}</tbody></table>
    <div class="small muted" style="margin-top:6px">${missing} missing · ${upd} with updates available.
    Packages with a custom <code>install</code> command (e.g. elbencho) are re-installed by <b>Update</b>.</div>`;
}
// Install / Reinstall / Uninstall one package: a cell acts on that client, the row's buttons on the ticked clients
$("#pkgTable").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-pact]");
  if (!b) return;
  const { pact: action, pkg } = b.dataset;
  const applies = (id) => action === "reinstall" || !!pkgStatus[id]?.packages[pkg]?.installed === (action === "uninstall");
  const ids = b.dataset.sid ? [b.dataset.sid] : selectedIds().filter((id) => pkgStatus[id] && applies(id));
  if (!ids.length) return alert(`Nothing to ${action} on the ticked clients - run Check packages first`);
  const who = ids.map((id) => pkgStatus[id]?.client || id).join(", ");
  if (action !== "install") {
    const custom = (CFG.package_info || {})[pkg]?.custom;
    const warn = action === "uninstall"
      ? (pkg === "vastnfs" ? "<p>The VAST NFS driver is removed; reboot the client to unload it. Unmount VAST first.</p>"
        : /nfs/.test(pkg) ? "<p>Unmount the VAST view first.</p>" : "")
      : (custom ? `<p>Runs the configured install command for <b>${esc(pkg)}</b> again${pkg === "vastnfs" ? " (rebuilds the driver: several minutes)" : ""}.</p>` : "");
    const ok = await uiConfirm({ title: `${action === "uninstall" ? "Uninstall" : "Reinstall"} ${esc(pkg)}?`,
      ok: action === "uninstall" ? "Uninstall" : "Reinstall", danger: action === "uninstall",
      html: `<p>${action === "uninstall" ? "Removes" : "Reinstalls"} <b>${esc(pkg)}</b> on: ${esc(who)}.</p>${warn}` });
    if (!ok) return;
  }
  const verb = { install: "Install", reinstall: "Reinstall", uninstall: "Uninstall" }[action];
  busy(b, () => startRun("/api/packages/action", { session_ids: ids, package: pkg, action }, `${verb} ${pkg}`,
    () => startRun("/api/packages/check", { session_ids: ids }, "Check packages", loadPkgStatus)));
});
async function loadPkgStatus() { pkgStatus = await api("GET", "/api/packages/status").catch(() => ({})); renderPackages(); }

async function startRun(url, body, label, onDone) {
  const ids = body.session_ids;
  if (!ids.length) return alert("No connected console is ticked ✓");
  const { run_id } = await api("POST", url, body);
  trackRun(run_id, label, onDone);
}
$("#pkgCheck").onclick = (e) => busy(e.target, () => startRun("/api/packages/check", { session_ids: selectedIds() }, "Check packages", loadPkgStatus));
$("#pkgInstall").onclick = (e) => busy(e.target, () => startRun("/api/packages/install", { session_ids: selectedIds(), mode: "missing" }, "Install missing packages", () => $("#pkgCheck").click()));
$("#pkgUpdate").onclick = async (e) => {
  if (!(await uiConfirm({ title: "Update packages?", ok: "Update", html: "Updates the required packages on the ticked clients." }))) return;
  busy(e.target, () => startRun("/api/packages/install", { session_ids: selectedIds(), mode: "update" }, "Update packages", () => $("#pkgCheck").click()));
};

/* ---- presets ---- */
// The Presets card only makes sense with a connected console: hide it (and show a hint) until one is.
function updatePresetsVisibility() {
  const any = [...consoles.values()].some((c) => c.status === "connected");
  $("#disconnectAll").hidden = ![...consoles.values()].some((c) => c.status === "connected" || c.status === "connecting");
  $("#pkgCard").hidden = !any;
  $("#presetsCard").hidden = !any;
  $("#presetsHint").hidden = any;
}
// Required packages / Presets fold down to their title bar; the choice is remembered in this browser
function setCollapsed(card, collapsed) {
  card.classList.toggle("collapsed", collapsed);
  const b = $(".collapse-toggle", card);
  b.setAttribute("aria-expanded", String(!collapsed));
  $(".chev", b).textContent = collapsed ? "▸" : "▾";
  store.set(`collapsed:${card.id}`, collapsed ? "1" : "");
}
$$(".card.collapsible").forEach((card) => {
  setCollapsed(card, store.get(`collapsed:${card.id}`) === "1");
  $(".collapse-toggle", card).onclick = () => setCollapsed(card, !card.classList.contains("collapsed"));
});
// "4g" -> bytes (elbencho size units k/m/g/t are binary)
const elbSize = (v) => { const m = /^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?\s*$/i.exec(String(v ?? "")); return m ? Number(m[1]) * 1024 ** " kmgt".indexOf((m[2] || " ").toLowerCase()) : null; };
const fmtBytes = (b) => { const u = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]; let i = 0; while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; } return `${+b.toFixed(1)} ${u[i]}`; };
function elbenchoInfo(e) {
  if (!e || !e.threads) return "";
  const per = elbSize(e.file_size) * Number(e.threads) * Number(e.dirs || 1) * Number(e.files || 1);
  const clients = Math.max(1, selectedIds().length);
  return `<b>Benchmark</b><span><span class="mono">${esc(e.threads)} threads × ${esc(e.dirs)} dir × ${esc(e.files)} files × ${esc(e.file_size)}</span>
      · block ${esc(e.block_size)} ${esc(e.flags || "")}${Number.isFinite(per) ? ` · <b>${fmtBytes(per)} per client</b>${clients > 1 ? ` (${fmtBytes(per * clients)} for ${clients} ticked consoles)` : ""}` : ""}</span>`;
}
// Presets are grouped into dropdown menus by each group's `menu:` key. Built-in presets fall back to
// their usual menu by id (older site configs have no `menu:`); anything else goes under Customs.
const PRESET_MENUS = ["Client Cmds", "Connection", "File System", "Performance Test", "Utilities"];
const DEFAULT_MENU = { precheck: "Client Cmds", client_info: "Client Cmds", ping_vip: "Connection",
  mount: "File System", mount_status: "File System", readahead: "File System", unmount: "File System",
  elbencho_write: "Performance Test", elbencho_read: "Performance Test", elbencho: "Performance Test",
  elbencho_delete: "Utilities", drop_caches: "Utilities" };
function presetMenus(groups) {
  const menus = new Map(PRESET_MENUS.map((m) => [m, []]));
  for (const g of groups) {
    const m = g.menu || DEFAULT_MENU[g.id] || "Customs";
    if (!menus.has(m)) menus.set(m, []);          // a new menu name in the config gets its own dropdown
    menus.get(m).push(g);
  }
  const customs = menus.get("Customs") || [];      // Customs always comes last, even when empty
  menus.delete("Customs"); menus.set("Customs", customs);
  return [...menus].filter(([m, gs]) => gs.length || m === "Customs").map(([m, gs], i) => `
    <div class="pmenu">
      <button type="button" class="btn pmenu-btn${gs.some((g) => g.results) ? " primary" : ""}" aria-haspopup="menu" aria-expanded="false"
        aria-controls="pmenu-${i}">${esc(m)} <span class="chev" aria-hidden="true">▾</span></button>
      <div class="pmenu-list" id="pmenu-${i}" role="menu" hidden>${gs.length
        ? gs.map((g) => {
            const danger = g.confirm && DEFAULT_MENU[g.id];      // red only for the built-in destructive ones (Delete)
            return `<button type="button" role="menuitem" class="pmenu-item${danger ? " danger" : ""}" data-group="${esc(g.id)}"${g.confirm ? ' title="Asks before running"' : ""}>${esc(g.label)}${g.confirm && !danger ? "…" : ""}</button>`;
          }).join("")
        : `<div class="pmenu-empty">No custom commands yet.<br><a href="#" data-goto-cc>Add one in ⚙ Configuration → Custom Linux commands</a></div>`}</div>
    </div>`).join("");
}
function closePresetMenus(except) {
  $$("#presets .pmenu").forEach((m) => {
    if (m === except) return;
    $(".pmenu-list", m).hidden = true;
    $(".pmenu-btn", m).setAttribute("aria-expanded", "false");
  });
}
$("#presets").addEventListener("click", (e) => {
  const b = e.target.closest(".pmenu-btn");
  if (b) {
    const m = b.parentElement, l = $(".pmenu-list", m);
    closePresetMenus(m);
    l.hidden = !l.hidden;
    b.setAttribute("aria-expanded", String(!l.hidden));
    if (!l.hidden) $(".pmenu-item", l)?.focus();
    return;
  }
  if (e.target.closest(".pmenu-item")) closePresetMenus();
  if (e.target.closest("[data-goto-cc]")) {
    e.preventDefault(); closePresetMenus(); showTab("t3");
    setCollapsed($("#ccCard"), false);
    setTimeout(() => $("#ccCard").scrollIntoView({ block: "start" }), 50);
  }
});
document.addEventListener("click", (e) => { if (!e.target.closest("#presets .pmenu")) closePresetMenus(); });
document.addEventListener("keydown", (e) => {
  const open = $("#presets .pmenu-list:not([hidden])");
  if (e.key === "Escape" && open) { closePresetMenus(); $(".pmenu-btn", open.parentElement).focus(); return; }
  const item = e.target.closest?.(".pmenu-item");
  if (item && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault();
    const its = $$(".pmenu-item", item.parentElement), i = its.indexOf(item);
    its[(i + (e.key === "ArrowDown" ? 1 : its.length - 1)) % its.length].focus();
  }
});
function renderPresets() {
  const m = CFG.mount || {};
  $("#mountInfo").innerHTML = `<b>Mount point</b><span class="mono">${esc(m.mount_point)}</span>
    <b>VIP</b><span>${esc(String(m.vip).toLowerCase() === "auto" ? "auto — clients spread over the tenant VIP pool" : m.vip)}</span>
    <b>Options</b><span class="mono">${esc(m.options)}</span><b>Read-ahead</b><span>${esc(m.read_ahead_kb)} KB</span>` +
    elbenchoInfo(CFG.elbencho || {});
  const groupBtn = (g) => `<button class="btn ${g.results ? "primary" : ""}" data-group="${esc(g.id)}">${g.section === "packages" ? "" : "▶ "}${esc(g.label)}</button>`;
  $("#presets").innerHTML = presetMenus(CFG.groups.filter((g) => g.section !== "packages"));
  $("#pkgGroups").innerHTML = CFG.groups.filter((g) => g.section === "packages").map(groupBtn).join("");
  $("#cmdList").innerHTML = CFG.groups
    .map((g) => `<div><b>${esc(g.label)}</b> <span class="muted small">· delay ${g.delay}s${g.results ? " · results exported" : ""}</span>
      <ol>${g.commands.map((c) => `<li>${esc(typeof c === "string" ? c : c.wait !== undefined ? `⏸ idle ${c.wait}s${c.label ? ` (${c.label})` : ""}` : c.run)}</li>`).join("")}</ol></div>`)
    .join("") + `<div><b>Mount command</b><ol><li>${esc(m.command)}</li></ol><b>Read-ahead command</b><ol><li>${esc(m.read_ahead_command)}</li></ol></div>` +
    (CFG.elbencho ? `<div><b>elbencho commands</b> <span class="muted small">· config section <code>elbencho</code></span><ol>${Object.entries(CFG.elbencho)
      .filter(([k]) => k.endsWith("_command") || k === "check_mount").map(([k, v]) => `<li><code>${esc(k)}</code>: ${esc(v)}</li>`).join("")}</ol></div>` : "");
}
$("#toggleCmds").onclick = (e) => { e.preventDefault(); const l = $("#cmdList"); l.hidden = !l.hidden; e.target.textContent = l.hidden ? "show commands" : "hide commands"; };

$("#pkgGroups").addEventListener("click", (e) => $("#presets").dispatchEvent(new CustomEvent("rungroup", { detail: e.target.dataset.group })));
// presets with `confirm:` in the config ask first (e.g. deleting benchmark files)
async function confirmGroup(g) {
  if (!g.confirm) return true;
  const n = selectedIds().length;
  return uiConfirm({ title: `${g.label}?`, ok: g.label, danger: true,
    html: `<p>${esc(g.confirm)}</p><p class="muted">Runs on ${n} ticked console${n === 1 ? "" : "s"}.</p>` });
}
$("#presets").addEventListener("rungroup", async (e) => {
  const g = CFG.groups.find((x) => x.id === e.detail);
  if (!g || !(await confirmGroup(g))) return;
  try { await startRun("/api/runs", { group: g.id, session_ids: selectedIds() }, g.label); } catch (err) { alert(err.message); }
});
$("#presets").addEventListener("click", async (e) => {
  const gid = e.target.closest(".pmenu-item")?.dataset.group;
  if (!gid) return;
  const g = CFG.groups.find((x) => x.id === gid);
  if (!(await confirmGroup(g))) return;
  try { await startRun("/api/runs", { group: gid, session_ids: selectedIds() }, g.label); } catch (err) { alert(err.message); }
});

// The status list shows only the latest run (plus any still running on other consoles);
// older runs are in the 🗒 Logs tab.
function pruneRunStatus(keep) {
  $$("#runStatus > div").forEach((x) => { if (x !== keep && !x.classList.contains("is-running")) x.remove(); });
}
function trackRun(runId, label, onDone) {
  const line = document.createElement("div");
  line.className = "is-running";
  const n = () => `<span class="muted small">${esc(runId)}</span>`;
  line.innerHTML = `<span class="pill running">running</span> ${esc(label)} · ${n()}`;
  $("#runStatus").prepend(line);
  pruneRunStatus(line);
  const poll = async () => {
    const run = await api("GET", `/api/runs/${runId}`).catch(() => null);
    if (!run) return setTimeout(poll, 2000);
    if (run.status === "running") {
      const w = Object.entries(run.waiting || {});
      line.innerHTML = `<span class="pill running">running</span> ${esc(label)} · ${n()}` +
        (w.length ? `<span class="waitline">✋ waiting for your input on ${w.map(([c, p]) => `<b>${esc(c)}</b> (“${esc(p)}”)`).join(", ")}</span>` : "");
      return setTimeout(poll, 1500);
    }
    const links = (run.files || []).map((f) => `<a href="/api/results/${encodeURIComponent(f)}">${esc(f)}</a>`).join(" · ");
    line.innerHTML = `<span class="pill ${run.status}">${run.status}</span> ${esc(label)} on ${run.clients.length} client(s) · ${links}` +
      ` · <a href="#" data-goto="t5" class="small">earlier runs in 🗒 Logs</a>`;
    line.classList.remove("is-running");
    // keep this finished line only if it is the newest one; otherwise a newer run already took its place
    if ($("#runStatus").firstElementChild !== line) line.remove();
    else pruneRunStatus(line);
    if (run.summary) renderSummary(run);
    if (run.summary && $("#t6")?.classList.contains("active")) loadResults().then(() => loadChart());
    loadResults();
    if (onDone) onDone(run);
  };
  setTimeout(poll, 1200);
}

const fmt = (v) => (v == null ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 }));
/* ---- Performance results: pick a run, show throughput in MB/s, GB/s or TB/s, export CSV ----
   elbencho reports MiB/s (2^20 bytes); shown in decimal units like the QoS settings (1 GB/s = 1000 MB/s). */
const PERF_UNITS = { MB: { div: 1e6, digits: 1 }, GB: { div: 1e9, digits: 2 }, TB: { div: 1e12, digits: 4 } };
let perfRun = null;   // the run shown (its saved JSON, or the run object of a run that just finished)
const perfUnit = () => $("#perfUnit").value || "GB";
const mib2 = (mib, unit = perfUnit()) => (mib == null ? null : (mib * 1048576) / PERF_UNITS[unit].div);
const fmtU = (v, unit = perfUnit()) => (v == null ? "–" : v.toLocaleString(undefined, { minimumFractionDigits: PERF_UNITS[unit].digits, maximumFractionDigits: PERF_UNITS[unit].digits }));
const opsPerSec = (m) => m["Files/s"] || m["Dirs/s"] || null;   // metadata phases (mkdirs, create, stat, delete)

// One row per operation: the aggregate across clients, and (for the CSV) each client on its own
function perfRows(run) {
  const agg = (run.summary || []).map((r) => ({
    scope: "all clients", index: r.command_index, operation: r.operation, clients: r.clients.join(" + "), command: r.command,
    tp_first: r.totals_first_done["Throughput MiB/s"], tp_last: r.totals_last_done["Throughput MiB/s"],
    iops_first: r.totals_first_done["IOPS"], iops_last: r.totals_last_done["IOPS"],
    ops_last: opsPerSec(r.totals_last_done), total_mib: r.totals_last_done["Total MiB"],
    elapsed_first: null, elapsed_last: r.max_elapsed_s,
  }));
  const per = [];
  for (const c of run.clients || []) {
    (c.commands || []).forEach((cmd, i) => (cmd.elbencho || []).forEach((op) => {
      const m = Object.fromEntries(Object.entries(op.metrics || {}).map(([k, v]) => [k, v]));
      const g = (k, w) => m[k]?.[w] ?? null;
      per.push({ scope: c.client, index: i + 1, operation: op.operation, clients: c.client, command: cmd.command,
        tp_first: g("Throughput MiB/s", "first_done"), tp_last: g("Throughput MiB/s", "last_done"),
        iops_first: g("IOPS", "first_done"), iops_last: g("IOPS", "last_done"),
        ops_last: g("Files/s", "last_done") ?? g("Dirs/s", "last_done"), total_mib: g("Total MiB", "last_done"),
        elapsed_first: g("Elapsed time", "first_done"), elapsed_last: g("Elapsed time", "last_done") });
    }));
  }
  return { agg, per };
}
function renderSummary(run) {
  if (run) perfRun = run;
  run = perfRun;
  if (!run) { $("#summary").innerHTML = '<div class="muted small">No performance run yet. Run one of the elbencho presets.</div>'; $("#perfCsv").disabled = true; return; }
  const { agg } = perfRows(run), u = perfUnit();
  $("#perfCsv").disabled = !agg.length;
  if (!agg.length) { $("#summary").innerHTML = `<div class="muted small">${esc(run.label || run.run_id)}: no elbencho result table found in the output${run.status === "failed" ? " (the run failed - see 🗒 Logs)" : ""}.</div>`; return; }
  const rows = agg.map((r) => `<tr><td>${r.index}</td><td><b>${esc(r.operation)}</b></td><td>${esc(r.clients)}</td>
    <td class="num">${fmtU(mib2(r.tp_first))}</td><td class="num"><b>${fmtU(mib2(r.tp_last))}</b></td>
    <td class="num">${fmt(r.iops_first)}</td><td class="num">${fmt(r.iops_last)}</td><td class="num">${fmt(r.ops_last)}</td>
    <td class="num">${r.total_mib != null ? fmtU((r.total_mib * 1048576) / PERF_UNITS[u].div) : "–"}</td><td class="num">${fmt(r.elapsed_last)}s</td></tr>`).join("");
  $("#summary").innerHTML = `<div class="small muted">${esc(run.label || "")} · ${esc(run.run_id)} · ${esc(run.status || "")} · summed across clients ·
      throughput in <b>${u}/s</b> (decimal; elbencho reports MiB/s)</div>
    <div class="table-wrap"><table class="summary"><thead><tr><th>#</th><th>Operation</th><th>Clients</th>
      <th class="num" title="when the first thread finished">${u}/s first done</th><th class="num" title="when all threads finished">${u}/s last done</th>
      <th class="num">IOPS first</th><th class="num">IOPS last</th><th class="num" title="files or dirs per second (metadata phases)">Files·dirs/s</th>
      <th class="num">Data (${u})</th><th class="num">Max elapsed</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
// run picker: the site's performance runs, newest first (from the Logs listing)
async function loadPerfRuns(selectId) {
  const runs = (logData.logs || []).filter((l) => l.performance && l.files.some((f) => f.name.endsWith(".json")));
  const sel = $("#perfRun"), keep = selectId || sel.value;
  sel.innerHTML = runs.length ? runs.map((l) => `<option value="${esc(l.id)}">${esc(new Date(l.time || l.mtime * 1000).toLocaleString())} · ${esc(l.label)} · ${esc(l.status)}</option>`).join("")
    : '<option value="">no performance runs yet</option>';
  sel.disabled = !runs.length;
  sel.value = runs.some((l) => l.id === keep) ? keep : (runs[0]?.id || "");
  if (sel.value && sel.value !== perfRun?.run_id) await showPerfRun(sel.value);
  else renderSummary();
}
async function showPerfRun(id) {
  try { renderSummary(await (await fetch(`/api/results/${encodeURIComponent(id)}.json`)).json()); }
  catch (e) { $("#summary").innerHTML = `<div class="err small">Could not load ${esc(id)}: ${esc(e.message)}</div>`; }
}
$("#perfRun").onchange = (e) => showPerfRun(e.target.value);
$("#perfUnit").value = store.get("perfUnit") || "GB";
$("#perfUnit").onchange = (e) => { store.set("perfUnit", e.target.value); renderSummary(); };
$("#perfCsv").onclick = () => {
  if (!perfRun) return;
  const u = perfUnit(), { agg, per } = perfRows(perfRun);
  const head = ["run_id", "preset", "run_status", "started_at", "scope", "command_index", "operation", "clients",
    `throughput_first_done_${u}_s`, `throughput_last_done_${u}_s`, "iops_first_done", "iops_last_done", "files_or_dirs_per_s",
    `data_${u}`, "elapsed_first_done_s", "elapsed_last_done_s", "command"];
  const num = (v, d) => (v == null ? "" : Number(v.toFixed(d)));
  const lines = [...agg, ...per].map((r) => [perfRun.run_id, perfRun.label, perfRun.status, perfRun.started_at, r.scope, r.index, r.operation, r.clients,
    num(mib2(r.tp_first, u), 6), num(mib2(r.tp_last, u), 6), r.iops_first ?? "", r.iops_last ?? "", r.ops_last ?? "",
    num(r.total_mib != null ? (r.total_mib * 1048576) / PERF_UNITS[u].div : null, 6), r.elapsed_first ?? "", r.elapsed_last ?? "", r.command]);
  const cell = (v) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const csv = [head, ...lines].map((row) => row.map(cell).join(",")).join("\r\n") + "\r\n";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `${perfRun.run_id}-${u}s.csv`;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

/* 📊 PowerPoint performance report: pick runs and unit → the server builds the deck → it is listed in 🗒 Logs */
let prFormats = (store.get("reportFormats") || "pptx").split(",");
async function perfReportModal() {
  const d = await api("GET", "/api/logs").catch((e) => { toast(e.message); return null; });
  if (!d) return;
  logData = d;
  const runs = d.logs.filter((l) => l.performance && l.type !== "report" && l.files.some((f) => f.name.endsWith(".json")));
  if (!runs.length) return toast("No performance runs yet - run Elbencho Write / Read on the consoles first.");
  const unit = ["MB", "GB", "TB"].includes(perfUnit()) ? perfUnit() : "GB";
  const exp = await api("GET", "/api/reports/expected").catch(() => ({}));
  const ev = (k) => (exp[k] ?? "") === "" ? "" : esc(String(exp[k]));
  const labs = await api("GET", "/api/reports/labels").catch(() => ({}));
  const lv = (k) => esc(String(labs[k] ?? ""));
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop ds-backdrop";
  wrap.innerHTML = `<div class="modal ds-modal vf-modal pr-modal" role="dialog" aria-modal="true" aria-labelledby="prTitle">
    <div class="ds-head"><h3 id="prTitle">📊 Performance report</h3>
      <button class="btn ghost sm ds-x" title="Close (Esc)" aria-label="Close">✕</button></div>
    <div class="ds-body">
      <p class="small muted">Charts and tables of the runs you pick - PowerPoint in the tester manual's template, Word in your report template. Throughput is summed across clients.
        The report is saved in this site's results and listed in <b>🗒 Logs</b>.</p>
      <div class="pr-tools small pr-formats"><b>Format</b>
        <label><input type="checkbox" class="pr-fmt" value="pptx" ${prFormats.includes("pptx") ? "checked" : ""}> 📊 PowerPoint</label>
        <label><input type="checkbox" class="pr-fmt" value="docx" ${prFormats.includes("docx") ? "checked" : ""}> 📄 Word</label>
        <span class="muted">Word uses the template from ⚙ Configuration → Report template</span></div>
      <div class="pr-tools small"><label><input type="checkbox" class="pr-all"> All runs</label>
        ${runs.some((l) => l.status === "failed") ? '<span class="muted">failed runs are not ticked</span>' : ""}
        <label>Unit <select class="pr-unit">${["MB", "GB", "TB"].map((u) => `<option ${u === unit ? "selected" : ""}>${u}</option>`).join("")}</select> /s</label></div>
      <div class="pr-runs">${runs.map((l) => `<label class="pr-run"><input type="checkbox" value="${esc(l.id)}" ${l.status === "failed" ? "" : "checked"}>
        <span><b>${esc(l.label)}</b> <span class="pill ${esc(l.status)}">${esc(l.status)}</span>
        <span class="muted small">${esc(new Date(l.time || l.mtime * 1000).toLocaleString())} · ${esc(l.clients.filter(Boolean).join(", "))}</span></span></label>`).join("")}</div>
      <details class="pr-exp" ${Object.keys(exp).length ? "open" : ""}>
        <summary><b>Expected performance</b> <span class="muted small">- for the Estimated vs Actual page; leave empty to skip it · saved for this site</span></summary>
        <div class="pr-exp-grid">
          <label>Read GB/s<input data-e="read_gbps" inputmode="decimal" value="${ev("read_gbps")}" placeholder="e.g. 45"></label>
          <label>Write GB/s<input data-e="write_gbps" inputmode="decimal" value="${ev("write_gbps")}" placeholder="e.g. 20"></label>
          <label>Read IOPS<input data-e="read_iops" inputmode="numeric" value="${ev("read_iops")}" placeholder="optional"></label>
          <label>Write IOPS<input data-e="write_iops" inputmode="numeric" value="${ev("write_iops")}" placeholder="optional"></label>
          <label>P99 latency ms<input data-e="p99_ms" inputmode="decimal" value="${ev("p99_ms")}" placeholder="e.g. 4"></label>
          <label class="pr-basis">Where the numbers come from<input data-e="basis" value="${ev("basis")}" placeholder="e.g. VAST sizing for 3 CNodes, POC success criteria"></label>
        </div>
      </details>
      <details class="pr-exp" ${Object.keys(labs).length ? "open" : ""}>
        <summary><b>Labels</b> <span class="muted small">- optional: switch and DBox names for the network diagram (counts and speeds come from the VMS), report author · saved for this site</span></summary>
        <div class="pr-exp-grid">
          <label>Front-end switch<input data-l="front_switch" value="${lv("front_switch")}" placeholder="e.g. Spectrum 200G"></label>
          <label>Back-end switch<input data-l="back_switch" value="${lv("back_switch")}" placeholder="e.g. Spectrum 200G"></label>
          <label>DBox model<input data-l="dbox_model" value="${lv("dbox_model")}" placeholder="e.g. Ceres 338"></label>
          <label>Author<input data-l="author" value="${lv("author")}" placeholder="for the Word report"></label>
        </div>
      </details>
      <label class="small pr-cl"><input type="checkbox" class="pr-cluster" checked> Add the cluster's own numbers from the VMS
        <span class="muted">(Network Architecture, Data Path and Cluster Performance pages - read-only)</span></label>
      <div class="pr-out"></div>
    </div>
    <div class="btn-row modal-actions ds-actions"></div></div>`;
  const body = wrap.querySelector(".ds-body"), acts = wrap.querySelector(".ds-actions"), out = wrap.querySelector(".pr-out");
  let busyNow = false;
  const close = () => { if (busyNow) return; wrap.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) close(); });
  wrap.querySelector(".ds-x").onclick = close;
  document.addEventListener("keydown", onKey);
  document.body.appendChild(wrap);
  const boxes = () => [...body.querySelectorAll(".pr-run input")];
  const fmts = () => [...body.querySelectorAll(".pr-fmt:checked")].map((b) => b.value);
  const sync = () => {
    const n = boxes().filter((b) => b.checked).length;
    const f = fmts();
    body.querySelector(".pr-all").checked = n === boxes().length;
    acts.querySelector('[data-a="go"]').disabled = !n || !f.length;
    acts.querySelector('[data-a="go"]').textContent = `${f.length === 1 && f[0] === "docx" ? "📄" : "📊"} Generate ${
      f.length === 2 ? "PowerPoint + Word" : f[0] === "docx" ? "Word report" : "PowerPoint"} (${n} run${n === 1 ? "" : "s"})`;
  };
  body.querySelector(".pr-all").onchange = (e) => { boxes().forEach((b) => (b.checked = e.target.checked)); sync(); };
  body.querySelector(".pr-runs").onchange = sync;
  body.querySelector(".pr-formats").onchange = () => { prFormats = fmts(); store.set("reportFormats", prFormats.join(",")); sync(); };
  acts.innerHTML = `<button class="btn ghost" data-a="cancel">Cancel</button><button class="btn primary" data-a="go"></button>`;
  sync();
  acts.onclick = async (e) => {
    const a = e.target.closest("[data-a]")?.dataset.a;
    if (a === "cancel" || a === "close") return close();
    if (a === "logs") { close(); logSel = { id: e.target.closest("[data-id]").dataset.id, file: null }; showTab("t5"); loadResults(); return; }
    if (a !== "go") return;
    busyNow = true;
    wrap.querySelector(".ds-x").hidden = true;
    acts.querySelectorAll("button").forEach((b) => (b.disabled = true));
    out.innerHTML = `<div class="ds-now small"><span class="ds-spin sm"></span> Building the PowerPoint - reading cluster figures from the VMS, charts, tables…</div>`;
    try {
      const expected = Object.fromEntries([...body.querySelectorAll("[data-e]")].map((i) => [i.dataset.e, i.value.trim()]));
      const r = await api("POST", "/api/reports/performance", { runs: boxes().filter((b) => b.checked).map((b) => b.value),
        unit: body.querySelector(".pr-unit").value, expected, cluster_metrics: body.querySelector(".pr-cluster").checked, formats: fmts(),
        labels: Object.fromEntries([...body.querySelectorAll("[data-l]")].map((i) => [i.dataset.l, i.value.trim()])) });
      out.innerHTML = `<div class="pr-done"><svg class="ds-check vf-check" viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="24"/><path d="M15 27 l8 8 l15 -17"/></svg>
        <div><b>Report ready</b> - ${r.runs} run${r.runs === 1 ? "" : "s"}${r.slides ? ` · PowerPoint ${r.slides} slides` : ""}${r.word_template ? ` · Word (${r.word_template === "uploaded" ? "your template" : "built-in layout"})` : ""}${body.querySelector(".pr-cluster").checked
          ? ` · cluster figures for ${r.cluster_runs} run${r.cluster_runs === 1 ? "" : "s"}` : ""}<div class="mono small">${r.files.map(esc).join(" · ")}</div></div></div>`;
      acts.innerHTML = `<button class="btn ghost" data-a="logs" data-id="${esc(r.log_id)}">🗒 Open in Logs</button>
        ${r.files.map((f) => `<a class="btn primary" href="/api/results/${encodeURIComponent(f)}" download>⤓ ${f.endsWith(".docx") ? "Word" : "PowerPoint"}</a>`).join("")}
        <button class="btn ghost" data-a="close">Close</button>`;
      loadResults();   // the new report appears in 🗒 Logs and in Performance result files
    } catch (err) {
      out.innerHTML = `<div class="err ds-err">${esc(err.message)}</div>`;
      acts.innerHTML = `<button class="btn ghost" data-a="cancel">Close</button><button class="btn primary" data-a="go"></button>`;
      sync();
    }
    busyNow = false;
    wrap.querySelector(".ds-x").hidden = false;
  };
}
$("#perfReport").onclick = perfReportModal;
$("#logsReport").onclick = perfReportModal;

/* links like <a data-goto="t5"> outside the site screen */
$("#t2").addEventListener("click", (e) => { if (e.target.dataset?.goto) { e.preventDefault(); showTab(e.target.dataset.goto); } });

/* ================= LOGS =================
   Every run writes <run id>.txt + .json to the site's results/ folder. The Logs tab lists them one row per run;
   the Performance card in tab ② shows only the performance (elbencho) runs. */
let logData = { logs: [], archives: [], dir: "" };
const fmtSize = (b) => (b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : (b / 1024).toFixed(1) + " KB");
const fileLink = (name) => `<a href="/api/results/${encodeURIComponent(name)}" target="_blank" rel="noopener">${esc(name.split(".").pop())}</a>`;

async function loadResults() {
  logData = await api("GET", "/api/logs").catch(() => ({ logs: [], archives: [], dir: "" }));
  const perf = logData.logs.filter((l) => l.performance);
  $("#files").innerHTML = perf.length
    ? perf.flatMap((l) => l.files).map((f) => `<li><a href="/api/results/${encodeURIComponent(f.name)}" target="_blank">${esc(f.name)}</a>
        <span class="muted small">${fmtSize(f.size)}</span></li>`).join("")
    : '<li class="muted small">No performance results yet. Run <b>▶ Run elbencho</b>.</li>';
  $("#logCount").textContent = logData.logs.length || "";
  loadPerfRuns(perfRun?.run_id);   // keeps the run shown; a run that just finished was set by renderSummary(run)
  const types = [...new Map(logData.logs.map((l) => [l.type, l.label])).entries()].sort((a, b) => a[1].localeCompare(b[1]));
  const sel = $("#logsType"), keep = sel.value;
  sel.innerHTML = '<option value="">All types</option>' + types.map(([t, label]) => `<option value="${esc(t)}">${esc(label)}</option>`).join("");
  sel.value = types.some(([t]) => t === keep) ? keep : "";
  renderLogs();
}
let logSel = { id: null, file: null };   // selected run and which of its files is shown
// Make the list | content split end at the window bottom, so both panes scroll inside it (not the page)
function fitLogsSplit() {
  const el = $(".logs-split");
  if (!el || !el.offsetParent) return;                 // Logs tab not visible
  if (window.innerWidth <= 900) { el.style.height = ""; return; }   // stacked layout: CSS decides
  el.style.height = `${Math.max(420, window.innerHeight - el.getBoundingClientRect().top - 16)}px`;
}
window.addEventListener("resize", fitLogsSplit);
const logTime = (l) => (l.time ? new Date(l.time) : new Date(l.mtime * 1000));
function filteredLogs() {
  const type = $("#logsType").value, status = $("#logsStatus").value, q = $("#logsSearch").value.trim().toLowerCase();
  return logData.logs.filter((l) => (!type || l.type === type) && (!status || l.status === status) &&
    (!q || [l.id, l.label, ...l.clients].join(" ").toLowerCase().includes(q)));
}
function renderLogs() {
  const rows = filteredLogs();
  const total = logData.logs.reduce((a, l) => a + l.size, 0);
  $("#logsInfo").innerHTML = `${logData.logs.length} run${logData.logs.length === 1 ? "" : "s"} · ${fmtSize(total)} · <code>${esc(logData.dir)}</code>` +
    (rows.length !== logData.logs.length ? ` · showing ${rows.length}` : "");
  if (!rows.some((l) => l.id === logSel.id)) logSel = { id: rows[0]?.id || null, file: null };
  // list pane: one compact two-line item per run, newest first, grouped by day
  let day = "";
  $("#logsList").innerHTML = rows.length ? rows.map((l) => {
    const t = logTime(l), d = t.toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
    const head = d !== day ? `<div class="logs-day">${esc((day = d))}</div>` : "";
    return head + `<div class="logitem${l.id === logSel.id ? " sel" : ""}" role="option" data-id="${esc(l.id)}" aria-selected="${l.id === logSel.id}">
      <div class="li-top"><span class="li-label">${esc(l.label)}</span><span class="li-time">${t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span></div>
      <div class="li-sub">${l.status ? `<span class="pill ${esc(l.status)}">${esc(l.status)}</span>` : ""}${l.performance ? '<span class="tag">performance</span>' : ""}
        <span class="li-clients">${esc(l.clients.filter(Boolean).join(", "))}</span><span class="li-size">${fmtSize(l.size)}</span></div></div>`;
  }).join("") : `<div class="muted small logs-none">${logData.logs.length ? "No logs match the filter." : "No logs yet. Runs in tab ② write their logs here."}</div>`;
  showLog();
  fitLogsSplit();
  $("#archivesCard").hidden = !logData.archives.length;
  $("#archivesBody").innerHTML = logData.archives.map((a) => `<tr>
      <td><a href="/api/logs/archives/${encodeURIComponent(a.name)}">${esc(a.name)}</a></td>
      <td>${new Date(a.mtime * 1000).toLocaleString()}</td><td class="num">${fmtSize(a.size)}</td></tr>`).join("");
}

// reading pane: the selected run's file (txt by default; json pretty-printed; switch with the format buttons)
let logViewToken = 0;
async function showLog() {
  const box = $("#logView");
  const l = logData.logs.find((x) => x.id === logSel.id);
  if (!l) { box.className = "logview-empty muted"; box.textContent = "Select a log on the left to read it here."; return; }
  const pref = ["txt", "md", "json", "pptx", "docx"];
  const file = l.files.find((f) => f.name === logSel.file) || [...l.files].sort((a, b) => pref.indexOf(a.name.split(".").pop()) - pref.indexOf(b.name.split(".").pop()))[0];
  logSel.file = file.name;
  const ext = file.name.split(".").pop();
  box.className = "logview";
  box.innerHTML = `<div class="lv-head">
      <div><div class="lv-title">${esc(l.label)} ${l.status ? `<span class="pill ${esc(l.status)}">${esc(l.status)}</span>` : ""}${l.performance ? ' <span class="tag">performance</span>' : ""}</div>
        <div class="muted small">${logTime(l).toLocaleString()}${l.clients.filter(Boolean).length ? " · " + esc(l.clients.filter(Boolean).join(", ")) : ""} · <span class="mono">${esc(l.id)}</span></div></div>
      <div class="btn-row">
        <span class="seg">${l.files.map((f) => `<button class="btn sm ${f.name === file.name ? "primary" : "ghost"}" data-file="${esc(f.name)}" title="${esc(f.name)} · ${fmtSize(f.size)}">${esc(f.name.split(".").pop())}</button>`).join("")}</span>
        <button class="btn sm ghost" id="lvCopy" title="Copy the file's text">⧉ Copy</button>
        <a class="btn sm ghost" href="/api/results/${encodeURIComponent(file.name)}" download title="Download ${esc(file.name)}">⤓</a>
        <a class="btn sm ghost" href="/api/results/${encodeURIComponent(file.name)}" target="_blank" rel="noopener" title="Open raw in a new tab">↗</a>
      </div></div>
    <pre class="lv-body ${ext}"><span class="muted">Loading ${esc(file.name)}…</span></pre>`;
  const token = ++logViewToken;
  if (ext === "pptx" || ext === "docx") {   // a report: a download panel instead of text
    const kind = ext === "docx" ? "Word" : "PowerPoint";
    $(".lv-body", box).outerHTML = `<div class="lv-pptx"><div class="lv-pptx-icon" aria-hidden="true">${ext === "docx" ? "📄" : "📊"}</div>
      <div><b>${esc(file.name)}</b><div class="muted small">${kind} · ${fmtSize(file.size)}</div>
      <a class="btn primary" href="/api/results/${encodeURIComponent(file.name)}" download>⤓ Download ${kind}</a>
      ${l.files.some((f) => f.name.endsWith(".md")) ? `<div class="small muted">The <b>md</b> button shows the report's summary table.</div>` : ""}</div></div>`;
    $("#lvCopy", box).hidden = true;
    return;
  }
  let text;
  try { text = await (await fetch(`/api/results/${encodeURIComponent(file.name)}`)).text(); }
  catch (e) { text = `Could not load ${file.name}: ${e.message}`; }
  if (token !== logViewToken) return;  // another log was selected meanwhile
  if (ext === "json") { try { text = JSON.stringify(JSON.parse(text), null, 2); } catch {} }
  const LIMIT = 3_000_000;  // keep the page light: very large files are cut, the full file is one click away
  const body = $(".lv-body", box);
  body.textContent = text.length > LIMIT ? text.slice(0, LIMIT) : text;
  if (text.length > LIMIT) body.insertAdjacentHTML("beforeend", `\n\n<span class="muted">… cut at ${fmtSize(LIMIT)} · download or open raw for the full file (${fmtSize(text.length)})</span>`);
  if (ext === "txt") highlightLog(body);
  $("#lvCopy", box).onclick = (e) => navigator.clipboard.writeText(text).then(() => { e.target.textContent = "✓ Copied"; setTimeout(() => (e.target.textContent = "⧉ Copy"), 1200); }).catch(() => {});
}
// light highlighting for run logs: command lines, exit codes, statuses, headings
function highlightLog(pre) {
  if (pre.textContent.length > 600_000) return;
  pre.innerHTML = esc(pre.textContent)
    .replace(/^(\[\d+\] \$ .*)$/gm, '<span class="lv-cmd">$1</span>')
    .replace(/\b(exit=0)\b/g, '<span class="lv-ok">$1</span>')
    .replace(/\b(exit=(?!0\b)-?\d+)\b/g, '<span class="lv-bad">$1</span>')
    .replace(/^(Status:\s+)(done|ok)$/gm, '$1<span class="lv-ok">$2</span>')
    .replace(/^(Status:\s+)(failed)$/gm, '$1<span class="lv-bad">$2</span>')
    .replace(/^(CLIENT .*)$/gm, '<span class="lv-client">$1</span>')
    .replace(/^(={20,})$/gm, '<span class="muted">$1</span>')
    .replace(/(status=)(ok)\b/g, '$1<span class="lv-ok">$2</span>').replace(/(status=)(failed)\b/g, '$1<span class="lv-bad">$2</span>')
    .replace(/^(\s*(?:error|RESULT|== ).*)$/gm, '<span class="lv-note">$1</span>');
}
$("#logsList").addEventListener("click", (e) => {
  const it = e.target.closest(".logitem");
  if (!it) return;
  logSel = { id: it.dataset.id, file: null };
  $$("#logsList .logitem").forEach((x) => { x.classList.toggle("sel", x === it); x.setAttribute("aria-selected", x === it); });
  showLog();
});
$("#logsList").addEventListener("keydown", (e) => {   // ↑ / ↓ move through the list like a mail client
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  e.preventDefault();
  const items = $$("#logsList .logitem"), i = items.findIndex((x) => x.classList.contains("sel"));
  const next = items[Math.max(0, Math.min(items.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
  if (next) { next.click(); next.scrollIntoView({ block: "nearest" }); }
});
$("#logView").addEventListener("click", (e) => {
  const b = e.target.closest("[data-file]");
  if (b) { logSel.file = b.dataset.file; showLog(); }
});
$("#logsSearch").addEventListener("input", renderLogs);
$("#refreshResults").onclick = loadResults;
$("#logsRefresh").onclick = loadResults;
$("#logsType").onchange = renderLogs;
$("#logsStatus").onchange = renderLogs;

function logsInScope() {
  const incl = $("#logsInclPerf").checked;
  const sel = logData.logs.filter((l) => incl || !l.performance);
  return { incl, runs: sel.length, size: sel.reduce((a, l) => a + l.size, 0), skippedPerf: logData.logs.filter((l) => l.performance).length };
}
$("#logsArchive").onclick = async (e) => {
  await loadResults();
  const s = logsInScope();
  if (!s.runs) { $("#logsMsg").innerHTML = '<span class="muted">Nothing to archive.</span>'; return; }
  const ok = await uiConfirm({ title: `Archive ${s.runs} log${s.runs === 1 ? "" : "s"}?`, ok: "Archive logs",
    html: `<p>Zips the logs of this site (${fmtSize(s.size)}) into <code>results/archive/</code> and removes them from the list.
      The zip stays downloadable under <b>Archives</b>.</p>` +
      (!s.incl && s.skippedPerf ? `<p class="muted">${s.skippedPerf} performance result${s.skippedPerf === 1 ? " is" : "s are"} kept (tick the box to include them).</p>` : "") });
  if (!ok) return;
  busy(e.target, async () => {
    const r = await api("POST", "/api/logs/archive", { include_performance: s.incl });
    await loadResults();
    $("#logsMsg").innerHTML = r.archive
      ? `<span class="okmsg">✓ Archived ${r.runs} log${r.runs === 1 ? "" : "s"} (${r.files} files) to <a href="/api/logs/archives/${encodeURIComponent(r.archive)}">${esc(r.archive)}</a>.</span>`
      : '<span class="muted">Nothing to archive.</span>';
  });
};
$("#logsPurge").onclick = async (e) => {
  await loadResults();
  const s = logsInScope();
  if (!s.runs) { $("#logsMsg").innerHTML = '<span class="muted">Nothing to purge.</span>'; return; }
  const ok = await uiConfirm({ title: `Permanently delete ${s.runs} log${s.runs === 1 ? "" : "s"}?`, ok: "Purge logs", danger: true,
    html: `<p>Deletes the logs of this site (${fmtSize(s.size)}) from <code>results/</code>. <b>This cannot be undone.</b>
      Use <b>Archive logs</b> instead to keep a zip.</p>` +
      (!s.incl && s.skippedPerf ? `<p class="muted">${s.skippedPerf} performance result${s.skippedPerf === 1 ? " is" : "s are"} kept (tick the box to include them).</p>` : "") +
      (logData.archives.length ? `<p class="muted">Existing archives in <code>results/archive/</code> are not touched.</p>` : "") });
  if (!ok) return;
  busy(e.target, async () => {
    const r = await api("POST", "/api/logs/purge", { include_performance: s.incl });
    await loadResults();
    $("#logsMsg").innerHTML = `<span class="okmsg">✓ Deleted ${r.runs} log${r.runs === 1 ? "" : "s"} (${r.files} files).</span>`;
  });
};

/* ================= TENANTS ================= */
let tenantData = { tenants: [], path: "" };
const fmtDate = (s) => (s ? new Date(s).toLocaleString() : "—");
const copyBtn = (text) => text ? `<button class="btn sm ghost copy" data-copy="${esc(text)}" title="Copy">⧉</button>` : "";

async function loadTenants() {
  secretCache.clear();
  tenantData = await api("GET", "/api/tenants").catch(() => ({ tenants: [], path: "" }));
  renderTenants();
  return tenantData;
}
function renderTenants() {
  const all = tenantData.tenants;
  const live = all.filter((t) => !tGone(t));
  $("#tenantCount").textContent = live.length ? live.length : "";
  const list = $("#showDeleted").checked ? all : live;
  $("#tenantsInfo").innerHTML = `${live.length} tenant${live.length === 1 ? "" : "s"} · ${all.length - live.length} deleted · stored in <code>${esc(tenantData.path)}</code>`;
  if (!list.length) {
    $("#tenantList").innerHTML = all.length
      ? `<div class="empty">No active tenants. Tick <b>show deleted</b> to see ${all.length} deleted.</div>`
      : `<div class="empty">No onboarded tenants yet. Run the onboarding in tab ① and they appear here.</div>`;
    return;
  }
  // secrets are masked and fetched from the server only when the user clicks show or copy
  const secretRow = (k, field) =>
    `<b>${esc(k)}</b><span class="secret"><span class="mono secretval" data-field="${field}">••••••••••••</span>` +
    `<button class="btn sm ghost" data-act="reveal" data-field="${field}" title="Show / hide">👁</button>` +
    `<button class="btn sm ghost" data-act="copysecret" data-field="${field}" title="Copy">⧉</button></span>`;
  const row = (k, v, copy = false, mono = false) => v == null || v === "" ? "" :
    `<b>${esc(k)}</b><span class="${mono ? "mono" : ""}">${esc(v)}${copy ? copyBtn(v) : ""}</span>`;
  $("#tenantList").innerHTML = [...list].reverse().map((t) => {
    const vf = t.verify || {};
    const stepPills = Object.entries(t.steps || {}).map(([k, st]) => {
      const vst = vf[k];
      const cls = vst ? (vst === "present" ? "passed" : "failed") : st;
      return `<span class="pill ${esc(cls)}" title="${esc(k)}: ${esc(st)}${vst ? " · on VMS: " + esc(vst) : ""}">${esc(k)}</span>`;
    }).join(" ");
    const verified = t.verified_at
      ? `<span class="pill ${t.verify_ok ? "passed" : "failed"}">${t.verify_ok ? "verified" : "check failed"}</span> <span class="muted small">${fmtDate(t.verified_at)}</span>`
      : "";
    return `<section class="card tenant ${tGone(t) ? "deleted" : ""}" data-key="${esc(t.key)}">
      <div class="card-head">
        <div><h2>${esc(t.name)} <span class="pill ${t.status === "active" ? "passed" : t.status === "partial" ? "reused" : "skipped"}">${esc(t.status)}</span></h2>
          <div class="muted small">${esc(t.vms)} · onboarded ${fmtDate(t.onboarded_at)}${t.deleted_at ? " · deleted " + fmtDate(t.deleted_at) : ""} ${verified}</div>
          ${t.note ? `<div class="small err">${esc(t.note)}</div>` : ""}</div>
        <div class="btn-row">
          ${!tGone(t) ? `<button class="btn" data-act="verify">✓ Verify on VMS</button>` : ""}
          <button class="btn ghost danger" data-act="remove" title="Remove from tenants.yaml (does not touch the VMS)">Remove from list</button>
        </div>
      </div>
      <div class="tgrid">
        <div class="tbox"><h3>Tenant</h3><div class="kv small">
          ${row("Tenant ID", t.tenant_id)}${row("Admin", t.admin?.username, true)}${row("Admin role", t.admin?.role)}
          ${t.admin?.username ? secretRow("Admin password", "admin_password") : ""}
          ${t.admin?.login_url ? `<b>Login</b><span><a href="${esc(t.admin.login_url)}" target="_blank" rel="noopener">${esc(t.admin.login_url)}</a></span>` : ""}</div></div>
        <div class="tbox"><h3>VIP pool</h3><div class="kv small">
          ${row("Name", t.vip_pool?.name)}${row("Range", t.vip_pool?.range, true, true)}${row("IPs", t.vip_pool?.ip_count ?? ((t.vip_pool?.ips || []).length || null))}
          ${row("Subnet CIDR", t.vip_pool?.subnet_cidr != null ? "/" + t.vip_pool.subnet_cidr : null)}${row("VLAN", t.vip_pool?.vlan != null ? (t.vip_pool.vlan || "none (0)") : null)}
          ${row("Gateway", t.vip_pool?.gateway)}${row("ID", t.vip_pool?.id)}</div></div>
        <div class="tbox"><h3>View</h3><div class="kv small">
          ${row("Name", t.view?.name)}${row("Path", t.view?.path, true, true)}${row("Protocols", (t.view?.protocols || []).join(" + "))}${row("Policy", t.view_policy?.name)}${row("Flavor", FLAVORS[t.view_policy?.flavor] || t.view_policy?.flavor)}</div></div>
        <div class="tbox"><h3>QoS &amp; quota</h3><div class="kv small">
          ${row("Read limit", t.qos?.read)}${row("Write limit", t.qos?.write)}${row("Hard limit", t.quota?.hard_limit)}${row("Soft limit", t.quota?.soft_limit && !/^0(\.0+)?\s/.test(t.quota.soft_limit) ? t.quota.soft_limit : "none")}</div>
          ${!tGone(t) ? `<div class="limit-switches" data-limits>
            <div class="small muted">Limits on the VMS (live)</div>
            ${["qos", "quota"].map((k) => `<label class="switch" data-kind="${k}">
              <input type="checkbox" data-limit="${k}" disabled><span class="slider" aria-hidden="true"></span>
              <span class="lim-name">${k === "qos" ? "QoS limits" : "Quota limits"}</span>
              <span class="lim-state muted">checking…</span></label>`).join("")}
            <div class="lim-msg small"></div></div>` : ""}</div>
        <div class="tbox"><h3>NFS</h3><div class="kv small">
          ${row("Export", t.nfs?.export, true, true)}</div>
          ${t.nfs?.mount_example ? `<div class="small muted" style="margin-top:6px">Mount example</div><div class="codeline"><code>${esc(t.nfs.mount_example)}</code>${copyBtn(t.nfs.mount_example)}</div>` : ""}</div>
        <div class="tbox"><h3>S3</h3><div class="kv small">
          ${row("Endpoint", t.s3?.endpoint, true, true)}${row("Bucket", t.s3?.bucket, true)}${row("User", t.s3?.user)}${row("Access key", t.s3?.access_key, true, true)}
          ${t.s3?.access_key ? secretRow("Secret key", "s3_secret_key") : ""}</div></div>
      </div>
      <div class="small steprow">${stepPills}</div>
    </section>`;
  }).join("");
  $$("#tenantList .tenant").forEach((card) => { if ($("[data-limits]", card)) refreshLimits(card); });
}

/* ---- QoS / quota limit switches: off = limits lifted on the VMS (values saved), on = saved values put back ---- */
const decBytes = (b) => { if (b == null) return "–"; const u = ["B", "KB", "MB", "GB", "TB", "PB"]; let i = 0; while (b >= 1000 && i < u.length - 1) { b /= 1000; i++; } return `${+b.toFixed(2)} ${u[i]}`; };
function limitText(kind, s) {
  if (s.error) return `<span class="err">${esc(s.error)}</span>`;
  if (kind === "qos") {
    const fmt = (x) => `read ${x.read_mbps || "∞"} MB/s · write ${x.write_mbps || "∞"} MB/s`;
    return s.enabled ? `<b class="ok">ON</b> · ${esc(s.attached_policy || s.name || "")} · ${fmt(s)}`
      : `<b>OFF</b> · no QoS on the view <span class="muted">(policy ${esc(s.name || "")} kept: ${fmt(s)})</span>`;
  }
  const hard = (x) => `hard ${decBytes(x.hard_limit)}${x.soft_limit ? ` · soft ${decBytes(x.soft_limit)}` : ""}`;
  return s.enabled ? `<b class="ok">ON</b> · ${hard(s)}`
    : `<b>OFF</b> · no limit${s.saved?.hard_limit ? ` <span class="muted">(saved: ${hard(s.saved)})</span>` : ""}`;
}
function showLimit(card, kind, s) {
  const lab = $(`.switch[data-kind="${kind}"]`, card), cb = $("input", lab);
  cb.checked = !!s.enabled && !s.error;
  cb.disabled = !!s.error;
  $(".lim-state", lab).innerHTML = limitText(kind, s);
  $(".lim-state", lab).classList.remove("muted");
}
async function refreshLimits(card) {
  try {
    const st = await api("GET", `/api/tenants/limits?key=${encodeURIComponent(card.dataset.key)}`);
    for (const k of ["qos", "quota"]) showLimit(card, k, st[k] || { error: "not available" });
  } catch (err) {
    $$(".switch input", card).forEach((cb) => (cb.disabled = true));
    $$(".lim-state", card).forEach((x) => (x.innerHTML = `<span class="err">${esc(err.message)}</span>`));
  }
}
$("#tenantList").addEventListener("change", async (e) => {
  const cb = e.target.closest("input[data-limit]");
  if (!cb) return;
  const card = cb.closest(".tenant"), kind = cb.dataset.limit, want = cb.checked;
  const msg = $(".lim-msg", card), name = kind === "qos" ? "QoS" : "Quota";
  $$(".switch input", card).forEach((x) => (x.disabled = true));
  $(`.switch[data-kind="${kind}"] .lim-state`, card).innerHTML = `<span class="muted">${want ? "putting back" : "lifting"} ${name.toLowerCase()} limits on the VMS…</span>`;
  try {
    const s = await api("POST", "/api/tenants/limits", { key: card.dataset.key, kind, enabled: want });
    showLimit(card, kind, s);
    msg.innerHTML = `<span class="okmsg">✓ ${name} limits are ${want ? "back on" : "off"} on the VMS${want ? "" : " (values saved in tenants.yaml)"}.</span>`;
  } catch (err) {
    cb.checked = !want;
    msg.innerHTML = `<span class="err">${name}: ${esc(err.message)}</span>`;
  }
  await refreshLimits(card);
});
$("#tenantList").addEventListener("click", async (e) => {
  const cp = e.target.closest(".copy");
  if (cp) {
    try { await navigator.clipboard.writeText(cp.dataset.copy); cp.textContent = "✓"; setTimeout(() => (cp.textContent = "⧉"), 1200); } catch {}
    return;
  }
  const act = e.target.dataset.act;
  if (!act) return;
  const key = e.target.closest(".tenant").dataset.key;
  if (act === "reveal" || act === "copysecret") {
    const field = e.target.dataset.field;
    const sec = await tenantSecrets(key).catch((err) => (alert(err.message), null));
    if (!sec) return;
    const val = sec[field];
    const out = e.target.closest(".secret").querySelector(".secretval");
    if (!val) { out.textContent = "not stored (onboarded before secrets were kept)"; out.classList.add("muted"); return; }
    if (act === "copysecret") {
      try { await navigator.clipboard.writeText(val); e.target.textContent = "✓"; setTimeout(() => (e.target.textContent = "⧉"), 1200); } catch {}
      return;
    }
    const shown = out.dataset.shown === "1";
    out.textContent = shown ? "••••••••••••" : val;
    out.dataset.shown = shown ? "" : "1";
    return;
  }
  if (act === "verify") return verifyModal(key, e.target.closest(".tenant")?.querySelector("h2")?.firstChild?.textContent?.trim() || key.split("@")[0]);
  if (act === "remove" && await uiConfirm({ title: `Remove ${key.split("@")[0]} from the list?`, ok: "Remove from list", danger: true,
      html: `Removes the record from <code>tenants.yaml</code> only. <b>Nothing is deleted on the VMS</b> - delete the tenant there manually (${esc(key.split("@")[1])}).` }))
    await busy(e.target, async () => { await api("DELETE", `/api/tenants?key=${encodeURIComponent(key)}`); await loadTenants(); });
});
const secretCache = new Map();
async function tenantSecrets(key) {
  if (!secretCache.has(key)) secretCache.set(key, await api("GET", `/api/tenants/secrets?key=${encodeURIComponent(key)}`));
  return secretCache.get(key);
}
$("#showDeleted").onchange = renderTenants;
$("#refreshTenants").onclick = (e) => busy(e.target, loadTenants);

/* ================= TAB 3: configuration ================= */
const cfgEdit = { loaded: false, mode: "form", data: null, original: null, text: "", dirty: false };
const SECTION_INFO = {
  app: "Where the app listens and stores results", vms: "VMS REST API endpoint and credentials",
  vars: "Variables used by onboarding steps and commands", vip_pool: "Candidate IP ranges for the tenant VIP pool",
  tenant_admin: "Tenant admin role and password policy", provisioning: "Tenant onboarding steps (REST calls)",
  ssh: "Default SSH login for clients", clients: "SSH client hosts", mount: "Standard NFSv3 mount and read-ahead",
  elbencho: "Benchmark size and commands; idle_before_read_seconds = pause between write and read in Auto Write/Read",
  packages: "Required client packages and how to install them", execution: "Delays, timeouts, input prompts",
  command_groups: "Preset buttons in Tab 2",
};
const isSecretKey = (k) => /password|token|secret/i.test(k);

async function loadRawConfig() {
  const r = await api("GET", "/api/config/raw");
  cfgEdit.loaded = true;
  cfgEdit.mtime = r.mtime;   // sent back on save, so a newer file on disk is never overwritten
  cfgEdit.data = r.data;
  cfgEdit.original = clone(r.data);
  cfgEdit.text = r.text;
  $("#cfgYaml").value = r.text;
  setDirty(false);
  renderOverview();
  renderForm();
}
function setDirty(v) { cfgEdit.dirty = v; $("#cfgDirty").hidden = !v; }
function cfgMsg(html, cls = "") { $("#cfgMsg").innerHTML = `<span class="${cls}">${html}</span>`; }

function renderOverview() {
  const d = cfgEdit.data || {};
  const vip = d.vip_pool || {};
  const card = (k, v, s = "") => `<div class="ov"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div>`;
  $("#cfgOverview").innerHTML = [
    card("VMS", d.vms?.url || "—", `${d.vms?.api_base || ""} · ${d.vms?.auth_type || ""}`),
    card("Tenant", d.vars?.tenant_name || "—", `${(d.provisioning?.steps || []).length} onboarding steps`),
    card("VIP pool", `${vip.ip_count ?? "?"} IPs`, (vip.ranges || []).map((r) => `${r.start}–${r.end}`).join(", ")),
    card("Clients", (d.clients || []).length, (d.clients || []).map((c) => c.host).join(", ")),
    card("Mount", d.mount?.mount_point || "—", `read-ahead ${d.mount?.read_ahead_kb ?? "?"} KB · VIP ${d.mount?.vip ?? "?"}`),
    card("Packages", (d.packages?.required || []).length + (d.packages?.system || []).length,
      [...(d.packages?.required || []).map((p) => p.name), ...(d.packages?.system || []).map((p) => (typeof p === "string" ? p : p?.name))].filter(Boolean).join(", ")),
    card("Execution", `${d.execution?.delay_between_commands_seconds ?? "?"}s delay`, `timeout ${d.execution?.command_timeout_seconds ?? "?"}s`),
  ].join("");
}

function getAt(path) { return path.reduce((o, k) => (o == null ? o : o[k]), cfgEdit.data); }
function setAt(path, val) {
  let o = cfgEdit.data;
  for (const k of path.slice(0, -1)) o = o[k];
  o[path[path.length - 1]] = val;
  setDirty(true);
  renderOverview();
}

function fieldFor(key, val, path, wide = false) {
  const wrap = document.createElement("label");
  wrap.className = "f-field" + (wide ? " wide" : "");
  const isScalarList = Array.isArray(val) && val.every((x) => x === null || typeof x !== "object");
  const unit = unitControl(val, (v) => { setAt(path, v); wrap.querySelector("input").classList.add("changed"); });
  if (unit) {
    wrap.textContent = key;
    wrap.appendChild(unit);
  } else if (typeof val === "boolean") {
    wrap.classList.add("check");
    wrap.innerHTML = `<input type="checkbox" ${val ? "checked" : ""}> ${esc(key)}`;
    wrap.querySelector("input").onchange = (e) => setAt(path, e.target.checked);
  } else if (typeof val === "number") {
    wrap.innerHTML = `${esc(key)}<input type="number" step="any" value="${val}">`;
    wrap.querySelector("input").onchange = (e) => { setAt(path, e.target.value === "" ? 0 : Number(e.target.value)); e.target.classList.add("changed"); };
  } else if (isScalarList) {
    wrap.classList.add("wide");
    const numeric = val.length > 0 && val.every((x) => typeof x === "number");
    wrap.innerHTML = `${esc(key)} <span class="muted">(one per line)</span><textarea rows="${Math.min(8, Math.max(2, val.length))}">${esc(val.join("\n"))}</textarea>`;
    wrap.querySelector("textarea").onchange = (e) => {
      const items = e.target.value.split("\n").map((s) => s.trim()).filter(Boolean);
      setAt(path, numeric ? items.map(Number) : items);
      e.target.classList.add("changed");
    };
  } else if (val !== null && typeof val === "object") {
    wrap.classList.add("wide");
    wrap.innerHTML = `${esc(key)} <span class="muted">(JSON)</span><textarea rows="3">${esc(JSON.stringify(val))}</textarea>`;
    wrap.querySelector("textarea").onchange = (e) => {
      try { setAt(path, JSON.parse(e.target.value)); e.target.classList.add("changed"); cfgMsg(""); }
      catch { cfgMsg(`Invalid JSON in ${esc(path.join("."))}`, "err"); }
    };
  } else {
    const s = val ?? "";
    const secret = isSecretKey(key) && !String(s).startsWith("${env:");
    const long = String(s).length > 60;
    wrap.innerHTML = long
      ? `${esc(key)}<textarea rows="2">${esc(s)}</textarea>`
      : `${esc(key)}<input type="text"${secret ? ' class="masked" autocomplete="off" spellcheck="false" data-lpignore="true" data-1p-ignore' : ""} value="${esc(s)}">`;
    if (long) wrap.classList.add("wide");
    const inp = wrap.querySelector("input,textarea");
    inp.onchange = (e) => { setAt(path, e.target.value); e.target.classList.add("changed"); };
  }
  return wrap;
}

function objectBody(obj, path) {
  const grid = document.createElement("div");
  grid.className = "f-grid";
  for (const [k, v] of Object.entries(obj)) {
    const p = [...path, k];
    if (Array.isArray(v) && v.length && v.every((x) => x && typeof x === "object" && !Array.isArray(x))) grid.appendChild(listOfObjects(k, v, p));
    else if (v && typeof v === "object" && !Array.isArray(v) && Object.keys(v).length && Object.values(v).some((x) => x === null || typeof x !== "object" || Array.isArray(x))) {
      const box = document.createElement("div");
      box.className = "f-obj";
      box.innerHTML = `<div class="lbl">${esc(k)}</div>`;
      box.appendChild(objectBody(v, p));
      grid.appendChild(box);
    } else grid.appendChild(fieldFor(k, v, p));
  }
  return grid;
}

function itemTitle(item, i) {
  return item.name || item.label || item.id || item.host || item.start || `#${i + 1}`;
}

function listOfObjects(key, list, path) {
  const box = document.createElement("div");
  box.className = "f-list";
  box.innerHTML = `<div class="lbl">${esc(key)} <span class="muted">(${list.length})</span></div>`;
  list.forEach((item, i) => {
    const det = document.createElement("details");
    det.className = "f-item";
    if (list.length <= 3) det.open = true;
    det.innerHTML = `<summary><span>${esc(itemTitle(item, i))} ${item.id && item.name ? `<span class="muted small">${esc(item.id)}</span>` : ""}</span>
      <span class="btn-row"><button class="btn sm ghost" data-act="up" title="Move up">↑</button><button class="btn sm ghost" data-act="dup" title="Duplicate">⧉</button><button class="btn sm ghost danger" data-act="del" title="Remove">✕</button></span></summary>`;
    const body = document.createElement("div");
    body.className = "secbody";
    body.appendChild(objectBody(item, [...path, i]));
    det.appendChild(body);
    det.querySelector("summary .btn-row").onclick = async (e) => {
      e.preventDefault();
      const act = e.target.dataset.act;
      const arr = getAt(path);
      if (act === "del") {
        if (!(await uiConfirm({ title: `Remove ${itemTitle(item, i)}?`, ok: "Remove", danger: true, html: "Takes effect when you click 💾 Save." }))) return;
        arr.splice(i, 1);
      } else if (act === "dup") arr.splice(i + 1, 0, clone(arr[i]));
      else if (act === "up" && i > 0) [arr[i - 1], arr[i]] = [arr[i], arr[i - 1]];
      else return;
      setDirty(true); renderOverview(); renderForm();
    };
    box.appendChild(det);
  });
  const add = document.createElement("button");
  add.className = "btn sm";
  add.textContent = `+ Add to ${key}`;
  add.onclick = () => {
    const arr = getAt(path);
    const tpl = clone(arr[arr.length - 1] || {});
    for (const k of ["name", "id"]) if (k in tpl) tpl[k] = `${tpl[k]}-new`;
    arr.push(tpl);
    setDirty(true); renderOverview(); renderForm();
  };
  box.appendChild(add);
  return box;
}

function renderForm() {
  const root = $("#cfgForm");
  const openState = new Set($$("details.sec[open]", root).map((d) => d.dataset.key));
  root.innerHTML = "";
  for (const [k, v] of Object.entries(cfgEdit.data || {})) {
    const det = document.createElement("details");
    det.className = "sec";
    det.dataset.key = k;
    det.open = openState.size ? openState.has(k) : ["vms", "vip_pool", "mount"].includes(k);
    det.innerHTML = `<summary>${esc(k)} <span class="d">${esc(SECTION_INFO[k] || "")}</span></summary>`;
    const body = document.createElement("div");
    body.className = "secbody";
    if (Array.isArray(v) && v.every((x) => x && typeof x === "object")) body.appendChild(listOfObjects(k, v, [k]));
    else if (v && typeof v === "object" && !Array.isArray(v)) body.appendChild(objectBody(v, [k]));
    else body.appendChild(fieldFor(k, v, [k], true));
    det.appendChild(body);
    root.appendChild(det);
  }
}

$$(".seg-btn").forEach((b) => (b.onclick = async () => {
  const mode = b.dataset.mode;
  if (mode === cfgEdit.mode) return;
  if (cfgEdit.dirty && !(await uiConfirm({ title: "Discard unsaved changes?", ok: "Switch view", html: "Switching views discards unsaved changes in this view." }))) return;
  $$(".seg-btn").forEach((x) => x.classList.toggle("active", x === b));
  cfgEdit.mode = mode;
  await loadRawConfig();
  $("#cfgForm").hidden = mode !== "form";
  $("#cfgYamlCard").hidden = mode !== "yaml";
  cfgMsg("");
}));
$("#cfgYaml").addEventListener("input", () => setDirty(true));
$("#cfgYaml").addEventListener("keydown", (e) => {
  if (e.key === "Tab") { e.preventDefault(); const t = e.target, p = t.selectionStart; t.setRangeText("  ", p, t.selectionEnd, "end"); setDirty(true); }
});

$("#cfgValidate").onclick = (e) => busy(e.target, async () => {
  const text = cfgEdit.mode === "yaml" ? $("#cfgYaml").value : null;
  if (text == null) { cfgMsg("Form values are validated when you save.", "muted"); return; }
  const r = await api("POST", "/api/config/validate", { text });
  cfgMsg(r.ok ? "✓ Valid" : r.errors.map(esc).join("<br>"), r.ok ? "okmsg" : "err");
});
$("#cfgRevert").onclick = (e) => busy(e.target, async () => { await loadRawConfig(); cfgMsg("Reverted to the saved file.", "muted"); });
$("#cfgSave").onclick = (e) => busy(e.target, async () => {
  try {
    const r = cfgEdit.mode === "yaml"
      ? await api("PUT", "/api/config/raw", { text: $("#cfgYaml").value, base_mtime: cfgEdit.mtime })
      : await api("PUT", "/api/config/data", { data: cfgEdit.data, base_mtime: cfgEdit.mtime });
    await loadRawConfig();
    await loadConfig();
    cfgMsg(`✓ Saved and reloaded.${r.backup ? ` Previous version backed up to <code>${esc(r.backup)}</code>` : ""}` +
      envNotes(r.warnings), "okmsg");
    if (!cc.dirty) loadCustomCmds();
  } catch (err) {
    cfgMsg("Not saved: " + (err.errors || [err.message]).map(esc).join("<br>"), "err");
  }
});
window.addEventListener("beforeunload", (e) => { if (cfgEdit.dirty) { e.preventDefault(); e.returnValue = ""; } });

/* ---- Custom Linux commands: their own small editor; saving rewrites only the custom presets ---- */
const cc = { loaded: false, list: [], mtime: null, dirty: false };
function ccSetDirty(v) { cc.dirty = v; $("#ccDirty").hidden = !v; }
function ccMsg(html, cls = "") { $("#ccMsg").innerHTML = html ? `<span class="${cls}">${html}</span>` : ""; }
/* 📄 Report template (Word): upload / download / remove, and what the report will take from it */
async function loadReportTemplate(info) {
  const t = info || await api("GET", "/api/reports/word-template").catch(() => null);
  if (!t) return;
  $("#rtDownload").hidden = $("#rtRemove").hidden = !t.exists;
  if (!t.exists) {
    $("#rtStatus").innerHTML = `<span class="pill">built-in</span> No template uploaded - the Word report uses a plain built-in layout.
      Start from <b>⤓ Starter template</b> or upload your company's .docx.`;
    return;
  }
  const bits = [];
  if (t.error) bits.push(`<span class="err">${esc(t.error)}</span>`);
  if (t.header_images) bits.push(`logo in the header`);
  bits.push(t.content_marker ? "report goes at <code>{{content}}</code>" : "template text is replaced by the report");
  if (t.placeholders?.length) bits.push(`placeholders: ${t.placeholders.map((x) => `<code>{{${esc(x)}}}</code>`).join(" ")}`);
  if (t.missing_styles?.length) bits.push(`<span class="warn-note">missing styles ${t.missing_styles.map(esc).join(", ")} - plain formatting is used</span>`);
  $("#rtStatus").innerHTML = `<span class="pill passed">uploaded</span> <b>${esc(t.name)}</b>
    <span class="muted">· ${fmtSize(t.size)}${t.page ? " · " + esc(t.page) : ""}${t.uploaded_at ? " · " + esc(fmtDate(t.uploaded_at)) : ""}</span>
    <div class="muted" style="margin-top:4px">${bits.join(" · ")}</div>`;
}
$("#rtUpload").onclick = () => { $("#rtFile").value = ""; $("#rtFile").click(); };
$("#rtFile").onchange = async (e) => {
  const f = e.target.files?.[0];
  if (!f) return;
  $("#rtStatus").innerHTML = `<span class="muted"><span class="ds-spin sm"></span> Checking ${esc(f.name)}…</span>`;
  try {
    const r = await fetch(`/api/reports/word-template?filename=${encodeURIComponent(f.name)}`, { method: "PUT", body: f });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `HTTP ${r.status}`);
    loadReportTemplate(d);
    toast(`Template “${f.name}” saved - the next Word report uses it.`, "ok", 4000);
  } catch (err) {
    $("#rtStatus").innerHTML = `<span class="err">Not uploaded: ${esc(err.message)}</span>`;
  }
};
$("#rtRemove").onclick = async (e) => {
  if (!(await uiConfirm({ title: "Remove the report template?", ok: "Remove", danger: true,
    html: "<p>The Word report goes back to the built-in layout. Reports already generated are not changed.</p>" }))) return;
  busy(e.target, async () => loadReportTemplate(await api("DELETE", "/api/reports/word-template")));
};

async function loadCustomCmds() {
  const r = await api("GET", "/api/custom-commands");
  cc.loaded = true; cc.mtime = r.mtime; cc.list = r.commands;
  ccSetDirty(false); renderCustomCmds();
}
function renderCustomCmds() {
  const root = $("#ccList");
  if (!cc.list.length) {
    root.innerHTML = `<div class="empty small">No custom commands yet. Click <b>+ New command</b>, enter a name and the Linux commands, then <b>💾 Save commands</b>.</div>`;
    return;
  }
  root.innerHTML = cc.list.map((c, i) => c.editable === false ? `
    <div class="cc-item locked" data-i="${i}">
      <div class="cc-head"><b>${esc(c.label)}</b><span class="muted small">${esc(c.id)} · written by hand in config.yaml${c.extra ? ` (uses ${c.extra.map(esc).join(", ")})` : ""} - edit it in the YAML view below</span>
        <span class="btn-row cc-acts">${ccMoveBtns(i)}</span></div>
      <pre class="cc-pre">${esc(c.text)}</pre>
    </div>` : `
    <div class="cc-item" data-i="${i}">
      <div class="cc-head">
        <label class="f-field cc-name">Name in the Customs menu<input type="text" data-k="label" value="${esc(c.label)}" placeholder="e.g. Disk usage" maxlength="60"></label>
        <div class="seg cc-mode" role="group" aria-label="How the commands run">
          <button type="button" class="seg-btn ${c.mode !== "script" ? "active" : ""}" data-mode="lines" title="Every line is a separate command, run one after another (stops at the first failure)">One command per line</button>
          <button type="button" class="seg-btn ${c.mode === "script" ? "active" : ""}" data-mode="script" title="The whole text runs as one bash script (use for if/for/variables)">One script</button>
        </div>
        <span class="btn-row cc-acts">${ccMoveBtns(i)}<button type="button" class="btn sm ghost danger" data-act="del" title="Remove">✕</button></span>
      </div>
      <label class="f-field wide">${c.mode === "script" ? "Script (runs as one bash script)" : "Linux commands (one per line, run in order)"}
        <textarea data-k="text" rows="${Math.min(12, Math.max(3, (c.text || "").split("\n").length + 1))}" spellcheck="false" placeholder="df -h ${"${mount.mount_point}"}&#10;lsblk">${esc(c.text)}</textarea></label>
      <div class="cc-opts">
        <label class="f-field check"><input type="checkbox" data-k="sudo" ${c.sudo ? "checked" : ""}> Run with sudo</label>
        <label class="f-field check"><input type="checkbox" data-k="ask" ${c.confirm ? "checked" : ""}> Ask before running</label>
        <label class="f-field cc-confirm" ${c.confirm ? "" : "hidden"}><input type="text" data-k="confirm" value="${esc(c.confirm)}" placeholder="Message shown before it runs"></label>
        <label class="f-field check" title="Parse elbencho output and save it as a performance result (Results card, Chart)"><input type="checkbox" data-k="results" ${c.results ? "checked" : ""}> Save elbencho results</label>
      </div>
    </div>`).join("");
}
const ccMoveBtns = (i) => `<button type="button" class="btn sm ghost" data-act="up" title="Move up" ${i ? "" : "disabled"}>↑</button>` +
  `<button type="button" class="btn sm ghost" data-act="down" title="Move down" ${i < cc.list.length - 1 ? "" : "disabled"}>↓</button>`;
$("#ccList").addEventListener("input", (e) => {
  const item = e.target.closest(".cc-item"), k = e.target.dataset.k;
  if (!item || !k) return;
  const c = cc.list[+item.dataset.i];
  if (k === "ask") {
    const box = $(".cc-confirm", item); box.hidden = !e.target.checked;
    c.confirm = e.target.checked ? ($("input", box).value || `Runs "${c.label || "this command"}" on the ticked consoles.`) : "";
    $("input", box).value = c.confirm;
  } else c[k] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
  ccSetDirty(true);
});
$("#ccList").addEventListener("click", async (e) => {
  const item = e.target.closest(".cc-item");
  if (!item) return;
  const i = +item.dataset.i;
  const mode = e.target.closest(".cc-mode .seg-btn")?.dataset.mode;
  if (mode) { if (cc.list[i].mode !== mode) { cc.list[i].mode = mode; ccSetDirty(true); renderCustomCmds(); } return; }
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (!act) return;
  if (act === "del") {
    if (!(await uiConfirm({ title: `Remove ${cc.list[i].label || "this command"}?`, ok: "Remove", danger: true, html: "Takes effect when you click 💾 Save commands." }))) return;
    cc.list.splice(i, 1);
  } else if (act === "up" && i > 0) [cc.list[i - 1], cc.list[i]] = [cc.list[i], cc.list[i - 1]];
  else if (act === "down" && i < cc.list.length - 1) [cc.list[i + 1], cc.list[i]] = [cc.list[i], cc.list[i + 1]];
  else return;
  ccSetDirty(true); renderCustomCmds();
});
$("#ccAdd").onclick = () => {
  setCollapsed($("#ccCard"), false);
  cc.list.push({ id: null, label: "", mode: "lines", text: "", sudo: false, confirm: "", results: false, editable: true });
  ccSetDirty(true); renderCustomCmds();
  const items = $$("#ccList .cc-item"); items[items.length - 1].scrollIntoView({ block: "nearest" });
  $("input[data-k=label]", items[items.length - 1]).focus();
};
$("#ccRevert").onclick = (e) => busy(e.target, async () => { await loadCustomCmds(); ccMsg("Reverted to the saved commands.", "muted"); });
$("#ccSave").onclick = (e) => busy(e.target, async () => {
  try {
    const r = await api("PUT", "/api/custom-commands", { commands: ccPayload(), base_mtime: cc.mtime });
    cc.mtime = r.mtime; cc.list = r.commands; ccSetDirty(false); renderCustomCmds();
    await loadConfig();                                        // Presets → Customs picks them up at once
    if (!cfgEdit.dirty) await loadRawConfig();                 // keep the full editor in step with the file
    else cfgMsg("config.yaml changed (custom commands saved). Your unsaved edits above would overwrite it - click Revert first.", "err");
    ccMsg(`✓ Saved ${r.commands.length} custom command${r.commands.length === 1 ? "" : "s"} - they are in Presets → Customs (tab ②).` +
      (r.backup ? ` Previous config backed up to <code>${esc(r.backup)}</code>` : ""), "okmsg");
  } catch (err) {
    ccMsg("Not saved: " + (err.errors || [err.message]).map(esc).join("<br>"), "err");
  }
});
window.addEventListener("beforeunload", (e) => { if (cc.dirty) { e.preventDefault(); e.returnValue = ""; } });

// Export: the editor's current list (unsaved edits included) as a YAML file. Import: a file exported here or on
// another site; the commands are added as unsaved changes, so they are reviewed before 💾 Save commands.
const ccPayload = () => cc.list.map((c) => c.editable === false ? { id: c.id, keep: true }
  : { id: c.id, label: c.label, mode: c.mode === "script" ? "script" : "lines", text: c.text || "", sudo: !!c.sudo, confirm: c.confirm || "", results: !!c.results });
$("#ccExport").onclick = (e) => busy(e.target, async () => {
  if (!cc.list.some((c) => c.editable !== false)) { ccMsg("Nothing to export yet - add a command first.", "muted"); return; }
  const r = await api("POST", "/api/custom-commands/export", { commands: ccPayload() });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([r.text], { type: "text/yaml" }));
  a.download = r.filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  ccMsg(`⤓ Exported ${r.count} command${r.count === 1 ? "" : "s"} to <code>${esc(r.filename)}</code>` +
    (r.skipped ? ` · ${r.skipped} written by hand in config.yaml not included` : "") +
    (cc.dirty ? " · includes your unsaved edits" : ""), "okmsg");
});
$("#ccImport").onclick = () => { $("#ccFile").value = ""; $("#ccFile").click(); };
$("#ccFile").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  try {
    if (f.size > 1_000_000) throw new Error("The file is too large (over 1 MB) for a list of commands.");
    const r = await api("POST", "/api/custom-commands/import", { text: await f.text() });
    if (!r.commands.length) { ccMsg(`Nothing imported from <code>${esc(f.name)}</code>: ${r.skipped.map(esc).join("; ")}`, "err"); return; }
    if (!cc.loaded) await loadCustomCmds();
    const byName = new Map(cc.list.map((c, i) => [String(c.label).trim().toLowerCase(), i]));
    const clash = r.commands.filter((c) => byName.has(c.label.toLowerCase()));
    let replace = false;
    if (clash.length) {
      replace = await uiConfirm({ title: `${clash.length} command${clash.length === 1 ? " has" : "s have"} the same name`, ok: "Replace existing", cancel: "Keep both",
        html: `<p>${clash.map((c) => `<b>${esc(c.label)}</b>`).join(", ")} already exist${clash.length === 1 ? "s" : ""} here.</p>
               <p class="muted">Replace overwrites them with the imported version. Keep both adds the imported one as “name (2)”.</p>` });
    }
    let added = 0, replaced = 0;
    for (const c of r.commands) {
      const key = c.label.toLowerCase();
      if (byName.has(key) && replace && cc.list[byName.get(key)].editable !== false) {
        const old = cc.list[byName.get(key)];
        cc.list[byName.get(key)] = { ...c, id: old.id };        // keep the id: logs and results stay linked
        replaced++;
        continue;
      }
      let label = c.label, k = 2;
      while ([...cc.list].some((x) => String(x.label).trim().toLowerCase() === label.toLowerCase())) label = `${c.label} (${k++})`.slice(0, 60);
      cc.list.push({ ...c, label });
      added++;
    }
    setCollapsed($("#ccCard"), false);
    ccSetDirty(true); renderCustomCmds();
    ccMsg(`⤒ Imported from <code>${esc(f.name)}</code>: ${added} added${replaced ? `, ${replaced} replaced` : ""}` +
      (r.skipped.length ? ` · skipped: ${r.skipped.map(esc).join("; ")}` : "") +
      ` - review them, then click <b>💾 Save commands</b>.`, "okmsg");
  } catch (err) {
    ccMsg("Not imported: " + (err.errors || [err.message]).map(esc).join("<br>"), "err");
  }
};

/* ================= TEST SITES ================= */
let siteData = null;
let siteTab = store.get("siteTab") || "active";   // 📁 Test site list: "active" | "vmsdel"
const fmtTime = (t) => (t ? new Date(t * 1000).toLocaleString() : "—");

// The test-site tab. With no site open it is the only usable tab ("sitemode").
async function showSites() {
  showTab("t0");
}
async function renderSites() {
  siteData = await fetch("/api/sites").then((r) => r.json());
  const cur = siteData.current;
  document.body.classList.toggle("sitemode", !cur);
  $("#siteCurrent").hidden = !cur;
  if (cur) {
    const runCmd = `./run.sh "${cur.path}"`;
    $("#siteCurrent").innerHTML = `<section class="card">
      <div class="card-head"><div><h2>📁 ${esc(cur.name)} <span class="pill passed">open</span></h2>
          <div class="muted small">${cur.description ? esc(cur.description) : "no description"}</div></div>
        <div class="btn-row">
          <button class="btn" data-export="${esc(cur.path)}" data-name="${esc(cur.name)}" title="All of this site's configuration, without logs - to import it on another computer">⤓ Export configuration</button>
          <a class="btn ghost" href="/api/sites/export" title="Configuration, tenants and all results / logs">⤓ Download with results</a></div></div>
      <div class="sitegrid">
        <div class="tbox"><h3>Site</h3><div class="kv small">
          <b>Folder</b><span class="mono">${esc(cur.path)}${copyBtn(cur.path)}</span>
          <b>Created</b><span>${cur.created_at ? new Date(cur.created_at).toLocaleString() : "—"}</span>
          <b>Last change</b><span>${fmtTime(cur.updated)}</span>
          <b>Started from</b><span class="mono">${esc(cur.copied_from || "—")}</span>
          <b>VMS</b><span class="mono">${esc(cur.vms || "not set")}</span></div></div>
        <div class="tbox"><h3>Contents</h3><div class="kv small">
          <b>config.yaml</b><span>configuration · <a href="#" data-goto="t3">open in ⚙ Configuration</a></span>
          <b>tenants.yaml</b><span>${cur.tenants} active tenant${cur.tenants === 1 ? "" : "s"} · <a href="#" data-goto="t4">open 🏢 Tenants</a></span>
          <b>results/</b><span>${cur.results} run${cur.results === 1 ? "" : "s"} exported · <a href="#" data-goto="t2">open results</a></span>
          <b>config-backups/</b><span>${cur.backups} previous version${cur.backups === 1 ? "" : "s"}</span></div></div>
        <div class="tbox"><h3>Name &amp; description</h3>
          <form id="siteMeta" class="sitemeta">
            <label class="f-field">Name<input name="name" value="${esc(cur.name)}" maxlength="64" required></label>
            <label class="f-field">Description<input name="description" value="${esc(cur.description || "")}"></label>
            <div><button class="btn" type="submit">Save</button> <span id="siteMetaMsg" class="small"></span></div>
          </form></div>
        <div class="tbox"><h3>Replicate on another machine</h3><ol class="small howto">
          <li><b>⤓ Export configuration</b> (top right) - the whole configuration without logs; tick the box to include saved passwords.</li>
          <li>Send the .zip.</li>
          <li>On the other computer: Test site tab → <b>⤒ Import site</b> → choose the .zip → Open site.</li></ol>
          <div class="small muted">config.yaml is exported as it is, so keep tokens and passwords you don't want to share as <code>\${env:…}</code> references.
            Or copy the whole folder and start it with <span class="codeline"><code>${esc(runCmd)}</code>${copyBtn(runCmd)}</span></div></div>
      </div></section>`;
    $("#siteMeta").onsubmit = async (e) => {
      e.preventDefault();
      try {
        const f = e.target;
        await api("PUT", "/api/sites/meta", { name: f.name.value, description: f.description.value });
        $("#siteName").textContent = f.name.value;
        await renderSites();
        $("#siteMetaMsg").innerHTML = '<span class="okmsg">✓ Saved</span>';
      } catch (err) { $("#siteMetaMsg").innerHTML = `<span class="err">${esc(err.message)}</span>`; }
    };
  }
  $("#sitesDir").textContent = `in ${siteData.sites_dir}`;
  const templates = [...(cur ? [cur] : []), ...siteData.sites.filter((s) => !cur || s.path !== cur.path)];
  const tpls = siteData.templates || [];
  $("#siteTemplate").innerHTML =
    `<optgroup label="Templates"><option value="" data-desc="Generic example - fill in the VMS, VIP ranges and clients.">Example configuration</option>` +
    tpls.map((t) => `<option value="${esc(t.id)}" data-desc="${esc(t.description)}">${esc(t.label)}</option>`).join("") + `</optgroup>` +
    (templates.length ? `<optgroup label="Copy from a test site">` +
      templates.map((s) => `<option value="${esc(s.path)}" data-desc="Copies the configuration only (not tenants or results).">“${esc(s.name)}”${cur && s.path === cur.path ? " (open now)" : ""}</option>`).join("") +
      `</optgroup>` : "");
  showTemplateDesc();
  // two tabs: active sites / sites whose tenant was deleted from the VMS (🗑 Delete site keeps them for their results)
  const isVmsDel = (s) => s.status === "vms record deleted";
  const groups = { active: siteData.sites.filter((s) => !isVmsDel(s)), vmsdel: siteData.sites.filter(isVmsDel) };
  if (!groups[siteTab]) siteTab = "active";
  $("#siteTabs").innerHTML = [["active", "Active sites"], ["vmsdel", "VMS Record Deleted Sites"]].map(([k, label]) =>
    `<button class="subtab ${siteTab === k ? "active" : ""}" role="tab" aria-selected="${siteTab === k}" data-sitetab="${k}">
      ${label} <span class="subtab-n">${groups[k].length}</span></button>`).join("");
  const shown = groups[siteTab];
  $("#siteList").innerHTML = shown.length
    ? `<table class="sitetable"><thead><tr>
        <th>Test site</th><th>VMS</th><th class="num">Tenants</th><th class="num">Results</th><th>Last change</th><th>Folder</th><th></th>
      </tr></thead><tbody>${shown.map((s) => {
        const isCur = cur && s.path === cur.path;
        return `<tr class="${isCur ? "current" : ""}" title="${esc(s.path)}">
          <td><b>${esc(s.name)}</b> ${isCur ? '<span class="pill passed">open</span>' : s.path === siteData.last ? '<span class="tag">last used</span>' : ""}
            ${s.status === "vms record deleted" ? `<span class="pill vmsdel" title="Tenant and assets deleted from the VMS${s.vms_deleted_at ? " on " + esc(fmtDate(s.vms_deleted_at)) : ""}. Local records and test results are kept.">VMS record deleted</span>` : ""}
            ${s.description ? `<div class="small muted">${esc(s.description)}</div>` : ""}</td>
          <td class="mono small">${esc(s.vms || "—")}</td>
          <td class="num">${s.tenants}</td>
          <td class="num">${s.results}</td>
          <td class="small">${fmtTime(s.updated)}</td>
          <td class="mono small folder" title="${esc(s.path)}">${esc(s.path.split("/").slice(-2).join("/"))}</td>
          <td class="acts">${isCur ? `<button class="btn sm" disabled>Opened</button>`
            : `<button class="btn sm ${s.path === siteData.last ? "primary" : ""}" data-open="${esc(s.path)}">Open site</button>`}
            <button class="btn sm ghost" data-export="${esc(s.path)}" data-name="${esc(s.name)}" title="Export this site's configuration (no logs)">⤓ Config</button>
            ${s.status === "vms record deleted" ? `<button class="btn sm ghost" data-del="${esc(s.path)}" data-name="${esc(s.name)}"
              title="Look on the VMS for anything the earlier delete left behind (e.g. the tenant's local provider) and delete it">🧹 Check VMS for leftovers</button>` : `<button class="btn sm ghost danger" data-del="${esc(s.path)}" data-name="${esc(s.name)}"
              title="Delete the tenant and its assets from the VMS. The site's local records and test results are kept.">Delete site</button>`}</td>
        </tr>`;
      }).join("")}</tbody></table>`
    : siteTab === "vmsdel"
      ? `<div class="empty">No sites here yet. When you use <b>Delete site</b>, the tenant is deleted from the VMS and the site moves here, with its test results kept.</div>`
      : siteData.sites.length ? `<div class="empty">No active sites - all ${siteData.sites.length} are in <b>VMS Record Deleted Sites</b>. Click <b>＋ New Test Site</b> to start a new one.</div>`
      : `<div class="empty">No test sites yet - click <b>＋ New Test Site</b>.</div>`;
  // with no sites at all, open the create form straight away
  if (!siteData.sites.length && !cur) showCreatePanel(true);
}
$("#t0").addEventListener("click", (e) => {
  const cp = e.target.closest(".copy");
  if (cp) { navigator.clipboard.writeText(cp.dataset.copy).then(() => { cp.textContent = "✓"; setTimeout(() => (cp.textContent = "⧉"), 1200); }).catch(() => {}); return; }
  if (e.target.dataset.goto) { e.preventDefault(); showTab(e.target.dataset.goto); }
});
function hideSites() {
  document.body.classList.remove("sitemode");
}
async function openSite(req, msgEl) {
  try {
    const r = await api("POST", req.create ? "/api/sites" : "/api/sites/open", req.create || { path: req.path });
    hideSites();
    resetUi();
    await startApp();
    return r;
  } catch (err) {
    msgEl.innerHTML = `<span class="err">${esc(err.message)}</span>`;
  }
}
function resetUi() {
  closeConsoleModal();
  consoles.forEach((c) => { c.closing = true; try { c.ws?.close(); c.term.dispose(); c.el.remove(); } catch {} });
  consoles.clear();
  $("#noConsoles").hidden = false;
  updatePresetsVisibility();
  pkgStatus = {}; vipData = null; provResults = {};
  $("#vipMap").innerHTML = ""; $("#runStatus").innerHTML = ""; $("#summary").innerHTML = "";
  $("#secrets").innerHTML = ""; $("#cleanupOut").innerHTML = ""; $("#vipSettingsMsg").innerHTML = "";
  $("#pkgTable").innerHTML = "Runs on the consoles that are ticked ✓.";
  cfgEdit.loaded = false;
  // the custom commands belong to the site: reload them for the newly opened one
  cc.loaded = false; cc.list = []; cc.mtime = null; ccSetDirty(false); ccMsg(""); renderCustomCmds();
}
$("#siteBtn").onclick = showSites;
$("#siteCreate").onsubmit = (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target).entries());
  openSite({ create: { name: f.name, description: f.description, template: f.template || null } }, $("#siteCreateMsg"))
    .then((r) => { if (r) { e.target.reset(); showCreatePanel(false); } });
};
$("#siteOpenPath").onsubmit = (e) => { e.preventDefault(); openSite({ path: e.target.path.value.trim() }, $("#siteOpenMsg")); };
function showTemplateDesc() {
  const o = $("#siteTemplate").selectedOptions[0];
  $("#siteTemplateDesc").textContent = o?.dataset.desc || "";
}
$("#siteTemplate").addEventListener("change", showTemplateDesc);
function showCreatePanel(show) {
  $("#siteCreatePanel").hidden = !show;
  $("#siteNewBtn").hidden = show;
  $("#siteCreateMsg").innerHTML = "";
  if (show) setTimeout(() => $("#siteCreate [name=name]").focus(), 50);
}
$("#siteNewBtn").onclick = () => showCreatePanel(true);
$("#siteLogBtn").onclick = () => showDeleteLog();

/* ---- ⤓ Export configuration / ⤒ Import site: a site's whole configuration without its logs ---- */
async function exportSiteModal(path, name) {
  exportSecretsChoice = false;   // never carried over from an earlier export
  const ok = await uiConfirm({ title: `⤓ Export configuration - ${name}`, ok: "⤓ Download .zip", wide: false,
    html: `<p>Everything that configures this site, <b>without logs or test results</b>:</p>
      <ul class="small rs-list"><li><code>config.yaml</code> - VMS connection, onboarding settings, VIP pool, SSH clients, presets,
        custom Linux commands, report targets and diagram labels</li>
        <li><code>site.yaml</code> - name, description, status</li><li><code>tenants.yaml</code> - onboarded tenants</li></ul>
      <label class="ds-warn small"><input type="checkbox" id="expSecrets">
        <span>Also include the saved passwords - SSH client passwords, tenant admin password, S3 keys - and the onboarding state.
        <b>Anyone with the zip can read them.</b></span></label>
      <p class="small muted">Import it on another computer with <b>⤒ Import site</b> (Test site tab).</p>` });
  if (!ok) return;
  location.href = `/api/sites/export-config?path=${encodeURIComponent(path)}&include_secrets=${exportSecretsChoice ? "true" : "false"}`;
}
let exportSecretsChoice = false;
document.addEventListener("change", (e) => { if (e.target.id === "expSecrets") exportSecretsChoice = e.target.checked; });
$("#siteCurrent").addEventListener("click", (e) => {
  const b = e.target.closest("[data-export]");
  if (b) exportSiteModal(b.dataset.export, b.dataset.name);
});
$("#siteImportBtn").onclick = () => { $("#siteImportFile").value = ""; $("#siteImportFile").click(); };
$("#siteImportFile").onchange = async (e) => {
  const f = e.target.files?.[0];
  if (!f) return;
  const msg = $("#siteListMsg");
  msg.innerHTML = `<span class="muted"><span class="ds-spin sm"></span> Importing ${esc(f.name)}…</span>`;
  try {
    const r = await fetch(`/api/sites/import?filename=${encodeURIComponent(f.name)}`, { method: "POST", headers: { "Content-Type": "application/zip" }, body: f });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `HTTP ${r.status}`);
    msg.innerHTML = "";
    siteTab = "active"; store.set("siteTab", "active");
    await renderSites();
    const open = await uiConfirm({ title: `⤒ Imported “${d.name}”`, ok: "Open site", cancel: "Close",
      html: `<p>New test site <b>${esc(d.name)}</b>${d.renamed ? " (renamed - a site with that name already exists)" : ""}.</p>
        <p class="small">Imported: ${d.files.map((x) => `<code>${esc(x)}</code>`).join(" ")}</p>
        ${d.skipped.length ? `<p class="small muted">Not imported (logs, results, backups): ${d.skipped.length} file${d.skipped.length === 1 ? "" : "s"}.</p>` : ""}
        ${d.includes_secrets ? "" : `<p class="small muted">No saved passwords were in the zip - enter SSH passwords with ✎ Edit on the Clients tab, or use \${env:…} values.</p>`}` });
    if (open) openSite({ path: d.path }, msg);
  } catch (err) {
    msg.innerHTML = `<span class="err">Import failed: ${esc(err.message)}</span>`;
  }
};
const onSiteTab = (e) => {
  const b = e.target.closest("[data-sitetab]");
  if (!b || b.dataset.sitetab === siteTab) return;
  siteTab = b.dataset.sitetab; store.set("siteTab", siteTab);
  e.preventDefault();
  $("#siteListMsg").innerHTML = "";
  renderSites();
};
$("#siteTabs").addEventListener("click", onSiteTab);
$("#siteListMsg").addEventListener("click", onSiteTab);
$("#siteCreateCancel").onclick = () => { $("#siteCreate").reset(); showCreatePanel(false); };
// Tenants that still exist on the VMS after their site's local records are deleted.
function vmsReminder(tenants) {
  if (!tenants.length) return "";
  const order = ["view", "view_policy", "s3_keys", "s3_user", "qos", "quota", "vippool", "tenant_admin", "admin_role", "tenant"];
  const label = { view: "view", view_policy: "view policy", s3_keys: "S3 access key", s3_user: "S3 user", qos: "QoS policy", quota: "quota",
    vippool: "VIP pool", tenant_admin: "tenant admin", admin_role: "admin role", tenant: "tenant" };
  return `<div class="vmsreminder"><b>⚠ Delete ${tenants.length === 1 ? "this tenant" : "these tenants"} manually in VMS</b>
    <ul>${tenants.map((t) => {
      const objs = order.filter((o) => (t.objects || []).includes(o)).map((o) => label[o]);
      return `<li><b>${esc(t.name)}</b>${t.tenant_id != null ? ` (tenant id ${esc(t.tenant_id)})` : ""} on <code>${esc(t.vms || "?")}</code>` +
        (t.vip_range ? ` · VIP pool ${esc(t.vip_range)}` : "") +
        (objs.length ? `<div class="small muted">Delete in this order: ${esc(objs.join(" → "))}</div>` : "") + `</li>`;
    }).join("")}</ul>
    <div class="small muted">Or, before deleting the site, open it and use 🧹 Cleanup in tab ① to remove them from the VMS.</div></div>`;
}
$("#siteList").addEventListener("click", async (e) => {
  const b = e.target;
  if (b.dataset.open) return openSite({ path: b.dataset.open }, $("#siteListMsg"));
  if (b.dataset.export) return exportSiteModal(b.dataset.export, b.dataset.name);
  if (b.dataset.del) {
    const site = siteData.sites.find((s) => s.path === b.dataset.del) || { name: b.dataset.name, path: b.dataset.del, active_tenants: [] };
    deleteSiteModal(site);
  }
});

/* ---- 🗑 Delete site: a 4-step modal. ① type the site + tenant names → ② scan the tenant on the VMS and review
   everything under it → ③ delete in order, animated → ④ done (folder moved to sites/.trash/). ---- */
const DS_ORDER = [["view", "View"], ["view_policy", "View policy"], ["s3_user", "S3 user"], ["qos", "QoS policy"],
  ["quota", "Quota"], ["vippool", "VIP pool"], ["tenant_admin", "Tenant admin"], ["admin_role", "Admin role"], ["tenant", "Tenant"], ["local_provider", "Local provider"]];
const dsLow = (l) => l.replace(/^(View|Quota|Tenant|Admin|Local)\b/, (m) => m.toLowerCase());          // "View policy" → "view policy", "S3 user" stays
const dsPlural = (l) => (l.endsWith("y") ? l.slice(0, -1) + "ies" : l + "s");
const dsTrash = (p) => (p ? "sites/.trash/" + String(p).split("/").pop() : "sites/.trash/");
const dsSleep = (ms) => new Promise((r) => setTimeout(r, ms));
const dsReduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

async function streamNdjson(url, body, onEvent) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) {
    const d = (await r.json().catch(() => ({}))).detail;
    throw new Error(typeof d === "string" ? d : `HTTP ${r.status}`);
  }
  const rd = r.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await rd.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const ln = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (ln) await onEvent(JSON.parse(ln));
    }
  }
  if (buf.trim()) await onEvent(JSON.parse(buf));
}

function deleteSiteModal(site) {
  const isCur = siteData.current && siteData.current.path === site.path;
  const leftovers = site.status === "vms record deleted";   // 🧹 Check VMS for leftovers on an already-deleted site
  const recorded = (leftovers ? site.deleted_tenants || [] : (site.active_tenants || []).map((t) => t.name)).filter(Boolean);
  const st = { step: 1, siteName: "", tenants: recorded.length ? recorded.map(() => "") : [""], scan: null, running: false,
    ok: false, deleted: 0, total: 0, movedTo: null };
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop ds-backdrop";
  wrap.innerHTML = `<div class="modal ds-modal" role="dialog" aria-modal="true" aria-labelledby="dsTitle">
    <div class="ds-head"><h3 id="dsTitle">${leftovers ? `🧹 Check VMS for leftovers - “${esc(site.name)}”` : `🗑 Delete test site “${esc(site.name)}”`}</h3>
      <button class="btn ghost sm ds-x" title="Close (Esc)" aria-label="Close">✕</button></div>
    <ol class="ds-stepper">${["Confirm names", "Review VMS assets", "Delete", "Done"].map((t, i) =>
      `<li data-s="${i + 1}"><span class="ds-num">${i + 1}</span><span>${t}</span></li>`).join("")}</ol>
    <div class="ds-body" aria-live="polite"></div>
    <div class="btn-row modal-actions ds-actions"></div></div>`;
  const body = wrap.querySelector(".ds-body"), acts = wrap.querySelector(".ds-actions");
  const close = () => {
    if (st.running) return;
    wrap.remove(); document.removeEventListener("keydown", onKey);
    if (st.step >= 3) afterSiteDeleted(site, isCur, st);
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) close(); });
  wrap.querySelector(".ds-x").onclick = close;
  document.addEventListener("keydown", onKey);
  document.body.appendChild(wrap);

  const setStep = (n) => {
    st.step = n;
    body.oninput = body.onkeydown = null;   // step 1's handlers must not fire on later steps' inputs
    wrap.querySelectorAll(".ds-stepper li").forEach((li) => {
      const s = +li.dataset.s;
      li.classList.toggle("active", s === n); li.classList.toggle("done", s < n);
    });
    body.classList.remove("ds-enter"); void body.offsetWidth; body.classList.add("ds-enter");
  };
  const chain = DS_ORDER.map(([, l]) => `<span class="ds-chip">${esc(l)}</span>`).join('<span class="ds-arrow">→</span>');

  // ---------- ① Confirm names ----------
  function step1() {
    setStep(1);
    body.innerHTML = (leftovers ? `
      <p>This site's tenant was already deleted from the VMS <code>${esc(site.vms || "?")}</code>. The app looks for anything that was
        left behind - such as the tenant's local provider <code>provider-&lt;tenant&gt;</code> - and deletes it after you review it.
        <b>VMS objects can't be restored.</b></p>` : `
      <p>This deletes the tenant${recorded.length > 1 ? "s" : ""} and everything under ${recorded.length > 1 ? "them" : "it"} on the VMS
        <code>${esc(site.vms || "?")}</code>. <b>VMS objects can't be restored.</b></p>`) + `
      <p class="small muted">The site's local records are <b>kept</b> - configuration, <code>tenants.yaml</code> and test results stay in the site,
        which is marked <b>VMS record deleted</b>.</p>
      <div class="ds-order"><div class="small muted">Assets are deleted in this order:</div>${chain}</div>
      <label class="ds-field"><span>Type the test site name <code>${esc(site.name)}</code></span>
        <span class="ds-inwrap"><input data-f="site" autocomplete="off" spellcheck="false" value="${esc(st.siteName)}" placeholder="${esc(site.name)}"><i class="ds-ok">✓</i></span></label>
      ${st.tenants.map((v, i) => recorded.length
        ? `<label class="ds-field"><span>Type the tenant name on VMS <code>${esc(recorded[i])}</code></span>
            <span class="ds-inwrap"><input data-f="tenant" data-i="${i}" autocomplete="off" spellcheck="false" value="${esc(v)}" placeholder="${esc(recorded[i])}"><i class="ds-ok">✓</i></span></label>`
        : `<label class="ds-field"><span>Type the tenant name on VMS <span class="muted small">- this site has no tenant on record; leave empty if it never created one</span></span>
            <span class="ds-inwrap"><input data-f="tenant" data-i="${i}" autocomplete="off" spellcheck="false" value="${esc(v)}" placeholder="tenant name"><i class="ds-ok">✓</i></span></label>`).join("")}
`;
    acts.innerHTML = `<button class="btn ghost" data-a="cancel">Cancel</button>
      <button class="btn danger-solid" data-a="scan" disabled>Find assets on VMS →</button>`;
    const valid = () => {
      const siteOk = st.siteName.trim() === site.name;
      body.querySelector('[data-f="site"]').closest(".ds-field").classList.toggle("ok", siteOk);
      let tOk = true;
      body.querySelectorAll('[data-f="tenant"]').forEach((el) => {
        const i = +el.dataset.i, v = st.tenants[i].trim();
        const good = recorded.length ? v === recorded[i] : true;
        el.closest(".ds-field").classList.toggle("ok", recorded.length ? good : !!v);
        tOk = tOk && good;
      });
      const btn = acts.querySelector('[data-a="scan"]');
      btn.disabled = !(siteOk && tOk);
      btn.textContent = st.tenants.some((t) => t.trim()) ? "Find assets on VMS →" : "Continue →";
    };
    body.oninput = (e) => {
      const el = e.target;
      if (el.dataset.f === "site") st.siteName = el.value;
      if (el.dataset.f === "tenant") st.tenants[+el.dataset.i] = el.value;
      valid();
    };
    body.onkeydown = (e) => { if (e.key === "Enter" && !acts.querySelector('[data-a="scan"]').disabled) step2(); };
    acts.onclick = (e) => {
      const a = e.target.closest("[data-a]")?.dataset.a;
      if (a === "cancel") close();
      if (a === "scan") step2();
    };
    valid();
    setTimeout(() => body.querySelector("input")?.focus(), 60);
  }

  // ---------- ② Review VMS assets ----------
  const payload = () => ({ path: site.path, site_name: st.siteName.trim(), tenants: st.tenants.map((t) => t.trim()).filter(Boolean) });
  async function step2() {
    setStep(2);
    const names = payload().tenants;
    if (!names.length) { st.scan = { tenants: [], vms: site.vms }; return renderReview(); }
    body.innerHTML = `<div class="ds-scanning"><span class="ds-spin"></span>
      <div><b>Checking the VMS…</b><div class="small muted ds-ticker">Looking up tenant ${names.map((n) => `“${esc(n)}”`).join(", ")} on <code>${esc(site.vms || "")}</code></div></div></div>`;
    acts.innerHTML = `<button class="btn ghost" data-a="back">← Back</button>`;
    acts.onclick = (e) => { if (e.target.closest("[data-a]")?.dataset.a === "back") step1(); };
    let k = 0;
    const tick = setInterval(() => {
      const t = body.querySelector(".ds-ticker");
      if (!t) return clearInterval(tick);
      t.textContent = `Listing ${dsLow(dsPlural(DS_ORDER[k++ % DS_ORDER.length][1]))} under the tenant…`;
    }, 450);
    try {
      st.scan = await api("POST", "/api/sites/teardown/scan", payload());
      clearInterval(tick);
      if (st.step === 2) renderReview();
    } catch (err) {
      clearInterval(tick);
      if (st.step !== 2) return;
      body.innerHTML = `<div class="err ds-err"><b>Couldn't check the VMS.</b><br>${esc(err.message)}</div>
        <p class="small muted">Nothing was deleted. Check the VMS connection, then try again.</p>`;
      acts.innerHTML = `<button class="btn ghost" data-a="back">← Back</button>
        <button class="btn primary" data-a="retry">↻ Try again</button>`;
      acts.onclick = async (e) => {
        const a = e.target.closest("[data-a]")?.dataset.a;
        if (a === "back") step1();
        if (a === "retry") step2();
      };
    }
  }

  const rowId = (t, k) => `${t}|${k}`;
  function timeline(mode) {
    // mode "review": what will be deleted; mode "run": the same rows with live status
    const ts = st.scan.tenants;
    let n = 0;
    const html = ts.map((t) => {
      const left = (t.groups || []).filter((g) => g.items.length);
      if (!t.found && !left.length) return `<div class="ds-tenant"><div class="ds-thead"><b>Tenant “${esc(t.name)}”</b>
          <span class="pill">not on VMS</span></div><div class="small muted">Not found on the VMS - nothing to delete there${t.recorded ? " (it was probably deleted already)" : ""}.</div></div>`;
      return `<div class="ds-tenant"><div class="ds-thead"><b>Tenant “${esc(t.name)}”</b> ${t.found ? `<span class="muted small">id ${esc(t.id)}</span>` : `<span class="pill">not on VMS</span>`}
          ${t.recorded || !t.found ? "" : `<span class="pill warn">not created by this site</span>`}</div>
        ${t.found ? "" : `<div class="small muted ds-leftnote">The tenant is already gone, but its local provider was left behind on the VMS.</div>`}
        <ol class="ds-timeline">${t.groups.map((g) => {
          const empty = !g.items.length;
          return `<li class="ds-row ${empty ? "ds-none" : ""} pending" data-row="${esc(rowId(t.name, g.kind))}" style="--i:${n++}">
            <span class="ds-dot" aria-hidden="true"></span>
            <div class="ds-rmain"><div class="ds-rhead"><b>${esc(g.label)}</b>
              <span class="ds-count">${empty ? "none" : g.items.length}</span><span class="ds-state small muted"></span></div>
              ${empty ? "" : `<ul class="ds-items">${g.items.map((it) => `<li data-item="${esc(rowId(t.name, g.kind) + "|" + it.id)}">
                  <span class="ds-ic"></span><span class="ds-iname">${esc(it.name)}</span>${it.detail ? ` <span class="muted small mono">${esc(it.detail)}</span>` : ""}<span class="ds-imsg small"></span></li>`).join("")}</ul>`}
            </div></li>`;
        }).join("")}</ol>
        ${t.notes.length ? `<div class="ds-notes small">${t.notes.map((x) => `<div>ⓘ ${esc(x)}</div>`).join("")}</div>` : ""}</div>`;
    }).join("");
    return html + `<ol class="ds-timeline ds-local"><li class="ds-row pending" data-row="local" style="--i:${n}">
        <span class="ds-dot" aria-hidden="true"></span><div class="ds-rmain"><div class="ds-rhead"><b>Site status → VMS record deleted</b>
        <span class="ds-state small muted"></span></div>
        <div class="small muted">Local records are kept: config.yaml, tenants.yaml, saved credentials and test results</div></div></li></ol>`;
  }

  function renderReview() {
    const ts = st.scan.tenants;
    st.total = ts.reduce((a, t) => a + t.groups.reduce((b, g) => b + g.items.length, 0), 0);
    const foreign = ts.filter((t) => t.found && !t.recorded);
    const mismatch = ts.filter((t) => t.found && t.recorded && t.recorded_id_matches === false);
    body.innerHTML = `
      <p>${st.total ? `<b>${st.total} object${st.total === 1 ? "" : "s"}</b> will be deleted from <code>${esc(st.scan.vms)}</code>, top to bottom.`
        : "Nothing to delete on the VMS."} The site's local records and test results are kept.</p>
      ${foreign.length ? `<label class="ds-warn"><input type="checkbox" data-f="foreign">
          <span>Tenant ${foreign.map((t) => `“${esc(t.name)}”`).join(", ")} is not in this site's records. I'm sure it is the right tenant and want it deleted.</span></label>` : ""}
      ${mismatch.length ? `<div class="ds-warn">⚠ Tenant ${mismatch.map((t) => `“${esc(t.name)}”`).join(", ")} has a different id on the VMS than this site recorded - it may have been re-created.</div>` : ""}
      <div class="ds-scroll">${timeline("review")}</div>`;
    acts.innerHTML = `<button class="btn ghost" data-a="back">← Back</button>
      ${leftovers && !st.total ? `<button class="btn primary" data-a="close">✓ Nothing left on the VMS - close</button>`
        : `<button class="btn danger-solid" data-a="go">${st.total ? `Delete ${st.total} object${st.total === 1 ? "" : "s"} from VMS` : "Mark VMS record deleted"}</button>`}`;
    const go = acts.querySelector('[data-a="go"]');
    const cb = body.querySelector('[data-f="foreign"]');
    if (cb && go) { go.disabled = true; cb.onchange = () => { go.disabled = !cb.checked; }; }
    acts.onclick = (e) => {
      const a = e.target.closest("[data-a]")?.dataset.a;
      if (a === "back") step1();
      if (a === "close") close();
      if (a === "go" && go && !go.disabled) step3();
    };
  }

  // ---------- ③ Delete (animated) ----------
  // force: { tenant name: typed "force delete <name>" } - re-runs keeping the timeline that is already on screen
  async function step3(force = null) {
    st.running = true;
    wrap.querySelector(".ds-x").hidden = true;
    if (force) {
      body.querySelectorAll(".ds-err, .ds-force").forEach((el) => el.remove());
      body.querySelector(".ds-now").innerHTML = `<span class="ds-spin sm"></span> <span class="ds-nowtxt">Force deleting…</span>`;
    } else {
      setStep(3);
      st.deleted = 0;
      body.innerHTML = `<div class="ds-progress"><div class="ds-bar"><i style="width:0%"></i></div>
          <div class="ds-now small"><span class="ds-spin sm"></span> <span class="ds-nowtxt">Starting…</span></div></div>
        <div class="ds-scroll">${timeline("run")}</div>`;
    }
    acts.innerHTML = `<span class="small muted">Please keep this window open until it finishes.</span>`;
    const bar = body.querySelector(".ds-bar i"), now = body.querySelector(".ds-nowtxt");
    const totalSteps = st.total + 1;   // + local records
    const prog = () => { bar.style.width = `${Math.round((st.deleted / totalSteps) * 100)}%`; };
    const row = (t, k) => body.querySelector(`[data-row="${CSS.escape(rowId(t, k))}"]`);
    const setRow = (el, cls, txt) => {
      if (!el) return;
      el.classList.remove("pending", "active", "done", "failed", "skipped"); el.classList.add(cls);
      if (txt != null) el.querySelector(".ds-state").textContent = txt;
      if (cls === "active") el.scrollIntoView({ block: "nearest", behavior: dsReduced() ? "auto" : "smooth" });
    };
    const pace = dsReduced() ? 0 : 260;
    let stop = null, canForce = null;
    const onEvent = async (ev) => {
      if (ev.t === "kind") {
        const el = row(ev.tenant, ev.kind);
        const label = DS_ORDER.find(([k]) => k === ev.kind)?.[1] || ev.kind;
        if (!ev.count) { if (!el?.classList.contains("done")) { setRow(el, "done", "nothing to delete"); await dsSleep(pace / 3); } }
        else if (ev.kind === "tenant" && force?.[ev.tenant]) { setRow(el, "active", "force deleting…"); now.textContent = `Force deleting tenant “${ev.tenant}”…`; }
        else { setRow(el, "active", "deleting…"); now.textContent = `Deleting ${dsLow(dsPlural(label))} in tenant “${ev.tenant}”…`; }
        if (ev.new?.length) toast(`New ${dsLow(label)} found since the review and not deleted: ${ev.new.join(", ")}`, "err", 9000);
      } else if (ev.t === "item") {
        const it = body.querySelector(`[data-item="${CSS.escape(rowId(ev.tenant, ev.kind) + "|" + ev.id)}"]`);
        if (ev.s === "deleting") {
          it?.classList.add("active");
          now.textContent = `Deleting ${dsLow(DS_ORDER.find(([k]) => k === ev.kind)?.[1] || ev.kind)} “${ev.name}”…`;
        } else {
          it?.classList.remove("active", "failed"); it?.classList.add(ev.s === "failed" ? "failed" : ev.s === "skipped" ? "skipped" : "done");
          if (it) it.querySelector(".ds-imsg").textContent = ev.msg ? ` - ${ev.msg}` : ev.forced && ev.s !== "failed" ? " - force deleted" : "";
          if (ev.s !== "failed") { st.deleted++; prog(); }
          await dsSleep(pace);
        }
      } else if (ev.t === "kind_done") {
        const el = row(ev.tenant, ev.kind);
        if (el && !el.classList.contains("done")) setRow(el, "done", ev.kind === "tenant" && force?.[ev.tenant] ? "force deleted" : "deleted");
      } else if (ev.t === "tenant_gone") {
        now.textContent = `Tenant “${ev.name}” is not on the VMS - nothing to delete there`;
      } else if (ev.t === "stop") {
        stop = ev.msg;
        canForce = ev.can_force ? ev.tenant_name || ev.tenant : null;
        if (ev.kind) setRow(row(ev.tenant, ev.kind), "failed", "failed");
      } else if (ev.t === "local") {
        const el = body.querySelector('[data-row="local"]');
        if (ev.s === "updating") { setRow(el, "active", "updating…"); now.textContent = "Marking the site “VMS record deleted”…"; await dsSleep(pace); }
        else { setRow(el, "done", "local records kept"); st.deleted++; prog(); }
      } else if (ev.t === "done") {
        st.ok = true;
      }
    };
    try {
      await streamNdjson("/api/sites/teardown/run", { ...payload(), force: force || {}, plan: Object.fromEntries(st.scan.tenants.filter((t) => t.found || (t.groups || []).some((g) => g.items.length))
        .map((t) => [t.name, Object.fromEntries(t.groups.map((g) => [g.kind, g.items.map((i) => i.id)]))])) }, onEvent);
    } catch (err) {
      stop = stop || `Lost contact with the app: ${err.message}. Some objects may already be deleted - scan again to see what is left.`;
    }
    st.running = false;
    wrap.querySelector(".ds-x").hidden = false;
    if (st.ok) { bar.style.width = "100%"; await dsSleep(dsReduced() ? 0 : 450); return step4(); }
    if (!stop) stop = "The delete ended without finishing. Scan again to see what is left.";
    body.querySelector(".ds-now").innerHTML = `<span class="ds-xmark">✕</span> <b>Stopped.</b>`;
    body.querySelector(".ds-progress").insertAdjacentHTML("afterend", `<div class="err ds-err">${esc(stop)}
      <div class="small">Nothing after this step was deleted and the site's local records are kept. Fix the problem on the VMS, then try again.</div></div>`);
    acts.innerHTML = `<button class="btn ghost" data-a="close">Close</button>
      ${canForce ? `<button class="btn ghost danger" data-a="force">⚠ Force delete tenant…</button>` : ""}
      <button class="btn primary" data-a="retry">↻ Scan again and continue</button>`;
    acts.onclick = (e) => {
      const a = e.target.closest("[data-a]")?.dataset.a;
      if (a === "close") close();
      if (a === "retry") step2();
      if (a === "force") forcePanel(canForce);
    };
  }

  // The VMS refused the tenant delete (e.g. "Failed to delete tenant's empty subdirectories ... consider using force").
  // DELETE /tenants/{id}/?force_remove=true also deletes any files left in the tenant's directory, so the user types
  // "force delete <tenant name>" first. Every force delete is written to the delete log.
  function forcePanel(tname) {
    if (body.querySelector(".ds-force")) return body.querySelector(".ds-force input").focus();
    const phrase = `force delete ${tname}`;
    body.querySelector(".ds-err").insertAdjacentHTML("afterend", `<div class="ds-force">
      <b>⚠ Force delete tenant “${esc(tname)}”</b>
      <p class="small">The VMS deletes the tenant <b>even if files remain in its directory - those files are deleted permanently</b>.
        Everything else under the tenant is already gone. This is recorded in the app's delete log.</p>
      <label class="ds-field"><span>Type <code>${esc(phrase)}</code> to confirm</span>
        <span class="ds-inwrap"><input data-f="force" autocomplete="off" spellcheck="false" placeholder="${esc(phrase)}"><i class="ds-ok">✓</i></span></label>
      <div class="btn-row"><button class="btn ghost" data-fa="cancel">Cancel</button>
        <button class="btn danger-solid" data-fa="go" disabled>Force delete tenant</button></div></div>`);
    const box = body.querySelector(".ds-force"), inp = box.querySelector("input"), go = box.querySelector('[data-fa="go"]');
    acts.querySelector('[data-a="force"]').disabled = true;
    const ok = () => inp.value.trim() === phrase;
    inp.oninput = () => { go.disabled = !ok(); inp.closest(".ds-field").classList.toggle("ok", ok()); };
    inp.onkeydown = (e) => { if (e.key === "Enter" && ok()) go.click(); };
    box.onclick = (e) => {
      const a = e.target.closest("[data-fa]")?.dataset.fa;
      if (a === "cancel") { box.remove(); acts.querySelector('[data-a="force"]').disabled = false; }
      if (a === "go" && ok()) step3({ [tname]: inp.value.trim() });
    };
    box.scrollIntoView({ block: "nearest" });
    setTimeout(() => inp.focus(), 50);
  }

  // ---------- ④ Done ----------
  function step4() {
    setStep(4);
    wrap.querySelectorAll(".ds-stepper li").forEach((li) => li.classList.add("done"));
    body.innerHTML = `<div class="ds-done">
      <svg class="ds-check" viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="24"/><path d="M15 27 l8 8 l15 -17"/></svg>
      <h4>${leftovers ? `Leftovers of “${esc(site.name)}” deleted from the VMS` : `VMS records of “${esc(site.name)}” deleted`}</h4>
      <p>${st.total ? `${st.deleted - 1} object${st.deleted - 1 === 1 ? "" : "s"} deleted from <code>${esc(st.scan.vms)}</code>.` : "Nothing was on the VMS."}</p>
      <p class="small muted">The site is kept with its configuration, <code>tenants.yaml</code> and test results, and marked <b>VMS record deleted</b>.
        Every step is recorded in <a href="#" data-a="log">🗒 Delete log</a>.</p></div>`;
    acts.innerHTML = `<button class="btn primary" data-a="close">Close</button>`;
    acts.onclick = () => close();
    body.querySelector('[data-a="log"]').onclick = (e) => { e.preventDefault(); close(); showDeleteLog(); };
    acts.querySelector("button").focus();
  }

  step1();
}

/* ---- ✓ Verify on VMS: a modal that shows each check as it runs (streamed from /api/tenants/verify/stream):
   connect to the VMS → GET every object onboarding created → result saved to tenants.yaml. ---- */
function verifyModal(key, tname) {
  const st = { running: false, ok: null };
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop ds-backdrop";
  wrap.innerHTML = `<div class="modal ds-modal vf-modal" role="dialog" aria-modal="true" aria-labelledby="vfTitle">
    <div class="ds-head"><h3 id="vfTitle">✓ Verify tenant “${esc(tname)}” on VMS</h3>
      <button class="btn ghost sm ds-x" title="Close (Esc)" aria-label="Close">✕</button></div>
    <div class="ds-body" aria-live="polite"></div>
    <div class="btn-row modal-actions ds-actions"></div></div>`;
  const body = wrap.querySelector(".ds-body"), acts = wrap.querySelector(".ds-actions");
  const close = () => {
    if (st.running) return;
    wrap.remove(); document.removeEventListener("keydown", onKey);
    loadTenants();
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) close(); });
  wrap.querySelector(".ds-x").onclick = close;
  document.addEventListener("keydown", onKey);
  document.body.appendChild(wrap);
  const pace = dsReduced() ? 0 : 280;

  async function run() {
    st.running = true; st.ok = null;
    wrap.querySelector(".ds-x").hidden = true;
    body.innerHTML = `<div class="ds-progress"><div class="ds-bar"><i style="width:0%"></i></div>
        <div class="ds-now small"><span class="ds-spin sm"></span> <span class="ds-nowtxt">Starting…</span></div></div>
      <div class="ds-scroll"><ol class="ds-timeline vf-list"></ol></div>`;
    acts.innerHTML = `<span class="small muted">Checking - read-only, nothing is changed on the VMS.</span>`;
    const bar = body.querySelector(".ds-bar i"), now = body.querySelector(".ds-nowtxt"), list = body.querySelector(".vf-list");
    let total = 1, doneN = 0, result = null, vms = "";
    const prog = () => { bar.style.width = `${Math.round((doneN / total) * 100)}%`; };
    const rowEl = (id) => list.querySelector(`[data-row="${CSS.escape(id)}"]`);
    const setRow = (el, cls, txt, sub) => {
      if (!el) return;
      el.classList.remove("pending", "active", "done", "failed"); el.classList.add(cls);
      if (txt != null) el.querySelector(".ds-state").textContent = txt;
      if (sub != null) el.querySelector(".vf-sub").innerHTML = sub;
      if (cls === "active") el.scrollIntoView({ block: "nearest", behavior: dsReduced() ? "auto" : "smooth" });
    };
    const addRow = (id, title, sub, i) => list.insertAdjacentHTML("beforeend",
      `<li class="ds-row pending" data-row="${esc(id)}" style="--i:${i}"><span class="ds-dot" aria-hidden="true"></span>
        <div class="ds-rmain"><div class="ds-rhead"><b>${title}</b><span class="ds-state small muted">waiting</span></div>
        <div class="small muted vf-sub">${sub}</div></div></li>`);
    const fmtFound = (f) => !f ? "" : Object.entries(f).filter(([k]) => k !== "username" || !f.name)
      .map(([k, v]) => k === "id" ? `id ${esc(v)}` : k === "name" || k === "username" ? `“${esc(v)}”` : esc(v)).join(" · ");
    const onEvent = async (ev) => {
      if (ev.t === "start") {
        vms = ev.vms; total = ev.objects.length + 1;
        addRow("connect", "Connect to the VMS", `<code>${esc(ev.vms)}</code>`, 0);
        ev.objects.forEach((o, i) => addRow("o|" + o.step, esc(o.name), `GET <code>${esc(o.endpoint)}</code>`, i + 1));
        addRow("save", "Save the result", "to this site's tenants.yaml", total);
        if (!ev.objects.length) now.textContent = "This tenant has no recorded objects to check.";
        await dsSleep(pace);
      } else if (ev.t === "connect") {
        const el = rowEl("connect");
        if (ev.s === "checking") { setRow(el, "active", "connecting…"); now.textContent = `Connecting to ${vms}…`; }
        else {
          setRow(el, ev.s === "ok" ? "done" : "failed", ev.s === "ok" ? `connected · ${ev.ms} ms` : "can't connect",
            ev.s === "ok" ? `<code>${esc(vms)}</code>${ev.detail ? " · " + esc(ev.detail) : ""}` : `<span class="err">${esc(ev.detail)}</span>`);
          if (ev.s === "ok") { doneN++; prog(); }
          await dsSleep(pace);
        }
      } else if (ev.t === "check") {
        const el = rowEl("o|" + ev.step);
        const name = el?.querySelector(".ds-rhead b").textContent || ev.step;
        if (ev.s === "checking") { setRow(el, "active", "checking…"); now.textContent = `Checking ${name}…`; }
        else {
          const good = ev.s === "present";
          setRow(el, good ? "done" : "failed", good ? `present · ${ev.ms} ms` : ev.s === "missing" ? "missing on VMS" : ev.s,
            good && ev.found ? `${el.querySelector(".vf-sub").innerHTML} → ${fmtFound(ev.found)}` : null);
          doneN++; prog();
          await dsSleep(pace);
        }
      } else if (ev.t === "done") {
        result = ev;
        if (!ev.connect_failed) { const el = rowEl("save"); setRow(el, "active", "saving…"); await dsSleep(pace / 2); setRow(el, "done", "saved"); }
      }
    };
    let err = null;
    try { await streamNdjson("/api/tenants/verify/stream", { key }, onEvent); }
    catch (e) { err = e.message; }
    st.running = false;
    wrap.querySelector(".ds-x").hidden = false;
    bar.style.width = "100%";
    const nowBox = body.querySelector(".ds-now");
    list.querySelectorAll(".ds-row.pending").forEach((r) => { r.classList.add("vf-skip"); r.querySelector(".ds-state").textContent = "not checked"; });
    if (err || !result) {
      nowBox.innerHTML = `<span class="ds-xmark">✕</span> <b>Couldn't verify.</b>`;
      body.querySelector(".ds-progress").insertAdjacentHTML("afterend", `<div class="err ds-err">${esc(err || "The check ended early.")}</div>`);
      bar.classList.add("bad");
    } else if (result.connect_failed) {
      nowBox.innerHTML = `<span class="ds-xmark">✕</span> <b>Can't reach the VMS.</b> <span class="muted">Nothing was checked - the last result is kept.</span>`;
      bar.classList.add("bad");
    } else if (result.ok) {
      nowBox.innerHTML = `<svg class="ds-check vf-check" viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="24"/><path d="M15 27 l8 8 l15 -17"/></svg>
        <b>All ${result.total} object${result.total === 1 ? "" : "s"} present on the VMS.</b>`;
      bar.classList.add("good");
    } else {
      const miss = result.total - result.present;
      nowBox.innerHTML = `<span class="ds-xmark">✕</span> <b>${miss} of ${result.total} object${result.total === 1 ? "" : "s"} not found or not readable.</b>
        <span class="muted">Red rows show what failed.</span>`;
      bar.classList.add("bad");
      list.querySelector(".ds-row.failed")?.scrollIntoView({ block: "nearest" });
    }
    acts.innerHTML = `<button class="btn ghost" data-a="again">↻ Verify again</button><button class="btn primary" data-a="close">Close</button>`;
    acts.onclick = (e) => {
      const a = e.target.closest("[data-a]")?.dataset.a;
      if (a === "close") close();
      if (a === "again") run();
    };
    acts.querySelector('[data-a="close"]').focus();
  }
  run();
}

/* 🗒 Delete log: every site deletion step, kept in sites/.delete-log.jsonl (outside the deleted folders) */
const DL_TEXT = {
  delete_site_started: (e) => `Delete site started${e.tenants?.length ? ` - tenant ${e.tenants.join(", ")}` : " - no tenant"}${e.force?.length ? " (force)" : ""}`,
  delete_object: (e) => `${e.result === "failed" ? "Failed to delete" : e.result === "skipped" ? "Skipped" : e.result === "deferred" ? "Deferred until after the tenant:" : "Deleted"} ${dsLow(DS_ORDER.find(([k]) => k === e.kind)?.[1] || e.kind)} “${e.name}”${e.msg ? ` - ${e.msg}` : ""}`,
  force_delete_tenant_requested: (e) => `Force delete of tenant “${e.tenant}” confirmed by typing “${e.typed}”`,
  force_delete_tenant: (e) => `${e.result === "failed" ? "Force delete FAILED for" : "FORCE DELETED"} tenant “${e.name}” (id ${e.id})${e.msg ? ` - ${e.msg}` : ""}`,
  tenant_not_on_vms: (e) => `Tenant “${e.tenant}” not on the VMS - nothing to delete there`,
  delete_site_stopped: (e) => `Stopped: ${e.msg}`,
  delete_site_done: (e) => e.kept_local ? "VMS records deleted - site kept with its test results, marked “VMS record deleted”" : `Site deleted - folder moved to ${dsTrash(e.moved_to)}`,
  delete_site_local_only: (e) => `Local records only deleted - folder moved to ${dsTrash(e.moved_to)}${e.tenants_left_on_vms?.length ? `; left on the VMS: ${e.tenants_left_on_vms.join(", ")}` : ""}`,
};
async function showDeleteLog() {
  const d = await api("GET", "/api/sites/delete-log").catch((e) => { toast(e.message); return null; });
  if (!d) return;
  const cls = (e) => (/force_delete_tenant$/.test(e.action) ? "dl-force" : e.result === "failed" || e.action === "delete_site_stopped" ? "dl-fail"
    : e.action === "delete_site_done" || e.action === "delete_site_local_only" ? "dl-done" : "");
  await uiConfirm({ title: "🗒 Delete log", ok: "Close", cancel: "", wide: true,
    html: `<div class="small muted">Every test-site deletion, newest first. Kept in <code>${esc(d.path)}</code>.</div>
      ${d.entries.length ? `<div class="dl-wrap"><table class="dl"><thead><tr><th>Time</th><th>Site</th><th>What happened</th></tr></thead><tbody>
        ${d.entries.map((e) => `<tr class="${cls(e)}"><td class="small mono">${esc(fmtDate(e.time))}</td><td>${esc(e.site || "")}
          <div class="small muted mono">${esc(e.vms || "")}</div></td><td>${esc((DL_TEXT[e.action] || ((x) => x.action))(e))}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">No sites deleted yet.</div>`}` });
}

async function afterSiteDeleted(site, isCur, st) {
  if (isCur) { loadTenants().catch(() => {}); loadConfig().catch(() => {}); }   // the open site stays open
  await renderSites();
  $("#siteListMsg").innerHTML = st.ok
    ? `<div class="okmsg">✓ VMS records of “${esc(site.name)}” deleted. The site is kept with its test results and moved to the <a href="#" data-sitetab="vmsdel">VMS Record Deleted Sites</a> tab.</div>`
    : "";
}

/* Header "⇠ All sites": close the open site and go back to the site list */
$("#siteBack").onclick = (e) => busy(e.target, async () => {
  if (consoles.size) {
    const ok = await uiConfirm({
      title: "Close this test site?",
      html: `<p>${consoles.size} SSH console${consoles.size > 1 ? "s are" : " is"} open. Closing the site disconnects ${consoles.size > 1 ? "them" : "it"}.</p>
        <p class="muted">Nothing is deleted. You can open the site again from the list.</p>`,
      ok: "Close site",
    });
    if (!ok) return;
  }
  await api("POST", "/api/sites/close");
  resetUi();
  $("#siteName").textContent = "no site";
  $("#cfgPath").textContent = "choose a test site";
  $("#warnings").hidden = true;
  showSites();
});

async function startApp() {
  await loadConfig().catch((e) => { $("#warnings").hidden = false; $("#warnings").textContent = e.message; });
  loadResults();
  const restored = await restoreConsoles();
  // Open consoles survive a reload: go back to them. Else the Tenants tab when the site has tenants, else onboarding.
  const d = await loadTenants();
  const last = store.get("tab");
  showTab(restored && last && $(`.tab[data-tab="${last}"]`) ? last : d.tenants.some((t) => !tGone(t)) ? "t4" : "t1");
}

/* ---------------- version (top bar) ---------------- */
async function showVersion() {
  const el = $("#appVer");
  try {
    const v = await (await fetch("/api/version", { cache: "no-store" })).json();
    if (!v.running) { el.hidden = true; return; }
    el.textContent = `v${v.running}`;
    const stale = v.on_disk && v.on_disk !== v.running;
    el.classList.toggle("stale", stale);
    el.title = stale ? `Running v${v.running}. v${v.on_disk} is installed - click to restart the app and load it.`
                     : `Running version ${v.running}`;
    if (stale) el.textContent += ` → v${v.on_disk} (restart)`;
    el.hidden = false;
  } catch { el.hidden = true; }
}

/* ---------------- 🔄 Restart app: confirm → restart the server → count down 5 s → reload the page ---------------- */
async function restartModal() {
  let info;
  try { info = await (await fetch("/api/restart", { cache: "no-store" })).json(); }
  catch (e) { return toast(`Can't reach the app: ${e.message}`); }
  if (!Array.isArray(info?.blockers)) return toast("This running copy of the app is older than the Restart button - restart it once from the terminal (stop, then run again).");
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop ds-backdrop";
  wrap.innerHTML = `<div class="modal ds-modal rs-modal" role="dialog" aria-modal="true" aria-labelledby="rsTitle">
    <div class="ds-head"><h3 id="rsTitle">🔄 Restart the app</h3>
      <button class="btn ghost sm ds-x" title="Close (Esc)" aria-label="Close">✕</button></div>
    <div class="ds-body" aria-live="polite"></div>
    <div class="btn-row modal-actions ds-actions"></div></div>`;
  const body = wrap.querySelector(".ds-body"), acts = wrap.querySelector(".ds-actions");
  let locked = false;
  const close = () => { if (locked) return; wrap.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) close(); });
  wrap.querySelector(".ds-x").onclick = close;
  document.addEventListener("keydown", onKey);
  document.body.appendChild(wrap);

  const open = (info.consoles || []).filter((c) => c.status === "connected");
  const newer = info.on_disk && info.on_disk !== info.version;
  body.innerHTML = `
    <p>The app stops and starts again${newer ? `, loading <b>v${esc(info.on_disk)}</b> (now running v${esc(info.version)})` : ` (v${esc(info.version)})`},
      then this page reloads by itself.</p>
    <ul class="rs-list small">
      <li>${info.site ? `Test site <b>${esc(info.site)}</b> opens again.` : "No test site is open."}</li>
      <li>${open.length ? `<b class="warnc">${open.length} SSH console${open.length === 1 ? "" : "s"} will be disconnected:</b> ${open.map((c) => esc(c.name)).join(", ")} - reconnect them after the restart.`
        : "No SSH consoles are connected."}</li>
      <li>Saved settings, tenants and test results are not affected.</li>
    </ul>
    ${info.blockers.length ? `<div class="err ds-err"><b>Can't restart now:</b> ${info.blockers.map(esc).join("; ")}. Wait for it to finish.</div>` : ""}`;
  acts.innerHTML = `<button class="btn ghost" data-a="cancel">Cancel</button>
    <button class="btn primary" data-a="go" ${info.blockers.length ? "disabled" : ""}>🔄 Restart now</button>`;
  acts.onclick = (e) => {
    const a = e.target.closest("[data-a]")?.dataset.a;
    if (a === "cancel") close();
    if (a === "go" && !e.target.disabled) go();
  };
  acts.querySelector('[data-a="go"]').focus();

  async function go() {
    locked = true;
    wrap.querySelector(".ds-x").hidden = true;
    acts.innerHTML = "";
    try { await api("POST", "/api/restart", {}); }
    catch (e) {
      locked = false; wrap.querySelector(".ds-x").hidden = false;
      body.insertAdjacentHTML("beforeend", `<div class="err ds-err">${esc(e.message)}</div>`);
      acts.innerHTML = `<button class="btn primary" data-a="cancel">Close</button>`;
      return;
    }
    const N = 5, C = 2 * Math.PI * 44;
    body.innerHTML = `<div class="rs-count">
        <svg viewBox="0 0 100 100" aria-hidden="true"><circle class="rs-track" cx="50" cy="50" r="44"/>
          <circle class="rs-arc" cx="50" cy="50" r="44" style="stroke-dasharray:${C};stroke-dashoffset:0"/></svg>
        <span class="rs-num">${N}</span></div>
      <p class="rs-msg"><b>Restarting the app…</b><br><span class="small muted">This page reloads in <span class="rs-sec">${N}</span> s.</span></p>`;
    const num = body.querySelector(".rs-num"), sec = body.querySelector(".rs-sec"), arc = body.querySelector(".rs-arc"), msg = body.querySelector(".rs-msg");
    arc.getBoundingClientRect();   // commit the full ring before the transition starts
    arc.style.transition = `stroke-dashoffset ${N}s linear`; arc.style.strokeDashoffset = C;
    for (let n = N - 1; n >= 0; n--) {
      await dsSleep(1000);
      num.textContent = n; sec.textContent = n;
      num.classList.remove("tick"); void num.offsetWidth; num.classList.add("tick");
    }
    // wait until the new server answers (normally it already does)
    msg.innerHTML = `<b>Waiting for the app to come back…</b>`;
    const until = Date.now() + 30000;
    while (Date.now() < until) {
      try { const r = await fetch("/api/version", { cache: "no-store" }); if (r.ok) { msg.innerHTML = "<b>Reloading…</b>"; location.reload(); return; } } catch {}
      await dsSleep(700);
    }
    msg.innerHTML = `<span class="err"><b>The app did not come back within 30 s.</b></span><br>
      <span class="small muted">Start it again with run.sh / run.command / run.bat, then reload this page.</span>`;
    locked = false; wrap.querySelector(".ds-x").hidden = false;
    acts.innerHTML = `<button class="btn primary" data-a="reload">↻ Reload page</button>`;
    acts.onclick = () => location.reload();
  }
}
$("#restartBtn").onclick = restartModal;
$("#appVer").addEventListener("click", () => { if ($("#appVer").classList.contains("stale")) restartModal(); });

/* ---------------- start ---------------- */
applyTheme();
showVersion();
fetch("/api/sites").then((r) => r.json()).then((d) => (d.current ? startApp() : showSites()));
