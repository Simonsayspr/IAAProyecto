/* ── Estado global ─────────────────────────────────────────────────────────── */
const state = {
  allCandidates: [],
  filtered:      [],
  students:      [],     // lista rápida de alumnos (students_ready)
  studentsMap:   {},     // {RUT: {NOMBRE_COMPLETO, PGA, N_VECES_AYUDANTE, ...}}
  studentInfo:   {},
  kpiData:       null,
  cursosList:    [],
  taTypeCounts:  {},     // {Docente: N, Corrección: N, ...}
  profesorMap:   {},     // {NRC: {nombre, rut}}
  useDemoMode:   true,
  loading:       false,
  aiMode:        false,
  viewMode:      "students",   // "students" | "candidates"
  filters: {
    escuela:     "",
    curso:       "",
    notaMin:     5.0,
    pgaMin:      0.0,
    dias:        {},
    exAyudante:  "",
  },
  pagination: { page: 1, pageSize: 20 },
};

const API = window.location.port && window.location.port !== "80"
  ? "http://localhost:8000"
  : "/api";

/* ── DOM refs ──────────────────────────────────────────────────────────────── */
const $ = id => document.getElementById(id);
const dom = {
  statusBadge:     $("statusBadge"),
  statusText:      $("statusText"),
  loadingPanel:    $("loadingPanel"),
  loadingStepText: $("loadingStepText"),
  btnRun:          $("btnRun"),
  btnAI:           $("btnAI"),
  btnExport:       $("btnExport"),
  btnKpis:         $("btnKpis"),
  btnDemo:         $("btnDemo"),
  btnSheets:       $("btnSheets"),
  btnClear:        $("btnClear"),
  fEscuela:        $("fEscuela"),
  fExAyudante:     $("fExAyudante"),
  fNotaMinima:     $("fNotaMinima"),
  fPgaMinima:      $("fPgaMinima"),
  notaMinimaVal:   $("notaMinimaVal"),
  pgaMinimaVal:    $("pgaMinimaVal"),
  dayBtns:         document.querySelectorAll(".day-btn"),
  dayWindows:      $("dayWindows"),
  kpiSection:      $("kpiSection"),
  statsSection:    $("statsSection"),
  resultsSection:  $("resultsSection"),
  sidebarCount:    $("sidebarCount"),
  countFiltered:   $("countFiltered"),
  countTotal:      $("countTotal"),
  tableCount:      $("tableCount"),
  candidatesBody:  $("candidatesBody"),
  statCandidatos:  $("statCandidatos"),
  statAsignados:   $("statAsignados"),
  statSecciones:   $("statSecciones"),
  featChart:       $("featChart"),
  modalOverlay:    $("modalOverlay"),
  modalClose:      $("modalClose"),
  paginationSection: $("paginationSection"),
  btnPrev:         $("btnPrev"),
  btnNext:         $("btnNext"),
  pageInfo:        $("pageInfo"),
};

/* ── School map ────────────────────────────────────────────────────────────── */
const SCHOOL_MAP = {
  "IN":   "Ingeniería",
  "IIN":  "Ing. Industrial",
  "ICI":  "Ing. Civil",
  "ICM":  "Ing. Comercial",
  "ICE":  "Ing. Eléctrica",
  "IIC":  "Ing. Informática",
  "ME":   "Medicina",
  "MED":  "Medicina",
  "ENF":  "Enfermería",
  "ODP":  "Odontología",
  "AD":   "Administración",
  "ADE":  "Administración",
  "MAT":  "Matemáticas",
  "FIS":  "Física",
  "QUI":  "Química",
  "BIO":  "Biología",
  "CC":   "Ciencias",
  "DE":   "Derecho",
  "AR":   "Arquitectura",
  "DIS":  "Diseño",
  "COM":  "Comunicaciones",
  "EDU":  "Educación",
  "PSI":  "Psicología",
  "FIL":  "Filosofía",
  "HIS":  "Historia",
  "ECO":  "Economía",
  "SOC":  "Sociología",
  "MUS":  "Música",
  "LIN":  "Lingüística",
};

function extractPrefix(materia) {
  const m = String(materia).match(/^([A-Z]+)/);
  return m ? m[1] : "";
}

function getSchoolName(prefix) {
  return SCHOOL_MAP[prefix] || prefix;
}

/* ── Day labels ────────────────────────────────────────────────────────────── */
const DAY_ORDER  = ["LUNES","MARTES","MIERCOLES","JUEVES","VIERNES","SABADO"];
const DAY_LABELS = { LUNES:"Lunes", MARTES:"Martes", MIERCOLES:"Miércoles", JUEVES:"Jueves", VIERNES:"Viernes", SABADO:"Sábado" };

/* ── Helpers ───────────────────────────────────────────────────────────────── */
function fmt(val, decimals = 2) {
  if (val == null) return "—";
  return Number(val).toFixed(decimals);
}

function parseTimeStr(str) {
  if (!str) return null;
  const [h, m] = str.split(":").map(Number);
  return h * 60 + (m || 0);
}

function parseSlotStr(str) {
  const m = String(str || "").match(/(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})/);
  if (!m) return null;
  return { start: +m[1]*60 + +m[2], end: +m[3]*60 + +m[4] };
}

function isAvailable(rut, dia, wantStart, wantEnd) {
  const slots = state.studentInfo[rut]?.ocupado?.[dia] || [];
  for (const s of slots) {
    const p = parseSlotStr(s);
    if (p && p.start < wantEnd && p.end > wantStart) return false;
  }
  return true;
}

function setStatus(type, text) {
  dom.statusBadge.className = `badge badge-${type}`;
  dom.statusText.textContent = text;
}

/* ── Loading panel ─────────────────────────────────────────────────────────── */
const STEP_TEXT = {
  1: "Cargando planillas de Google Sheets…",
  2: "Cruzando datos y filtrando candidatos elegibles…",
  3: "Calculando disponibilidad horaria…",
};

function setLoadingStep(step) {
  dom.loadingStepText.textContent = STEP_TEXT[step] || "Procesando…";
  [1,2,3].forEach(n => {
    $(`stepDot${n}`).className = n < step ? "step-dot done" : n === step ? "step-dot active" : "step-dot";
    if (n < 3) $(`stepLine${n}`).className = n < step ? "step-line done" : "step-line";
  });
}

function showLoading() {
  state.loading       = true;
  state.aiMode        = false;
  state.viewMode      = "students";
  state.allCandidates = [];
  state.students      = [];
  state.studentsMap   = {};
  state.filtered      = [];
  state.kpiData       = null;

  dom.loadingPanel.style.display   = "";
  dom.statsSection.style.display   = "none";
  dom.resultsSection.style.display = "none";
  dom.sidebarCount.style.display   = "none";
  dom.kpiSection.style.display     = "none";
  dom.btnRun.disabled               = true;
  dom.btnAI.style.display           = "none";
  dom.btnAI.textContent             = "Aplicar Modo IA";
  dom.btnAI.disabled                = false;
  dom.btnKpis.style.display         = "none";
  dom.btnExport.style.display       = "none";
  setLoadingStep(1);
}

function hideLoading() {
  state.loading = false;
  dom.loadingPanel.style.display = "none";
  dom.btnRun.disabled = false;
}

/* ── API ───────────────────────────────────────────────────────────────────── */
async function checkHealth() {
  try {
    const res = await fetch(`${API}/health`);
    if (!res.ok) throw new Error();
    setStatus("ok", "Backend activo");
  } catch {
    setStatus("error", "Backend no disponible");
  }
}

