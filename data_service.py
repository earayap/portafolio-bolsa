"""Servicio de datos: descarga, cache en SQLite y fallback.

Estrategia de resiliencia:
1. Se intenta descargar historia diaria (>= 2 años) con yfinance.
2. Todo lo descargado se persiste en SQLite (cache local).
3. Si la API no devuelve datos (acción ilíquida o símbolo inexistente),
   se sirve lo último cacheado.
4. Si NUNCA hubo datos para ese ticker, se genera una serie sintética
   determinística para que la interfaz siempre cargue sin fallar.
"""

import sqlite3
import json
import logging
import threading
import time
from datetime import datetime, timedelta

import numpy as np

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("data_service")

_LOCK = threading.Lock()

# Registro del portafolio inyectado desde app.py (fuente única de verdad).
# ticker -> {"nombre", "cantidad", "precio_compra"}
_PORTFOLIO = {}


def set_portfolio(portfolio):
    """Inyecta el diccionario PORTAFOLIO definido en app.py."""
    global _PORTFOLIO
    _PORTFOLIO = dict(portfolio)


def get_tickers():
    return list(_PORTFOLIO.keys())


# --------------------------------------------------------------------------- #
# Base de datos
# --------------------------------------------------------------------------- #
def get_conn():
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prices (
                ticker  TEXT NOT NULL,
                date    TEXT NOT NULL,
                open    REAL,
                high    REAL,
                low     REAL,
                close   REAL,
                volume  INTEGER,
                PRIMARY KEY (ticker, date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                ticker      TEXT PRIMARY KEY,
                last_update TEXT,
                source      TEXT,
                rows        INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dividends (
                ticker  TEXT NOT NULL,
                date    TEXT NOT NULL,
                amount  REAL,            -- dividendo por acción (CLP)
                PRIMARY KEY (ticker, date)
            )
            """
        )
        # Bitácora del refresco diario: la PK por día actúa como candado entre
        # procesos (workers de gunicorn) para que sólo uno descargue por día.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_log (
                day  TEXT PRIMARY KEY,
                ts   TEXT
            )
            """
        )
        conn.commit()
    log.info("Base de datos inicializada en %s", config.DB_PATH)


# --------------------------------------------------------------------------- #
# Persistencia
# --------------------------------------------------------------------------- #
def _save_prices(ticker, records, source):
    """records: lista de dicts con date/open/high/low/close/volume."""
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO prices (ticker, date, open, high, low, close, volume)
            VALUES (:ticker, :date, :open, :high, :low, :close, :volume)
            ON CONFLICT(ticker, date) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume
            """,
            [{"ticker": ticker, **r} for r in records],
        )
        conn.execute(
            """
            INSERT INTO meta (ticker, last_update, source, rows)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                last_update=excluded.last_update, source=excluded.source, rows=excluded.rows
            """,
            (ticker, datetime.utcnow().isoformat(), source, len(records)),
        )
        conn.commit()


def _load_prices(ticker):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM prices "
            "WHERE ticker = ? ORDER BY date ASC",
            (ticker,),
        ).fetchall()
    return [dict(r) for r in rows]


def _get_meta(ticker):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM meta WHERE ticker = ?", (ticker,)).fetchone()
    return dict(row) if row else None


def _cache_is_fresh(ticker):
    meta = _get_meta(ticker)
    if not meta or not meta.get("last_update"):
        return False
    try:
        last = datetime.fromisoformat(meta["last_update"])
    except ValueError:
        return False
    return datetime.utcnow() - last < timedelta(hours=config.CACHE_TTL_HOURS)


# --------------------------------------------------------------------------- #
# Descarga desde Yahoo Finance
# --------------------------------------------------------------------------- #
def _download_yfinance(ticker):
    """Devuelve lista de records o None si no hay datos."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance no está instalado; se usará cache/fallback")
        return None

    period = f"{config.HISTORY_YEARS}y"
    try:
        # IMPORTANTE: auto_adjust=False para conservar la columna "Close" con el
        # precio de cierre NOMINAL (no ajustado por dividendos/splits). Así el
        # precio coincide con el valor real de la Bolsa de Comercio de Santiago.
        df = yf.download(
            ticker,
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception as exc:  # red, rate-limit, etc.
        log.warning("Fallo al descargar %s: %s", ticker, exc)
        return None

    return _df_to_records(df, ticker)


def _download_last_price(ticker):
    """Mecanismo secundario: consulta el ÚLTIMO precio registrado.

    Se usa cuando la descarga de historia completa falla (acción de baja
    liquidez o deslistada). Intenta recuperar al menos la historia reciente
    disponible para mostrar el último cierre real conocido.
    """
    try:
        import yfinance as yf
    except ImportError:
        return None
    for period in ("6mo", "2y", "max"):
        try:
            df = yf.Ticker(ticker).history(
                period=period, interval="1d", auto_adjust=False
            )
        except Exception as exc:
            log.warning("history() falló para %s (%s): %s", ticker, period, exc)
            continue
        recs = _df_to_records(df, ticker)
        if recs:
            log.info("%s: último precio registrado recuperado vía history(%s)", ticker, period)
            return recs
    return None


# --------------------------------------------------------------------------- #
# Dividendos (yfinance)
# --------------------------------------------------------------------------- #
def _download_dividends(ticker):
    """Descarga el historial de dividendos por acción. Devuelve lista de
    {date, amount} o None si yfinance no está disponible/falla."""
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        serie = yf.Ticker(ticker).dividends  # Series: fecha -> dividendo/acción
    except Exception as exc:
        log.warning("dividends() falló para %s: %s", ticker, exc)
        return None
    if serie is None or len(serie) == 0:
        return []
    recs = []
    for idx, amt in serie.items():
        try:
            amount = float(amt)
        except (TypeError, ValueError):
            continue
        if amount != amount or amount <= 0:  # NaN o cero
            continue
        recs.append({"date": idx.strftime("%Y-%m-%d"), "amount": round(amount, 4)})
    return recs


def _save_dividends(ticker, records):
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO dividends (ticker, date, amount)
            VALUES (:ticker, :date, :amount)
            ON CONFLICT(ticker, date) DO UPDATE SET amount=excluded.amount
            """,
            [{"ticker": ticker, **r} for r in records],
        )
        conn.commit()


def _refresh_dividends(ticker):
    """Actualiza (best-effort) los dividendos de un ticker. Nunca lanza."""
    try:
        recs = _download_dividends(ticker)
    except Exception as exc:
        log.warning("No se pudieron actualizar dividendos de %s: %s", ticker, exc)
        return
    if recs:
        _save_dividends(ticker, recs)
        log.info("%s: %d dividendos registrados", ticker, len(recs))


def get_dividends_total(ticker, since="2025-03-01"):
    """Total de dividendos pagados por la posición desde `since` (inclusive).

    dividendo_por_acción × cantidad, sumado sobre las fechas de pago.
    Usa la tabla oficial de la BCS si existe para el ticker; si no, cae a los
    dividendos cacheados desde Yahoo Finance.
    """
    per_share = _dividends_per_share(ticker, since)
    cantidad = _PORTFOLIO.get(ticker, {}).get("cantidad", 0) or 0
    return round(per_share * cantidad, 2)


def _dividends_per_share(ticker, since):
    """Dividendo total por acción desde `since`. BCS tiene precedencia."""
    try:
        from dividendos_bcs import DIVIDENDOS_BCS
    except ImportError:
        DIVIDENDOS_BCS = {}

    if ticker in DIVIDENDOS_BCS:
        hoy = datetime.utcnow().date().isoformat()
        return sum(
            d["amount"]
            for d in DIVIDENDOS_BCS[ticker]
            if since <= d["date"] <= hoy  # sólo dividendos ya pagados
        )

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT amount FROM dividends WHERE ticker = ? AND date >= ?",
            (ticker, since),
        ).fetchall()
    return sum((r["amount"] or 0) for r in rows)


def _df_to_records(df, ticker):
    """Convierte un DataFrame de yfinance a lista de records (usa Close nominal)."""
    if df is None or getattr(df, "empty", True):
        log.info("Sin datos desde la API para %s", ticker)
        return None

    # yfinance puede devolver columnas MultiIndex cuando hay un solo ticker
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)

    # Se usa exclusivamente "Close" (nominal); "Adj Close" se ignora a propósito.
    if "Close" not in df.columns:
        log.warning("%s: el DataFrame no trae columna 'Close'", ticker)
        return None

    records = []
    for idx, row in df.iterrows():
        try:
            close = float(row["Close"])
        except (KeyError, TypeError, ValueError):
            continue
        if close != close:  # NaN
            continue
        records.append(
            {
                "date": idx.strftime("%Y-%m-%d"),
                "open": _f(row.get("Open")),
                "high": _f(row.get("High")),
                "low": _f(row.get("Low")),
                "close": close,
                "volume": _i(row.get("Volume")),
            }
        )
    return records or None


def _f(v):
    try:
        v = float(v)
        return round(v, 2) if v == v else None
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        v = float(v)
        return int(v) if v == v else 0
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# Fallback sintético (determinístico por ticker)
# --------------------------------------------------------------------------- #
def _synthetic_series(ticker):
    """Genera una serie realista y estable (misma salida para el mismo ticker).

    Se usa solo cuando NUNCA hubo datos reales, para que la interfaz no falle.
    """
    seed = abs(hash(ticker)) % (2**32)
    rng = np.random.default_rng(seed)

    days = config.HISTORY_YEARS * 365
    start_price = float(rng.integers(500, 15000))  # precio inicial en CLP
    drift = rng.normal(0.0002, 0.0004)             # tendencia diaria
    vol = rng.uniform(0.010, 0.028)                # volatilidad diaria

    prices = [start_price]
    for _ in range(days - 1):
        shock = rng.normal(drift, vol)
        prices.append(max(50.0, prices[-1] * (1 + shock)))

    records = []
    today = datetime.utcnow().date()
    for i, close in enumerate(prices):
        d = today - timedelta(days=days - 1 - i)
        if d.weekday() >= 5:  # omitir fines de semana
            continue
        intraday = abs(rng.normal(0, vol)) * close
        high = close + intraday * rng.uniform(0.3, 1.0)
        low = close - intraday * rng.uniform(0.3, 1.0)
        open_ = low + (high - low) * rng.uniform(0, 1)
        base_vol = int(rng.integers(1_000, 80_000))
        records.append(
            {
                "date": d.strftime("%Y-%m-%d"),
                "open": round(open_, 2),
                "high": round(max(high, close), 2),
                "low": round(max(1.0, min(low, close)), 2),
                "close": round(close, 2),
                "volume": base_vol,
            }
        )
    return records


# --------------------------------------------------------------------------- #
# API pública del servicio
# --------------------------------------------------------------------------- #
def refresh_ticker(ticker, force=False):
    """Asegura datos para un ticker. Devuelve (source, n_filas)."""
    with _LOCK:
        if not force and _cache_is_fresh(ticker):
            meta = _get_meta(ticker)
            return meta.get("source", "cache"), meta.get("rows", 0)

        # Dividendos (best-effort, independiente del resultado de precios).
        _refresh_dividends(ticker)

        # 1) Historia completa (2 años) con precio de cierre nominal.
        records = _download_yfinance(ticker)
        if records:
            _save_prices(ticker, records, source="yfinance")
            log.info("%s: %d filas desde yfinance", ticker, len(records))
            return "yfinance", len(records)

        # 2) Baja liquidez/deslistada: consultar el último precio registrado.
        last = _download_last_price(ticker)
        if last:
            _save_prices(ticker, last, source="yfinance")
            log.info("%s: %d filas (último precio registrado)", ticker, len(last))
            return "yfinance", len(last)

        # 3) ¿Tenemos algo cacheado de antes?
        cached = _load_prices(ticker)
        if cached:
            log.info("%s: usando cache local (%d filas)", ticker, len(cached))
            return "cache", len(cached)

        # 4) Nada de nada: fallback sintético (solo para no romper la UI).
        synth = _synthetic_series(ticker)
        _save_prices(ticker, synth, source="synthetic")
        log.info("%s: fallback sintético (%d filas)", ticker, len(synth))
        return "synthetic", len(synth)


def refresh_all(force=False):
    results = {}
    for ticker in _PORTFOLIO:
        try:
            results[ticker] = refresh_ticker(ticker, force=force)
        except Exception as exc:
            log.error("Error refrescando %s: %s", ticker, exc)
            results[ticker] = ("error", 0)
    return results


# --------------------------------------------------------------------------- #
# Refresco automático diario (tras el cierre de la Bolsa de Santiago)
# --------------------------------------------------------------------------- #
_SCHED_STARTED = False


def _market_now():
    """Fecha/hora actual en el huso horario del mercado (con DST)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(config.MARKET_TZ))
    except Exception:  # sin tzdata: se usa la hora local del servidor
        return datetime.now()


