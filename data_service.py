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
from datetime import datetime, timedelta, timezone

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
        # Indicadores macro (UF, dólar, TPM, cobre, etc. — ver indicadores_macro.py)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS indicadores (
                codigo  TEXT NOT NULL,
                date    TEXT NOT NULL,
                value   REAL,
                PRIMARY KEY (codigo, date)
            )
            """
        )
        # Fundamentales por acción (ver fundamentales.py). La CMF no tiene API
        # pública para emisores no bancarios; se usa yfinance sanitizado.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fundamentales (
                ticker            TEXT PRIMARY KEY,
                updated_at        TEXT,
                trailing_pe       REAL,
                forward_pe        REAL,
                price_to_book     REAL,
                return_on_equity  REAL,
                profit_margin     REAL,
                debt_to_equity    REAL,
                market_cap        REAL,
                sector            TEXT
            )
            """
        )
        # Migración: columnas de Valor de Empresa (EV/EBITDA, EV/Ventas — ver
        # valor_empresa.py) agregadas después de la creación original de la
        # tabla. ALTER TABLE no tiene "IF NOT EXISTS" en SQLite, así que se
        # ignora el error si la columna ya existe.
        for col in (
            "enterprise_value REAL", "ev_to_ebitda REAL", "ev_to_revenue REAL",
            "ebitda REAL", "total_revenue REAL",
        ):
            try:
                conn.execute(f"ALTER TABLE fundamentales ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        # Dividendos reales ingresados a mano desde /posiciones. Tienen la
        # misma precedencia que DIVIDENDOS_BCS (dividendos_bcs.py) sobre el
        # historial de yfinance — se combinan ambas fuentes por ticker.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_dividends (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker  TEXT NOT NULL,
                date    TEXT NOT NULL,
                amount  REAL NOT NULL
            )
            """
        )
        # EEFF trimestrales ingresados a mano desde /posiciones (ver
        # fundamentales.py: la CMF no tiene API pública para EEFF de
        # emisores no bancarios, así que se transcriben del PDF del portal
        # CMF). Solo histórico visual — no alimentan el score del screener
        # (no hay serie histórica confiable para todo el portafolio).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS eeff_trimestral (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker            TEXT NOT NULL,
                anio              INTEGER NOT NULL,
                trimestre         INTEGER NOT NULL,
                ingresos          REAL,
                utilidad_neta     REAL,
                roe               REAL,
                deuda_patrimonio  REAL,
                margen_neto       REAL,
                UNIQUE(ticker, anio, trimestre)
            )
            """
        )
        # Migración: campos para calcular Valor de Empresa a mano (ver
        # valor_empresa.py) cuando yfinance no trae el ratio para un ticker
        # ilíquido. Se transcriben del mismo PDF de la CMF que el resto del
        # EEFF trimestral: resultado operacional y D&A del Estado de
        # Resultados/Flujo de Efectivo, deuda financiera (corriente + no
        # corriente sumadas) y efectivo del Estado de Situación Financiera.
        for col in (
            "resultado_operacional REAL", "depreciacion_amortizacion REAL",
            "deuda_financiera REAL", "efectivo_equivalentes REAL",
        ):
            try:
                conn.execute(f"ALTER TABLE eeff_trimestral ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        # Aportes de caja a la corredora (depósitos), ingresados a mano desde
        # /posiciones. No son por ticker: es el capital que entra a la cuenta,
        # independiente de en qué se invierta después. Sirve para comparar
        # capital aportado vs. valor de mercado actual de la cartera (retorno
        # real simple, no ponderado por tiempo).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS aportes (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                date    TEXT NOT NULL,
                amount  REAL NOT NULL
            )
            """
        )
        # Operaciones (compra/venta) importadas automáticamente desde los
        # comprobantes PDF de las corredoras (Vector Capital, Itaú) — ver
        # comprobantes.py. El UNIQUE compuesto (no solo el hash del archivo)
        # es necesario porque una misma factura puede traer más de un
        # instrumento (p.ej. dos acciones en la misma factura Itaú), así que
        # varias filas comparten el mismo hash de archivo.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS operaciones (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker            TEXT NOT NULL,
                tipo              TEXT NOT NULL,
                fecha             TEXT NOT NULL,
                cantidad          REAL NOT NULL,
                precio_unitario   REAL,
                monto             REAL NOT NULL,
                institucion       TEXT NOT NULL,
                numero_factura    TEXT,
                archivo           TEXT NOT NULL,
                hash              TEXT NOT NULL,
                creado_en         TEXT NOT NULL,
                UNIQUE(hash, ticker, tipo, cantidad, monto)
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

    return _apply_live_quote(_df_to_records(df, ticker), ticker)


def _fetch_live_quote(ticker):
    """Consulta el snapshot de cotización en vivo (Ticker.info).

    Para varias acciones ilíquidas de la BCS, el endpoint de histórico
    (yf.download/history, usado en _download_yfinance) devuelve el cierre
    CONGELADO de días/semanas atrás con volumen 0, y hasta el último precio
    TRANSADO (regularMarketPrice) puede quedar viejo si la acción no ha
    cruzado operaciones. El punto medio bid/ask sí refleja cotizaciones
    activas del mercado en este momento, así que se prefiere como estimación
    del valor actual cuando está disponible (decisión explícita del usuario:
    para acciones sin transacciones recientes, usar (bid+ask)/2 en vez del
    último precio transado).

    Excepción: si el spread bid/ask es más ancho que
    config.MAX_BID_ASK_SPREAD_PCT, el punto medio deja de ser representativo
    (ej. bid=10200/ask=11363, spread ~11%, punto medio se aleja bastante del
    último precio real transado) y se cae al último precio transado en su
    lugar.
    """
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        info = yf.Ticker(ticker).get_info()
    except Exception as exc:
        log.warning("get_info() falló para %s: %s", ticker, exc)
        return None

    bid, ask = info.get("bid"), info.get("ask")
    if bid and ask and bid > 0 and ask > 0:
        mid = (float(bid) + float(ask)) / 2
        spread_pct = (float(ask) - float(bid)) / mid
        if spread_pct <= config.MAX_BID_ASK_SPREAD_PCT:
            mid = round(mid, 2)
            return {
                "date": _market_now().date().isoformat(),
                "open": mid,
                "high": max(mid, round(float(ask), 2)),
                "low": min(mid, round(float(bid), 2)),
                "close": mid,
                "volume": _i(info.get("regularMarketVolume")),
            }
        log.info(
            "%s: spread bid/ask %.1f%% supera el máximo (%.1f%%); se usa el "
            "último precio transado en vez del punto medio",
            ticker, spread_pct * 100, config.MAX_BID_ASK_SPREAD_PCT * 100,
        )

    price = info.get("regularMarketPrice")
    ts = info.get("regularMarketTime")
    if price is None or ts is None:
        return None
    try:
        date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None
    return {
        "date": date,
        "open": _f(info.get("regularMarketOpen")) or round(float(price), 2),
        "high": _f(info.get("regularMarketDayHigh")) or round(float(price), 2),
        "low": _f(info.get("regularMarketDayLow")) or round(float(price), 2),
        "close": round(float(price), 2),
        "volume": _i(info.get("regularMarketVolume")),
    }


def _apply_live_quote(records, ticker):
    """Reemplaza o agrega el último registro con el snapshot en vivo si es
    igual o más reciente que el último dato histórico (ver _fetch_live_quote
    para la razón)."""
    if not records:
        return records
    quote = _fetch_live_quote(ticker)
    if not quote:
        return records
    if quote["date"] < records[-1]["date"]:
        return records
    if quote["date"] == records[-1]["date"]:
        records[-1] = quote
    else:
        records.append(quote)
    return records


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
            return _apply_live_quote(recs, ticker)
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


def get_dividends_per_share(ticker, since):
    """Dividendo total por acción pagado desde `since` (inclusive) hasta hoy."""
    return _dividends_per_share(ticker, since)


def get_dividends_per_share_rango(ticker, since, hasta):
    """Dividendo total por acción pagado entre `since` y `hasta` (ambos
    inclusive). A diferencia de get_dividends_per_share (que asume "hasta
    hoy"), esta respeta un corte en el pasado — indispensable para el
    backtest, que no debe usar dividendos pagados después de la fecha que
    está evaluando (look-ahead bias)."""
    known = _known_dividend_records(ticker)
    if known is not None:
        return sum(d["amount"] for d in known if since <= d["date"] <= hasta)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT amount FROM dividends WHERE ticker = ? AND date >= ? AND date <= ?",
            (ticker, since, hasta),
        ).fetchall()
    return sum((r["amount"] or 0) for r in rows)


def get_dividends_total(ticker, since="2025-03-01"):
    """Total de dividendos efectivamente cobrados por la posición desde
    `since` (inclusive).

    A diferencia de get_dividends_per_share (que es una suma por acción,
    usada para el dividend yield), esto es un monto en CLP — así que
    importa CUÁNTAS acciones tenías el día que quedó fijado cada dividendo
    (fecha ex-dividendo, no la fecha de pago: comprar después del corte
    ex-dividendo no da derecho a ese reparto aunque ya se tengan acciones
    para cuando se paga). Por eso cada registro se multiplica por
    cantidad_al(ticker, fecha_ex) en vez de por la cantidad actual de
    portfolio.json — que sería la cantidad de HOY, casi siempre mayor a la
    que había en cada fecha pasada.

    Usa la tabla oficial de la BCS (dividendos_bcs.py, con fecha_ex) y/o
    los dividendos manuales si existen para el ticker; si no, cae a los
    dividendos cacheados desde Yahoo Finance (sin fecha ex-dividendo
    propia — se usa la misma fecha del registro como aproximación)."""
    known = _known_dividend_records(ticker)
    hoy = datetime.utcnow().date().isoformat()

    if known is not None:
        total = 0.0
        for d in known:
            fecha_ex = d.get("date_ex") or d["date"]
            if since <= d["date"] <= hoy:
                total += d["amount"] * cantidad_al(ticker, fecha_ex)
        return round(total, 2)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, amount FROM dividends WHERE ticker = ? AND date >= ?",
            (ticker, since),
        ).fetchall()
    total = sum((r["amount"] or 0) * cantidad_al(ticker, r["date"]) for r in rows if r["date"] <= hoy)
    return round(total, 2)


def _dividends_per_share(ticker, since):
    """Dividendo total por acción desde `since`. BCS + manuales tienen
    precedencia sobre el historial de yfinance."""
    known = _known_dividend_records(ticker)
    if known is not None:
        hoy = datetime.utcnow().date().isoformat()
        return sum(
            d["amount"] for d in known if since <= d["date"] <= hoy  # sólo pagados
        )

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT amount FROM dividends WHERE ticker = ? AND date >= ?",
            (ticker, since),
        ).fetchall()
    return sum((r["amount"] or 0) for r in rows)


def _known_dividend_records(ticker):
    """Combina DIVIDENDOS_BCS (dividendos_bcs.py) con los dividendos
    ingresados a mano (tabla manual_dividends) para un ticker. Devuelve
    `None` si no hay ningún dato "conocido" para ese ticker (en cuyo caso
    quien llama debe caer al historial de yfinance)."""
    try:
        from dividendos_bcs import DIVIDENDOS_BCS
    except ImportError:
        DIVIDENDOS_BCS = {}

    records = list(DIVIDENDOS_BCS.get(ticker, [])) + list_manual_dividends(ticker)
    return records or None


def list_dividendos_todos(ticker=None):
    """Todos los dividendos "conocidos" (DIVIDENDOS_BCS + manual_dividends)
    de la cartera, para mostrarlos juntos en /posiciones. Los de BCS vienen
    con id=None (no se pueden borrar desde la UI: viven en dividendos_bcs.py,
    no en la base de datos) y fuente="BCS"; los manuales traen su id real y
    fuente="manual". Ordenados por fecha descendente.

    Cada registro trae además "cantidad" (acciones en cartera a la fecha
    límite/ex-dividendo — o a la fecha del registro si no hay "date_ex",
    caso de los dividendos manuales) y "total" (amount × cantidad): cuánto
    dinero se cobró realmente por ese pago, no solo el monto por acción.
    Ver cantidad_al — misma lógica que get_dividends_total."""
    try:
        from dividendos_bcs import DIVIDENDOS_BCS
    except ImportError:
        DIVIDENDOS_BCS = {}

    tickers = [ticker] if ticker else list(_PORTFOLIO.keys())
    registros = []
    for t in tickers:
        bcs = DIVIDENDOS_BCS.get(t, [])
        manuales = list_manual_dividends(t)
        for d in bcs:
            fecha_ref = d.get("date_ex") or d["date"]
            cantidad = cantidad_al(t, fecha_ref)
            registros.append({
                "id": None, "ticker": t, "date": d["date"], "amount": d["amount"],
                "fuente": "BCS", "cantidad": cantidad,
                "total": round(d["amount"] * cantidad, 2),
            })
        for d in manuales:
            cantidad = cantidad_al(t, d["date"])
            registros.append({
                **d, "ticker": t, "fuente": "manual", "cantidad": cantidad,
                "total": round(d["amount"] * cantidad, 2),
            })
        # Sin dato "conocido" (BCS ni manual) para este ticker: cae al
        # historial de dividendos cacheado de yfinance — mismo fallback que
        # get_dividends_total (ver docstring ahí). Sin esto, tickers que
        # dependen 100% de yfinance quedaban invisibles en /posiciones y en
        # el panel de dividendos del Resumen aunque sí se les cobrara y
        # contara en el dividend yield del screener.
        if not bcs and not manuales:
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT date, amount FROM dividends WHERE ticker = ? ORDER BY date DESC",
                    (t,),
                ).fetchall()
            for r in rows:
                cantidad = cantidad_al(t, r["date"])
                registros.append({
                    "id": None, "ticker": t, "date": r["date"], "amount": r["amount"],
                    "fuente": "yfinance", "cantidad": cantidad,
                    "total": round((r["amount"] or 0) * cantidad, 2),
                })

    registros.sort(key=lambda r: (r["date"], r["ticker"]), reverse=True)
    return registros


# --------------------------------------------------------------------------- #
# Dividendos manuales (ingresados por el usuario en /posiciones)
# --------------------------------------------------------------------------- #
def list_manual_dividends(ticker=None):
    """Lista de dividendos manuales, opcionalmente filtrados por ticker,
    ordenados por fecha descendente."""
    with get_conn() as conn:
        if ticker:
            rows = conn.execute(
                "SELECT id, ticker, date, amount FROM manual_dividends "
                "WHERE ticker = ? ORDER BY date DESC, id DESC",
                (ticker,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, ticker, date, amount FROM manual_dividends "
                "ORDER BY date DESC, id DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def add_manual_dividend(ticker, date, amount):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO manual_dividends (ticker, date, amount) VALUES (?, ?, ?)",
            (ticker, date, amount),
        )
        conn.commit()
        return cur.lastrowid


def delete_manual_dividend(dividend_id):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM manual_dividends WHERE id = ?", (dividend_id,))
        conn.commit()
        return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Aportes de caja a la corredora (ingresados por el usuario en /posiciones)
# --------------------------------------------------------------------------- #
def list_aportes():
    """Lista de aportes (depósitos), ordenados por fecha descendente."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, date, amount FROM aportes ORDER BY date DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def add_aporte(date, amount):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO aportes (date, amount) VALUES (?, ?)", (date, amount)
        )
        conn.commit()
        return cur.lastrowid


