"use strict";
// Asistente BIM Vialidad — cliente. Todo el análisis IFC corre en server.py (ifcopenshell).
const DISC = ["GEO", "PAV", "DRE", "EST", "SEG", "SER"];
const DISC_NAMES = {
  GEO: "Geometría y Mov. Tierras", PAV: "Pavimentos y Calzada", DRE: "Drenaje y Obras de Arte",
  EST: "Puentes y Estructuras", SEG: "Seguridad Vial y Señaliz.", SER: "Servicios Afectados e ITS"
};
const SEV_COLOR = { "Crítica": "#e11d48", "Mayor": "#f59e0b", "Moderada": "#6366f1" };
const PAGE = 100;

const S = { files: [], res: null, pair: null, page: 0, charts: {}, axisCsvPath: null };
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => Number(n).toLocaleString("es-CL");

function toast(msg, bad) {
  $("toastMsg").textContent = msg;
  $("toastDot").className = "w-2.5 h-2.5 rounded-full " + (bad ? "bg-rose-400" : "bg-emerald-400");
  $("toast").classList.remove("hidden");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => $("toast").classList.add("hidden"), 3500);
}

async function api(path, opt) {
  const r = await fetch(path, opt);
  if (!r.ok) {
    let m = r.statusText;
    try { m = (await r.json()).error || m; } catch (e) { /* ignore */ }
    throw new Error(m);
  }
  return r.json();
}

// ------------------------------------------------------------------ pestañas
function switchTab(id) {
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.add("hidden"));
  $(id).classList.remove("hidden");
  document.querySelectorAll(".nav-tab").forEach((b) => {
    const on = b.dataset.tab === id;
    b.className = "nav-tab px-4 py-2.5 text-sm border-b-2 whitespace-nowrap " +
      (on ? "font-semibold border-amber-400 text-white" : "font-medium border-transparent text-slate-400 hover:text-slate-200");
  });
  if (S.res && id === "tabDash") { renderCharts(); drawPlan(); }
  if (S.res && id === "tabAudit") renderAuditChart();
  if (S.res && id === "tabReport" && !$("reportDoc").innerHTML) buildReport();
}

// ------------------------------------------------------------------ archivos
async function loadFiles(keep) {
  S.files = await api("/api/files");
  S.files.forEach((f) => { f.checked = keep ? keep.has(f.path) : f.origin === "referencia"; });
  const box = $("fileList");
  if (!S.files.length) {
    box.innerHTML = '<div class="text-xs text-slate-400 py-6 text-center">No hay .IFC en _Referencias.IFC. Sube archivos con el botón.</div>';
  } else {
    box.innerHTML = S.files.map((f, i) => `
      <div class="border border-slate-200 rounded-lg p-2.5 flex flex-wrap items-center justify-between gap-2 text-xs ${f.checked ? "bg-indigo-50/50" : "bg-white"}">
        <label class="flex items-center gap-2 min-w-0 flex-1">
          <input type="checkbox" data-i="${i}" class="fchk" ${f.checked ? "checked" : ""}>
          <span class="min-w-0"><span class="block font-semibold text-slate-900 truncate">${esc(f.name)}</span>
          <span class="text-[11px] text-slate-500">${f.mb} MB • ${esc(f.origin)} • especialidad por ${esc(f.src)}</span></span>
        </label>
        <select data-i="${i}" class="fdisc border border-slate-300 rounded-md px-2 py-1 font-semibold bg-slate-50">
          ${DISC.map((d) => `<option value="${d}" ${f.discipline === d ? "selected" : ""}>${d} — ${DISC_NAMES[d]}</option>`).join("")}
        </select>
      </div>`).join("");
    box.querySelectorAll(".fchk").forEach((c) => c.addEventListener("change", (e) => { S.files[+e.target.dataset.i].checked = e.target.checked; updateRunBtn(); }));
    box.querySelectorAll(".fdisc").forEach((c) => c.addEventListener("change", (e) => { S.files[+e.target.dataset.i].discipline = e.target.value; }));
  }
  updateRunBtn();
}

function updateRunBtn() {
  const n = S.files.filter((f) => f.checked).length;
  $("btnRun").disabled = n < 2;
  $("btnRun").textContent = n < 2 ? "Marca 2 o más modelos" : `Ejecutar cruce (${n} modelos)`;
}

async function uploadFiles(list) {
  const st = $("uploadStatus"), added = [];
  st.classList.remove("hidden");
  for (const f of list) {
    st.textContent = `Subiendo ${f.name} (${(f.size / 1e6).toFixed(0)} MB)…`;
    const r = await fetch("/api/upload?name=" + encodeURIComponent(f.name), { method: "POST", body: f });
    if (!r.ok) { toast("Error subiendo " + f.name, true); continue; }
    added.push((await r.json()).path);
  }
  st.classList.add("hidden");
  const keep = new Set(S.files.filter((f) => f.checked).map((f) => f.path));
  added.forEach((p) => keep.add(p));
  await loadFiles(keep);
  toast("Archivos subidos");
}

// ------------------------------------------------------------------ ejecución
async function runCross() {
  const chosen = S.files.filter((f) => f.checked);
  if (chosen.length < 2) return;
  $("btnRun").disabled = true;
  $("progressBox").classList.remove("hidden");
  $("progressBar").style.width = "2%";
  $("progressLog").textContent = "Enviando…";
  let axis = null;
  const af = $("axisInput").files[0];
  if (af) {
    const r = await fetch("/api/upload?name=" + encodeURIComponent(af.name), { method: "POST", body: af });
    axis = (await r.json()).path;
  }
  try {
    const { job } = await api("/api/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        files: chosen.map((f) => ({ path: f.path, discipline: f.discipline })),
        name: $("projName").value, intra: $("optIntra").checked,
        spacing: $("spacing").value, axis_csv: axis
      })
    });
    for (;;) {
      await new Promise((r) => setTimeout(r, 1200));
      const j = await api("/api/job/" + job);
      $("progressBar").style.width = Math.max(2, Math.round((j.pct || 0) * 100)) + "%";
      $("progressLog").textContent = (j.log || []).slice(-12).join("\n");
      if (j.status === "error") throw new Error(j.error);
      if (j.status === "done") break;
    }
    const full = await api("/api/job/" + job + "?full=1");
    S.res = full.result;
    S.pair = null;
    S.page = 0;
    onResult();
    toast(`Cruce listo: ${S.res.issues.length} interferencias`);
  } catch (e) {
    toast("Error: " + e.message, true);
    $("progressLog").textContent += "\nERROR: " + e.message;
  } finally {
    updateRunBtn();
  }
}

function onResult() {
  ["btnXlsx", "btnBcf", "btnCsv", "btnPdf"].forEach((b) => ($(b).disabled = false));
  $("emptyReport").classList.add("hidden"); $("reportBody").classList.remove("hidden"); $("reportDoc").innerHTML = "";
  P.fitted = false; P.sel = null; P.layers.sev = { "Crítica": true, "Mayor": true, "Moderada": true };
  $("planSel").textContent = "Selecciona un marcador para ver su ficha.";
  $("emptyDash").classList.add("hidden"); $("dashBody").classList.remove("hidden");
  $("emptyAudit").classList.add("hidden"); $("auditBody").classList.remove("hidden");
  fillFilters();
  renderKpis(); renderMatrix(); renderIssues(); renderAudit(); renderPlanLayers(); renderHotZones();
  switchTab("tabDash");
}

// ------------------------------------------------------------------ KPIs
function counts() {
  const is = S.res.issues;
  const act = is.filter((i) => i.status !== "Resuelta").length;
  return { total: is.length, act, res: is.length - act, crit: is.filter((i) => i.severity === "Crítica" && i.status !== "Resuelta").length };
}

function renderKpis() {
  const r = S.res, c = counts();
  const rate = c.total ? Math.round((100 * c.res) / c.total) : 0;
  let top = ["—", 0];
  for (let i = 0; i < 6; i++) for (let j = i; j < 6; j++) if (r.matrix[i][j] > top[1]) top = [`${DISC[i]} × ${DISC[j]}`, r.matrix[i][j]];
  const gv = r.georef;
  const card = (t, v, s, cls = "") => `<div class="bg-white rounded-2xl border border-slate-200 shadow-sm p-3.5"><div class="text-[11px] uppercase font-bold text-slate-400">${t}</div><div class="text-2xl font-bold ${cls}">${v}</div><div class="text-[11px] text-slate-500">${s}</div></div>`;
  $("kpiRow").innerHTML =
    card("Interferencias activas / total", `${c.act} / ${c.total}`, `${rate}% resuelto`) +
    card("Críticas activas", c.crit, "choques duros o regla crítica", c.crit ? "text-rose-600" : "text-emerald-600") +
    card("Elementos auditados", fmt(r.totalElements), `${r.models.length} modelos • ${fmt(r.pairs_tested)} pares evaluados`) +
    card("Completitud media", r.kpi.avgCompleteness + "%", `Proxy medio ${r.kpi.avgProxy}%`) +
    card("Cruce principal", top[0], `${top[1]} interferencias`) ;
  const w = [];
  if (r.axis.source.startsWith("estimado")) w.push("<b>Pk estimado:</b> los IFC no traen IfcAlignment y no se cargó un eje. El Pk es la distancia a lo largo de un eje reconstruido desde los centroides; carga un CSV de eje para el Pk oficial.");
  gv.warnings.forEach((x) => w.push(esc(x)));
  w.push(`<b>Georreferenciación:</b> ${gv.crs_valid_models} de ${gv.models} modelos con CRS válido (IfcProjectedCRS). No se afirma un datum específico.`);
  const wb = $("warnBox");
  wb.innerHTML = w.join("<br>");
  wb.classList.remove("hidden");
}

// ------------------------------------------------------------------ matriz
function renderMatrix() {
  const r = S.res;
  $("mxHead").innerHTML = '<th class="p-1.5 text-left text-slate-400">Esp.</th>' + DISC.map((d) => `<th class="p-1.5 text-center" title="${DISC_NAMES[d]}">${d}</th>`).join("");
  $("mxBody").innerHTML = DISC.map((rd, i) => `<tr><td class="p-1.5 font-bold text-slate-700 whitespace-nowrap" title="${DISC_NAMES[rd]}">${rd}</td>` +
    DISC.map((cd, j) => {
      const v = r.matrix[i][j];
      const sel = S.pair && ((S.pair[0] === rd && S.pair[1] === cd) || (S.pair[0] === cd && S.pair[1] === rd));
      let cls = "bg-slate-50 text-slate-400 border-slate-200";
      if (v >= 16) cls = "bg-rose-100 text-rose-900 border-rose-300 font-bold";
      else if (v >= 6) cls = "bg-amber-100 text-amber-900 border-amber-300 font-semibold";
      else if (v > 0) cls = "bg-emerald-50 text-emerald-800 border-emerald-200";
      return `<td class="p-1 text-center"><button data-r="${rd}" data-c="${cd}" class="mx w-full py-1.5 rounded-lg border ${cls} ${sel ? "ring-2 ring-indigo-600" : ""} ${v ? "matrix-cell cursor-pointer" : "cursor-default"}">${v}</button></td>`;
    }).join("") + "</tr>").join("");
  document.querySelectorAll(".mx").forEach((b) => b.addEventListener("click", () => {
    if (b.textContent === "0") return;
    S.pair = [b.dataset.r, b.dataset.c]; S.page = 0;
    $("mxClear").classList.remove("hidden");
    $("mxStatus").textContent = `Filtro: ${b.dataset.r} × ${b.dataset.c}`;
    renderMatrix(); renderIssues(); switchTab("tabIssues");
  }));
}

