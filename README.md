# Sistema de Asignación de Ayudantes

MVP para selección y asignación óptima de ayudantes en la Facultad de Ingeniería,
Universidad de los Andes. Cruza las planillas de Google Sheets, aplica filtros
determinísticos, entrena un modelo **XGBoost** y optimiza la asignación con
Programación Lineal Entera (ILP).

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│  Navegador  http://localhost                                 │
│  Frontend (nginx) — HTML/CSS/JS vanilla, filtros en memoria  │
└──────────────────────────┬──────────────────────────────────┘
                           │  /api/*  (proxy nginx)
┌──────────────────────────▼──────────────────────────────────┐
│  Backend  FastAPI :8000                                      │
│  POST /pipeline/run    →  cruce de datos + score (SSE)       │
│  POST /pipeline/score  →  XGBoost + ILP + KPIs (SSE)         │
│  POST /pipeline/export →  descarga Excel                     │
│  GET  /health · /sheets/check                                │
└──────────────────────────┬──────────────────────────────────┘
                           │  gspread / Drive API
┌──────────────────────────▼──────────────────────────────────┐
│  Google Sheets                                              │
│  Malla · Promedios · NRC · Inscritos · Postulaciones        │
│  (+ Plan de Estudios, opcional)                             │
└─────────────────────────────────────────────────────────────┘
```

El pipeline se ejecuta en dos fases vía **Server-Sent Events (SSE)**: primero
`/pipeline/run` cruza los datos y emite los candidatos con un score
determinístico; luego `/pipeline/score` aplica el modelo de IA + ILP sobre los
candidatos que el profesor tiene filtrados en pantalla.

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
| `SPREADSHEET_URL_PLAN_ESTUDIOS` | (opcional) URL del *Plan de Estudios / Malla Nueva* |
| `APP_ROLE` | Rol de la instancia: `admin` (facultad) o `profesor` |

> **Compartir las planillas:** cada Spreadsheet debe estar compartido con el
> email de la Service Account (`...@...iam.gserviceaccount.com`) como Lector o
> Editor. Si alguna planilla es un archivo `.xlsx` subido a Drive (no un Google
> Sheet nativo), además debe estar habilitada la **Google Drive API** en el
> proyecto de Google Cloud.

### 2 — Construir y levantar

```bash
docker-compose up --build
```

La primera vez tarda un poco mientras construye la imagen (Python sobre Alpine).

### 3 — Abrir la app

```
http://localhost
```

Pulsa **"Consultar"** para leer las planillas en vivo y ejecutar el pipeline.

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
# Copia y completa el .env (ENVIRONMENT=LOCAL lee de este archivo)
cp .env.example .env

# Inicia el servidor
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend

Abre `frontend/index.html` con [Live Server](https://marketplace.visualstudio.com/items?itemName=ritwickdey.LiveServer)
(VS Code) o cualquier servidor estático. El JS detecta automáticamente el puerto
y apunta las llamadas a `http://localhost:8000`.

---

## Fuentes de datos (Google Sheets)

| Hoja | Descripción | Columnas usadas |
|---|---|---|
| Cumplimiento de Malla Pregrado | Historial académico por alumno | RUT, MATERIA, CURSO, NOTA, ORIGEN |
| Reporte Alumnos con Promedio | PGA y datos del alumno | RUT, PROMEDIO GENERAL ACUMULADO, nombre, carrera |
| Listado de NRC por Periodo | Secciones del semestre actual | NRC, MATERIA, CURSO, TITULO, TIPO, STATUS, INSCRITOS, LUNES–SABADO, PROFESOR, PERIODO |
| Ramos Inscritos por Periodo | Carga académica actual del alumno | RUT, NRC, MATERIA, CURSO |
| Postulaciones a ayudantías | Historial de postulaciones y evaluaciones | RUT, Periodo, Estado, Evaluación, Materia, Curso, Tipo de ayudante, Profesor |
| Plan de Estudios *(opcional)* | Catálogo de cursos y requisitos | MATERIA, CURSO, TITULO, Requisitos |

**Reglas de negocio clave:**

- Una asignatura se identifica por `(MATERIA, CURSO)`. Aprobarla (NOTA ≥ mínima y
  `ORIGEN ∈ {H, OE, TR}`) habilita a ser ayudante de cualquier NRC de ese ramo.
- En Postulaciones, solo el estado **`Aceptado`** cuenta como ayudantía realizada.
  "Postulante actual" = postuló este período con estado `Aceptado` o `Pendiente`.
- Cada sección necesita `ceil(INSCRITOS / 25)` ayudantes (mínimo 1, sin tope).
- Cada alumno puede ser ayudante en **máximo 3** cosas a la vez (lo garantiza el ILP).

> Las planillas pueden ser Google Sheets nativos **o** archivos `.xlsx` subidos a
> Drive (el backend detecta el tipo y usa la API correcta automáticamente).

---

## Filtros disponibles en la interfaz

| Filtro | Descripción |
|---|---|
| **Escuela** | Filtra por prefijo de la materia (escuela/carrera) |
| **Curso** | Filtra por materia y código (ej. MAT 1200) |
| **Nota mínima del ramo** | Nota mínima que el alumno obtuvo en ese curso |
| **Promedio general mínimo (PGA)** | Filtro por rendimiento global del alumno |
| **Día disponible / Ventana horaria** | Muestra solo alumnos disponibles en ese horario |
| **Ex-ayudante** | Filtra por experiencia previa como ayudante |

Los filtros operan **en memoria** sobre los datos ya cargados — sin re-fetch.
Al filtrar por un curso concreto, cada alumno aparece **una sola vez** (no se
repite por cada sección/NRC de ese ramo).

---

## Roles (admin / profesor)

El rol se fija al levantar la imagen con la variable de entorno `APP_ROLE` y el
backend lo expone en `GET /config`; el frontend lo lee y adapta la interfaz.

| | **admin** (facultad) | **profesor** |
|---|---|---|
| RUT del alumno | Visible | Oculto |
| Nota del ramo | Siempre | Solo del ramo filtrado |
| Funciones (IA, KPIs, export) | Todas | Todas |
| Selección de profesor | — | Elige su nombre en pantalla |

```bash
# Levantar como profesor
APP_ROLE=profesor docker-compose up --build
```

> **Nota de seguridad:** el ocultamiento es a nivel de interfaz (cosmético). Los
> datos completos aún viajan en el payload de la API; para una separación
> estricta habría que filtrarlos también en el backend.

---

## Endpoints de la API

| Método | Ruta | Descripción |
|---|---|---|
| `GET`  | `/health` | Estado del servidor y configuración |
| `GET`  | `/sheets/check` | Verifica la lectura de las planillas |
| `POST` | `/pipeline/run` | Cruce de datos + score determinístico (SSE) |
| `POST` | `/pipeline/score` | XGBoost + ILP + KPIs sobre los candidatos filtrados (SSE) |
| `GET`  | `/pipeline/last` | Último resultado sin re-ejecutar |
| `POST` | `/pipeline/export` | Descarga los candidatos filtrados como `.xlsx` |
| `GET`  | `/kpi/metadata` | Definición formal de los 3 KPIs |
| `GET`  | `/weights/presets` | Presets de pesos para el score determinístico |

### Body de `POST /pipeline/run`

```json
{
  "nota_minima": 5.0,
  "max_ayudantias": 2,
  "weight_preset": "balanced"
}
```

La documentación interactiva (Swagger) está en:

```
http://localhost:8000/docs        (acceso directo al backend)
http://localhost/api/docs         (a través del proxy nginx)
```

---

## Pipeline de IA

```
Google Sheets
    │
    ▼ cruce por RUT y por (MATERIA, CURSO)
Candidatos elegibles (filtros determinísticos)
    ├─ Aprobó el ramo con nota ≥ NOTA_MINIMA
    ├─ No está cursando el ramo actualmente
    └─ Sin conflicto de horario
    │
    ▼ XGBoost (gradient boosting de árboles)
Score de idoneidad por (alumno × sección)
    │  Features: NOTA_RAMO, PGA, CARGA_ACTUAL,
    │            N_VECES_AYUDANTE, AVANCE_MALLA,
    │            PROM_EVAL_PREVIA, POSTULANTE_ACTUAL
    │  Label: evaluación de desempeño ≥ umbral
    ▼ ILP (PuLP)
Asignación óptima (maximiza score total)
    ├─ Cada sección recibe los ayudantes que necesita
    └─ Cada alumno cubre ≤ 3 secciones
    │
    ▼
KPI 1: F1-Score del modelo (baseline 0.55, meta 0.80)
KPI 2: Calidad promedio de evaluaciones (meta ≥ 5.0 / 7.0)
KPI 3: Tasa de cobertura de restricciones (meta ≥ 0.90)
```

**¿Por qué XGBoost?** Entrena árboles de forma secuencial donde cada uno corrige
el error residual del conjunto anterior (boosting), optimizando `logloss` con
regularización. Suele superar a un Random Forest en datos tabulares, maneja
valores faltantes de forma nativa y usa `scale_pos_weight` para el desbalance de
clases (pocos ayudantes "buenos" frente al total).

---

## Estructura del proyecto

```
.
├── backend/
│   ├── app.py                     # API FastAPI (endpoints, SSE)
│   ├── config.py                  # Carga de variables de entorno
│   ├── pipeline/                  # Paquete del pipeline (una clase por módulo)
│   │   ├── constants.py           # Umbrales, pesos y vocabulario
│   │   ├── column_normalizer.py   # Limpieza de columnas / RUT
│   │   ├── schedule_analyzer.py   # Horarios y conflictos
│   │   ├── experience_analyzer.py # Experiencia previa como ayudante
│   │   ├── curriculum_catalog.py  # Avance de malla / cursos nuevos
│   │   ├── candidate_builder.py   # Construcción de candidatos elegibles
│   │   ├── scoring.py             # Score determinístico (sin ML)
│   │   ├── scorer.py              # Modelo XGBoost
│   │   ├── optimizer.py           # Optimización ILP
│   │   ├── kpi_reporter.py        # Cálculo de los KPIs
│   │   └── runner.py              # Orquestación del pipeline
│   └── skills/
│       ├── google_auth.py         # Autenticación Service Account
│       └── google_spreadsheet.py  # Lectura Sheets / Drive API
├── frontend/
│   ├── index.html                 # Interfaz de usuario
│   ├── styles.css                 # Estilos
│   └── app.js                     # Lógica frontend + filtros
├── Dockerfile                     # Imagen del backend (Python · Alpine)
├── docker-compose.yml             # Backend + frontend nginx
├── nginx.conf                     # Proxy /api/ → backend
├── requirements.txt
└── .env.example                   # Plantilla de configuración
```