async function runPipeline() {
  if (state.loading) return;
  showLoading();
  setStatus("checking", "Ejecutando consulta…");

  try {
    const res = await fetch(`${API}/pipeline/run`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        usar_demo:      state.useDemoMode,
        nota_minima:    parseFloat(dom.fNotaMinima.value),
        max_ayudantias: 2,
        weight_preset:  document.getElementById("fWeightPreset")?.value || "balanced",
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw Object.assign(new Error(err.detail || res.statusText), { is503: res.status === 503 });
    }

    const reader = res.body.getReader();
    const dec    = new TextDecoder();
    let   buf    = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const chunks = buf.split("\n\n");
      buf = chunks.pop();

      for (const chunk of chunks) {
        if (!chunk.startsWith("data: ")) continue;
        let ev;
        try { ev = JSON.parse(chunk.slice(6)); } catch { continue; }

        if (ev.type === "progress") {
          setLoadingStep(ev.step);
        } else if (ev.type === "students_ready") {
          ingestStudents(ev);
          hideLoading();
          setStatus("checking", "Cargando cruce de ramos…");
        } else if (ev.type === "candidates_ready") {
          ingestCandidates(ev);
          setStatus("ok", "Datos de ramos listos");
        } else if (ev.type === "error") {
          throw Object.assign(new Error(ev.detail), { is503: ev.status === 503 });
        }
      }
    }
  } catch(e) {
    setStatus("error","Error al cargar datos");
    alert(`${e.is503?"Permiso faltante en Google Cloud":"Error al ejecutar la consulta"}:\n\n${e.message}`);
    hideLoading();
  }
}

/* ── Modo IA ───────────────────────────────────────────────────────────────── */
async function runAI() {
  if (state.loading) return;
  dom.btnAI.disabled    = true;
  dom.btnAI.textContent = "Aplicando IA…";
  setStatus("checking", "Ejecutando modelo IA…");

  try {
    const res = await fetch(`${API}/pipeline/score`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ candidates: state.allCandidates }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }

    const reader = res.body.getReader();
    const dec    = new TextDecoder();
    let   buf    = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const chunks = buf.split("\n\n");
      buf = chunks.pop();

      for (const chunk of chunks) {
        if (!chunk.startsWith("data: ")) continue;
        let ev;
        try { ev = JSON.parse(chunk.slice(6)); } catch { continue; }

        if (ev.type === "scored") {
          ingestAIScores(ev);
          setStatus("ok", "Modo IA aplicado");
        } else if (ev.type === "kpis_ready") {
          state.kpiData = ev;
          dom.btnKpis.style.display   = "";
          dom.btnKpis.textContent     = "Métricas IA";
        } else if (ev.type === "error") {
          throw new Error(ev.detail);
        }
      }
    }
  } catch(e) {
    setStatus("error", "Error en modelo IA");
    alert(`Error al aplicar Modo IA:\n\n${e.message}`);
  } finally {
    dom.btnAI.disabled    = false;
    dom.btnAI.textContent = "Modo IA aplicado ✓";
  }
}

/* ── Ingest ────────────────────────────────────────────────────────────────── */
function ingestStudents(data) {
  // Resetear candidatos para activar modo-estudiante
  state.allCandidates = [];
  state.filtered      = [];
  state.aiMode        = false;
  state.viewMode      = "students";

  state.students = data.students || [];
  state.studentsMap = {};
  state.students.forEach(s => { state.studentsMap[String(s.RUT)] = s; });

  // Populate school filter from CARRERA field
  const carreras = [...new Set(state.students.map(s => s.CARRERA).filter(Boolean))].sort();
  if (carreras.length) {
    dom.fEscuela.innerHTML = '<option value="">Todas las escuelas</option>';
    carreras.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      dom.fEscuela.appendChild(opt);
    });
  }

  dom.statCandidatos.textContent = data.n_students ?? state.students.length;
  dom.statAsignados.textContent  = "—";
  dom.statSecciones.textContent  = "—";
  dom.statsSection.style.display   = "";
  dom.resultsSection.style.display = "";
  dom.sidebarCount.style.display   = "";
  dom.countTotal.textContent = state.students.length;

  // Ocultar botones de modo candidato
  dom.btnAI.style.display     = "none";
  dom.btnExport.style.display = "none";
  dom.btnKpis.style.display   = "none";

  applyFilters();
}

function ingestCandidates(data) {
  state.allCandidates = data.candidates || [];
  state.studentInfo   = data.student_info || {};
  state.taTypeCounts  = data.ta_type_counts || {};
  state.profesorMap   = data.profesor_map || {};

  // Enrich candidates with names from studentsMap
  if (Object.keys(state.studentsMap).length) {
    state.allCandidates.forEach(c => {
      const s = state.studentsMap[String(c.RUT)];
      if (s && !c.NOMBRE_COMPLETO) c.NOMBRE_COMPLETO = s.NOMBRE_COMPLETO || "";
    });
  }

  buildSchoolAndCoursePicker(data.cursos || []);

  dom.statCandidatos.textContent = data.n_candidatos ?? state.allCandidates.length;
  dom.statAsignados.textContent  = data.n_asignados  ?? "—";
  dom.statSecciones.textContent  = data.n_secciones  ?? "—";

  // Render TA type breakdown
  renderTaTypeCounts(state.taTypeCounts);

  // Dejar que applyFilters() decida qué vista mostrar
  applyFilters();
}

function renderTaTypeCounts(counts) {
  const section = document.getElementById("taTypeSection");
  if (!section) return;

  const docente = counts["Docente"] || 0;
  const correccion = counts["Corrección"] || counts["Correccion"] || 0;
  const laboratorio = counts["Laboratorio"] || 0;
  const total = Object.values(counts).reduce((s, v) => s + v, 0);
  const otro = total - docente - correccion - laboratorio;

  document.getElementById("statDocente").textContent = docente;
  document.getElementById("statCorreccion").textContent = correccion;
  document.getElementById("statLaboratorio").textContent = laboratorio;
  document.getElementById("statOtroTipo").textContent = Math.max(0, otro);

  section.style.display = total > 0 ? "" : "none";
}

function ingestAIScores(data) {
  state.allCandidates = data.candidates || [];
  state.aiMode   = true;
  state.viewMode = "candidates";
  dom.statAsignados.textContent = data.n_asignados ?? "—";
  dom.statSecciones.textContent = data.n_secciones ?? "—";
  applyFilters();
}

/* ── School + Course picker ────────────────────────────────────────────────── */
function buildSchoolAndCoursePicker(cursos) {
  state.cursosList = cursos.map(c => ({
    key:    `${c.MATERIA}-${c.CURSO}`,
    label:  `${c.MATERIA} ${c.CURSO}`,
    titulo: c.TITULO || "",
    prefix: extractPrefix(c.MATERIA),
  }));

  // Populate school select con prefijos de MATERIA (modo candidato)
  const prefixes = [...new Set(state.cursosList.map(c => c.prefix))].sort();
  dom.fEscuela.innerHTML = '<option value="">Todas las escuelas</option>';
  prefixes.forEach(p => {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = `${getSchoolName(p)} (${p})`;
    dom.fEscuela.appendChild(opt);
  });

  // Si el filtro actual no coincide con las nuevas opciones, limpiar
  if (state.filters.escuela && !prefixes.includes(state.filters.escuela)) {
    state.filters.escuela = "";
    dom.fEscuela.value = "";
  }
}

