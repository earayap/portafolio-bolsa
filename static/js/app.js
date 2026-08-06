/* Portafolio Bolsa de Santiago — lógica del dashboard */

const state = {
  stocks: [],
  screener: {}, // ticker -> resultado del screener
  fundamentales: {}, // ticker -> P/E, P/B, ROE, margen, deuda/patrimonio, sector
  current: null,
  range: "1y",
  priceChart: null,
  volumeChart: null,
  hide: localStorage.getItem("hideMoney") === "1", // modo discreto
};

function signalClass(senal) {
  return { COMPRAR: "sig-buy", MANTENER: "sig-hold", VENDER: "sig-sell" }[senal] || "";
}

function signalBadge(ticker) {
  const s = state.screener[ticker];
  if (!s) return "";
  return `<span class="badge badge-signal ${signalClass(s.senal)}" title="Score ${s.score}">${s.senal}</span>`;
}

const fmtCLP = (v) =>
  new Intl.NumberFormat("es-CL", { maximumFractionDigits: 2 }).format(v);
const fmtInt = (v) =>
  new Intl.NumberFormat("es-CL", { maximumFractionDigits: 0 }).format(v);
const pct = (v) => (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
const cls = (v) => (v >= 0 ? "pos" : "neg");

/* Montos de dinero: se ocultan en "modo discreto" (botón del ojo). */
const MASK = "••••";
const money = (v) => (state.hide ? "$" + MASK : "$" + fmtInt(v));
const moneySigned = (v) =>
  state.hide ? "$" + MASK : (v >= 0 ? "+" : "") + "$" + fmtInt(v);

/* Etiqueta de aviso para datos no vigentes / estimados */
function staleBadge(x) {
  if (x.source === "synthetic")
    return `<span class="badge badge-est" title="Sin datos reales disponibles; valor estimado">⚠ Estimado</span>`;
  if (x.stale) {
    const d = x.days_old != null ? `hace ${x.days_old}d` : "desactualizado";
    return `<span class="badge badge-old" title="Último precio registrado: ${x.last_date} (${d})">⚠ Últ. ${x.last_date}</span>`;
  }
  return "";
}

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}

/* ---------- Arranque ---------- */
async function boot() {
  clock();
  setInterval(clock, 1000);
  bindUI();
  await waitReady();
  await loadStocks();
  hideLoader();
}

async function waitReady() {
  const txt = document.getElementById("loaderText");
  for (let i = 0; i < 60; i++) {
    try {
      const s = await getJSON("/api/status");
      if (s.ready) return;
      txt.textContent = "Descargando historia de mercado…";
    } catch (_) {}
    await new Promise((r) => setTimeout(r, 1000));
  }
}

async function loadStocks() {
  state.stocks = await getJSON("/api/stocks");
  try {
    const rows = await getJSON("/api/screener");
    state.screener = Object.fromEntries(rows.map((r) => [r.ticker, r]));
  } catch (_) {
    state.screener = {};
  }
  try {
    state.fundamentales = await getJSON("/api/fundamentales");
  } catch (_) {
    state.fundamentales = {};
  }
  renderKPIs();
  renderList();
  if (state.stocks.length) selectStock(state.stocks[0].ticker);
}


/* ---------- KPIs globales ---------- */
function renderKPIs() {
  const s = state.stocks;
  const n = s.length;
  const avgChg = n ? s.reduce((a, x) => a + x.change_pct, 0) / n : 0;
  const gainers = s.filter((x) => x.change_pct > 0).length;
  const best = s.reduce((a, x) => (x.return_1y > (a?.return_1y ?? -1e9) ? x : a), null);
  const real = s.filter((x) => x.source === "yfinance").length;

  // Valorización total del portafolio.
  // Se excluyen las posiciones "estimadas" (sin precio real) para no
  // distorsionar el total con valores sintéticos.
  const valuables = s.filter((x) => x.source !== "synthetic");
  const excl = n - valuables.length;
  const totInv = valuables.reduce((a, x) => a + (x.invertido || 0), 0);
  const totVal = valuables.reduce((a, x) => a + (x.valor_mercado || 0), 0);
  const totPnl = totVal - totInv;
  const totPnlPct = totInv ? (totPnl / totInv) * 100 : 0;
  const exclNote = excl ? ` · ${excl} estimada${excl > 1 ? "s" : ""} excluida${excl > 1 ? "s" : ""}` : "";

  const cards = [
    { label: "Valor de mercado", value: money(totVal), sub: `${real} en vivo${exclNote}` },
    { label: "Costo invertido", value: money(totInv), sub: "precio de compra × cantidad" },
    { label: "Ganancia / Pérdida", value: pct(totPnlPct), sub: moneySigned(totPnl), cls: cls(totPnl) },
    { label: "Variación media (día)", value: pct(avgChg), sub: `${gainers} al alza · ${n - gainers} a la baja`, cls: cls(avgChg) },
    { label: "Mejor retorno 1A", value: best ? best.name : "—", sub: best ? pct(best.return_1y) : "", cls: best ? cls(best.return_1y) : "" },
  ];
  document.getElementById("kpis").innerHTML = cards
    .map(
      (c) => `<div class="kpi"><div class="label">${c.label}</div>
      <div class="value ${c.cls || ""}">${c.value}</div>
      <div class="sub ${c.cls || ""}">${c.sub || ""}</div></div>`
    )
    .join("");
}