def delete_aporte(aporte_id):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM aportes WHERE id = ?", (aporte_id,))
        conn.commit()
        return cur.rowcount > 0


def total_aportado():
    with get_conn() as conn:
        row = conn.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM aportes").fetchone()
    return row["total"]


# --------------------------------------------------------------------------- #
# Operaciones (compra/venta) importadas de comprobantes PDF — ver
# comprobantes.py y app.py: POST /api/comprobantes/sync
# --------------------------------------------------------------------------- #
def list_operaciones(ticker=None):
    """Lista de operaciones, opcionalmente filtradas por ticker, ordenadas
    por fecha descendente (más reciente primero)."""
    with get_conn() as conn:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM operaciones WHERE ticker = ? "
                "ORDER BY fecha DESC, id DESC",
                (ticker,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM operaciones ORDER BY fecha DESC, id DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def insert_operaciones(operaciones):
    """Inserta operaciones parseadas de comprobantes.py. Las que ya existen
    (mismo hash+ticker+tipo+cantidad+monto — comprobante duplicado o ya
    sincronizado antes) se ignoran silenciosamente. Devuelve cuántas filas
    nuevas se insertaron efectivamente."""
    if not operaciones:
        return 0
    with get_conn() as conn:
        cur = conn.executemany(
            """
            INSERT OR IGNORE INTO operaciones
                (ticker, tipo, fecha, cantidad, precio_unitario, monto,
                 institucion, numero_factura, archivo, hash, creado_en)
            VALUES
                (:ticker, :tipo, :fecha, :cantidad, :precio_unitario, :monto,
                 :institucion, :numero_factura, :archivo, :hash, :creado_en)
            """,
            [{**op, "creado_en": datetime.utcnow().isoformat()} for op in operaciones],
        )
        conn.commit()
        return cur.rowcount


def cantidad_al(ticker, fecha):
    """Cantidad de acciones en cartera al cierre de `fecha` (YYYY-MM-DD),
    derivada del historial de `operaciones` (comprobantes) en vez de la
    cantidad actual de portfolio.json — indispensable para dividendos: la
    cartera ha ido creciendo con compras posteriores, así que multiplicar
    un dividendo pagado hace meses por la cantidad de HOY infla el monto
    muy por encima de lo que realmente se cobró (ver get_dividends_total).

    Si `operaciones` no cubre el 100% del historial de un ticker (p.ej.
    compras hechas antes de empezar a guardar comprobantes — caso
    detectado en TRICAHUE, ver recalcular_posiciones), la diferencia entre
    el total derivado de `operaciones` y la cantidad actual se trata como
    un "saldo base" ya en cartera desde antes de la primera operación
    registrada, en vez de asumir 0. Si el ticker no tiene ninguna
    operación registrada, esto se reduce a devolver la cantidad actual
    para cualquier fecha — mismo comportamiento que antes de tener este
    historial, sin regresión."""
    cantidad_actual = _PORTFOLIO.get(ticker, {}).get("cantidad", 0) or 0
    with get_conn() as conn:
        filas = conn.execute(
            "SELECT tipo, fecha, cantidad FROM operaciones WHERE ticker = ? ORDER BY fecha ASC",
            (ticker,),
        ).fetchall()
    if not filas:
        return cantidad_actual

    derivado_total = 0.0
    derivado_al_fecha = 0.0
    for f in filas:
        delta = f["cantidad"] if f["tipo"] == "COMPRA" else -f["cantidad"]
        derivado_total += delta
        if f["fecha"] <= fecha:
            derivado_al_fecha += delta

    saldo_base = max(0.0, cantidad_actual - derivado_total)
    return saldo_base + derivado_al_fecha


def recalcular_posiciones():
    """Deriva cantidad y precio de compra promedio ponderado por ticker
    desde el historial completo de `operaciones`, en orden cronológico:
    cada COMPRA promedia su precio con lo que ya había (costo promedio
    ponderado); cada VENTA solo resta cantidad (el precio promedio de lo
    que queda no cambia — no calculamos P&L realizado, fuera de alcance).
    La cantidad nunca baja de 0 (una venta que exceda lo comprado registrado
    se trata como si vendiera todo lo que hay).

    No escribe nada — devuelve {ticker: {"cantidad": ..., "precio_compra":
    ...}} para que app.py arme un preview antes/después y el usuario
    confirme antes de aplicarlo a portfolio.json."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ticker, tipo, fecha, cantidad, precio_unitario FROM operaciones "
            "ORDER BY fecha ASC, id ASC"
        ).fetchall()

    posiciones = {}
    for r in rows:
        pos = posiciones.setdefault(r["ticker"], {"cantidad": 0.0, "precio_compra": 0.0})
        if r["tipo"] == "COMPRA":
            costo_actual = pos["cantidad"] * pos["precio_compra"]
            costo_nuevo = r["cantidad"] * (r["precio_unitario"] or 0)
            cantidad_total = pos["cantidad"] + r["cantidad"]
            pos["precio_compra"] = (
                (costo_actual + costo_nuevo) / cantidad_total if cantidad_total else 0
            )
            pos["cantidad"] = cantidad_total
        elif r["tipo"] == "VENTA":
            pos["cantidad"] = max(0.0, pos["cantidad"] - r["cantidad"])

    return posiciones


# --------------------------------------------------------------------------- #
# EEFF trimestrales (ingresados por el usuario en /posiciones)
# --------------------------------------------------------------------------- #
def list_eeff_trimestral(ticker=None):
    """Lista de EEFF trimestrales, opcionalmente filtrados por ticker,
    ordenados por año/trimestre descendente."""
    with get_conn() as conn:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM eeff_trimestral WHERE ticker = ? "
                "ORDER BY anio DESC, trimestre DESC",
                (ticker,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM eeff_trimestral ORDER BY anio DESC, trimestre DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def upsert_eeff_trimestral(ticker, anio, trimestre, ingresos, utilidad_neta,
                            roe, deuda_patrimonio, margen_neto,
                            resultado_operacional=None, depreciacion_amortizacion=None,
                            deuda_financiera=None, efectivo_equivalentes=None):
    """Crea o actualiza (por ticker+año+trimestre) un registro de EEFF."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO eeff_trimestral
                (ticker, anio, trimestre, ingresos, utilidad_neta, roe,
                 deuda_patrimonio, margen_neto, resultado_operacional,
                 depreciacion_amortizacion, deuda_financiera, efectivo_equivalentes)
            VALUES (:ticker, :anio, :trimestre, :ingresos, :utilidad_neta,
                     :roe, :deuda_patrimonio, :margen_neto, :resultado_operacional,
                     :depreciacion_amortizacion, :deuda_financiera, :efectivo_equivalentes)
            ON CONFLICT(ticker, anio, trimestre) DO UPDATE SET
                ingresos=excluded.ingresos, utilidad_neta=excluded.utilidad_neta,
                roe=excluded.roe, deuda_patrimonio=excluded.deuda_patrimonio,
                margen_neto=excluded.margen_neto,
                resultado_operacional=excluded.resultado_operacional,
                depreciacion_amortizacion=excluded.depreciacion_amortizacion,
                deuda_financiera=excluded.deuda_financiera,
                efectivo_equivalentes=excluded.efectivo_equivalentes
            """,
            {
                "ticker": ticker, "anio": anio, "trimestre": trimestre,
                "ingresos": ingresos, "utilidad_neta": utilidad_neta, "roe": roe,
                "deuda_patrimonio": deuda_patrimonio, "margen_neto": margen_neto,
                "resultado_operacional": resultado_operacional,
                "depreciacion_amortizacion": depreciacion_amortizacion,
                "deuda_financiera": deuda_financiera,
                "efectivo_equivalentes": efectivo_equivalentes,
            },
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM eeff_trimestral WHERE ticker = ? AND anio = ? AND trimestre = ?",
            (ticker, anio, trimestre),
        ).fetchone()
        return row["id"]


def delete_eeff_trimestral(eeff_id):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM eeff_trimestral WHERE id = ?", (eeff_id,))
        conn.commit()
        return cur.rowcount > 0


def get_eeff_trimestral_ultimo(ticker):
    """Registro EEFF trimestral más reciente (por año/trimestre) de un
    ticker, para usarlo como respaldo del cálculo de Valor de Empresa
    cuando yfinance no trae el dato. None si no hay ninguno cargado."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM eeff_trimestral WHERE ticker = ? "
            "ORDER BY anio DESC, trimestre DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    return dict(row) if row else None


def get_eeff_trimestral_ttm(ticker):
    """Suma los últimos 4 trimestres cargados a mano (no necesariamente
    consecutivos) para aproximar un EBITDA/ingresos TTM (trailing twelve
    months), comparable con el ev_to_ebitda de yfinance (que también es
    TTM). Ver valor_empresa._desde_manual: usar un solo trimestre como si
    fuera el EBITDA anual infla el múltiplo EV/EBITDA ~4x.
    Los campos de balance (deuda financiera, efectivo) usan el trimestre
    más reciente (son un saldo a un punto en el tiempo, no un flujo que se
    pueda sumar). None si hay menos de 4 trimestres con resultado
    operacional y D&A cargados."""
    trimestres = [
        t for t in list_eeff_trimestral(ticker)
        if t.get("resultado_operacional") is not None
        and t.get("depreciacion_amortizacion") is not None
    ][:4]
    if len(trimestres) < 4:
        return None
    ultimo = trimestres[0]
    return {
        "resultado_operacional": sum(t["resultado_operacional"] for t in trimestres),
        "depreciacion_amortizacion": sum(t["depreciacion_amortizacion"] for t in trimestres),
        "ingresos": sum(t["ingresos"] for t in trimestres if t.get("ingresos") is not None) or None,
        "deuda_financiera": ultimo.get("deuda_financiera"),
        "efectivo_equivalentes": ultimo.get("efectivo_equivalentes"),
        "periodo": f"TTM {trimestres[-1]['anio']}-Q{trimestres[-1]['trimestre']}..{ultimo['anio']}-Q{ultimo['trimestre']}",
    }


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
# Indicadores macro (UF, dólar, TPM, cobre, IPC, UTM)
# --------------------------------------------------------------------------- #
def _save_indicadores(por_codigo):
    with get_conn() as conn:
        for codigo, records in por_codigo.items():
            if not records:
                continue
            conn.executemany(
                """
                INSERT INTO indicadores (codigo, date, value)
                VALUES (:codigo, :date, :value)
                ON CONFLICT(codigo, date) DO UPDATE SET value=excluded.value
                """,
                records,
            )
        conn.commit()


def refresh_indicadores():
    """Descarga y persiste los indicadores macro. Best-effort, nunca lanza."""
    import indicadores_macro
    try:
        por_codigo = indicadores_macro.fetch_all()
        _save_indicadores(por_codigo)
        log.info("Indicadores macro actualizados: %s", list(por_codigo.keys()))
    except Exception as exc:
        log.error("Fallo al actualizar indicadores macro: %s", exc)


def get_indicadores():
    """Devuelve el último valor conocido de cada indicador macro."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT codigo, date, value FROM indicadores i
            WHERE date = (SELECT MAX(date) FROM indicadores WHERE codigo = i.codigo)
            """
        ).fetchall()
    return {r["codigo"]: {"date": r["date"], "value": r["value"]} for r in rows}


