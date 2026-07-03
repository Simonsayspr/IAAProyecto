"""
API REST — MVP Asignacion de Ayudantes
Framework: FastAPI
"""

import io
import json
import re
import sys
import os
import traceback
from typing import Optional

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.config import global_vars
from backend.pipeline import (
    run_pipeline_deterministic,
    run_pipeline_ai,
    KPI_METADATA,
    WEIGHT_PRESETS,
    CurriculumCatalogProcessor,
    EligibleCandidateBuilder,
)
from backend.skills.google_auth import GoogleAuth
from backend.skills.google_spreadsheet import GoogleSpreadsheetSkill


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="MVP Gestion de Ayudantes",
    description="API para seleccion y asignacion optima de ayudantes.",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_cache: dict = {}

DIAS = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_credentials():
    sa = global_vars.get("service_account", {})
    if not sa:
        raise HTTPException(
            status_code=503,
            detail="Service Account no configurada. Define GOOGLE_SERVICE_ACCOUNT_JSON en .env",
        )
    return GoogleAuth(sa, global_vars["url_scope"]).get_credentials()


def _read_one_sheet(creds, url_key: str, sheet_name: str) -> pd.DataFrame:
    url = global_vars.get(url_key, "")
    if not url:
        raise HTTPException(
            status_code=503,
            detail=f"URL no configurada para '{url_key}'. Defínela en .env",
        )
    return GoogleSpreadsheetSkill(creds, url).get_dataframe(sheet_name)


def _read_one_sheet_optional(creds, url_key: str, sheet_name: str) -> pd.DataFrame:
    url = global_vars.get(url_key, "")
    if not url:
        return pd.DataFrame()
    try:
        return GoogleSpreadsheetSkill(creds, url).get_dataframe(sheet_name)
    except Exception:
        return pd.DataFrame()


def _read_sheets() -> dict[str, pd.DataFrame]:
    creds = _get_credentials()
    sheets_cfg = {
        "malla":         ("spreadsheet_url_malla",         "RA311 - Cumplimiento de Malla P"),
        "promedios":     ("spreadsheet_url_promedios",     "UG305 - Reporte Alumnos con Pro"),
        "nrc":           ("spreadsheet_url_nrc",           "UG201 - Listado de NRC por Peri"),
        "inscritos":     ("spreadsheet_url_inscritos",     "UG307 - Ramos Inscritos por Per"),
        "postulaciones": ("spreadsheet_url_postulaciones", "Registros"),
    }
    dfs = {}
    for key, (url_key, sheet_name) in sheets_cfg.items():
        url = global_vars.get(url_key, "")
        if not url:
            if key == "postulaciones":
                dfs[key] = pd.DataFrame()
                continue
            raise HTTPException(
                status_code=503,
                detail=f"URL no configurada para '{key}'. Define {url_key.upper()} en .env",
            )
        skill = GoogleSpreadsheetSkill(creds, url)
        dfs[key] = skill.get_dataframe(sheet_name)
    return dfs


def _df_to_records(df: pd.DataFrame) -> list:
    return df.replace({np.nan: None}).to_dict(orient="records")


def _latest_application_period(postulaciones_df: Optional[pd.DataFrame]) -> Optional[str]:
    """
    Período de postulación más reciente desde la hoja de Postulaciones.

    Busca la columna 'Periodo' sin importar mayúsculas/acentos. Representa el
    período en que el alumno postuló (no el semestre vigente).
    """
    if postulaciones_df is None or postulaciones_df.empty:
        return None
    period_column = next(
        (c for c in postulaciones_df.columns
         if str(c).strip().upper().replace("Í", "I") == "PERIODO"),
        None,
    )
    if not period_column:
        return None
    periods = postulaciones_df[period_column].dropna().astype(str).unique()
    return sorted(periods)[-1] if len(periods) else None