// ------------------------------------------------------------------ interferencias
function fillFilters() {
  $("fSpec").innerHTML = '<option value="ALL">Toda especialidad</option>' + DISC.map((d) => `<option value="${d}">${d} — ${DISC_NAMES[d]}</option>`).join("");
  const lv = [...new Set(S.res.issues.map((i) => i.level))].sort((a, b) => parseInt(a.replace(/\D+/, "")) - parseInt(b.replace(/\D+/, "")));
  $("fLevel").innerHTML = '<option value="ALL">Todo el trazado</option>' + lv.map((l) => `<option>${esc(l)}</option>`).join("");
}

function filtered() {
  const f = { sp: $("fSpec").value, lv: $("fLevel").value, sv: $("fSev").value, kd: $("fKind").value, st: $("fStatus").value, q: $("fSearch").value.trim().toLowerCase() };
  return S.res.issues.filter((i) => {
    if (S.pair && !(i.specs.includes(S.pair[0]) && i.specs.includes(S.pair[1]) && (S.pair[0] !== S.pair[1] || (i.specs[0] === i.specs[1])))) return false;
    if (f.sp !== "ALL" && !i.specs.includes(f.sp)) return false;
    if (f.lv !== "ALL" && i.level !== f.lv) return false;
    if (f.sv !== "ALL" && i.severity !== f.sv) return false;
    if (f.kd !== "ALL" && i.kind !== f.kd) return false;
    if (f.st !== "ALL" && i.status !== f.st) return false;
    if (f.q && !`${i.id} ${i.elem} ${i.nameA} ${i.nameB} ${i.guidA} ${i.guidB} ${i.desc}`.toLowerCase().includes(f.q)) return false;
    return true;
  });
}

function renderIssues() {
  if (!S.res) return;
  const all = filtered();
  const pages = Math.max(1, Math.ceil(all.length / PAGE));
  S.page = Math.min(S.page, pages - 1);
  const rows = all.slice(S.page * PAGE, (S.page + 1) * PAGE);
  $("issuesCount").textContent = `Mostrando ${rows.length} de ${all.length} filtradas (${S.res.issues.length} en total)` + (S.pair ? ` • matriz ${S.pair[0]} × ${S.pair[1]}` : "");
  $("pgInfo").textContent = `Página ${S.page + 1} de ${pages}`;
  $("issuesBody").innerHTML = rows.length ? rows.map((i) => {
    const sev = i.severity === "Crítica" ? "bg-rose-50 text-rose-700 border-rose-200" : i.severity === "Mayor" ? "bg-amber-50 text-amber-800 border-amber-200" : "bg-slate-100 text-slate-700 border-slate-200";
    const st = i.status === "Resuelta" ? "bg-emerald-50 text-emerald-700 border-emerald-300" : i.status === "En Proceso" ? "bg-amber-50 text-amber-800 border-amber-300" : "bg-rose-50 text-rose-700 border-rose-300";
    const rule = i.rule.verificado ? "" : ' <span class="text-amber-600" title="Regla de ejemplo, no verificada">⚠</span>';
    return `<tr class="hover:bg-slate-50 align-top">
      <td class="py-2.5 px-3"><div class="font-bold text-slate-900">${esc(i.id)}</div><div class="text-[11px] text-slate-400 font-mono">${esc(i.elem)}</div></td>
      <td class="px-3 whitespace-nowrap"><div class="font-semibold">${esc(i.pk_text)}</div><div class="text-[11px] text-slate-400">${i.offset == null ? "" : "a " + i.offset.toFixed(0) + " m del eje"}</div></td>
      <td class="px-3 whitespace-nowrap"><span class="px-2 py-0.5 rounded-md bg-indigo-50 text-indigo-700 font-semibold">${esc(i.pair)}</span><div class="text-[11px] text-slate-500 mt-1">${i.kind === "choque" ? "choque" : "holgura " + i.distance.toFixed(2) + " m"}</div></td>
      <td class="px-3 text-slate-600 max-w-md"><div>${esc(i.desc)}${rule}</div><button data-id="${esc(i.id)}" class="v3d mt-1 text-[11px] font-bold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 px-2 py-0.5 rounded border border-indigo-200">Ver en 3D →</button></td>
      <td class="px-3"><span class="px-2 py-0.5 rounded-full border font-semibold ${sev}">${esc(i.severity)}</span></td>
      <td class="px-3 text-slate-700">${esc(i.owner)}</td>
      <td class="px-3"><select data-key="${esc(i.key)}" class="stsel rounded-lg px-2 py-1 border font-semibold ${st}">
        ${["Activa", "En Proceso", "Resuelta"].map((o) => `<option ${i.status === o ? "selected" : ""}>${o}</option>`).join("")}</select></td></tr>`;
  }).join("") : '<tr><td colspan="7" class="py-8 text-center text-slate-400">Sin interferencias para los filtros elegidos.</td></tr>';
  document.querySelectorAll(".v3d").forEach((b) => b.addEventListener("click", () => open3d(b.dataset.id)));
  document.querySelectorAll(".stsel").forEach((s) => s.addEventListener("change", async (e) => {
    const it = S.res.issues.find((x) => x.key === e.target.dataset.key);
    await setStatus(it, e.target.value);
    $("reportDoc").innerHTML = "";
    renderKpis(); renderIssues(); renderCharts(); drawPlan(); renderHotZones();
  }));
}

// ------------------------------------------------------------------ gráficos
function chart(id, cfg) {
  if (S.charts[id]) S.charts[id].destroy();
  S.charts[id] = new Chart($(id).getContext("2d"), cfg);
}
const baseOpt = { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } } } };

function renderCharts() {
  if (!S.res || $("tabDash").classList.contains("hidden")) return;
  const is = S.res.issues, h = S.res.history || [];
  chart("chTrend", { type: "line", data: { labels: h.map((x) => x.fecha), datasets: [
    { label: "Activas", data: h.map((x) => x.activas), borderColor: "#e11d48", backgroundColor: "rgba(225,29,72,.1)", fill: true, tension: .3 },
    { label: "Resueltas", data: h.map((x) => x.resueltas), borderColor: "#10b981", backgroundColor: "rgba(16,185,129,.1)", fill: true, tension: .3 }] },
    options: { ...baseOpt, scales: { y: { beginAtZero: true } } } });
  const sevs = ["Crítica", "Mayor", "Moderada"];
  chart("chSev", { type: "bar", data: { labels: DISC, datasets: sevs.map((s) => ({
    label: s, backgroundColor: SEV_COLOR[s],
    data: DISC.map((d) => is.filter((i) => i.severity === s && i.specs.includes(d)).length) })) },
    options: { ...baseOpt, scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } } });
  const lv = Object.entries(S.res.kpi.tramos).sort((a, b) => parseInt(a[0].replace(/\D+/, "")) - parseInt(b[0].replace(/\D+/, "")));
  chart("chLevel", { type: "bar", data: { labels: lv.map((x) => x[0]), datasets: [{ label: "Interferencias", data: lv.map((x) => x[1]), backgroundColor: "#6366f1", borderRadius: 5 }] },
    options: { ...baseOpt, indexAxis: "y", plugins: { legend: { display: false } } } });
  const ow = {};
  is.forEach((i) => { (ow[i.owner] ||= { p: 0, r: 0 })[i.status === "Resuelta" ? "r" : "p"]++; });
  const ol = Object.keys(ow);
  chart("chOwner", { type: "bar", data: { labels: ol, datasets: [
    { label: "Pendientes", data: ol.map((o) => ow[o].p), backgroundColor: "#f43f5e" },
    { label: "Resueltas", data: ol.map((o) => ow[o].r), backgroundColor: "#10b981" }] },
    options: { ...baseOpt, scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } } });
}

function renderAuditChart() {
  const m = S.res.models;
  chart("chAudit", { type: "bar", data: { labels: m.map((x) => x.code + " " + x.file.slice(-28)), datasets: [
    { label: "Pset %", data: m.map((x) => x.pset), backgroundColor: "#6366f1" },
    { label: "Geometría %", data: m.map((x) => x.geom_pct), backgroundColor: "#10b981" },
    { label: "Nombre %", data: m.map((x) => x.name_pct), backgroundColor: "#f59e0b" },
    { label: "Proxy %", data: m.map((x) => x.proxy), backgroundColor: "#e11d48" }] },
    options: { ...baseOpt, scales: { y: { beginAtZero: true, max: 100 } } } });
}

// ------------------------------------------------------------------ auditoría
function renderAudit() {
  const r = S.res;
  $("auditRows").innerHTML = r.models.map((m) => {
    const c = m.completitud >= 85 ? "text-emerald-600 font-bold" : "text-amber-600 font-semibold";
    return `<tr><td class="py-2 px-2 font-semibold">${m.code} — ${esc(m.name)}<div class="text-[10px] text-slate-400 font-normal">por ${esc(m.disc_src)}</div></td>
      <td class="font-mono text-[11px] max-w-xs truncate" title="${esc(m.file)}">${esc(m.file)} <span class="px-1 rounded bg-slate-100 font-sans">${esc(m.format)}</span></td>
      <td class="text-right">${fmt(m.elements)}</td><td class="text-right">${m.pset}</td><td class="text-right">${m.geom_pct}</td>
      <td class="text-right">${m.name_pct}</td><td class="text-right">${m.class_pct}</td><td class="text-right">${m.proxy}</td>
      <td class="text-right ${c}">${m.completitud}%</td><td class="px-2"><span class="px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 font-semibold">${esc(m.lod)}</span></td></tr>`;
  }).join("");
  $("geoRows").innerHTML = r.models.map((m) => {
    const g = m.georef || {};
    const bb = m.bbox ? `E ${fmt(Math.round(m.bbox[0][0]))}–${fmt(Math.round(m.bbox[1][0]))} • N ${fmt(Math.round(m.bbox[0][1]))}–${fmt(Math.round(m.bbox[1][1]))}` : "sin geometría";
    const ok = g.crs_valido;
    const ge = Object.entries(m.geom_errors || {}).map(([k, v]) => `${v}× ${esc(k)}`).join("; ");
    return `<div class="border border-slate-200 rounded-lg p-2.5"><div class="font-semibold">${m.code} • ${esc(m.file)}</div>
      <div>CRS: <b class="${ok ? "text-emerald-600" : "text-rose-600"}">${esc(g.crs || "no definido")}</b> ${ok ? "" : "(no válido)"} • MapConversion: ${g.mapconversion ? "sí" : "no"}</div>
      <div class="text-slate-500">Caja en planta: ${bb}</div>${ge ? `<div class="text-amber-700">Geometría no leída: ${ge}</div>` : ""}</div>`;
  }).join("") + `<div class="text-slate-500">Eje para Pk: <b>${esc(r.axis.source)}</b>${r.axis.length ? ` (${fmt(Math.round(r.axis.length))} m)` : ""}</div>`;
}

// ------------------------------------------------------------------ utilidades geométricas 2D
const MODEL_COLORS = ["#6366f1", "#10b981", "#f59e0b", "#0ea5e9", "#a855f7", "#64748b"];
const SEV_RANK = { "Crítica": 0, "Mayor": 1, "Moderada": 2 };

