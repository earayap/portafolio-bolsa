"""Parser de comprobantes de compra/venta (facturas electrónicas de
Vector Capital e Itaú Corredores de Bolsa) hacia operaciones de la
tabla `operaciones` (ver data_service.py).

Ambas corredoras emiten "Factura Electrónica" en PDF pero con layouts
distintos:

- **Itaú**: una línea por instrumento, con toda la info en el mismo
  renglón: "{COMPRA|VENTA} {tipo instrumento} {TICKER} [APB] {fecha}",
  seguida de precio/cantidad/monto en las 3 líneas siguientes. Una
  misma factura puede listar más de un instrumento (ver
  ITAU_...768.pdf: BICE + SCHWAGER en la misma factura).
- **Vector Capital**: dos líneas de encabezado por instrumento
  ("{TIPO} RV {TICKER}" + "{TIPO} ACCIONES CB {TICKER}", duplicadas),
  sin fecha propia — la fecha es la del encabezado de la factura
  completa ("LAS CONDES, DD de MES del AAAA").

El ticker se deriva como "{NOMBRE}.SN": coincide con el 100% de los
tickers ya presentes en portfolio.json (BICE, CGET, ECL, EMBONOR-B,
ENLASA, FROWARD, LIPIGAS, MINERA, NAVARINO, PEHUENCHE, QUINENCO,
TRICAHUE), así que no hace falta una tabla de mapeo a mano — también
funciona para instrumentos nuevos (SCHWAGER, SOQUICOM, SQM-B,
CFMITNIPSA) sin tocar este archivo.

Nota sobre la fecha registrada: en Itaú, la fecha que va pegada a la
línea del instrumento coincide con "Fecha Pago" (liquidación), no con
la fecha de emisión de la factura (que puede ser 1-2 días antes). En
Vector Capital no hay fecha por instrumento, así que se usa la fecha
de emisión de la factura. Son ligeras inconsistencias de origen (cada
corredora imprime lo que imprime) pero ambas caen dentro de la misma
ventana de liquidación T+2, suficiente para un registro de control.
"""

import hashlib
import re
from pathlib import Path

import fitz  # PyMuPDF

import config
import data_service

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def _num_cl(s):
    """Convierte un número en formato chileno ('1.676' o '408,971193')
    a float. Punto = separador de miles, coma = separador decimal."""
    return float(s.replace(".", "").replace(",", "."))


def _fecha_ddmmyyyy(s):
    """'13-08-2026' -> '2026-08-13'."""
    d, m, y = s.split("-")
    return f"{y}-{m}-{d}"


def _extraer_texto(path):
    with fitz.open(path) as doc:
        texto = "\n".join(page.get_text() for page in doc)
    # PyMuPDF preserva \xa0 (espacio no separable) tal como está en el PDF
    # en vez de espacio normal — rompe cualquier match de texto/regex si no
    # se normaliza primero.
    return texto.replace("\xa0", " ")


