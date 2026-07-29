/* Portafolio Bolsa de Santiago — pantalla de distribución (gráficos de torta) */

const state = { data: null, charts: {}, hide: localStorage.getItem("hideMoney") === "1" };
const MASK = "••••";

/* Paleta categórica validada (skill dataviz). 7 slots + gris "Otras".
   Cada tema tiene sus pasos propios sobre su superficie. */
const PALETTE = {
  dark:  ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181"],
  light: ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4"],
};
const OTHER_COLOR = "#898781"; // gris muted, común a ambos temas
const TOP_N = 7;               // sectores con color propio; el resto → "Otras"

const fmtInt = (v) => new Intl.NumberFormat("es-CL", { maximumFractionDigits: 0 }).format(v);
const fmtCLP = (v) => "$" + fmtInt(v);
const pctOf = (v, total) => (total ? (v / total) * 100 : 0);

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}

function theme() {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}
function surfaceColor() {
  // Coincide con --bg-elev de style.css para el separador entre sectores.
  return theme() === "light" ? "#ffffff" : "#141925";
}
function inkColor() {
  return theme() === "light" ? "#4a5a72" : "#93a1b8";
}

/* Asigna color por entidad (posición en el ranking descendente). */
function colorFor(rankIndex) {
  return rankIndex < TOP_N ? PALETTE[theme()][rankIndex] : OTHER_COLOR;
}

/* ---------- Arranque ---------- */
async function boot() {
  clock();
  setInterval(clock, 1000);
  bindUI();
  try {
    state.data = await getJSON("/api/distribucion");
    render();
  } catch (e) {
    document.getElementById("loaderText").textContent = "No se pudieron cargar los datos.";
    return;
  }
  hideLoader();
}

function render() {
  const { since, stocks } = state.data;
  document.getElementById("sinceLabel").textContent = formatSince(since);

  drawMetric({
    canvas: "pieCantidad", legend: "legendCantidad", total: "totalCantidad",
    stocks, key: "cantidad", fmt: fmtInt, unit: "acciones", money: false,
  });
  drawMetric({
    canvas: "pieValor", legend: "legendValor", total: "totalValor",
    stocks, key: "valor_mercado", fmt: fmtCLP, unit: "", money: true,
  });
  drawMetric({
    canvas: "pieDividendos", legend: "legendDividendos", total: "totalDividendos",
    stocks, key: "dividendos", fmt: fmtCLP, unit: "", money: true,
  });

  document.getElementById("lastUpdate").textContent =
    "Corte dividendos: " + formatSince(since);
}

/* Construye una torta + su leyenda para una métrica dada. */
function drawMetric({ canvas, legend, total, stocks, key, fmt, unit, money }) {
  // En modo discreto se ocultan los montos de dinero (no la cantidad de acciones).
  const disp = (v) => (state.hide && money ? "$" + MASK : fmt(v));
  // Solo posiciones con valor > 0, ordenadas de mayor a menor.
  const rows = stocks
    .map((s) => ({ name: s.name, ticker: s.ticker, value: +s[key] || 0 }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value);

  const grand = rows.reduce((a, r) => a + r.value, 0);
  const totalEl = document.getElementById(total);
  totalEl.textContent = grand > 0 ? `${disp(grand)}${unit ? " " + unit : ""}` : "—";

  // Estado vacío (p. ej. sin dividendos registrados aún).
  if (grand <= 0) {
    if (state.charts[canvas]) { state.charts[canvas].destroy(); delete state.charts[canvas]; }
    const ctx = document.getElementById(canvas);
    ctx.getContext("2d").clearRect(0, 0, ctx.width, ctx.height);
    document.getElementById(legend).innerHTML =
      `<div class="legend-empty">Sin datos disponibles para esta métrica.</div>`;
    return;
  }

  // Asigna color por ranking; agrupa la cola en "Otras".
  rows.forEach((r, i) => (r.color = colorFor(i)));
  const named = rows.slice(0, TOP_N);
  const tail = rows.slice(TOP_N);
  const tailSum = tail.reduce((a, r) => a + r.value, 0);

  const slices = named.map((r) => ({ label: r.name, value: r.value, color: r.color }));
  if (tailSum > 0) slices.push({ label: `Otras (${tail.length})`, value: tailSum, color: OTHER_COLOR });

  // --- Gráfico ---
  if (state.charts[canvas]) state.charts[canvas].destroy();
  state.charts[canvas] = new Chart(document.getElementById(canvas), {
    type: "doughnut",
    data: {
      labels: slices.map((s) => s.label),
      datasets: [{
        data: slices.map((s) => s.value),
        backgroundColor: slices.map((s) => s.color),
        borderColor: surfaceColor(),
        borderWidth: 2,
        hoverOffset: 6,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: "58%",
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (i) => {
              const v = i.parsed;
              return ` ${i.label}: ${disp(v)}${unit ? " " + unit : ""} (${pctOf(v, grand).toFixed(1)}%)`;
            },
          },
        },
      },
    },
  });

  // --- Leyenda/tabla: TODAS las posiciones con su monto exacto y % ---
  document.getElementById(legend).innerHTML = rows
    .map((r) => `
      <div class="legend-row">
        <span class="legend-dot" style="background:${r.color}"></span>
        <span class="legend-name" title="${r.ticker}">${r.name}</span>
        <span class="legend-val">${disp(r.value)}</span>
        <span class="legend-pct">${pctOf(r.value, grand).toFixed(1)}%</span>
      </div>`)
    .join("");
}

/* ---------- Utilidades ---------- */
function formatSince(iso) {
  try {
    const [y, m, d] = iso.split("-").map(Number);
    const meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
    return `${d} ${meses[m - 1]} ${y}`;
  } catch (_) {
    return iso;
  }
}

function updateEye() {
  const b = document.getElementById("eyeBtn");
  if (!b) return;
  b.textContent = state.hide ? "🙈" : "👁️";
  b.title = state.hide ? "Mostrar montos" : "Ocultar montos";
}

function bindUI() {
  updateEye();
  document.getElementById("eyeBtn").addEventListener("click", () => {
    state.hide = !state.hide;
    localStorage.setItem("hideMoney", state.hide ? "1" : "0");
    updateEye();
    if (state.data) render();
  });
  document.getElementById("themeBtn").addEventListener("click", () => {
    const el = document.documentElement;
    const light = el.getAttribute("data-theme") === "light";
    el.setAttribute("data-theme", light ? "dark" : "light");
    document.getElementById("themeBtn").textContent = light ? "☀️" : "🌙";
    if (state.data) render(); // redibuja con la paleta del nuevo tema
  });
}

function clock() {
  const now = new Date().toLocaleString("es-CL", {
    timeZone: "America/Santiago", hour: "2-digit", minute: "2-digit", second: "2-digit",
    day: "2-digit", month: "short",
  });
  const el = document.getElementById("clock");
  if (el) el.textContent = "🇨🇱 " + now;
}

function hideLoader() {
  document.getElementById("loader").classList.add("hide");
}

boot();