function footPts(fp) {
  if (fp.k === "p") return [[fp.c[0], fp.c[1]]];
  const out = [];
  for (let i = 0; i < fp.p.length; i += 2) out.push([fp.p[i], fp.p[i + 1]]);
  return out;
}
function footBounds(fp) {
  const ps = footPts(fp);
  let x0 = 1e18, y0 = 1e18, x1 = -1e18, y1 = -1e18;
  ps.forEach(([x, y]) => { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); });
  return [x0, y0, x1, y1];
}
function niceLen(m) {
  const p = Math.pow(10, Math.floor(Math.log10(m)));
  const f = m / p;
  return (f >= 5 ? 5 : f >= 2 ? 2 : 1) * p;
}
function fmtLen(m) { return m >= 1000 ? (m / 1000) + " km" : m + " m"; }
function worstSev(list) {
  let w = "Moderada";
  list.forEach((i) => { if (SEV_RANK[i.severity] < SEV_RANK[w]) w = i.severity; });
  return w;
}
function footIndex(mi) {
  const m = S.res.models[mi];
  if (!m._idx) { m._idx = new Map(); (m.foot || []).forEach((f) => m._idx.set(f.s, f)); }
  return m._idx;
}

// ------------------------------------------------------------------ planta interactiva
const P = { cx: 0, cy: 0, k: 1, W: 0, H: 0, drag: null, hits: [], sel: null, hover: null, vis: {}, layers: { axis: true, sev: { "Crítica": true, "Mayor": true, "Moderada": true }, resolved: false, models: [] }, fitted: false };

function planBounds(items) {
  let x0 = 1e18, y0 = 1e18, x1 = -1e18, y1 = -1e18;
  const add = (x, y) => { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); };
  if (items) items.forEach((i) => add(i.point[0], i.point[1]));
  else {
    S.res.models.forEach((m) => { if (m.bbox) { add(m.bbox[0][0], m.bbox[0][1]); add(m.bbox[1][0], m.bbox[1][1]); } });
    S.res.axis.polyline.forEach((p) => add(p[0], p[1]));
  }
  return [x0, y0, x1, y1];
}
function planFit(items) {
  const cv = $("planMap");
  P.W = cv.clientWidth; P.H = cv.clientHeight;
  const [x0, y0, x1, y1] = planBounds(items);
  if (!isFinite(x0)) return;
  const spanX = Math.max(items ? 60 : 1, x1 - x0), spanY = Math.max(items ? 60 : 1, y1 - y0);
  P.cx = (x0 + x1) / 2; P.cy = (y0 + y1) / 2;
  P.k = Math.min((P.W - 80) / spanX, (P.H - 80) / spanY);
  P.fitted = true;
  drawPlan();
}
const PX = (x) => P.W / 2 + (x - P.cx) * P.k;
const PY = (y) => P.H / 2 - (y - P.cy) * P.k;

function renderPlanLayers() {
  const r = S.res;
  P.layers.models = r.models.map(() => true);
  $("plLayers").innerHTML =
    r.models.map((m, i) => `<label class="flex items-center gap-1"><input type="checkbox" class="pl-m" data-i="${i}" checked><span class="inline-block w-3 h-3 rounded-sm" style="background:${MODEL_COLORS[i % 6]}"></span>${esc(m.code)} <span class="text-slate-400 hidden sm:inline">${esc(m.file.slice(-26))}</span></label>`).join("") +
    '<span class="text-slate-300">|</span>' +
    '<label class="flex items-center gap-1"><input type="checkbox" id="plAxis" checked> Eje y Pk</label>' +
    Object.keys(SEV_COLOR).map((s) => `<label class="flex items-center gap-1"><input type="checkbox" class="pl-s" data-s="${s}" checked><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:${SEV_COLOR[s]}"></span>${s}</label>`).join("") +
    '<label class="flex items-center gap-1"><input type="checkbox" id="plRes"> Resueltas</label>';
  document.querySelectorAll(".pl-m").forEach((c) => c.addEventListener("change", () => { P.layers.models[+c.dataset.i] = c.checked; drawPlan(); }));
  document.querySelectorAll(".pl-s").forEach((c) => c.addEventListener("change", () => { P.layers.sev[c.dataset.s] = c.checked; drawPlan(); }));
  $("plAxis").addEventListener("change", (e) => { P.layers.axis = e.target.checked; drawPlan(); });
  $("plRes").addEventListener("change", (e) => { P.layers.resolved = e.target.checked; drawPlan(); });
}

function planIssues() {
  return S.res.issues.filter((i) => P.layers.sev[i.severity] && (P.layers.resolved || i.status !== "Resuelta"));
}

function drawFootCanvas(g, fp, color, alpha, bold) {
  g.globalAlpha = alpha; g.strokeStyle = color; g.fillStyle = color;
  if (fp.k === "p") {
    const w = Math.max(bold ? 5 : 2, fp.e[0] * P.k), h = Math.max(bold ? 5 : 2, fp.e[1] * P.k);
    g.fillRect(PX(fp.c[0]) - w / 2, PY(fp.c[1]) - h / 2, w, h);
  } else {
    g.lineWidth = Math.max(bold ? 3 : 1.2, fp.w * P.k); g.lineCap = "round"; g.lineJoin = "round";
    g.beginPath();
    for (let i = 0; i < fp.p.length; i += 2) (i ? g.lineTo(PX(fp.p[i]), PY(fp.p[i + 1])) : g.moveTo(PX(fp.p[i]), PY(fp.p[i + 1])));
    g.stroke();
  }
  g.globalAlpha = 1;
}

function drawPlan() {
  const cv = $("planMap");
  if (!S.res || !cv || cv.offsetParent === null) return;
  const dpr = window.devicePixelRatio || 1;
  P.W = cv.clientWidth; P.H = cv.clientHeight;
  if (!P.fitted) { planFit(); return; }
  cv.width = P.W * dpr; cv.height = P.H * dpr;
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.fillStyle = "#f8fafc"; g.fillRect(0, 0, P.W, P.H);

  // cuadrícula adaptativa
  const gridM = niceLen(90 / P.k);
  const wx0 = P.cx - P.W / 2 / P.k, wx1 = P.cx + P.W / 2 / P.k, wy0 = P.cy - P.H / 2 / P.k, wy1 = P.cy + P.H / 2 / P.k;
  g.strokeStyle = "#e2e8f0"; g.lineWidth = 1; g.fillStyle = "#94a3b8"; g.font = "10px Segoe UI, sans-serif";
  g.beginPath();
  for (let x = Math.ceil(wx0 / gridM) * gridM; x <= wx1; x += gridM) { g.moveTo(PX(x), 0); g.lineTo(PX(x), P.H); }
  for (let y = Math.ceil(wy0 / gridM) * gridM; y <= wy1; y += gridM) { g.moveTo(0, PY(y)); g.lineTo(P.W, PY(y)); }
  g.stroke();
  for (let x = Math.ceil(wx0 / gridM) * gridM; x <= wx1; x += gridM) g.fillText(Math.round(x).toLocaleString("es-CL"), PX(x) + 2, P.H - 3);
  for (let y = Math.ceil(wy0 / gridM) * gridM; y <= wy1; y += gridM) g.fillText(Math.round(y).toLocaleString("es-CL"), 3, PY(y) - 2);

  // elementos de los modelos
  const sel = P.sel;
  S.res.models.forEach((m, i) => {
    if (!P.layers.models[i]) return;
    const col = MODEL_COLORS[i % 6];
    if (m.bbox && P.k * 1 < 0.02) {  // muy lejos: sólo la caja
      g.strokeStyle = col; g.globalAlpha = .5; g.strokeRect(PX(m.bbox[0][0]), PY(m.bbox[1][1]), (m.bbox[1][0] - m.bbox[0][0]) * P.k, (m.bbox[1][1] - m.bbox[0][1]) * P.k); g.globalAlpha = 1;
    }
    (m.foot || []).forEach((fp) => {
      const [a, b, c, d] = footBounds(fp);
      if (c < wx0 || a > wx1 || d < wy0 || b > wy1) return;
      drawFootCanvas(g, fp, col, 0.8, false);
    });
  });
  if (sel) {
    [[sel.ia, sel.stepA, "#0284c7"], [sel.ib, sel.stepB, "#d97706"]].forEach(([mi, st, col]) => {
      const fp = footIndex(mi).get(st);
      if (fp) drawFootCanvas(g, fp, col, 1, true);
    });
  }

  // eje y estaciones Pk
  if (P.layers.axis && S.res.axis.polyline.length) {
    g.strokeStyle = "#0f172a"; g.lineWidth = 2.2; g.setLineDash(S.res.axis.source.startsWith("estimado") ? [8, 5] : []);
    g.beginPath(); S.res.axis.polyline.forEach((p, i) => (i ? g.lineTo(PX(p[0]), PY(p[1])) : g.moveTo(PX(p[0]), PY(p[1])))); g.stroke(); g.setLineDash([]);
    g.font = "bold 10px Segoe UI, sans-serif"; g.textAlign = "center";
    (S.res.axis.ticks || []).forEach((t) => {
      const x = PX(t.x), y = PY(t.y);
      if (x < -20 || y < -20 || x > P.W + 20 || y > P.H + 20) return;
      const dx = Math.sin(t.ang) * 7, dy = Math.cos(t.ang) * 7;
      g.strokeStyle = "#0f172a"; g.lineWidth = 1.4; g.beginPath(); g.moveTo(x - dx, y - dy); g.lineTo(x + dx, y + dy); g.stroke();
      if (!P._lastLbl || Math.hypot(x - P._lastLbl[0], y - P._lastLbl[1]) > 64) {
        g.fillStyle = "rgba(255,255,255,.85)"; const w = g.measureText(t.txt).width + 6; g.fillRect(x - w / 2, y + 9, w, 13);
        g.fillStyle = "#0f172a"; g.fillText(t.txt.replace("Pk ", ""), x, y + 19); P._lastLbl = [x, y];
      }
    });
    P._lastLbl = null; g.textAlign = "left";
  }

  // marcadores / clústeres
  P.hits = [];
  const items = planIssues();
  const cell = 30, buckets = new Map();
  items.forEach((it) => {
    const x = PX(it.point[0]), y = PY(it.point[1]);
    if (x < -20 || y < -20 || x > P.W + 20 || y > P.H + 20) return;
    const key = Math.floor(x / cell) + "_" + Math.floor(y / cell);
    (buckets.get(key) || buckets.set(key, []).get(key)).push({ it, x, y });
  });
  const zoomedEnough = P.k > 0.35;
  buckets.forEach((arr) => {
    if (arr.length > 1 && !zoomedEnough) {
      const x = arr.reduce((s, a) => s + a.x, 0) / arr.length, y = arr.reduce((s, a) => s + a.y, 0) / arr.length;
      const col = SEV_COLOR[worstSev(arr.map((a) => a.it))], r = 9 + Math.min(10, Math.log2(arr.length) * 2.6);
      g.fillStyle = col + "cc"; g.strokeStyle = "#fff"; g.lineWidth = 2; g.beginPath(); g.arc(x, y, r, 0, 7); g.fill(); g.stroke();
      g.fillStyle = "#fff"; g.font = "bold 11px Segoe UI, sans-serif"; g.textAlign = "center"; g.fillText(arr.length, x, y + 4); g.textAlign = "left";
      P.hits.push({ x, y, r, items: arr.map((a) => a.it) });
    } else {
      arr.forEach(({ it, x, y }) => {
        const col = SEV_COLOR[it.severity], isSel = sel && sel.id === it.id;
        g.fillStyle = col; g.strokeStyle = "#fff"; g.lineWidth = 2;
        if (isSel) { g.strokeStyle = "#0f172a"; g.lineWidth = 2.5; g.beginPath(); g.arc(x, y, 14, 0, 7); g.stroke(); g.strokeStyle = "#fff"; g.lineWidth = 2; }
        g.beginPath();
        if (it.kind === "choque") g.arc(x, y, 6.5, 0, 7);
        else { g.moveTo(x, y - 8); g.lineTo(x + 7, y); g.lineTo(x, y + 8); g.lineTo(x - 7, y); g.closePath(); }
        g.fill(); g.stroke();
        if (it.status === "Resuelta") { g.strokeStyle = "#10b981"; g.lineWidth = 2; g.beginPath(); g.moveTo(x - 3, y); g.lineTo(x - 1, y + 3); g.lineTo(x + 4, y - 3); g.stroke(); }
        P.hits.push({ x, y, r: 10, items: [it] });
      });
    }
  });

  // barra de escala y norte
  const barM = niceLen(110 / P.k), bw = barM * P.k;
  g.fillStyle = "rgba(255,255,255,.9)"; g.fillRect(10, P.H - 40, bw + 20, 28);
  g.fillStyle = "#0f172a"; g.fillRect(20, P.H - 24, bw, 3); g.font = "11px Segoe UI, sans-serif"; g.fillText(fmtLen(barM), 20, P.H - 29);
  g.fillStyle = "#0f172a"; g.beginPath(); g.moveTo(P.W - 28, 14); g.lineTo(P.W - 22, 32); g.lineTo(P.W - 28, 28); g.lineTo(P.W - 34, 32); g.closePath(); g.fill();
  g.font = "bold 11px Segoe UI, sans-serif"; g.fillText("N", P.W - 32, 46);
  g.font = "10px Segoe UI, sans-serif"; g.fillStyle = "#64748b";
  g.fillText(`${planIssues().length} interferencias visibles • ${S.res.axis.source.startsWith("estimado") ? "Pk estimado" : "Pk de eje cargado"}`, 12, 14);
}

