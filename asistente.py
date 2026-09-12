"""Asistente conversacional del portafolio (modelo local vía Ollama).

Chat en lenguaje natural que responde preguntas sobre las acciones del
portafolio usando los mismos datos ya calculados por el resto de la app
(posiciones, screener, dividendos, fundamentales, valor de empresa,
backtest, simulador, indicadores macro) — vía tool calling, no un prompt con
todo el estado pegado adentro: cada pregunta dispara solo las llamadas que
necesita, y siempre contra datos frescos (no un snapshot fijo).

Corre 100% local contra un servidor Ollama (sin costo de API, sin mandar
datos del portafolio a ningún servicio externo). Requiere tener Ollama
corriendo y el modelo descargado (`ollama pull llama3.1:8b`). Configurable
vía OLLAMA_HOST (default http://localhost:11434 — usar
http://host.docker.internal:11434 al correr en Docker, ver docker-compose.yml)
y OLLAMA_MODEL (default llama3.1:8b, elegido porque entra completo en 8GB de
VRAM y tiene soporte nativo de tool-calling).
"""

import difflib
import json
import os
from datetime import datetime, timezone

import requests

import backtest
import data_service
import screener
import simulador
import valor_empresa

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
TIMEOUT = 120

SYSTEM_PROMPT = """Eres el asistente del portafolio de acciones chilenas del usuario \
(Bolsa de Comercio de Santiago). Respondes en español, de forma directa y fría: solo \
datos observados, sin hype de mercado, sin proyecciones de precio futuro ni \
recomendaciones genéricas de inversión. Si el usuario pregunta si debería comprar o \
vender, respóndele con lo que dicen las métricas ya calculadas (señal del screener, \
Sharpe, dividend yield, valoración) — nunca inventes un número, siempre consulta las \
herramientas disponibles antes de responder sobre precios, cantidades o dividendos.

Usa siempre las herramientas para obtener datos reales de la cartera antes de responder \
preguntas sobre acciones, precios, dividendos o posiciones — no respondas de memoria. \
Los montos son en pesos chilenos (CLP) salvo que se indique lo contrario. Sé breve: \
respuestas de pocas frases, con los números concretos, no ensayos. Responde siempre en \
lenguaje natural, nunca con código (nada de Python, pandas, ni bloques ```): el resultado \
de la herramienta ya viene procesado, solo tienes que leerlo y responder con palabras. \
Si te preguntan cuáles acciones cumplen una condición (ej. "cuáles están BARATAS", "cuál \
tiene la mayor ganancia"), responde con los tickers exactos usando los campos ya \
agrupados o precalculados que traen las herramientas — no des una respuesta genérica \
sobre cómo evaluar acciones en general, y no agregues disclaimers ni recomendaciones de \
"investigar más": si la herramienta no trae el dato, dilo así de directo."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "resumen_cartera",
            "description": "Devuelve 'posiciones' (todas las acciones de la cartera, "
            "ordenadas de mayor a menor ganancia %, con ticker, nombre, cantidad, "
            "precio actual, valor de mercado, ganancia/pérdida %, peso en la cartera y "
            "señal del screener) más 'mayor_ganancia_pct', 'mayor_perdida_pct' y "
            "'mayor_peso_en_cartera' ya calculados — para preguntas de \"mejor/peor "
            "posición\" o \"cuál pesa más\", usa directamente esos tres campos en vez "
            "de recorrer 'posiciones'. Punto de partida para casi cualquier pregunta "
            "sobre la cartera en su conjunto, y también la forma correcta de "
            "encontrar el ticker exacto de una empresa por su nombre — si no estás "
            "100% seguro del ticker de una acción, llama primero a esta herramienta y "
            "busca el 'nombre' que coincida, en vez de adivinar un ticker (los "
            "tickers reales del mercado a veces no coinciden con los de esta "
            "cartera).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detalle_accion",
            "description": "Detalle completo de una acción puntual: precio, cantidad, "
            "precio de compra, ganancia/pérdida, señal del screener con las razones, "
            "fundamentales (P/E, ROE, margen, deuda) y clasificación de valor de "
            "empresa.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker con sufijo .SN, ej. QUINENCO.SN"},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dividendos",
            "description": "Dividendos de la cartera, opcionalmente filtrados por "
            "ticker, ya separados en 'dividendos_ya_pagados' (con 'total_cobrado_clp') "
            "y 'dividendos_declarados_a_futuro_no_pagados' — usa cada lista según lo "
            "que pregunte el usuario, no las mezcles. Cada registro trae monto por "
            "acción, cantidad de acciones en esa fecha y total en CLP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker con sufijo .SN (opcional; si se omite trae toda la cartera)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "valor_empresa_cartera",
            "description": "Clasificación BARATA/NEUTRAL/CARA por Valor de Empresa "
            "(EV/EBITDA, EV/Ventas) de cada acción de la cartera — valoración pura, "
            "sin mezclar riesgo. Devuelve 'baratas', 'neutrales', 'caras' y "
            "'sin_datos' ya agrupadas — usa directamente la lista que corresponda a "
            "la pregunta en vez de filtrar 'todas' vos mismo.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "indicadores_macro",
            "description": "Último valor de los indicadores macro chilenos: UF, "
            "dólar, TPM, IPC, UTM y precio de la libra de cobre.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backtest_senal",
            "description": "Backtest histórico de la señal del screener: retorno real "
            "a 6 meses que habría tenido seguir cada señal (COMPRAR/MANTENER/VENDER) "
            "en el pasado, y tasa de acierto por señal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "years": {"type": "integer", "description": "Años de historia a evaluar (default 3)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulador_montecarlo",
            "description": "Simulación de Monte Carlo para una acción puntual: "
            "probabilidad estadística de que el precio esté más arriba o más abajo "
            "que hoy a 1/3/6/12 meses, basada en su volatilidad histórica. No es una "
            "predicción — dilo así si el usuario lo pregunta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker con sufijo .SN"},
                    "horizonte": {"type": "string", "enum": ["1m", "3m", "6m", "1a"], "description": "Horizonte (default 6m)"},
                },
                "required": ["ticker"],
            },
        },
    },
]


_PLACEHOLDERS = {"none", "null", "n/a", "undefined", ""}


def _str_arg(tool_input, key):
    """Lee un argumento string opcional, tratando los placeholders que a
    veces manda el modelo local (el string literal "None"/"null" en vez de
    omitir el campo) como si no se hubiera pasado nada."""
    v = (tool_input.get(key) or "").strip()
    return v if v.lower() not in _PLACEHOLDERS else ""


def _resolver_ticker(entrada, portafolio):
    """Encuentra el ticker real de la cartera a partir de lo que mandó el
    modelo — que puede venir con errores de tipeo (p.ej. "PEHUENCHO.SN" en
    vez de "PEHUENCHE.SN") o como un intento de nombre de empresa mal armado
    (p.ej. "BANCO.SN" por "Banco de Chile"). Devuelve (ticker, None) si
    encuentra una sola coincidencia razonable, o (None, candidatos) si el
    texto es ambiguo (calza con más de una empresa) — nunca adivina entre
    varias opciones igual de válidas, porque eso es justo lo que causó que
    "BANCO.SN" resolviera silenciosamente a Banco BICE en vez de Banco de
    Chile. (None, None) si no encuentra nada parecido."""
    entrada = (entrada or "").strip().upper()
    if not entrada:
        return None, None, False

    sin_sufijo = entrada[:-3] if entrada.endswith(".SN") else entrada
    con_sufijo = entrada if entrada.endswith(".SN") else f"{entrada}.SN"
    if con_sufijo in portafolio:
        return con_sufijo, None, False

    nombres = {t: (info.get("nombre") or "").upper() for t, info in portafolio.items()}
    exactos = [t for t, nombre in nombres.items() if nombre == entrada]
    if len(exactos) == 1:
        return exactos[0], None, False

    parciales = [t for t, nombre in nombres.items() if nombre and sin_sufijo in nombre]
    if len(parciales) == 1:
        return parciales[0], None, False
    if len(parciales) > 1:
        return None, [{"ticker": t, "nombre": portafolio[t].get("nombre")} for t in parciales], False

    # A partir de acá el match es solo por similitud de texto (ej. "BCI" vs
    # "BICE") — dos empresas reales distintas pueden tener tickers parecidos,
    # así que esto es mucho menos confiable que un match exacto o por nombre.
    # Se marca "aproximado" para que quien llame pueda avisarle al modelo.
    candidatos = list(portafolio.keys()) + [t[:-3] for t in portafolio]
    match = difflib.get_close_matches(con_sufijo, candidatos, n=1, cutoff=0.75) or \
        difflib.get_close_matches(sin_sufijo, candidatos, n=1, cutoff=0.75)
    if match:
        m = match[0]
        return (m if m.endswith(".SN") else f"{m}.SN"), None, True

    match_nombre = difflib.get_close_matches(entrada, list(nombres.values()), n=1, cutoff=0.75)
    if match_nombre:
        for t, nombre in nombres.items():
            if nombre == match_nombre[0]:
                return t, None, True

    return None, None, False


def _error_ticker(entrada, candidatos):
    if candidatos:
        opciones = ", ".join(f"{c['ticker']} ({c['nombre']})" for c in candidatos)
        return {"error": f"'{entrada}' es ambiguo, coincide con varias acciones de la cartera: {opciones}. Especifica el ticker exacto."}
    return {"error": f"'{entrada}' no coincide con ninguna acción de la cartera"}


def _run_tool(name, tool_input, portafolio):
    tickers = list(portafolio.keys())

    if name == "resumen_cartera":
        screener_map = {r["ticker"]: r for r in screener.evaluate_all(tickers)}
        out = []
        for t in tickers:
            s = data_service.get_summary(t)
            if not s:
                continue
            sig = screener_map.get(t, {})
            out.append({
                "ticker": t,
                "nombre": portafolio[t].get("nombre", t),
                "cantidad": s["cantidad"],
                "precio": s["last_close"] if s.get("precio_manual") is None else s["precio_manual"],
                "valor_mercado": s["valor_mercado"],
                "pnl_pct": s["pnl_pct"],
                "señal": sig.get("senal"),
            })
        total = sum(x["valor_mercado"] or 0 for x in out)
        for x in out:
            x["peso_pct"] = round((x["valor_mercado"] / total) * 100, 1) if total else 0
        # Ordenado de mayor a menor ganancia %: el modelo local es chico y no
        # siempre escanea bien una lista de 25 para encontrar el máximo/mínimo
        # por su cuenta — dejar el orden ya resuelto (y el propio máximo/mínimo
        # ya calculado aparte, ver abajo) evita ese error.
        out.sort(key=lambda x: x["pnl_pct"] if x["pnl_pct"] is not None else -1e9, reverse=True)
        return {
            "posiciones": out,
            "mayor_ganancia_pct": out[0] if out else None,
            "mayor_perdida_pct": out[-1] if out else None,
            "mayor_peso_en_cartera": max(out, key=lambda x: x["peso_pct"]) if out else None,
        }

    if name == "detalle_accion":
        entrada = _str_arg(tool_input, "ticker")
        ticker, candidatos, aproximado = _resolver_ticker(entrada, portafolio)
        if not ticker:
            return _error_ticker(entrada, candidatos)
        resumen = data_service.get_summary(ticker)
        sig = screener.evaluate(ticker)
        fund = data_service.get_fundamentales(ticker)
        ve = valor_empresa.evaluate(ticker)
        resultado = {"resumen": resumen, "screener": sig, "fundamentales": fund, "valor_empresa": ve}
        if aproximado:
            resultado["advertencia"] = (
                f"'{entrada}' no calzó exacto con ningún ticker ni nombre de la "
                f"cartera; se asumió {ticker} ({portafolio[ticker].get('nombre')}) por "
                f"parecido de texto, que podría ser una empresa distinta a la que "
                f"preguntas. Dile esto al usuario en vez de responder como si fuera "
                f"seguro."
            )
        return resultado

    if name == "dividendos":
        entrada_ticker = _str_arg(tool_input, "ticker")
        ticker = None
        aproximado = False
        if entrada_ticker:
            ticker, candidatos, aproximado = _resolver_ticker(entrada_ticker, portafolio)
            if not ticker:
                return _error_ticker(entrada_ticker, candidatos)
        registros = data_service.list_dividendos_todos(ticker)
        hoy = datetime.now(timezone.utc).date().isoformat()
        # El modelo local no razona bien solo sobre qué fecha es "futura" —
        # se hace la separación acá en vez de mandarle una lista mezclada con
        # la fecha de hoy y esperar que filtre bien.
        pagados = [r for r in registros if r["date"] <= hoy]
        futuros = sorted((r for r in registros if r["date"] > hoy), key=lambda r: r["date"])
        total = sum(r["total"] for r in pagados)
        resultado = {
            "hoy": hoy,
            "total_cobrado_clp": round(total, 2),
            "dividendos_ya_pagados": pagados[:30],
            "dividendos_declarados_a_futuro_no_pagados": futuros,
        }
        if aproximado:
            resultado["advertencia"] = (
                f"'{entrada_ticker}' no calzó exacto; se asumió {ticker} "
                f"({portafolio[ticker].get('nombre')}) por parecido de texto — podría "
                f"ser una empresa distinta. Dile esto al usuario."
            )
        return resultado

    if name == "valor_empresa_cartera":
        rows = valor_empresa.evaluate_all(tickers)
        # El modelo local se confunde con un JSON grande y denso (15+ campos
        # numéricos por ticker) — se lo reduce a lo mínimo para responder
        # "cuáles están baratas/caras" y se agrupa por clasificación en vez
        # de dejar que el propio modelo filtre o recalcule el score.
        compactas = [
            {
                "ticker": r["ticker"],
                "clasificacion": r.get("clasificacion"),
                "razones": r.get("razones"),
                "diferencia_vs_precio_justo_pct": r.get("diferencia_pct"),
            }
            for r in rows
        ]
        grupos = {"BARATA": [], "NEUTRAL": [], "CARA": [], "SIN_DATOS": []}
        for r in compactas:
            clave = (r.get("clasificacion") or "SIN DATOS").upper().replace(" ", "_")
            grupos.setdefault(clave, []).append(r)
        return {
            "baratas": grupos["BARATA"],
            "caras": grupos["CARA"],
            "neutrales": grupos["NEUTRAL"],
            "sin_datos": grupos["SIN_DATOS"],
        }

    if name == "indicadores_macro":
        return data_service.get_indicadores()

    if name == "backtest_senal":
        try:
            years = int(tool_input.get("years") or 3)
        except (TypeError, ValueError):
            years = 3
        return backtest.backtest_portafolio(tickers, years=years)

    if name == "simulador_montecarlo":
        entrada = _str_arg(tool_input, "ticker")
        ticker, candidatos, aproximado = _resolver_ticker(entrada, portafolio)
        if not ticker:
            return _error_ticker(entrada, candidatos)
        horizonte = _str_arg(tool_input, "horizonte") or "6m"
        crudo = simulador.simular(ticker, horizonte)
        if not crudo:
            return {"error": "historia insuficiente para simular"}
        # "historico" (60 precios diarios) y "abanico" (23 puntos del gráfico
        # de percentiles) son para dibujar un gráfico, no para responder una
        # pregunta en texto — el mismo problema de JSON denso que confundía
        # al modelo en valor_empresa_cartera. Se descartan acá.
        resultado = {k: v for k, v in crudo.items() if k not in ("historico", "abanico")}
        if aproximado:
            resultado["advertencia"] = (
                f"'{entrada}' no calzó exacto; se asumió {ticker} "
                f"({portafolio[ticker].get('nombre')}) por parecido de texto — podría "
                f"ser una empresa distinta. Dile esto al usuario."
            )
        return resultado

    return {"error": f"herramienta desconocida: {name}"}


def _ollama_disponible():
    try:
        requests.get(f"{OLLAMA_HOST}/api/version", timeout=3)
        return True
    except requests.RequestException:
        return False


def responder(mensaje, historial, portafolio):
    """Responde una pregunta del usuario. `historial` es una lista de mensajes
    previos (formato de Ollama /api/chat, sin el nuevo mensaje). Devuelve
    (texto_respuesta, historial_actualizado)."""
    if not _ollama_disponible():
        return (
            f"No se pudo conectar a Ollama en {OLLAMA_HOST}. Verifica que esté "
            f"corriendo (`ollama serve`) y que el modelo esté descargado "
            f"(`ollama pull {MODEL}`).",
            historial,
        )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(historial) + [
        {"role": "user", "content": mensaje}
    ]

    for _ in range(6):  # tope de vueltas de tool-use por pregunta
        try:
            resp = requests.post(
                f"{OLLAMA_HOST}/api/chat",
                json={"model": MODEL, "messages": messages, "tools": TOOLS, "stream": False},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            return (f"Error llamando a Ollama: {e}", historial[1:] if historial and historial[0].get("role") == "system" else historial)

        msg = data.get("message", {})
        messages.append(msg)
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            return (msg.get("content", ""), messages[1:])  # sin el system prompt

        for call in tool_calls:
            fn = call.get("function", {})
            nombre = fn.get("name")
            args = fn.get("arguments") or {}
            try:
                resultado = _run_tool(nombre, args, portafolio)
            except Exception as e:  # datos inesperados no deben tumbar el chat
                resultado = {"error": str(e)}
            messages.append({
                "role": "tool",
                "content": json.dumps(resultado, ensure_ascii=False, default=str),
            })

    return ("No pude terminar de responder (demasiadas llamadas a herramientas). Intenta de nuevo.", messages[1:])
