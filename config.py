"""Configuración central de la aplicación.

El detalle de las acciones (tickers, nombres, cantidades y precios de compra)
vive en `portfolio.json` (datos reales, ignorado por git) o `portfolio.example.json`
(datos de ejemplo), cargado por `app.py` y inyectado a `data_service` al arrancar.
Aquí solo quedan los parámetros técnicos (rutas, ventana de historia y TTL de cache).
"""

import os
import sys

# BASE_DIR: carpeta donde viven los datos del usuario (portfolio.json, cache
# SQLite). Al correr empaquetado con PyInstaller, esto apunta junto al .exe
# (escribible) en vez de dentro del paquete de solo lectura.
# RESOURCE_DIR: carpeta con los assets de solo lectura del paquete (templates,
# static, portfolio.example.json). Coincide con BASE_DIR fuera de un .exe.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    RESOURCE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = BASE_DIR

# Ruta de la base de datos SQLite (cache local)
DB_PATH = os.path.join(BASE_DIR, "data", "portfolio.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Años de historia a descargar. 4 años porque el backtest (backtest.py)
# necesita puntos de evaluación repartidos en 3 años, cada uno mirando 1 año
# hacia atrás para calcular sus métricas (Sharpe, volatilidad, etc.).
HISTORY_YEARS = 4

# Tiempo (en horas) que un dato en cache se considera "fresco".
# Pasado este umbral, la app intenta actualizar desde la API.
CACHE_TTL_HOURS = 12

# --------------------------------------------------------------------------- #
# Refresco automático diario
#   La app corre un hilo en segundo plano que, una vez al día tras el cierre
#   de la Bolsa de Santiago, descarga los cierres del día desde Yahoo Finance.
#   La hora es en horario de Chile (el mercado cierra ~17:00).
# --------------------------------------------------------------------------- #
MARKET_TZ = "America/Santiago"
DAILY_REFRESH_HOUR = 17
DAILY_REFRESH_MINUTE = 15

# Índice de referencia para calcular beta. Yahoo Finance solo tiene 1 dato
# histórico para "^IPSA" (cobertura pobre de índices extranjeros), así que se
# usa ECH (iShares MSCI Chile ETF, NYSE) como proxy: sigue de cerca a las
# principales acciones del IPSA y tiene historia diaria completa.
# Se cachea igual que cualquier ticker del portafolio, pero no se muestra
# en el listado de posiciones.
BENCHMARK_TICKER = "ECH"