function filteredCursos() {
  const esc = state.filters.escuela;
  return esc ? state.cursosList.filter(c => c.prefix === esc) : state.cursosList;
}

/* ── Course picker logic ───────────────────────────────────────────────────── */
function setupCursoPicker() {
  const input    = $("fCursoInput");
  const dropdown = $("cursoDropdown");
  const clearBtn = $("cursoClear");
  const picker   = $("cursoPicker");

  function render(options) {
    dropdown.innerHTML = options.length
      ? options.slice(0, 60).map(c => `
          <div class="curso-option" data-key="${c.key}">
            <strong>${c.label}</strong><small>${c.titulo}</small>
          </div>`).join("")
      : `<div class="curso-option empty-option">Sin resultados</div>`;
    dropdown.style.display = "";
  }

  function filter(q) {
    const base = filteredCursos();
    if (!q.trim()) return base;
    const ql = q.toLowerCase();
    return base.filter(c => c.label.toLowerCase().includes(ql) || c.titulo.toLowerCase().includes(ql));
  }

  const close = () => { dropdown.style.display = "none"; };

  input.addEventListener("focus", () => { if (state.cursosList.length) render(filter(input.value)); });
  input.addEventListener("input", () => {
    clearBtn.style.display = input.value ? "" : "none";
    if (state.cursosList.length) render(filter(input.value));
    if (!input.value) { state.filters.curso = ""; applyFilters(); }
  });

  dropdown.addEventListener("mousedown", e => {
    const opt = e.target.closest(".curso-option[data-key]");
    if (!opt) return;
    e.preventDefault();
    const found = state.cursosList.find(c => c.key === opt.dataset.key);
    if (found) {
      input.value = `${found.label} — ${found.titulo}`;
      state.filters.curso = found.key;
      clearBtn.style.display = "";
      close();
      applyFilters();
    }
  });

  clearBtn.addEventListener("click", () => {
    input.value = "";
    state.filters.curso = "";
    clearBtn.style.display = "none";
    close();
    applyFilters();
  });

  document.addEventListener("click", e => { if (!picker.contains(e.target)) close(); });
}

/* ── Multi-day windows ─────────────────────────────────────────────────────── */
function setupDayButtons() {
  dom.dayBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      const day = btn.dataset.day;
      if (state.filters.dias[day]) {
        delete state.filters.dias[day];
        btn.classList.remove("active");
        const row = dom.dayWindows.querySelector(`.day-window-row[data-day="${day}"]`);
        if (row) row.remove();
      } else {
        state.filters.dias[day] = { inicio: "", fin: "" };
        btn.classList.add("active");
        addDayRow(day);
      }
      applyFilters();
    });
  });
}

function addDayRow(day) {
  const row = document.createElement("div");
  row.className = "day-window-row";
  row.dataset.day = day;
  row.innerHTML = `
    <span class="day-window-label">${DAY_LABELS[day]}</span>
    <input type="time" class="dw-start" data-day="${day}" />
    <span class="day-window-sep">→</span>
    <input type="time" class="dw-end"   data-day="${day}" />`;

  // Insert in calendar order
  const dayIdx = DAY_ORDER.indexOf(day);
  const existing = [...dom.dayWindows.querySelectorAll(".day-window-row")];
  const after = existing.find(r => DAY_ORDER.indexOf(r.dataset.day) > dayIdx);
  after ? dom.dayWindows.insertBefore(row, after) : dom.dayWindows.appendChild(row);

  row.querySelector(".dw-start").addEventListener("change", e => {
    if (state.filters.dias[day]) { state.filters.dias[day].inicio = e.target.value; applyFilters(); }
  });
  row.querySelector(".dw-end").addEventListener("change", e => {
    if (state.filters.dias[day]) { state.filters.dias[day].fin = e.target.value; applyFilters(); }
  });
}

/* ── KPI toggle ────────────────────────────────────────────────────────────── */
function toggleKpis() {
  const visible = dom.kpiSection.style.display !== "none";
  if (visible) {
    dom.kpiSection.style.display = "none";
    dom.btnKpis.textContent = "Métricas IA";
  } else {
    if (state.kpiData) { renderKPIs(state.kpiData.kpi1, state.kpiData.kpi2, state.kpiData.kpi3); renderFeatImportance(state.kpiData.feature_importance); }
    dom.kpiSection.style.display = "";
    dom.btnKpis.textContent = "Ocultar métricas";
    dom.kpiSection.scrollIntoView({ behavior:"smooth", block:"start" });
  }
}

/* ── KPIs ──────────────────────────────────────────────────────────────────── */
function renderKPIs(kpi1, kpi2, kpi3) {
  renderOneKPI("kpi1", kpi1, 0, 1, v => v?.toFixed(4));
  renderOneKPI("kpi2", kpi2, 0, 7, v => v?.toFixed(2) + " / 7.0");
  renderOneKPI("kpi3", kpi3, 0, 1, v => (v*100)?.toFixed(1) + "%");
}

function renderOneKPI(id, kpi, sMin, sMax, fmtFn) {
  if (!kpi) return;
  const val = kpi.valor, meta = kpi.meta ?? 0, base = kpi.baseline ?? 0;
  const pct    = val != null ? Math.min(100,((val-sMin)/(sMax-sMin))*100) : 0;
  const metaPct = Math.min(100,((meta-sMin)/(sMax-sMin))*100);
  $(`${id}Nombre`).textContent = kpi.nombre||"";
  $(`${id}Valor`).textContent  = val != null ? fmtFn(val) : "Sin datos";
  $(`${id}Fill`).style.width   = pct+"%";
  $(`${id}Mark`).style.left    = metaPct+"%";
  $(`${id}Base`).textContent   = `Baseline: ${base}`;
  $(`${id}Meta`).textContent   = `Meta: ${meta}`;
  const el = $(`${id}Estado`);
  const est = kpi.estado || (val==null?"SIN_DATOS": val>=meta?"OPTIMO": val>=meta*0.85?"SUFICIENTE":"CRITICO");
  el.textContent = est.replace(/_/g," "); el.className = `kpi-estado estado-${est}`;
}

const FEAT_LABELS = {
  NOTA_RAMO:"Nota en el ramo", PGA:"Prom. general (PGA)", CARGA_ACTUAL:"Carga académica",
  N_VECES_AYUDANTE:"Veces ayudante", AVANCE_MALLA:"Avance malla",
  PROM_EVAL_PREVIA:"Eval. previa", POSTULANTE_ACTUAL:"Postulante actual",
};

function renderFeatImportance(fi) {
  dom.featChart.innerHTML = "";
  if (!fi) { dom.featChart.textContent = "Sin datos."; return; }
  const entries = Object.entries(fi).sort((a,b)=>b[1]-a[1]);
  const max = entries[0]?.[1] || 1;
  entries.forEach(([k,v]) => {
    dom.featChart.insertAdjacentHTML("beforeend", `
      <div class="feat-row">
        <span class="feat-label" title="${FEAT_LABELS[k]||k}">${FEAT_LABELS[k]||k}</span>
        <div class="feat-bar-wrap"><div class="feat-bar" style="width:${(v/max*100).toFixed(1)}%"></div></div>
        <span class="feat-pct">${(v*100).toFixed(1)}%</span>
      </div>`);
  });
}

