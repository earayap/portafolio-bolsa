/* Valor de Empresa — clasificación de valoración pura por EV/EBITDA y EV/Ventas */

const fmtCompact = (v) =>
  v == null ? "—" : new Intl.NumberFormat("es-CL", { notation: "compact", maximumFractionDigits: 1 }).format(v);
const fmtCLP = (v) =>
  v == null ? "—" : "$" + new Intl.NumberFormat("es-CL", { maximumFractionDigits: 2 }).format(v);
const fmtPct = (v) => (v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(1) + "%");
const cls = (v) => (v == null ? "" : v >= 0 ? "pos" : "neg");

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}

function claseClasificacion(clasificacion) {
  return { BARATA: "sig-buy", NEUTRAL: "sig-hold", CARA: "sig-sell" }[clasificacion] || "";
}

function renderKPIs(rows) {
  const n = rows.length;
  const baratas = rows.filter((r) => r.clasificacion === "BARATA").length;
  const caras = rows.filter((r) => r.clasificacion === "CARA").length;
  const sinDatos = rows.filter((r) => r.clasificacion === "SIN DATOS").length;
  const neutrales = n - baratas - caras - sinDatos;

  const cards = [
    { label: "Baratas", value: baratas, cls: "pos" },
    { label: "Neutrales", value: neutrales, cls: "" },
    { label: "Caras", value: caras, cls: "neg" },
    { label: "Sin datos", value: sinDatos, cls: "" },
  ];
  document.getElementById("veKpis").innerHTML = cards
    .map(
      (c) => `<div class="kpi"><div class="label">${c.label}</div>
      <div class="value ${c.cls}">${c.value}</div></div>`
    )
    .join("");
}

function renderTable(rows, names) {
  document.getElementById("veBody").innerHTML = rows
    .map((r) => {
      const name = names[r.ticker] || r.ticker;
      return `<tr>
        <td><div class="st-name">${name}</div><div class="st-ticker">${r.ticker}</div></td>
        <td><span class="signal ${claseClasificacion(r.clasificacion)}">${r.clasificacion}</span></td>
        <td>${fmtCompact(r.enterprise_value)}</td>
        <td class="${cls(r.ev_ebitda != null ? 8 - r.ev_ebitda : null)}">${r.ev_ebitda != null ? r.ev_ebitda + "x" : "—"}</td>
        <td class="${cls(r.ev_revenue != null ? 2 - r.ev_revenue : null)}">${r.ev_revenue != null ? r.ev_revenue + "x" : "—"}</td>
        <td class="${cls(r.ebitda)}">${fmtCompact(r.ebitda)}</td>
        <td>${fmtCLP(r.precio_actual)}</td>
        <td>${fmtCLP(r.precio_justo)}</td>
        <td class="${cls(r.diferencia_pct)}">${fmtPct(r.diferencia_pct)}</td>
        <td>${r.sector || "—"}</td>
        <td>${r.fuente || "—"}</td>
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
      getJSON("/api/valor-empresa"),
      getJSON("/api/stocks"),
    ]);
    const names = {};
    stocks.forEach((s) => (names[s.ticker] = s.name));
    renderKPIs(rows);
    renderTable(rows, names);
    document.getElementById("lastUpdate").textContent =
      "Calculado: " + new Date().toLocaleString("es-CL");
  } catch (e) {
    document.getElementById("loaderText").textContent = "Error cargando la valoración.";
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
