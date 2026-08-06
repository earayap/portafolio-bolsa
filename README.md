# 📈 Portafolio Bursátil — Bolsa de Comercio de Santiago

Aplicación (web + escritorio, Flask/Docker/PyInstaller) para visualizar un
portafolio de acciones chilenas con historia diaria de al menos **2 años**:
precio de cierre, variación %, volumen, máximos/mínimos, medias móviles y
retornos.

Este proyecto es de código abierto: cada quien configura **su propio**
portafolio localmente. Ninguna posición, cantidad o precio de compra de nadie
se incluye en el repositorio.

## ⚙️ Configura tu portafolio

Las acciones se definen en `portfolio.json`, que **no se sube a git**
(está en `.gitignore`). Al clonar el repo:

```bash
cp portfolio.example.json portfolio.json
```

Luego edita `portfolio.json` con tus propios tickers, cantidades y precio de
compra promedio:

```json
{
  "QUINENCO.SN": {"nombre": "Quiñenco S.A.", "cantidad": 100, "precio_compra": 4000.00}
}
```

> El ticker debe ser el símbolo oficial de Yahoo Finance / Bolsa de Santiago
> (sufijo `.SN`). Si no existe `portfolio.json`, la app arranca igual usando
> `portfolio.example.json` con datos de ejemplo.

## Fuente de datos y resiliencia

El servicio (`data_service.py`) sigue esta cascada para que la app **nunca falle**:

1. **`yfinance`** — descarga historia diaria de 2 años desde Yahoo Finance.
2. **Cache SQLite** (`data/portfolio.db`) — todo lo descargado se persiste; si la API
   no responde, se sirve lo último guardado.
3. **Fallback sintético** — si una acción es ilíquida o el símbolo no devuelve datos
   y nunca hubo cache, se genera una serie determinística (estable por ticker) para
   que la interfaz cargue fluido.

La fuente real de cada acción se muestra en la interfaz con una etiqueta:
**En vivo** (yfinance) · **Cache local** · **Estimado** (sintético).

## 🚀 Ejecución con Docker (recomendado)

```bash
docker compose up --build
```

Luego abre <http://localhost:8000>.

La cache SQLite se persiste en `./data` gracias al volumen del `docker-compose.yml`,
así que los reinicios son instantáneos.

## 🐍 Ejecución local (sin Docker)

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

Abre <http://localhost:8000>.

## 🖥️ Aplicación de escritorio (Windows, sin instalar nada)

También existe una versión empaquetada como ejecutable standalone (no requiere
Python ni Docker instalados). Se genera con PyInstaller a partir de
`desktop.py`, que levanta el servidor Flask y abre el navegador automáticamente:

```bash
pip install pyinstaller
pyinstaller build_exe.spec
```

El ejecutable queda en `dist/PortafolioBolsa/PortafolioBolsa.exe`. Antes de
generarlo, recuerda copiar y editar tu `portfolio.json` (ver sección anterior);
el instalador lo empaqueta junto al resto de la app.

## API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/status` | Estado de la carga inicial |
| GET | `/api/stocks` | Resumen de todas las acciones |
| GET | `/api/stock/<ticker>?range=1m\|3m\|6m\|1y\|2y\|all` | Historia con indicadores |
| GET | `/api/indicadores` | Último valor de UF, dólar, TPM, cobre, IPC, UTM |
| GET | `/api/indicadores/<codigo>?limit=90` | Historial reciente de un indicador (ej. `uf`) |
| GET | `/api/screener` | Señal COMPRAR/MANTENER/VENDER por acción, con sus métricas |
| GET | `/api/fundamentales` | P/E, P/B, ROE, margen neto, deuda/patrimonio y sector por acción |
| GET | `/api/fundamentales/<ticker>` | Fundamentales de una acción puntual |
| GET | `/api/backtest?years=3` | Retorno futuro realizado según la señal histórica del screener |
| GET | `/api/simulador/<ticker>?horizonte=1m\|3m\|6m\|1a` | Simulación de Monte Carlo: probabilidad de subir/bajar |
| POST | `/api/refresh?ticker=<opcional>` | Fuerza actualización desde la API |

## Screener cuantitativo

`screener.py` calcula, para cada acción del portafolio, sobre el último año
bursátil (252 ruedas, `LOOKBACK_DAYS`):

- **Retorno diario simple** — base de casi todo lo demás:
  ```
  r_t = (P_t - P_(t-1)) / P_(t-1)
  ```

- **Retorno anualizado** — compuesto sobre el período observado:
  ```
  retorno_anual = (P_final / P_inicial) ^ (252 / n_dias) - 1
  ```

- **Volatilidad anualizada** — desviación estándar muestral de los retornos
  diarios, escalada a un año:
  ```
  volatilidad_anual = desviación_estándar(r_1..r_n) × √252
  ```