/* ── Weight Presets ────────────────────────────────────────────────────────── */
const WEIGHT_PRESETS = {
  balanced:   { label:"Equilibrado",           description:"Balance entre nota, promedio, experiencia y avance curricular",
                weights:{ NOTA_RAMO:0.40, PGA:0.25, N_VECES_AYUDANTE:0.10, AVANCE_MALLA:0.15, CARGA_ACTUAL:0.05, POSTULANTE_ACTUAL:0.05 }},
  academic:   { label:"Rendimiento académico",  description:"Prioriza nota en el ramo y promedio general",
                weights:{ NOTA_RAMO:0.55, PGA:0.30, N_VECES_AYUDANTE:0.05, AVANCE_MALLA:0.05, CARGA_ACTUAL:0.03, POSTULANTE_ACTUAL:0.02 }},
  experience: { label:"Experiencia previa",      description:"Prioriza experiencia como ayudante y postulación activa",
                weights:{ NOTA_RAMO:0.25, PGA:0.15, N_VECES_AYUDANTE:0.35, AVANCE_MALLA:0.10, CARGA_ACTUAL:0.05, POSTULANTE_ACTUAL:0.10 }},
  curriculum: { label:"Avance curricular",       description:"Prioriza alumnos avanzados en la malla con baja carga",
                weights:{ NOTA_RAMO:0.25, PGA:0.15, N_VECES_AYUDANTE:0.05, AVANCE_MALLA:0.40, CARGA_ACTUAL:0.10, POSTULANTE_ACTUAL:0.05 }},
};

const WEIGHT_LABELS = {
  NOTA_RAMO:"Nota en el ramo", PGA:"Promedio general",
  N_VECES_AYUDANTE:"Experiencia previa", AVANCE_MALLA:"Avance malla",
  CARGA_ACTUAL:"Disponibilidad (carga)", POSTULANTE_ACTUAL:"Postulante actual",
};

function renderWeightBars(presetKey) {
  const bars = document.getElementById("weightBars");
  if (!bars) return;
  const preset = WEIGHT_PRESETS[presetKey];
  if (!preset) return;
  const w = preset.weights;
  bars.innerHTML = Object.entries(w)
    .sort((a,b) => b[1] - a[1])
    .map(([k,v]) => `
      <div class="weight-bar-row">
        <span class="weight-bar-label">${WEIGHT_LABELS[k]||k}</span>
        <div class="weight-bar-track"><div class="weight-bar-fill" style="width:${(v*100).toFixed(0)}%"></div></div>
        <span class="weight-bar-pct">${(v*100).toFixed(0)}%</span>
      </div>`).join("");
}

function setupWeightPresets() {
  const sel = document.getElementById("fWeightPreset");
  const desc = document.getElementById("presetDescription");
  if (!sel) return;
  sel.addEventListener("change", () => {
    const key = sel.value;
    const p = WEIGHT_PRESETS[key];
    if (desc && p) desc.textContent = p.description;
    renderWeightBars(key);
  });
  renderWeightBars("balanced");
}

/* ── Filters ───────────────────────────────────────────────────────────────── */

function hasActiveFilters() {
  return (
    state.filters.escuela !== "" ||
    state.filters.curso !== "" ||
    state.filters.exAyudante !== "" ||
    Object.keys(state.filters.dias).length > 0 ||
    Math.abs(state.filters.notaMin - 5.0) > 0.05 ||
    state.filters.pgaMin > 0.05
  );
}

function applyFilters() {
  state.filters.notaMin = parseFloat(dom.fNotaMinima.value) || 0;
  state.filters.pgaMin  = parseFloat(dom.fPgaMinima.value)  || 0;

  const active        = hasActiveFilters();
  const hasCandidates = state.allCandidates.length > 0;

  // ── Determinar vista ──────────────────────────────────────────────────────
  // Filtros activos + datos de ramos listos → vista candidatos
  // Sin filtros (o sin datos de ramos) → vista estudiantes
  if (active && hasCandidates) {
    state.viewMode = "candidates";
  } else {
    state.viewMode = "students";
  }

  // ── Vista CANDIDATOS ──────────────────────────────────────────────────────
  if (state.viewMode === "candidates") {
    let result = state.allCandidates;

    if (state.filters.escuela)
      result = result.filter(c => extractPrefix(c.MATERIA || "") === state.filters.escuela);

    if (state.filters.curso) {
      const [mat, cur] = state.filters.curso.split("-");
      result = result.filter(c => c.MATERIA === mat && c.CURSO === cur);
    }

    if (state.filters.notaMin > 1.0)
      result = result.filter(c => (c.NOTA_RAMO ?? 0) >= state.filters.notaMin);

    if (state.filters.pgaMin > 0.1)
      result = result.filter(c => (c.PGA ?? 0) >= state.filters.pgaMin);

    if (state.filters.exAyudante === "si")
      result = result.filter(c => (c.N_VECES_AYUDANTE ?? 0) > 0);
    else if (state.filters.exAyudante === "no")
      result = result.filter(c => (c.N_VECES_AYUDANTE ?? 0) === 0);

    const diasKeys = Object.keys(state.filters.dias);
    if (diasKeys.length > 0) {
      result = result.filter(c => diasKeys.every(dia => {
        const w = state.filters.dias[dia];
        const hI = w.inicio ? parseTimeStr(w.inicio) : null;
        const hF = w.fin    ? parseTimeStr(w.fin)    : null;
        if (hI !== null && hF !== null && hF > hI) return isAvailable(c.RUT, dia, hI, hF);
        return true;
      }));
    }

    state.filtered = result;
    state.pagination.page = 1;
    renderTable();
    dom.countFiltered.textContent = state.filtered.length;
    dom.countTotal.textContent    = state.allCandidates.length;
    // Mostrar botones de modo candidato
    dom.btnAI.style.display     = "";
    dom.btnExport.style.display = "";
    return;
  }

  // ── Vista ESTUDIANTES ─────────────────────────────────────────────────────
  let result = state.students;

  // Escuela: filtrar por CARRERA (antes de que carguen candidatos)
  // o por referencia cruzada si ya hay candidatos (pero filtros borrados)
  if (state.filters.escuela)
    result = result.filter(s => s.CARRERA === state.filters.escuela);

  if (state.filters.pgaMin > 0.1)
    result = result.filter(s => (s.PGA ?? 0) >= state.filters.pgaMin);

  if (state.filters.exAyudante === "si")
    result = result.filter(s => (s.N_VECES_AYUDANTE ?? 0) > 0);
  else if (state.filters.exAyudante === "no")
    result = result.filter(s => (s.N_VECES_AYUDANTE ?? 0) === 0);

  state.filtered = result;
  state.pagination.page = 1;
  renderTable();
  dom.countFiltered.textContent = state.filtered.length;
  dom.countTotal.textContent    = state.students.length;

  // En modo estudiante ocultar botones de IA/exportar
  dom.btnAI.style.display     = "none";
  dom.btnExport.style.display = "none";
}

/* ── Score justification ───────────────────────────────────────────────────── */
function scoreJustification(c) {
  const parts = [];
  if (c.NOTA_RAMO != null) parts.push(`Nota ${fmt(c.NOTA_RAMO, 1)}`);
  if (c.PGA       != null) parts.push(`PGA ${fmt(c.PGA, 1)}`);
  const exp = c.N_VECES_AYUDANTE ?? 0;
  if (exp > 0) parts.push(`${exp}× ayudante`);
  else if (c.POSTULANTE_ACTUAL)  parts.push("postulante");
  return parts.join(" · ");
}

