"""Backtest del screener: ¿la señal COMPRAR/MANTENER/VENDER de hace tiempo
habría anticipado buen o mal desempeño futuro?

Alcance y limitación deliberada: solo se backtestea la parte del score que
depende de precios y dividendos (Sharpe, volatilidad, beta, drawdown,
retorno real, dividend yield). Los fundamentales (P/E, ROE) NO se
backtestean porque solo se guarda el dato fundamental actual, no su
historia — incluirlos daría una falsa sensación de precisión.

Cada punto de evaluación usa exclusivamente datos disponibles HASTA esa
fecha (ventana de 1 año hacia atrás, dividendos pagados hasta ese día) y
mide el retorno realizado en los HORIZON_DAYS siguientes. Comparar el
retorno futuro promedio de las señales COMPRAR vs VENDER es lo que valida
(o no) si las reglas del score realmente distinguen algo.
"""

import config
import data_service
import screener

STEP_DAYS = 21   # ~1 mes bursátil entre puntos de evaluación
HORIZON_DAYS = 126  # ~6 meses de retorno "hacia adelante" para juzgar la señal


def _evaluar_en(records, bench_por_fecha, indice, ticker):
    """Replica screener._score usando solo datos hasta `indice` (sin fundamentales)."""
    ventana = records[max(0, indice - screener.LOOKBACK_DAYS + 1):indice + 1]
    closes = [r["close"] for r in ventana]
    if len(closes) < 30:
        return None

    fecha = records[indice]["date"]
    fecha_inicio = ventana[0]["date"]

    bench_closes = [bench_por_fecha[r["date"]] for r in ventana if r["date"] in bench_por_fecha]

    returns = screener._daily_returns(closes)
    bench_returns = screener._daily_returns(bench_closes) if len(bench_closes) > 1 else []

    vol = screener._annualized_vol(returns)
    ret_anual = screener._annualized_return(closes)
    tpm = data_service.get_indicador_valor_en("tpm", fecha)
    tasa_libre_riesgo = (tpm / 100) if tpm is not None else 0.0
    sharpe = (ret_anual - tasa_libre_riesgo) / vol if vol else None

    beta = screener._beta(returns, bench_returns) if bench_returns else None
    max_dd = screener._max_drawdown(closes)

    uf_fin = data_service.get_indicador_valor_en("uf", fecha)
    uf_inicio = data_service.get_indicador_valor_en("uf", fecha_inicio)
    uf_var = (uf_fin - uf_inicio) / uf_inicio if (uf_fin and uf_inicio) else None

    precio_actual = closes[-1]
    div_ps = data_service.get_dividends_per_share_rango(ticker, fecha_inicio, fecha)
    dividend_yield = (div_ps / precio_actual) if precio_actual else None

    retorno_ventana = (closes[-1] / closes[0] - 1) if closes[0] else 0.0
    retorno_real = (retorno_ventana - uf_var) if uf_var is not None else None

    score, _ = screener._score(sharpe, dividend_yield, retorno_real, max_dd, beta, fund=None)
    return score, screener._senal(score)


def backtest_ticker(ticker, years=3):
    """Lista de puntos {date, score, senal, retorno_fwd} para un ticker.
    None si no hay suficiente historia."""
    data = data_service.get_history(ticker)
    records = data["records"]
    bench = data_service.get_history(config.BENCHMARK_TICKER)
    bench_por_fecha = {r["date"]: r["close"] for r in bench["records"]}

    n = len(records)
    if n < screener.LOOKBACK_DAYS + STEP_DAYS + HORIZON_DAYS:
        return None

    max_eval_idx = n - HORIZON_DAYS - 1
    min_eval_idx = max(screener.LOOKBACK_DAYS, max_eval_idx - years * 252)
    if min_eval_idx >= max_eval_idx:
        return None

    puntos = []
    for i in range(min_eval_idx, max_eval_idx, STEP_DAYS):
        resultado = _evaluar_en(records, bench_por_fecha, i, ticker)
        if resultado is None:
            continue
        score, senal = resultado
        precio_hoy = records[i]["close"]
        precio_futuro = records[i + HORIZON_DAYS]["close"]
        if not precio_hoy:
            continue
        retorno_fwd = (precio_futuro - precio_hoy) / precio_hoy
        puntos.append({
            "date": records[i]["date"],
            "score": score,
            "senal": senal,
            "retorno_fwd": round(retorno_fwd * 100, 2),
        })
    return puntos or None


def _resumen(puntos):
    por_senal = {"COMPRAR": [], "MANTENER": [], "VENDER": []}
    for p in puntos:
        por_senal[p["senal"]].append(p["retorno_fwd"])
    resumen = {}
    for senal, retornos in por_senal.items():
        if not retornos:
            resumen[senal] = {"n": 0, "retorno_promedio": None, "tasa_acierto": None}
            continue
        promedio = sum(retornos) / len(retornos)
        positivos = sum(1 for r in retornos if r > 0)
        resumen[senal] = {
            "n": len(retornos),
            "retorno_promedio": round(promedio, 2),
            "tasa_acierto": round(positivos / len(retornos) * 100, 1),
        }
    return resumen


def backtest_portafolio(tickers, years=3):
    """Backtest agregado del portafolio: resumen global + detalle por acción."""
    detalle = {}
    todos_los_puntos = []
    for t in tickers:
        try:
            puntos = backtest_ticker(t, years=years)
        except Exception:
            puntos = None
        if puntos:
            detalle[t] = {"resumen": _resumen(puntos), "puntos": puntos}
            todos_los_puntos.extend(puntos)

    return {
        "horizonte_dias": HORIZON_DAYS,
        "anios": years,
        "resumen_global": _resumen(todos_los_puntos),
        "detalle_por_accion": detalle,
    }
