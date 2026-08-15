/* Mis Posiciones — cantidad/precio de compra editables y dividendos reales manuales */

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}

async function postJSON(url, body, method = "POST") {
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body != null ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.status);
  return data;
}

let PORTFOLIO_NAMES = {};

function renderPosRow(ticker, info) {
  return `<tr data-ticker="${ticker}">
    <td><div class="st-name">${info.nombre}</div><div class="st-ticker">${ticker}</div></td>
    <td><input type="number" class="pos-input" data-field="cantidad" min="0" step="1" value="${info.cantidad}"></td>
    <td><input type="number" class="pos-input" data-field="precio_compra" min="0" step="0.01" value="${info.precio_compra}"></td>
    <td><button class="btn btn-save" data-ticker="${ticker}">Guardar</button></td>
  </tr>`;
}

function renderPosTable(portfolio) {
  const body = document.getElementById("posBody");
  body.innerHTML = Object.entries(portfolio).map(([t, info]) => renderPosRow(t, info)).join("");
  body.querySelectorAll(".btn-save").forEach((btn) => {
    btn.addEventListener("click", () => savePosicion(btn.dataset.ticker));
  });
}

async function savePosicion(ticker) {
  const row = document.querySelector(`#posBody tr[data-ticker="${ticker}"]`);
  const cantidad = row.querySelector('[data-field="cantidad"]').value;
  const precio_compra = row.querySelector('[data-field="precio_compra"]').value;
  const btn = row.querySelector(".btn-save");
  const original = btn.textContent;
  btn.textContent = "Guardando…";
  btn.disabled = true;
  try {
    await postJSON(`/api/posiciones/${encodeURIComponent(ticker)}`, { cantidad, precio_compra });
    btn.textContent = "✓ Guardado";
  } catch (e) {
    btn.textContent = "Error";
    console.error(e);
  } finally {
    setTimeout(() => {
      btn.textContent = original;
      btn.disabled = false;
    }, 1500);
  }
}

function money(v) {
  return "$" + Math.round(v).toLocaleString("es-CL");
}

function renderOperacionRow(op) {
  const name = PORTFOLIO_NAMES[op.ticker] || op.ticker;
  const tipoClass = op.tipo === "COMPRA" ? "sig-buy" : "sig-sell";
  return `<tr>
    <td>${op.fecha}</td>
    <td><span class="signal ${tipoClass}">${op.tipo}</span></td>
    <td><div class="st-name">${name}</div><div class="st-ticker">${op.ticker}</div></td>
    <td>${op.cantidad}</td>
    <td>${op.precio_unitario != null ? money(op.precio_unitario) : "—"}</td>
    <td>${money(op.monto)}</td>
    <td>${op.institucion}</td>
    <td title="${op.archivo}">${op.numero_factura || "—"}</td>
  </tr>`;
}

async function loadOperaciones() {
  const rows = await getJSON("/api/operaciones");
  const body = document.getElementById("operacionesBody");
  body.innerHTML = rows.length
    ? rows.map(renderOperacionRow).join("")
    : `<tr><td colspan="8" class="legend-empty">Todavía no hay operaciones sincronizadas. Usa "Sincronizar comprobantes".</td></tr>`;
}

async function syncComprobantes() {
  const btn = document.getElementById("syncBtn");
  const status = document.getElementById("syncStatus");
  btn.disabled = true;
  btn.textContent = "Sincronizando…";
  status.textContent = "";
  try {
    const r = await postJSON("/api/comprobantes/sync", {});
    if (r.error) {
      status.textContent = r.error;
    } else {
      status.textContent =
        `${r.archivos_vistos} archivo(s) revisados, ${r.operaciones_nuevas} operación(es) nueva(s) importada(s).` +
        (r.sin_parsear.length ? ` ${r.sin_parsear.length} sin reconocer: ${r.sin_parsear.join(", ")}` : "");
      await loadOperaciones();
    }
  } catch (e) {
    status.textContent = "Error: " + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Sincronizar comprobantes";
  }
}

function renderRecalcRow(r) {
  const name = r.nombre || r.ticker;
  const nuevoBadge = r.es_nuevo ? ` <span class="badge badge-est" title="No existe todavía en Posiciones">NUEVO</span>` : "";
  return `<tr data-ticker="${r.ticker}" class="${r.cambia ? "" : "legend-empty"}">
    <td><input type="checkbox" class="recalc-check" data-ticker="${r.ticker}" ${r.cambia ? "checked" : ""}></td>
    <td><div class="st-name">${name}${nuevoBadge}</div><div class="st-ticker">${r.ticker}</div></td>
    <td>${r.cantidad_actual ?? "—"}</td>
    <td>${r.cantidad_calculada}</td>
    <td>${r.precio_compra_actual != null ? money(r.precio_compra_actual) : "—"}</td>
    <td>${money(r.precio_compra_calculado)}</td>
  </tr>`;
}