/* ── Aptitude tag ──────────────────────────────────────────────────────────── */
function aptitudeTag(c) {
  const pct = Math.round((c.SCORE ?? 0) * 100);
  if (state.aiMode && c.ASIGNADO === 1)
    return `<span class="tag-aptitud tag-seleccionado">${pct}% · Seleccionado</span>`;
  if (state.aiMode)
    return `<span class="tag-aptitud tag-${pct >= 70 ? "recomendado" : "candidato"}">${pct}% rec.</span>`;
  if ((c.SCORE ?? 0) >= 0.75)
    return `<span class="tag-aptitud tag-recomendado">★ Alto puntaje</span>`;
  return `<span class="tag-aptitud tag-candidato">Candidato</span>`;
}

/* ── AI justification (short for table) ───────────────────────────────────── */
function aiJustification(c) {
  const parts = [];
  const nota = c.NOTA_RAMO ?? 0;
  const pga  = c.PGA ?? 0;
  const exp  = c.N_VECES_AYUDANTE ?? 0;

  if (c.ES_CURSO_NUEVO) parts.push(`Nota inferida ${nota.toFixed(1)}`);
  else if (nota >= 6.0)  parts.push(`Nota ${nota.toFixed(1)}`);
  else if (nota >= 5.5)  parts.push(`Nota ${nota.toFixed(1)}`);
  else                    parts.push(`Nota ${nota.toFixed(1)}`);

  parts.push(`PGA ${pga.toFixed(1)}`);

  if (exp > 0) parts.push(`${exp}× ayudante`);
  else if (c.POSTULANTE_ACTUAL) parts.push("postulante");

  return parts.join(" · ");
}

/* ── AI detailed explanation (for profile modal) ──────────────────────────── */
function aiDetailedExplanation(c, allForRUT) {
  const pct = Math.round((c.SCORE ?? 0) * 100);
  const nota = c.NOTA_RAMO ?? 0;
  const pga  = c.PGA ?? 0;
  const exp  = c.N_VECES_AYUDANTE ?? 0;
  const avance = c.AVANCE_MALLA ?? 0;
  const carga  = c.CARGA_ACTUAL ?? 0;
  const esNuevo = c.ES_CURSO_NUEVO || false;

  // Get current preset weights
  const presetSel = document.getElementById("fWeightPreset");
  const presetKey = presetSel ? presetSel.value : "balanced";
  const preset = WEIGHT_PRESETS[presetKey] || WEIGHT_PRESETS.balanced;
  const w = preset.weights;

  // Calculate contribution of each factor
  const factors = [];

  // 1. Nota en el ramo
  const notaContrib = (nota / 7.0) * (w.NOTA_RAMO || 0);
  if (esNuevo)
    factors.push({ label:"Nota inferida (cursos requisito)", value:nota, max:7, contribution:notaContrib, icon:"🔮" });
  else
    factors.push({ label:"Nota en el ramo", value:nota, max:7, contribution:notaContrib, icon:"📝" });

  // 2. PGA
  const pgaContrib = (pga / 7.0) * (w.PGA || 0);
  factors.push({ label:"Promedio general (PGA)", value:pga, max:7, contribution:pgaContrib, icon:"📊" });

  // 3. Experiencia
  const expContrib = (Math.min(exp, 4) / 4.0) * (w.N_VECES_AYUDANTE || 0);
  factors.push({ label:"Experiencia previa", value:`${exp} vec${exp!==1?"es":""}`, max:null, contribution:expContrib, icon:"🎓" });

  // 4. Avance malla
  const avanceContrib = avance * (w.AVANCE_MALLA || 0);
  factors.push({ label:"Avance curricular", value:`${(avance*100).toFixed(0)}%`, max:null, contribution:avanceContrib, icon:"📈" });

  // 5. Carga
  const cargaContrib = (1 - Math.min(carga, 8) / 8.0) * (w.CARGA_ACTUAL || 0);
  factors.push({ label:"Disponibilidad (carga)", value:`${carga} ramos`, max:null, contribution:cargaContrib, icon:"📅" });

  // 6. Postulante
  const postContrib = (c.POSTULANTE_ACTUAL ? 1 : 0) * (w.POSTULANTE_ACTUAL || 0);
  if (c.POSTULANTE_ACTUAL)
    factors.push({ label:"Postuló este período", value:"Sí", max:null, contribution:postContrib, icon:"✋" });

  const totalContrib = factors.reduce((s, f) => s + f.contribution, 0);
  const maxBar = Math.max(...factors.map(f => f.contribution), 0.01);

  // Build the explanation
  let html = `<div class="ai-score-header">
    <span class="ai-score-pct">${pct}%</span>
    <span class="ai-score-label">recomendado · Perfil: ${preset.label}</span>
  </div>`;

  // Ranking context
  if (allForRUT && allForRUT.length > 1) {
    const rank = allForRUT.findIndex(x => x.NRC === c.NRC && x.MATERIA === c.MATERIA) + 1;
    html += `<p class="ai-rank-note">Ramo #${rank} de ${allForRUT.length} opciones para este alumno</p>`;
  }

  if (esNuevo) {
    html += `<div class="ai-new-course-badge">Curso nuevo — nota inferida desde cursos requisito</div>`;
  }

  html += `<div class="ai-factors">`;
  factors.sort((a, b) => b.contribution - a.contribution);
  for (const f of factors) {
    const barW = Math.max((f.contribution / maxBar) * 100, 2);
    const valStr = f.max != null ? `${Number(f.value).toFixed(1)} / ${f.max}` : f.value;
    html += `<div class="ai-factor-row">
      <span class="ai-factor-icon">${f.icon}</span>
      <span class="ai-factor-label">${f.label}</span>
      <div class="ai-factor-bar-wrap"><div class="ai-factor-bar" style="width:${barW.toFixed(0)}%"></div></div>
      <span class="ai-factor-val">${valStr}</span>
    </div>`;
  }
  html += `</div>`;

  // Summary text
  const topFactor = factors[0];
  const secondFactor = factors[1];
  html += `<p class="ai-summary">El factor más influyente es <strong>${topFactor.label.toLowerCase()}</strong>`;
  if (secondFactor) html += `, seguido de <strong>${secondFactor.label.toLowerCase()}</strong>`;
  html += `.</p>`;

  return html;
}

/* ── Table + pagination ────────────────────────────────────────────────────── */

// Headers de la tabla según el modo actual
const THEAD_STUDENTS   = `<tr><th>#</th><th>RUT</th><th>Nombre</th><th>PGA</th><th>Ayudante prev.</th><th></th></tr>`;
const THEAD_CANDIDATES = `<tr><th>#</th><th>RUT / Nombre</th><th>Curso</th><th>Nota</th><th>PGA</th><th>Ayudante prev.</th><th>Aptitud IA</th><th></th></tr>`;

function renderTable() {
  if (state.viewMode === "candidates") renderCandidateTable();
  else renderStudentTable();
}

