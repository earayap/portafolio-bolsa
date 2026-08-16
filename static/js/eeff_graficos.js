/* EEFF trimestrales — comparación entre acciones por Q, un gráfico de líneas
   por métrica (small multiples: cada métrica tiene su propia escala, nunca
   se mezclan CLP con % ni con veces en un mismo eje). */

const state = { rows: [], tickers: [], hidden: new Set(), charts: {} };

/* Paleta categórica validada (skill dataviz) — las 8 familias completas
   (distribucion.js solo usa 7 porque ahí la cola cae en "Otras"; acá no hay
   cola, cada ticker necesita mantener su propia identidad). Asignada de
   forma ESTABLE por ticker (orden fijo), no por ranking: el color identifica
   a la acción a través del tiempo, no una posición relativa que cambia de
   gráfico en gráfico.

   Con más de 8 series el propio skill de dataviz prohíbe generar un 9no tono
   (indistinguible bajo daltonismo) o colapsarlas todas en un gris "Otras"
   (que es justo el bug que esto reemplaza: 8 acciones quedaban con el mismo
   punto gris, indistinguibles entre sí). En vez de eso, el ticker 9 reutiliza
   el tono del ticker 1 pero con línea punteada, el 10 el del 2, etc. — tono
   + trazo como codificación compuesta, nunca solo color. */
const PALETTE = {
  dark:  ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181", "#d95926"],
  light: ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"],
};
const DASH_PATTERNS = [[], [7, 4]]; // sólido para tickers 1-8, punteado para 9-16

const METRICS = [
  { key: "ingresos", label: "Ingresos", unit: "MM CLP" },
  { key: "utilidad_neta", label: "Utilidad neta", unit: "MM CLP" },
  { key: "roe", label: "ROE", unit: "%" },
  { key: "deuda_patrimonio", label: "Deuda/Patrimonio", unit: "x" },
  { key: "margen_neto", label: "Margen neto", unit: "%" },
  { key: "resultado_operacional", label: "Resultado operacional", unit: "MM CLP" },
  { key: "depreciacion_amortizacion", label: "D&A", unit: "MM CLP" },
  { key: "deuda_financiera", label: "Deuda financiera", unit: "MM CLP" },
  { key: "efectivo_equivalentes", label: "Efectivo y equivalentes", unit: "MM CLP" },
];

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}

function theme() {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}
function themeColors() {
  const light = theme() === "light";
  return {
    grid: light ? "rgba(20,30,60,.07)" : "rgba(255,255,255,.06)",
    tick: light ? "#4a5a72" : "#93a1b8",
  };
}
function colorFor(tickerIndex) {
  const p = PALETTE[theme()];
  return p[tickerIndex % p.length];
}
function dashFor(tickerIndex) {
  const p = PALETTE[theme()];
  const tier = Math.floor(tickerIndex / p.length);
  return DASH_PATTERNS[Math.min(tier, DASH_PATTERNS.length - 1)];
}

function fmtNum(v) {
  if (v == null) return "—";
  return new Intl.NumberFormat("es-CL", { maximumFractionDigits: 2 }).format(v);
}

/* Etiqueta de periodo ordenable cronológicamente: "2025-Q4", "2026-Q1"... */
function periodoKey(r) {
  return `${r.anio}-Q${r.trimestre}`;
}

function buildPeriodos(rows) {
  const set = new Set(rows.map(periodoKey));
  return Array.from(set).sort(); // "2025-Q4" < "2026-Q1" ordena bien como string
}

function renderLegend() {
  const el = document.getElementById("tickerLegend");
  el.innerHTML = state.tickers
    .map((t, i) => {
      const name = state.names[t] || t;
      const off = state.hidden.has(t);
      const dashed = dashFor(i).length > 0;
      const swatch = dashed
        ? `<svg class="chip-swatch" width="16" height="10" viewBox="0 0 16 10"><line x1="0" y1="5" x2="16" y2="5" stroke="${colorFor(i)}" stroke-width="2.5" stroke-dasharray="4,2.5"/></svg>`
        : `<span class="chip-dot" style="background:${colorFor(i)}"></span>`;
      return `<button class="chip ${off ? "chip-off" : ""}" data-ticker="${t}" title="${dashed ? "Línea punteada (comparte tono con otra acción arriba)" : ""}">
        ${swatch}${name}
      </button>`;
    })
    .join("");
  el.querySelectorAll(".chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const t = btn.dataset.ticker;
      if (state.hidden.has(t)) state.hidden.delete(t);
      else state.hidden.add(t);
      renderLegend();
      renderCharts();
    });
  });
}