- **Sharpe ratio** — retorno anualizado menos la TPM vigente (como tasa
  libre de riesgo en CLP), sobre la volatilidad anualizada:
  ```
  sharpe = (retorno_anual - TPM/100) / volatilidad_anual
  ```

- **Beta** vs el mercado chileno, usando **ECH** (iShares MSCI Chile ETF)
  como proxy — Yahoo Finance no tiene historia utilizable para `^IPSA`:
  ```
  beta = Covarianza(r_acción, r_mercado) / Varianza(r_mercado)
  ```

- **Drawdown máximo** — la peor caída desde cualquier máximo previo dentro
  del período:
  ```
  drawdown_max = mín para todo t de [ (P_t - máx(P_1..P_t)) / máx(P_1..P_t) ]
  ```

- **Retorno real** — retorno nominal del período menos la variación de la
  UF en el mismo período (descuenta inflación):
  ```
  retorno_real = retorno_nominal - (UF_final - UF_inicial) / UF_inicial
  ```

- **Dividend yield** — dividendos por acción pagados en los últimos 12
  meses (con precedencia de la tabla oficial BCS), sobre el precio actual:
  ```
  dividend_yield = dividendos_12m_por_acción / precio_actual
  ```

Con esas métricas arma un puntaje: `+2` si Sharpe > 1, `+1` si > 0.5, `-2`
si < 0; `+2` si dividend yield > 5%, `+1` si > 2%; `-2` si retorno real
< -10%, `+1` si > 5%; `-1` si drawdown < -40%; `-1` si beta > 1.5 sin Sharpe
que lo respalde. El puntaje total define la señal: `≥3` COMPRAR, `<0`
VENDER, el resto MANTENER. Es determinístico y transparente — no hay
proyecciones ni narrativa, solo lo ya observado.

Cuando hay fundamentales disponibles (ver sección siguiente), también suman
al puntaje: **P/E bajo** (≤12, barata en relación a sus utilidades) o
**ROE alto** (>15%) suman `+1` cada uno; **P/E negativo o alto** (>30),
**ROE bajo** (<3%) o **deuda/patrimonio alta** (>150, solo para empresas no
financieras — el apalancamiento de un banco como BICE es su modelo de
negocio, no una señal de riesgo) restan `-1` cada uno. Si un ticker no tiene
fundamentales confiables, esas reglas simplemente no aplican — el score no
penaliza por falta de dato.

## Datos fundamentales (P/E, P/B, ROE, margen, deuda)