async function mostrarRecalculoPreview() {
  const btn = document.getElementById("recalcBtn");
  const panel = document.getElementById("recalcPanel");
  const status = document.getElementById("recalcStatus");
  btn.disabled = true;
  status.textContent = "";
  try {
    const rows = await getJSON("/api/posiciones/recalculo_preview");
    document.getElementById("recalcBody").innerHTML = rows.map(renderRecalcRow).join("");
    panel.classList.remove("hidden");
  } catch (e) {
    status.textContent = "Error: " + e.message;
  } finally {
    btn.disabled = false;
  }
}

async function aplicarRecalculo() {
  const btn = document.getElementById("recalcApplyBtn");
  const status = document.getElementById("recalcStatus");
  const tickers = Array.from(document.querySelectorAll(".recalc-check:checked")).map((c) => c.dataset.ticker);
  if (!tickers.length) {
    status.textContent = "No hay tickers seleccionados.";
    return;
  }
  btn.disabled = true;
  btn.textContent = "Aplicando…";
  try {
    const r = await postJSON("/api/posiciones/recalculo_aplicar", { tickers });
    status.textContent = `Aplicado a ${r.aplicados.length} posición(es). Recargando…`;
    setTimeout(() => window.location.reload(), 1200);
  } catch (e) {
    status.textContent = "Error: " + e.message;
    btn.disabled = false;
    btn.textContent = "Aplicar seleccionados";
  }
}

function bindOperaciones() {
  document.getElementById("syncBtn").addEventListener("click", syncComprobantes);
  document.getElementById("recalcBtn").addEventListener("click", mostrarRecalculoPreview);
  document.getElementById("recalcApplyBtn").addEventListener("click", aplicarRecalculo);
}

function renderAporteRow(a) {
  return `<tr data-id="${a.id}">
    <td>${a.date}</td>
    <td>${money(a.amount)}</td>
    <td><button class="btn btn-ghost btn-del" data-id="${a.id}" title="Eliminar">🗑️</button></td>
  </tr>`;
}

async function loadAportes() {
  const rows = await getJSON("/api/aportes");
  const body = document.getElementById("aporteBody");
  body.innerHTML = rows.length
    ? rows.map(renderAporteRow).join("")
    : `<tr><td colspan="3" class="legend-empty">Todavía no hay aportes ingresados a mano.</td></tr>`;
  body.querySelectorAll(".btn-del").forEach((btn) => {
    btn.addEventListener("click", () => deleteAporte(btn.dataset.id));
  });
  const total = rows.reduce((a, r) => a + r.amount, 0);
  document.getElementById("aporteTotal").textContent =
    rows.length ? `Total aportado: ${money(total)}` : "";
}

async function deleteAporte(id) {
  try {
    await postJSON(`/api/aportes/${id}`, null, "DELETE");
    await loadAportes();
  } catch (e) {
    console.error(e);
  }
}

function bindAporteForm() {
  const form = document.getElementById("aporteForm");
  const errorEl = document.getElementById("aporteError");
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    errorEl.textContent = "";
    const date = document.getElementById("aporteDate").value;
    const amount = document.getElementById("aporteAmount").value;
    try {
      await postJSON("/api/aportes", { date, amount });
      document.getElementById("aporteAmount").value = "";
      await loadAportes();
    } catch (e) {
      errorEl.textContent = e.message;
    }
  });
}

function renderDivRow(d) {
  const name = PORTFOLIO_NAMES[d.ticker] || d.ticker;
  const accion = d.fuente === "manual"
    ? `<button class="btn btn-ghost btn-del" data-id="${d.id}" title="Eliminar">🗑️</button>`
    : `<span class="badge badge-est" title="Dato oficial de la BCS, no editable desde acá">BCS</span>`;
  return `<tr data-id="${d.id ?? ""}">
    <td><div class="st-name">${name}</div><div class="st-ticker">${d.ticker}</div></td>
    <td>${d.date}</td>
    <td>$${d.amount}</td>
    <td>${accion}</td>
  </tr>`;
}

async function loadDividendos() {
  const rows = await getJSON("/api/dividendos");
  const body = document.getElementById("divBody");
  body.innerHTML = rows.length
    ? rows.map(renderDivRow).join("")
    : `<tr><td colspan="4" class="legend-empty">Todavía no hay dividendos registrados.</td></tr>`;
  body.querySelectorAll(".btn-del").forEach((btn) => {
    btn.addEventListener("click", () => deleteDividendo(btn.dataset.id));
  });
}

