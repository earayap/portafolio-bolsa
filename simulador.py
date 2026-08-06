"""Simulador de Monte Carlo: ¿qué tan probable es que una acción suba o baje?

Es una simulación estadística basada en el retorno y la volatilidad
históricos de la propia acción (movimiento geométrico browniano) — NO una
predicción. No hay proyecciones de negocio, catalizadores ni eventos
futuros: solo se asume que el comportamiento pasado del precio (su "ruido"
estadístico) es representativo del futuro cercano, lo cual es una hipótesis
razonable a semanas o meses, pero se degrada mientras más largo el horizonte.

Se usan retornos LOGARÍTMICOS diarios (no simples) porque son los que se
componen correctamente en el tiempo bajo el modelo geométrico browniano:
precio_t = precio_0 * exp((mu - 0.5*sigma^2)*t + sigma*sqrt(t)*Z)
"""

import numpy as np

import data_service

LOOKBACK_DIAS = 504  # ~2 años de retornos diarios para estimar mu y sigma
N_SIMULACIONES = 2000
PUNTOS_ABANICO = 24  # cuántos días del horizonte se envían al frontend para el gráfico

HORIZONTES = {"1m": 21, "3m": 63, "6m": 126, "1a": 252}


def _semilla(ticker, fecha, horizonte_dias):
    """Semilla determinística: mismos resultados el mismo día para el mismo
    ticker/horizonte (no cambia cada vez que se recarga la página), pero se
    actualiza al día siguiente con datos nuevos."""
    clave = f"{ticker}-{fecha}-{horizonte_dias}"
    return abs(hash(clave)) % (2**32)


def simular(ticker, horizonte_key="6m"):
    horizonte_dias = HORIZONTES.get(horizonte_key, HORIZONTES["6m"])

    data = data_service.get_history(ticker)
    records = data["records"]
    if len(records) < 60:
        return None

    ventana = records[-LOOKBACK_DIAS:]
    closes = np.array([r["close"] for r in ventana], dtype=float)
    log_returns = np.diff(np.log(closes))
    if len(log_returns) < 30:
        return None

    mu = float(np.mean(log_returns))
    sigma = float(np.std(log_returns, ddof=1))
    precio_actual = float(closes[-1])
    fecha_actual = records[-1]["date"]

    rng = np.random.default_rng(_semilla(ticker, fecha_actual, horizonte_dias))
    incrementos = rng.normal(
        loc=(mu - 0.5 * sigma**2), scale=sigma, size=(N_SIMULACIONES, horizonte_dias)
    )
    log_acumulado = np.cumsum(incrementos, axis=1)
    trayectorias = precio_actual * np.exp(log_acumulado)  # (N_SIMULACIONES, horizonte_dias)

    precios_finales = trayectorias[:, -1]
    probabilidad_sube = float(np.mean(precios_finales > precio_actual) * 100)
    p10, p50, p90 = np.percentile(precios_finales, [10, 50, 90])

    # Abanico: percentiles por día, submuestreado a PUNTOS_ABANICO puntos
    # para no mandar miles de valores al frontend.
    dias_muestra = np.unique(np.linspace(0, horizonte_dias - 1, PUNTOS_ABANICO).astype(int))
    abanico = []
    for d in dias_muestra:
        col = trayectorias[:, d]
        ap10, ap50, ap90 = np.percentile(col, [10, 50, 90])
        abanico.append({
            "dia": int(d) + 1,
            "p10": round(float(ap10), 2),
            "p50": round(float(ap50), 2),
            "p90": round(float(ap90), 2),
        })

    historico = [
        {"date": r["date"], "close": r["close"]} for r in records[-60:]
    ]

    return {
        "ticker": ticker,
        "precio_actual": round(precio_actual, 2),
        "fecha_actual": fecha_actual,
        "horizonte": horizonte_key,
        "horizonte_dias": horizonte_dias,
        "n_simulaciones": N_SIMULACIONES,
        "drift_diario_pct": round(mu * 100, 4),
        "volatilidad_diaria_pct": round(sigma * 100, 4),
        "probabilidad_sube": round(probabilidad_sube, 1),
        "probabilidad_baja": round(100 - probabilidad_sube, 1),
        "percentiles_finales": {
            "p10": round(float(p10), 2),
            "p50": round(float(p50), 2),
            "p90": round(float(p90), 2),
        },
        "retorno_esperado_pct": round((p50 - precio_actual) / precio_actual * 100, 2),
        "historico": historico,
        "abanico": abanico,
    }
