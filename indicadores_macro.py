"""Indicadores macroeconómicos chilenos (mindicador.cl) + commodities (yfinance).

Fuente: API pública del Banco Central / mindicador.cl — no requiere key.
https://mindicador.cl/api

Se usan como contexto para las decisiones de inversión:
- uf:    Unidad de Fomento (valor diario, referencia de inflación/indexación)
- dolar: Tipo de cambio USD/CLP observado
- utm:   Unidad Tributaria Mensual
- ipc:   Variación mensual del IPC
- tpm:   Tasa de Política Monetaria del Banco Central
- libra_cobre: Precio de la libra de cobre (USD), motor del IPSA

mindicador.cl no cubre otros commodities relevantes para exportadoras
chilenas (mineras de hierro, forestales), así que esos se completan con
futuros de Yahoo Finance vía yfinance (ver COMMODITY_TICKERS).

La celulosa (pulpa kraft) no tiene futuro en Yahoo Finance, pero sí se
transa como futuro de pulpa de madera blanqueada de coníferas en la Bolsa
de Futuros de Shanghái (SHFE, contrato "SP", cotizado en CNY/tonelada) —
es el mismo dato que muestra TradingEconomics en su página de Kraft Pulp.
Sina Finance publica el histórico diario de ese contrato continuo (SP0) en
un endpoint JSON no oficial pero público y sin key, usado también por
proyectos como akshare/tushare para datos de futuros chinos. Ver
_fetch_sina_futures.
"""

import logging
from datetime import datetime, timedelta

import requests

log = logging.getLogger("indicadores_macro")

BASE_URL = "https://mindicador.cl/api"
INDICADORES = ["uf", "dolar", "utm", "ipc", "tpm", "libra_cobre"]
TIMEOUT = 10

# codigo interno -> ticker de futuro en Yahoo Finance
COMMODITY_TICKERS = {
    "hierro": "TIO=F",  # Mineral de hierro 62% Fe CFR China (TSI), SGX
    "oro": "GC=F",  # Oro, COMEX
}

SINA_FUTURES_URL = "https://stock2.finance.sina.com.cn/futures/api/json.php/InnerFuturesNewService.getDailyKLine"
# codigo interno -> símbolo de contrato continuo en Sina Finance
SINA_FUTURES = {
    "celulosa": "SP0",  # Pulpa de madera (纸浆) continuo, SHFE, CNY/tonelada
}


def _fetch_indicador(codigo):
    """Descarga la serie reciente de un indicador. Devuelve lista de
    {codigo, date, value} o [] si falla."""
    try:
        resp = requests.get(f"{BASE_URL}/{codigo}", timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("No se pudo descargar el indicador %s: %s", codigo, exc)
        return []

    records = []
    for punto in data.get("serie", []):
        # mindicador.cl devuelve fechas ISO con hora, p.ej. "2026-08-05T04:00:00.000Z"
        try:
            fecha = punto["fecha"][:10]
            valor = float(punto["valor"])
        except (KeyError, TypeError, ValueError):
            continue
        records.append({"codigo": codigo, "date": fecha, "value": valor})
    return records


def _fetch_commodity_yf(codigo, ticker, period="1mo"):
    """Descarga el precio de cierre diario de un futuro desde Yahoo Finance.
    Devuelve lista de {codigo, date, value} o [] si falla."""
    try:
        import yfinance as yf

        hist = yf.Ticker(ticker).history(period=period)
    except Exception as exc:
        log.warning("No se pudo descargar el commodity %s (%s): %s", codigo, ticker, exc)
        return []

    records = []
    for fecha, row in hist.iterrows():
        try:
            records.append(
                {"codigo": codigo, "date": fecha.strftime("%Y-%m-%d"), "value": float(row["Close"])}
            )
        except (KeyError, TypeError, ValueError):
            continue
    return records


def _fetch_sina_futures(codigo, symbol, limit=90):
    """Descarga el histórico diario de un contrato continuo de futuros desde
    Sina Finance (endpoint público no oficial, sin key). Usa el precio de
    liquidación diaria ("s") — es el que reportan los agregadores tipo
    TradingEconomics — con fallback al cierre ("c") si falta. Devuelve lista
    de {codigo, date, value} o [] si falla."""
    try:
        resp = requests.get(SINA_FUTURES_URL, params={"symbol": symbol}, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("No se pudo descargar el futuro Sina %s (%s): %s", codigo, symbol, exc)
        return []

    records = []
    for punto in data[-limit:]:
        try:
            valor = float(punto.get("s") or punto["c"])
            records.append({"codigo": codigo, "date": punto["d"], "value": valor})
        except (KeyError, TypeError, ValueError):
            continue
    return records


def fetch_all():
    """Descarga todos los indicadores + commodities configurados. Devuelve
    dict codigo -> records."""
    resultado = {}
    for codigo in INDICADORES:
        recs = _fetch_indicador(codigo)
        resultado[codigo] = recs
        log.info("%s: %d puntos descargados", codigo, len(recs))
    for codigo, ticker in COMMODITY_TICKERS.items():
        recs = _fetch_commodity_yf(codigo, ticker)
        resultado[codigo] = recs
        log.info("%s: %d puntos descargados (yfinance %s)", codigo, len(recs), ticker)
    for codigo, symbol in SINA_FUTURES.items():
        recs = _fetch_sina_futures(codigo, symbol)
        resultado[codigo] = recs
        log.info("%s: %d puntos descargados (Sina futures %s)", codigo, len(recs), symbol)
    return resultado


def fetch_uf_hoy():
    """Valor de la UF del día (o el último disponible). Devuelve dict o None."""
    recs = _fetch_indicador("uf")
    if not recs:
        return None
    return max(recs, key=lambda r: r["date"])


def fetch_historico(codigo, anios):
    """Descarga la serie de un indicador para varios años (mindicador.cl
    expone /api/<codigo>/<anio> con el año completo). Se usa para el
    backtesting, que necesita UF y TPM de años anteriores, no solo los
    últimos días que trae el endpoint por defecto."""
    records = []
    for anio in anios:
        try:
            resp = requests.get(f"{BASE_URL}/{codigo}/{anio}", timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("No se pudo descargar %s del año %s: %s", codigo, anio, exc)
            continue
        for punto in data.get("serie", []):
            try:
                fecha = punto["fecha"][:10]
                valor = float(punto["valor"])
            except (KeyError, TypeError, ValueError):
                continue
            records.append({"codigo": codigo, "date": fecha, "value": valor})
    return records