function planPick(e) {
  const r = $("planMap").getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
  let best = null, bd = 1e9;
  P.hits.forEach((h) => { const d = Math.hypot(h.x - x, h.y - y); if (d <= h.r + 4 && d < bd) { bd = d; best = h; } });
  return { hit: best, x, y };
}
function planZoom(f, mx, my) {
  mx = mx ?? P.W / 2; my = my ?? P.H / 2;
  const wx = P.cx + (mx - P.W / 2) / P.k, wy = P.cy - (my - P.H / 2) / P.k;
  P.k = Math.min(60, Math.max(0.002, P.k * f));
  P.cx = wx - (mx - P.W / 2) / P.k; P.cy = wy + (my - P.H / 2) / P.k;
  drawPlan();
}
function planSelect(it, center) {
  P.sel = it;
  if (center) { P.cx = it.point[0]; P.cy = it.point[1]; if (P.k < 1.5) P.k = 1.5; }
  drawPlan();
  const rule = it.rule.verificado ? "" : ' <span class="text-amber-600">(regla de ejemplo)</span>';
  $("planSel").innerHTML = `<div class="border border-slate-200 rounded-lg p-3 bg-slate-50 flex flex-wrap gap-x-6 gap-y-1 items-start">
    <div><span class="px-2 py-0.5 rounded-full text-white font-bold" style="background:${SEV_COLOR[it.severity]}">${esc(it.id)} • ${esc(it.severity)}</span>
      <span class="ml-2 font-semibold text-slate-800">${esc(it.pk_text)}</span> <span class="text-slate-500">${esc(it.pair)}</span></div>
    <div class="flex-1 min-w-[260px] text-slate-700"><b>${it.kind === "choque" ? "Choque geométrico" : "Holgura insuficiente"}</b>: ${esc(it.accion)}${rule}<div class="text-slate-500 mt-0.5">A: ${esc(it.nameA)} <span class="font-mono">#${it.stepA}</span> — B: ${esc(it.nameB)} <span class="font-mono">#${it.stepB}</span></div></div>
    <div class="flex gap-2"><button class="plBtn3d px-3 py-1.5 rounded-lg bg-indigo-600 text-white font-bold" data-id="${esc(it.id)}">Ver en 3D</button>
      <button class="plBtnTab px-3 py-1.5 rounded-lg bg-slate-200 font-semibold" data-id="${esc(it.id)}">Ver en tabla</button></div></div>`;
  document.querySelector(".plBtn3d").addEventListener("click", (e) => open3d(e.currentTarget.dataset.id));
  document.querySelector(".plBtnTab").addEventListener("click", (e) => { $("fSearch").value = e.currentTarget.dataset.id; S.page = 0; renderIssues(); switchTab("tabIssues"); });
}

function bindPlan() {
  const cv = $("planMap");
  cv.addEventListener("wheel", (e) => { e.preventDefault(); const r = cv.getBoundingClientRect(); planZoom(e.deltaY < 0 ? 1.25 : 0.8, e.clientX - r.left, e.clientY - r.top); }, { passive: false });
  cv.addEventListener("pointerdown", (e) => { cv.setPointerCapture(e.pointerId); P.drag = { x: e.clientX, y: e.clientY, moved: 0 }; });
  cv.addEventListener("pointermove", (e) => {
    if (P.drag) {
      const dx = e.clientX - P.drag.x, dy = e.clientY - P.drag.y;
      P.drag.moved += Math.abs(dx) + Math.abs(dy); P.drag.x = e.clientX; P.drag.y = e.clientY;
      P.cx -= dx / P.k; P.cy += dy / P.k; drawPlan(); return;
    }
    const { hit, x, y } = planPick(e), tip = $("planTip");
    if (hit) {
      const t = hit.items.length > 1 ? `${hit.items.length} interferencias — clic para acercar` :
        `${hit.items[0].id} • ${hit.items[0].pk_text} • ${hit.items[0].pair} • ${hit.items[0].severity}${hit.items[0].kind === "holgura" ? " • " + hit.items[0].distance.toFixed(2) + " m" : ""}`;
      tip.textContent = t; tip.style.left = Math.min(P.W - 240, x + 14) + "px"; tip.style.top = Math.max(4, y - 30) + "px"; tip.classList.remove("hidden");
      cv.style.cursor = "pointer";
    } else { tip.classList.add("hidden"); cv.style.cursor = "grab"; }
  });
  cv.addEventListener("pointerup", (e) => {
    const moved = P.drag ? P.drag.moved : 99; P.drag = null;
    if (moved > 5) return;
    const { hit } = planPick(e);
    if (!hit) return;
    if (hit.items.length > 1) planFit(hit.items); else planSelect(hit.items[0], false);
  });
  cv.addEventListener("dblclick", (e) => { const { hit } = planPick(e); if (hit && hit.items.length === 1) open3d(hit.items[0].id); });
  $("plFit").addEventListener("click", () => planFit());
  $("plZin").addEventListener("click", () => planZoom(1.5));
  $("plZout").addEventListener("click", () => planZoom(1 / 1.5));
}

function renderHotZones() {
  const act = S.res.issues.filter((i) => i.status !== "Resuelta" && i.pk != null);
  if (!act.length) { $("hotZones").innerHTML = '<div class="text-slate-400">Sin interferencias activas con Pk.</div>'; return; }
  const win = 100, groups = new Map();
  act.forEach((i) => { const k = Math.floor(i.pk / win) * win; (groups.get(k) || groups.set(k, []).get(k)).push(i); });
  const top = [...groups.entries()].sort((a, b) => b[1].length - a[1].length).slice(0, 8);
  const mx = top[0][1].length;
  $("hotZones").innerHTML = top.map(([k, arr], n) => {
    const pairs = {}; arr.forEach((i) => (pairs[i.pair] = (pairs[i.pair] || 0) + 1));
    const ptxt = Object.entries(pairs).sort((a, b) => b[1] - a[1]).map(([p, c]) => `${p} (${c})`).join(", ");
    const w = worstSev(arr);
    return `<button data-z="${n}" class="hz w-full text-left border border-slate-200 rounded-lg p-2 hover:bg-slate-50">
      <div class="flex justify-between"><b>${esc(fmtPk(k))} – ${esc(fmtPk(k + win).replace("Pk ", ""))}</b><span class="font-bold" style="color:${SEV_COLOR[w]}">${arr.length}</span></div>
      <div class="h-1.5 rounded bg-slate-100 mt-1"><div class="h-1.5 rounded" style="width:${Math.round(100 * arr.length / mx)}%;background:${SEV_COLOR[w]}"></div></div>
      <div class="text-[11px] text-slate-500 mt-1 truncate">${esc(ptxt)}</div></button>`;
  }).join("");
  document.querySelectorAll(".hz").forEach((b) => b.addEventListener("click", () => { planFit(top[+b.dataset.z][1]); $("planWrap").scrollIntoView({ behavior: "smooth", block: "center" }); }));
}
function fmtPk(m) { const km = Math.floor(m / 1000); return `Pk ${km}+${String(Math.round(m - km * 1000)).padStart(3, "0")}`; }

// ------------------------------------------------------------------ visor 3D
const V = { rd: null, scene: null, cam: null, st: null, g: {}, id: null, it: null, anim: 0, info: null, clip: new THREE.Plane(new THREE.Vector3(0, 0, -1), 0), zr: [0, 1], sprites: [], scaled: [] };

function setStatus(it, status) {
  it.status = status;
  return api("/api/status", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project: S.res.project, key: it.key, status, run: S.res.run }) })
    .catch((err) => toast("No se guardó el estado: " + err.message, true));
}

function infoPanel(it) {
  const rule = it.rule;
  return `
    <div class="p-2.5 rounded-lg border" style="border-color:${SEV_COLOR[it.severity]};background:${SEV_COLOR[it.severity]}12">
      <div class="font-bold" style="color:${SEV_COLOR[it.severity]}">${esc(it.severity)} • ${it.kind === "choque" ? "choque geométrico" : "holgura insuficiente"}</div>
      <div class="mt-1">${esc(it.accion)}</div></div>
    <div class="p-2.5 rounded-lg bg-slate-50 border"><div class="font-bold text-sky-700">A • ${esc(it.specs[0])}</div><div class="font-semibold">${esc(it.nameA)}</div><div class="text-slate-500">${esc(it.fileA)}</div><div class="font-mono text-[10px] break-all">GUID ${esc(it.guidA)} • #${it.stepA}</div></div>
    <div class="p-2.5 rounded-lg bg-slate-50 border"><div class="font-bold text-amber-600">B • ${esc(it.specs[1])}</div><div class="font-semibold">${esc(it.nameB)}</div><div class="text-slate-500">${esc(it.fileB)}</div><div class="font-mono text-[10px] break-all">GUID ${esc(it.guidB)} • #${it.stepB}</div></div>
    <div class="p-2.5 rounded-lg bg-slate-50 border"><div><b>Ubicación:</b> ${esc(it.pk_text)}${it.offset != null ? " (a " + it.offset.toFixed(0) + " m del eje)" : ""} ${S.res.axis.source.startsWith("estimado") ? '<span class="text-amber-600">(Pk estimado)</span>' : ""}</div>
      <div><b>Coord.:</b> E ${it.point[0].toFixed(2)} • N ${it.point[1].toFixed(2)} • Z ${it.point[2].toFixed(2)}</div>
      ${it.kind === "holgura" ? `<div><b>Distancia:</b> ${it.distance.toFixed(3)} m • requerida ${rule.holgura_m} m • déficit ${it.deficit.toFixed(2)} m</div>` : ""}
      <div class="mt-1"><b>Responsable:</b> ${esc(it.owner)}</div></div>
    <div class="p-2.5 rounded-lg border ${rule.verificado ? "bg-emerald-50 border-emerald-200" : "bg-amber-50 border-amber-200"}"><b>Regla ${esc(rule.par)}</b>: ${esc(rule.norma)}${rule.verificado ? "" : '<div class="font-semibold text-amber-800">Regla de ejemplo: verificar con el MC vigente.</div>'}</div>
    <div class="flex items-center gap-2"><b>Estado</b>
      <select id="mStatus" class="rounded-lg px-2 py-1 border border-slate-300 font-semibold">${["Activa", "En Proceso", "Resuelta"].map((o) => `<option ${it.status === o ? "selected" : ""}>${o}</option>`).join("")}</select>
      <button id="mToPlan" class="ml-auto px-2.5 py-1 rounded-md bg-slate-100 hover:bg-slate-200 font-semibold">Ver en planta</button></div>`;
}

