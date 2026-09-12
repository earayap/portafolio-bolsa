FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

# Dependencias primero (mejor cache de capas)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código de la aplicación
COPY . .

# Carpeta de datos persistente (cache SQLite)
RUN mkdir -p /app/data

EXPOSE 8000

# 1 worker (single process) con varios threads: PORTAFOLIO/_PORTFOLIO viven
# en memoria de proceso, así que con >1 worker cada proceso tenía su propia
# copia y una escritura en un worker quedaba invisible para el otro hasta
# reiniciar el contenedor (posiciones "no se actualizaban" de forma
# intermitente). Los threads sí comparten memoria, así que mantienen algo
# de concurrencia sin ese problema.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"]