/* Tabla modo estudiante — fase inicial antes del cruce de ramos */
function renderStudentTable() {
  // Actualizar cabecera
  document.querySelector("#candidatesTable thead").innerHTML = THEAD_STUDENTS;

  const sorted = [...state.filtered].sort((a, b) => (b.PGA ?? 0) - (a.PGA ?? 0));
  const { page, pageSize } = state.pagination;
  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage   = Math.min(page, totalPages);
  state.pagination.page = safePage;
  const start = (safePage - 1) * pageSize;
  const slice = sorted.slice(start, start + pageSize);

  dom.tableCount.textContent = `${state.filtered.length} estudiante${state.filtered.length !== 1 ? "s" : ""}`;

  if (!slice.length) {
    dom.candidatesBody.innerHTML = `<tr class="empty-row"><td colspan="6">Sin estudiantes con los filtros aplicados.</td></tr>`;
    dom.paginationSection.style.display = "none";
    return;
  }

  dom.candidatesBody.innerHTML = slice.map((s, i) => {
    const n        = start + i + 1;
    const expTimes = s.N_VECES_AYUDANTE ?? 0;
    const expHtml  = expTimes > 0
      ? `<span class="tag-exp tag-exp-si">Sí (${expTimes}×)</span>`
      : `<span class="tag-exp tag-exp-no">No</span>`;
    const nombre   = s.NOMBRE_COMPLETO || "—";
    return `<tr>
      <td style="color:var(--ink-faint);font-size:0.80rem">${n}</td>
      <td style="font-family:'Space Grotesk',sans-serif;font-weight:600">${s.RUT ?? ""}</td>
      <td style="font-size:0.87rem">${nombre}</td>
      <td style="font-weight:700">${fmt(s.PGA, 2)}</td>
      <td>${expHtml}</td>
      <td><button class="btn-profile btn-profile-student" data-rut="${s.RUT}">Ver perfil</button></td>
    </tr>`;
  }).join("");

  dom.candidatesBody.querySelectorAll(".btn-profile-student").forEach(btn => {
    btn.addEventListener("click", () => openStudentModal(btn.dataset.rut));
  });

  dom.paginationSection.style.display = "";
  dom.pageInfo.textContent = `Pág. ${safePage} de ${totalPages}`;
  dom.btnPrev.disabled = safePage <= 1;
  dom.btnNext.disabled = safePage >= totalPages;
}

/* Tabla modo candidato — fase completa con cruce de ramos */
function renderCandidateTable() {
  // Restaurar cabecera completa
  document.querySelector("#candidatesTable thead").innerHTML = THEAD_CANDIDATES;

  const sorted = [...state.filtered].sort((a, b) => (b.SCORE ?? 0) - (a.SCORE ?? 0));
  const { page, pageSize } = state.pagination;
  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage   = Math.min(page, totalPages);
  state.pagination.page = safePage;
  const start = (safePage - 1) * pageSize;
  const slice = sorted.slice(start, start + pageSize);

  dom.tableCount.textContent = `${state.filtered.length} resultado${state.filtered.length !== 1 ? "s" : ""}`;

  if (!slice.length) {
    dom.candidatesBody.innerHTML = `<tr class="empty-row"><td colspan="8">Sin candidatos con los filtros aplicados.</td></tr>`;
    dom.paginationSection.style.display = "none";
    return;
  }

  dom.candidatesBody.innerHTML = slice.map((c, i) => {
    const n        = start + i + 1;
    const expTimes = c.N_VECES_AYUDANTE ?? 0;
    const expHtml  = expTimes > 0
      ? `<span class="tag-exp tag-exp-si">Sí (${expTimes}×)</span>`
      : `<span class="tag-exp tag-exp-no">No</span>`;
    const curso    = `${c.MATERIA ?? ""} ${c.CURSO ?? ""} ${c.SECC != null ? `(S${c.SECC})` : ""}`.trim();
    const nombre   = c.NOMBRE_COMPLETO || "";
    const justifyLine = state.aiMode ? aiJustification(c) : scoreJustification(c);
    const nAceptadas = c.N_ACEPTADAS_ACTUAL ?? 0;
    const maxWarning = nAceptadas >= 3 ? `<span class="tag-max-ta">Máx. 3 TA</span>` : "";
    const estadoPost = c.ESTADO_POSTULACION || "";
    const estadoBadge = estadoPost
      ? `<span class="tag-estado tag-estado-${estadoPost.toLowerCase().includes("aceptad")?"aceptado":estadoPost.toLowerCase().includes("rechazad")?"rechazado":"pendiente"}">${estadoPost}</span>`
      : "";
    return `<tr${nAceptadas >= 3 ? ' class="row-max-ta"' : ""}>
      <td style="color:var(--ink-faint);font-size:0.80rem">${n}</td>
      <td>
        <div style="font-family:'Space Grotesk',sans-serif;font-weight:600">${c.RUT ?? ""}</div>
        ${nombre ? `<div style="font-size:0.76rem;color:var(--ink-faint)">${nombre}</div>` : ""}
        ${maxWarning}
      </td>
      <td>
        <div style="font-weight:600;font-size:0.87rem">${curso}</div>
        <div style="font-size:0.76rem;color:var(--ink-faint)">${c.TITULO ?? ""}</div>
        ${estadoBadge}
      </td>
      <td style="font-weight:700">${fmt(c.NOTA_RAMO, 1)}</td>
      <td>${fmt(c.PGA, 2)}</td>
      <td>${expHtml}</td>
      <td>
        ${aptitudeTag(c)}
        <div class="score-justify">${justifyLine}</div>
      </td>
      <td><button class="btn-profile" data-idx="${start + i}">Ver perfil</button></td>
    </tr>`;
  }).join("");

  dom.candidatesBody.querySelectorAll(".btn-profile").forEach(btn => {
    btn.addEventListener("click", () => openModal(sorted[parseInt(btn.dataset.idx)], sorted[parseInt(btn.dataset.idx)].RUT));
  });

  dom.paginationSection.style.display = "";
  dom.pageInfo.textContent = `Pág. ${safePage} de ${totalPages}`;
  dom.btnPrev.disabled = safePage <= 1;
  dom.btnNext.disabled = safePage >= totalPages;
}

/* ── Modal ─────────────────────────────────────────────────────────────────── */
const DIAS_DISPLAY = [["LUNES","Lun"],["MARTES","Mar"],["MIERCOLES","Mié"],["JUEVES","Jue"],["VIERNES","Vie"],["SABADO","Sáb"]];

