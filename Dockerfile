FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema (libgomp1 es necesaria para XGBoost)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copiar el codigo del proyecto
COPY backend/  ./backend/

# Puerto expuesto por FastAPI/uvicorn
EXPOSE 8000

# Variables de entorno de produccion
ENV ENVIRONMENT=PROD
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
ENV APP_ROLE=admin

# Comando de inicio
CMD ["python", "-m", "uvicorn", "backend.app:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