/* ---------- Listado lateral ---------- */
function renderList(filter = "") {
  const q = filter.trim().toLowerCase();
  const items = state.stocks
    .filter((x) => !q || x.name.toLowerCase().includes(q) || x.ticker.toLowerCase().includes(q))
    .sort((a, b) => {
      const sa = state.screener[a.ticker]?.score;
      const sb = state.screener[b.ticker]?.score;
      // Sin señal (aún no calculada) va al final, no primero.
      if (sa == null && sb == null) return 0;
      if (sa == null) return 1;
      if (sb == null) return -1;
      return sb - sa;
    });
  document.getElementById("stockList").innerHTML = items
    .map((x) => {
      const warn = staleBadge(x);
      return `<li class="stock-item ${x.ticker === state.current ? "active" : ""}" data-ticker="${x.ticker}">
        <div><div class="si-name">${x.name} ${warn}</div><div class="si-ticker">${x.ticker} ${signalBadge(x.ticker)}</div></div>
        <div class="si-right"><div class="si-price">$${fmtCLP(x.last_close)}</div>
        <div class="si-chg ${cls(x.change_pct)}">${pct(x.change_pct)}</div></div>
      </li>`;
    })
    .join("");
  document.querySelectorAll(".stock-item").forEach((el) =>
    el.addEventListener("click", () => selectStock(el.dataset.ticker))
  );
}

/* ---------- Selección y detalle ---------- */
async function selectStock(ticker) {
  state.current = ticker;
  renderList(document.getElementById("search").value);
  const data = await getJSON(`/api/stock/${ticker}?range=${state.range}`);
  renderDetail(data);
}

function renderDetail(data) {
  const recs = data.records;
  const last = recs[recs.length - 1] || {};
  document.getElementById("detailName").textContent = data.name;
  document.getElementById("detailTicker").textContent = data.ticker;
  document.getElementById("detailSector").textContent =
    `${fmtInt(data.cantidad || 0)} acciones · compra $${fmtCLP(data.precio_compra || 0)}`;
  const srcEl = document.getElementById("detailSource");
  const srcLabel = { yfinance: "En vivo", cache: "Cache local", synthetic: "Estimado" };
  srcEl.textContent = srcLabel[data.source] || data.source;
  srcEl.dataset.src = data.source;

  const sig = state.screener[data.ticker];
  const sigEl = document.getElementById("detailSignal");
  if (sig) {
    sigEl.textContent = sig.senal;
    sigEl.className = "signal " + signalClass(sig.senal);
    sigEl.title = (sig.razones || []).join(" · ") || `Score ${sig.score}`;
  } else {
    sigEl.textContent = "";
    sigEl.className = "signal";
  }

  // Banner de aviso cuando el precio no está vigente o es estimado
  const warnEl = document.getElementById("detailWarn");
  if (data.source === "synthetic") {
    warnEl.className = "detail-warn est show";
    warnEl.innerHTML = `⚠ <strong>Precio estimado.</strong> Yahoo Finance no entrega datos para esta acción (baja liquidez o deslistada); los valores son referenciales, no nominales de la bolsa.`;
  } else if (data.stale) {
    const d = data.days_old != null ? ` (hace ${data.days_old} días)` : "";
    warnEl.className = "detail-warn old show";
    warnEl.innerHTML = `⚠ <strong>Precio desactualizado.</strong> Mostrando el último cierre real registrado: <strong>${data.last_price_date}</strong>${d}. La acción no ha transado recientemente.`;
  } else {
    warnEl.className = "detail-warn";
    warnEl.innerHTML = "";
  }

  document.getElementById("detailPrice").textContent = "$" + fmtCLP(last.close || 0);
  const chEl = document.getElementById("detailChange");
  chEl.textContent = pct(last.change_pct || 0);
  chEl.className = "change " + cls(last.change_pct || 0);

  renderStats(data);
  drawPrice(recs);
  drawVolume(recs);
  document.getElementById("lastUpdate").textContent =
    "Últ. dato: " + (last.date || "—");
}