def backfill_indicadores_historicos(anios):
    """Descarga UF y TPM de años anteriores (mindicador.cl solo trae los
    últimos días por defecto). Necesario para que el backtest pueda calcular
    retorno real y tasa libre de riesgo en fechas pasadas. Best-effort."""
    import indicadores_macro
    for codigo in ("uf", "tpm"):
        try:
            recs = indicadores_macro.fetch_historico(codigo, anios)
            _save_indicadores({codigo: recs})
            log.info("Backfill %s: %d puntos (%s)", codigo, len(recs), anios)
        except Exception as exc:
            log.error("Fallo el backfill de %s: %s", codigo, exc)


def get_indicador_valor_en(codigo, fecha):
    """Último valor conocido de un indicador en o antes de `fecha` (YYYY-MM-DD)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM indicadores WHERE codigo = ? AND date <= ? "
            "ORDER BY date DESC LIMIT 1",
            (codigo, fecha),
        ).fetchone()
    return row["value"] if row else None


def get_indicador_historial(codigo, limit=90):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, value FROM indicadores WHERE codigo = ? ORDER BY date DESC LIMIT ?",
            (codigo, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# --------------------------------------------------------------------------- #
# Fundamentales por acción (ver fundamentales.py)
# --------------------------------------------------------------------------- #
def _save_fundamentales(ticker, datos):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO fundamentales
                (ticker, updated_at, trailing_pe, forward_pe, price_to_book,
                 return_on_equity, profit_margin, debt_to_equity, market_cap, sector,
                 enterprise_value, ev_to_ebitda, ev_to_revenue, ebitda, total_revenue)
            VALUES
                (:ticker, :updated_at, :trailing_pe, :forward_pe, :price_to_book,
                 :return_on_equity, :profit_margin, :debt_to_equity, :market_cap, :sector,
                 :enterprise_value, :ev_to_ebitda, :ev_to_revenue, :ebitda, :total_revenue)
            ON CONFLICT(ticker) DO UPDATE SET
                updated_at=excluded.updated_at, trailing_pe=excluded.trailing_pe,
                forward_pe=excluded.forward_pe, price_to_book=excluded.price_to_book,
                return_on_equity=excluded.return_on_equity, profit_margin=excluded.profit_margin,
                debt_to_equity=excluded.debt_to_equity, market_cap=excluded.market_cap,
                sector=excluded.sector, enterprise_value=excluded.enterprise_value,
                ev_to_ebitda=excluded.ev_to_ebitda, ev_to_revenue=excluded.ev_to_revenue,
                ebitda=excluded.ebitda, total_revenue=excluded.total_revenue
            """,
            {"ticker": ticker, "updated_at": datetime.utcnow().isoformat(), **datos},
        )
        conn.commit()


