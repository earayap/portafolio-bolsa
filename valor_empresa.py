"""Analizador de acciones baratas por Valor de Empresa (Enterprise Value).

A diferencia del P/E (usado en screener.py), el EV/EBITDA y el EV/Ventas
descuentan la deuda neta de cada empresa, así que comparan negocios con
distinta estructura de capital (una industrial muy apalancada vs. una con
caja neta) de forma más justa. Mismo enfoque "cash flow real, sin
narrativa" del resto del proyecto: sin proyecciones. No se mezcla con el
score de riesgo/retorno del screener — es una lectura de valoración pura.

Fuente preferida: el EEFF trimestral cargado a mano en /posiciones
(resultado operacional + D&A, deuda financiera, efectivo, ingresos —
transcritos del informe auditado de la CMF), agregado a TTM (últimos 4
trimestres) cuando hay suficiente historial cargado. Mismo criterio que
dividendos_bcs.py: para acciones chilenas el dato oficial tiene
precedencia sobre yfinance, que puede traer EV/EBITDA poco confiable para
tickers ilíquidos de la BCS (market cap mal estimado, EBITDA TTM que no
calza con el calendario fiscal chileno). yfinance solo se usa como
respaldo cuando no hay EEFF manual cargado (o no alcanza para TTM/1
trimestre). Ver _desde_manual().
"""

import data_service

# El EEFF trimestral manual se ingresa en millones de CLP (mismo formato que
# el resto del formulario de /posiciones); yfinance reporta en CLP nominal.
_MM = 1_000_000

# Múltiplos "justos" para el precio de compra objetivo (ver _precio_justo):
# el mismo techo que ya usa _score() para clasificar "BARATA" (no un DCF ni
# una proyección — coherente con el enfoque "cash flow real, sin narrativa"
# del resto del proyecto). EV/EBITDA es la base primaria; EV/Ventas 1x solo
# se usa de respaldo cuando el EBITDA es negativo o nulo (ahí el múltiplo de
# EBITDA no tiene un precio "justo" bien definido).
FAIR_EV_EBITDA = 8
FAIR_EV_REVENUE = 1


def _clasificacion(score):
    if score >= 2:
        return "BARATA"
    if score <= -2:
        return "CARA"
    return "NEUTRAL"


def _score(ev_ebitda, ev_revenue, ebitda):
    score = 0
    razones = []

    if ebitda is not None and ebitda <= 0:
        score -= 2
        razones.append("EBITDA negativo o nulo: el negocio no genera utilidad operativa")
    elif ev_ebitda is not None:
        if ev_ebitda <= 5:
            score += 2
            razones.append(f"EV/EBITDA muy bajo ({ev_ebitda:.1f}x)")
        elif ev_ebitda <= 8:
            score += 1
            razones.append(f"EV/EBITDA bajo ({ev_ebitda:.1f}x)")
        elif ev_ebitda > 14:
            score -= 1
            razones.append(f"EV/EBITDA alto ({ev_ebitda:.1f}x)")

    if ev_revenue is not None:
        if ev_revenue <= 1:
            score += 1
            razones.append(f"EV/Ventas bajo ({ev_revenue:.2f}x)")
        elif ev_revenue > 4:
            score -= 1
            razones.append(f"EV/Ventas alto ({ev_revenue:.2f}x)")

    return score, razones


def _desde_manual(ticker, market_cap):
    """Reconstruye EV/EBITDA y EV/Ventas desde los EEFF trimestrales
    ingresados a mano en /posiciones. Usa la suma de los últimos 4
    trimestres (TTM) en vez de un solo trimestre: el EV/EBITDA de yfinance
    también es TTM, y comparar EV contra el EBITDA de un solo trimestre
    infla el múltiplo ~4x. Si hay menos de 4 trimestres cargados, cae a un
    solo trimestre (peor que nada, pero mejor marcarlo como tal en
    "fuente"). None si no hay ninguna carga."""
    ttm = data_service.get_eeff_trimestral_ttm(ticker)
    if ttm:
        return _calcular_ev(ttm, market_cap, ttm["periodo"])

    eeff = data_service.get_eeff_trimestral_ultimo(ticker)
    if not eeff:
        return None
    periodo = f"{eeff['anio']}-Q{eeff['trimestre']} (1 trimestre, no TTM)"
    return _calcular_ev(eeff, market_cap, periodo)


def _calcular_ev(eeff, market_cap, periodo):
    resultado_operacional = eeff.get("resultado_operacional")
    dep_amort = eeff.get("depreciacion_amortizacion")
    deuda_financiera = eeff.get("deuda_financiera")
    efectivo = eeff.get("efectivo_equivalentes")
    ingresos = eeff.get("ingresos")

    ebitda = None
    if resultado_operacional is not None and dep_amort is not None:
        ebitda = (resultado_operacional + dep_amort) * _MM

    ev = None
    if market_cap is not None and deuda_financiera is not None:
        deuda_neta = deuda_financiera * _MM - (efectivo * _MM if efectivo is not None else 0)
        ev = market_cap + deuda_neta

    revenue = ingresos * _MM if ingresos is not None else None
    ev_ebitda = ev / ebitda if (ev is not None and ebitda) else None
    ev_revenue = ev / revenue if (ev is not None and revenue) else None

    if ev_ebitda is None and ev_revenue is None:
        return None

    return {
        "ev": ev, "ev_ebitda": ev_ebitda, "ev_revenue": ev_revenue,
        "ebitda": ebitda, "revenue": revenue,
        "periodo": periodo,
    }


