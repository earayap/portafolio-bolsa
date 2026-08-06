"""Screener cuantitativo: métricas de riesgo/retorno y señal de decisión.

Filosofía "cash flow real, no hype": se pondera fuerte el dividend yield y el
Sharpe ratio (retorno ajustado por riesgo), y se penalizan la volatilidad alta
sin retorno que la respalde y los retornos reales negativos (descontada la
inflación vía UF). No hay narrativa ni proyecciones — solo números ya
observados.

Tasa libre de riesgo: se usa la TPM (Tasa de Política Monetaria) vigente,
por ser la referencia de corto plazo en CLP que publica el Banco Central.
"""

import math
from datetime import datetime, timedelta

import config
import data_service

TRADING_DAYS_YEAR = 252
LOOKBACK_DAYS = 252  # ~1 año bursátil


def _daily_returns(closes):
    return [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1]
    ]


def _annualized_return(closes):
    if len(closes) < 2 or not closes[0]:
        return 0.0
    total = closes[-1] / closes[0] - 1
    years = len(closes) / TRADING_DAYS_YEAR
    if years <= 0:
        return 0.0
    return (1 + total) ** (1 / years) - 1


def _annualized_vol(returns):
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS_YEAR)


def _max_drawdown(closes):
    peak = closes[0] if closes else 0
    max_dd = 0.0
    for c in closes:
        peak = max(peak, c)
        if peak:
            max_dd = min(max_dd, (c - peak) / peak)
    return max_dd  # negativo, ej. -0.35 = -35%