def _seconds_until_next_refresh():
    """Segundos hasta la próxima hora de refresco (hoy o mañana)."""
    now = _market_now()
    target = now.replace(
        hour=config.DAILY_REFRESH_HOUR,
        minute=config.DAILY_REFRESH_MINUTE,
        second=0,
        microsecond=0,
    )
    if target <= now:
        target += timedelta(days=1)
    return max(60.0, (target - now).total_seconds())


def _claim_day(day):
    """Reserva el refresco del día `day`. Devuelve True si este proceso ganó.

    La PK sobre `day` hace que sólo el primer worker en insertar tenga éxito;
    los demás reciben IntegrityError y se saltan la descarga.
    """
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO refresh_log (day, ts) VALUES (?, ?)",
                (day, datetime.utcnow().isoformat()),
            )
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def _daily_refresh_loop():
    while True:
        time.sleep(_seconds_until_next_refresh())
        day = _market_now().strftime("%Y-%m-%d")
        if _claim_day(day):
            log.info("Refresco diario automático iniciado (%s)", day)
            try:
                refresh_all(force=True)
                log.info("Refresco diario automático completado (%s)", day)
            except Exception as exc:
                log.error("Fallo en el refresco diario (%s): %s", day, exc)
        else:
            log.info("Refresco diario ya realizado por otro proceso (%s)", day)


