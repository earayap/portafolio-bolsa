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
| POST | `/api/refresh?ticker=<opcional>` | Fuerza actualización desde la API |

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