async function deleteDividendo(id) {
  try {
    await postJSON(`/api/dividendos_manuales/${id}`, null, "DELETE");
    await loadDividendos();
  } catch (e) {
    console.error(e);
  }
}

function bindDivForm() {
  const form = document.getElementById("divForm");
  const errorEl = document.getElementById("divError");
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    errorEl.textContent = "";
    const ticker = document.getElementById("divTicker").value;
    const date = document.getElementById("divDate").value;
    const amount = document.getElementById("divAmount").value;
    try {
      await postJSON("/api/dividendos_manuales", { ticker, date, amount });
      document.getElementById("divAmount").value = "";
      await loadDividendos();
    } catch (e) {
      errorEl.textContent = e.message;
    }
  });
}

function fmtNum(v) {
  return v === null || v === undefined ? "—" : v;
}

function renderEeffRow(r) {
  const name = PORTFOLIO_NAMES[r.ticker] || r.ticker;
  return `<tr data-id="${r.id}">
    <td><div class="st-name">${name}</div><div class="st-ticker">${r.ticker}</div></td>
    <td>${r.anio}</td>
    <td>Q${r.trimestre}</td>
    <td>${fmtNum(r.ingresos)}</td>
    <td>${fmtNum(r.utilidad_neta)}</td>
    <td>${fmtNum(r.roe)}</td>
    <td>${fmtNum(r.deuda_patrimonio)}</td>
    <td>${fmtNum(r.margen_neto)}</td>
    <td>${fmtNum(r.resultado_operacional)}</td>
    <td>${fmtNum(r.depreciacion_amortizacion)}</td>
    <td>${fmtNum(r.deuda_financiera)}</td>
    <td>${fmtNum(r.efectivo_equivalentes)}</td>
    <td><button class="btn btn-ghost btn-del" data-id="${r.id}" title="Eliminar">🗑️</button></td>
  </tr>`;
}

async function loadEeff() {
  const rows = await getJSON("/api/eeff_trimestral");
  const body = document.getElementById("eeffBody");
  body.innerHTML = rows.length
    ? rows.map(renderEeffRow).join("")
    : `<tr><td colspan="13" class="legend-empty">Todavía no hay EEFF trimestrales ingresados a mano.</td></tr>`;
  body.querySelectorAll(".btn-del").forEach((btn) => {
    btn.addEventListener("click", () => deleteEeff(btn.dataset.id));
  });
}

async function deleteEeff(id) {
  try {
    await postJSON(`/api/eeff_trimestral/${id}`, null, "DELETE");
    await loadEeff();
  } catch (e) {
    console.error(e);
  }
}

function bindEeffForm() {
  const form = document.getElementById("eeffForm");
  const errorEl = document.getElementById("eeffError");
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    errorEl.textContent = "";
    const body = {
      ticker: document.getElementById("eeffTicker").value,
      anio: document.getElementById("eeffAnio").value,
      trimestre: document.getElementById("eeffTrimestre").value,
      ingresos: document.getElementById("eeffIngresos").value,
      utilidad_neta: document.getElementById("eeffUtilidad").value,
      roe: document.getElementById("eeffRoe").value,
      deuda_patrimonio: document.getElementById("eeffDeuda").value,
      margen_neto: document.getElementById("eeffMargen").value,
      resultado_operacional: document.getElementById("eeffResultOp").value,
      depreciacion_amortizacion: document.getElementById("eeffDepAmort").value,
      deuda_financiera: document.getElementById("eeffDeudaFin").value,
      efectivo_equivalentes: document.getElementById("eeffEfectivo").value,
    };
    try {
      await postJSON("/api/eeff_trimestral", body);
      [
        "eeffIngresos", "eeffUtilidad", "eeffRoe", "eeffDeuda", "eeffMargen",
        "eeffResultOp", "eeffDepAmort", "eeffDeudaFin", "eeffEfectivo",
      ].forEach((id) => (document.getElementById(id).value = ""));
      await loadEeff();
    } catch (e) {
      errorEl.textContent = e.message;
    }
  });
}

async function boot() {
  clock();
  setInterval(clock, 1000);
  bindUI();
  bindAporteForm();
  bindDivForm();
  bindEeffForm();
  bindOperaciones();
  try {
    const stocks = await getJSON("/api/stocks");
    stocks.forEach((s) => (PORTFOLIO_NAMES[s.ticker] = s.name));
    renderPosTable(window.PORTFOLIO);
    await loadAportes();
    await loadDividendos();
    await loadEeff();
    await loadOperaciones();
    document.getElementById("lastUpdate").textContent =
      "Actualizado: " + new Date().toLocaleString("es-CL");
  } catch (e) {
    document.getElementById("loaderText").textContent = "Error cargando posiciones.";
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