def start_scheduler():
    """Arranca el hilo de refresco diario (idempotente por proceso)."""
    global _SCHED_STARTED
    if _SCHED_STARTED:
        return
    _SCHED_STARTED = True
    threading.Thread(target=_daily_refresh_loop, daemon=True).start()
    log.info(
        "Planificador de refresco diario activo (%02d:%02d hora %s)",
        config.DAILY_REFRESH_HOUR, config.DAILY_REFRESH_MINUTE, config.MARKET_TZ,
    )


def _compute_indicators(records):
    """Agrega variación % diaria y medias móviles a cada registro."""
    closes = [r["close"] for r in records]
    prev = None
    for i, r in enumerate(records):
        c = r["close"]
        r["change_pct"] = round((c - prev) / prev * 100, 2) if prev else 0.0
        prev = c
        r["ma20"] = round(sum(closes[max(0, i - 19):i + 1]) / min(i + 1, 20), 2)
        r["ma50"] = round(sum(closes[max(0, i - 49):i + 1]) / min(i + 1, 50), 2)
    return records


# Días de tolerancia antes de marcar un precio como "obsoleto".
# (Cubre fines de semana + un par de feriados de la BCS.)
STALE_DAYS = 4


def _staleness(last_date_str, source):
    """Devuelve (stale, days_old). stale=True si el último precio no es reciente
    o si la serie es sintética (sin datos reales)."""
    try:
        d = datetime.strptime(last_date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return True, None
    days_old = (datetime.utcnow().date() - d).days
    stale = source == "synthetic" or days_old > STALE_DAYS
    return stale, days_old


def get_history(ticker):
    """Devuelve dict con metadatos + serie completa con indicadores."""
    records = _load_prices(ticker)
    if not records:
        refresh_ticker(ticker)
        records = _load_prices(ticker)
    records = _compute_indicators(records)
    meta = _get_meta(ticker) or {}
    info = _PORTFOLIO.get(ticker, {})
    source = meta.get("source", "cache")
    last_date = records[-1]["date"] if records else None
    stale, days_old = _staleness(last_date, source)
    return {
        "ticker": ticker,
        "name": info.get("nombre", ticker),
        "cantidad": info.get("cantidad", 0),
        "precio_compra": info.get("precio_compra", 0),
        "source": source,
        "last_update": meta.get("last_update"),
        "last_price_date": last_date,
        "stale": stale,
        "days_old": days_old,
        "records": records,
    }


def get_summary(ticker):
    """Métricas resumidas para las tarjetas del listado."""
    data = get_history(ticker)
    recs = data["records"]
    if not recs:
        return None
    last = recs[-1]
    first_year = recs[max(0, len(recs) - 252)]  # ~1 año bursátil
    closes = [r["close"] for r in recs]

    # Valorización de la posición
    cantidad = data["cantidad"] or 0
    precio_compra = data["precio_compra"] or 0
    invertido = round(cantidad * precio_compra, 2)
    valor_mercado = round(cantidad * last["close"], 2)
    pnl = round(valor_mercado - invertido, 2)
    pnl_pct = round((last["close"] - precio_compra) / precio_compra * 100, 2) if precio_compra else 0.0

    return {
        "ticker": ticker,
        "name": data["name"],
        "source": data["source"],
        "last_close": last["close"],
        "change_pct": last["change_pct"],
        "volume": last["volume"],
        "high": last["high"],
        "low": last["low"],
        "high_52w": round(max(closes[-252:]), 2),
        "low_52w": round(min(closes[-252:]), 2),
        "return_1y": round((last["close"] - first_year["close"]) / first_year["close"] * 100, 2)
        if first_year["close"] else 0.0,
        "last_date": last["date"],
        "stale": data["stale"],
        "days_old": data["days_old"],
        "n_points": len(recs),
        # Posición del portafolio
        "cantidad": cantidad,
        "precio_compra": precio_compra,
        "invertido": invertido,
        "valor_mercado": valor_mercado,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    }