function renderStats(data) {
  const recs = data.records;
  const closes = recs.map((r) => r.close);
  const last = recs[recs.length - 1] || {};
  const first = recs[0] || {};
  const ret = first.close ? ((last.close - first.close) / first.close) * 100 : 0;
  const vols = recs.map((r) => r.volume);
  const avgVol = vols.reduce((a, b) => a + b, 0) / (vols.length || 1);

  // Posición y P&L
  const cant = data.cantidad || 0;
  const pc = data.precio_compra || 0;
  const invertido = cant * pc;
  const valorMdo = cant * (last.close || 0);
  const pnl = valorMdo - invertido;
  const pnlPct = pc ? ((last.close - pc) / pc) * 100 : 0;

  const stats = [
    { l: "Cierre", v: "$" + fmtCLP(last.close || 0) },
    { l: "Cantidad", v: fmtInt(cant) },
    { l: "Precio compra", v: "$" + fmtCLP(pc) },
    { l: "Invertido", v: money(invertido) },
    { l: "Valor mercado", v: money(valorMdo) },
    { l: "Ganancia / Pérdida", v: moneySigned(pnl), cls: cls(pnl) },
    { l: "Rentabilidad", v: pct(pnlPct), cls: cls(pnlPct) },
    { l: "Retorno período", v: pct(ret), cls: cls(ret) },
    { l: "Máx período", v: "$" + fmtCLP(Math.max(...closes)) },
    { l: "Mín período", v: "$" + fmtCLP(Math.min(...closes)) },
    { l: "Volumen", v: fmtInt(last.volume || 0) },
    { l: "Vol. medio", v: fmtInt(avgVol) },
  ];

  const sig = state.screener[data.ticker];
  if (sig) {
    stats.push(
      { l: "Sharpe (screener)", v: sig.sharpe ?? "—", cls: cls(sig.sharpe ?? 0) },
      { l: "Beta vs mercado", v: sig.beta_vs_ipsa ?? "—" },
      { l: "Drawdown máx.", v: sig.max_drawdown != null ? pct(sig.max_drawdown) : "—", cls: "neg" },
      { l: "Retorno real (UF)", v: sig.retorno_real_uf != null ? pct(sig.retorno_real_uf) : "—", cls: cls(sig.retorno_real_uf ?? 0) },
      { l: "Dividend yield", v: sig.dividend_yield != null ? sig.dividend_yield + "%" : "—" },
    );
  }

  const f = state.fundamentales[data.ticker];
  const nd = (v, suffix = "") => (v == null ? "—" : v.toFixed ? v.toFixed(2) + suffix : v + suffix);
  if (f) {
    stats.push(
      { l: "P/E", v: nd(f.trailing_pe) },
      { l: "P/B", v: nd(f.price_to_book) },
      { l: "ROE", v: f.return_on_equity != null ? pct(f.return_on_equity * 100) : "—" },
      { l: "Margen neto", v: f.profit_margin != null ? pct(f.profit_margin * 100) : "—" },
      { l: "Deuda/Patrimonio", v: nd(f.debt_to_equity) },
      { l: "Sector", v: f.sector || "—" },
    );
  }

  document.getElementById("statsGrid").innerHTML = stats
    .map(
      (s) => `<div class="stat"><div class="s-label">${s.l}</div>
      <div class="s-value ${s.cls || ""}">${s.v}</div></div>`
    )
    .join("");
}


/* ---------- Gráficos ---------- */
function themeColors() {
  const light = document.documentElement.getAttribute("data-theme") === "light";
  return {
    grid: light ? "rgba(20,30,60,.07)" : "rgba(255,255,255,.06)",
    tick: light ? "#4a5a72" : "#93a1b8",
  };
}

