# ── Etapa de construccion ─────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Instalar dependencias del sistema para compilar paquetes nativos (pulp, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Imagen final ──────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Copiar dependencias instaladas en la etapa builder
COPY --from=builder /install /usr/local

# Copiar el codigo del proyecto
COPY backend/  ./backend/
COPY run_demo.py .

# Puerto expuesto por FastAPI/uvicorn
EXPOSE 8000

# Variable de entorno para indicar que estamos en produccion
ENV ENVIRONMENT=PROD
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

# Comando de inicio: uvicorn con recarga desactivada en produccion
CMD ["python", "-m", "uvicorn", "backend.app:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
