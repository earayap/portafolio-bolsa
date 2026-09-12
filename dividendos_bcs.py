"""Dividendos oficiales de la Bolsa de Comercio de Santiago.

Fuente: https://www.bolsadesantiago.com/dividendos/<NEMOTECNICO>
(el nemotécnico es el ticker sin el sufijo ".SN").

Se registran los dividendos por su FECHA DE PAGO ("date") y el monto declarado
por acción (campo "Evento", que trae el valor exacto en pesos chilenos; el
campo "Pago por Acción" del sitio viene redondeado a enteros y NO se usa).

Cada registro trae además "date_ex" (columna "Fecha límite" del sitio — el
corte de derecho a dividendo, comúnmente llamada fecha ex-dividendo): hay que
tener las acciones compradas ANTES de esa fecha para cobrar el dividendo,
independiente de cuántas acciones se tengan para cuando se paga. data_service
usa "date_ex" (no "date") para calcular cuántas acciones había en cartera en
ese momento (ver data_service.cantidad_al) — usar la fecha de pago ahí
sobrestima sistemáticamente el monto, porque cuenta compras hechas después
del corte que en la realidad no alcanzaron a cobrar ese dividendo.

Esta tabla tiene PRECEDENCIA sobre los dividendos de Yahoo Finance, porque para
las acciones chilenas el dato oficial de la BCS es más completo y confiable.

Notas:
- Se incluyen también dividendos ya DECLARADOS con fecha de pago futura; el
  servicio sólo suma los que ya fueron pagados (date <= hoy), de modo que se
  contabilizan automáticamente al llegar su fecha.
- CFMITNIPSA es un fondo de inversión con repartos frecuentes y fraccionarios;
  se listan agrupados por fecha de pago. Su fecha límite es sistemáticamente
  1 día calendario antes del pago (patrón confirmado en 15+ repartos
  consecutivos del sitio de la BCS) — se aplica esa regla en vez de revisar
  cada uno a mano.
- Excepción a "usar Evento, no Pago por Acción": FROWARD y SOQUICOM declaran
  su dividendo en US$ (columna "Evento" viene en dólares crudos, sin
  convertir). Para esos dos emisores se usa "Pago por Acción" en su lugar,
  que sí viene en CLP ya convertido al tipo de cambio del día — usar el
  Evento en US$ como si fuera CLP subestimaría el monto ~800-900 veces.

Formato: ticker (con .SN) -> lista de {"date": "YYYY-MM-DD" (pago),
"date_ex": "YYYY-MM-DD" (fecha límite / ex-dividendo), "amount": CLP_por_accion}

Datos recolectados el 2026-07-28; fechas ex-dividendo y dividendos declarados
después de esa fecha (PROVIDA 2026-08-06) agregados el 2026-08-15. Revisado
contra el sitio de la BCS el 2026-09-11: se corrigieron montos que habían
quedado redondeados a enteros en la carga inicial (BICE, CGET, ECL,
EMBONOR-B, ENLASA, FROWARD, MINERA, NAVARINO, PEHUENCHE, QUINENCO, SOQUICOM,
TRICAHUE) y se agregó un reparto de CFMITNIPSA (2026-08-27) que faltaba
dentro de la ventana en que esa posición estuvo en cartera (2026-02-11 a
2026-08-26). ZOFRI, PROVIDA, SCHWAGER y SQM-B ya estaban correctos.
"""


def _cfmitnipsa_ex(fecha_pago):
    """Fecha límite = fecha de pago - 1 día calendario (ver nota arriba)."""
    from datetime import date, timedelta
    y, m, d = (int(x) for x in fecha_pago.split("-"))
    return (date(y, m, d) - timedelta(days=1)).isoformat()