def _fundamentales_frescos(ticker):
    row = get_fundamentales(ticker)
    if not row or not row.get("updated_at"):
        return False
    try:
        last = datetime.fromisoformat(row["updated_at"])
    except ValueError:
        return False
    return datetime.utcnow() - last < timedelta(hours=config.CACHE_TTL_HOURS)


def refresh_fundamentales(force=False):
    """Actualiza los fundamentales de todo el portafolio. Best-effort, nunca lanza.

    Los EEFF no cambian a diario, así que se respeta el mismo TTL que la
    cache de precios para no golpear la API de yfinance sin necesidad.
    """
    import fundamentales
    for ticker in _PORTFOLIO:
        if not force and _fundamentales_frescos(ticker):
            continue
        try:
            datos = fundamentales.fetch(ticker)
            if datos:
                _save_fundamentales(ticker, datos)
        except Exception as exc:
            log.warning("No se pudieron actualizar fundamentales de %s: %s", ticker, exc)
    log.info("Fundamentales actualizados para %d acciones", len(_PORTFOLIO))


def get_fundamentales(ticker):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM fundamentales WHERE ticker = ?", (ticker,)
        ).fetchone()
    return dict(row) if row else None


def get_fundamentales_all():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM fundamentales").fetchall()
    return {r["ticker"]: dict(r) for r in rows}


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
    try:
        results[config.BENCHMARK_TICKER] = refresh_ticker(config.BENCHMARK_TICKER, force=force)
    except Exception as exc:
        log.error("Error refrescando benchmark %s: %s", config.BENCHMARK_TICKER, exc)
    refresh_indicadores()
    refresh_fundamentales(force=force)
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
    """Agrega variación % diaria, medias móviles y la marca "congelado" a
    cada registro. "congelado" (volumen 0) señala un día sin transacciones
    reales — yfinance a veces devuelve así varias semanas seguidas para
    tickers de la BCS (visto incluso en nombres líquidos como Quiñenco).
    Antes esos tramos se rellenaban con una interpolación lineal que
    fabricaba una tendencia de precio que nunca ocurrió; ahora se muestra
    el dato real (plano) y se deja que el frontend lo marque visualmente
    en vez de inventar una variación diaria creíble pero falsa."""
    closes = [r["close"] for r in records]
    prev = None
    for i, r in enumerate(records):
        c = r["close"]
        r["change_pct"] = round((c - prev) / prev * 100, 2) if prev else 0.0
        prev = c
        r["ma20"] = round(sum(closes[max(0, i - 19):i + 1]) / min(i + 1, 20), 2)
        r["ma50"] = round(sum(closes[max(0, i - 49):i + 1]) / min(i + 1, 50), 2)
        r["congelado"] = not r.get("volume")
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
        "precio_manual": info.get("precio_manual"),
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

    # Valorización de la posición. Si hay un precio_manual cargado (acciones
    # ilíquidas donde el último cierre de Yahoo Finance queda desactualizado
    # por falta de transacciones), reemplaza el precio vigente para efectos
    # de valorización — pero no reescribe la serie histórica real (records),
    # que sigue viniendo de yfinance para MM20/MM50, 52w, retorno 1 año, etc.
    precio_manual = data.get("precio_manual")
    last_close = precio_manual if precio_manual is not None else last["close"]
    cantidad = data["cantidad"] or 0
    precio_compra = data["precio_compra"] or 0
    invertido = round(cantidad * precio_compra, 2)
    valor_mercado = round(cantidad * last_close, 2)
    pnl = round(valor_mercado - invertido, 2)
    pnl_pct = round((last_close - precio_compra) / precio_compra * 100, 2) if precio_compra else 0.0

    # Distancia entre MM20 y MM50, hoy y hace ~5 ruedas, para poder avisar
    # cuando un cruce dorado/de la muerte está cerca (sin predecir cuándo:
    # sólo qué tan cerca están y si se están acercando). Ver app.js maProximity().
    ref = recs[max(0, len(recs) - 6)]
    ma_gap_pct = (
        round((last["ma20"] - last["ma50"]) / last["ma50"] * 100, 2) if last.get("ma50") else None
    )
    ma_gap_pct_prev = (
        round((ref["ma20"] - ref["ma50"]) / ref["ma50"] * 100, 2) if ref.get("ma50") else None
    )

    return {
        "ticker": ticker,
        "name": data["name"],
        "source": data["source"],
        "last_close": last_close,
        "precio_manual": precio_manual,
        "change_pct": last["change_pct"] if precio_manual is None else 0.0,
        "volume": last["volume"],
        "high": last["high"],
        "low": last["low"],
        "high_52w": round(max(closes[-252:]), 2),
        "low_52w": round(min(closes[-252:]), 2),
        "return_1y": round((last["close"] - first_year["close"]) / first_year["close"] * 100, 2)
        if first_year["close"] else 0.0,
        "last_date": last["date"],
        "stale": data["stale"] and precio_manual is None,
        "days_old": data["days_old"],
        "n_points": len(recs),
        # Posición del portafolio
        "cantidad": cantidad,
        "precio_compra": precio_compra,
        "invertido": invertido,
        "valor_mercado": valor_mercado,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "ma_gap_pct": ma_gap_pct,
        "ma_gap_pct_prev": ma_gap_pct_prev,
    }