def _precio_justo(ebitda, revenue, ev, market_cap, precio_actual):
    """Precio de compra objetivo por acción: el precio al que el mercado
    estaría pagando exactamente el múltiplo "barata" (FAIR_EV_EBITDA, o
    FAIR_EV_REVENUE si el EBITDA no sirve de base), no más.

    Deuda neta se despeja del propio EV ya calculado (ev = market_cap +
    deuda_neta, para cualquier fuente — manual o yfinance), así que no hace
    falta traer de nuevo deuda_financiera/efectivo. Acciones fuera de
    /posiciones o sin historial de precio (market_cap o precio_actual en 0
    o None) no tienen un precio justo calculable — se omite (None) en vez
    de mostrar una cifra engañosa.

    None también cuando el "EV justo" (múltiplo × EBITDA o ventas) no
    alcanza a cubrir la deuda neta actual: significa que, a este nivel de
    apalancamiento, ni el negocio completo vale lo suficiente para que haya
    un precio por acción positivo bajo este método — un resultado honesto
    para una empresa muy endeudada, no un error de cálculo."""
    if not market_cap or not precio_actual or ev is None:
        return None

    deuda_neta = ev - market_cap

    ev_justo = None
    if ebitda is not None and ebitda > 0:
        ev_justo = FAIR_EV_EBITDA * ebitda
    elif revenue is not None and revenue > 0:
        ev_justo = FAIR_EV_REVENUE * revenue
    if ev_justo is None:
        return None

    market_cap_justo = ev_justo - deuda_neta
    if market_cap_justo <= 0:
        return None

    return market_cap_justo * precio_actual / market_cap


def evaluate(ticker):
    """Calcula la clasificación de valoración por EV para un ticker.

    Orden de fuentes: EEFF trimestral manual primero (informe auditado de
    la CMF, mismo criterio de precedencia que dividendos_bcs.py sobre
    yfinance para acciones chilenas); si no hay EEFF cargado (o no trae
    suficiente para calcular ni EV/EBITDA ni EV/Ventas), se cae a yfinance.
    Si tampoco hay eso, la clasificación queda "SIN DATOS" en vez de omitir
    el ticker.
    """
    fund = data_service.get_fundamentales(ticker) or {}
    ev = ev_ebitda = ev_revenue = ebitda = revenue = None
    fuente = None

    manual = _desde_manual(ticker, fund.get("market_cap"))
    if manual:
        ev = manual["ev"]
        ev_ebitda = manual["ev_ebitda"]
        ev_revenue = manual["ev_revenue"]
        ebitda = manual["ebitda"]
        revenue = manual["revenue"]
        fuente = f"manual ({manual['periodo']})"

    if ev_ebitda is None and ev_revenue is None:
        ev = fund.get("enterprise_value")
        ev_ebitda = fund.get("ev_to_ebitda")
        ev_revenue = fund.get("ev_to_revenue")
        ebitda = fund.get("ebitda")
        revenue = fund.get("total_revenue")
        if ev_ebitda is not None or ev_revenue is not None:
            fuente = "yfinance"

    resumen = data_service.get_summary(ticker)
    precio_actual = resumen.get("last_close") if resumen else None
    market_cap = fund.get("market_cap")

    if ev_ebitda is None and ev_revenue is None:
        return {
            "ticker": ticker,
            "enterprise_value": ev,
            "market_cap": market_cap,
            "precio_actual": precio_actual,
            "precio_justo": None,
            "diferencia_pct": None,
            "ebitda": ebitda,
            "revenue": revenue,
            "ev_ebitda": None,
            "ev_revenue": None,
            "sector": fund.get("sector"),
            "fuente": None,
            "score": 0,
            "clasificacion": "SIN DATOS",
            "razones": [
                "No hay EEFF trimestral cargado a mano en /posiciones, y Yahoo "
                "Finance tampoco reporta EV/EBITDA ni EV/Ventas para este ticker"
            ],
        }

    score, razones = _score(ev_ebitda, ev_revenue, ebitda)

    precio_justo = _precio_justo(ebitda, revenue, ev, market_cap, precio_actual)
    diferencia_pct = (
        round((precio_justo / precio_actual - 1) * 100, 1)
        if precio_justo is not None and precio_actual
        else None
    )

    return {
        "ticker": ticker,
        "enterprise_value": ev,
        "market_cap": market_cap,
        "precio_actual": precio_actual,
        "precio_justo": round(precio_justo, 2) if precio_justo is not None else None,
        "diferencia_pct": diferencia_pct,
        "ebitda": ebitda,
        "revenue": revenue,
        "ev_ebitda": round(ev_ebitda, 2) if ev_ebitda is not None else None,
        "ev_revenue": round(ev_revenue, 2) if ev_revenue is not None else None,
        "sector": fund.get("sector"),
        "fuente": fuente,
        "score": score,
        "clasificacion": _clasificacion(score),
        "razones": razones,
    }


def evaluate_all(tickers):
    resultados = [evaluate(t) for t in tickers]
    resultados.sort(key=lambda r: r["score"], reverse=True)
    return resultados