async function open3d(id) {
  const it = S.res.issues.find((x) => x.id === id);
  if (!it) return;
  V.id = id; V.it = it;
  $("modal3d").classList.remove("hidden");
  document.body.style.overflow = "hidden";
  $("mId").textContent = it.id; $("mId").style.background = SEV_COLOR[it.severity] + "22"; $("mId").style.color = SEV_COLOR[it.severity];
  $("mPair").textContent = it.pair; $("mPk").textContent = it.pk_text;
  $("mTitle").textContent = it.kind === "choque" ? `Choque entre «${it.nameA.slice(0, 40)}» y «${it.nameB.slice(0, 40)}»` : `Holgura de ${it.distance.toFixed(2)} m entre «${it.nameA.slice(0, 36)}» y «${it.nameB.slice(0, 36)}»`;
  $("mInfo").innerHTML = infoPanel(it);
  $("mStatus").addEventListener("change", async (e) => { await setStatus(it, e.target.value); renderKpis(); renderIssues(); renderCharts(); drawPlan(); renderHotZones(); });
  $("mToPlan").addEventListener("click", () => { close3d(); switchTab("tabDash"); P.fitted = true; planSelect(it, true); $("planWrap").scrollIntoView({ block: "center" }); });
  const host = $("viewer3d");
  host.querySelectorAll("canvas").forEach((c) => c.remove());
  $("viewerHud").textContent = "Cargando geometría…";
  try {
    const m = await api(`/api/mesh?run=${S.res.run}&id=${encodeURIComponent(id)}`);
    if (V.id !== id) return;
    build3d(m, it);
  } catch (e) { $("viewerHud").innerHTML = '<span class="text-rose-600">No se pudo cargar la geometría: ' + esc(e.message) + "</span>"; }
}

function labelSprite(text, color) {
  const c = document.createElement("canvas"); c.width = 256; c.height = 64;
  const g = c.getContext("2d");
  g.fillStyle = "rgba(255,255,255,.92)"; g.strokeStyle = color; g.lineWidth = 4;
  g.beginPath(); g.roundRect ? g.roundRect(4, 4, 248, 56, 12) : g.rect(4, 4, 248, 56); g.fill(); g.stroke();
  g.fillStyle = "#0f172a"; g.font = "bold 28px Segoe UI, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(text, 128, 33);
  const tex = new THREE.CanvasTexture(c);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  sp.renderOrder = 10; sp.userData.aspect = 4;
  return sp;
}

function disposeScene() {
  if (!V.scene) return;
  V.scene.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) { (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => { if (m.map) m.map.dispose(); m.dispose(); }); } });
  V.scene = null;
}

function build3d(m, it) {
  const host = $("viewer3d");
  disposeScene();
  if (V.rd) { V.rd.dispose(); V.rd = null; }
  host.querySelectorAll("canvas").forEach((c) => c.remove());
  const w = host.clientWidth, h = host.clientHeight;
  const scene = new THREE.Scene(); scene.background = new THREE.Color(0xeef2f7);
  const cam = new THREE.PerspectiveCamera(45, w / h, 0.02, 20000); cam.up.set(0, 0, 1);
  const rd = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  rd.setSize(w, h); rd.setPixelRatio(Math.min(2, window.devicePixelRatio || 1)); rd.localClippingEnabled = true;
  host.insertBefore(rd.domElement, host.firstChild);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x94a3b8, 0.85));
  const dl = new THREE.DirectionalLight(0xffffff, 0.7); dl.position.set(6, -9, 14); scene.add(dl);
  V.scene = scene; V.cam = cam; V.rd = rd; V.sprites = []; V.scaled = [];
  const G = { A: new THREE.Group(), B: new THREE.Group(), ctx: new THREE.Group(), dim: new THREE.Group(), axes: new THREE.Group(), wire: [] };
  Object.values(G).forEach((x) => x.isGroup && scene.add(x));
  V.g = G;

  const mk = (v, f) => { const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(v, 3)); geo.setIndex(f); geo.computeVertexNormals(); return geo; };
  const box = new THREE.Box3();
  const mats = [];
  const add = (grp, geo, color, opacity, wireOp) => {
    const mat = new THREE.MeshStandardMaterial({ color, side: THREE.DoubleSide, transparent: opacity < 1, opacity, flatShading: true, depthWrite: opacity >= 0.9, clippingPlanes: [] });
    const mesh = new THREE.Mesh(geo, mat); grp.add(mesh); mats.push(mat);
    const wm = new THREE.LineBasicMaterial({ color: 0x0f172a, transparent: true, opacity: wireOp, clippingPlanes: [] });
    const wire = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 25), wm); wire.visible = true; grp.add(wire); mats.push(wm); G.wire.push(wire);
    return mesh;
  };
  const meshesA = add(G.A, mk(m.a.v, m.a.f), 0x0284c7, 0.92, 0.35);
  const meshesB = add(G.B, mk(m.b.v, m.b.f), 0xf59e0b, 0.92, 0.35);
  m.ctx.forEach((c) => add(G.ctx, mk(c.v, c.f), c.m === "a" ? 0x7dd3fc : 0xfcd34d, 0.35, 0.12));
  box.expandByObject(G.A); box.expandByObject(G.B);
  V.mats = mats;

  // suelo y marcador
  const size = box.getSize(new THREE.Vector3()), ctr = box.getCenter(new THREE.Vector3());
  const span = Math.max(size.x, size.y, 6);
  const gridSize = Math.ceil((span * 3) / 10) * 10;
  const grid = new THREE.GridHelper(gridSize, Math.round(gridSize / 2), 0x94a3b8, 0xcbd5e1);
  grid.rotation.x = Math.PI / 2; grid.position.set(ctr.x, ctr.y, box.min.z - 0.01); scene.add(grid); G.grid = grid;
  V.zr = [box.min.z, box.max.z];

  const dot = new THREE.Mesh(new THREE.SphereGeometry(1, 16, 16), new THREE.MeshBasicMaterial({ color: 0xe11d48, depthTest: false, transparent: true, opacity: 0.9 }));
  dot.renderOrder = 9; G.dim.add(dot); V.scaled.push([dot, 0.014]);

  // zona de choque / cota de holgura
  const P0 = new THREE.Vector3(...it.point);
  const rel = (a) => new THREE.Vector3(a[0] - P0.x, a[1] - P0.y, a[2] - P0.z);
  let focusR = 4;
  if (it.kind === "choque" && it.zone) {
    const lo = rel(it.zone[0]), hi = rel(it.zone[1]);
    const sz = new THREE.Vector3(Math.max(hi.x - lo.x, 0.25), Math.max(hi.y - lo.y, 0.25), Math.max(hi.z - lo.z, 0.25));
    const zc = lo.clone().add(hi).multiplyScalar(0.5);
    const zb = new THREE.Mesh(new THREE.BoxGeometry(sz.x, sz.y, sz.z), new THREE.MeshBasicMaterial({ color: 0xe11d48, transparent: true, opacity: 0.28, depthWrite: false }));
    zb.position.copy(zc); G.dim.add(zb);
    const ze = new THREE.LineSegments(new THREE.EdgesGeometry(zb.geometry), new THREE.LineBasicMaterial({ color: 0xe11d48 })); ze.position.copy(zc); G.dim.add(ze);
    focusR = Math.max(3, sz.length() * 2.5);
    const lb = labelSprite(`Zona de choque ${sz.x.toFixed(2)}×${sz.y.toFixed(2)}×${sz.z.toFixed(2)} m`, "#e11d48");
    lb.position.set(zc.x, zc.y, zc.z + sz.z / 2 + 0.3); G.dim.add(lb); V.scaled.push([lb, 0.05]);
  } else if (it.gap) {
    const a = rel(it.gap[0]), b = rel(it.gap[1]);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]), new THREE.LineBasicMaterial({ color: 0xe11d48, depthTest: false }));
    line.renderOrder = 8; G.dim.add(line);
    [a, b].forEach((p) => { const s = new THREE.Mesh(new THREE.SphereGeometry(1, 12, 12), new THREE.MeshBasicMaterial({ color: 0xe11d48, depthTest: false })); s.position.copy(p); s.renderOrder = 9; G.dim.add(s); V.scaled.push([s, 0.008]); });
    const lb = labelSprite(`${it.distance.toFixed(2)} m  (req. ${it.rule.holgura_m} m)`, "#e11d48");
    lb.position.copy(a.clone().add(b).multiplyScalar(0.5)).add(new THREE.Vector3(0, 0, 0.4)); G.dim.add(lb); V.scaled.push([lb, 0.05]);
    focusR = Math.max(3, a.distanceTo(b) * 6);
  }
  const ax = new THREE.AxesHelper(1); ax.renderOrder = 7; G.axes.add(ax); V.scaled.push([ax, 0.12]);
  const eL = labelSprite("E", "#e11d48"), nL = labelSprite("N", "#16a34a");
  eL.position.set(1.15, 0, 0); nL.position.set(0, 1.15, 0); eL.userData.rel = nL.userData.rel = true;
  G.axes.add(eL); G.axes.add(nL);

  // estado de cámara
  V.center = ctr.clone(); V.zone = focusR; V.span = Math.max(size.length(), 6);  // la malla llega relativa al punto del conflicto
  V.st = { tgt: new THREE.Vector3(), r: 10, th: -Math.PI / 4, ph: Math.PI / 3 };
  V.zr = [box.min.z, box.max.z];
  applyToggles();
  setView("iso", false);
  bind3dControls();
  render3d();
}

function render3d() {
  if (!V.rd) return;
  const { st, cam } = V;
  cam.position.set(st.tgt.x + st.r * Math.sin(st.ph) * Math.cos(st.th), st.tgt.y + st.r * Math.sin(st.ph) * Math.sin(st.th), st.tgt.z + st.r * Math.cos(st.ph));
  cam.lookAt(st.tgt);
  V.scaled.forEach(([o, k]) => {
    const s = st.r * k;
    if (o.isSprite) o.scale.set(s * o.userData.aspect, s, 1); else o.scale.setScalar(Math.max(s, 0.01));
  });
  V.g.axes.children.forEach((o) => { if (o.isSprite) { const s = st.r * 0.03; o.scale.set(s * 4, s, 1); o.position.setLength(st.r * 0.14); } });
  V.rd.render(V.scene, cam);
  $("viewerHud").innerHTML = `Cámara a ${st.r.toFixed(1)} m • ${V.sectionOn ? "corte Z ≤ " + (V.it.point[2] + V.clip.constant).toFixed(2) : "sin corte"}`;
}