function openModal(candidate, rut) {
  const info  = state.studentInfo[rut] || {};
  const s     = state.studentsMap[String(rut)] || {};
  const email = info.email || `${rut}@miuandes.cl`;
  const nombre = candidate.NOMBRE_COMPLETO || s.NOMBRE_COMPLETO || "";

  $("modalTitle").textContent  = nombre ? `Perfil — ${nombre}` : `Perfil — RUT ${rut}`;
  $("mRut").textContent        = rut;
  $("mEmail").textContent      = email;
  $("mEmail").href             = `mailto:${email}`;
  const cursoLabel = candidate.ES_CURSO_NUEVO
    ? `${candidate.MATERIA??""} ${candidate.CURSO??""} — ${candidate.TITULO??""} (curso nuevo)`
    : `${candidate.MATERIA??""} ${candidate.CURSO??""} — ${candidate.TITULO??""}`;
  $("mCurso").textContent      = cursoLabel;
  $("mNota").textContent       = candidate.ES_CURSO_NUEVO
    ? `${fmt(candidate.NOTA_RAMO,1)} (inferida)`
    : fmt(candidate.NOTA_RAMO,1);
  $("mPGA").textContent        = fmt(candidate.PGA,2);
  $("mAvance").textContent     = candidate.AVANCE_MALLA != null ? `${(candidate.AVANCE_MALLA*100).toFixed(0)}%` : "—";
  $("mCarga").textContent      = candidate.CARGA_ACTUAL != null ? `${candidate.CARGA_ACTUAL} ramo(s)` : "—";
  $("mPostulante").textContent = candidate.POSTULANTE_ACTUAL ? "Sí" : "No";

  // Application status
  const estadoPost = candidate.ESTADO_POSTULACION || "";
  const estadoPostEl = $("mEstadoPost");
  if (estadoPostEl) {
    if (estadoPost) {
      const cls = estadoPost.toLowerCase().includes("aceptad") ? "tag-estado-aceptado"
                : estadoPost.toLowerCase().includes("rechazad") ? "tag-estado-rechazado"
                : "tag-estado-pendiente";
      estadoPostEl.innerHTML = `<span class="${cls}">${estadoPost}</span>`;
    } else {
      estadoPostEl.textContent = "No postuló a este curso";
    }
  }

  $("mTipoAyPost").textContent = candidate.TIPO_AYUDANTE_POST || "—";

  // Accepted count + max 3 warning
  const nAceptadas = candidate.N_ACEPTADAS_ACTUAL ?? 0;
  const nAceptEl = $("mNAceptadas");
  if (nAceptEl) {
    if (nAceptadas >= 3) {
      nAceptEl.innerHTML = `<span class="tag-estado-rechazado">${nAceptadas} (máximo alcanzado)</span>`;
    } else if (nAceptadas > 0) {
      nAceptEl.textContent = `${nAceptadas} de 3 máximo`;
    } else {
      nAceptEl.textContent = "0";
    }
  }

  // Professor info
  const profEl = $("mProfesor");
  if (profEl) {
    const nrcKey = String(candidate.NRC || "");
    const profData = state.profesorMap[nrcKey];
    const profPost = candidate.PROFESOR_POST || "";
    if (profData && profData.nombre) {
      profEl.textContent = profData.nombre;
    } else if (profPost) {
      profEl.textContent = profPost;
    } else {
      profEl.textContent = "—";
    }
  }

  // Score
  if (candidate.SCORE != null) {
    const pct = Math.round(candidate.SCORE * 100);
    $("mScore").innerHTML = `<span style="font-size:1.1em;font-weight:700">${pct}%</span> recomendado`;
  } else {
    $("mScore").textContent = "—";
  }

  // AI explanation block
  const aiBlock = $("mAiExplanationBlock");
  const aiEl    = $("mAiExplanation");
  if (aiBlock && aiEl) {
    const allForRUT = state.allCandidates.filter(x => x.RUT === rut).sort((a,b) => (b.SCORE??0) - (a.SCORE??0));
    aiBlock.style.display = "";
    aiEl.innerHTML = aiDetailedExplanation(candidate, allForRUT);
  }

  $("mSched").innerHTML = DIAS_DISPLAY.map(([key,label]) => {
    const slots = info.ocupado?.[key] || [];
    const libre = slots.length === 0;
    return `<div class="sched-day ${libre?"sched-free":"sched-busy"}">
      <div class="day-name">${label}</div>
      ${libre ? '<div class="day-slots">Libre</div>' : slots.map(s=>`<div class="day-slots">${s}</div>`).join("")}
    </div>`;
  }).join("");

  // ── Cursos recomendados para este alumno ──────────────────────────────────
  const allForRUT = state.allCandidates
    .filter(c => c.RUT === rut)
    .sort((a, b) => (b.SCORE ?? 0) - (a.SCORE ?? 0));

  const recEl = $("mRecomendaciones");
  if (recEl) {
    if (!allForRUT.length) {
      recEl.innerHTML = `<p class="ta-empty">Sin datos de recomendación.</p>`;
    } else {
      recEl.innerHTML = allForRUT.slice(0, 6).map((c, i) => {
        const pct = Math.round((c.SCORE ?? 0) * 100);
        const asigBadge = state.aiMode && c.ASIGNADO === 1
          ? `<span class="tag-aptitud tag-seleccionado" style="font-size:0.72rem">✓ Asignado</span>` : "";
        const justLine = state.aiMode ? aiJustification(c) : scoreJustification(c);
        return `<div class="ta-item">
          <span class="ta-periodo" style="min-width:36px;text-align:center">${i===0?"★":"#"+(i+1)}</span>
          <span class="ta-curso" style="flex:1">
            <strong>${c.MATERIA??""} ${c.CURSO??""}</strong> — ${c.TITULO??""} &nbsp;${asigBadge}
            <div style="font-size:0.74rem;color:var(--ink-faint);margin-top:2px">${justLine}</div>
          </span>
          <span class="ta-eval">${state.aiMode ? pct + "%" : "Score: " + fmt(c.SCORE,3)}</span>
        </div>`;
      }).join("");
    }
  }

  const list    = $("mTaList");
  const history = info.ayudantias_previas || [];
  if (!history.length) {
    list.innerHTML = `<p class="ta-empty">Sin experiencia previa como ayudante.</p>`;
  } else {
    const sorted = [...history].sort((a,b)=>(b.periodo||"").localeCompare(a.periodo||""));
    list.innerHTML = sorted.map(ta => `
      <div class="ta-item">
        <span class="ta-periodo">${ta.periodo||"—"}</span>
        <span class="ta-curso">
          ${ta.materia??""} ${ta.curso??""} — ${ta.asignatura??""} <span style="font-size:0.76rem;color:var(--ink-faint)">(${ta.tipo||""})</span>
          ${ta.profesor ? `<div style="font-size:0.72rem;color:var(--ink-faint)">Prof: ${ta.profesor}</div>` : ""}
        </span>
        <span class="ta-eval">${ta.evaluacion!=null?"★ "+Number(ta.evaluacion).toFixed(1):"s/eval"}</span>
      </div>`).join("");
    if (candidate.PROM_EVAL_PREVIA != null)
      list.insertAdjacentHTML("beforeend", `<div style="margin-top:6px;font-size:0.82rem;color:var(--ink-soft)">Promedio evaluaciones: <strong>${Number(candidate.PROM_EVAL_PREVIA).toFixed(2)}</strong></div>`);
  }

  dom.modalOverlay.classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

/* Modal para la vista de estudiantes (sin filtro activo) */
function openStudentModal(rut) {
  const s    = state.studentsMap[String(rut)] || {};
  const info = state.studentInfo[rut] || {};
  const email = info.email || s.email || `${rut}@miuandes.cl`;
  const hasCandidateData = state.allCandidates.length > 0;

  $("modalTitle").textContent  = `Perfil — ${s.NOMBRE_COMPLETO || "RUT " + rut}`;
  $("mRut").textContent        = rut;
  $("mEmail").textContent      = email;
  $("mEmail").href             = `mailto:${email}`;
  $("mCurso").textContent      = hasCandidateData ? "Aplicar filtros para ver ramos" : "Cargando ramos…";
  $("mNota").textContent       = "—";
  $("mPGA").textContent        = fmt(s.PGA, 2);
  $("mAvance").textContent     = "—";
  $("mCarga").textContent      = "—";
  $("mPostulante").textContent = "—";
  if ($("mEstadoPost"))   $("mEstadoPost").textContent   = "—";
  if ($("mTipoAyPost"))   $("mTipoAyPost").textContent   = "—";
  if ($("mNAceptadas"))   $("mNAceptadas").textContent   = "—";
  if ($("mProfesor"))     $("mProfesor").textContent     = "—";
  $("mScore").textContent      = "—";

  // Hide AI explanation in student mode
  const aiBlock = $("mAiExplanationBlock");
  if (aiBlock) aiBlock.style.display = "none";

  // Disponibilidad horaria
  if (Object.keys(info.ocupado || {}).length) {
    $("mSched").innerHTML = DIAS_DISPLAY.map(([key,label]) => {
      const slots = info.ocupado?.[key] || [];
      const libre = slots.length === 0;
      return `<div class="sched-day ${libre?"sched-free":"sched-busy"}">
        <div class="day-name">${label}</div>
        ${libre ? '<div class="day-slots">Libre</div>' : slots.map(sl=>`<div class="day-slots">${sl}</div>`).join("")}
      </div>`;
    }).join("");
  } else {
    $("mSched").innerHTML = `<p style="color:var(--ink-faint);font-size:0.82rem;grid-column:1/-1">
      ${hasCandidateData ? "Sin datos de horario." : "Disponibilidad se cargará con el cruce de ramos."}
    </p>`;
  }

  // Recomendaciones — si ya hay datos de candidatos, mostrar ramos del alumno
  const recEl = $("mRecomendaciones");
  if (recEl) {
    const allForRUT = state.allCandidates.filter(c => c.RUT === rut).sort((a,b) => (b.SCORE??0) - (a.SCORE??0));
    if (allForRUT.length) {
      recEl.innerHTML = allForRUT.slice(0, 6).map((c, i) => {
        const pct = Math.round((c.SCORE ?? 0) * 100);
        return `<div class="ta-item">
          <span class="ta-periodo" style="min-width:36px;text-align:center">${i===0?"★":"#"+(i+1)}</span>
          <span class="ta-curso" style="flex:1">
            <strong>${c.MATERIA??""} ${c.CURSO??""}</strong> — ${c.TITULO??""}
            <div style="font-size:0.74rem;color:var(--ink-faint);margin-top:2px">${scoreJustification(c)}</div>
          </span>
          <span class="ta-eval">${state.aiMode ? pct+"%" : "Score: "+fmt(c.SCORE,3)}</span>
        </div>`;
      }).join("");
    } else {
      recEl.innerHTML = `<p class="ta-empty">${hasCandidateData ? "No hay ramos elegibles para este alumno." : "Cargando ramos…"}</p>`;
    }
  }

  // Historial de ayudantías
  const list    = $("mTaList");
  const history = info.ayudantias_previas || [];
  if (!history.length) {
    list.innerHTML = `<p class="ta-empty">Sin experiencia previa como ayudante.</p>`;
  } else {
    const sortedH = [...history].sort((a, b) => (b.periodo || "").localeCompare(a.periodo || ""));
    list.innerHTML = sortedH.map(ta => `
      <div class="ta-item">
        <span class="ta-periodo">${ta.periodo || "—"}</span>
        <span class="ta-curso">${ta.materia ?? ""} ${ta.curso ?? ""} — ${ta.asignatura ?? ""} <span style="font-size:0.76rem;color:var(--ink-faint)">(${ta.tipo || ""})</span></span>
        <span class="ta-eval">${ta.evaluacion != null ? "★ " + Number(ta.evaluacion).toFixed(1) : "s/eval"}</span>
      </div>`).join("");
  }

  dom.modalOverlay.classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

function closeModal() {
  dom.modalOverlay.classList.add("hidden");
  document.body.style.overflow = "";
}

/* ── Clear filters ─────────────────────────────────────────────────────────── */
function clearFilters() {
  // Course picker
  const inp = $("fCursoInput"), clr = $("cursoClear"), drp = $("cursoDropdown");
  if (inp) { inp.value=""; clr.style.display="none"; drp.style.display="none"; }
  state.filters.curso   = "";
  state.filters.escuela = "";
  dom.fEscuela.value    = "";

  dom.fNotaMinima.value      = "5.0";
  dom.fPgaMinima.value       = "0.0";
  dom.notaMinimaVal.textContent = "5.0";
  dom.pgaMinimaVal.textContent  = "0.0";

  // Clear days
  dom.dayBtns.forEach(b => b.classList.remove("active"));
  dom.dayWindows.innerHTML = "";
  state.filters.dias = {};

  // Clear ex-ayudante
  dom.fExAyudante.value       = "";
  state.filters.exAyudante    = "";

  applyFilters();
}

/* ── Export ────────────────────────────────────────────────────────────────── */
async function exportXLSX() {
  if (!state.filtered.length) { alert("No hay candidatos visibles para exportar."); return; }
  dom.btnExport.disabled = true;
  dom.btnExport.textContent = "Generando…";
  try {
    const res = await fetch(`${API}/pipeline/export`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ candidates: state.filtered }),
    });
    if (!res.ok) { const e=await res.json().catch(()=>({detail:res.statusText})); throw new Error(e.detail||res.statusText); }
    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    const a    = Object.assign(document.createElement("a"), { href:url, download:"ayudantes_filtrados.xlsx" });
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch(e) { alert("Error al exportar: "+e.message); }
  finally { dom.btnExport.disabled=false; dom.btnExport.textContent="Exportar XLSX"; }
}

