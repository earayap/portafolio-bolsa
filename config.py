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

# Años de historia a descargar. La mayoría de las acciones del portafolio
# tiene datos en Yahoo Finance desde el año 2000; algunas (p.ej. CGET.SN,
# escindida de CGE en 2022) simplemente no tienen tanta historia y quedan
# con lo que exista. 10 años deja margen de sobra para el backtest, que por
# defecto evalúa 3 años pero acepta el parámetro ?years= para pedir más.
HISTORY_YEARS = 10

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

# Spread máximo bid/ask (como fracción del punto medio) para confiar en
# (bid+ask)/2 como estimación del precio actual (ver
# data_service._fetch_live_quote). Con spreads más anchos que esto, el punto
# medio deja de ser representativo — se cae al último precio transado.
MAX_BID_ASK_SPREAD_PCT = 0.04

# Índice de referencia para calcular beta. Yahoo Finance solo tiene 1 dato
# histórico para "^IPSA" (cobertura pobre de índices extranjeros), así que se
# usa ECH (iShares MSCI Chile ETF, NYSE) como proxy: sigue de cerca a las
# principales acciones del IPSA y tiene historia diaria completa.
# Se cachea igual que cualquier ticker del portafolio, pero no se muestra
# en el listado de posiciones.
BENCHMARK_TICKER = "ECH"

# Carpeta con los comprobantes PDF de compra/venta (Vector Capital, Itaú),
# montada de solo lectura desde el escritorio del usuario (ver
# docker-compose.yml). Ver comprobantes.py y data_service.recalcular_posiciones.
COMPROBANTES_DIR = os.environ.get("COMPROBANTES_DIR", "/comprobantes")