function applyToggles() {
  if (!V.scene) return;
  V.g.A.visible = $("tgA").checked; V.g.B.visible = $("tgB").checked; V.g.ctx.visible = $("tgCtx").checked;
  V.g.dim.visible = $("tgDim").checked; V.g.axes.visible = $("tgAxes").checked;
  V.g.wire.forEach((w) => (w.visible = $("tgWire").checked || w.parent === V.g.A || w.parent === V.g.B));
  V.g.wire.forEach((w) => { w.material.opacity = $("tgWire").checked ? 0.6 : (w.parent === V.g.ctx ? 0 : 0.3); });
  V.sectionOn = $("tgSec").checked;
  $("secZ").classList.toggle("hidden", !V.sectionOn);
  const zmin = V.zr[0], zmax = V.zr[1] + 0.2;
  const z = zmin + (zmax - zmin) * (+$("secZ").value / 100);
  V.clip.constant = z;
  V.mats.forEach((m) => { m.clippingPlanes = V.sectionOn ? [V.clip] : []; m.needsUpdate = true; });
}

function setView(name, animate = true) {
  const st = V.st; if (!st) return;
  const to = { tgt: new THREE.Vector3(), r: 10, th: st.th, ph: st.ph };
  const c = V.center.clone(), R = Math.max(V.span * 1.4, 6);
  if (name === "iso") Object.assign(to, { tgt: c.clone().multiplyScalar(0.5), r: R * 1.2, th: -Math.PI / 4, ph: Math.PI / 3 });
  if (name === "top") Object.assign(to, { tgt: c, r: R * 1.3, th: -Math.PI / 2, ph: 0.001 });
  if (name === "front") Object.assign(to, { tgt: c, r: R * 1.3, th: -Math.PI / 2, ph: Math.PI / 2 });
  if (name === "side") Object.assign(to, { tgt: c, r: R * 1.3, th: 0, ph: Math.PI / 2 });
  if (name === "focus") Object.assign(to, { tgt: new THREE.Vector3(), r: Math.max(3, V.zone), th: -Math.PI / 4, ph: Math.PI / 3 });
  if (!animate) { Object.assign(st, to); return; }
  const from = { tgt: st.tgt.clone(), r: st.r, th: st.th, ph: st.ph }, t0 = performance.now();
  cancelAnimationFrame(V.anim);
  const step = (now) => {
    const t = Math.min(1, (now - t0) / 260), e = t * (2 - t);
    st.tgt.lerpVectors(from.tgt, to.tgt, e); st.r = from.r + (to.r - from.r) * e; st.th = from.th + (to.th - from.th) * e; st.ph = from.ph + (to.ph - from.ph) * e;
    render3d();
    if (t < 1) V.anim = requestAnimationFrame(step);
  };
  V.anim = requestAnimationFrame(step);
}

let ctl3dBound = false;
function bind3dControls() {
  const el = V.rd.domElement, st = V.st;
  let drag = null;
  el.onpointerdown = (e) => { cancelAnimationFrame(V.anim); drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey || e.button === 2 || e.button === 1 }; el.setPointerCapture(e.pointerId); };
  el.onpointerup = () => (drag = null);
  el.oncontextmenu = (e) => e.preventDefault();
  el.onpointermove = (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY;
    if (drag.pan) {
      const dir = V.cam.getWorldDirection(new THREE.Vector3()), right = new THREE.Vector3().crossVectors(dir, V.cam.up).normalize(), up = new THREE.Vector3().crossVectors(right, dir).normalize();
      const k = 0.93 * st.r / el.clientHeight;
      st.tgt.addScaledVector(right, -dx * k).addScaledVector(up, dy * k);
    } else { st.th -= dx * 0.008; st.ph = Math.min(Math.PI - 0.02, Math.max(0.001, st.ph - dy * 0.008)); }
    render3d();
  };
  el.onwheel = (e) => { e.preventDefault(); st.r = Math.min(3000, Math.max(0.4, st.r * (e.deltaY > 0 ? 1.12 : 0.89))); render3d(); };
  el.ondblclick = (e) => {
    const r = el.getBoundingClientRect();
    const ndc = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    V.ray = V.ray || new THREE.Raycaster(); V.ray.setFromCamera(ndc, V.cam);
    const hits = V.ray.intersectObjects([V.g.A, V.g.B, V.g.ctx].filter((g) => g.visible), true).filter((h) => h.object.isMesh);
    if (hits.length) { const to = hits[0].point.clone(); const from = st.tgt.clone(), t0 = performance.now(); const stp = (now) => { const t = Math.min(1, (now - t0) / 220); st.tgt.lerpVectors(from, to, t); render3d(); if (t < 1) V.anim = requestAnimationFrame(stp); }; V.anim = requestAnimationFrame(stp); }
  };
}
function bindModalOnce() {
  document.querySelectorAll(".vw").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  ["tgA", "tgB", "tgCtx", "tgWire", "tgSec", "secZ", "tgDim", "tgAxes"].forEach((id) => $(id).addEventListener("input", () => { applyToggles(); render3d(); }));
  $("mPrev").addEventListener("click", () => nav3d(-1));
  $("mNext").addEventListener("click", () => nav3d(1));
  window.addEventListener("keydown", (e) => {
    if ($("modal3d").classList.contains("hidden")) return;
    if (e.key === "Escape") close3d();
    if (e.key === "ArrowLeft") nav3d(-1);
    if (e.key === "ArrowRight") nav3d(1);
  });
  window.addEventListener("resize", () => {
    if (!V.rd || $("modal3d").classList.contains("hidden")) return;
    const h = $("viewer3d"); V.rd.setSize(h.clientWidth, h.clientHeight); V.cam.aspect = h.clientWidth / h.clientHeight; V.cam.updateProjectionMatrix(); render3d();
  });
}
function nav3d(d) {
  const list = filtered(), i = list.findIndex((x) => x.id === V.id);
  const n = list[(i + d + list.length) % list.length];
  if (n) open3d(n.id);
}
function close3d() {
  cancelAnimationFrame(V.anim);
  $("modal3d").classList.add("hidden");
  document.body.style.overflow = "";
  disposeScene();
  if (V.rd) { V.rd.dispose(); V.rd = null; }
  V.id = null;
  $("viewer3d").querySelectorAll("canvas").forEach((c) => c.remove());
}

// ------------------------------------------------------------------ croquis SVG (informe)
function croquisSvg(it, W = 400, H = 300) {
  const A = footIndex(it.ia).get(it.stepA), B = footIndex(it.ib).get(it.stepB);
  const cx = it.point[0], cy = it.point[1];
  let ext = 8;
  [A, B].forEach((fp) => { if (fp) { const [a, b, c, d] = footBounds(fp); ext = Math.max(ext, (c - a) * 0.6 + 6, (d - b) * 0.6 + 6); } });
  const R = Math.min(90, ext), k = Math.min(W, H) / (2 * R);
  const X = (x) => (W / 2 + (x - cx) * k).toFixed(1), Y = (y) => (H / 2 - (y - cy) * k).toFixed(1);
  const inWin = (fp) => { const [a, b, c, d] = footBounds(fp); return !(c < cx - R * 1.4 || a > cx + R * 1.4 || d < cy - R * 1.4 || b > cy + R * 1.4); };
  const foot = (fp, color, op, bold) => {
    if (fp.k === "p") { const w = Math.max(bold ? 6 : 2.5, fp.e[0] * k), h = Math.max(bold ? 6 : 2.5, fp.e[1] * k); return `<rect x="${(X(fp.c[0]) - w / 2).toFixed(1)}" y="${(Y(fp.c[1]) - h / 2).toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" fill="${color}" opacity="${op}"/>`; }
    const pts = []; for (let i = 0; i < fp.p.length; i += 2) pts.push(X(fp.p[i]) + "," + Y(fp.p[i + 1]));
    return `<polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="${Math.max(bold ? 3.5 : 1.5, fp.w * k).toFixed(1)}" stroke-linecap="round" stroke-linejoin="round" opacity="${op}"/>`;
  };
  let svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="100%" style="background:#f8fafc;border:1px solid #cbd5e1;border-radius:4px"><defs><clipPath id="cp${it.id}"><rect width="${W}" height="${H}"/></clipPath></defs><g clip-path="url(#cp${it.id})">`;
  // cuadrícula
  const gm = niceLen(R / 3);
  for (let x = Math.ceil((cx - R * 1.5) / gm) * gm; x < cx + R * 1.5; x += gm) svg += `<line x1="${X(x)}" y1="0" x2="${X(x)}" y2="${H}" stroke="#e2e8f0"/>`;
  for (let y = Math.ceil((cy - R * 1.5) / gm) * gm; y < cy + R * 1.5; y += gm) svg += `<line x1="0" y1="${Y(y)}" x2="${W}" y2="${Y(y)}" stroke="#e2e8f0"/>`;
  [it.ia, it.ib].forEach((mi, n) => { if (n && it.ib === it.ia) return; (S.res.models[mi].foot || []).forEach((fp) => { if (fp.s !== it.stepA && fp.s !== it.stepB && inWin(fp)) svg += foot(fp, MODEL_COLORS[mi % 6], 0.45, false); }); });
  // eje
  const ap = S.res.axis.polyline.filter((p) => Math.abs(p[0] - cx) < R * 3 && Math.abs(p[1] - cy) < R * 3);
  if (ap.length > 1) svg += `<polyline points="${ap.map((p) => X(p[0]) + "," + Y(p[1])).join(" ")}" fill="none" stroke="#0f172a" stroke-width="1.8" stroke-dasharray="6 4"/>`;
  if (A) svg += foot(A, "#0284c7", 1, true);
  if (B) svg += foot(B, "#d97706", 1, true);
  if (it.kind === "holgura" && it.gap) svg += `<line x1="${X(it.gap[0][0])}" y1="${Y(it.gap[0][1])}" x2="${X(it.gap[1][0])}" y2="${Y(it.gap[1][1])}" stroke="#e11d48" stroke-width="2"/>`;
  const col = SEV_COLOR[it.severity];
  if (it.kind === "choque") svg += `<circle cx="${X(cx)}" cy="${Y(cy)}" r="9" fill="none" stroke="${col}" stroke-width="2.5"/><circle cx="${X(cx)}" cy="${Y(cy)}" r="3" fill="${col}"/>`;
  else svg += `<path d="M${X(cx)} ${(Y(cy) - 9)} l8 9 l-8 9 l-8 -9z" fill="none" stroke="${col}" stroke-width="2.5"/>`;
  svg += "</g>";
  const bar = niceLen(R * 0.7), bw = bar * k;
  svg += `<rect x="6" y="${H - 26}" width="${bw + 14}" height="20" fill="#fff" opacity=".9"/><rect x="12" y="${H - 14}" width="${bw}" height="3" fill="#0f172a"/><text x="12" y="${H - 17}" font-size="10" font-family="Segoe UI,sans-serif">${fmtLen(bar)}</text>`;
  svg += `<path d="M${W - 18} 8 l5 16 l-5 -4 l-5 4z" fill="#0f172a"/><text x="${W - 22}" y="38" font-size="10" font-weight="700" font-family="Segoe UI,sans-serif">N</text>`;
  svg += `<text x="8" y="14" font-size="11" font-weight="700" font-family="Segoe UI,sans-serif" fill="#0f172a">${esc(it.pk_text)}</text></svg>`;
  return svg;
}

function overviewSvg(W = 760, H = 330) {
  const [x0, y0, x1, y1] = planBounds();
  const pad = 22, k = Math.min((W - 2 * pad) / Math.max(1, x1 - x0), (H - 2 * pad) / Math.max(1, y1 - y0));
  const ox = (W - (x1 - x0) * k) / 2, oy = (H - (y1 - y0) * k) / 2;
  const X = (x) => (ox + (x - x0) * k).toFixed(1), Y = (y) => (H - oy - (y - y0) * k).toFixed(1);
  let s = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="100%" style="background:#f8fafc;border:1px solid #cbd5e1;border-radius:4px">`;
  S.res.models.forEach((m, i) => (m.foot || []).forEach((fp) => {
    const [a, b] = footBounds(fp);
    if (fp.k === "p") s += `<circle cx="${X(fp.c[0])}" cy="${Y(fp.c[1])}" r="0.9" fill="${MODEL_COLORS[i % 6]}" opacity=".6"/>`;
    else { const pts = []; for (let j = 0; j < fp.p.length; j += 2) pts.push(X(fp.p[j]) + "," + Y(fp.p[j + 1])); s += `<polyline points="${pts.join(" ")}" fill="none" stroke="${MODEL_COLORS[i % 6]}" stroke-width="1.6" opacity=".8"/>`; }
  }));
  const ap = S.res.axis.polyline;
  if (ap.length) s += `<polyline points="${ap.map((p) => X(p[0]) + "," + Y(p[1])).join(" ")}" fill="none" stroke="#0f172a" stroke-width="1.6" stroke-dasharray="6 4"/>`;
  let lastX = -1e9, lastY = -1e9;
  (S.res.axis.ticks || []).forEach((t) => {
    const x = +X(t.x), y = +Y(t.y);
    s += `<circle cx="${x}" cy="${y}" r="2" fill="#0f172a"/>`;
    if (Math.hypot(x - lastX, y - lastY) > 55) { s += `<text x="${x + 4}" y="${y - 4}" font-size="9" font-family="Segoe UI,sans-serif" font-weight="700">${esc(t.txt.replace("Pk ", ""))}</text>`; lastX = x; lastY = y; }
  });
  S.res.issues.filter((i) => i.status !== "Resuelta").forEach((i) => { s += `<circle cx="${X(i.point[0])}" cy="${Y(i.point[1])}" r="${i.severity === "Crítica" ? 4.2 : 3}" fill="${SEV_COLOR[i.severity]}" stroke="#fff" stroke-width="1"/>`; });
  const bar = niceLen(150 / k), bw = bar * k;
  s += `<rect x="10" y="${H - 28}" width="${bw + 16}" height="22" fill="#fff" opacity=".9"/><rect x="18" y="${H - 14}" width="${bw}" height="3" fill="#0f172a"/><text x="18" y="${H - 17}" font-size="10" font-family="Segoe UI,sans-serif">${fmtLen(bar)}</text>`;
  return s + `<path d="M${W - 20} 8 l5 16 l-5 -4 l-5 4z" fill="#0f172a"/><text x="${W - 24}" y="40" font-size="10" font-weight="700">N</text></svg>`;
}