function drawPrice(recs) {
  const ctx = document.getElementById("priceChart");
  const labels = recs.map((r) => r.date);
  const up = (recs[recs.length - 1]?.close ?? 0) >= (recs[0]?.close ?? 0);
  const line = up ? "#22c55e" : "#ef4444";
  const c = themeColors();
  if (state.priceChart) state.priceChart.destroy();

  const grad = ctx.getContext("2d").createLinearGradient(0, 0, 0, 360);
  grad.addColorStop(0, up ? "rgba(34,197,94,.28)" : "rgba(239,68,68,.28)");
  grad.addColorStop(1, "rgba(0,0,0,0)");

  state.priceChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Cierre", data: recs.map((r) => r.close), borderColor: line,
          backgroundColor: grad, borderWidth: 2, fill: true, pointRadius: 0, tension: 0.15 },
        { label: "MM20", data: recs.map((r) => r.ma20), borderColor: "#3b82f6",
          borderWidth: 1.2, borderDash: [5, 4], pointRadius: 0, fill: false, tension: 0.15 },
        { label: "MM50", data: recs.map((r) => r.ma50), borderColor: "#f59e0b",
          borderWidth: 1.2, borderDash: [2, 3], pointRadius: 0, fill: false, tension: 0.15 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: true, labels: { color: c.tick, boxWidth: 14, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: (i) => `${i.dataset.label}: $${fmtCLP(i.parsed.y)}`,
          },
        },
      },
      scales: {
        x: { type: "time", time: { unit: "month" }, grid: { color: c.grid },
             ticks: { color: c.tick, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
        y: { grid: { color: c.grid }, ticks: { color: c.tick, callback: (v) => "$" + fmtCLP(v) } },
      },
    },
  });
}

function drawVolume(recs) {
  const ctx = document.getElementById("volumeChart");
  const c = themeColors();
  if (state.volumeChart) state.volumeChart.destroy();
  state.volumeChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: recs.map((r) => r.date),
      datasets: [{
        data: recs.map((r) => r.volume),
        backgroundColor: recs.map((r) => (r.change_pct >= 0 ? "rgba(34,197,94,.55)" : "rgba(239,68,68,.55)")),
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (i) => fmtInt(i.parsed.y) } } },
      scales: {
        x: { type: "time", time: { unit: "month" }, grid: { display: false },
             ticks: { color: c.tick, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
        y: { grid: { color: c.grid }, ticks: { color: c.tick, maxTicksLimit: 4,
             callback: (v) => (v >= 1000 ? (v / 1000).toFixed(0) + "K" : v) } },
      },
    },
  });
}

/* ---------- UI ---------- */
function bindUI() {
  document.getElementById("search").addEventListener("input", (e) => renderList(e.target.value));
  document.getElementById("rangeTabs").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    document.querySelectorAll("#rangeTabs button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    state.range = b.dataset.range;
    if (state.current) selectStock(state.current);
  });
  updateEye();
  document.getElementById("eyeBtn").addEventListener("click", () => {
    state.hide = !state.hide;
    localStorage.setItem("hideMoney", state.hide ? "1" : "0");
    updateEye();
    renderKPIs();
    if (state.current) selectStock(state.current);
  });
  document.getElementById("themeBtn").addEventListener("click", () => {
    const el = document.documentElement;
    const light = el.getAttribute("data-theme") === "light";
    el.setAttribute("data-theme", light ? "dark" : "light");
    document.getElementById("themeBtn").textContent = light ? "☀️" : "🌙";
    if (state.current) selectStock(state.current);
  });
  document.getElementById("refreshBtn").addEventListener("click", async () => {
    const btn = document.getElementById("refreshBtn");
    btn.classList.add("spin");
    try {
      await fetch("/api/refresh", { method: "POST" });
      await loadStocks();
    } catch (_) {}
    btn.classList.remove("spin");
  });
}

function clock() {
  const now = new Date().toLocaleString("es-CL", {
    timeZone: "America/Santiago", hour: "2-digit", minute: "2-digit", second: "2-digit",
    day: "2-digit", month: "short",
  });
  document.getElementById("clock").textContent = "🇨🇱 " + now;
}

function updateEye() {
  const b = document.getElementById("eyeBtn");
  if (!b) return;
  b.textContent = state.hide ? "🙈" : "👁️";
  b.title = state.hide ? "Mostrar montos" : "Ocultar montos";
}

function hideLoader() {
  document.getElementById("loader").classList.add("hide");
}

boot();
