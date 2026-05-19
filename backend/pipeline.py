"""
Pipeline de Asignacion de Ayudantes
Facultad de Ingenieria, Universidad de los Andes

Modulos:
    1. ColumnNormalizer        — limpieza y estandarizacion de columnas
    2. ScheduleAnalyzer        — analisis de horarios y deteccion de conflictos
    3. ExperienceAnalyzer      — features de experiencia previa como ayudante
    4. EligibleCandidateBuilder— construccion de tabla de candidatos elegibles
    5. RandomForestScorer      — modelo predictivo RF para aptitud de ayudantes
    6. AssignmentOptimizer     — optimizacion ILP de asignacion
    7. KPIReporter             — calculo y reporte de metricas del proyecto
"""

import re
import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pulp
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, f1_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")


# ---------------------------------------------------------------------------
# Constantes globales
# ---------------------------------------------------------------------------

MINIMUM_GRADE_TO_BE_ELIGIBLE = 5.0
GOOD_PERFORMANCE_GRADE_THRESHOLD = 5.5
MAX_SIMULTANEOUS_ASSISTANTSHIPS = 3
STUDENTS_PER_TEACHING_ASSISTANT = 25
RANDOM_STATE = 42

DETERMINISTIC_SCORE_WEIGHTS = {
    "NOTA_RAMO":         0.40,
    "PGA":               0.25,
    "N_VECES_AYUDANTE":  0.10,
    "AVANCE_MALLA":      0.15,
    "CARGA_ACTUAL":      0.05,
    "POSTULANTE_ACTUAL": 0.05,
}

# Presets de pesos configurables desde la UI
WEIGHT_PRESETS = {
    "balanced": {
        "label": "Equilibrado",
        "description": "Balance entre nota, promedio, experiencia y avance curricular",
        "weights": {
            "NOTA_RAMO": 0.40, "PGA": 0.25, "N_VECES_AYUDANTE": 0.10,
            "AVANCE_MALLA": 0.15, "CARGA_ACTUAL": 0.05, "POSTULANTE_ACTUAL": 0.05,
        },
    },
    "academic": {
        "label": "Rendimiento académico",
        "description": "Prioriza nota en el ramo y promedio general",
        "weights": {
            "NOTA_RAMO": 0.55, "PGA": 0.30, "N_VECES_AYUDANTE": 0.05,
            "AVANCE_MALLA": 0.05, "CARGA_ACTUAL": 0.03, "POSTULANTE_ACTUAL": 0.02,
        },
    },
    "experience": {
        "label": "Experiencia previa",
        "description": "Prioriza experiencia como ayudante y postulación activa",
        "weights": {
            "NOTA_RAMO": 0.25, "PGA": 0.15, "N_VECES_AYUDANTE": 0.35,
            "AVANCE_MALLA": 0.10, "CARGA_ACTUAL": 0.05, "POSTULANTE_ACTUAL": 0.10,
        },
    },
    "curriculum": {
        "label": "Avance curricular",
        "description": "Prioriza alumnos avanzados en la malla con baja carga",
        "weights": {
            "NOTA_RAMO": 0.25, "PGA": 0.15, "N_VECES_AYUDANTE": 0.05,
            "AVANCE_MALLA": 0.40, "CARGA_ACTUAL": 0.10, "POSTULANTE_ACTUAL": 0.05,
        },
    },
}

WEEKDAYS = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO"]

# Listas de nombres sinteticos para completar RUTs sin nombre
_PATERNO_SYNTH = [
    "Garcia", "Rodriguez", "Lopez", "Martinez", "Gonzalez", "Perez", "Sanchez",
    "Ramirez", "Torres", "Flores", "Rivera", "Gomez", "Diaz", "Reyes", "Cruz",
    "Morales", "Ortiz", "Herrera", "Medina", "Jimenez", "Munoz", "Rojas", "Vera",
    "Fuentes", "Espinoza", "Bravo", "Navarro", "Molina", "Ramos", "Guerrero",
]
_MATERNO_SYNTH = [
    "Vargas", "Castillo", "Silva", "Salinas", "Vega", "Navarrete", "Aguilar",
    "Pino", "Santos", "Mendoza", "Figueroa", "Rios", "Soto", "Araya", "Caceres",
    "Moya", "Sepulveda", "Contreras", "Lara", "Pena", "Vidal", "Palma", "Tapia",
    "Bustos", "Leiva", "Poblete", "Cornejo", "Carrasco", "Valenzuela", "Olea",
]
_NOMBRES_M_SYNTH = [
    "Carlos", "Luis", "Andres", "Diego", "Felipe", "Sebastian", "Pablo", "Tomas",
    "Juan", "Rodrigo", "Nicolas", "Ignacio", "Matias", "Cristobal", "Fernando",
    "Alejandro", "Roberto", "Ricardo", "Alberto", "Mauricio",
]
_NOMBRES_F_SYNTH = [
    "Maria", "Valentina", "Camila", "Sofia", "Isabella", "Ana", "Laura",
    "Daniela", "Paula", "Natalia", "Constanza", "Javiera", "Francisca",
    "Carolina", "Catalina", "Gabriela", "Claudia", "Patricia", "Sandra", "Gloria",
]