function drawMetric(metric, periodos) {
  const canvasId = "chart_" + metric.key;
  const c = themeColors();
  if (state.charts[canvasId]) state.charts[canvasId].destroy();

  const datasets = state.tickers
    .filter((t) => !state.hidden.has(t))
    .map((t) => {
      const idx = state.tickers.indexOf(t);
      const byPeriodo = {};
      state.rows.filter((r) => r.ticker === t).forEach((r) => (byPeriodo[periodoKey(r)] = r[metric.key]));
      return {
        label: state.names[t] || t,
        data: periodos.map((p) => (byPeriodo[p] != null ? byPeriodo[p] : null)),
        borderColor: colorFor(idx),
        backgroundColor: colorFor(idx),
        borderDash: dashFor(idx),
        borderWidth: 2,
        pointRadius: 4,
        pointHoverRadius: 6,
        spanGaps: true,
        tension: 0.15,
      };
    });

  state.charts[canvasId] = new Chart(document.getElementById(canvasId), {
    type: "line",
    data: { labels: periodos, datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false }, // leyenda única y compartida arriba de la grilla
        tooltip: {
          filter: (item) => item.parsed.y != null,
          callbacks: {
            label: (i) => ` ${i.dataset.label}: ${fmtNum(i.parsed.y)} ${metric.unit}`,
          },
        },
      },
      scales: {
        x: { grid: { color: c.grid }, ticks: { color: c.tick } },
        y: { grid: { color: c.grid }, ticks: { color: c.tick, callback: (v) => fmtNum(v) } },
      },
    },
  });
}

function renderCharts() {
  const periodos = buildPeriodos(state.rows);
  METRICS.forEach((m) => drawMetric(m, periodos));
}

function renderGrid() {
  const grid = document.getElementById("eeffGrid");
  grid.innerHTML = METRICS.map(
    (m) => `
    <div class="chart-card">
      <div class="chart-title">${m.label}${m.unit ? " (" + m.unit + ")" : ""}</div>
      <div class="chart-wrap chart-wrap-sm"><canvas id="chart_${m.key}"></canvas></div>
    </div>`
  ).join("");
}

async function boot() {
  clock();
  setInterval(clock, 1000);
  bindUI();
  try {
    const [rows, stocks] = await Promise.all([getJSON("/api/eeff_trimestral"), getJSON("/api/stocks")]);
    state.names = {};
    stocks.forEach((s) => (state.names[s.ticker] = s.name));
    state.rows = rows;
    state.tickers = Array.from(new Set(rows.map((r) => r.ticker))).sort();

    if (!state.tickers.length) {
      document.getElementById("eeffEmpty").classList.remove("hidden");
    } else {
      renderGrid();
      renderLegend();
      renderCharts();
    }
    document.getElementById("lastUpdate").textContent = "Actualizado: " + new Date().toLocaleString("es-CL");
  } catch (e) {
    document.getElementById("loaderText").textContent = "Error cargando EEFF trimestrales.";
    console.error(e);
    return;
  }
  hideLoader();
}

function bindUI() {
  document.getElementById("themeBtn").addEventListener("click", () => {
    const el = document.documentElement;
    const light = el.getAttribute("data-theme") === "light";
    el.setAttribute("data-theme", light ? "dark" : "light");
    document.getElementById("themeBtn").textContent = light ? "☀️" : "🌙";
    if (state.tickers.length) { renderLegend(); renderCharts(); }
  });
}

function clock() {
  const now = new Date().toLocaleString("es-CL", {
    timeZone: "America/Santiago", hour: "2-digit", minute: "2-digit", second: "2-digit",
    day: "2-digit", month: "short",
  });
  document.getElementById("clock").textContent = "🇨🇱 " + now;
}

function hideLoader() {
  document.getElementById("loader").classList.add("hide");
}

boot();