def _compute_student_info(
    inscritos_df: pd.DataFrame,
    nrc_df: pd.DataFrame,
    postulaciones_df: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Retorna un dict {RUT: {email, ocupado, ayudantias_previas}} con:
    - ocupado: {DIA: ["HH:MM -HH:MM", ...]} — franjas en que el alumno tiene clase
    - ayudantias_previas: lista de dict {periodo, materia, curso, asignatura, evaluacion, tipo}
    """

    # ── Horario por NRC ──────────────────────────────────────────────────────
    # Solo TIPO ∈ {CLAS, LAB, AYUD}; acumula slots si el NRC tiene varias filas
    TIPOS_CLASE = {"CLAS", "LAB", "AYUD"}
    nrc_schedule: dict[str, dict] = {}
    if not nrc_df.empty:
        ndf = nrc_df.copy()
        ndf.columns = [re.sub(r"\s+", " ", str(c).strip().upper()) for c in ndf.columns]
        for _, row in ndf.iterrows():
            tipo = str(row.get("TIPO", "")).strip().upper()
            if tipo and tipo not in TIPOS_CLASE:
                continue
            nrc_key = str(row.get("NRC", "")).strip()
            if not nrc_key:
                continue
            if nrc_key not in nrc_schedule:
                nrc_schedule[nrc_key] = {}
            for dia in DIAS:
                val = str(row.get(dia, "")).strip()
                if val and val.lower() not in ("nan", "none", ""):
                    nrc_schedule[nrc_key].setdefault(dia, []).append(val)

    # ── Schedule por alumno (de ramos inscritos actualmente) ─────────────────
    student_info: dict[str, dict] = {}

    if not inscritos_df.empty:
        idf = inscritos_df.copy()
        idf.columns = [re.sub(r"\s+", " ", str(c).strip().upper()) for c in idf.columns]
        # Acumular NRCs únicos por alumno para calcular n_ramos_inscritos
        _nrcs_por_rut: dict[str, set] = {}
        for _, row in idf.iterrows():
            rut_raw = str(row.get("RUT", "")).strip().upper()
            if rut_raw.endswith(".0"): rut_raw = rut_raw[:-2]
            rut = re.sub(r"[^0-9K]", "", rut_raw)
            nrc_key = str(row.get("NRC", "")).strip()
            if not rut or not nrc_key:
                continue
            if rut not in student_info:
                student_info[rut] = {
                    "email": "",
                    "ocupado": {},
                    "ayudantias_previas": [],
                }
            _nrcs_por_rut.setdefault(rut, set()).add(nrc_key)
            for dia, slots in nrc_schedule.get(nrc_key, {}).items():
                student_info[rut]["ocupado"].setdefault(dia, []).extend(slots)
        # Guardar conteo de ramos inscritos directamente desde la misma fuente
        for rut_key, nrcs in _nrcs_por_rut.items():
            student_info[rut_key]["n_ramos_inscritos"] = len(nrcs)

    # ── Historial de ayudantías + postulaciones actuales ────────────────────
    ACEPTADO = {"aceptado", "aprobado"}
    # Rastrear (rut, periodo, materia, curso) ya agregados al historial
    _seen_historial: dict[str, set] = {}
    if postulaciones_df is not None and not postulaciones_df.empty:
        pdf = postulaciones_df.copy()
        pdf.columns = [str(c).strip().upper() for c in pdf.columns]
        matched_experiencia = 0
        for _, row in pdf.iterrows():
            rut_raw = str(row.get("RUT", "")).strip().upper()
            if rut_raw.endswith(".0"): rut_raw = rut_raw[:-2]
            rut = re.sub(r"[^0-9K]", "", rut_raw)
            estado = str(row.get("ESTADO", "")).strip()
            estado_lower = estado.lower()
            if not rut:
                continue
            if rut not in student_info:
                student_info[rut] = {
                    "email": "",
                    "ocupado": {},
                    "ayudantias_previas": [],
                    "postulaciones_actuales": [],
                }
            if "postulaciones_actuales" not in student_info[rut]:
                student_info[rut]["postulaciones_actuales"] = []
            correo = str(row.get("CORREO", "")).strip()
            if correo and correo.lower() not in ("nan", "none") and "@" in correo:
                student_info[rut]["email"] = correo

            raw_prof = str(row.get("PROFESOR", "")).strip()
            profesor = raw_prof if raw_prof.lower() not in ("nan", "none", "") else ""
            tipo_ay = str(row.get("TIPO DE AYUDANTE", "")).strip()

            if estado_lower in ACEPTADO:
                periodo_h  = str(row.get("PERIODO", ""))
                materia_h  = str(row.get("MATERIA", ""))
                curso_h    = str(row.get("CURSO", ""))
                hist_key   = f"{periodo_h}|{materia_h}|{curso_h}"
                if hist_key not in _seen_historial.setdefault(rut, set()):
                    _seen_historial[rut].add(hist_key)
                    matched_experiencia += 1
                    eval_raw = str(row.get("EVALUACIÓN", row.get("EVALUACION", ""))).strip()
                    student_info[rut]["ayudantias_previas"].append({
                        "periodo":    periodo_h,
                        "materia":    materia_h,
                        "curso":      curso_h,
                        "asignatura": str(row.get("ASIGNATURA", "")),
                        "evaluacion": eval_raw if eval_raw not in ("", "nan", "None") else None,
                        "tipo":       tipo_ay,
                        "profesor":   profesor,
                    })

            # Postulaciones del periodo actual
            periodo = str(row.get("PERIODO", "")).strip()
            student_info[rut]["postulaciones_actuales"].append({
                "periodo":    periodo,
                "materia":    str(row.get("MATERIA", "")),
                "curso":      str(row.get("CURSO", "")),
                "asignatura": str(row.get("ASIGNATURA", "")),
                "estado":     estado,
                "tipo":       tipo_ay,
                "profesor":   profesor,
            })

    return student_info


# ── Modelos ────────────────────────────────────────────────────────────────────

class PipelineRequest(BaseModel):
    nota_minima: float = 5.0
    max_ayudantias: int = 2
    weight_preset: Optional[str] = None
    custom_weights: Optional[dict] = None


class ScoreRequest(BaseModel):
    candidates: list[dict]  # candidatos determinísticos desde el frontend


class ExportRequest(BaseModel):
    candidates: list[dict]


class HealthResponse(BaseModel):
    status: str
    version: str
    service_account_configurada: bool
    urls_configuradas: dict


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["Estado"])
def health():
    sa_ok = bool(global_vars.get("service_account"))
    urls = {
        k: bool(global_vars.get(f"spreadsheet_url_{k}"))
        for k in ["malla", "promedios", "nrc", "inscritos", "postulaciones", "plan_estudios"]
    }
    return HealthResponse(
        status="ok",
        version="1.1.0",
        service_account_configurada=sa_ok,
        urls_configuradas=urls,
    )


@app.get("/config", tags=["Estado"])
def app_config():
    """Configuración pública para el frontend (rol de la instancia)."""
    return {"app_role": global_vars.get("app_role", "admin")}


@app.get("/sheets/check", tags=["Google Sheets"])
def check_sheets():
    try:
        dfs = _read_sheets()
        return {"status": "ok", "hojas": {k: len(v) for k, v in dfs.items()}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/pipeline/run", tags=["Pipeline"])
def run(req: PipelineRequest):
    """
    Ejecuta el pipeline completo como Server-Sent Events.
    El cliente recibe eventos {type: progress|result|error} a medida que
    cada etapa termina, evitando timeouts en pipelines lentos.
    """
    def generate():
        try:
            student_builder = EligibleCandidateBuilder(minimum_grade=req.nota_minima)

            # ── Fase 1: datos de estudiantes (rápido) ─────────────────────
            yield _sse({"type": "progress", "step": 1, "msg": "Cargando datos de estudiantes…"})

            creds = _get_credentials()
            promedios     = _read_one_sheet(creds, "spreadsheet_url_promedios", "UG305 - Reporte Alumnos con Pro")
            postulaciones = _read_one_sheet_optional(creds, "spreadsheet_url_postulaciones", "Registros")

            post_df = postulaciones if not postulaciones.empty else None

            # Período de postulación más reciente (aproximación para la lista
            # inicial de alumnos; el período definitivo se toma de NRC en la fase 2)
            periodo_actual = _latest_application_period(post_df)

            # Emitir lista de estudiantes inmediatamente (sin esperar el cruce)
            students_df = student_builder.get_students_summary(promedios, post_df, periodo_actual)
            yield _sse({
                "type":     "students_ready",
                "students": _df_to_records(students_df),
                "n_students": len(students_df),
            })

            # ── Fase 2: cargar planillas restantes (más lentas) ────────────
            yield _sse({"type": "progress", "step": 2, "msg": "Cargando malla y ramos del período…"})

            malla     = _read_one_sheet(creds, "spreadsheet_url_malla",     "RA311 - Cumplimiento de Malla P")
            nrc       = _read_one_sheet(creds, "spreadsheet_url_nrc",       "UG201 - Listado de NRC por Peri")
            inscritos = _read_one_sheet(creds, "spreadsheet_url_inscritos", "UG307 - Ramos Inscritos por Per")
            # Período actual = semestre vigente según NRC (cursos impartidos ahora)
            if "PERIODO" in nrc.columns and not nrc.empty:
                periodo_actual = str(nrc["PERIODO"].dropna().astype(str).mode().iloc[0])

            # ── Cargar catálogo de plan de estudios (opcional) ─────────────
            catalog_df = None
            prerequisites_map = None
            try:
                cat_raw = _read_one_sheet_optional(creds, "spreadsheet_url_plan_estudios", "Periodo")
                if not cat_raw.empty:
                    catalog_processor = CurriculumCatalogProcessor()
                    catalog_df = catalog_processor.load_course_catalog(cat_raw)
                    prereq_raw = _read_one_sheet_optional(creds, "spreadsheet_url_plan_estudios", "Nueva Malla - Requisitos")
                    if not prereq_raw.empty:
                        prerequisites_map = catalog_processor.load_prerequisites(prereq_raw)
            except Exception as e:
                print(f"  [Info] Catálogo de plan de estudios no disponible: {e}")

            # ── Fase 3: cruce determinístico ───────────────────────────────
            yield _sse({"type": "progress", "step": 2, "msg": "Cruzando datos y filtrando candidatos elegibles…"})

            results = run_pipeline_deterministic(
                malla_df=malla,
                promedios_df=promedios,
                inscritos_df=inscritos,
                nrc_df=nrc,
                postulaciones_df=post_df,
                periodo_actual=periodo_actual,
                nota_minima=req.nota_minima,
                catalog_df=catalog_df,
                prerequisites_map=prerequisites_map,
                weight_preset=req.weight_preset,
                custom_weights=req.custom_weights,
            )

            if not results:
                yield _sse({"type": "error", "status": 422, "detail": "Pipeline no produjo resultados."})
                return

            candidates_df = results["candidates"]
            _cache["candidates_df"] = candidates_df.copy()
            _cache["postulaciones"] = post_df

            # ── Fase 4: horarios y historial ───────────────────────────────
            yield _sse({"type": "progress", "step": 3, "msg": "Calculando disponibilidad horaria de candidatos…"})

            # Cursos disponibles para el filtro = todos los activos del NRC
            # (no solo los que tienen candidatos) + los que ya tienen candidatos.
            _nrc_norm = nrc.copy()
            _nrc_norm.columns = [c.strip().upper() for c in _nrc_norm.columns]
            _nrc_cursos_cols = [c for c in ["MATERIA", "CURSO", "TITULO"] if c in _nrc_norm.columns]
            nrc_cursos_df = (
                _nrc_norm[_nrc_cursos_cols]
                .dropna(subset=["MATERIA", "CURSO"])
                .drop_duplicates(subset=["MATERIA", "CURSO"])
            )
            _cand_cursos = candidates_df[
                [c for c in ["MATERIA", "CURSO", "TITULO"] if c in candidates_df.columns]
            ].drop_duplicates(subset=["MATERIA", "CURSO"])
            cursos_df = (
                pd.concat([nrc_cursos_df, _cand_cursos], ignore_index=True)
                .drop_duplicates(subset=["MATERIA", "CURSO"])
                .sort_values(["MATERIA", "CURSO"])
            )
            cursos = _df_to_records(cursos_df)

            # Unión de MATERIAs de NRC + Plan de Estudios para el filtro de escuelas.
            # Incluye todas las escuelas aunque no tengan candidatos en este período.
            _mat_sources = []
            if "MATERIA" in nrc.columns:
                _mat_sources.append(nrc["MATERIA"].dropna().astype(str))
            if catalog_df is not None and "MATERIA" in catalog_df.columns:
                _mat_sources.append(catalog_df["MATERIA"].dropna().astype(str))
            if _mat_sources:
                _all_mat = pd.concat(_mat_sources).str.strip()
                all_materias = sorted(
                    m for m in _all_mat.unique()
                    if m and m.lower() not in ("nan", "none")
                )
            else:
                all_materias = []

            student_info = _compute_student_info(inscritos, nrc, post_df)

            # Compute dashboard stats: count by TA type
            ta_type_counts = {}
            if post_df is not None and not post_df.empty:
                pdf_tmp = post_df.copy()
                pdf_tmp.columns = [str(c).strip() for c in pdf_tmp.columns]
                if "Estado" in pdf_tmp.columns and "Tipo de ayudante" in pdf_tmp.columns:
                    accepted_mask = pdf_tmp["Estado"].str.strip().str.lower().isin(
                        {"aceptado", "aprobado", "activo", "seleccionado"},
                    )
                    accepted = pdf_tmp[accepted_mask]
                    ta_type_counts = (
                        accepted["Tipo de ayudante"]
                        .fillna("Sin tipo").str.strip()
                        .value_counts().to_dict()
                    )

            # Professor info from NRC
            profesor_map = {}
            if not nrc.empty:
                ndf_tmp = nrc.copy()
                ndf_tmp.columns = [re.sub(r"\s+", " ", str(c).strip().upper()) for c in ndf_tmp.columns]
                for _, row in ndf_tmp.drop_duplicates(subset=["NRC"], keep="first").iterrows():
                    nrc_key = str(row.get("NRC", "")).strip()
                    prof = str(row.get("PROFESOR", "")).strip()
                    rut_prof = str(row.get("RUT PROFESOR", "")).strip()
                    if nrc_key and prof and prof not in ("", "nan", "None"):
                        profesor_map[nrc_key] = {
                            "nombre": prof,
                            "rut": rut_prof if rut_prof not in ("", "nan", "None") else "",
                        }

            candidates_payload = {
                "type":            "candidates_ready",
                "candidates":      _df_to_records(candidates_df),
                "student_info":    student_info,
                "cursos":          cursos,
                "all_materias":    all_materias,
                "n_candidatos":    len(candidates_df),
                "n_asignados":     0,
                "n_secciones":     int(candidates_df["NRC"].nunique()) if "NRC" in candidates_df.columns else 0,
                "ta_type_counts":  ta_type_counts,
                "profesor_map":    profesor_map,
            }
            yield _sse(candidates_payload)
            _cache["last"] = {**candidates_payload, "type": "result"}

        except PermissionError as e:
            yield _sse({"type": "error", "status": 503, "detail": str(e)})
        except Exception as e:
            traceback.print_exc()
            yield _sse({"type": "error", "status": 500, "detail": str(e)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/pipeline/score", tags=["Pipeline"])
def score_pipeline(req: ScoreRequest):
    """
    Aplica RF + ILP sobre los candidatos recibidos del frontend.
    Recibe la lista completa de candidatos determinísticos en el body,
    por lo que no depende de caché en servidor.
    """
    if not req.candidates:
        raise HTTPException(status_code=400, detail="No se recibieron candidatos.")

    def generate():
        try:
            yield _sse({"type": "progress", "step": 2, "msg": "Ejecutando modelo de IA (XGBoost)…"})

            candidates_df = pd.DataFrame(req.candidates)
            # Restaurar tipos numéricos que JSON convierte a object
            for col in ["NOTA_RAMO", "PGA", "SCORE",
                        "N_VECES_AYUDANTE", "PROM_EVAL_PREVIA", "CARGA_ACTUAL",
                        "AYUDANTES_REQUERIDOS", "AVANCE_MALLA", "N_ACEPTADAS_ACTUAL"]:
                if col in candidates_df.columns:
                    candidates_df[col] = pd.to_numeric(candidates_df[col], errors="coerce")
            for col in ["EXPERIENCIA_PREVIA", "POSTULANTE_ACTUAL"]:
                if col in candidates_df.columns:
                    candidates_df[col] = candidates_df[col].fillna(False).astype(bool)

            ai = run_pipeline_ai(
                candidates_df,
                _cache.get("postulaciones"),
            )

            if not ai:
                yield _sse({"type": "error", "status": 422, "detail": "El modelo IA no produjo resultados."})
                return

            result_df = ai["result"]
            fi = (
                ai["feature_importance"].to_dict()
                if ai.get("feature_importance") is not None
                else None
            )

            asignados_df = result_df[result_df["ASIGNADO"] == 1]
            yield _sse({
                "type":        "scored",
                "candidates":  _df_to_records(result_df),
                "n_asignados": int(result_df["ASIGNADO"].sum()),
                "n_secciones": int(asignados_df["NRC"].nunique()),
            })
            yield _sse({
                "type":               "kpis_ready",
                "kpi1":               ai["kpi1"],
                "kpi2":               ai["kpi2"],
                "kpi3":               ai["kpi3"],
                "feature_importance": fi,
            })

        except Exception as e:
            traceback.print_exc()
            yield _sse({"type": "error", "status": 500, "detail": str(e)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/pipeline/last", tags=["Pipeline"])
def last_result():
    if not _cache.get("last"):
        raise HTTPException(
            status_code=404,
            detail="No hay resultados previos. Ejecuta POST /pipeline/run primero.",
        )
    return _cache["last"]


COLS_EXPORT = [
    "NOMBRE_COMPLETO", "TITULO", "MATERIA", "CURSO",
    "PGA", "email", "N_VECES_AYUDANTE", "PROM_EVAL_PREVIA",
    "POSTULANTE_ACTUAL", "NOTA_RAMO",
]

COLS_LABELS = {
    "NOMBRE_COMPLETO":  "Nombre",
    "TITULO":           "Nombre del ramo",
    "MATERIA":          "Materia",
    "CURSO":            "NRC",
    "PGA":              "GPA",
    "email":            "Correo miuandes",
    "N_VECES_AYUDANTE": "Veces ayudante (Global)",
    "PROM_EVAL_PREVIA": "Prom. evaluación (ayudante)",
    "POSTULANTE_ACTUAL":"Postulante ser ayudante a este ramo (Actualidad)",
    "NOTA_RAMO":        "Nota final en el ramo",
}


@app.post("/pipeline/export", tags=["Pipeline"])
def export_filtered(req: ExportRequest):
    """
    Recibe la lista de candidatos filtrada por el frontend y genera un Excel.
    Solo incluye los registros que el profesor esta viendo en pantalla.
    """
    if not req.candidates:
        raise HTTPException(status_code=400, detail="No hay candidatos para exportar.")

    df = pd.DataFrame(req.candidates)

    # Convertir booleano a SI/NO
    if "POSTULANTE_ACTUAL" in df.columns:
        df["POSTULANTE_ACTUAL"] = df["POSTULANTE_ACTUAL"].map(
            lambda v: "SI" if v else "NO"
        )

    existing = [c for c in COLS_EXPORT if c in df.columns]
    df_out = df[existing].rename(columns=COLS_LABELS)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_out.to_excel(writer, sheet_name="Candidatos filtrados", index=False)

        # Segunda hoja: solo los asignados por ILP dentro del filtro
        if "ASIGNADO" in df.columns:
            asig = df[df["ASIGNADO"] == 1]
            if not asig.empty:
                asig_existing = [c for c in COLS_EXPORT if c in asig.columns]
                asig[asig_existing].rename(columns=COLS_LABELS).to_excel(
                    writer, sheet_name="Asignados en filtro", index=False
                )

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=ayudantes_filtrados.xlsx"},
    )


@app.get("/kpi/metadata", tags=["KPIs"])
def kpi_metadata():
    return KPI_METADATA


@app.get("/weights/presets", tags=["Configuración"])
def weight_presets():
    """Retorna los presets de pesos disponibles para el scoring."""
    return WEIGHT_PRESETS


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=True)
