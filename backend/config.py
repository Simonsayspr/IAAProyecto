"""
Configuracion del backend.

Variables de entorno requeridas (en .env o en el entorno del contenedor):

  GOOGLE_SERVICE_ACCOUNT_JSON  JSON completo de la Service Account (string)
  GOOGLE_SERVICE_ACCOUNT_FILE  Alternativa: ruta al archivo JSON (uso local)

  SPREADSHEET_URL_MALLA        URL hoja Cumplimiento de Malla Pregrado
  SPREADSHEET_URL_PROMEDIOS    URL hoja Reporte Alumnos con Promedio
  SPREADSHEET_URL_NRC          URL hoja Listado de NRC por Periodo
  SPREADSHEET_URL_INSCRITOS    URL hoja Ramos Inscritos por Periodo
  SPREADSHEET_URL_POSTULACIONES URL hoja Postulaciones (opcional)
  SPREADSHEET_URL_PLAN_ESTUDIOS URL hoja Plan de Estudios / Malla Nueva

  NOTA_MINIMA_AYUDANTE         Nota minima para ser candidato (default: 5.0)
  MAX_AYUDANTIAS_ALUMNO        Maximo de cursos como ayudante (default: 2)
"""

import json
import os

from dotenv import dotenv_values


def _load_raw() -> dict:
    """Lee variables desde el entorno (PROD) o desde .env (LOCAL)."""
    env = os.environ.get("ENVIRONMENT", "LOCAL").upper()
    if env == "PROD":
        return dict(os.environ)
    # Buscar .env en el directorio raiz del proyecto
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    return dict(dotenv_values(env_path))


def _load_service_account(raw: dict) -> dict:
    """Carga las credenciales JSON de Service Account."""
    # Opcion 1: JSON como string en variable de entorno
    sa_json = raw.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if sa_json:
        try:
            return json.loads(sa_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"GOOGLE_SERVICE_ACCOUNT_JSON no es JSON valido: {e}")

    # Opcion 2: ruta a archivo JSON
    sa_file = raw.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if sa_file and os.path.exists(sa_file):
        with open(sa_file, encoding="utf-8") as f:
            return json.load(f)

    # Sin credenciales — devuelve dict vacio (demo sin Google Sheets)
    return {}


def build_config() -> dict:
    raw = _load_raw()
    return {
        "service_account": _load_service_account(raw),
        "url_scope": raw.get(
            "GOOGLE_SCOPE", "https://spreadsheets.google.com/feeds"
        ),
        # URLs de cada Spreadsheet
        "spreadsheet_url_malla":        raw.get("SPREADSHEET_URL_MALLA", ""),
        "spreadsheet_url_promedios":    raw.get("SPREADSHEET_URL_PROMEDIOS", ""),
        "spreadsheet_url_nrc":          raw.get("SPREADSHEET_URL_NRC", ""),
        "spreadsheet_url_inscritos":    raw.get("SPREADSHEET_URL_INSCRITOS", ""),
        "spreadsheet_url_postulaciones":raw.get("SPREADSHEET_URL_POSTULACIONES", ""),
        "spreadsheet_url_plan_estudios":raw.get("SPREADSHEET_URL_PLAN_ESTUDIOS", ""),
        # Parametros del pipeline
        "nota_minima_ayudante": float(raw.get("NOTA_MINIMA_AYUDANTE", 5.0)),
        "max_ayudantias_alumno": int(raw.get("MAX_AYUDANTIAS_ALUMNO", 2)),
    }


# Instancia global accesible como config.global_vars["clave"]
global_vars = build_config()
