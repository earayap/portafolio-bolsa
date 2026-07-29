"""Aplicación Flask: Portafolio Bolsa de Comercio de Santiago."""

import json
import os
import sys
import threading

from flask import Flask, jsonify, render_template, request

import config
import data_service

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

# Estado de la carga inicial en segundo plano
_warmup = {"done": False, "results": {}}


def _warmup_data():
    """Descarga/hidrata la cache al arrancar, sin bloquear el servidor."""
    data_service.init_db()
    _warmup["results"] = data_service.refresh_all(force=False)
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


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Fuerza una actualización desde la API pública."""
    ticker = request.args.get("ticker")
    if ticker and ticker in PORTAFOLIO:
        src, n = data_service.refresh_ticker(ticker, force=True)
        return jsonify({"ticker": ticker, "source": src, "rows": n})
    results = data_service.refresh_all(force=True)
    return jsonify(results)


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