def _beta(returns, bench_returns):
    n = min(len(returns), len(bench_returns))
    if n < 2:
        return None
    r = returns[-n:]
    b = bench_returns[-n:]
    mean_r = sum(r) / n
    mean_b = sum(b) / n
    cov = sum((r[i] - mean_r) * (b[i] - mean_b) for i in range(n)) / (n - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (n - 1)
    if var_b == 0:
        return None
    return cov / var_b


def _uf_variacion(dias):
    """Variación % de la UF en los últimos `dias`. None si no hay datos suficientes."""
    hist = data_service.get_indicador_historial("uf", limit=dias + 5)
    if len(hist) < 2:
        return None
    return (hist[-1]["value"] - hist[0]["value"]) / hist[0]["value"]


def _senal(score):
    if score >= 3:
        return "COMPRAR"
    if score < 0:
        return "VENDER"
    return "MANTENER"


def _score(sharpe, dividend_yield, retorno_real, max_dd, beta, fund=None):
    score = 0
    razones = []

    if sharpe is not None:
        if sharpe > 1:
            score += 2
            razones.append(f"Sharpe alto ({sharpe:.2f})")
        elif sharpe > 0.5:
            score += 1
        elif sharpe < 0:
            score -= 2
            razones.append(f"Sharpe negativo ({sharpe:.2f}): retorno no compensa el riesgo")

    if dividend_yield is not None:
        if dividend_yield > 0.05:
            score += 2
            razones.append(f"Dividend yield alto ({dividend_yield * 100:.1f}%)")
        elif dividend_yield > 0.02:
            score += 1

    if retorno_real is not None:
        if retorno_real < -0.10:
            score -= 2
            razones.append(f"Retorno real negativo tras UF ({retorno_real * 100:.1f}%)")
        elif retorno_real > 0.05:
            score += 1

    if max_dd is not None and max_dd < -0.40:
        score -= 1
        razones.append(f"Drawdown severo ({max_dd * 100:.1f}%)")

    if beta is not None and beta > 1.5 and (sharpe is None or sharpe < 0.5):
        score -= 1
        razones.append(f"Beta alto ({beta:.2f}) sin retorno ajustado que lo respalde")

    # Fundamentales (yfinance sanitizado — ver fundamentales.py). La CMF no
    # tiene API pública para emisores no bancarios, así que estos campos
    # pueden faltar; cuando faltan, simplemente no aportan al score.
    if fund:
        pe = fund.get("trailing_pe")
        if pe is not None:
            if 0 < pe <= 12:
                score += 1
                razones.append(f"P/E bajo ({pe:.1f}): barata en relación a sus utilidades")
            elif pe < 0 or pe > 30:
                score -= 1
                razones.append(f"P/E {'negativo' if pe < 0 else 'alto'} ({pe:.1f})")

        roe = fund.get("return_on_equity")
        if roe is not None:
            if roe > 0.15:
                score += 1
                razones.append(f"ROE alto ({roe * 100:.1f}%)")
            elif roe < 0.03:
                score -= 1
                razones.append(f"ROE bajo ({roe * 100:.1f}%): rentabilidad débil sobre patrimonio")

        # El apalancamiento es el modelo de negocio de un banco (BICE), no
        # una señal de riesgo comparable a la de una industrial u holding.
        deuda = fund.get("debt_to_equity")
        if deuda is not None and fund.get("sector") != "Financial Services" and deuda > 150:
            score -= 1
            razones.append(f"Deuda/Patrimonio alta ({deuda:.0f}) para una empresa no financiera")

    return score, razones


def evaluate(ticker):
    """Calcula todas las métricas y la señal de decisión para un ticker."""
    data = data_service.get_history(ticker)
    records = data["records"][-LOOKBACK_DAYS:]
    closes = [r["close"] for r in records]
    if len(closes) < 30:
        return None

    bench = data_service.get_history(config.BENCHMARK_TICKER)
    bench_closes = [r["close"] for r in bench["records"][-LOOKBACK_DAYS:]]

    returns = _daily_returns(closes)
    bench_returns = _daily_returns(bench_closes)

    vol = _annualized_vol(returns)
    ret_anual = _annualized_return(closes)
    tpm = data_service.get_indicadores().get("tpm", {}).get("value")
    tasa_libre_riesgo = (tpm / 100) if tpm is not None else 0.0
    sharpe = (ret_anual - tasa_libre_riesgo) / vol if vol else None

    beta = _beta(returns, bench_returns)
    max_dd = _max_drawdown(closes)

    since_1y = (datetime.utcnow().date() - timedelta(days=365)).isoformat()
    precio_actual = closes[-1]
    div_ps = data_service.get_dividends_per_share(ticker, since_1y)
    dividend_yield = (div_ps / precio_actual) if precio_actual else None

    uf_var = _uf_variacion(365)
    retorno_1y = data["records"][-1]["close"] / data["records"][max(0, len(data["records"]) - 252)]["close"] - 1
    retorno_real = (retorno_1y - uf_var) if uf_var is not None else None

    fund = data_service.get_fundamentales(ticker)
    score, razones = _score(sharpe, dividend_yield, retorno_real, max_dd, beta, fund)

    return {
        "ticker": ticker,
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "volatilidad_anual": round(vol * 100, 2),
        "retorno_anual": round(ret_anual * 100, 2),
        "beta_vs_ipsa": round(beta, 2) if beta is not None else None,
        "max_drawdown": round(max_dd * 100, 2),
        "dividend_yield": round(dividend_yield * 100, 2) if dividend_yield is not None else None,
        "retorno_real_uf": round(retorno_real * 100, 2) if retorno_real is not None else None,
        "tasa_libre_riesgo_tpm": tpm,
        "pe": fund.get("trailing_pe") if fund else None,
        "roe": round(fund["return_on_equity"] * 100, 2) if fund and fund.get("return_on_equity") is not None else None,
        "debt_to_equity": fund.get("debt_to_equity") if fund else None,
        "sector": fund.get("sector") if fund else None,
        "score": score,
        "senal": _senal(score),
        "razones": razones,
    }


def evaluate_all(tickers):
    resultados = []
    for t in tickers:
        try:
            r = evaluate(t)
        except Exception:
            r = None
        if r:
            resultados.append(r)
    resultados.sort(key=lambda r: r["score"], reverse=True)
    return resultados
