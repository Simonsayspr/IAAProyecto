# ── Etapa de construccion ─────────────────────────────────────────────────────
FROM python:3.11-alpine AS builder

WORKDIR /build

# Toolchain para compilar cualquier paquete que no traiga wheel musllinux.
# (numpy/pandas/scikit-learn normalmente instalan wheels precompilados.)
RUN apk add --no-cache build-base gfortran openblas-dev linux-headers

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Imagen final ──────────────────────────────────────────────────────────────
FROM python:3.11-alpine

WORKDIR /app

# Librerias de runtime para numpy / scipy / scikit-learn (BLAS, OpenMP, libstdc++)
RUN apk add --no-cache libstdc++ libgomp openblas

# Copiar dependencias instaladas en la etapa builder
COPY --from=builder /install /usr/local

# Copiar el codigo del proyecto
COPY backend/  ./backend/

# Puerto expuesto por FastAPI/uvicorn
EXPOSE 8000

# Variables de entorno de produccion
ENV ENVIRONMENT=PROD
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
# Rol con el que se levanta la imagen: 'admin' (facultad) o 'profesor'.
# Sobreescribible en docker-compose / docker run con -e APP_ROLE=profesor
ENV APP_ROLE=admin

# Comando de inicio: uvicorn con recarga desactivada en produccion
CMD ["python", "-m", "uvicorn", "backend.app:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