**Importante:** la CMF (Comisión para el Mercado Financiero) **no tiene una
API pública para emisores no bancarios**. Su única API abierta,
[api.cmfchile.cl](https://api.cmfchile.cl), cubre exclusivamente bancos e
instituciones financieras — de este portafolio, solo alcanzaría a Banco
BICE. Para el resto (industriales, utilities, retail, holdings), los EEFF
de la CMF solo se pueden descargar manualmente en PDF desde su portal; no
son automatizables.

Por eso `fundamentales.py` usa **yfinance** (`Ticker.info`) como fuente
automatizable única, con un filtro de sanidad: para acciones chilenas de
baja liquidez Yahoo Finance a veces devuelve campos con órdenes de magnitud
absurdas (P/B > 1000, márgenes > 800%). Esos valores se descartan y se
muestran como "no disponible" en vez de un dato engañoso. Se cachean en la
tabla `fundamentales` con el mismo TTL que los precios.

## Backtest de la señal (`/backtest`)

Responde la pregunta: *si hubiera seguido esta señal en el pasado, ¿habría
funcionado?* `backtest.py` recorre la historia de precios (se descargan 4
años — ver `HISTORY_YEARS` en `config.py`) y, cada ~1 mes bursátil, recalcula
el score usando **solo datos disponibles hasta esa fecha** (ventana de 1
año hacia atrás, dividendos pagados hasta ese día — nunca información
futura) y mide el retorno real de la acción en los 6 meses siguientes.

**Limitación deliberada:** solo se backtestea la parte de precios/dividendos
del score (las mismas fórmulas de la sección anterior, recalculadas con la
ventana de 1 año que termina en cada fecha histórica evaluada — nunca con
datos posteriores a esa fecha). Los fundamentales (P/E, ROE) quedan fuera
porque solo se guarda el dato *actual* — no hay forma de saber cuál era el
P/E de una acción hace 2 años sin una fuente de historia fundamental, que
no existe gratis para emisores chilenos no bancarios (ver sección
anterior). Meter fundamentales "de hoy" en una evaluación de hace 2 años
sería sesgo de información futura (look-ahead bias) y falsearía el
resultado.

El retorno futuro que valida cada señal se mide así (`HORIZON_DAYS` = 126
ruedas, ~6 meses):
```
retorno_fwd = (P_(t + 126) - P_t) / P_t
```
y la tasa de acierto por señal es simplemente el % de esos puntos con
`retorno_fwd > 0`.

Requiere también historia multianual de UF y TPM (`mindicador.cl` solo trae
los últimos días por defecto); `data_service.backfill_indicadores_historicos`
la descarga una vez al arrancar, año por año.

## Simulador de precio (`/simulador`)

Simulación de Monte Carlo (movimiento geométrico browniano) que usa el
retorno y la volatilidad **históricos** de la propia acción (últimos ~2
años de retornos logarítmicos diarios, `LOOKBACK_DIAS` = 504) para generar
2.000 trayectorias de precio posibles, y así estimar la probabilidad de
que la acción esté más arriba o más abajo que hoy a 1, 3, 6 o 12 meses.

**Fórmulas:** se usan retornos *logarítmicos* (no simples) porque son los
que se componen correctamente en el tiempo bajo este modelo:
```
retorno_log_t = ln(P_t / P_(t-1))
μ = promedio(retorno_log_1..n)          # drift diario
σ = desviación_estándar(retorno_log_1..n)  # volatilidad diaria
```
Cada trayectoria simulada avanza día a día como:
```
P_t = P_0 × exp( (μ - σ²/2) × t + σ × √t × Z )      con Z ~ Normal(0, 1)
```
El término `-σ²/2` es la corrección de Itô: sin ella, promediar muchas
trayectorias multiplicativas sobreestimaría el precio esperado. Con las
2.000 trayectorias simuladas, la probabilidad de subir es simplemente:
```
P(sube) = % de trayectorias donde P_final > P_actual
```
y los percentiles 10/50/90 de `P_final` (y de cada día intermedio, para el
gráfico de abanico) se calculan directamente sobre esa distribución
simulada.

**No es una predicción.** Asume que el comportamiento estadístico pasado
del precio (su drift y volatilidad) se parece al futuro cercano — una
hipótesis razonable a semanas o meses, que se degrada mientras más largo
el horizonte y que no incorpora ningún catalizador (fusiones, resultados,
cambios regulatorios). El resultado es consistente con el screener por
construcción: usan la misma fuente de datos, así que una acción con señal
VENDER típicamente muestra baja probabilidad de subir aquí también.

La semilla aleatoria es determinística por ticker/fecha/horizonte (mismo
resultado si se recarga la página el mismo día, cambia al día siguiente
con datos nuevos).

## Indicadores macroeconómicos

Además de las acciones del portafolio, el servicio descarga a diario desde
[mindicador.cl](https://mindicador.cl/api) (API pública del Banco Central,
sin key) los siguientes indicadores, útiles como contexto para decisiones de
inversión:

| Código | Descripción |
|---|---|
| `uf` | Unidad de Fomento (valor diario) |
| `dolar` | Tipo de cambio USD/CLP observado |
| `utm` | Unidad Tributaria Mensual |
| `ipc` | Variación mensual del IPC |
| `tpm` | Tasa de Política Monetaria del Banco Central |
| `libra_cobre` | Precio de la libra de cobre (USD) |

Se guardan en la misma cache SQLite (tabla `indicadores`), con el mismo
mecanismo de refresco diario que las acciones.

## Estructura

```
portafolio-bolsa/
├── app.py                    # Servidor Flask y rutas
├── desktop.py                # Punto de entrada para el .exe (abre el navegador)
├── data_service.py           # Descarga, cache SQLite y fallback
├── config.py                 # Parámetros técnicos (rutas, TTL, historia)
├── portfolio.example.json    # Portafolio de ejemplo (público)
├── portfolio.json            # Tu portafolio real — NO se sube a git
├── requirements.txt
├── build_exe.spec            # Config de PyInstaller
├── Dockerfile
├── docker-compose.yml
├── templates/index.html
├── static/css/style.css
├── static/js/app.js
└── data/                     # Cache SQLite (persistente)
```

## Características de la interfaz

- Dashboard con KPIs globales (variación media, mejor retorno 1A, fuentes).
- Listado lateral con búsqueda y precio/variación en vivo.
- Gráfico de precios con medias móviles (MM20 / MM50) y área degradada.
- Gráfico de volumen coloreado por dirección del día.
- Selector de rango temporal (1M · 3M · 6M · 1A · 2A · Máx).
- Tema claro/oscuro y reloj de mercado (hora de Santiago).
- Botón de actualización que refresca los datos desde la API.

## 🤝 Contribuir

Ideas bienvenidas: nuevas acciones en `portfolio.example.json`, mejoras de
indicadores en `data_service.py`, o de interfaz. Haz un fork, crea una rama y
abre un Pull Request. Antes de subir cualquier cambio, verifica que no incluya
tu `portfolio.json` real ni datos personales (`git status` no debería
mostrarlo — está en `.gitignore`).

## Licencia

MIT — ver [LICENSE](LICENSE). Úsalo, modifícalo y compártelo libremente.
