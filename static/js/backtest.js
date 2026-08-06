/* Backtest del screener — retorno futuro realizado por señal histórica */

const pct = (v) => (v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%");
const cls = (v) => (v == null ? "" : v >= 0 ? "pos" : "neg");

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}

function signalClass(senal) {
  return { COMPRAR: "sig-buy", MANTENER: "sig-hold", VENDER: "sig-sell" }[senal] || "";
}

function renderKPIs(resumen) {
  const cards = ["COMPRAR", "MANTENER", "VENDER"].map((senal) => {
    const r = resumen[senal] || {};
    return {
      label: senal,
      value: r.retorno_promedio != null ? pct(r.retorno_promedio) : "Sin datos",
      sub: r.n ? `${r.n} señales · ${r.tasa_acierto}% acierto` : "",
      cls: cls(r.retorno_promedio),
    };
  });
  document.getElementById("resumenGlobal").innerHTML = cards
    .map(
      (c) => `<div class="kpi"><div class="label">${c.label}</div>
      <div class="value ${c.cls}">${c.value}</div>
      <div class="sub ${c.cls}">${c.sub}</div></div>`
    )
    .join("");
}

function renderTable(detalle, names) {
  const filas = Object.entries(detalle).flatMap(([ticker, info]) =>
    ["COMPRAR", "MANTENER", "VENDER"]
      .filter((senal) => info.resumen[senal].n > 0)
      .map((senal) => ({ ticker, senal, ...info.resumen[senal] }))
  );
  document.getElementById("backtestBody").innerHTML = filas
    .map(
      (f) => `<tr>
        <td><div class="st-name">${names[f.ticker] || f.ticker}</div><div class="st-ticker">${f.ticker}</div></td>
        <td><span class="signal ${signalClass(f.senal)}">${f.senal}</span></td>
        <td>${f.n}</td>
        <td class="${cls(f.retorno_promedio)}">${pct(f.retorno_promedio)}</td>
        <td>${f.tasa_acierto}%</td>
      </tr>`
    )
    .join("");
}

async function boot() {
  clock();
  setInterval(clock, 1000);
  bindUI();
  try {
    const [bt, stocks] = await Promise.all([
      getJSON("/api/backtest?years=3"),
      getJSON("/api/stocks"),
    ]);
    const names = {};
    stocks.forEach((s) => (names[s.ticker] = s.name));
    document.getElementById("anios").textContent = bt.anios;
    document.getElementById("horizonte").textContent = Math.round(bt.horizonte_dias / 21);
    renderKPIs(bt.resumen_global);
    renderTable(bt.detalle_por_accion, names);
    document.getElementById("lastUpdate").textContent =
      "Calculado: " + new Date().toLocaleString("es-CL");
  } catch (e) {
    document.getElementById("loaderText").textContent = "Error corriendo el backtest.";
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
