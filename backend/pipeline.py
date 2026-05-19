"""
Pipeline MVP - Asignación de Ayudantes
Facultad de Ingeniería, Universidad de los Andes

Etapas:
1. Cruce y filtrado de datos (DataProcessor)
2. Modelo predictivo RF/XGBoost   (CandidateScorer) → KPI F1-Score
3. Optimización ILP               (AssignmentOptimizer) → KPI Cobertura Restricciones
4. Reporte de KPIs                (KPIReporter)
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

# ── Parámetros globales ────────────────────────────────────────────────────────
NOTA_MINIMA_ELEGIBLE = 5.0       # nota mínima para postular como ayudante
NOTA_BUEN_DESEMPENO = 5.5        # umbral que define "buen ayudante" (label=1)
MAX_AYUDANTIAS_POR_ALUMNO = 2    # carga máxima simultánea como ayudante
ALUMNOS_POR_AYUDANTE = 25        # ratio para calcular ayudantes requeridos por curso
RANDOM_STATE = 42

# Pesos del score determinístico (sin ML)
PESOS_SCORE = {
    "NOTA_RAMO":        0.50,   # nota en el ramo específico (más relevante)
    "PGA":              0.25,   # promedio general acumulado
    "PUA":              0.15,   # promedio último año (reciente)
    "N_VECES_AYUDANTE": 0.08,   # experiencia previa como ayudante
    "POSTULANTE_ACTUAL": 0.02,  # postuló formalmente este período
}


def _compute_deterministic_score(df: pd.DataFrame) -> pd.Series:
    """
    Score ponderado determinístico (sin ML).
    Normaliza cada variable a [0,1] y aplica los pesos de PESOS_SCORE.
    """
    idx = df.index
    nota = df.get("NOTA_RAMO",          pd.Series(0.0, index=idx)).fillna(0) / 7.0
    pga  = df.get("PGA",                pd.Series(0.0, index=idx)).fillna(0) / 7.0
    pua  = df.get("PUA",                pd.Series(0.0, index=idx)).fillna(0) / 7.0
    exp  = df.get("N_VECES_AYUDANTE",   pd.Series(0,   index=idx)).fillna(0).clip(0, 4) / 4.0
    post = df.get("POSTULANTE_ACTUAL",  pd.Series(False, index=idx)).fillna(False).astype(float)
    return (
        PESOS_SCORE["NOTA_RAMO"]        * nota
        + PESOS_SCORE["PGA"]            * pga
        + PESOS_SCORE["PUA"]            * pua
        + PESOS_SCORE["N_VECES_AYUDANTE"] * exp
        + PESOS_SCORE["POSTULANTE_ACTUAL"] * post
    ).clip(0.0, 1.0)


# ── 1. Procesamiento y cruce de datos ─────────────────────────────────────────

class DataProcessor:
    """
    Cruza las 4 fuentes de datos y construye la tabla de candidatos elegibles.

    Fuentes:
      malla_df         → Cumplimiento de Malla Pregrado      (historial académico)
      promedios_df     → Reporte Alumnos con Promedio        (GPA por alumno)
      inscritos_df     → Ramos Inscritos por Periodo 202610  (carga actual)
      nrc_df           → Listado de NRC por Periodo 202610   (cursos + horarios)
      postulaciones_df → Postulaciones de ayudantías         (experiencia + evaluaciones)
    """

    # Estados que indican que la postulación fue efectivamente aceptada
    ESTADOS_ACEPTADO = {"ACEPTADO", "APROBADO", "ACTIVO", "SELECCIONADO"}

    DIAS = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO"]

    def __init__(self, nota_minima: float = NOTA_MINIMA_ELEGIBLE):
        self.nota_minima = nota_minima

    # ── utilidades ──────────────────────────────────────────────────────────

    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normaliza nombres de columnas y limpia RUT."""
        df = df.copy()
        df.columns = [re.sub(r"\s+", " ", str(c).strip().upper()) for c in df.columns]
        if "RUT" in df.columns:
            # Elimina el '.0' si Pandas lo casteó a float, y quita puntos y guiones
            df["RUT"] = (
                df["RUT"]
                .astype(str)
                .str.upper()
                .str.replace(r"\.0$", "", regex=True)
                .str.replace(r"[^0-9K]", "", regex=True)
                .str.strip()
            )
        return df

    def _to_float(self, series: pd.Series) -> pd.Series:
        """Convierte serie a float manejando coma decimal (formato europeo)."""
        return (
            series.astype(str)
            .str.replace(",", ".", regex=False)
            .pipe(pd.to_numeric, errors="coerce")
        )

    def _parse_time_range(self, s: str) -> Optional[Tuple[int, int]]:
        """'13:30 -15:20' → (810, 920) en minutos desde medianoche."""
        match = re.search(r"(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})", str(s))
        if not match:
            return None
        h1, m1, h2, m2 = map(int, match.groups())
        return h1 * 60 + m1, h2 * 60 + m2

    def _slots_from_row(self, row: pd.Series) -> List[Tuple]:
        """Extrae lista de (dia, inicio_min, fin_min) de una fila de NRC."""
        slots = []
        for dia in self.DIAS:
            val = str(row.get(dia, "")).strip()
            if val and val.lower() not in ("nan", "none", ""):
                times = self._parse_time_range(val)
                if times:
                    slots.append((dia, *times))
        return slots

    def _has_conflict(self, slots_a: List[Tuple], slots_b: List[Tuple]) -> bool:
        """True si hay solapamiento entre dos listas de slots."""
        for da, ia, fa in slots_a:
            for db, ib, fb in slots_b:
                if da == db and ia < fb and ib < fa:
                    return True
        return False

    TIPOS_CLASE = {"CLAS", "LAB", "AYUD"}

    def _build_nrc_slots(self, nrc_df: pd.DataFrame) -> Dict[str, List[Tuple]]:
        """
        Retorna {NRC: [(dia, ini_min, fin_min), ...]} considerando solo filas
        TIPO ∈ {CLAS, LAB, AYUD}. Un NRC puede tener varias filas (CLAS + LAB).
        """
        df = self._normalize(nrc_df)
        result: Dict[str, List] = {}
        for _, row in df.iterrows():
            tipo = str(row.get("TIPO", "")).strip().upper()
            if tipo and tipo not in self.TIPOS_CLASE:
                continue
            nrc_key = str(row.get("NRC", "")).strip()
            if not nrc_key:
                continue
            result.setdefault(nrc_key, []).extend(self._slots_from_row(row))
        return result

    # ── utilidad de nombres ──────────────────────────────────────────────────

    @staticmethod
    def _extract_nombre_completo(df: pd.DataFrame) -> pd.Series:
        """Construye NOMBRE_COMPLETO desde columnas PATERNO / MATERNO / NOMBRE."""
        def _col(candidates, names):
            for n in names:
                if n in candidates.columns and candidates[n].fillna("").astype(str).str.strip().ne("").any():
                    return candidates[n].fillna("").astype(str).str.strip()
            return pd.Series("", index=candidates.index)

        pat = _col(df, ["PATERNO", "PRIMER APELLIDO", "APELLIDO PATERNO", "APELLIDO 1", "APELLIDOS"])
        mat = _col(df, ["MATERNO", "SEGUNDO APELLIDO", "APELLIDO MATERNO", "APELLIDO 2"])
        nom = _col(df, ["NOMBRE", "NOMBRES", "PRIMER NOMBRE", "NOMBRE ALUMNO"])
        return (pat + " " + mat + " " + nom).str.replace(r"\s+", " ", regex=True).str.strip()

    # ── métodos principales ──────────────────────────────────────────────────

    def get_students_fast(
        self,
        promedios_df: pd.DataFrame,
        postulaciones_df: Optional[pd.DataFrame] = None,
        periodo_actual: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Retorna rápidamente una tabla de alumnos con info básica.
        Solo necesita promedios + postulaciones (sin malla ni NRC).
        """
        prom = self._normalize(promedios_df)
        prom["RUT"] = prom["RUT"].astype(str).str.strip()
        prom = prom.drop_duplicates(subset=["RUT"], keep="first").copy()

        prom["NOMBRE_COMPLETO"] = self._extract_nombre_completo(prom)

        for src, dst in [
            ("PROMEDIO GENERAL ACUMULADO",  "PGA"),
            ("PROMEDIO ULTIMO AÑO CURSADO", "PUA"),
            ("PROMEDIO RAMOS APROBADOS",    "PROM_APROBADOS"),
        ]:
            if src in prom.columns:
                prom[dst] = self._to_float(prom[src])
            elif dst not in prom.columns:
                prom[dst] = np.nan

        # Carrera / escuela
        for col in ["CARRERA", "PROGRAMA", "ESCUELA"]:
            if col in prom.columns:
                prom["CARRERA"] = prom[col].fillna("").astype(str).str.strip()
                break
        else:
            prom["CARRERA"] = ""

        # Historial de ayudantías — pasar periodo_actual para separar historial de postulación actual
        exp = self.build_experiencia_features(postulaciones_df, periodo_actual)
        exp_cols = [c for c in ["RUT", "N_VECES_AYUDANTE", "EXPERIENCIA_PREVIA"] if c in exp.columns]
        if not exp.empty and exp_cols:
            prom = prom.merge(exp[exp_cols], on="RUT", how="left")
        prom["N_VECES_AYUDANTE"]  = prom.get("N_VECES_AYUDANTE",  pd.Series(0,     index=prom.index)).fillna(0).astype(int)
        prom["EXPERIENCIA_PREVIA"] = prom.get("EXPERIENCIA_PREVIA", pd.Series(False, index=prom.index)).fillna(False)

        keep = ["RUT", "NOMBRE_COMPLETO", "PGA", "PUA", "PROM_APROBADOS",
                "N_VECES_AYUDANTE", "EXPERIENCIA_PREVIA", "CARRERA"]
        return prom[[c for c in keep if c in prom.columns]].reset_index(drop=True)

    def get_historial_aprobados(self, malla_df: pd.DataFrame) -> pd.DataFrame:
        """
        Retorna los cursos aprobados por cada alumno con nota >= nota_minima.
        Una fila por (RUT, MATERIA, CURSO) con la nota más alta obtenida.
        """
        df = self._normalize(malla_df)
        df["NOTA"] = self._to_float(df["NOTA"])

        mask = df["NOTA"] >= self.nota_minima
        if "ORIGEN" in df.columns:
            # ORIGEN='H' = historial; excluye cursos en curso o convalidaciones dudosas
            mask &= df["ORIGEN"].str.strip().str.upper().isin(["H", "OE", "TR"])

        aprobados = df[mask][["RUT", "MATERIA", "CURSO", "NOTA"]].copy()
        aprobados = (
            aprobados.sort_values("NOTA", ascending=False)
            .groupby(["RUT", "MATERIA", "CURSO"], as_index=False)
            .first()
        )
        return aprobados.reset_index(drop=True)

    def get_cursos_necesitan_ta(self, nrc_df: pd.DataFrame) -> pd.DataFrame:
        """
        Retorna secciones del periodo que necesitan ayudantes (STATUS=A).
        Una fila por NRC — el NRC puede tener múltiples filas en la fuente
        (CLAS, LAB, AYUD…); se colapsa a una fila representativa para evitar
        que el join posterior duplique candidatos.
        Calcula AYUDANTES_REQUERIDOS = ceil(INSCRITOS / ALUMNOS_POR_AYUDANTE).
        """
        df = self._normalize(nrc_df)

        mask = pd.Series([True] * len(df), index=df.index)
        if "STATUS" in df.columns:
            mask &= df["STATUS"].str.strip().str.upper() == "A"

        cursos = df[mask].copy()

        # Una fila por NRC (CLAS/LAB/AYUD producen múltiples filas en Banner)
        cursos = cursos.drop_duplicates(subset=["NRC"], keep="first")

        cursos["INSCRITOS"] = self._to_float(cursos.get("INSCRITOS", pd.Series([30] * len(cursos)))).fillna(30)
        cursos["CUPOS"] = self._to_float(cursos.get("CUPOS", pd.Series([40] * len(cursos)))).fillna(40)
        cursos["AYUDANTES_REQUERIDOS"] = (
            np.ceil(cursos["INSCRITOS"] / ALUMNOS_POR_AYUDANTE).clip(1, 3).astype(int)
        )

        keep = ["NRC", "MATERIA", "CURSO", "TITULO", "INSCRITOS", "AYUDANTES_REQUERIDOS"] + self.DIAS
        return cursos[[c for c in keep if c in cursos.columns]].reset_index(drop=True)

    def get_horarios_por_alumno(
        self, inscritos_df: pd.DataFrame, nrc_df: pd.DataFrame
    ) -> Dict[str, List[Tuple]]:
        """
        Retorna {RUT: [(dia, ini_min, fin_min), ...]} con la ocupación horaria
        de cada alumno en el período actual.
        Solo considera filas TIPO ∈ {CLAS, LAB, AYUD} de la planilla NRC.
        """
        ins = self._normalize(inscritos_df)
        nrc = self._normalize(nrc_df)

        # Filtrar solo filas de clase real (excluye EVAL, EXAM, etc.)
        if "TIPO" in nrc.columns:
            nrc = nrc[nrc["TIPO"].str.strip().str.upper().isin(self.TIPOS_CLASE)]

        ins["NRC"] = ins["NRC"].astype(str)
        nrc["NRC"] = nrc["NRC"].astype(str)

        merged = ins[["RUT", "NRC"]].merge(nrc, on="NRC", how="left")
        horarios: Dict[str, List] = {}
        for _, row in merged.iterrows():
            rut = str(row["RUT"])
            slots = self._slots_from_row(row)
            horarios.setdefault(rut, []).extend(slots)
        return horarios

    def build_candidates(
        self,
        malla_df: pd.DataFrame,
        promedios_df: pd.DataFrame,
        inscritos_df: pd.DataFrame,
        nrc_df: pd.DataFrame,
        postulaciones_df: Optional[pd.DataFrame] = None,
        periodo_actual: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Construye la tabla de pares (alumno, NRC_TA) elegibles con sus features.

        Filtros determinísticos aplicados:
          1. Alumno aprobó la asignatura con nota >= nota_minima
          2. Alumno no está cursando esa asignatura actualmente
          3. Alumno no tiene conflicto horario con el horario del curso TA
        """
        aprobados = self.get_historial_aprobados(malla_df)
        cursos_ta = self.get_cursos_necesitan_ta(nrc_df)
        promedios = self._normalize(promedios_df)
        inscritos = self._normalize(inscritos_df)

        # ── Cruce candidato × curso TA ──────────────────────────────────────
        # Un alumno es candidato a un NRC si aprobó esa MATERIA+CURSO
        candidates = aprobados.merge(
            cursos_ta,
            on=["MATERIA", "CURSO"],
            how="inner",
            suffixes=("_HIST", "_TA"),
        ).rename(columns={"NOTA": "NOTA_RAMO"})

        print(f"  Pares elegibles (antes filtros horario/carga): {len(candidates)}")

        # ── Filtro: no puede ser ayudante de algo que está cursando ──────────
        cursando = inscritos[["RUT", "MATERIA", "CURSO"]].copy()
        cursando["RUT"] = cursando["RUT"].astype(str)
        cursando["_CURSANDO"] = True
        candidates = candidates.merge(cursando, on=["RUT", "MATERIA", "CURSO"], how="left")
        candidates = candidates[candidates["_CURSANDO"].isna()].drop(columns=["_CURSANDO"])

        # ── Feature: carga académica actual ─────────────────────────────────
        carga = inscritos.groupby("RUT")["NRC"].count().reset_index()
        carga.columns = ["RUT", "CARGA_ACTUAL"]
        carga["RUT"] = carga["RUT"].astype(str)
        candidates = candidates.merge(carga, on="RUT", how="left")
        candidates["CARGA_ACTUAL"] = candidates["CARGA_ACTUAL"].fillna(0).astype(int)

        # ── Filtro: disponibilidad horaria ───────────────────────────────────
        # Construir slots completos por NRC (CLAS+LAB+AYUD) para chequeo de conflicto
        nrc_slots = self._build_nrc_slots(nrc_df)
        horarios_alumnos = self.get_horarios_por_alumno(inscritos_df, nrc_df)

        candidates["DISPONIBLE"] = candidates.apply(
            lambda row: int(
                not self._has_conflict(
                    nrc_slots.get(str(row["NRC"]), []),
                    horarios_alumnos.get(str(row["RUT"]), []),
                )
            ),
            axis=1,
        )
        candidates = candidates[candidates["DISPONIBLE"] == 1].copy()

        # ── Agregar promedios ────────────────────────────────────────────────
        prom_cols = {
            "PROMEDIO GENERAL ACUMULADO": "PGA",
            "PROMEDIO ULTIMO AÑO CURSADO": "PUA",
            "PROMEDIO RAMOS APROBADOS": "PROM_APROBADOS",
        }
        promedios["RUT"] = promedios["RUT"].astype(str)
        for col in prom_cols:
            if col in promedios.columns:
                promedios[col] = self._to_float(promedios[col])

        # Deduplicar promedios: una fila por RUT (evita multiplicar candidatos si hay repetidos)
        promedios = promedios.drop_duplicates(subset=["RUT"], keep="first")
        # Incluir columnas de nombre junto con los promedios
        name_raw = [c for c in ["PATERNO", "MATERNO", "NOMBRE", "NOMBRES",
                                 "PRIMER APELLIDO", "SEGUNDO APELLIDO"] if c in promedios.columns]
        keep_prom = ["RUT"] + [c for c in prom_cols if c in promedios.columns] + name_raw
        candidates = candidates.merge(promedios[keep_prom], on="RUT", how="left")

        # Renombrar para comodidad
        candidates = candidates.rename(columns=prom_cols)

        # Construir NOMBRE_COMPLETO
        candidates["NOMBRE_COMPLETO"] = self._extract_nombre_completo(candidates)

        # ── Features de experiencia (postulaciones) ──────────────────────────
        exp_df = self.build_experiencia_features(postulaciones_df, periodo_actual)
        if not exp_df.empty and "RUT" in exp_df.columns:
            exp_cols = [c for c in [
                "RUT", "EXPERIENCIA_PREVIA", "N_VECES_AYUDANTE",
                "PROM_EVAL_PREVIA", "ULTIMA_EVAL",
                "POSTULANTE_ACTUAL", "MOTIVACION_SCORE",
            ] if c in exp_df.columns]
            candidates = candidates.merge(exp_df[exp_cols], on="RUT", how="left")
            candidates["EXPERIENCIA_PREVIA"] = candidates["EXPERIENCIA_PREVIA"].fillna(False)
            candidates["N_VECES_AYUDANTE"]   = candidates.get("N_VECES_AYUDANTE", pd.Series(0)).fillna(0).astype(int)
            candidates["POSTULANTE_ACTUAL"]  = candidates.get("POSTULANTE_ACTUAL", pd.Series(False)).fillna(False)
            n_exp = candidates["EXPERIENCIA_PREVIA"].sum()
            n_post = candidates["POSTULANTE_ACTUAL"].sum()
            print(f"  Con experiencia previa: {n_exp} | Postulantes actuales: {n_post}")
        else:
            candidates["EXPERIENCIA_PREVIA"] = False
            candidates["N_VECES_AYUDANTE"]   = 0
            candidates["POSTULANTE_ACTUAL"]  = False

        # Dedup de seguridad: un par (alumno, sección) debe aparecer solo una vez
        before = len(candidates)
        candidates = candidates.drop_duplicates(subset=["RUT", "NRC"]).reset_index(drop=True)
        if len(candidates) < before:
            print(f"  [Dedup] Eliminados {before - len(candidates)} pares duplicados (RUT, NRC)")
        print(f"  Pares elegibles (despues de filtros):          {len(candidates)}")
        return candidates

    def build_experiencia_features(
        self, postulaciones_df: pd.DataFrame, periodo_actual: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Extrae features de experiencia desde la hoja de Postulaciones.

        Columnas esperadas:
          RUT, Periodo, Materia, Curso, Estado, Motivacion,
          Tipo de ayudante, Evaluacion, Asistencia taller

        Retorna DataFrame con una fila por RUT con las columnas:
          EXPERIENCIA_PREVIA   bool  — fue aceptado como ayudante en al menos 1 periodo anterior
          N_VECES_AYUDANTE     int   — cuántas veces fue aceptado (periodos anteriores)
          PROM_EVAL_PREVIA     float — promedio de evaluaciones históricas (escala del sistema)
          ULTIMA_EVAL          float — evaluación más reciente
          POSTULANTE_ACTUAL    bool  — postuló en el periodo actual
          MOTIVACION_SCORE     float — motivación numérica si el campo es numérico, else NaN
        """
        if postulaciones_df is None or postulaciones_df.empty:
            return pd.DataFrame(columns=["RUT"])

        df = self._normalize(postulaciones_df)

        # Mapear nombres de columna posibles (la hoja usa nombres con mayúscula/tilde)
        col_map = {
            "PERIODO":           ["PERIODO"],
            "ESTADO":            ["ESTADO"],
            "EVALUACION":        ["EVALUACION", "EVALUACIÓN"],
            "MOTIVACION":        ["MOTIVACION", "MOTIVACIÓN"],
            "TIPO DE AYUDANTE":  ["TIPO DE AYUDANTE"],
            "ASISTENCIA TALLER": ["ASISTENCIA TALLER"],
            "MATERIA":           ["MATERIA"],
            "CURSO":             ["CURSO"],
        }
        for canonical, variants in col_map.items():
            for v in variants:
                if v in df.columns and canonical not in df.columns:
                    df = df.rename(columns={v: canonical})

        # Convertir EVALUACION y MOTIVACION a numérico
        for col in ["EVALUACION", "MOTIVACION"]:
            if col in df.columns:
                df[col] = self._to_float(df[col])

        # Limpiar PERIODO de posibles '.0' al ser leídos como float
        df["PERIODO"] = df["PERIODO"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
        
        # Limpiar y normalizar ESTADO
        estado_s = df.get("ESTADO", pd.Series(dtype=str)).astype(str).str.strip().str.upper()
        # Eliminar tildes comunes que puedan causar missmatch
        for char, repl in zip("ÁÉÍÓÚ", "AEIOU"):
            estado_s = estado_s.str.replace(char, repl, regex=False)
        df["ESTADO_NORM"] = estado_s
        
        df["FUE_ACEPTADO"] = df["ESTADO_NORM"].isin(self.ESTADOS_ACEPTADO)

        # Separar histórico (periodos anteriores al actual) de actuales
        historico = df.copy()
        actuales = pd.DataFrame()
        if periodo_actual:
            # Consideramos histórico todo periodo anterior, O CUALQUIER postulación que ya esté ACEPTADA
            # (esto resuelve el problema cuando la base de datos solo tiene el periodo actual)
            historico = df[(df["PERIODO"] < str(periodo_actual)) | df["FUE_ACEPTADO"]]
            actuales  = df[df["PERIODO"] == str(periodo_actual)]

        # Agregar por alumno sobre el histórico aceptado
        hist_acep = historico[historico["FUE_ACEPTADO"]]

        exp = (
            hist_acep.sort_values("PERIODO")
            .groupby("RUT")
            .agg(
                N_VECES_AYUDANTE=("PERIODO", "count"),
                PROM_EVAL_PREVIA=("EVALUACION", "mean"),
                ULTIMA_EVAL=("EVALUACION", "last"),
            )
            .reset_index()
        )
        exp["EXPERIENCIA_PREVIA"] = True

        # Postulantes del periodo actual (independiente de si fueron aceptados)
        if not actuales.empty:
            post_actual = actuales[["RUT"]].drop_duplicates().copy()
            post_actual["POSTULANTE_ACTUAL"] = True
            exp = exp.merge(post_actual, on="RUT", how="outer")
        else:
            exp["POSTULANTE_ACTUAL"] = False

        # Motivacion numérica del periodo actual
        if not actuales.empty and "MOTIVACION" in actuales.columns:
            motiv = (
                actuales.groupby("RUT")["MOTIVACION"]
                .mean()
                .reset_index()
                .rename(columns={"MOTIVACION": "MOTIVACION_SCORE"})
            )
            exp = exp.merge(motiv, on="RUT", how="left")

        exp["EXPERIENCIA_PREVIA"]  = exp["EXPERIENCIA_PREVIA"].fillna(False)
        exp["POSTULANTE_ACTUAL"]   = exp["POSTULANTE_ACTUAL"].fillna(False)
        exp["N_VECES_AYUDANTE"]    = exp.get("N_VECES_AYUDANTE", pd.Series(dtype=float)).fillna(0).astype(int)
        exp["RUT"] = exp["RUT"].astype(str)
        return exp


# ── 2. Modelo predictivo ───────────────────────────────────────────────────────

class CandidateScorer:
    """
    Random Forest que estima la probabilidad de que un alumno sea buen ayudante.

    Si existen evaluaciones reales en la hoja de Postulaciones (columna Evaluacion),
    se usan como etiquetas de entrenamiento en lugar de la simulacion por nota.
    """

    FEATURES = [
        "NOTA_RAMO",          # nota del alumno en el ramo específico
        "PGA",                # promedio general acumulado
        "PUA",                # promedio último año (más relevante)
        "PROM_APROBADOS",     # promedio ramos aprobados
        "CARGA_ACTUAL",       # ramos inscritos actualmente
        "N_VECES_AYUDANTE",   # veces que fue ayudante antes
        "PROM_EVAL_PREVIA",   # promedio de evaluaciones históricas
        "POSTULANTE_ACTUAL",  # si postuló formalmente este periodo
    ]

    def __init__(
        self,
        nota_umbral: float = NOTA_BUEN_DESEMPENO,
        random_state: int = RANDOM_STATE,
    ):
        self.nota_umbral = nota_umbral
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
        self._fitted = False

    def _create_labels(self, df: pd.DataFrame) -> pd.Series:
        """
        Etiquetas de entrenamiento.

        Si la columna PROM_EVAL_PREVIA existe con suficientes valores reales,
        se usa como etiqueta real (1 si evaluacion >= umbral).
        En caso contrario, se simula con NOTA_RAMO >= umbral + 5% de ruido.
        """
        eval_col = "PROM_EVAL_PREVIA"
        if eval_col in df.columns:
            real = df[eval_col].dropna()
            if len(real) >= 0.5 * len(df):  # al menos 50% con datos reales
                labels = (df[eval_col] >= self.nota_umbral).fillna(
                    df["NOTA_RAMO"] >= self.nota_umbral
                ).astype(int)
                print("  [Etiquetas] Usando evaluaciones reales de Postulaciones.")
                return labels

        # Fallback: simulacion por nota en el ramo
        np.random.seed(self.random_state)
        labels = (df["NOTA_RAMO"] >= self.nota_umbral).astype(int).copy()
        noise = np.random.rand(len(labels)) < 0.05
        labels[noise] = 1 - labels[noise]
        print("  [Etiquetas] Usando simulacion (NOTA_RAMO >= umbral). "
              "Agregar evaluaciones reales de Postulaciones para mejorar.")
        return labels

    def _get_X(self, df: pd.DataFrame) -> pd.DataFrame:
        available = [c for c in self.FEATURES if c in df.columns]
        X = df[available].copy().fillna(df[available].median())
        return X

    def train(self, candidates_df: pd.DataFrame) -> "CandidateScorer":
        """Entrena el modelo y reporta F1-score en test set (80/20)."""
        X = self._get_X(candidates_df)
        y = self._create_labels(candidates_df)

        if len(candidates_df) < 30:
            print("  [Aviso] Pocos datos para entrenar; se usará scoring heurístico.")
            return self

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.20, random_state=self.random_state, stratify=y
        )
        X_train_sc = self.scaler.fit_transform(X_train)
        X_test_sc = self.scaler.transform(X_test)

        self.model.fit(X_train_sc, y_train)
        y_pred = self.model.predict(X_test_sc)

        self.f1_score_ = f1_score(y_test, y_pred, zero_division=0)
        self.feature_importance_ = pd.Series(
            self.model.feature_importances_, index=X.columns
        ).sort_values(ascending=False)
        self._fitted = True

        print(f"  F1-Score (test set): {self.f1_score_:.4f}")
        print(classification_report(y_test, y_pred, zero_division=0, target_names=["No apto", "Apto"]))
        return self

    def score(self, candidates_df: pd.DataFrame) -> pd.Series:
        """Retorna probabilidad de buen desempeño [0, 1] para cada candidato."""
        X = self._get_X(candidates_df)

        if not self._fitted:
            return _compute_deterministic_score(candidates_df)

        X_sc = self.scaler.transform(X.fillna(X.median()))
        return pd.Series(self.model.predict_proba(X_sc)[:, 1], index=candidates_df.index)


# ── 3. Optimización ILP ────────────────────────────────────────────────────────

class AssignmentOptimizer:
    """
    Problema de asignación generalizada (ILP) resuelto con PuLP/CBC.

    Objetivo:   maximizar Σ score[i,j] · x[i,j]
    Sujeto a:
      R1 - Cobertura:        Σ_i x[i,j] == req_j        ∀ curso j
      R2 - Carga máxima:     Σ_j x[i,j] <= max_ayud     ∀ alumno i
      R3 - Disponibilidad:   ya filtrada en DataProcessor (candidatos elegibles)
      R4 - Requisito acad.:  ya filtrada en DataProcessor (nota >= minima)
    """

    def __init__(self, max_ayudantias: int = MAX_AYUDANTIAS_POR_ALUMNO):
        self.max_ayudantias = max_ayudantias

    def optimize(self, candidates_df: pd.DataFrame, scores: pd.Series) -> pd.DataFrame:
        """
        Retorna candidates_df con columna ASIGNADO (1=asignado, 0=no asignado).
        """
        df = candidates_df.copy()
        df["SCORE"] = scores.values
        df["NRC"] = df["NRC"].astype(str)

        if df.empty:
            df["ASIGNADO"] = 0
            return df

        alumnos = df["RUT"].unique().tolist()
        cursos = df["NRC"].unique().tolist()
        req_ta: Dict[str, int] = (
            df.groupby("NRC")["AYUDANTES_REQUERIDOS"].first().astype(int).to_dict()
        )

        prob = pulp.LpProblem("AsignacionAyudantes", pulp.LpMaximize)

        # Variables de decisión x[(rut, nrc)] ∈ {0, 1}
        x = {
            (str(row["RUT"]), str(row["NRC"])): pulp.LpVariable(
                f"x_{idx}", cat="Binary"
            )
            for idx, row in df.iterrows()
        }

        # Objetivo
        prob += pulp.lpSum(
            row["SCORE"] * x[(str(row["RUT"]), str(row["NRC"]))]
            for _, row in df.iterrows()
        )

        # R1 - Cobertura por curso (igualdad, relajada a <= si no hay suficientes candidatos)
        for nrc in cursos:
            sub = df[df["NRC"] == nrc]
            req = req_ta.get(nrc, 1)
            disponibles = len(sub)
            if disponibles >= req:
                prob += (
                    pulp.lpSum(x[(str(r["RUT"]), nrc)] for _, r in sub.iterrows()) == req,
                    f"cob_{nrc}",
                )
            else:
                prob += (
                    pulp.lpSum(x[(str(r["RUT"]), nrc)] for _, r in sub.iterrows()) <= disponibles,
                    f"cob_{nrc}",
                )

        # R2 - Carga máxima por alumno
        for rut in alumnos:
            sub = df[df["RUT"] == rut]
            prob += (
                pulp.lpSum(x[(rut, str(r["NRC"]))] for _, r in sub.iterrows())
                <= self.max_ayudantias,
                f"carga_{rut}",
            )

        prob.solve(pulp.PULP_CBC_CMD(msg=0))
        status = pulp.LpStatus[prob.status]
        print(f"  Estado del solver: {status}")

        df["ASIGNADO"] = df.apply(
            lambda row: int(round(x[(str(row["RUT"]), str(row["NRC"]))].value() or 0)),
            axis=1,
        )

        n_asig = df["ASIGNADO"].sum()
        n_cursos = df[df["ASIGNADO"] == 1]["NRC"].nunique()
        print(f"  Ayudantes asignados: {n_asig} en {n_cursos} secciones")
        return df


# ── 4. KPIs ────────────────────────────────────────────────────────────────────

# Metadata de cada KPI (formula, variables, baseline, meta) para informe
KPI_METADATA = {
    "kpi1": {
        "nombre": "Capacidad predictiva del modelo de clasificacion",
        "formula": "F1 = 2 * (Precision * Recall) / (Precision + Recall)",
        "variables": {
            "Precision": "VP / (VP + FP)  — predicciones positivas correctas",
            "Recall":    "VP / (VP + FN)  — casos positivos detectados correctamente",
            "VP": "Verdadero Positivo: predijo buen ayudante y lo era",
            "FP": "Falso Positivo: predijo buen ayudante pero no lo fue",
            "FN": "Falso Negativo: descarto a un alumno que si era buen ayudante",
            "Umbral_positivo": f"Nota en la ayudantia >= {NOTA_BUEN_DESEMPENO} (escala 1-7)",
        },
        "baseline": 0.55,
        "baseline_nota": (
            "Seleccion manual subjetiva actual: estimado F1 ~0.55 "
            "(supuesto razonado — sin sistema de evaluacion formal)"
        ),
        "meta": 0.80,
        "interpretacion": "F1 >= 0.80 valida que el modelo predice mejor que el proceso manual",
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
        "meta_nota": "Promedio >= 5.0 en escala 1-7 (equivale a nota 'buena' en el ramo)",
    },
    "kpi3": {
        "nombre": "Tasa de cobertura de restricciones operativas",
        "formula": "KPI3 = R_cumplidas / R_totales   [0, 1]",
        "variables": {
            "R_cumplidas": "Numero de restricciones operativas cumplidas en la asignacion final",
            "R_totales":   "Total de restricciones activas evaluadas en ese proceso",
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
        "meta_nota": "Meta revisada a 0.90 (era 0.97, considerado demasiado elevado para MVP)",
    },
}


class KPIReporter:
    """
    Calcula los 3 KPIs del proyecto con sus formulas corregidas.

    KPI 1 — F1-Score del modelo predictivo
        Mide la capacidad del modelo para identificar correctamente a los buenos
        ayudantes. Usa precision y recall para evitar sesgos por desbalance de clases.

    KPI 2 — Calidad promedio del desempeno de ayudantes
        Formula corregida segun feedback:
        KPI2 = (sum_puntajes_sem_t-1 + sum_puntajes_sem_t) / total_instancias_evaluadas
        Requiere datos de evaluacion de encuestas. Sin datos = pendiente.

    KPI 3 — Tasa de cobertura de restricciones operativas
        KPI3 = R_cumplidas / R_totales  (escala 0-1, no porcentaje)
        Meta revisada: 0.90 (antes 0.97, considerado demasiado elevado).
    """

    @staticmethod
    def kpi1_f1(f1: Optional[float]) -> Dict:
        """
        KPI 1: Capacidad predictiva del modelo.
        Variables: Precision, Recall, VP, FP, FN (ver KPI_METADATA['kpi1']).
        Baseline estimado: 0.55 (seleccion manual subjetiva).
        Meta: F1 >= 0.80.
        """
        meta = KPI_METADATA["kpi1"]
        if f1 is None:
            return {
                "kpi": "KPI1", "valor": None,
                "baseline": meta["baseline"], "meta": meta["meta"],
                "estado": "Sin etiquetas de evaluacion historica disponibles",
            }
        estado = (
            "OPTIMO  (F1 > 0.85)"      if f1 >= 0.85 else
            "SUFICIENTE (0.80-0.85)"   if f1 >= 0.80 else
            "INSUFICIENTE (F1 < 0.80)"
        )
        mejora = round(f1 - meta["baseline"], 4)
        return {
            "kpi": "KPI1",
            "nombre": meta["nombre"],
            "formula": meta["formula"],
            "valor": round(f1, 4),
            "baseline": meta["baseline"],
            "meta": meta["meta"],
            "mejora_vs_baseline": mejora,
            "estado": estado,
        }

    @staticmethod
    def kpi2_calidad_desempeno(
        postulaciones_df: Optional[pd.DataFrame] = None,
    ) -> Dict:
        """
        KPI 2: Calidad promedio del desempeno de ayudantes.

        Formula corregida segun feedback del profesor:
          KPI2 = (Pa1 + Pa2 + ... + Pam + Pa1' + Pa2' + ... + Pan') / (m + n)
          donde Pa_i  = puntaje de ayudantia i en semestre t-1  (m instancias)
                Pa_i' = puntaje de ayudantia i en semestre t    (n instancias)

        Fuente de datos: columna 'Evaluacion' de la hoja de Postulaciones,
        filtrada por Estado = Aceptado en los 2 semestres mas recientes.

        Parameters
        ----------
        postulaciones_df : DataFrame de la hoja de Postulaciones con columnas:
            RUT, Periodo, Estado, Evaluacion
        """
        meta = KPI_METADATA["kpi2"]
        sin_datos = {
            "kpi": "KPI2",
            "nombre": meta["nombre"],
            "formula": meta["formula"],
            "valor": None,
            "baseline": meta["baseline"],
            "meta": meta["meta"],
            "estado": (
                "PENDIENTE — columna 'Evaluacion' en hoja de Postulaciones "
                "es la fuente de este KPI. Completar evaluaciones de ayudantes."
            ),
        }

        if postulaciones_df is None or postulaciones_df.empty:
            return sin_datos

        df = postulaciones_df.copy()
        df.columns = [re.sub(r"\s+", " ", str(c).strip().upper()) for c in df.columns]

        # Normalizar nombres de columna con tilde
        rename = {"EVALUACIÓN": "EVALUACION", "PERÍODO": "PERIODO"}
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

        if "EVALUACION" not in df.columns or "PERIODO" not in df.columns:
            return sin_datos

        # Convertir a numérico
        df["EVALUACION"] = (
            df["EVALUACION"].astype(str).str.replace(",", ".").pipe(pd.to_numeric, errors="coerce")
        )
        df["PERIODO"] = df["PERIODO"].astype(str).str.strip()

        # Filtrar solo ayudantes aceptados con evaluacion registrada
        estados_ok = {"ACEPTADO", "APROBADO", "ACTIVO", "SELECCIONADO"}
        if "ESTADO" in df.columns:
            df = df[df["ESTADO"].astype(str).str.strip().str.upper().isin(estados_ok)]

        df = df.dropna(subset=["EVALUACION"])
        if df.empty:
            return sin_datos

        # Tomar los 2 semestres mas recientes con datos de evaluacion
        semestres = sorted(df["PERIODO"].unique())[-2:]
        subset = df[df["PERIODO"].isin(semestres)]

        # Formula: suma de todos los puntajes / total de instancias evaluadas
        total_puntaje   = subset["EVALUACION"].sum()
        total_instancias = len(subset)
        valor = round(total_puntaje / total_instancias, 4)

        estado = (
            "OPTIMO  (>= 5.5)"    if valor >= 5.5 else
            "SUFICIENTE (>= 5.0)" if valor >= 5.0 else
            "INSUFICIENTE (< 5.0)"
        )
        detalle_por_semestre = {
            sem: round(subset[subset["PERIODO"] == sem]["EVALUACION"].mean(), 4)
            for sem in semestres
        }
        return {
            "kpi": "KPI2",
            "nombre": meta["nombre"],
            "formula": meta["formula"],
            "valor": valor,
            "semestres_usados": semestres,
            "total_instancias": total_instancias,
            "promedio_por_semestre": detalle_por_semestre,
            "baseline": meta["baseline"],
            "meta": meta["meta"],
            "estado": estado,
        }

    @staticmethod
    def kpi3_cobertura_restricciones(result_df: pd.DataFrame) -> Dict:
        """
        KPI 3: Tasa de cobertura de restricciones operativas.

        Formula: KPI3 = R_cumplidas / R_totales  (escala 0-1)
        Meta revisada: 0.90 (feedback: 0.97 era demasiado elevado para un MVP).
        Baseline estimado: 0.70 (proceso manual actual).

        Restricciones evaluadas (binario por asignacion):
          R1 - Disponibilidad horaria (garantizada en DataProcessor)
          R2 - Carga maxima por alumno (<= MAX_AYUDANTIAS_POR_ALUMNO)
          R3 - Requisito academico (NOTA_RAMO >= NOTA_MINIMA_ELEGIBLE)
          R4 - Cobertura del curso (recibe exactamente los ayudantes requeridos)
        """
        meta = KPI_METADATA["kpi3"]
        asig = result_df[result_df["ASIGNADO"] == 1]

        # R1 — disponibilidad horaria (todos los asignados pasaron el filtro)
        r1_ok, r1_total = len(asig), len(asig)

        # R3 — requisito academico
        r3_ok = int((asig["NOTA_RAMO"] >= NOTA_MINIMA_ELEGIBLE).sum())
        r3_total = len(asig)

        # R4 — cobertura del curso
        req_por_nrc = result_df.groupby("NRC")["AYUDANTES_REQUERIDOS"].first().astype(int)
        asig_por_nrc = asig.groupby("NRC")["ASIGNADO"].sum()
        r4_ok = int(sum(asig_por_nrc.get(nrc, 0) >= req for nrc, req in req_por_nrc.items()))
        r4_total = len(req_por_nrc)

        # R2 — carga maxima por alumno
        carga = asig.groupby("RUT")["ASIGNADO"].sum()
        r2_ok = int((carga <= MAX_AYUDANTIAS_POR_ALUMNO).sum())
        r2_total = len(carga)

        r_cumplidas = r1_ok + r2_ok + r3_ok + r4_ok
        r_totales   = r1_total + r2_total + r3_total + r4_total
        tasa = r_cumplidas / max(r_totales, 1)

        estado = (
            "OPTIMO  (>= 0.90)"       if tasa >= 0.90 else
            "SUFICIENTE (0.80-0.89)"  if tasa >= 0.80 else
            "INSUFICIENTE (< 0.80)"
        )
        mejora = round(tasa - meta["baseline"], 4)
        return {
            "kpi": "KPI3",
            "nombre": meta["nombre"],
            "formula": meta["formula"],
            "valor": round(tasa, 4),          # tasa decimal 0-1, NO porcentaje
            "baseline": meta["baseline"],
            "meta": meta["meta"],
            "mejora_vs_baseline": mejora,
            "estado": estado,
            "detalle": {
                "R1_disponibilidad_horaria": f"{r1_ok}/{r1_total}",
                "R2_carga_maxima_alumno":    f"{r2_ok}/{r2_total}",
                "R3_requisito_academico":    f"{r3_ok}/{r3_total}",
                "R4_cobertura_curso":        f"{r4_ok}/{r4_total}",
            },
        }

    def print_report(
        self,
        kpi1: Dict,
        kpi2: Dict,
        kpi3: Dict,
        result_df: pd.DataFrame,
    ) -> None:
        asig = result_df[result_df["ASIGNADO"] == 1]
        sep = "=" * 64

        print(f"\n{sep}")
        print("        REPORTE DE KPIs — MVP GESTION DE AYUDANTES")
        print(sep)

        # KPI 1
        print("\n[KPI 1] Capacidad predictiva del modelo (F1-Score)")
        print(f"  Formula : F1 = 2*(Precision*Recall)/(Precision+Recall)")
        v1 = kpi1.get("valor")
        print(f"  Valor   : {f'{v1:.4f}' if v1 is not None else 'N/A'}")
        print(f"  Baseline: {kpi1.get('baseline', 'N/A')} (seleccion manual estimada)")
        print(f"  Meta    : >= {kpi1.get('meta', 0.80)}")
        if kpi1.get("mejora_vs_baseline") is not None:
            print(f"  Mejora  : {kpi1['mejora_vs_baseline']:+.4f} vs baseline")
        print(f"  Estado  : {kpi1.get('estado', 'N/A')}")

        # KPI 2
        print("\n[KPI 2] Calidad promedio del desempeno de ayudantes")
        print(f"  Formula : KPI2 = (SumPa_sem1 + SumPa_sem2) / total_instancias")
        v2 = kpi2.get("valor")
        print(f"  Valor   : {f'{v2:.4f} / 7.0' if v2 is not None else 'N/A'}")
        print(f"  Meta    : >= {kpi2.get('meta', 5.0)} (escala 1-7)")
        print(f"  Estado  : {kpi2.get('estado', 'N/A')}")

        # KPI 3
        print("\n[KPI 3] Tasa de cobertura de restricciones [0-1]")
        print(f"  Formula : KPI3 = R_cumplidas / R_totales")
        v3 = kpi3.get("valor")
        print(f"  Valor   : {f'{v3:.4f}' if v3 is not None else 'N/A'}")
        print(f"  Baseline: {kpi3.get('baseline', 'N/A')} (proceso manual estimado)")
        print(f"  Meta    : >= {kpi3.get('meta', 0.90)}")
        if kpi3.get("mejora_vs_baseline") is not None:
            print(f"  Mejora  : {kpi3['mejora_vs_baseline']:+.4f} vs baseline")
        print(f"  Estado  : {kpi3.get('estado', 'N/A')}")
        for k, v in kpi3.get("detalle", {}).items():
            print(f"    {k:32s}: {v}")

        # Resumen asignacion
        print("\n[ASIGNACION FINAL]")
        print(f"  Ayudantes asignados : {len(asig)}")
        print(f"  Secciones cubiertas : {asig['NRC'].nunique()}")
        if not asig.empty:
            print(f"  Score promedio      : {asig['SCORE'].mean():.4f}")
            print(f"  Nota media en ramo  : {asig['NOTA_RAMO'].mean():.2f}")
        print(sep + "\n")

    def get_ranking_por_curso(self, result_df: pd.DataFrame) -> pd.DataFrame:
        """Ranking completo de candidatos por sección, asignados primero."""
        cols = ["NRC", "MATERIA", "CURSO", "RUT", "NOTA_RAMO", "PGA", "SCORE", "ASIGNADO"]
        cols = [c for c in cols if c in result_df.columns]
        return (
            result_df[cols]
            .sort_values(["NRC", "ASIGNADO", "SCORE"], ascending=[True, False, False])
            .reset_index(drop=True)
        )


# ── 5. Pipeline completo ───────────────────────────────────────────────────────

def run_pipeline_deterministic(
    malla_df: pd.DataFrame,
    promedios_df: pd.DataFrame,
    inscritos_df: pd.DataFrame,
    nrc_df: pd.DataFrame,
    postulaciones_df: Optional[pd.DataFrame] = None,
    periodo_actual: Optional[str] = None,
    nota_minima: float = NOTA_MINIMA_ELEGIBLE,
) -> Dict:
    """
    Etapa determinística únicamente: cruce de datos, filtros académicos y
    horarios, score ponderado. Sin ML ni ILP.

    Retorna:
      candidates  → tabla de candidatos con SCORE determinístico y ASIGNADO=0
    """
    print("\n--- Pipeline determinístico (sin ML) ---")
    processor = DataProcessor(nota_minima=nota_minima)
    candidates = processor.build_candidates(
        malla_df, promedios_df, inscritos_df, nrc_df,
        postulaciones_df=postulaciones_df,
        periodo_actual=periodo_actual,
    )
    if candidates.empty:
        print("[ERROR] No hay candidatos elegibles.")
        return {}

    candidates["SCORE"]    = _compute_deterministic_score(candidates)
    candidates["ASIGNADO"] = 0
    print(f"  Score determinístico: media={candidates['SCORE'].mean():.4f}  pesos={PESOS_SCORE}")
    return {"candidates": candidates}


def run_pipeline_ai(
    candidates: pd.DataFrame,
    postulaciones_df: Optional[pd.DataFrame] = None,
) -> Dict:
    """
    Aplica RF + ILP sobre los candidatos ya procesados (resultado de
    run_pipeline_deterministic). No vuelve a cargar planillas.

    Retorna:
      result             → candidates + SCORE (RF) + ASIGNADO (ILP)
      kpi1, kpi2, kpi3   → KPIs del proyecto
      feature_importance → importancia de variables del RF
    """
    print("\n--- Pipeline IA (RF + ILP) ---")
    if candidates.empty:
        return {}

    scorer = CandidateScorer()
    scorer.train(candidates)
    scores = scorer.score(candidates)

    optimizer = AssignmentOptimizer()
    result = optimizer.optimize(candidates, scores)

    reporter = KPIReporter()
    kpi1 = reporter.kpi1_f1(scorer.f1_score_)
    kpi2 = reporter.kpi2_calidad_desempeno(postulaciones_df)
    kpi3 = reporter.kpi3_cobertura_restricciones(result)
    reporter.print_report(kpi1, kpi2, kpi3, result)

    return {
        "result":              result,
        "kpi1":                kpi1,
        "kpi2":                kpi2,
        "kpi3":                kpi3,
        "feature_importance":  scorer.feature_importance_,
    }


def run_pipeline(
    malla_df: pd.DataFrame,
    promedios_df: pd.DataFrame,
    inscritos_df: pd.DataFrame,
    nrc_df: pd.DataFrame,
    postulaciones_df: Optional[pd.DataFrame] = None,
    periodo_actual: Optional[str] = None,
    nota_minima: float = NOTA_MINIMA_ELEGIBLE,
) -> Dict:
    """
    Ejecuta el pipeline completo.

    Retorna diccionario con:
      candidates        → tabla de candidatos elegibles con features
      result            → candidates + columna ASIGNADO
      ranking           → ranking por sección
      kpi_f1            → resultado KPI 1
      kpi_cobertura     → resultado KPI 2
      kpi_evolucion     → resultado KPI 3
      feature_importance→ importancia de variables del RF
    """
    print("\n--- Etapa 1: Cruce y filtrado de datos ---")
    processor = DataProcessor(nota_minima=nota_minima)
    candidates = processor.build_candidates(
        malla_df, promedios_df, inscritos_df, nrc_df,
        postulaciones_df=postulaciones_df,
        periodo_actual=periodo_actual,
    )

    if candidates.empty:
        print("[ERROR] No hay candidatos elegibles. Revisa los datos de entrada.")
        return {}

    print("\n--- Etapa 2: Modelo predictivo (Random Forest) ---")
    scorer = CandidateScorer()
    scorer.train(candidates)
    scores = scorer.score(candidates)

    print("\n--- Etapa 3: Optimizacion ILP ---")
    optimizer = AssignmentOptimizer()
    result = optimizer.optimize(candidates, scores)

    print("\n--- Etapa 4: KPIs ---")
    reporter = KPIReporter()
    kpi1 = reporter.kpi1_f1(scorer.f1_score_)
    kpi2 = reporter.kpi2_calidad_desempeno(postulaciones_df)
    kpi3 = reporter.kpi3_cobertura_restricciones(result)
    reporter.print_report(kpi1, kpi2, kpi3, result)

    ranking = reporter.get_ranking_por_curso(result)

    return {
        "candidates": candidates,
        "result": result,
        "ranking": ranking,
        "kpi1": kpi1,
        "kpi2": kpi2,
        "kpi3": kpi3,
        "kpi_metadata": KPI_METADATA,
        "feature_importance": scorer.feature_importance_,
    }
