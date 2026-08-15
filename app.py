"""Aplicación Flask: Portafolio Bolsa de Comercio de Santiago."""

import json
import os
import sys
import threading
from datetime import datetime

from flask import Flask, jsonify, render_template, request

import config
import data_service
import screener
import backtest
import simulador
import valor_empresa
import comprobantes

app = Flask(__name__)

# --------------------------------------------------------------------------- #
# Portafolio (fuente única de verdad)
#   ticker -> nombre, cantidad y precio de compra promedio.
#   El ticker debe ser el símbolo oficial de Yahoo Finance / Bolsa de Santiago.
#
#   Se carga desde portfolio.json (datos reales, ignorado por git) y, si no
#   existe, desde portfolio.example.json (datos de ejemplo para quien clona
#   el repo). Ver README para instrucciones de configuración.
# --------------------------------------------------------------------------- #
def _load_portfolio():
    real_path = os.path.join(config.BASE_DIR, "portfolio.json")
    example_path = os.path.join(config.RESOURCE_DIR, "portfolio.example.json")

    if not os.path.exists(real_path) and getattr(sys, "frozen", False):
        # App empaquetada (.exe): copiamos el ejemplo junto al ejecutable para
        # que el usuario tenga un portfolio.json editable y persistente.
        import shutil
        shutil.copyfile(example_path, real_path)

    path = real_path if os.path.exists(real_path) else example_path
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if path == example_path:
        print(f"[portafolio-bolsa] No se encontró portfolio.json: usando datos de ejemplo "
              f"({example_path}). Copia portfolio.example.json a portfolio.json y edítalo "
              f"con tus propias posiciones.")
    return data


PORTAFOLIO = _load_portfolio()

# Inyecta el portafolio en el servicio de datos.
data_service.set_portfolio(PORTAFOLIO)

_portfolio_lock = threading.Lock()


