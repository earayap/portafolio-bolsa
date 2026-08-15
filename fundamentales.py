"""Datos fundamentales por acción.

La CMF (Comisión para el Mercado Financiero) NO ofrece una API pública para
emisores no bancarios: su API abierta (api.cmfchile.cl) cubre solo bancos e
instituciones financieras. Para el resto de las acciones del portafolio
(industriales, utilities, retail, holdings), los EEFF sólo se pueden
descargar manualmente en PDF desde el portal de la CMF — no son
automatizables.

Por eso se usa yfinance (`Ticker.info`) como única fuente automatizable de
fundamentales para todo el portafolio. Es best-effort: para acciones
chilenas de baja liquidez, Yahoo Finance a veces devuelve campos con
órdenes de magnitud absurdas (ej. price-to-book > 1000, margen neto > 800%).
Esos valores se descartan con un filtro de sanidad y se muestran como no
disponibles en vez de un dato engañoso.
"""

import logging

log = logging.getLogger("fundamentales")

CAMPOS_NUMERICOS = {
    # campo yfinance -> (mínimo, máximo) razonable
    "trailingPE": (-300, 300),
    "forwardPE": (-300, 300),
    "priceToBook": (0, 50),
    "returnOnEquity": (-2, 2),
    "profitMargins": (-2, 2),
    "debtToEquity": (0, 1000),
    # EV/EBITDA puede ser negativo (EBITDA negativo) sin ser un error de dato,
    # a diferencia de trailingPE ese caso sí es una señal real (negocio que
    # pierde a nivel operativo), por eso se deja un rango amplio simétrico.
    "enterpriseToEbitda": (-300, 300),
    "enterpriseToRevenue": (-5, 50),
}


def _sane(campo, valor):
    if valor is None or isinstance(valor, str):  # yfinance a veces devuelve "Infinity"
        return None
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    lo, hi = CAMPOS_NUMERICOS[campo]
    return v if lo <= v <= hi else None


def fetch(ticker):
    """Descarga y sanitiza los fundamentales de un ticker. None si falla."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
    except Exception as exc:
        log.warning("No se pudo obtener info fundamental de %s: %s", ticker, exc)
        return None
    if not info:
        return None

    ev = info.get("enterpriseValue")
    ebitda = info.get("ebitda")
    revenue = info.get("totalRevenue")

    # Para varias acciones de baja liquidez de la BCS, yfinance trae el EV, el
    # EBITDA y los ingresos por separado pero NO el ratio precalculado
    # (enterpriseToEbitda/enterpriseToRevenue vienen None). En vez de perder
    # cobertura para media cartera, se calcula el ratio a mano cuando hay
    # insumos suficientes, y pasa por el mismo filtro de sanidad.
    ev_to_ebitda = info.get("enterpriseToEbitda")
    if ev_to_ebitda is None and ev is not None and ebitda:
        ev_to_ebitda = ev / ebitda

    ev_to_revenue = info.get("enterpriseToRevenue")
    if ev_to_revenue is None and ev is not None and revenue:
        ev_to_revenue = ev / revenue

    return {
        "trailing_pe": _sane("trailingPE", info.get("trailingPE")),
        "forward_pe": _sane("forwardPE", info.get("forwardPE")),
        "price_to_book": _sane("priceToBook", info.get("priceToBook")),
        "return_on_equity": _sane("returnOnEquity", info.get("returnOnEquity")),
        "profit_margin": _sane("profitMargins", info.get("profitMargins")),
        "debt_to_equity": _sane("debtToEquity", info.get("debtToEquity")),
        "market_cap": info.get("marketCap"),
        "sector": info.get("sector"),
        # Valor de Empresa (EV) — a diferencia del P/E, descuenta la deuda
        # neta de cada empresa, así que compara negocios con distinta
        # estructura de capital de forma más justa. Ver valor_empresa.py.
        "enterprise_value": ev,
        "ev_to_ebitda": _sane("enterpriseToEbitda", ev_to_ebitda),
        "ev_to_revenue": _sane("enterpriseToRevenue", ev_to_revenue),
        "ebitda": ebitda,
        "total_revenue": revenue,
    }