// ------------------------------------------------------------------ informe de interferencias
function buildReport() {
  const r = S.res, scope = $("rpScope").value, fichasSel = $("rpFichas").value, groupBy = $("rpGroup").value;
  const author = $("rpAuthor").value.trim();
  const inScope = (i) => scope === "all" || (scope === "active" && i.status !== "Resuelta") ||
    (scope === "crit" && i.severity === "Crítica" && i.status !== "Resuelta") ||
    (scope === "critmay" && i.severity !== "Moderada" && i.status !== "Resuelta");
  const list = r.issues.filter(inScope);
  const act = r.issues.filter((i) => i.status !== "Resuelta");
  const cnt = (f) => r.issues.filter(f).length;
  const sevChip = (s) => `<span class="chip" style="background:${SEV_COLOR[s]}">${esc(s)}</span>`;
  const scopeTxt = { crit: "críticas activas", critmay: "críticas y mayores activas", active: "todas las interferencias activas", all: "todas las interferencias" }[scope];
  const unverified = [...new Set(r.issues.filter((i) => !i.rule.verificado).map((i) => i.rule.par))];
  const estPk = r.axis.source.startsWith("estimado");
  const now = new Date().toLocaleString("es-CL");
  let h = "";

  // ---- portada + resumen
  h += `<div class="page">
    <div style="border-left:8px solid #f59e0b;padding-left:14px;margin-bottom:14px">
      <div style="font-size:11px;letter-spacing:.14em;color:#b45309;font-weight:800">COORDINACIÓN BIM VIALIDAD • INFORME DE INTERFERENCIAS</div>
      <h1>${esc(r.project)}</h1>
      <div style="color:#475569">Emitido ${esc(now)}${author ? " • Preparado por " + esc(author) : ""} • Alcance: ${esc(scopeTxt)} (${list.length} de ${r.issues.length})</div>
    </div>
    <h2>1. Resumen ejecutivo</h2>
    <table style="margin-bottom:8px"><tr>
      ${[["Interferencias detectadas", r.issues.length, "#0f172a"], ["Activas", act.length, "#0f172a"], ["Críticas activas", cnt((i) => i.severity === "Crítica" && i.status !== "Resuelta"), SEV_COLOR["Crítica"]],
        ["Choques geométricos", cnt((i) => i.kind === "choque"), "#0f172a"], ["Holguras insuficientes", cnt((i) => i.kind === "holgura"), "#0f172a"], ["Resueltas", r.issues.length - act.length, "#059669"]]
        .map(([t, v, c]) => `<td style="border:1px solid #e2e8f0;text-align:center;padding:8px"><div style="font-size:22px;font-weight:800;color:${c}">${v}</div><div style="font-size:10px;color:#64748b">${t}</div></td>`).join("")}
    </tr></table>
    ${(() => {
      const top = [...r.issues].filter((i) => i.status !== "Resuelta"), pairs = {};
      top.forEach((i) => { (pairs[i.pair] ||= { Crítica: 0, Mayor: 0, Moderada: 0 })[i.severity]++; });
      const ent = Object.entries(pairs).sort((a, b) => (b[1].Crítica * 100 + b[1].Mayor * 10 + b[1].Moderada) - (a[1].Crítica * 100 + a[1].Mayor * 10 + a[1].Moderada));
      const firstPair = ent[0];
      const hot = (() => { const g = {}; top.filter((i) => i.pk != null).forEach((i) => { const k = Math.floor(i.pk / 100) * 100; g[k] = (g[k] || 0) + 1; }); const e = Object.entries(g).sort((a, b) => b[1] - a[1])[0]; return e ? `${fmtPk(+e[0])} a ${fmtPk(+e[0] + 100).replace("Pk ", "")} (${e[1]} interferencias)` : "—"; })();
      return `<p><b>Conclusión.</b> Se evaluaron ${fmt(r.totalElements)} elementos de ${r.models.length} modelos IFC (${fmt(r.pairs_tested)} pares candidatos tras filtro de cajas) y se registraron <b>${r.issues.length}</b> interferencias: ${cnt((i) => i.kind === "choque")} choques geométricos y ${cnt((i) => i.kind === "holgura")} infracciones de holgura. ${firstPair ? `El cruce con más hallazgos es <b>${esc(firstPair[0])}</b> (${firstPair[1].Crítica} críticas, ${firstPair[1].Mayor} mayores, ${firstPair[1].Moderada} moderadas). ` : ""}La zona más cargada es <b>${esc(hot)}</b>.</p>`;
    })()}
    <h3 style="margin-top:10px">Modelos coordinados</h3>
    <table><tr><th>Esp.</th><th>Archivo</th><th>Esquema</th><th style="text-align:right">Elementos</th><th style="text-align:right">Interf. donde participa</th></tr>
    ${r.models.map((m, mi) => `<tr><td><b>${esc(m.code)}</b></td><td style="word-break:break-all">${esc(m.file)}</td><td>${esc(m.format)}</td><td style="text-align:right">${fmt(m.elements)}</td><td style="text-align:right">${cnt((i) => i.ia === mi || i.ib === mi)}</td></tr>`).join("")}</table>
    <h3 style="margin-top:10px">Plano general de interferencias activas</h3>
    ${overviewSvg()}
    <div style="font-size:10px;color:#475569;margin-top:3px">${r.models.map((m, i) => `<span style="color:${MODEL_COLORS[i % 6]};font-weight:700">■</span> ${esc(m.code)} ${esc(m.file.slice(-32))}`).join(" &nbsp; ")} &nbsp; <span style="color:${SEV_COLOR["Crítica"]}">●</span> Crítica &nbsp; <span style="color:${SEV_COLOR["Mayor"]}">●</span> Mayor &nbsp; <span style="color:${SEV_COLOR["Moderada"]}">●</span> Moderada &nbsp; – – eje${estPk ? " (estimado)" : ""}</div>
  </div>`;

  // ---- distribución
  const pairRows = (() => {
    const pairs = {}; list.forEach((i) => { (pairs[i.pair] ||= { Crítica: 0, Mayor: 0, Moderada: 0, choque: 0, holgura: 0 }); pairs[i.pair][i.severity]++; pairs[i.pair][i.kind]++; });
    return Object.entries(pairs).sort((a, b) => (b[1].Crítica * 1e4 + b[1].Mayor * 100 + b[1].Moderada) - (a[1].Crítica * 1e4 + a[1].Mayor * 100 + a[1].Moderada));
  })();
  const tr = {}; list.forEach((i) => { (tr[i.level] ||= { n: 0, c: 0 }); tr[i.level].n++; if (i.severity === "Crítica") tr[i.level].c++; });
  const trRows = Object.entries(tr).sort((a, b) => parseInt(a[0].replace(/\D+/, "")) - parseInt(b[0].replace(/\D+/, "")));
  const trMax = Math.max(1, ...trRows.map((x) => x[1].n));
  h += `<div class="page pb"><h2>2. Distribución de las interferencias</h2>
    <h3>Por cruce de especialidades</h3>
    <table><tr><th>Cruce</th><th style="text-align:right">Total</th><th style="text-align:right">Críticas</th><th style="text-align:right">Mayores</th><th style="text-align:right">Moderadas</th><th style="text-align:right">Choques</th><th style="text-align:right">Holguras</th></tr>
    ${pairRows.map(([p, v]) => `<tr><td><b>${esc(p)}</b></td><td style="text-align:right">${v.Crítica + v.Mayor + v.Moderada}</td><td style="text-align:right;color:${SEV_COLOR["Crítica"]};font-weight:700">${v.Crítica}</td><td style="text-align:right">${v.Mayor}</td><td style="text-align:right">${v.Moderada}</td><td style="text-align:right">${v.choque}</td><td style="text-align:right">${v.holgura}</td></tr>`).join("") || '<tr><td colspan="7">Sin interferencias en el alcance.</td></tr>'}</table>
    <h3 style="margin-top:12px">Por tramo de 1 km (Pk${estPk ? " estimado" : ""})</h3>
    <table><tr><th style="width:26%">Tramo</th><th style="text-align:right;width:9%">Total</th><th style="text-align:right;width:9%">Críticas</th><th>Magnitud</th></tr>
    ${trRows.map(([l, v]) => `<tr><td>${esc(l)}</td><td style="text-align:right">${v.n}</td><td style="text-align:right;color:${SEV_COLOR["Crítica"]};font-weight:700">${v.c}</td><td><div class="bar" style="width:${Math.round(100 * v.n / trMax)}%"></div></td></tr>`).join("") || '<tr><td colspan="4">—</td></tr>'}</table>
    <h3 style="margin-top:12px">Responsables y acciones pendientes</h3>
    <table><tr><th>Responsable</th><th style="text-align:right">Pendientes</th><th style="text-align:right">Críticas</th><th>Interferencias</th></tr>
    ${(() => {
      const ow = {}; list.filter((i) => i.status !== "Resuelta").forEach((i) => { (ow[i.owner] ||= []).push(i); });
      return Object.entries(ow).sort((a, b) => b[1].length - a[1].length).map(([o, arr]) => `<tr><td><b>${esc(o)}</b></td><td style="text-align:right">${arr.length}</td><td style="text-align:right">${arr.filter((i) => i.severity === "Crítica").length}</td><td style="font-size:9.5px">${arr.slice(0, 40).map((i) => esc(i.id.replace("VIAL-", ""))).join(", ")}${arr.length > 40 ? " … (+" + (arr.length - 40) + ")" : ""}</td></tr>`).join("") || '<tr><td colspan="4">—</td></tr>';
    })()}</table>
  </div>`;

  // ---- ranking
  const ranked = [...list].sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity] || (a.kind === b.kind ? b.deficit - a.deficit : a.kind === "choque" ? -1 : 1));
  const topN = ranked.slice(0, 40);
  h += `<div class="page pb"><h2>3. Priorización: interferencias más graves</h2>
    <p style="color:#475569;margin-bottom:6px">Orden: gravedad, choques antes que holguras y mayor déficit primero. Se listan ${topN.length} de ${ranked.length}.</p>
    <table><tr><th>#</th><th>ID</th><th>Pk</th><th>Cruce</th><th>Hallazgo</th><th>Acción requerida</th><th>Responsable</th></tr>
    ${topN.map((i, n) => `<tr><td>${n + 1}</td><td><b>${esc(i.id)}</b><br>${sevChip(i.severity)}</td><td style="white-space:nowrap">${esc(i.pk_text)}</td><td>${esc(i.pair)}</td><td>${i.kind === "choque" ? "Choque" : "Holgura " + i.distance.toFixed(2) + " m"}</td><td>${esc(i.accion)}</td><td>${esc(i.owner)}</td></tr>`).join("") || '<tr><td colspan="7">—</td></tr>'}</table>
  </div>`;

  // ---- fichas
  const nF = fichasSel === "all" ? ranked.length : +fichasSel;
  const fich = ranked.slice(0, nF);
  const keyOf = { sev: (i) => i.severity, pk: (i) => i.level, pair: (i) => i.pair }[groupBy];
  const ord = groupBy === "pk" ? (a, b) => (a.pk ?? 1e12) - (b.pk ?? 1e12) : groupBy === "sev" ? (a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity] || (a.pk ?? 0) - (b.pk ?? 0) : (a, b) => a.pair.localeCompare(b.pair) || (a.pk ?? 0) - (b.pk ?? 0);
  fich.sort(ord);
  if (fich.length) {
    h += `<div class="page pb"><h2>4. Fichas de interferencia</h2><p style="color:#475569">${fich.length} fichas agrupadas por ${{ sev: "gravedad", pk: "progresiva", pair: "cruce" }[groupBy]}. Croquis en planta: elemento A en azul, B en naranja, eje punteado, contexto del mismo modelo atenuado.</p>`;
    let lastKey = null;
    fich.forEach((i) => {
      const k = keyOf(i);
      if (k !== lastKey) { h += `<h3 style="margin:12px 0 6px;background:#f1f5f9;padding:3px 8px;border-radius:4px">${esc(k)}</h3>`; lastKey = k; }
      h += `<div class="ficha"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">
        <div><b style="font-size:13px">${esc(i.id)}</b> ${sevChip(i.severity)} <span style="color:#475569">${i.kind === "choque" ? "Choque geométrico" : "Holgura insuficiente"} • ${esc(i.pair)}</span></div>
        <div style="font-weight:700">${esc(i.pk_text)}</div></div>
        <div style="display:flex;gap:10px;align-items:flex-start">
          <div style="flex:1.25;min-width:0"><table class="kv">
            <tr><td>Elemento A</td><td><b>${esc(i.nameA)}</b><br><span style="color:#64748b">${esc(i.fileA)} • #${i.stepA}<br>GUID ${esc(i.guidA)}</span></td></tr>
            <tr><td>Elemento B</td><td><b>${esc(i.nameB)}</b><br><span style="color:#64748b">${esc(i.fileB)} • #${i.stepB}<br>GUID ${esc(i.guidB)}</span></td></tr>
            <tr><td>Hallazgo</td><td>${i.kind === "choque" ? "Las mallas de A y B se intersectan." : `Distancia mínima ${i.distance.toFixed(3)} m; requerida ${i.rule.holgura_m} m; déficit ${i.deficit.toFixed(2)} m.`}</td></tr>
            <tr><td>Criterio</td><td>${esc(i.rule.norma)}${i.rule.verificado ? "" : ' <i style="color:#b45309">(regla de ejemplo, verificar)</i>'}</td></tr>
            <tr><td>Ubicación</td><td>E ${i.point[0].toFixed(2)} • N ${i.point[1].toFixed(2)} • Z ${i.point[2].toFixed(2)}${i.offset != null ? " • a " + i.offset.toFixed(0) + " m del eje" : ""}</td></tr>
            <tr><td>Acción</td><td><b>${esc(i.accion)}</b></td></tr>
            <tr><td>Responsable</td><td>${esc(i.owner)} • Estado: ${esc(i.status)}</td></tr></table></div>
          <div style="flex:1;min-width:0">${croquisSvg(i, 360, 290)}</div></div></div>`;
    });
    h += "</div>";
  }

  // ---- anexo
  h += `<div class="page pb"><h2>${fich.length ? 5 : 4}. Alcance, método y limitaciones</h2><ul style="list-style:disc;padding-left:18px;line-height:1.55">
    <li><b>Detección.</b> Geometría de cada IFC leída con ifcopenshell en coordenadas de mundo. Filtro por cajas envolventes y prueba exacta entre mallas triangulares: <i>choque</i> = aristas de una malla atraviesan triángulos de la otra; <i>holgura</i> = distancia mínima entre superficies muestreadas (resolución ${esc($("spacing").value)} m) menor que la regla del par.</li>
    <li><b>Pk.</b> ${estPk ? "<b>Estimado:</b> los IFC no contienen IfcAlignment y no se cargó un eje. Se reconstruyó un eje desde los centroides de los elementos (" + fmt(Math.round(r.axis.length || 0)) + " m); no sustituye al Pk de proyecto." : "Calculado sobre el eje cargado (" + esc(r.axis.source) + ")."}</li>
    <li><b>Reglas.</b> ${unverified.length ? `Las reglas de los pares <b>${esc(unverified.join(", "))}</b> son valores de ejemplo no verificados contra el Manual de Carreteras vigente; los umbrales y citas deben validarse antes de uso contractual.` : "Todas las reglas aplicadas están marcadas como verificadas."}</li>
    <li><b>Georreferenciación.</b> ${r.georef.crs_valid_models} de ${r.georef.models} modelos declaran un CRS válido. ${esc(r.georef.warnings.join(" "))} La coincidencia espacial entre modelos se asume por compartir coordenadas de mundo.</li>
    <li><b>Geometría simplificada.</b> Los elementos con más de 6.000 triángulos se simplifican por agrupación de vértices antes de las pruebas, lo que puede desplazar hallazgos del orden de centímetros.</li>
    <li><b>Estados.</b> Los estados y responsables son los registrados en el Asistente al momento de emitir este informe.</li></ul></div>`;
  $("reportDoc").innerHTML = h;
  return list.length;
}