DIVIDENDOS_BCS = {
    # Banco BICE (BICECORP)
    "BICE.SN": [
        {"date": "2026-05-14", "date_ex": "2026-05-08", "amount": 6.26519},
        {"date": "2026-05-14", "date_ex": "2026-05-08", "amount": 4.18481},
    ],
    # CGE Distribución
    "CGET.SN": [
        {"date": "2026-05-11", "date_ex": "2026-05-05", "amount": 9.044},
    ],
    # Engie Energía Chile
    "ECL.SN": [
        {"date": "2026-05-27", "date_ex": "2026-05-20", "amount": 57.598},
    ],
    # Coca-Cola Embonor B
    "EMBONOR-B.SN": [
        {"date": "2026-05-19", "date_ex": "2026-05-13", "amount": 14.05819},
        {"date": "2026-05-19", "date_ex": "2026-05-13", "amount": 52.94181},
    ],
    # Energía Latina S.A. (Enlasa)
    "ENLASA.SN": [
        {"date": "2026-05-13", "date_ex": "2026-05-07", "amount": 25.719},
        {"date": "2026-05-13", "date_ex": "2026-05-07", "amount": 56.025},
    ],
    # Puerto Froward — dividendo declarado en USD; se usa el "Pago por
    # Acción" (CLP, ya convertido al tipo de cambio del día) en vez del
    # "Evento" (que viene en US$ crudo) porque este emisor es la excepción
    # a la regla general de dividendos_bcs — ver docstring del módulo.
    "FROWARD.SN": [
        {"date": "2026-04-24", "date_ex": "2026-04-18", "amount": 48.487},
    ],
    # Empresas Lipigas S.A.
    "LIPIGAS.SN": [
        {"date": "2026-03-31", "date_ex": "2026-03-25", "amount": 95},
        {"date": "2026-05-07", "date_ex": "2026-04-30", "amount": 118},
        {"date": "2026-06-22", "date_ex": "2026-06-16", "amount": 95},
    ],
    # Minera Valparaíso
    "MINERA.SN": [
        {"date": "2026-05-18", "date_ex": "2026-05-12", "amount": 320.72},
    ],
    # Navarino
    "NAVARINO.SN": [
        {"date": "2026-05-07", "date_ex": "2026-04-30", "amount": 84.791},
    ],
    # Empresa Eléctrica Pehuenche
    "PEHUENCHE.SN": [
        {"date": "2026-05-15", "date_ex": "2026-05-09", "amount": 74.962},
    ],
    # Quiñenco S.A.
    "QUINENCO.SN": [
        {"date": "2026-05-15", "date_ex": "2026-05-09", "amount": 62.61297},
        {"date": "2026-05-15", "date_ex": "2026-05-09", "amount": 286.4259},
    ],
    # Inversiones Tricahue
    "TRICAHUE.SN": [
        {"date": "2026-05-22", "date_ex": "2026-05-15", "amount": 38.5},
    ],
    # AFP Provida
    "PROVIDA.SN": [
        {"date": "2026-05-28", "date_ex": "2026-05-22", "amount": 153},
        {"date": "2026-08-06", "date_ex": "2026-07-31", "amount": 55},
    ],
    # Zofri S.A. (el 2026-11-27 está declarado pero aún no pagado)
    "ZOFRI.SN": [
        {"date": "2026-05-22", "date_ex": "2026-05-15", "amount": 36.19},
        {"date": "2026-11-27", "date_ex": "2026-11-21", "amount": 36.19},
    ],
    # Schwager S.A.
    "SCHWAGER.SN": [
        {"date": "2026-05-29", "date_ex": "2026-05-23", "amount": 0.066},
    ],
    # Soquimich Comercial S.A. — dividendo declarado en USD; ver nota en FROWARD.
    "SOQUICOM.SN": [
        {"date": "2026-05-15", "date_ex": "2026-05-09", "amount": 25.227},
    ],
    # SQM-B
    "SQM-B.SN": [
        {"date": "2026-05-14", "date_ex": "2026-05-08", "amount": 917.19},
    ],
    # CFI IT Nipsa (fondo de inversión; repartos frecuentes agrupados por fecha)
    "CFMITNIPSA.SN": [
        {"date": "2026-03-26", "amount": 34.96},
        {"date": "2026-03-31", "amount": 0.17},
        {"date": "2026-04-02", "amount": 7.51},
        {"date": "2026-04-09", "amount": 8.90},
        {"date": "2026-04-20", "amount": 6.34},
        {"date": "2026-04-24", "amount": 1.55},
        {"date": "2026-04-29", "amount": 2.24},
        {"date": "2026-04-30", "amount": 3.53},
        {"date": "2026-05-06", "amount": 17.00},
        {"date": "2026-05-12", "amount": 1.32},
        {"date": "2026-05-14", "amount": 20.42},
        {"date": "2026-05-15", "amount": 7.91},
        {"date": "2026-05-20", "amount": 14.39},
        {"date": "2026-05-27", "amount": 1.82},
        {"date": "2026-05-28", "amount": 3.17},
        {"date": "2026-06-03", "amount": 0.01},
        {"date": "2026-06-04", "amount": 1.74},
        {"date": "2026-08-27", "amount": 1.52689841},
    ],
}

for _evento in DIVIDENDOS_BCS["CFMITNIPSA.SN"]:
    _evento["date_ex"] = _cfmitnipsa_ex(_evento["date"])
