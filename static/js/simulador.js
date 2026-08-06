/* Simulador de Monte Carlo — gráfico de abanico de trayectorias */

const state = { ticker: null, horizonte: "6m", chart: null };

const fmtCLP = (v) => new Intl.NumberFormat("es-CL", { maximumFractionDigits: 0 }).format(v);
const pct = (v) => (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
const cls = (v) => (v >= 0 ? "pos" : "neg");

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}

function themeColors() {
  const light = document.documentElement.getAttribute("data-theme") === "light";
  return {
    grid: light ? "rgba(20,30,60,.07)" : "rgba(255,255,255,.06)",
    tick: light ? "#4a5a72" : "#93a1b8",
  };
}

function renderKPIs(d) {
  const cards = [
    { label: "Precio actual", value: "$" + fmtCLP(d.precio_actual) },
    { label: "Probabilidad de subir", value: d.probabilidad_sube + "%", cls: cls(d.probabilidad_sube - 50) },
    { label: "Probabilidad de bajar", value: d.probabilidad_baja + "%", cls: cls(50 - d.probabilidad_baja) },
    { label: "Retorno esperado (mediana)", value: pct(d.retorno_esperado_pct), cls: cls(d.retorno_esperado_pct) },
    { label: "Rango 80% de confianza", value: `$${fmtCLP(d.percentiles_finales.p10)} — $${fmtCLP(d.percentiles_finales.p90)}` },
  ];
  document.getElementById("simKpis").innerHTML = cards
    .map(
      (c) => `<div class="kpi"><div class="label">${c.label}</div>
      <div class="value ${c.cls || ""}">${c.value}</div></div>`
    )
    .join("");
  document.getElementById("nSim").textContent = d.n_simulaciones.toLocaleString("es-CL");
}

function drawChart(d) {
  const ctx = document.getElementById("simChart");
  const c = themeColors();
  if (state.chart) state.chart.destroy();

  // Eje temporal continuo: fechas reales para el histórico, y días hábiles
  // aproximados (+1 día calendario por día bursátil) para la proyección.
  const historico = d.historico.map((r) => ({ x: r.date, y: r.close }));
  const ultimaFecha = new Date(d.historico[d.historico.length - 1].date);

  const fechaProyeccion = (diaBursatil) => {
    const f = new Date(ultimaFecha);
    f.setDate(f.getDate() + Math.round(diaBursatil * 7 / 5)); // aprox. días hábiles -> calendario
    return f.toISOString().slice(0, 10);
  };

  const base = { x: d.historico[d.historico.length - 1].date, y: d.precio_actual };
  const p10 = [base, ...d.abanico.map((a) => ({ x: fechaProyeccion(a.dia), y: a.p10 }))];
  const p50 = [base, ...d.abanico.map((a) => ({ x: fechaProyeccion(a.dia), y: a.p50 }))];
  const p90 = [base, ...d.abanico.map((a) => ({ x: fechaProyeccion(a.dia), y: a.p90 }))];

  state.chart = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        { label: "Histórico", data: historico, borderColor: "#3b82f6", borderWidth: 2,
          pointRadius: 0, tension: 0.1 },
        { label: "P90 (optimista)", data: p90, borderColor: "#22c55e", borderWidth: 1.3,
          borderDash: [5, 4], pointRadius: 0, fill: "+1", backgroundColor: "rgba(34,197,94,.08)" },
        { label: "Mediana (P50)", data: p50, borderColor: "#f59e0b", borderWidth: 2,
          pointRadius: 0 },
        { label: "P10 (pesimista)", data: p10, borderColor: "#ef4444", borderWidth: 1.3,
          borderDash: [5, 4], pointRadius: 0 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: true, labels: { color: c.tick, boxWidth: 14, font: { size: 11 } } },
        tooltip: { callbacks: { label: (i) => `${i.dataset.label}: $${fmtCLP(i.parsed.y)}` } },
      },
      scales: {
        x: { type: "time", time: { unit: "month" }, grid: { color: c.grid },
             ticks: { color: c.tick, maxRotation: 0, autoSkip: true, maxTicksLimit: 10 } },
        y: { grid: { color: c.grid }, ticks: { color: c.tick, callback: (v) => "$" + fmtCLP(v) } },
      },
    },
  });
}

async function cargar() {
  document.getElementById("loader").classList.remove("hide");
  try {
    const d = await getJSON(`/api/simulador/${state.ticker}?horizonte=${state.horizonte}`);
    renderKPIs(d);
    drawChart(d);
    document.getElementById("lastUpdate").textContent = "Basado en cierre del: " + d.fecha_actual;
  } catch (e) {
    console.error(e);
  }
  document.getElementById("loader").classList.add("hide");
}

function bindUI() {
  const sel = document.getElementById("tickerSelect");
  state.ticker = sel.value;
  sel.addEventListener("change", () => {
    state.ticker = sel.value;
    cargar();
  });

  document.getElementById("horizonTabs").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    document.querySelectorAll("#horizonTabs button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    state.horizonte = b.dataset.h;
    cargar();
  });

  document.getElementById("themeBtn").addEventListener("click", () => {
    const el = document.documentElement;
    const light = el.getAttribute("data-theme") === "light";
    el.setAttribute("data-theme", light ? "dark" : "light");
    document.getElementById("themeBtn").textContent = light ? "☀️" : "🌙";
    cargar();
  });
}

function clock() {
  const now = new Date().toLocaleString("es-CL", {
    timeZone: "America/Santiago", hour: "2-digit", minute: "2-digit", second: "2-digit",
    day: "2-digit", month: "short",
  });
  document.getElementById("clock").textContent = "🇨🇱 " + now;
}

clock();
setInterval(clock, 1000);
bindUI();
cargar();