// ------------------------------------------------------------------ exportar
function exportCsv() {
  const H = ["ID", "Pk", "Tramo", "Tipo", "Cruce", "Archivo_A", "GUID_A", "STEP_A", "Archivo_B", "GUID_B", "STEP_B", "Distancia_m", "Severidad", "Responsable", "Estado", "Descripcion", "E", "N", "Z"];
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const rows = S.res.issues.map((i) => [i.id, i.pk_text, i.level, i.kind, i.pair, i.fileA, i.guidA, i.stepA, i.fileB, i.guidB, i.stepB, i.distance, i.severity, i.owner, i.status, i.desc, ...i.point].map(q).join(","));
  const blob = new Blob(["﻿" + [H.join(","), ...rows].join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `Coordinacion_${S.res.project}.csv`;
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
}

// ------------------------------------------------------------------ reglas
async function loadRules() {
  try { $("rulesText").value = JSON.stringify(await api("/api/rules"), null, 2); } catch (e) { /* offline */ }
}
async function saveRules() {
  try {
    const obj = JSON.parse($("rulesText").value);
    await api("/api/rules", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj) });
    $("rulesMsg").textContent = "Guardado. Vuelve a ejecutar el cruce para aplicar.";
  } catch (e) { $("rulesMsg").textContent = "Error: " + e.message; }
}

// ------------------------------------------------------------------ init
document.querySelectorAll(".nav-tab").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
$("btnRun").addEventListener("click", runCross);
$("uploadInput").addEventListener("change", (e) => uploadFiles([...e.target.files]));
["fSpec", "fLevel", "fSev", "fKind", "fStatus"].forEach((id) => $(id).addEventListener("change", () => { S.page = 0; renderIssues(); }));
$("fSearch").addEventListener("input", () => { S.page = 0; renderIssues(); });
$("pgPrev").addEventListener("click", () => { S.page = Math.max(0, S.page - 1); renderIssues(); });
$("pgNext").addEventListener("click", () => { S.page++; renderIssues(); });
$("mxClear").addEventListener("click", () => { S.pair = null; $("mxClear").classList.add("hidden"); $("mxStatus").textContent = ""; renderMatrix(); renderIssues(); });
$("mClose").addEventListener("click", close3d);
$("modal3d").addEventListener("click", (e) => { if (e.target === $("modal3d")) close3d(); });
$("btnXlsx").addEventListener("click", () => (location.href = "/api/export/xlsx?run=" + S.res.run));
$("btnBcf").addEventListener("click", () => (location.href = "/api/export/bcf?run=" + S.res.run));
$("btnCsv").addEventListener("click", exportCsv);
$("btnPdf").addEventListener("click", () => { switchTab("tabReport"); buildReport(); });
$("rpBuild").addEventListener("click", () => { const n = buildReport(); toast(`Informe generado (${n} interferencias en alcance)`); });
$("rpPrint").addEventListener("click", () => { if (!$("reportDoc").innerHTML) buildReport(); setTimeout(() => window.print(), 150); });
bindPlan(); bindModalOnce();
$("btnSaveRules").addEventListener("click", saveRules);
window.addEventListener("resize", () => { if (S.res) drawPlan(); });

(async function init() {
  try {
    await api("/api/ping");
    $("engineBadge").textContent = "motor: ifcopenshell OK";
    $("engineBadge").className = "px-2.5 py-1 rounded-full bg-emerald-600 text-white font-semibold";
    await loadFiles();
    await loadRules();
  } catch (e) {
    $("offlineBanner").classList.remove("hidden");
    $("engineBadge").textContent = "motor: sin conexión";
    $("engineBadge").className = "px-2.5 py-1 rounded-full bg-rose-600 text-white font-semibold";
  }
})();