def _save_portfolio():
    """Persiste PORTAFOLIO en portfolio.json (fuente única de verdad)."""
    path = os.path.join(config.BASE_DIR, "portfolio.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(PORTAFOLIO, f, ensure_ascii=False, indent=2)
        f.write("\n")

# Estado de la carga inicial en segundo plano
_warmup = {"done": False, "results": {}}


def _warmup_data():
    """Descarga/hidrata la cache al arrancar, sin bloquear el servidor."""
    data_service.init_db()
    _warmup["results"] = data_service.refresh_all(force=False)
    # UF/TPM de años anteriores (mindicador.cl solo trae los últimos días por
    # defecto). Necesario para que /api/backtest pueda evaluar fechas pasadas.
    anio_actual = datetime.now().year
    data_service.backfill_indicadores_historicos(range(anio_actual - config.HISTORY_YEARS, anio_actual + 1))
    _warmup["done"] = True


# --------------------------------------------------------------------------- #
# Vistas
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return render_template("index.html", portfolio=PORTAFOLIO)


@app.route("/distribucion")
def distribucion():
    return render_template("distribucion.html")


@app.route("/screener")
def screener_page():
    return render_template("screener.html")


@app.route("/backtest")
def backtest_page():
    return render_template("backtest.html")


@app.route("/simulador")
def simulador_page():
    return render_template("simulador.html", portfolio=PORTAFOLIO)


@app.route("/posiciones")
def posiciones_page():
    return render_template("posiciones.html", portfolio=PORTAFOLIO)


@app.route("/valor-empresa")
def valor_empresa_page():
    return render_template("valor_empresa.html")


@app.route("/api/status")
def api_status():
    return jsonify({"ready": _warmup["done"], "results": _warmup["results"]})


# Fecha de corte para la vista de distribución (dividendos desde este día).
DIST_SINCE = "2026-03-01"


@app.route("/api/distribucion")
def api_distribucion():
    """Datos agregados por acción para los gráficos de torta.

    Devuelve, por cada posición: cantidad de acciones, valor de mercado y
    dividendos pagados desde `since` (por defecto marzo 2025).
    """
    since = request.args.get("since", DIST_SINCE)
    stocks = []
    for ticker, info in PORTAFOLIO.items():
        s = data_service.get_summary(ticker)
        if not s:
            continue
        stocks.append({
            "ticker": ticker,
            "name": info.get("nombre", ticker),
            "source": s["source"],
            "cantidad": s["cantidad"],
            "valor_mercado": s["valor_mercado"],
            "dividendos": data_service.get_dividends_total(ticker, since),
        })
    return jsonify({"since": since, "stocks": stocks})


@app.route("/api/stocks")
def api_stocks():
    """Resumen de todas las acciones para el listado lateral y las tarjetas."""
    summaries = []
    for ticker in PORTAFOLIO:
        s = data_service.get_summary(ticker)
        if s:
            summaries.append(s)
    return jsonify(summaries)


@app.route("/api/stock/<ticker>")
def api_stock(ticker):
    """Historia completa con indicadores de una acción."""
    if ticker not in PORTAFOLIO:
        return jsonify({"error": "Acción no encontrada"}), 404
    data = data_service.get_history(ticker)
    rng = request.args.get("range", "all")
    limits = {"1m": 22, "3m": 66, "6m": 132, "1y": 252, "2y": 504}
    if rng in limits:
        data["records"] = data["records"][-limits[rng]:]
    return jsonify(data)


@app.route("/api/indicadores")
def api_indicadores():
    """Último valor conocido de cada indicador macro (UF, dólar, TPM, cobre...)."""
    return jsonify(data_service.get_indicadores())


@app.route("/api/indicadores/<codigo>")
def api_indicador_historial(codigo):
    """Historial reciente de un indicador macro puntual."""
    limit = request.args.get("limit", 90, type=int)
    return jsonify(data_service.get_indicador_historial(codigo, limit=limit))


@app.route("/api/fundamentales")
def api_fundamentales():
    """Fundamentales por acción (P/E, P/B, ROE, margen, deuda/patrimonio, sector).

    Fuente: yfinance sanitizado (la CMF no tiene API pública para emisores
    no bancarios). Ver fundamentales.py."""
    return jsonify(data_service.get_fundamentales_all())


@app.route("/api/fundamentales/<ticker>")
def api_fundamentales_ticker(ticker):
    datos = data_service.get_fundamentales(ticker)
    if not datos:
        return jsonify({"error": "Sin datos fundamentales para esta acción"}), 404
    return jsonify(datos)


@app.route("/api/screener")
def api_screener():
    """Señal de decisión (COMPRAR/MANTENER/VENDER) por acción del portafolio,
    basada en Sharpe, dividend yield, retorno real (ajustado por UF), beta
    vs IPSA y drawdown máximo."""
    return jsonify(screener.evaluate_all(list(PORTAFOLIO.keys())))


@app.route("/api/valor-empresa")
def api_valor_empresa():
    """Clasificación BARATA/NEUTRAL/CARA por Valor de Empresa (EV/EBITDA,
    EV/Ventas) — a diferencia del /api/screener, es una lectura de
    valoración pura, sin mezclar riesgo/retorno. Ver valor_empresa.py."""
    return jsonify(valor_empresa.evaluate_all(list(PORTAFOLIO.keys())))


@app.route("/api/backtest")
def api_backtest():
    """Backtest de la señal del screener: retorno futuro realizado (6 meses)
    según la señal COMPRAR/MANTENER/VENDER de cada punto histórico. Solo usa
    la parte precio/dividendos del score (no fundamentales, que no tienen
    historia guardada). Ver backtest.py."""
    years = request.args.get("years", 3, type=int)
    return jsonify(backtest.backtest_portafolio(list(PORTAFOLIO.keys()), years=years))


@app.route("/api/simulador/<ticker>")
def api_simulador(ticker):
    """Simulación de Monte Carlo (movimiento geométrico browniano) usando el
    retorno y la volatilidad histórica de la propia acción. Es estadístico,
    no una predicción — ver simulador.py."""
    if ticker not in PORTAFOLIO:
        return jsonify({"error": "Acción no encontrada"}), 404
    horizonte = request.args.get("horizonte", "6m")
    resultado = simulador.simular(ticker, horizonte)
    if not resultado:
        return jsonify({"error": "Historia insuficiente para simular"}), 404
    resultado["name"] = PORTAFOLIO[ticker].get("nombre", ticker)
    return jsonify(resultado)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Fuerza una actualización desde la API pública."""
    ticker = request.args.get("ticker")
    if ticker and ticker in PORTAFOLIO:
        src, n = data_service.refresh_ticker(ticker, force=True)
        return jsonify({"ticker": ticker, "source": src, "rows": n})
    results = data_service.refresh_all(force=True)
    return jsonify(results)


@app.route("/api/posiciones/<ticker>", methods=["POST"])
def api_actualizar_posicion(ticker):
    """Actualiza la cantidad real de acciones y/o el precio de compra
    promedio de una posición, y lo persiste en portfolio.json."""
    if ticker not in PORTAFOLIO:
        return jsonify({"error": "Acción no encontrada"}), 404

    body = request.get_json(silent=True) or {}

    cantidad = body.get("cantidad")
    precio_compra = body.get("precio_compra")

    if cantidad is not None:
        try:
            cantidad = int(cantidad)
        except (TypeError, ValueError):
            return jsonify({"error": "cantidad debe ser un entero"}), 400
        if cantidad < 0:
            return jsonify({"error": "cantidad no puede ser negativa"}), 400

    if precio_compra is not None:
        try:
            precio_compra = float(precio_compra)
        except (TypeError, ValueError):
            return jsonify({"error": "precio_compra debe ser un número"}), 400
        if precio_compra < 0:
            return jsonify({"error": "precio_compra no puede ser negativo"}), 400

    with _portfolio_lock:
        if cantidad is not None:
            PORTAFOLIO[ticker]["cantidad"] = cantidad
        if precio_compra is not None:
            PORTAFOLIO[ticker]["precio_compra"] = precio_compra
        _save_portfolio()

    return jsonify(data_service.get_summary(ticker) or {"ok": True})


@app.route("/api/dividendos_manuales")
def api_dividendos_manuales():
    """Lista los dividendos ingresados a mano, opcionalmente filtrados por
    ticker (?ticker=XXX.SN)."""
    ticker = request.args.get("ticker")
    if ticker and ticker not in PORTAFOLIO:
        return jsonify({"error": "Acción no encontrada"}), 404
    return jsonify(data_service.list_manual_dividends(ticker))


@app.route("/api/dividendos")
def api_dividendos_todos():
    """Todos los dividendos "conocidos" de la cartera: los oficiales de la
    BCS (dividendos_bcs.py, no editables desde acá) más los ingresados a
    mano en /posiciones. Para mostrarlos juntos en la UI; el CRUD real
    sigue siendo por /api/dividendos_manuales."""
    ticker = request.args.get("ticker")
    if ticker and ticker not in PORTAFOLIO:
        return jsonify({"error": "Acción no encontrada"}), 404
    return jsonify(data_service.list_dividendos_todos(ticker))


@app.route("/api/dividendos_manuales", methods=["POST"])
def api_crear_dividendo_manual():
    """Registra un dividendo real pagado (fecha + monto por acción)."""
    body = request.get_json(silent=True) or {}
    ticker = body.get("ticker")
    date = body.get("date")
    amount = body.get("amount")

    if ticker not in PORTAFOLIO:
        return jsonify({"error": "Acción no encontrada"}), 404
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return jsonify({"error": "date debe tener formato YYYY-MM-DD"}), 400
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "amount debe ser un número"}), 400
    if amount <= 0:
        return jsonify({"error": "amount debe ser mayor que 0"}), 400

    new_id = data_service.add_manual_dividend(ticker, date, amount)
    return jsonify({"id": new_id, "ticker": ticker, "date": date, "amount": amount}), 201


@app.route("/api/dividendos_manuales/<int:dividend_id>", methods=["DELETE"])
def api_borrar_dividendo_manual(dividend_id):
    ok = data_service.delete_manual_dividend(dividend_id)
    if not ok:
        return jsonify({"error": "Dividendo no encontrado"}), 404
    return jsonify({"ok": True})


@app.route("/api/aportes")
def api_aportes():
    """Lista los aportes (depósitos) a la corredora ingresados a mano."""
    return jsonify(data_service.list_aportes())


@app.route("/api/aportes", methods=["POST"])
def api_crear_aporte():
    """Registra un depósito real a la cuenta de inversión (fecha + monto)."""
    body = request.get_json(silent=True) or {}
    date = body.get("date")
    amount = body.get("amount")

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return jsonify({"error": "date debe tener formato YYYY-MM-DD"}), 400
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "amount debe ser un número"}), 400
    if amount <= 0:
        return jsonify({"error": "amount debe ser mayor que 0"}), 400

    new_id = data_service.add_aporte(date, amount)
    return jsonify({"id": new_id, "date": date, "amount": amount}), 201


@app.route("/api/aportes/<int:aporte_id>", methods=["DELETE"])
def api_borrar_aporte(aporte_id):
    ok = data_service.delete_aporte(aporte_id)
    if not ok:
        return jsonify({"error": "Aporte no encontrado"}), 404
    return jsonify({"ok": True})


def _num_opcional(body, campo):
    """Convierte un campo opcional a float, o None si viene vacío/ausente.
    Lanza ValueError si viene con un valor no numérico."""
    v = body.get(campo)
    if v is None or v == "":
        return None
    return float(v)


@app.route("/api/eeff_trimestral")
def api_eeff_trimestral():
    """Lista los EEFF trimestrales ingresados a mano, opcionalmente
    filtrados por ticker (?ticker=XXX.SN)."""
    ticker = request.args.get("ticker")
    if ticker and ticker not in PORTAFOLIO:
        return jsonify({"error": "Acción no encontrada"}), 404
    return jsonify(data_service.list_eeff_trimestral(ticker))


@app.route("/api/eeff_trimestral", methods=["POST"])
def api_crear_eeff_trimestral():
    """Crea o actualiza (por ticker+año+trimestre) un registro de EEFF
    trimestral transcrito a mano desde el PDF del portal CMF."""
    body = request.get_json(silent=True) or {}
    ticker = body.get("ticker")
    if ticker not in PORTAFOLIO:
        return jsonify({"error": "Acción no encontrada"}), 404

    try:
        anio = int(body.get("anio"))
    except (TypeError, ValueError):
        return jsonify({"error": "anio debe ser un entero"}), 400
    if anio < 2000 or anio > datetime.utcnow().year + 1:
        return jsonify({"error": "anio fuera de rango"}), 400

    try:
        trimestre = int(body.get("trimestre"))
    except (TypeError, ValueError):
        return jsonify({"error": "trimestre debe ser un entero"}), 400
    if trimestre not in (1, 2, 3, 4):
        return jsonify({"error": "trimestre debe ser 1, 2, 3 o 4"}), 400

    try:
        ingresos = _num_opcional(body, "ingresos")
        utilidad_neta = _num_opcional(body, "utilidad_neta")
        roe = _num_opcional(body, "roe")
        deuda_patrimonio = _num_opcional(body, "deuda_patrimonio")
        margen_neto = _num_opcional(body, "margen_neto")
        resultado_operacional = _num_opcional(body, "resultado_operacional")
        depreciacion_amortizacion = _num_opcional(body, "depreciacion_amortizacion")
        deuda_financiera = _num_opcional(body, "deuda_financiera")
        efectivo_equivalentes = _num_opcional(body, "efectivo_equivalentes")
    except ValueError:
        return jsonify({"error": "los campos numéricos deben ser números"}), 400

    new_id = data_service.upsert_eeff_trimestral(
        ticker, anio, trimestre, ingresos, utilidad_neta, roe,
        deuda_patrimonio, margen_neto, resultado_operacional,
        depreciacion_amortizacion, deuda_financiera, efectivo_equivalentes,
    )
    return jsonify({"id": new_id, "ticker": ticker, "anio": anio, "trimestre": trimestre}), 201


@app.route("/api/eeff_trimestral/<int:eeff_id>", methods=["DELETE"])
def api_borrar_eeff_trimestral(eeff_id):
    ok = data_service.delete_eeff_trimestral(eeff_id)
    if not ok:
        return jsonify({"error": "Registro no encontrado"}), 404
    return jsonify({"ok": True})


@app.route("/api/operaciones")
def api_operaciones():
    """Historial de operaciones (compra/venta) importadas de comprobantes
    PDF, opcionalmente filtradas por ticker (?ticker=XXX.SN)."""
    ticker = request.args.get("ticker")
    return jsonify(data_service.list_operaciones(ticker))


@app.route("/api/comprobantes/sync", methods=["POST"])
def api_comprobantes_sync():
    """Escanea la carpeta de comprobantes montada (ver
    config.COMPROBANTES_DIR) e importa las operaciones nuevas a la tabla
    `operaciones`. No toca portfolio.json — para eso ver
    /api/posiciones/recalculo_preview y /api/posiciones/recalculo_aplicar."""
    return jsonify(comprobantes.sync_comprobantes())


@app.route("/api/posiciones/recalculo_preview")
def api_recalculo_preview():
    """Compara la cantidad/precio_compra actuales de portfolio.json contra
    lo que resulta de recalcularlos desde el historial completo de
    `operaciones` (costo promedio ponderado). No modifica nada — solo
    informa las diferencias para que el usuario decida qué aplicar en
    /api/posiciones/recalculo_aplicar."""
    calculado = data_service.recalcular_posiciones()
    preview = []
    for ticker, datos in calculado.items():
        actual = PORTAFOLIO.get(ticker, {})
        preview.append({
            "ticker": ticker,
            "nombre": actual.get("nombre"),
            "es_nuevo": ticker not in PORTAFOLIO,
            "cantidad_actual": actual.get("cantidad"),
            "cantidad_calculada": datos["cantidad"],
            "precio_compra_actual": actual.get("precio_compra"),
            "precio_compra_calculado": round(datos["precio_compra"], 4),
            "cambia": (
                ticker not in PORTAFOLIO
                or actual.get("cantidad") != datos["cantidad"]
                or round(actual.get("precio_compra", 0), 4) != round(datos["precio_compra"], 4)
            ),
        })
    preview.sort(key=lambda p: (not p["cambia"], p["ticker"]))
    return jsonify(preview)


@app.route("/api/posiciones/recalculo_aplicar", methods=["POST"])
def api_recalculo_aplicar():
    """Aplica a portfolio.json el recálculo de cantidad/precio_compra desde
    `operaciones` para los tickers indicados (o todos, si no se especifica).
    Los tickers nuevos (comprados en algún comprobante pero ausentes de
    portfolio.json) se agregan con el ticker como nombre por defecto —
    editable después a mano en /posiciones."""
    body = request.get_json(silent=True) or {}
    tickers_pedidos = body.get("tickers")

    calculado = data_service.recalcular_posiciones()
    if tickers_pedidos:
        calculado = {t: v for t, v in calculado.items() if t in tickers_pedidos}

    with _portfolio_lock:
        for ticker, datos in calculado.items():
            if ticker not in PORTAFOLIO:
                PORTAFOLIO[ticker] = {"nombre": ticker.replace(".SN", ""), "cantidad": 0, "precio_compra": 0}
            PORTAFOLIO[ticker]["cantidad"] = datos["cantidad"]
            PORTAFOLIO[ticker]["precio_compra"] = round(datos["precio_compra"], 4)
        _save_portfolio()
        data_service.set_portfolio(PORTAFOLIO)

    return jsonify({"aplicados": list(calculado.keys())})


# --------------------------------------------------------------------------- #
# Arranque
# --------------------------------------------------------------------------- #
data_service.init_db()
threading.Thread(target=_warmup_data, daemon=True).start()

# Refresco automático diario tras el cierre de la bolsa (~17:15 hora Chile).
data_service.start_scheduler()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