def generate_synthetic_name(rut_str: str) -> str:
    """Genera un nombre completo determinístico a partir del RUT."""
    try:
        n = int(re.sub(r"[^0-9]", "", str(rut_str)))
    except (ValueError, TypeError):
        n = hash(str(rut_str)) % 10000
    pat = _PATERNO_SYNTH[n % len(_PATERNO_SYNTH)]
    mat = _MATERNO_SYNTH[(n * 7) % len(_MATERNO_SYNTH)]
    if (n // len(_PATERNO_SYNTH)) % 2 == 0:
        nom = _NOMBRES_M_SYNTH[(n * 3) % len(_NOMBRES_M_SYNTH)]
    else:
        nom = _NOMBRES_F_SYNTH[(n * 3) % len(_NOMBRES_F_SYNTH)]
    return f"{pat} {mat} {nom}"
CLASSROOM_ACTIVITY_TYPES = {"CLAS", "LAB", "AYUD"}
ACCEPTED_APPLICATION_STATES = {"ACEPTADO", "APROBADO", "ACTIVO", "SELECCIONADO"}

# Columnas de carrera en la hoja de Plan de Estudios
CAREER_COLUMNS = ["ICI", "IOC", "ICE", "ICC", "ICA", "ICQ"]

# Aliases publicos para compatibilidad con app.py y otros modulos
NOTA_MINIMA_ELEGIBLE = MINIMUM_GRADE_TO_BE_ELIGIBLE
NOTA_BUEN_DESEMPENO = GOOD_PERFORMANCE_GRADE_THRESHOLD
MAX_AYUDANTIAS_POR_ALUMNO = MAX_SIMULTANEOUS_ASSISTANTSHIPS
ALUMNOS_POR_AYUDANTE = STUDENTS_PER_TEACHING_ASSISTANT
PESOS_SCORE = DETERMINISTIC_SCORE_WEIGHTS


# ---------------------------------------------------------------------------
# Score deterministico (sin ML)
# ---------------------------------------------------------------------------

def compute_deterministic_score(
    candidates: pd.DataFrame,
    custom_weights: Optional[Dict[str, float]] = None,
) -> pd.Series:
    """Calcula un score ponderado [0,1] normalizando cada variable y aplicando pesos."""
    idx = candidates.index
    weights = custom_weights or DETERMINISTIC_SCORE_WEIGHTS

    grade_normalized = (
        candidates.get("NOTA_RAMO", pd.Series(0.0, index=idx)).fillna(0) / 7.0
    )
    gpa_normalized = (
        candidates.get("PGA", pd.Series(0.0, index=idx)).fillna(0) / 7.0
    )
    experience_normalized = (
        candidates.get("N_VECES_AYUDANTE", pd.Series(0, index=idx))
        .fillna(0).clip(0, 4) / 4.0
    )
    curriculum_progress = (
        candidates.get("AVANCE_MALLA", pd.Series(0.0, index=idx))
        .fillna(0).clip(0, 1)
    )
    # Carga baja = mejor (invertir: 0 ramos=1.0, 8+ ramos=0.0)
    raw_load = candidates.get("CARGA_ACTUAL", pd.Series(0, index=idx)).fillna(0)
    load_availability = (1.0 - raw_load.clip(0, 8) / 8.0)
    is_current_applicant = (
        candidates.get("POSTULANTE_ACTUAL", pd.Series(False, index=idx))
        .fillna(False).astype(float)
    )

    return (
        weights.get("NOTA_RAMO", 0)         * grade_normalized
        + weights.get("PGA", 0)             * gpa_normalized
        + weights.get("N_VECES_AYUDANTE", 0) * experience_normalized
        + weights.get("AVANCE_MALLA", 0)    * curriculum_progress
        + weights.get("CARGA_ACTUAL", 0)    * load_availability
        + weights.get("POSTULANTE_ACTUAL", 0) * is_current_applicant
    ).clip(0.0, 1.0)


# Alias para compatibilidad
_compute_deterministic_score = compute_deterministic_score


# ---------------------------------------------------------------------------
# 1. Normalizacion de columnas
# ---------------------------------------------------------------------------

class ColumnNormalizer:
    """Estandariza nombres de columnas, RUTs y valores numericos en DataFrames."""

    @staticmethod
    def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Convierte nombres de columnas a MAYUSCULAS y limpia el RUT."""
        df = df.copy()
        df.columns = [
            re.sub(r"\s+", " ", str(col).strip().upper())
            for col in df.columns
        ]
        if "RUT" in df.columns:
            df["RUT"] = (
                df["RUT"]
                .astype(str)
                .str.upper()
                .str.replace(r"\.0$", "", regex=True)
                .str.replace(r"[^0-9K]", "", regex=True)
                .str.strip()
            )
        return df

    @staticmethod
    def parse_european_decimal(series: pd.Series) -> pd.Series:
        """Convierte una serie con coma decimal (formato europeo) a float."""
        return (
            series.astype(str)
            .str.replace(",", ".", regex=False)
            .pipe(pd.to_numeric, errors="coerce")
        )

    @staticmethod
    def extract_full_name(df: pd.DataFrame) -> pd.Series:
        """Construye NOMBRE_COMPLETO concatenando columnas de apellido y nombre."""
        def _find_column(dataframe: pd.DataFrame, candidates: List[str]) -> pd.Series:
            for name in candidates:
                if name in dataframe.columns:
                    col_data = dataframe[name].fillna("").astype(str).str.strip()
                    if col_data.ne("").any():
                        return col_data
            return pd.Series("", index=dataframe.index)

        last_name = _find_column(df, [
            "PATERNO", "PRIMER APELLIDO", "APELLIDO PATERNO",
            "APELLIDO 1", "APELLIDOS",
        ])
        second_last_name = _find_column(df, [
            "MATERNO", "SEGUNDO APELLIDO", "APELLIDO MATERNO", "APELLIDO 2",
        ])
        first_name = _find_column(df, [
            "NOMBRE", "NOMBRES", "PRIMER NOMBRE", "NOMBRE ALUMNO",
        ])

        full_name = (last_name + " " + second_last_name + " " + first_name)
        return full_name.str.replace(r"\s+", " ", regex=True).str.strip()

    @staticmethod
    def remove_spanish_accents(text_series: pd.Series) -> pd.Series:
        """Elimina tildes espanolas de una serie de texto en mayusculas."""
        result = text_series
        for accented, plain in zip("AEIOU", "AEIOU"):
            pass  # Already uppercase, handle accented versions
        for accented, plain in zip("ÁÉÍÓÚ", "AEIOU"):
            result = result.str.replace(accented, plain, regex=False)
        return result


# ---------------------------------------------------------------------------
# 2. Analisis de horarios
# ---------------------------------------------------------------------------

class ScheduleAnalyzer:
    """Parsea horarios de cursos y detecta conflictos entre bloques."""

    @staticmethod
    def parse_time_range(time_string: str) -> Optional[Tuple[int, int]]:
        """Convierte '13:30 -15:20' a (810, 920) en minutos desde medianoche."""
        match = re.search(
            r"(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})",
            str(time_string),
        )
        if not match:
            return None
        h1, m1, h2, m2 = map(int, match.groups())
        return h1 * 60 + m1, h2 * 60 + m2

    @staticmethod
    def extract_time_slots_from_row(row: pd.Series) -> List[Tuple[str, int, int]]:
        """Extrae lista de (dia, inicio_min, fin_min) de una fila con columnas de dia."""
        slots: List[Tuple[str, int, int]] = []
        for day in WEEKDAYS:
            raw_value = str(row.get(day, "")).strip()
            if raw_value and raw_value.lower() not in ("nan", "none", ""):
                parsed = ScheduleAnalyzer.parse_time_range(raw_value)
                if parsed:
                    slots.append((day, parsed[0], parsed[1]))
        return slots

    @staticmethod
    def has_schedule_overlap(
        slots_a: List[Tuple[str, int, int]],
        slots_b: List[Tuple[str, int, int]],
    ) -> bool:
        """Retorna True si hay solapamiento temporal entre dos listas de bloques."""
        for day_a, start_a, end_a in slots_a:
            for day_b, start_b, end_b in slots_b:
                if day_a == day_b and start_a < end_b and start_b < end_a:
                    return True
        return False

    @staticmethod
    def build_course_schedule_map(nrc_df: pd.DataFrame) -> Dict[str, List[Tuple]]:
        """Construye {NRC: [(dia, inicio, fin), ...]} para tipos CLAS/LAB/AYUD."""
        normalizer = ColumnNormalizer()
        df = normalizer.normalize_columns(nrc_df)
        schedule_by_nrc: Dict[str, List] = {}

        for _, row in df.iterrows():
            activity_type = str(row.get("TIPO", "")).strip().upper()
            if activity_type and activity_type not in CLASSROOM_ACTIVITY_TYPES:
                continue
            nrc_key = str(row.get("NRC", "")).strip()
            if not nrc_key:
                continue
            time_slots = ScheduleAnalyzer.extract_time_slots_from_row(row)
            schedule_by_nrc.setdefault(nrc_key, []).extend(time_slots)

        return schedule_by_nrc

    @staticmethod
    def build_student_schedule_map(
        enrolled_courses_df: pd.DataFrame,
        nrc_df: pd.DataFrame,
    ) -> Dict[str, List[Tuple]]:
        """Retorna {RUT: [(dia, inicio, fin), ...]} con la ocupacion horaria de cada alumno."""
        normalizer = ColumnNormalizer()
        enrolled = normalizer.normalize_columns(enrolled_courses_df)
        nrc = normalizer.normalize_columns(nrc_df)

        if "TIPO" in nrc.columns:
            nrc = nrc[nrc["TIPO"].str.strip().str.upper().isin(CLASSROOM_ACTIVITY_TYPES)]

        enrolled["NRC"] = enrolled["NRC"].astype(str)
        nrc["NRC"] = nrc["NRC"].astype(str)

        merged = enrolled[["RUT", "NRC"]].merge(nrc, on="NRC", how="left")
        schedule_by_student: Dict[str, List] = {}

        for _, row in merged.iterrows():
            rut = str(row["RUT"])
            time_slots = ScheduleAnalyzer.extract_time_slots_from_row(row)
            schedule_by_student.setdefault(rut, []).extend(time_slots)

        return schedule_by_student


# ---------------------------------------------------------------------------
# 3. Analisis de experiencia previa
# ---------------------------------------------------------------------------

class ExperienceAnalyzer:
    """Extrae features de experiencia como ayudante desde la hoja de postulaciones."""

    COLUMN_ALIASES = {
        "PERIODO":           ["PERIODO"],
        "ESTADO":            ["ESTADO"],
        "EVALUACION":        ["EVALUACION", "EVALUACIÓN"],
        "MOTIVACION":        ["MOTIVACION", "MOTIVACIÓN"],
        "TIPO DE AYUDANTE":  ["TIPO DE AYUDANTE"],
        "ASISTENCIA TALLER": ["ASISTENCIA TALLER"],
        "MATERIA":           ["MATERIA"],
        "CURSO":             ["CURSO"],
        "PROFESOR":          ["PROFESOR"],
    }

    def __init__(self):
        self._normalizer = ColumnNormalizer()

    def build_experience_features(
        self,
        applications_df: Optional[pd.DataFrame],
        current_period: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Genera features de experiencia por RUT desde postulaciones.

        Columnas retornadas:
            EXPERIENCIA_PREVIA  (bool)  — fue aceptado en al menos 1 periodo anterior
            N_VECES_AYUDANTE   (int)   — cuantas veces fue aceptado historicamente
            PROM_EVAL_PREVIA   (float) — promedio de evaluaciones historicas
            ULTIMA_EVAL        (float) — evaluacion mas reciente
            POSTULANTE_ACTUAL  (bool)  — postulo en el periodo actual
            MOTIVACION_SCORE   (float) — motivacion numerica del periodo actual
        """
        if applications_df is None or applications_df.empty:
            return pd.DataFrame(columns=["RUT"])

        df = self._normalizer.normalize_columns(applications_df)
        df = self._map_column_aliases(df)
        df = self._clean_numeric_columns(df)
        df = self._clean_period_column(df)
        df = self._normalize_application_state(df)
        df["FUE_ACEPTADO"] = df["ESTADO_NORM"].isin(ACCEPTED_APPLICATION_STATES)

        historical_df, current_period_df = self._split_by_period(df, current_period)
        experience_summary = self._aggregate_historical_experience(historical_df)
        experience_summary = self._add_current_applicant_flag(
            experience_summary, current_period_df,
        )
        experience_summary = self._add_motivation_score(
            experience_summary, current_period_df,
        )

        experience_summary["EXPERIENCIA_PREVIA"] = (
            experience_summary["EXPERIENCIA_PREVIA"].fillna(False)
        )
        experience_summary["POSTULANTE_ACTUAL"] = (
            experience_summary["POSTULANTE_ACTUAL"].fillna(False)
        )
        experience_summary["N_VECES_AYUDANTE"] = (
            experience_summary.get("N_VECES_AYUDANTE", pd.Series(dtype=float))
            .fillna(0).astype(int)
        )
        experience_summary["RUT"] = experience_summary["RUT"].astype(str)
        return experience_summary

    def _map_column_aliases(self, df: pd.DataFrame) -> pd.DataFrame:
        """Renombra columnas segun alias conocidos (tildes, variaciones)."""
        for canonical_name, variants in self.COLUMN_ALIASES.items():
            for variant in variants:
                if variant in df.columns and canonical_name not in df.columns:
                    df = df.rename(columns={variant: canonical_name})
        return df

    def _clean_numeric_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Convierte EVALUACION y MOTIVACION a numerico."""
        for col in ["EVALUACION", "MOTIVACION"]:
            if col in df.columns:
                df[col] = self._normalizer.parse_european_decimal(df[col])
        return df

    @staticmethod
    def _clean_period_column(df: pd.DataFrame) -> pd.DataFrame:
        """Limpia PERIODO: elimina '.0' de casteo float y espacios."""
        df["PERIODO"] = (
            df["PERIODO"].astype(str)
            .str.replace(r"\.0$", "", regex=True)
            .str.strip()
        )
        return df

    @staticmethod
    def _normalize_application_state(df: pd.DataFrame) -> pd.DataFrame:
        """Normaliza ESTADO: mayusculas y sin tildes para matching confiable."""
        raw_state = (
            df.get("ESTADO", pd.Series(dtype=str))
            .astype(str).str.strip().str.upper()
        )
        for accented_char, plain_char in zip("ÁÉÍÓÚ", "AEIOU"):
            raw_state = raw_state.str.replace(accented_char, plain_char, regex=False)
        df["ESTADO_NORM"] = raw_state
        return df

    @staticmethod
    def _split_by_period(
        df: pd.DataFrame, current_period: Optional[str],
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Separa registros historicos (periodos anteriores o aceptados) de los actuales."""
        if not current_period:
            return df.copy(), pd.DataFrame()

        historical = df[
            (df["PERIODO"] < str(current_period)) | df["FUE_ACEPTADO"]
        ]
        current = df[df["PERIODO"] == str(current_period)]
        return historical, current

    @staticmethod
    def _aggregate_historical_experience(
        historical_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Agrega estadisticas de experiencia por RUT sobre registros aceptados."""
        accepted_records = historical_df[historical_df["FUE_ACEPTADO"]]

        if accepted_records.empty:
            return pd.DataFrame(columns=["RUT", "EXPERIENCIA_PREVIA"])

        summary = (
            accepted_records.sort_values("PERIODO")
            .groupby("RUT")
            .agg(
                N_VECES_AYUDANTE=("PERIODO", "count"),
                PROM_EVAL_PREVIA=("EVALUACION", "mean"),
                ULTIMA_EVAL=("EVALUACION", "last"),
            )
            .reset_index()
        )
        summary["EXPERIENCIA_PREVIA"] = True
        return summary

    @staticmethod
    def _add_current_applicant_flag(
        experience_df: pd.DataFrame,
        current_period_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Agrega POSTULANTE_ACTUAL=True si el alumno postulo en el periodo actual."""
        if current_period_df.empty:
            experience_df["POSTULANTE_ACTUAL"] = False
            return experience_df

        current_applicants = (
            current_period_df[["RUT"]].drop_duplicates().copy()
        )
        current_applicants["POSTULANTE_ACTUAL"] = True
        return experience_df.merge(current_applicants, on="RUT", how="outer")

    @staticmethod
    def _add_motivation_score(
        experience_df: pd.DataFrame,
        current_period_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Agrega MOTIVACION_SCORE promedio del periodo actual."""
        if current_period_df.empty or "MOTIVACION" not in current_period_df.columns:
            return experience_df

        motivation_avg = (
            current_period_df.groupby("RUT")["MOTIVACION"]
            .mean()
            .reset_index()
            .rename(columns={"MOTIVACION": "MOTIVACION_SCORE"})
        )
        return experience_df.merge(motivation_avg, on="RUT", how="left")

    def build_current_application_status(
        self,
        applications_df: Optional[pd.DataFrame],
        current_period: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Retorna estado de postulacion del periodo actual por (RUT, MATERIA, CURSO).

        Columnas: RUT, MATERIA, CURSO, ESTADO_POSTULACION, TIPO_AYUDANTE_POST,
                  PROFESOR_POST, N_ACEPTADAS_ACTUAL
        """
        if applications_df is None or applications_df.empty:
            return pd.DataFrame(
                columns=["RUT", "MATERIA", "CURSO", "ESTADO_POSTULACION"],
            )

        df = self._normalizer.normalize_columns(applications_df)
        df = self._map_column_aliases(df)
        df = self._clean_period_column(df)

        if not current_period:
            return pd.DataFrame(
                columns=["RUT", "MATERIA", "CURSO", "ESTADO_POSTULACION"],
            )

        current = df[df["PERIODO"] == str(current_period)].copy()
        if current.empty:
            return pd.DataFrame(
                columns=["RUT", "MATERIA", "CURSO", "ESTADO_POSTULACION"],
            )

        # Normalizar estado para display
        current["ESTADO_POSTULACION"] = (
            current.get("ESTADO", pd.Series(dtype=str))
            .fillna("").astype(str).str.strip()
        )

        # Tipo de ayudante
        current["TIPO_AYUDANTE_POST"] = (
            current.get("TIPO DE AYUDANTE", pd.Series("", index=current.index))
            .fillna("").astype(str).str.strip()
        )

        # Profesor
        current["PROFESOR_POST"] = (
            current.get("PROFESOR", pd.Series("", index=current.index))
            .fillna("").astype(str).str.strip()
        )

        # Contar cuantas aceptadas tiene cada RUT en el periodo actual
        estado_norm = current["ESTADO_POSTULACION"].str.strip().str.upper()
        for acc, plain in zip("ÁÉÍÓÚ", "AEIOU"):
            estado_norm = estado_norm.str.replace(acc, plain, regex=False)
        current["_ACEPTADA"] = estado_norm.isin(ACCEPTED_APPLICATION_STATES)

        accepted_counts = (
            current[current["_ACEPTADA"]]
            .groupby("RUT")
            .size()
            .reset_index(name="N_ACEPTADAS_ACTUAL")
        )

        result = current[
            ["RUT", "MATERIA", "CURSO", "ESTADO_POSTULACION",
             "TIPO_AYUDANTE_POST", "PROFESOR_POST"]
        ].drop_duplicates(subset=["RUT", "MATERIA", "CURSO"], keep="first")

        result = result.merge(accepted_counts, on="RUT", how="left")
        result["N_ACEPTADAS_ACTUAL"] = (
            result["N_ACEPTADAS_ACTUAL"].fillna(0).astype(int)
        )

        return result.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 3b. Catalogo curricular (Plan de Estudios + Requisitos)
# ---------------------------------------------------------------------------

class CurriculumCatalogProcessor:
    """
    Procesa la hoja de Plan de Estudios para:
    - Calcular avance curricular por alumno (AVANCE_MALLA)
    - Identificar cursos nuevos sin historial de notas
    - Inferir candidatos para cursos nuevos via requisitos relacionados
    """

    def __init__(self):
        self._normalizer = ColumnNormalizer()

    def load_course_catalog(
        self, catalog_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Carga y normaliza el catalogo de cursos (hoja 'Periodo').

        Extrae codigo, materia, curso, titulo, semestre por carrera y requisitos.
        """
        df = self._normalizer.normalize_columns(catalog_df)

        # Construir SEMESTRE_CARRERA: {carrera: semestre} para cada curso
        career_semester = {}
        for career_col in CAREER_COLUMNS:
            if career_col in df.columns:
                # La columna contiene el semestre (e.g. "10i", "9") o vacío
                semester_raw = (
                    df[career_col].fillna("").astype(str).str.strip()
                    .str.replace(r"[^0-9]", "", regex=True)
                )
                career_semester[career_col] = pd.to_numeric(
                    semester_raw, errors="coerce",
                )

        df["SEMESTRE_MAX"] = pd.DataFrame(career_semester).max(axis=1).fillna(0)

        # Normalizar REQUISITOS
        req_col = None
        for candidate in ["REQUISITOS", "REQUISITO"]:
            if candidate in df.columns:
                req_col = candidate
                break
        if req_col:
            df["REQUISITOS_NORM"] = (
                df[req_col].fillna("").astype(str).str.strip()
            )
        else:
            df["REQUISITOS_NORM"] = ""

        keep = ["MATERIA", "CURSO", "TITULO", "SEMESTRE_MAX",
                "REQUISITOS_NORM", "PLAN COMÚN"] + [
            c for c in CAREER_COLUMNS if c in df.columns
        ]
        return df[[c for c in keep if c in df.columns]].copy()

    def load_prerequisites(
        self, prerequisites_df: pd.DataFrame,
    ) -> Dict[str, List[str]]:
        """
        Carga mapa de requisitos: {MATERIA-CURSO: [titulo_requisito, ...]}.

        La hoja 'Nueva Malla - Requisitos' tiene: MATERIA, CURSO, TITULO, Requisitos.
        """
        df = self._normalizer.normalize_columns(prerequisites_df)

        prerequisites_map: Dict[str, List[str]] = {}
        for _, row in df.iterrows():
            key = f"{row.get('MATERIA', '')}-{row.get('CURSO', '')}"
            raw_reqs = str(row.get("REQUISITOS", "")).strip()
            if raw_reqs:
                # Separar por coma, limpiar " (p)" y espacios
                reqs = [
                    re.sub(r"\s*\(p\)\s*", "", r).strip()
                    for r in raw_reqs.split(",")
                    if r.strip()
                ]
                prerequisites_map[key] = reqs
        return prerequisites_map

    def compute_curriculum_progress(
        self,
        curriculum_df: pd.DataFrame,
        catalog_df: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        """
        Calcula AVANCE_MALLA por RUT: proporcion de cursos aprobados
        respecto al total de cursos del catalogo.

        Si no hay catalogo, estima avance por cantidad de ramos aprobados.
        """
        norm = self._normalizer.normalize_columns(curriculum_df)

        # Contar ramos aprobados por alumno
        if "ORIGEN" in norm.columns:
            approved = norm[
                norm["ORIGEN"].str.strip().str.upper().isin({"H", "OE", "TR"})
            ]
        else:
            approved = norm

        courses_per_student = (
            approved.groupby("RUT")[["MATERIA", "CURSO"]]
            .apply(lambda g: len(g.drop_duplicates()))
            .reset_index(name="N_APROBADOS")
        )

        if catalog_df is not None and not catalog_df.empty:
            total_courses = len(catalog_df)
        else:
            # Estimacion: malla tipica tiene ~55 cursos
            total_courses = 55

        courses_per_student["AVANCE_MALLA"] = (
            courses_per_student["N_APROBADOS"] / max(total_courses, 1)
        ).clip(0.0, 1.0)

        return courses_per_student[["RUT", "AVANCE_MALLA", "N_APROBADOS"]]

    def find_new_courses_without_history(
        self,
        catalog_df: pd.DataFrame,
        curriculum_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Identifica cursos del catalogo que ningun alumno ha cursado.

        Retorna DataFrame con MATERIA, CURSO, TITULO de cursos nuevos.
        """
        cat = self._normalizer.normalize_columns(catalog_df)
        hist = self._normalizer.normalize_columns(curriculum_df)

        catalog_keys = set(
            cat.apply(lambda r: f"{r['MATERIA']}-{r['CURSO']}", axis=1),
        )
        history_keys = set(
            hist.apply(lambda r: f"{r['MATERIA']}-{r['CURSO']}", axis=1),
        )

        new_keys = catalog_keys - history_keys
        new_courses = cat[
            cat.apply(lambda r: f"{r['MATERIA']}-{r['CURSO']}", axis=1)
            .isin(new_keys)
        ]
        return new_courses.reset_index(drop=True)

    def infer_candidates_for_new_courses(
        self,
        new_courses_df: pd.DataFrame,
        prerequisites_map: Dict[str, List[str]],
        catalog_df: pd.DataFrame,
        approved_courses_df: pd.DataFrame,
        student_grades_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Infiere candidatos para cursos nuevos basandose en:
        1. Notas en cursos requisito (prerequisitos directos)
        2. Notas en cursos de la misma area/carrera (inferencia)
        3. PGA general del alumno

        Retorna DataFrame con pares (RUT, NRC/MATERIA, CURSO) + NOTA_INFERIDA.
        """
        if new_courses_df.empty:
            return pd.DataFrame()

        norm = self._normalizer.normalize_columns
        catalog = norm(catalog_df)
        students = norm(student_grades_df)

        # Mapa de titulo -> MATERIA-CURSO para resolver requisitos por nombre
        title_to_key = {}
        for _, row in catalog.iterrows():
            titulo = str(row.get("TITULO", "")).strip().upper()
            if titulo:
                title_to_key[titulo] = f"{row['MATERIA']}-{row['CURSO']}"

        inferred_rows = []

        for _, course in new_courses_df.iterrows():
            course_key = f"{course['MATERIA']}-{course['CURSO']}"
            prereq_titles = prerequisites_map.get(course_key, [])

            # Resolver titulos de requisitos a MATERIA-CURSO
            prereq_keys = []
            for title in prereq_titles:
                title_upper = title.strip().upper()
                if title_upper in title_to_key:
                    prereq_keys.append(title_to_key[title_upper])

            if prereq_keys and not approved_courses_df.empty:
                # Buscar alumnos que aprobaron los requisitos
                for _, approved_row in approved_courses_df.iterrows():
                    approved_key = (
                        f"{approved_row['MATERIA']}-{approved_row['CURSO']}"
                    )
                    if approved_key in prereq_keys:
                        inferred_rows.append({
                            "RUT": approved_row["RUT"],
                            "MATERIA": course["MATERIA"],
                            "CURSO": course["CURSO"],
                            "TITULO": course.get("TITULO", ""),
                            "NOTA_INFERIDA": float(approved_row.get("NOTA", 0)),
                            "FUENTE_INFERENCIA": f"Requisito: {approved_key}",
                            "ES_CURSO_NUEVO": True,
                        })

        if not inferred_rows:
            return pd.DataFrame()

        result = pd.DataFrame(inferred_rows)

        # Promediar si un alumno tiene multiples requisitos aprobados
        result = (
            result.groupby(["RUT", "MATERIA", "CURSO", "TITULO"])
            .agg(
                NOTA_INFERIDA=("NOTA_INFERIDA", "mean"),
                FUENTE_INFERENCIA=("FUENTE_INFERENCIA", "first"),
            )
            .reset_index()
        )
        result["ES_CURSO_NUEVO"] = True
        result["NOTA_RAMO"] = result["NOTA_INFERIDA"]

        return result


# ---------------------------------------------------------------------------
# 3c. Generador de explicaciones IA
# ---------------------------------------------------------------------------

class AIExplanationGenerator:
    """Genera explicaciones detalladas de por que la IA recomienda a un candidato."""

    @staticmethod
    def generate_explanation(
        candidate: Dict,
        weights: Optional[Dict[str, float]] = None,
    ) -> str:
        """
        Genera una explicacion detallada de la recomendacion IA.

        Analiza cada factor y su contribucion al score final.
        """
        w = weights or DETERMINISTIC_SCORE_WEIGHTS
        parts = []

        nota = candidate.get("NOTA_RAMO") or 0
        pga = candidate.get("PGA") or 0
        exp = candidate.get("N_VECES_AYUDANTE") or 0
        avance = candidate.get("AVANCE_MALLA") or 0
        carga = candidate.get("CARGA_ACTUAL") or 0
        postulante = candidate.get("POSTULANTE_ACTUAL", False)
        es_nuevo = candidate.get("ES_CURSO_NUEVO", False)

        # Factor 1: Nota en el ramo (mayor peso normalmente)
        if es_nuevo:
            parts.append(
                f"Nota inferida {nota:.1f}/7.0 basada en cursos requisito"
            )
        elif nota >= 6.0:
            parts.append(f"Nota sobresaliente en el ramo ({nota:.1f})")
        elif nota >= 5.5:
            parts.append(f"Buena nota en el ramo ({nota:.1f})")
        elif nota >= 5.0:
            parts.append(f"Nota aceptable ({nota:.1f})")
        else:
            parts.append(f"Nota justa ({nota:.1f})")

        # Factor 2: PGA
        if pga >= 5.5:
            parts.append(f"alto PGA ({pga:.1f})")
        elif pga >= 5.0:
            parts.append(f"buen PGA ({pga:.1f})")

        # Factor 3: Experiencia
        if exp > 1:
            parts.append(f"experiencia sólida ({exp} veces ayudante)")
        elif exp == 1:
            parts.append("tiene experiencia previa")

        # Factor 4: Avance curricular
        if avance >= 0.7:
            parts.append("avanzado en la malla curricular")
        elif avance >= 0.4:
            parts.append("buen avance en la malla")

        # Factor 5: Carga academica
        if carga <= 4 and carga > 0:
            parts.append(f"carga ligera ({carga} ramos)")
        elif carga >= 7:
            parts.append(f"carga alta ({carga} ramos)")

        # Factor 6: Postulante
        if postulante:
            parts.append("postuló este período")

        return ". ".join(parts[:4]) + "." if parts else "Candidato evaluado."

    @staticmethod
    def generate_short_explanation(candidate: Dict) -> str:
        """Genera explicacion corta (max 15 palabras) para el dashboard."""
        nota = candidate.get("NOTA_RAMO") or 0
        pga = candidate.get("PGA") or 0
        exp = candidate.get("N_VECES_AYUDANTE") or 0
        es_nuevo = candidate.get("ES_CURSO_NUEVO", False)
        parts = []

        if es_nuevo:
            parts.append(f"Nota inferida {nota:.1f}")
        elif nota >= 6.0:
            parts.append(f"Nota {nota:.1f}")
        elif nota >= 5.5:
            parts.append(f"Nota {nota:.1f}")
        else:
            parts.append(f"Nota {nota:.1f}")

        parts.append(f"PGA {pga:.1f}")

        if exp > 0:
            parts.append(f"{exp}× ayudante")
        elif candidate.get("POSTULANTE_ACTUAL"):
            parts.append("postulante")

        return " · ".join(parts)


# ---------------------------------------------------------------------------
# 4. Construccion de candidatos elegibles
# ---------------------------------------------------------------------------

class EligibleCandidateBuilder:
    """
    Cruza las fuentes de datos academicos y construye la tabla de candidatos.

    Fuentes:
        malla_df         — historial academico (Cumplimiento de Malla)
        promedios_df     — promedios por alumno (Reporte Alumnos con Promedio)
        inscritos_df     — ramos inscritos en el periodo (Ramos Inscritos)
        nrc_df           — cursos y horarios del periodo (Listado de NRC)
        postulaciones_df — postulaciones de ayudantias (historial + actual)
    """

    def __init__(self, minimum_grade: float = MINIMUM_GRADE_TO_BE_ELIGIBLE):
        self.minimum_grade = minimum_grade
        self._normalizer = ColumnNormalizer()
        self._schedule_analyzer = ScheduleAnalyzer()
        self._experience_analyzer = ExperienceAnalyzer()
        self._curriculum_processor = CurriculumCatalogProcessor()

    # -- Consulta rapida de estudiantes --

    def get_students_summary(
        self,
        grades_df: pd.DataFrame,
        applications_df: Optional[pd.DataFrame] = None,
        current_period: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Retorna rapidamente una tabla de alumnos con info basica.

        Solo necesita promedios + postulaciones (sin malla ni NRC).
        Util para mostrar la lista de estudiantes mientras se cargan datos pesados.
        """
        students = self._normalizer.normalize_columns(grades_df)
        students["RUT"] = students["RUT"].astype(str).str.strip()
        students = students.drop_duplicates(subset=["RUT"], keep="first").copy()
        students["NOMBRE_COMPLETO"] = self._normalizer.extract_full_name(students)

        if "PROMEDIO GENERAL ACUMULADO" in students.columns:
            students["PGA"] = self._normalizer.parse_european_decimal(
                students["PROMEDIO GENERAL ACUMULADO"],
            )
        elif "PGA" not in students.columns:
            students["PGA"] = np.nan

        # Carrera / escuela
        for col_name in ["CARRERA", "PROGRAMA", "ESCUELA"]:
            if col_name in students.columns:
                students["CARRERA"] = (
                    students[col_name].fillna("").astype(str).str.strip()
                )
                break
        else:
            students["CARRERA"] = ""

        # Historial de ayudantias
        experience = self._experience_analyzer.build_experience_features(
            applications_df, current_period,
        )
        experience_columns = [
            c for c in ["RUT", "N_VECES_AYUDANTE", "EXPERIENCIA_PREVIA"]
            if c in experience.columns
        ]
        if not experience.empty and experience_columns:
            students = students.merge(experience[experience_columns], on="RUT", how="left")

        students["N_VECES_AYUDANTE"] = (
            students.get("N_VECES_AYUDANTE", pd.Series(0, index=students.index))
            .fillna(0).astype(int)
        )
        students["EXPERIENCIA_PREVIA"] = (
            students.get("EXPERIENCIA_PREVIA", pd.Series(False, index=students.index))
            .fillna(False)
        )

        # Fill empty names with synthetic ones
        if "NOMBRE_COMPLETO" in students.columns:
            empty_mask = (
                students["NOMBRE_COMPLETO"].fillna("").str.strip() == ""
            )
            if empty_mask.any():
                students.loc[empty_mask, "NOMBRE_COMPLETO"] = (
                    students.loc[empty_mask, "RUT"].apply(generate_synthetic_name)
                )

        output_columns = [
            "RUT", "NOMBRE_COMPLETO", "PGA",
            "N_VECES_AYUDANTE", "EXPERIENCIA_PREVIA", "CARRERA",
        ]
        return students[
            [c for c in output_columns if c in students.columns]
        ].reset_index(drop=True)

    # -- Historial de cursos aprobados --

    def get_approved_courses(self, curriculum_df: pd.DataFrame) -> pd.DataFrame:
        """
        Retorna cursos aprobados por cada alumno con nota >= minima.

        Una fila por (RUT, MATERIA, CURSO) con la nota mas alta obtenida.
        """
        df = self._normalizer.normalize_columns(curriculum_df)
        df["NOTA"] = self._normalizer.parse_european_decimal(df["NOTA"])

        passed_mask = df["NOTA"] >= self.minimum_grade
        if "ORIGEN" in df.columns:
            valid_origins = {"H", "OE", "TR"}
            passed_mask &= df["ORIGEN"].str.strip().str.upper().isin(valid_origins)

        approved = df[passed_mask][["RUT", "MATERIA", "CURSO", "NOTA"]].copy()
        approved = (
            approved.sort_values("NOTA", ascending=False)
            .groupby(["RUT", "MATERIA", "CURSO"], as_index=False)
            .first()
        )
        return approved.reset_index(drop=True)

    # -- Cursos que necesitan ayudantes --

    def get_courses_needing_assistants(
        self, nrc_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Retorna secciones activas del periodo que necesitan ayudantes.

        Colapsa multiples filas por NRC (CLAS/LAB/AYUD) a una fila unica.
        Calcula AYUDANTES_REQUERIDOS = ceil(INSCRITOS / ratio).
        """
        df = self._normalizer.normalize_columns(nrc_df)

        active_mask = pd.Series([True] * len(df), index=df.index)
        if "STATUS" in df.columns:
            active_mask &= df["STATUS"].str.strip().str.upper() == "A"

        active_courses = df[active_mask].copy()
        active_courses = active_courses.drop_duplicates(subset=["NRC"], keep="first")

        active_courses["INSCRITOS"] = self._normalizer.parse_european_decimal(
            active_courses.get("INSCRITOS", pd.Series([30] * len(active_courses))),
        ).fillna(30)
        active_courses["CUPOS"] = self._normalizer.parse_european_decimal(
            active_courses.get("CUPOS", pd.Series([40] * len(active_courses))),
        ).fillna(40)
        active_courses["AYUDANTES_REQUERIDOS"] = (
            np.ceil(active_courses["INSCRITOS"] / STUDENTS_PER_TEACHING_ASSISTANT)
            .clip(1, 3).astype(int)
        )

        output_columns = [
            "NRC", "MATERIA", "CURSO", "TITULO",
            "INSCRITOS", "AYUDANTES_REQUERIDOS",
        ] + WEEKDAYS
        return active_courses[
            [c for c in output_columns if c in active_courses.columns]
        ].reset_index(drop=True)

    # -- Construccion completa de candidatos --

    def build_eligible_candidates(
        self,
        curriculum_df: pd.DataFrame,
        grades_df: pd.DataFrame,
        enrolled_df: pd.DataFrame,
        nrc_df: pd.DataFrame,
        applications_df: Optional[pd.DataFrame] = None,
        current_period: Optional[str] = None,
        catalog_df: Optional[pd.DataFrame] = None,
        prerequisites_map: Optional[Dict[str, List[str]]] = None,
    ) -> pd.DataFrame:
        """
        Construye la tabla de pares (alumno, seccion) elegibles con features.

        Filtros deterministicos:
            1. Alumno aprobo la asignatura con nota >= minima
            2. Alumno no esta cursando esa asignatura actualmente
            3. Alumno no tiene conflicto horario con el curso TA

        Si se proporciona catalog_df, tambien:
            - Calcula AVANCE_MALLA por alumno
            - Infiere candidatos para cursos nuevos sin historial
        """
        approved = self.get_approved_courses(curriculum_df)
        courses_needing_ta = self.get_courses_needing_assistants(nrc_df)
        student_grades = self._normalizer.normalize_columns(grades_df)
        enrolled = self._normalizer.normalize_columns(enrolled_df)

        # Cruce: alumno es candidato si aprobo la MATERIA+CURSO del NRC
        candidates = approved.merge(
            courses_needing_ta,
            on=["MATERIA", "CURSO"],
            how="inner",
            suffixes=("_HIST", "_TA"),
        ).rename(columns={"NOTA": "NOTA_RAMO"})
        print(f"  Pares elegibles (antes filtros horario/carga): {len(candidates)}")

        candidates = self._exclude_currently_enrolled(candidates, enrolled)
        candidates = self._add_academic_load(candidates, enrolled)
        candidates = self._filter_by_schedule_availability(
            candidates, nrc_df, enrolled_df,
        )
        candidates = self._merge_student_grades(candidates, student_grades)
        candidates = self._merge_experience_features(
            candidates, applications_df, current_period,
        )

        # Avance curricular
        candidates = self._add_curriculum_progress(
            candidates, curriculum_df, catalog_df,
        )

        # Cursos nuevos sin historial (inferencia por requisitos)
        if (catalog_df is not None and prerequisites_map
                and not catalog_df.empty):
            inferred = self._infer_new_course_candidates(
                catalog_df, prerequisites_map, curriculum_df,
                approved, student_grades, courses_needing_ta,
                enrolled, enrolled_df, nrc_df,
                applications_df, current_period,
            )
            if not inferred.empty:
                candidates = pd.concat(
                    [candidates, inferred], ignore_index=True,
                )
                print(f"  Candidatos inferidos (cursos nuevos): {len(inferred)}")

        # Deduplicacion de seguridad: un par (RUT, NRC) solo una vez
        count_before = len(candidates)
        candidates = candidates.drop_duplicates(
            subset=["RUT", "NRC"],
        ).reset_index(drop=True)
        if len(candidates) < count_before:
            removed = count_before - len(candidates)
            print(f"  [Dedup] Eliminados {removed} pares duplicados (RUT, NRC)")

        # Asegurar que ES_CURSO_NUEVO exista
        if "ES_CURSO_NUEVO" not in candidates.columns:
            candidates["ES_CURSO_NUEVO"] = False

        print(f"  Pares elegibles (despues de filtros):          {len(candidates)}")
        return candidates

    def _add_curriculum_progress(
        self,
        candidates: pd.DataFrame,
        curriculum_df: pd.DataFrame,
        catalog_df: Optional[pd.DataFrame],
    ) -> pd.DataFrame:
        """Agrega AVANCE_MALLA a cada candidato."""
        progress = self._curriculum_processor.compute_curriculum_progress(
            curriculum_df, catalog_df,
        )
        if not progress.empty:
            candidates = candidates.merge(
                progress[["RUT", "AVANCE_MALLA"]], on="RUT", how="left",
            )
        candidates["AVANCE_MALLA"] = (
            candidates.get("AVANCE_MALLA", pd.Series(0.0)).fillna(0.0)
        )
        return candidates

    def _infer_new_course_candidates(
        self,
        catalog_df: pd.DataFrame,
        prerequisites_map: Dict[str, List[str]],
        curriculum_df: pd.DataFrame,
        approved_df: pd.DataFrame,
        student_grades_df: pd.DataFrame,
        courses_needing_ta: pd.DataFrame,
        enrolled: pd.DataFrame,
        enrolled_raw: pd.DataFrame,
        nrc_raw: pd.DataFrame,
        applications_df: Optional[pd.DataFrame],
        current_period: Optional[str],
    ) -> pd.DataFrame:
        """Infiere candidatos para cursos nuevos y los prepara con features."""
        new_courses = self._curriculum_processor.find_new_courses_without_history(
            catalog_df, curriculum_df,
        )
        if new_courses.empty:
            return pd.DataFrame()

        # Solo cursos nuevos que tambien esten en el NRC actual
        new_in_nrc = new_courses.merge(
            courses_needing_ta[["MATERIA", "CURSO", "NRC", "TITULO",
                                "INSCRITOS", "AYUDANTES_REQUERIDOS"]],
            on=["MATERIA", "CURSO"],
            how="inner",
        )
        if new_in_nrc.empty:
            return pd.DataFrame()

        inferred = self._curriculum_processor.infer_candidates_for_new_courses(
            new_in_nrc, prerequisites_map, catalog_df,
            approved_df, student_grades_df,
        )
        if inferred.empty:
            return pd.DataFrame()

        # Merge con NRC para tener horarios
        inferred = inferred.merge(
            courses_needing_ta, on=["MATERIA", "CURSO"],
            how="inner", suffixes=("", "_TA"),
        )
        if "TITULO_TA" in inferred.columns:
            inferred["TITULO"] = inferred["TITULO"].fillna(inferred["TITULO_TA"])
            inferred.drop(columns=["TITULO_TA"], inplace=True, errors="ignore")

        # Agregar PGA
        student_grades_norm = self._normalizer.normalize_columns(student_grades_df)
        if "PROMEDIO GENERAL ACUMULADO" in student_grades_norm.columns:
            student_grades_norm["PGA"] = self._normalizer.parse_european_decimal(
                student_grades_norm["PROMEDIO GENERAL ACUMULADO"],
            )
        student_grades_norm = student_grades_norm.drop_duplicates(subset=["RUT"], keep="first")
        if "PGA" in student_grades_norm.columns:
            inferred = inferred.merge(
                student_grades_norm[["RUT", "PGA"]], on="RUT", how="left",
            )

        # Agregar nombre
        inferred["NOMBRE_COMPLETO"] = self._normalizer.extract_full_name(
            student_grades_norm,
        ).reindex(inferred.index).fillna("")
        # Re-merge nombre by RUT
        name_map = student_grades_norm.set_index("RUT")
        if "NOMBRE_COMPLETO" not in name_map.columns:
            name_map["NOMBRE_COMPLETO"] = self._normalizer.extract_full_name(
                student_grades_norm,
            )
        name_df = name_map[["NOMBRE_COMPLETO"]].reset_index()
        name_df = name_df.rename(columns={"NOMBRE_COMPLETO": "NOMBRE_COMPLETO_PROM"})
        inferred = inferred.merge(name_df, on="RUT", how="left")
        inferred["NOMBRE_COMPLETO"] = inferred["NOMBRE_COMPLETO"].fillna(
            inferred.get("NOMBRE_COMPLETO_PROM", ""),
        )
        inferred.drop(columns=["NOMBRE_COMPLETO_PROM"], errors="ignore", inplace=True)

        # Defaults para campos faltantes
        for col, default in [
            ("CARGA_ACTUAL", 0), ("DISPONIBLE", 1),
            ("AVANCE_MALLA", 0.0), ("N_VECES_AYUDANTE", 0),
            ("EXPERIENCIA_PREVIA", False), ("POSTULANTE_ACTUAL", False),
        ]:
            if col not in inferred.columns:
                inferred[col] = default

        return inferred

    def _exclude_currently_enrolled(
        self,
        candidates: pd.DataFrame,
        enrolled: pd.DataFrame,
    ) -> pd.DataFrame:
        """Excluye alumnos que estan cursando la misma asignatura."""
        currently_taking = enrolled[["RUT", "MATERIA", "CURSO"]].copy()
        currently_taking["RUT"] = currently_taking["RUT"].astype(str)
        currently_taking["_CURSANDO"] = True

        candidates = candidates.merge(
            currently_taking, on=["RUT", "MATERIA", "CURSO"], how="left",
        )
        return candidates[
            candidates["_CURSANDO"].isna()
        ].drop(columns=["_CURSANDO"])

    @staticmethod
    def _add_academic_load(
        candidates: pd.DataFrame,
        enrolled: pd.DataFrame,
    ) -> pd.DataFrame:
        """Agrega CARGA_ACTUAL (cantidad de ramos inscritos) por alumno."""
        load_by_student = enrolled.groupby("RUT")["NRC"].count().reset_index()
        load_by_student.columns = ["RUT", "CARGA_ACTUAL"]
        load_by_student["RUT"] = load_by_student["RUT"].astype(str)

        candidates = candidates.merge(load_by_student, on="RUT", how="left")
        candidates["CARGA_ACTUAL"] = (
            candidates["CARGA_ACTUAL"].fillna(0).astype(int)
        )
        return candidates

    def _filter_by_schedule_availability(
        self,
        candidates: pd.DataFrame,
        nrc_df: pd.DataFrame,
        enrolled_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Elimina candidatos con conflicto horario entre su carga y el curso TA."""
        course_schedules = self._schedule_analyzer.build_course_schedule_map(nrc_df)
        student_schedules = self._schedule_analyzer.build_student_schedule_map(
            enrolled_df, nrc_df,
        )

        candidates["DISPONIBLE"] = candidates.apply(
            lambda row: int(
                not self._schedule_analyzer.has_schedule_overlap(
                    course_schedules.get(str(row["NRC"]), []),
                    student_schedules.get(str(row["RUT"]), []),
                )
            ),
            axis=1,
        )
        return candidates[candidates["DISPONIBLE"] == 1].copy()

    def _merge_student_grades(
        self,
        candidates: pd.DataFrame,
        student_grades: pd.DataFrame,
    ) -> pd.DataFrame:
        """Agrega promedios academicos y nombre completo al DataFrame de candidatos."""
        grade_column_mapping = {
            "PROMEDIO GENERAL ACUMULADO": "PGA",
        }
        student_grades["RUT"] = student_grades["RUT"].astype(str)
        for col in grade_column_mapping:
            if col in student_grades.columns:
                student_grades[col] = self._normalizer.parse_european_decimal(
                    student_grades[col],
                )

        student_grades = student_grades.drop_duplicates(subset=["RUT"], keep="first")

        name_columns = [
            c for c in [
                "PATERNO", "MATERNO", "NOMBRE", "NOMBRES",
                "PRIMER APELLIDO", "SEGUNDO APELLIDO",
            ] if c in student_grades.columns
        ]
        columns_to_keep = (
            ["RUT"]
            + [c for c in grade_column_mapping if c in student_grades.columns]
            + name_columns
        )

        candidates = candidates.merge(
            student_grades[columns_to_keep], on="RUT", how="left",
        )
        candidates = candidates.rename(columns=grade_column_mapping)
        candidates["NOMBRE_COMPLETO"] = self._normalizer.extract_full_name(candidates)

        # Fill empty names with synthetic ones
        empty_mask = (
            candidates["NOMBRE_COMPLETO"].fillna("").str.strip() == ""
        )
        if empty_mask.any():
            candidates.loc[empty_mask, "NOMBRE_COMPLETO"] = (
                candidates.loc[empty_mask, "RUT"].apply(generate_synthetic_name)
            )
        return candidates

    def _merge_experience_features(
        self,
        candidates: pd.DataFrame,
        applications_df: Optional[pd.DataFrame],
        current_period: Optional[str],
    ) -> pd.DataFrame:
        """Agrega features de experiencia previa como ayudante."""
        experience = self._experience_analyzer.build_experience_features(
            applications_df, current_period,
        )

        if not experience.empty and "RUT" in experience.columns:
            experience_columns = [
                c for c in [
                    "RUT", "EXPERIENCIA_PREVIA", "N_VECES_AYUDANTE",
                    "PROM_EVAL_PREVIA", "ULTIMA_EVAL",
                    "POSTULANTE_ACTUAL", "MOTIVACION_SCORE",
                ] if c in experience.columns
            ]
            candidates = candidates.merge(
                experience[experience_columns], on="RUT", how="left",
            )
            candidates["EXPERIENCIA_PREVIA"] = (
                candidates["EXPERIENCIA_PREVIA"].fillna(False)
            )
            candidates["N_VECES_AYUDANTE"] = (
                candidates.get("N_VECES_AYUDANTE", pd.Series(0))
                .fillna(0).astype(int)
            )
            candidates["POSTULANTE_ACTUAL"] = (
                candidates.get("POSTULANTE_ACTUAL", pd.Series(False))
                .fillna(False)
            )
            n_experienced = candidates["EXPERIENCIA_PREVIA"].sum()
            n_applicants = candidates["POSTULANTE_ACTUAL"].sum()
            print(f"  Con experiencia previa: {n_experienced} | "
                  f"Postulantes actuales: {n_applicants}")
        else:
            candidates["EXPERIENCIA_PREVIA"] = False
            candidates["N_VECES_AYUDANTE"] = 0
            candidates["POSTULANTE_ACTUAL"] = False

        # Merge current-period application status per (RUT, MATERIA, CURSO)
        app_status = self._experience_analyzer.build_current_application_status(
            applications_df, current_period,
        )
        if not app_status.empty:
            candidates = candidates.merge(
                app_status, on=["RUT", "MATERIA", "CURSO"], how="left",
            )
        for col, default in [
            ("ESTADO_POSTULACION", ""),
            ("TIPO_AYUDANTE_POST", ""),
            ("PROFESOR_POST", ""),
            ("N_ACEPTADAS_ACTUAL", 0),
        ]:
            if col not in candidates.columns:
                candidates[col] = default
            else:
                candidates[col] = candidates[col].fillna(default)
        candidates["N_ACEPTADAS_ACTUAL"] = (
            candidates["N_ACEPTADAS_ACTUAL"].fillna(0).astype(int)
        )

        return candidates


# ---------------------------------------------------------------------------
# Wrapper de compatibilidad: DataProcessor
# ---------------------------------------------------------------------------

class DataProcessor(EligibleCandidateBuilder):
    """
    Alias de compatibilidad con el API existente.

    Delega toda la logica a EligibleCandidateBuilder y sus componentes,
    manteniendo los nombres de metodos que usa app.py.
    """

    ESTADOS_ACEPTADO = ACCEPTED_APPLICATION_STATES
    DIAS = WEEKDAYS
    TIPOS_CLASE = CLASSROOM_ACTIVITY_TYPES

    def __init__(self, nota_minima: float = MINIMUM_GRADE_TO_BE_ELIGIBLE):
        super().__init__(minimum_grade=nota_minima)
        self.nota_minima = nota_minima

    # Metodos delegados con nombres originales

    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        return self._normalizer.normalize_columns(df)

    def _to_float(self, series: pd.Series) -> pd.Series:
        return self._normalizer.parse_european_decimal(series)

    def _parse_time_range(self, s: str) -> Optional[Tuple[int, int]]:
        return ScheduleAnalyzer.parse_time_range(s)

    def _slots_from_row(self, row: pd.Series) -> List[Tuple]:
        return ScheduleAnalyzer.extract_time_slots_from_row(row)

    def _has_conflict(
        self, slots_a: List[Tuple], slots_b: List[Tuple],
    ) -> bool:
        return ScheduleAnalyzer.has_schedule_overlap(slots_a, slots_b)

    def _build_nrc_slots(self, nrc_df: pd.DataFrame) -> Dict[str, List[Tuple]]:
        return ScheduleAnalyzer.build_course_schedule_map(nrc_df)

    @staticmethod
    def _extract_nombre_completo(df: pd.DataFrame) -> pd.Series:
        return ColumnNormalizer.extract_full_name(df)

    def get_students_fast(
        self,
        promedios_df: pd.DataFrame,
        postulaciones_df: Optional[pd.DataFrame] = None,
        periodo_actual: Optional[str] = None,
    ) -> pd.DataFrame:
        return self.get_students_summary(promedios_df, postulaciones_df, periodo_actual)

    def get_historial_aprobados(self, malla_df: pd.DataFrame) -> pd.DataFrame:
        return self.get_approved_courses(malla_df)

    def get_cursos_necesitan_ta(self, nrc_df: pd.DataFrame) -> pd.DataFrame:
        return self.get_courses_needing_assistants(nrc_df)

    def get_horarios_por_alumno(
        self, inscritos_df: pd.DataFrame, nrc_df: pd.DataFrame,
    ) -> Dict[str, List[Tuple]]:
        return ScheduleAnalyzer.build_student_schedule_map(inscritos_df, nrc_df)

    def build_candidates(
        self,
        malla_df: pd.DataFrame,
        promedios_df: pd.DataFrame,
        inscritos_df: pd.DataFrame,
        nrc_df: pd.DataFrame,
        postulaciones_df: Optional[pd.DataFrame] = None,
        periodo_actual: Optional[str] = None,
        catalog_df: Optional[pd.DataFrame] = None,
        prerequisites_map: Optional[Dict[str, List[str]]] = None,
    ) -> pd.DataFrame:
        return self.build_eligible_candidates(
            malla_df, promedios_df, inscritos_df, nrc_df,
            postulaciones_df, periodo_actual,
            catalog_df=catalog_df,
            prerequisites_map=prerequisites_map,
        )

    def build_experiencia_features(
        self,
        postulaciones_df: Optional[pd.DataFrame],
        periodo_actual: Optional[str] = None,
    ) -> pd.DataFrame:
        return self._experience_analyzer.build_experience_features(
            postulaciones_df, periodo_actual,
        )


# ---------------------------------------------------------------------------
# 5. Modelo predictivo (Random Forest)
# ---------------------------------------------------------------------------

class RandomForestScorer:
    """
    Clasifica candidatos segun probabilidad de buen desempeno como ayudante.

    Si existen evaluaciones reales en postulaciones, las usa como etiquetas.
    En caso contrario, simula etiquetas basadas en la nota del ramo.
    """

    FEATURE_COLUMNS = [
        "NOTA_RAMO",
        "PGA",
        "CARGA_ACTUAL",
        "N_VECES_AYUDANTE",
        "AVANCE_MALLA",
        "PROM_EVAL_PREVIA",
        "POSTULANTE_ACTUAL",
    ]

    def __init__(
        self,
        performance_threshold: float = GOOD_PERFORMANCE_GRADE_THRESHOLD,
        random_state: int = RANDOM_STATE,
    ):
        self.performance_threshold = performance_threshold
        self.random_state = random_state
        self.model = RandomForestClassifier(
            n_estimators=200,
            max_depth=6,
            min_samples_leaf=5,
            class_weight="balanced",
            random_state=random_state,
        )
        self.scaler = StandardScaler()
        self.f1_score_: Optional[float] = None
        self.feature_importance_: Optional[pd.Series] = None
        self._is_trained = False

    def generate_training_labels(self, candidates: pd.DataFrame) -> pd.Series:
        """
        Genera etiquetas de entrenamiento.

        Prioriza evaluaciones reales (PROM_EVAL_PREVIA) si hay suficientes datos.
        Fallback: simula con NOTA_RAMO >= umbral + 5% de ruido.
        """
        eval_column = "PROM_EVAL_PREVIA"
        if eval_column in candidates.columns:
            real_evaluations = candidates[eval_column].dropna()
            has_enough_real_data = len(real_evaluations) >= 0.5 * len(candidates)

            if has_enough_real_data:
                labels = (
                    candidates[eval_column] >= self.performance_threshold
                ).fillna(
                    candidates["NOTA_RAMO"] >= self.performance_threshold
                ).astype(int)
                print("  [Etiquetas] Usando evaluaciones reales de Postulaciones.")
                return labels

        # Fallback: simulacion por nota
        np.random.seed(self.random_state)
        labels = (
            candidates["NOTA_RAMO"] >= self.performance_threshold
        ).astype(int).copy()
        noise_mask = np.random.rand(len(labels)) < 0.05
        labels[noise_mask] = 1 - labels[noise_mask]
        print(
            "  [Etiquetas] Usando simulacion (NOTA_RAMO >= umbral). "
            "Agregar evaluaciones reales de Postulaciones para mejorar."
        )
        return labels

    def _extract_feature_matrix(self, candidates: pd.DataFrame) -> pd.DataFrame:
        """Extrae y rellena las columnas de features disponibles."""
        available_features = [
            col for col in self.FEATURE_COLUMNS if col in candidates.columns
        ]
        feature_matrix = candidates[available_features].copy()
        return feature_matrix.fillna(feature_matrix.median())

    def train(self, candidates: pd.DataFrame) -> "RandomForestScorer":
        """Entrena el modelo y calcula F1-score en test set (80/20)."""
        feature_matrix = self._extract_feature_matrix(candidates)
        labels = self.generate_training_labels(candidates)

        if len(candidates) < 30:
            print("  [Aviso] Pocos datos para entrenar; se usara scoring heuristico.")
            return self

        x_train, x_test, y_train, y_test = train_test_split(
            feature_matrix, labels,
            test_size=0.20,
            random_state=self.random_state,
            stratify=labels,
        )
        x_train_scaled = self.scaler.fit_transform(x_train)
        x_test_scaled = self.scaler.transform(x_test)

        self.model.fit(x_train_scaled, y_train)
        y_predicted = self.model.predict(x_test_scaled)

        self.f1_score_ = f1_score(y_test, y_predicted, zero_division=0)
        self.feature_importance_ = pd.Series(
            self.model.feature_importances_,
            index=feature_matrix.columns,
        ).sort_values(ascending=False)
        self._is_trained = True

        print(f"  F1-Score (test set): {self.f1_score_:.4f}")
        print(classification_report(
            y_test, y_predicted,
            zero_division=0,
            target_names=["No apto", "Apto"],
        ))
        return self

    def score(self, candidates: pd.DataFrame) -> pd.Series:
        """Retorna probabilidad de buen desempeno [0,1] para cada candidato."""
        feature_matrix = self._extract_feature_matrix(candidates)

        if not self._is_trained:
            return compute_deterministic_score(candidates)

        scaled_features = self.scaler.transform(
            feature_matrix.fillna(feature_matrix.median()),
        )
        return pd.Series(
            self.model.predict_proba(scaled_features)[:, 1],
            index=candidates.index,
        )


# Alias de compatibilidad
class CandidateScorer(RandomForestScorer):
    """Alias de compatibilidad para codigo existente."""

    FEATURES = RandomForestScorer.FEATURE_COLUMNS

    def __init__(
        self,
        nota_umbral: float = GOOD_PERFORMANCE_GRADE_THRESHOLD,
        random_state: int = RANDOM_STATE,
    ):
        super().__init__(
            performance_threshold=nota_umbral,
            random_state=random_state,
        )
        self.nota_umbral = nota_umbral

    def _create_labels(self, df: pd.DataFrame) -> pd.Series:
        return self.generate_training_labels(df)

    def _get_X(self, df: pd.DataFrame) -> pd.DataFrame:
        return self._extract_feature_matrix(df)


# ---------------------------------------------------------------------------
# 6. Optimizacion ILP
# ---------------------------------------------------------------------------

class AssignmentOptimizer:
    """
    Asignacion optima de ayudantes usando Programacion Lineal Entera (ILP).

    Objetivo:   maximizar sum(score[i,j] * x[i,j])
    Restricciones:
        R1 — Cobertura:     sum_i x[i,j] == requeridos_j    para cada seccion j
        R2 — Carga maxima:  sum_j x[i,j] <= max_ayudantias  para cada alumno i
        R3 — Disponibilidad: ya filtrada en EligibleCandidateBuilder
        R4 — Requisito academico: ya filtrado en EligibleCandidateBuilder
    """

    def __init__(
        self,
        max_assistantships: int = MAX_SIMULTANEOUS_ASSISTANTSHIPS,
    ):
        self.max_assistantships = max_assistantships

    def optimize(
        self,
        candidates: pd.DataFrame,
        scores: pd.Series,
    ) -> pd.DataFrame:
        """Resuelve ILP y retorna candidates con columna ASIGNADO (0/1)."""
        df = candidates.copy()
        df["SCORE"] = scores.values
        df["NRC"] = df["NRC"].astype(str)

        if df.empty:
            df["ASIGNADO"] = 0
            return df

        unique_students = df["RUT"].unique().tolist()
        unique_sections = df["NRC"].unique().tolist()
        required_per_section: Dict[str, int] = (
            df.groupby("NRC")["AYUDANTES_REQUERIDOS"]
            .first().astype(int).to_dict()
        )

        problem = pulp.LpProblem("AsignacionAyudantes", pulp.LpMaximize)

        # Variables de decision binarias
        assignment_vars = {
            (str(row["RUT"]), str(row["NRC"])): pulp.LpVariable(
                f"x_{idx}", cat="Binary",
            )
            for idx, row in df.iterrows()
        }

        # Funcion objetivo: maximizar score total
        problem += pulp.lpSum(
            row["SCORE"] * assignment_vars[(str(row["RUT"]), str(row["NRC"]))]
            for _, row in df.iterrows()
        )

        # R1 — Cobertura por seccion
        for section_nrc in unique_sections:
            section_candidates = df[df["NRC"] == section_nrc]
            required = required_per_section.get(section_nrc, 1)
            available_count = len(section_candidates)

            constraint_expr = pulp.lpSum(
                assignment_vars[(str(r["RUT"]), section_nrc)]
                for _, r in section_candidates.iterrows()
            )
            if available_count >= required:
                problem += (constraint_expr == required, f"cob_{section_nrc}")
            else:
                problem += (constraint_expr <= available_count, f"cob_{section_nrc}")

        # R2 — Carga maxima por alumno
        for student_rut in unique_students:
            student_candidates = df[df["RUT"] == student_rut]
            problem += (
                pulp.lpSum(
                    assignment_vars[(student_rut, str(r["NRC"]))]
                    for _, r in student_candidates.iterrows()
                ) <= self.max_assistantships,
                f"carga_{student_rut}",
            )

        problem.solve(pulp.PULP_CBC_CMD(msg=0))
        solver_status = pulp.LpStatus[problem.status]
        print(f"  Estado del solver: {solver_status}")

        df["ASIGNADO"] = df.apply(
            lambda row: int(round(
                assignment_vars[(str(row["RUT"]), str(row["NRC"]))].value() or 0,
            )),
            axis=1,
        )

        assigned_count = df["ASIGNADO"].sum()
        covered_sections = df[df["ASIGNADO"] == 1]["NRC"].nunique()
        print(f"  Ayudantes asignados: {assigned_count} en {covered_sections} secciones")
        return df


# ---------------------------------------------------------------------------
# 7. KPIs y Reporte
# ---------------------------------------------------------------------------

KPI_METADATA = {
    "kpi1": {
        "nombre": "Capacidad predictiva del modelo de clasificacion",
        "formula": "F1 = 2 * (Precision * Recall) / (Precision + Recall)",
        "variables": {
            "Precision": "VP / (VP + FP) — predicciones positivas correctas",
            "Recall":    "VP / (VP + FN) — casos positivos detectados correctamente",
            "VP": "Verdadero Positivo: predijo buen ayudante y lo era",
            "FP": "Falso Positivo: predijo buen ayudante pero no lo fue",
            "FN": "Falso Negativo: descarto a un alumno que si era buen ayudante",
            "Umbral_positivo": (
                f"Nota en la ayudantia >= {GOOD_PERFORMANCE_GRADE_THRESHOLD} "
                f"(escala 1-7)"
            ),
        },
        "baseline": 0.55,
        "baseline_nota": (
            "Seleccion manual subjetiva actual: estimado F1 ~0.55 "
            "(supuesto razonado — sin sistema de evaluacion formal)"
        ),
        "meta": 0.80,
        "interpretacion": (
            "F1 >= 0.80 valida que el modelo predice mejor que el proceso manual"
        ),
    },
    "kpi2": {
        "nombre": "Calidad promedio del desempeno de los ayudantes",
        "formula": (
            "KPI2 = (Pa1 + Pa2 + ... + Pam + Pa1' + Pa2' + ... + Pan') / (m + n)\n"
            "  donde Pa1..Pam = puntajes de ayudantias en semestre t-1 (m instancias)\n"
            "        Pa1'..Pan' = puntajes de ayudantias en semestre t  (n instancias)"
        ),
        "variables": {
            "Pai":  "Puntaje de la ayudantia i en el semestre t-1 (escala 1-7)",
            "Pai'": "Puntaje de la ayudantia i en el semestre t   (escala 1-7)",
            "m":    "Total de instancias de ayudantia evaluadas en semestre t-1",
            "n":    "Total de instancias de ayudantia evaluadas en semestre t",
        },
        "baseline": None,
        "baseline_nota": (
            "Sin sistema formal de evaluacion — baseline = 0 / no medible. "
            "Requiere reinstaurar encuestas estructuradas en 2 instancias por semestre."
        ),
        "meta": 5.0,
        "meta_nota": (
            "Promedio >= 5.0 en escala 1-7 (equivale a nota 'buena' en el ramo)"
        ),
    },
    "kpi3": {
        "nombre": "Tasa de cobertura de restricciones operativas",
        "formula": "KPI3 = R_cumplidas / R_totales   [0, 1]",
        "variables": {
            "R_cumplidas": "Numero de restricciones cumplidas en la asignacion final",
            "R_totales":   "Total de restricciones activas evaluadas",
            "R1": "Disponibilidad horaria del ayudante en el horario del curso",
            "R2": "Carga maxima de ayudantias simultaneas por alumno",
            "R3": "Requisito academico minimo (nota >= nota_minima en el ramo)",
            "R4": "Cobertura del curso: recibe exactamente los ayudantes requeridos",
        },
        "baseline": 0.70,
        "baseline_nota": (
            "Proceso manual actual: estimado 0.65-0.75 "
            "(supuesto razonado — restricciones no se cruzan sistematicamente)"
        ),
        "meta": 0.90,
        "meta_nota": (
            "Meta revisada a 0.90 (era 0.97, considerado demasiado elevado para MVP)"
        ),
    },
}


class KPIReporter:
    """Calcula y reporta los 3 KPIs del proyecto."""

    @staticmethod
    def compute_predictive_capability_kpi(
        f1_value: Optional[float],
    ) -> Dict:
        """KPI 1: F1-Score del modelo predictivo."""
        metadata = KPI_METADATA["kpi1"]

        if f1_value is None:
            return {
                "kpi": "KPI1",
                "valor": None,
                "baseline": metadata["baseline"],
                "meta": metadata["meta"],
                "estado": "Sin etiquetas de evaluacion historica disponibles",
            }

        if f1_value >= 0.85:
            status = "OPTIMO  (F1 > 0.85)"
        elif f1_value >= 0.80:
            status = "SUFICIENTE (0.80-0.85)"
        else:
            status = "INSUFICIENTE (F1 < 0.80)"

        improvement = round(f1_value - metadata["baseline"], 4)
        return {
            "kpi": "KPI1",
            "nombre": metadata["nombre"],
            "formula": metadata["formula"],
            "valor": round(f1_value, 4),
            "baseline": metadata["baseline"],
            "meta": metadata["meta"],
            "mejora_vs_baseline": improvement,
            "estado": status,
        }

    @staticmethod
    def compute_performance_quality_kpi(
        applications_df: Optional[pd.DataFrame] = None,
    ) -> Dict:
        """
        KPI 2: Calidad promedio del desempeno de ayudantes.

        Usa evaluaciones de los 2 semestres mas recientes.
        """
        metadata = KPI_METADATA["kpi2"]
        no_data_response = {
            "kpi": "KPI2",
            "nombre": metadata["nombre"],
            "formula": metadata["formula"],
            "valor": None,
            "baseline": metadata["baseline"],
            "meta": metadata["meta"],
            "estado": (
                "PENDIENTE — columna 'Evaluacion' en hoja de Postulaciones "
                "es la fuente de este KPI. Completar evaluaciones de ayudantes."
            ),
        }

        if applications_df is None or applications_df.empty:
            return no_data_response

        df = applications_df.copy()
        df.columns = [
            re.sub(r"\s+", " ", str(c).strip().upper()) for c in df.columns
        ]
        rename_map = {"EVALUACIÓN": "EVALUACION", "PERÍODO": "PERIODO"}
        df = df.rename(columns={
            k: v for k, v in rename_map.items() if k in df.columns
        })

        if "EVALUACION" not in df.columns or "PERIODO" not in df.columns:
            return no_data_response

        df["EVALUACION"] = (
            df["EVALUACION"].astype(str)
            .str.replace(",", ".")
            .pipe(pd.to_numeric, errors="coerce")
        )
        df["PERIODO"] = df["PERIODO"].astype(str).str.strip()

        if "ESTADO" in df.columns:
            df = df[
                df["ESTADO"].astype(str).str.strip().str.upper()
                .isin(ACCEPTED_APPLICATION_STATES)
            ]

        df = df.dropna(subset=["EVALUACION"])
        if df.empty:
            return no_data_response

        two_most_recent_periods = sorted(df["PERIODO"].unique())[-2:]
        recent_evaluations = df[df["PERIODO"].isin(two_most_recent_periods)]

        total_score = recent_evaluations["EVALUACION"].sum()
        total_instances = len(recent_evaluations)
        average_score = round(total_score / total_instances, 4)

        if average_score >= 5.5:
            status = "OPTIMO  (>= 5.5)"
        elif average_score >= 5.0:
            status = "SUFICIENTE (>= 5.0)"
        else:
            status = "INSUFICIENTE (< 5.0)"

        score_by_period = {
            period: round(
                recent_evaluations[
                    recent_evaluations["PERIODO"] == period
                ]["EVALUACION"].mean(),
                4,
            )
            for period in two_most_recent_periods
        }

        return {
            "kpi": "KPI2",
            "nombre": metadata["nombre"],
            "formula": metadata["formula"],
            "valor": average_score,
            "semestres_usados": two_most_recent_periods,
            "total_instancias": total_instances,
            "promedio_por_semestre": score_by_period,
            "baseline": metadata["baseline"],
            "meta": metadata["meta"],
            "estado": status,
        }

    @staticmethod
    def compute_constraint_coverage_kpi(
        result_df: pd.DataFrame,
    ) -> Dict:
        """KPI 3: Tasa de cobertura de restricciones operativas."""
        metadata = KPI_METADATA["kpi3"]
        assigned = result_df[result_df["ASIGNADO"] == 1]

        # R1 — Disponibilidad horaria (todos pasaron el filtro)
        r1_satisfied = len(assigned)
        r1_total = len(assigned)

        # R3 — Requisito academico
        r3_satisfied = int(
            (assigned["NOTA_RAMO"] >= MINIMUM_GRADE_TO_BE_ELIGIBLE).sum()
        )
        r3_total = len(assigned)

        # R4 — Cobertura del curso
        required_per_section = (
            result_df.groupby("NRC")["AYUDANTES_REQUERIDOS"]
            .first().astype(int)
        )
        assigned_per_section = assigned.groupby("NRC")["ASIGNADO"].sum()
        r4_satisfied = int(sum(
            assigned_per_section.get(nrc, 0) >= required
            for nrc, required in required_per_section.items()
        ))
        r4_total = len(required_per_section)

        # R2 — Carga maxima por alumno
        load_per_student = assigned.groupby("RUT")["ASIGNADO"].sum()
        r2_satisfied = int(
            (load_per_student <= MAX_SIMULTANEOUS_ASSISTANTSHIPS).sum()
        )
        r2_total = len(load_per_student)

        constraints_satisfied = r1_satisfied + r2_satisfied + r3_satisfied + r4_satisfied
        constraints_total = r1_total + r2_total + r3_total + r4_total
        coverage_rate = constraints_satisfied / max(constraints_total, 1)

        if coverage_rate >= 0.90:
            status = "OPTIMO  (>= 0.90)"
        elif coverage_rate >= 0.80:
            status = "SUFICIENTE (0.80-0.89)"
        else:
            status = "INSUFICIENTE (< 0.80)"

        improvement = round(coverage_rate - metadata["baseline"], 4)
        return {
            "kpi": "KPI3",
            "nombre": metadata["nombre"],
            "formula": metadata["formula"],
            "valor": round(coverage_rate, 4),
            "baseline": metadata["baseline"],
            "meta": metadata["meta"],
            "mejora_vs_baseline": improvement,
            "estado": status,
            "detalle": {
                "R1_disponibilidad_horaria": f"{r1_satisfied}/{r1_total}",
                "R2_carga_maxima_alumno":    f"{r2_satisfied}/{r2_total}",
                "R3_requisito_academico":    f"{r3_satisfied}/{r3_total}",
                "R4_cobertura_curso":        f"{r4_satisfied}/{r4_total}",
            },
        }

    # Aliases de compatibilidad
    @staticmethod
    def kpi1_f1(f1: Optional[float]) -> Dict:
        return KPIReporter.compute_predictive_capability_kpi(f1)

    @staticmethod
    def kpi2_calidad_desempeno(
        postulaciones_df: Optional[pd.DataFrame] = None,
    ) -> Dict:
        return KPIReporter.compute_performance_quality_kpi(postulaciones_df)

    @staticmethod
    def kpi3_cobertura_restricciones(result_df: pd.DataFrame) -> Dict:
        return KPIReporter.compute_constraint_coverage_kpi(result_df)

    def print_report(
        self,
        kpi1: Dict,
        kpi2: Dict,
        kpi3: Dict,
        result_df: pd.DataFrame,
    ) -> None:
        """Imprime reporte formateado de los 3 KPIs."""
        assigned = result_df[result_df["ASIGNADO"] == 1]
        separator = "=" * 64

        print(f"\n{separator}")
        print("        REPORTE DE KPIs — MVP GESTION DE AYUDANTES")
        print(separator)

        # KPI 1
        print("\n[KPI 1] Capacidad predictiva del modelo (F1-Score)")
        print("  Formula : F1 = 2*(Precision*Recall)/(Precision+Recall)")
        v1 = kpi1.get("valor")
        print(f"  Valor   : {f'{v1:.4f}' if v1 is not None else 'N/A'}")
        print(f"  Baseline: {kpi1.get('baseline', 'N/A')} (seleccion manual estimada)")
        print(f"  Meta    : >= {kpi1.get('meta', 0.80)}")
        if kpi1.get("mejora_vs_baseline") is not None:
            print(f"  Mejora  : {kpi1['mejora_vs_baseline']:+.4f} vs baseline")
        print(f"  Estado  : {kpi1.get('estado', 'N/A')}")

        # KPI 2
        print("\n[KPI 2] Calidad promedio del desempeno de ayudantes")
        print("  Formula : KPI2 = (SumPa_sem1 + SumPa_sem2) / total_instancias")
        v2 = kpi2.get("valor")
        print(f"  Valor   : {f'{v2:.4f} / 7.0' if v2 is not None else 'N/A'}")
        print(f"  Meta    : >= {kpi2.get('meta', 5.0)} (escala 1-7)")
        print(f"  Estado  : {kpi2.get('estado', 'N/A')}")

        # KPI 3
        print("\n[KPI 3] Tasa de cobertura de restricciones [0-1]")
        print("  Formula : KPI3 = R_cumplidas / R_totales")
        v3 = kpi3.get("valor")
        print(f"  Valor   : {f'{v3:.4f}' if v3 is not None else 'N/A'}")
        print(f"  Baseline: {kpi3.get('baseline', 'N/A')} (proceso manual estimado)")
        print(f"  Meta    : >= {kpi3.get('meta', 0.90)}")
        if kpi3.get("mejora_vs_baseline") is not None:
            print(f"  Mejora  : {kpi3['mejora_vs_baseline']:+.4f} vs baseline")
        print(f"  Estado  : {kpi3.get('estado', 'N/A')}")
        for key, value in kpi3.get("detalle", {}).items():
            print(f"    {key:32s}: {value}")

        # Resumen de asignacion
        print("\n[ASIGNACION FINAL]")
        print(f"  Ayudantes asignados : {len(assigned)}")
        print(f"  Secciones cubiertas : {assigned['NRC'].nunique()}")
        if not assigned.empty:
            print(f"  Score promedio      : {assigned['SCORE'].mean():.4f}")
            print(f"  Nota media en ramo  : {assigned['NOTA_RAMO'].mean():.2f}")
        print(separator + "\n")

    def get_ranking_por_curso(self, result_df: pd.DataFrame) -> pd.DataFrame:
        """Ranking completo de candidatos por seccion, asignados primero."""
        columns = [
            "NRC", "MATERIA", "CURSO", "RUT",
            "NOTA_RAMO", "PGA", "SCORE", "ASIGNADO",
        ]
        available_columns = [c for c in columns if c in result_df.columns]
        return (
            result_df[available_columns]
            .sort_values(
                ["NRC", "ASIGNADO", "SCORE"],
                ascending=[True, False, False],
            )
            .reset_index(drop=True)
        )


# ---------------------------------------------------------------------------
# 8. Funciones de pipeline (API publica)
# ---------------------------------------------------------------------------

def _resolve_weights(
    preset_name: Optional[str] = None,
    custom_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Resuelve los pesos a usar: preset > custom > default."""
    if preset_name and preset_name in WEIGHT_PRESETS:
        return WEIGHT_PRESETS[preset_name]["weights"]
    if custom_weights:
        return custom_weights
    return DETERMINISTIC_SCORE_WEIGHTS

def run_pipeline_deterministic(
    malla_df: pd.DataFrame,
    promedios_df: pd.DataFrame,
    inscritos_df: pd.DataFrame,
    nrc_df: pd.DataFrame,
    postulaciones_df: Optional[pd.DataFrame] = None,
    periodo_actual: Optional[str] = None,
    nota_minima: float = MINIMUM_GRADE_TO_BE_ELIGIBLE,
    catalog_df: Optional[pd.DataFrame] = None,
    prerequisites_map: Optional[Dict[str, List[str]]] = None,
    weight_preset: Optional[str] = None,
    custom_weights: Optional[Dict[str, float]] = None,
) -> Dict:
    """
    Pipeline deterministico: cruce de datos, filtros y score ponderado.

    Sin ML ni ILP. Retorna {candidates: DataFrame}.
    """
    print("\n--- Pipeline deterministico (sin ML) ---")
    builder = EligibleCandidateBuilder(minimum_grade=nota_minima)
    candidates = builder.build_eligible_candidates(
        malla_df, promedios_df, inscritos_df, nrc_df,
        applications_df=postulaciones_df,
        current_period=periodo_actual,
        catalog_df=catalog_df,
        prerequisites_map=prerequisites_map,
    )

    if candidates.empty:
        print("[ERROR] No hay candidatos elegibles.")
        return {}

    # Resolver pesos: preset > custom > default
    weights = _resolve_weights(weight_preset, custom_weights)

    candidates["SCORE"] = compute_deterministic_score(candidates, weights)
    candidates["ASIGNADO"] = 0
    print(
        f"  Score deterministico: media={candidates['SCORE'].mean():.4f}  "
        f"pesos={weights}"
    )
    return {"candidates": candidates, "weights_used": weights}


def run_pipeline_ai(
    candidates: pd.DataFrame,
    postulaciones_df: Optional[pd.DataFrame] = None,
) -> Dict:
    """
    Aplica Random Forest + ILP sobre candidatos ya procesados.

    Retorna {result, kpi1, kpi2, kpi3, feature_importance}.
    """
    print("\n--- Pipeline IA (RF + ILP) ---")
    if candidates.empty:
        return {}

    scorer = RandomForestScorer()
    scorer.train(candidates)
    scores = scorer.score(candidates)

    optimizer = AssignmentOptimizer()
    result = optimizer.optimize(candidates, scores)

    reporter = KPIReporter()
    kpi1 = reporter.compute_predictive_capability_kpi(scorer.f1_score_)
    kpi2 = reporter.compute_performance_quality_kpi(postulaciones_df)
    kpi3 = reporter.compute_constraint_coverage_kpi(result)
    reporter.print_report(kpi1, kpi2, kpi3, result)

    return {
        "result":             result,
        "kpi1":               kpi1,
        "kpi2":               kpi2,
        "kpi3":               kpi3,
        "feature_importance": scorer.feature_importance_,
    }


def run_pipeline(
    malla_df: pd.DataFrame,
    promedios_df: pd.DataFrame,
    inscritos_df: pd.DataFrame,
    nrc_df: pd.DataFrame,
    postulaciones_df: Optional[pd.DataFrame] = None,
    periodo_actual: Optional[str] = None,
    nota_minima: float = MINIMUM_GRADE_TO_BE_ELIGIBLE,
    catalog_df: Optional[pd.DataFrame] = None,
    prerequisites_map: Optional[Dict[str, List[str]]] = None,
) -> Dict:
    """
    Pipeline completo: cruce de datos + RF + ILP + KPIs.

    Retorna diccionario con candidates, result, ranking, kpis y feature_importance.
    """
    print("\n--- Etapa 1: Cruce y filtrado de datos ---")
    builder = EligibleCandidateBuilder(minimum_grade=nota_minima)
    candidates = builder.build_eligible_candidates(
        malla_df, promedios_df, inscritos_df, nrc_df,
        applications_df=postulaciones_df,
        current_period=periodo_actual,
        catalog_df=catalog_df,
        prerequisites_map=prerequisites_map,
    )

    if candidates.empty:
        print("[ERROR] No hay candidatos elegibles. Revisa los datos de entrada.")
        return {}

    print("\n--- Etapa 2: Modelo predictivo (Random Forest) ---")
    scorer = RandomForestScorer()
    scorer.train(candidates)
    scores = scorer.score(candidates)

    print("\n--- Etapa 3: Optimizacion ILP ---")
    optimizer = AssignmentOptimizer()
    result = optimizer.optimize(candidates, scores)

    print("\n--- Etapa 4: KPIs ---")
    reporter = KPIReporter()
    kpi1 = reporter.compute_predictive_capability_kpi(scorer.f1_score_)
    kpi2 = reporter.compute_performance_quality_kpi(postulaciones_df)
    kpi3 = reporter.compute_constraint_coverage_kpi(result)
    reporter.print_report(kpi1, kpi2, kpi3, result)

    ranking = reporter.get_ranking_por_curso(result)

    return {
        "candidates":         candidates,
        "result":             result,
        "ranking":            ranking,
        "kpi1":               kpi1,
        "kpi2":               kpi2,
        "kpi3":               kpi3,
        "kpi_metadata":       KPI_METADATA,
        "feature_importance": scorer.feature_importance_,
    }
