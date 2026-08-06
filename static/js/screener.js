/* Screener cuantitativo — tabla de señales por acción */

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

function renderKPIs(rows) {
  const n = rows.length;
  const comprar = rows.filter((r) => r.senal === "COMPRAR").length;
  const vender = rows.filter((r) => r.senal === "VENDER").length;
  const mantener = n - comprar - vender;
  const avgSharpe = n ? rows.reduce((a, r) => a + (r.sharpe || 0), 0) / n : 0;

  const cards = [
    { label: "Comprar", value: comprar, cls: "pos" },
    { label: "Mantener", value: mantener, cls: "" },
    { label: "Vender", value: vender, cls: "neg" },
    { label: "Sharpe promedio", value: avgSharpe.toFixed(2), cls: cls(avgSharpe) },
  ];
  document.getElementById("screenerKpis").innerHTML = cards
    .map(
      (c) => `<div class="kpi"><div class="label">${c.label}</div>
      <div class="value ${c.cls}">${c.value}</div></div>`
    )
    .join("");
}

function renderTable(rows, names) {
  document.getElementById("screenerBody").innerHTML = rows
    .map((r) => {
      const name = names[r.ticker] || r.ticker;
      return `<tr>
        <td><div class="st-name">${name}</div><div class="st-ticker">${r.ticker}</div></td>
        <td><span class="signal ${signalClass(r.senal)}">${r.senal}</span></td>
        <td class="${cls(r.sharpe)}">${r.sharpe ?? "—"}</td>
        <td class="${cls(r.retorno_anual)}">${pct(r.retorno_anual)}</td>
        <td class="${cls(r.retorno_real_uf)}">${pct(r.retorno_real_uf)}</td>
        <td>${r.volatilidad_anual ?? "—"}%</td>
        <td>${r.beta_vs_ipsa ?? "—"}</td>
        <td class="neg">${pct(r.max_drawdown)}</td>
        <td>${r.dividend_yield != null ? r.dividend_yield + "%" : "—"}</td>
        <td>${r.pe != null ? r.pe.toFixed(1) : "—"}</td>
        <td class="${cls(r.roe)}">${r.roe != null ? r.roe.toFixed(1) + "%" : "—"}</td>
        <td class="st-razones">${(r.razones || []).join(" · ") || "—"}</td>
      </tr>`;
    })
    .join("");
}

async function boot() {
  clock();
  setInterval(clock, 1000);
  bindUI();
  try {
    const [rows, stocks] = await Promise.all([
      getJSON("/api/screener"),
      getJSON("/api/stocks"),
    ]);
    const names = {};
    stocks.forEach((s) => (names[s.ticker] = s.name));
    renderKPIs(rows);
    renderTable(rows, names);
    document.getElementById("lastUpdate").textContent =
      "Calculado: " + new Date().toLocaleString("es-CL");
  } catch (e) {
    document.getElementById("loaderText").textContent = "Error cargando el screener.";
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