def _hash_archivo(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _lineas(texto):
    return [ln.strip() for ln in texto.split("\n")]


_RE_ITAU_DOC = re.compile(
    r"^(COMPRA|VENTA)\s+(.+?)\s+(\d{2}-\d{2}-\d{4})$"
)

_RE_VECTOR_HEADER = re.compile(r"^(COMPRA|VENTA)\s+RV\s+(.+)$")
_RE_VECTOR_FECHA = re.compile(
    r"LAS CONDES,\s*(\d{1,2})\s*de\s*(\w+)\s*del\s*(\d{4})", re.IGNORECASE
)

_RE_NUM = re.compile(r"^[\d.,]+$")


def _parse_itau(texto, archivo, numero_factura):
    lineas = _lineas(texto)
    operaciones = []
    for i, ln in enumerate(lineas):
        m = _RE_ITAU_DOC.match(ln)
        if not m:
            continue
        tipo, resto, fecha_str = m.groups()
        tokens = resto.split()
        if tokens and tokens[-1].upper() == "APB":
            tokens = tokens[:-1]
        if not tokens:
            continue
        ticker = tokens[-1].upper() + ".SN"

        valores = [v for v in lineas[i + 1:i + 4] if _RE_NUM.match(v)]
        if len(valores) < 3:
            continue
        precio_unitario, cantidad, monto = (_num_cl(v) for v in valores)

        operaciones.append({
            "ticker": ticker,
            "tipo": tipo,
            "fecha": _fecha_ddmmyyyy(fecha_str),
            "cantidad": cantidad,
            "precio_unitario": precio_unitario,
            "monto": monto,
            "institucion": "Itaú",
            "numero_factura": numero_factura,
            "archivo": archivo,
        })
    return operaciones


def _parse_vector_capital(texto, archivo, numero_factura):
    lineas = _lineas(texto)

    fecha = None
    fm = _RE_VECTOR_FECHA.search(texto)
    if fm:
        dia, mes_nombre, anio = fm.groups()
        mes = _MESES.get(mes_nombre.lower())
        if mes:
            fecha = f"{anio}-{mes:02d}-{int(dia):02d}"

    operaciones = []
    i = 0
    while i < len(lineas):
        m = _RE_VECTOR_HEADER.match(lineas[i])
        if not m:
            i += 1
            continue
        tipo, ticker = m.groups()
        ticker = ticker.strip().upper() + ".SN"

        # Salta la línea duplicada "{TIPO} ACCIONES CB {TICKER}".
        j = i + 2
        valores = []
        while j < len(lineas) and not _RE_VECTOR_HEADER.match(lineas[j]) \
                and not lineas[j].upper().startswith("SUB TOTAL"):
            if _RE_NUM.match(lineas[j]):
                valores.append(lineas[j])
            j += 1

        if len(valores) >= 3 and fecha:
            precio_unitario, cantidad, monto = (_num_cl(v) for v in valores[:3])
            operaciones.append({
                "ticker": ticker,
                "tipo": tipo,
                "fecha": fecha,
                "cantidad": cantidad,
                "precio_unitario": precio_unitario,
                "monto": monto,
                "institucion": "Vector Capital",
                "numero_factura": numero_factura,
                "archivo": archivo,
            })
        i = j

    return operaciones


def parse_pdf(path):
    """Parsea un comprobante PDF y devuelve (lista_operaciones, hash).
    Lista vacía si no se reconoce el formato o no se encontraron filas."""
    texto = _extraer_texto(path)
    hash_archivo = _hash_archivo(path)

    m_factura = re.search(r"N[°ºo]\.?\s*([\d.]+)", texto)
    numero_factura = m_factura.group(1).replace(".", "") if m_factura else None

    if "ITAU" in texto.upper():
        ops = _parse_itau(texto, path.name, numero_factura)
    elif "VECTOR CAPITAL" in texto.upper():
        ops = _parse_vector_capital(texto, path.name, numero_factura)
    else:
        ops = []

    for op in ops:
        op["hash"] = hash_archivo
    return ops, hash_archivo


def sync_comprobantes():
    """Escanea config.COMPROBANTES_DIR, parsea todos los PDF encontrados e
    inserta las operaciones nuevas (las duplicadas — mismo comprobante
    descargado más de una vez, o ya sincronizado en una corrida anterior —
    se ignoran vía el UNIQUE de la tabla). Se reparsean todos los archivos
    en cada sync en vez de llevar un registro aparte de "ya visto": con
    ~50 comprobantes es trivial en costo y evita un segundo mecanismo de
    tracking que se puede desincronizar del real dedup por hash.

    Devuelve un resumen: archivos vistos, operaciones nuevas insertadas,
    y la lista de archivos que no se pudieron parsear (formato desconocido
    o filas sin los 3 valores numéricos esperados) para que el usuario los
    revise a mano."""
    carpeta = Path(config.COMPROBANTES_DIR)
    if not carpeta.is_dir():
        return {
            "error": f"No se encontró la carpeta de comprobantes: {carpeta}",
            "archivos_vistos": 0, "operaciones_nuevas": 0, "sin_parsear": [],
        }

    archivos = sorted(carpeta.glob("*.pdf"))
    operaciones = []
    sin_parsear = []
    for path in archivos:
        try:
            ops, _ = parse_pdf(path)
        except Exception as e:  # PDF corrupto o formato inesperado
            sin_parsear.append(f"{path.name}: {e}")
            continue
        if not ops:
            sin_parsear.append(path.name)
            continue
        operaciones.extend(ops)

    insertadas = data_service.insert_operaciones(operaciones)

    return {
        "archivos_vistos": len(archivos),
        "operaciones_encontradas": len(operaciones),
        "operaciones_nuevas": insertadas,
        "sin_parsear": sin_parsear,
    }
