"""Indicadores macroeconómicos chilenos (mindicador.cl).

Fuente: API pública del Banco Central / mindicador.cl — no requiere key.
https://mindicador.cl/api

Se usan como contexto para las decisiones de inversión:
- uf:    Unidad de Fomento (valor diario, referencia de inflación/indexación)
- dolar: Tipo de cambio USD/CLP observado
- utm:   Unidad Tributaria Mensual
- ipc:   Variación mensual del IPC
- tpm:   Tasa de Política Monetaria del Banco Central
- libra_cobre: Precio de la libra de cobre (USD), motor del IPSA
"""

import logging
from datetime import datetime, timedelta

import requests

log = logging.getLogger("indicadores_macro")

BASE_URL = "https://mindicador.cl/api"
INDICADORES = ["uf", "dolar", "utm", "ipc", "tpm", "libra_cobre"]
TIMEOUT = 10


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


def fetch_all():
    """Descarga todos los indicadores configurados. Devuelve dict codigo -> records."""
    resultado = {}
    for codigo in INDICADORES:
        recs = _fetch_indicador(codigo)
        resultado[codigo] = recs
        log.info("%s: %d puntos descargados", codigo, len(recs))
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