/* ── Event wiring ──────────────────────────────────────────────────────────── */
function wireEvents() {
  dom.btnRun.addEventListener("click", runPipeline);
  dom.btnAI.addEventListener("click", runAI);
  dom.btnExport.addEventListener("click", exportXLSX);
  dom.btnKpis.addEventListener("click", toggleKpis);

  dom.btnDemo.addEventListener("click", () => {
    state.useDemoMode = true;
    dom.btnDemo.classList.add("active"); dom.btnSheets.classList.remove("active");
  });
  dom.btnSheets.addEventListener("click", () => {
    state.useDemoMode = false;
    dom.btnSheets.classList.add("active"); dom.btnDemo.classList.remove("active");
  });

  dom.fNotaMinima.addEventListener("input", () => { dom.notaMinimaVal.textContent=parseFloat(dom.fNotaMinima.value).toFixed(1); applyFilters(); });
  dom.fPgaMinima.addEventListener("input",  () => { dom.pgaMinimaVal.textContent =parseFloat(dom.fPgaMinima.value).toFixed(1);  applyFilters(); });

  dom.fExAyudante.addEventListener("change", () => {
    state.filters.exAyudante = dom.fExAyudante.value;
    applyFilters();
  });

  // School filter refreshes course picker + applies filters
  dom.fEscuela.addEventListener("change", () => {
    state.filters.escuela = dom.fEscuela.value;
    // Clear course if it belongs to a different school
    if (state.filters.curso) {
      const cur = state.cursosList.find(c => c.key === state.filters.curso);
      if (cur && state.filters.escuela && cur.prefix !== state.filters.escuela) {
        state.filters.curso = "";
        const inp=$("fCursoInput"),clr=$("cursoClear");
        if(inp){inp.value="";clr.style.display="none";}
      }
    }
    applyFilters();
  });

  dom.btnClear.addEventListener("click", clearFilters);

  // Pagination
  dom.btnPrev.addEventListener("click", () => { if(state.pagination.page>1){state.pagination.page--; renderTable();} });
  dom.btnNext.addEventListener("click", () => { state.pagination.page++; renderTable(); });
  document.querySelectorAll(".page-size-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      state.pagination.pageSize = parseInt(btn.dataset.size);
      state.pagination.page = 1;
      document.querySelectorAll(".page-size-btn").forEach(b=>b.classList.remove("active"));
      btn.classList.add("active");
      renderTable();
    });
  });

  dom.modalClose.addEventListener("click", closeModal);
  dom.modalOverlay.addEventListener("click", e => { if(e.target===dom.modalOverlay)closeModal(); });
  document.addEventListener("keydown", e => { if(e.key==="Escape")closeModal(); });
}

/* ── Init ──────────────────────────────────────────────────────────────────── */
async function init() {
  wireEvents();
  setupCursoPicker();
  setupDayButtons();
  setupWeightPresets();
  await checkHealth();
}

init();
