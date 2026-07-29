"""Dividendos oficiales de la Bolsa de Comercio de Santiago.

Fuente: https://www.bolsadesantiago.com/dividendos/<NEMOTECNICO>
(el nemotécnico es el ticker sin el sufijo ".SN").

Se registran los dividendos por su FECHA DE PAGO y el monto declarado por acción
(campo "Evento", que trae el valor exacto en pesos chilenos; el campo "Pago por
Acción" del sitio viene redondeado a enteros y NO se usa).

Esta tabla tiene PRECEDENCIA sobre los dividendos de Yahoo Finance, porque para
las acciones chilenas el dato oficial de la BCS es más completo y confiable.

Notas:
- Se incluyen también dividendos ya DECLARADOS con fecha de pago futura; el
  servicio sólo suma los que ya fueron pagados (date <= hoy), de modo que se
  contabilizan automáticamente al llegar su fecha.
- CFMITNIPSA es un fondo de inversión con repartos frecuentes y fraccionarios;
  se listan agrupados por fecha de pago.

Formato: ticker (con .SN) -> lista de {"date": "YYYY-MM-DD", "amount": CLP_por_accion}
Datos recolectados el 2026-07-28.
"""

DIVIDENDOS_BCS = {
    # Banco BICE (BICECORP)
    "BICE.SN": [
        {"date": "2026-05-14", "amount": 6},
        {"date": "2026-05-14", "amount": 4},
    ],
    # CGE Distribución
    "CGET.SN": [
        {"date": "2026-05-11", "amount": 9},
    ],
    # Engie Energía Chile
    "ECL.SN": [
        {"date": "2026-05-27", "amount": 58},
    ],
    # Coca-Cola Embonor B
    "EMBONOR-B.SN": [
        {"date": "2026-05-19", "amount": 14},
        {"date": "2026-05-19", "amount": 53},
    ],
    # Energía Latina S.A. (Enlasa)
    "ENLASA.SN": [
        {"date": "2026-05-13", "amount": 26},
        {"date": "2026-05-13", "amount": 56},
    ],
    # Puerto Froward
    "FROWARD.SN": [
        {"date": "2026-04-24", "amount": 48},
    ],
    # Empresas Lipigas S.A.
    "LIPIGAS.SN": [
        {"date": "2026-03-31", "amount": 95},
        {"date": "2026-05-07", "amount": 118},
        {"date": "2026-06-22", "amount": 95},
    ],
    # Minera Valparaíso
    "MINERA.SN": [
        {"date": "2026-05-18", "amount": 321},
    ],
    # Navarino
    "NAVARINO.SN": [
        {"date": "2026-05-07", "amount": 85},
    ],
    # Empresa Eléctrica Pehuenche
    "PEHUENCHE.SN": [
        {"date": "2026-05-15", "amount": 75},
    ],
    # Quiñenco S.A.
    "QUINENCO.SN": [
        {"date": "2026-05-15", "amount": 63},
        {"date": "2026-05-15", "amount": 286},
    ],
    # Inversiones Tricahue
    "TRICAHUE.SN": [
        {"date": "2026-05-22", "amount": 39},
    ],
    # AFP Provida
    "PROVIDA.SN": [
        {"date": "2026-05-28", "amount": 153},
    ],
    # Zofri S.A. (el 2026-11-27 está declarado pero aún no pagado)
    "ZOFRI.SN": [
        {"date": "2026-05-22", "amount": 36.19},
        {"date": "2026-11-27", "amount": 36.19},
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
    ],
}
