# Sistema de Asignación de Ayudantes

MVP para selección y asignación óptima de ayudantes en la Facultad de Ingeniería,
Universidad de los Andes. Cruza 5 fuentes de datos desde Google Sheets, aplica
filtros determinísticos, entrena un modelo Random Forest y optimiza la asignación
con Programación Lineal Entera (ILP).

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│  Navegador  http://localhost                                 │
│  Frontend (nginx) — HTML/CSS/JS vanilla, filtros en memoria │
└──────────────────────────┬──────────────────────────────────┘
                           │  /api/*  (proxy nginx)
┌──────────────────────────▼──────────────────────────────────┐
│  Backend  FastAPI :8000                                      │
│  POST /pipeline/run  →  cruce de datos + RF + ILP + KPIs    │
│  GET  /pipeline/export  →  descarga Excel                   │
│  GET  /health                                               │
└──────────────────────────┬──────────────────────────────────┘
                           │  gspread / Drive API
┌──────────────────────────▼──────────────────────────────────┐
│  Google Sheets (5 planillas)                                │
│  Malla · Promedios · NRC · Inscritos · Postulaciones        │
└─────────────────────────────────────────────────────────────┘
```

---

## Levantar con Docker (recomendado)

### Requisitos previos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado y corriendo.

### 1 — Configurar credenciales

Copia el archivo de ejemplo y completa las variables:

```bash
cp .env.example .env
```

Edita `.env` con:

| Variable | Descripción |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | JSON completo de la Service Account (como string) |
| `SPREADSHEET_URL_MALLA` | URL de la hoja *Cumplimiento de Malla Pregrado* |
| `SPREADSHEET_URL_PROMEDIOS` | URL de la hoja *Reporte Alumnos con Promedio* |
| `SPREADSHEET_URL_NRC` | URL de la hoja *Listado de NRC por Periodo* |
| `SPREADSHEET_URL_INSCRITOS` | URL de la hoja *Ramos Inscritos por Periodo* |
| `SPREADSHEET_URL_POSTULACIONES` | URL de la hoja *Postulaciones a ayudantías* |

> **Nota:** Cada Spreadsheet debe estar compartido con el email de la Service Account
> como Editor (`spreadsheet-ia-aplicada@...iam.gserviceaccount.com`).

### 2 — Construir y levantar

```bash
docker-compose up --build
```

La primera vez tarda ~2 min mientras construye la imagen Python.

### 3 — Abrir la app

```
http://localhost
```

- Botón **"Cargar datos"** en modo **Demo** → datos sintéticos, sin necesidad de Google Sheets.
- Botón **"Cargar datos"** en modo **Google Sheets** → lee las 5 planillas en vivo.

### Detener

```bash
docker-compose down
```

---

## Levantar en desarrollo local (sin Docker)

### Requisitos previos

- Python 3.11+
- `pip install -r requirements.txt`

### Backend

```bash
# Copia y completa el .env
cp .env.example .env

# Inicia el servidor
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend

Abre `frontend/index.html` con [Live Server](https://marketplace.visualstudio.com/items?itemName=ritwickdey.LiveServer)
(VS Code) o cualquier servidor estático. El JS detecta automáticamente el puerto
y apunta las llamadas a `http://localhost:8000`.

### Demo rápido (solo línea de comandos)

```bash
python run_demo.py          # genera datos sintéticos y ejecuta el pipeline
python run_demo.py --export # además exporta resultados a output_resultados.xlsx
```

---

## Fuentes de datos (Google Sheets)

| Hoja | Descripción | Columnas clave |
|---|---|---|
| Cumplimiento de Malla Pregrado | Historial académico por alumno | RUT, MATERIA, CURSO, NOTA, ORIGEN |
| Reporte Alumnos con Promedio | PGA / PUA por alumno | RUT, PROMEDIO GENERAL ACUMULADO, PROMEDIO ULTIMO AÑO |
| Listado de NRC por Periodo | Secciones del semestre actual | NRC, MATERIA, CURSO, LUNES-SABADO (horario) |
| Ramos Inscritos por Periodo | Carga académica actual del alumno | RUT, NRC, MATERIA, CURSO |
| Postulaciones a ayudantías | Historial de postulaciones y evaluaciones | RUT, Periodo, Estado, Evaluación |

> Los archivos pueden ser Google Sheets nativos **o** archivos `.xlsx` subidos a Drive
> (el backend detecta el tipo automáticamente y usa la API correcta).

---

## Filtros disponibles en la interfaz

| Filtro | Descripción |
|---|---|
| **Curso** | Filtra por materia y código (ej. MAT 1200) |
| **Nota mínima del ramo** | Nota mínima que el alumno obtuvo en ese curso |
| **Promedio general mínimo (PGA)** | Filtro por rendimiento global del alumno |
| **Día disponible** | Muestra solo alumnos sin clases ese día |
| **Ventana horaria** | Filtra por disponibilidad en un rango hora inicio–fin |

Los filtros operan **en memoria** sobre los datos ya cargados — sin re-fetch.

---

## Endpoints de la API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Estado del servidor y configuración |
| `POST` | `/pipeline/run` | Ejecuta el pipeline completo |
| `GET` | `/pipeline/last` | Último resultado sin re-ejecutar |
| `GET` | `/pipeline/export` | Descarga resultados como `.xlsx` |
| `GET` | `/kpi/metadata` | Definición formal de los 3 KPIs |
| `GET` | `/sheets/check` | Verifica conexión con Google Sheets |

### Body de `POST /pipeline/run`

```json
{
  "nota_minima": 5.0,
  "max_ayudantias": 2,
  "usar_demo": false
}
```

La documentación interactiva (Swagger) está disponible en:

```
http://localhost:8000/docs        (acceso directo al backend)
http://localhost/api/docs         (a través del proxy nginx)
```

---

## Pipeline de IA

```
Google Sheets
    │
    ▼ cruce por RUT
Candidatos elegibles (filtros determinísticos)
    ├─ Aprobó el ramo con nota ≥ NOTA_MINIMA
    ├─ No está cursando el ramo actualmente
    └─ Sin conflicto de horario
    │
    ▼ Random Forest
Score de idoneidad por (alumno × sección)
    │  Features: nota ramo, PGA, PUA, carga actual,
    │            n° veces ayudante, prom. evaluación previa,
    │            es postulante actual
    ▼ ILP (PuLP)
Asignación óptima (maximiza score total)
    ├─ Cada sección recibe los ayudantes que necesita
    └─ Cada alumno cubre ≤ MAX_AYUDANTIAS secciones
    │
    ▼
KPI 1: F1-Score del modelo (baseline 0.55, meta 0.80)
KPI 2: Calidad promedio evaluaciones (meta ≥ 5.0 / 7.0)
KPI 3: Tasa de cobertura de restricciones (meta ≥ 0.90)
```

---

## Estructura del proyecto

```
.
├── backend/
│   ├── app.py                  # API FastAPI (endpoints)
│   ├── pipeline.py             # Pipeline ML + ILP + KPIs
│   ├── config.py               # Carga de variables de entorno
│   └── skills/
│       ├── google_auth.py      # Autenticación Service Account
│       └── google_spreadsheet.py  # Lectura Sheets / Drive API
├── frontend/
│   ├── index.html              # Interfaz de usuario
│   ├── styles.css              # Estilos
│   └── app.js                  # Lógica frontend + filtros
├── run_demo.py                 # Demo standalone (sin Google Sheets)
├── Dockerfile                  # Imagen del backend
├── docker-compose.yml          # Backend + frontend nginx
├── nginx.conf                  # Proxy /api/ → backend
├── requirements.txt
└── .env.example                # Plantilla de configuración
```
