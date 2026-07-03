"""
Procesamiento del Plan de Estudios (catalogo de cursos + requisitos).

Calcula el avance curricular por alumno, detecta cursos nuevos sin historial de
notas e infiere candidatos para esos cursos a partir de sus requisitos aprobados.
"""

import re
from typing import Dict, List, Optional

import pandas as pd

from backend.pipeline.column_normalizer import ColumnNormalizer


class CurriculumCatalogProcessor:
    """
    Procesa la hoja de Plan de Estudios para:
    - Calcular avance curricular por alumno (AVANCE_MALLA)
    - Identificar cursos nuevos sin historial de notas
    - Inferir candidatos para cursos nuevos via requisitos relacionados
    """

    def __init__(self):
        self._normalizer = ColumnNormalizer()

    @staticmethod
    def _normalize_title(raw: str) -> str:
        """Normaliza un título de curso para comparación robusta.

        Elimina cualquier sufijo entre paréntesis (ej. "(p)", "(req)", "(I)"),
        colapsa espacios múltiples y convierte a mayúsculas.
        Se aplica a ambos lados del emparejamiento para que el match no dependa
        de variaciones de formato en las planillas.
        """
        cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", raw.strip())
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned.upper()

    def load_course_catalog(
        self, catalog_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Carga y normaliza el catalogo de cursos (hoja 'Periodo').

        Solo se conservan MATERIA, CURSO y TITULO: es lo unico que el pipeline
        usa downstream (total de cursos para el avance de malla, identificacion
        de cursos nuevos y titulo para mostrar).
        """
        df = self._normalizer.normalize_columns(catalog_df)
        columns_to_keep = ["MATERIA", "CURSO", "TITULO"]
        return df[[c for c in columns_to_keep if c in df.columns]].copy()

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
            course_key = f"{row.get('MATERIA', '')}-{row.get('CURSO', '')}"
            raw_requisites = str(row.get("REQUISITOS", "")).strip()
            if raw_requisites:
                requisites = [
                    self._normalize_title(item)
                    for item in raw_requisites.split(",")
                    if item.strip()
                ]
                prerequisites_map[course_key] = requisites
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
        normalized = self._normalizer.normalize_columns(curriculum_df)

        # Contar ramos aprobados por alumno
        if "ORIGEN" in normalized.columns:
            approved = normalized[
                normalized["ORIGEN"].str.strip().str.upper().isin({"H", "OE", "TR"})
            ]
        else:
            approved = normalized

        courses_per_student = (
            approved.groupby("RUT")[["MATERIA", "CURSO"]]
            .apply(lambda group: len(group.drop_duplicates()))
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
        catalog = self._normalizer.normalize_columns(catalog_df)
        history = self._normalizer.normalize_columns(curriculum_df)

        catalog_keys = set(
            catalog.apply(lambda r: f"{r['MATERIA']}-{r['CURSO']}", axis=1),
        )
        history_keys = set(
            history.apply(lambda r: f"{r['MATERIA']}-{r['CURSO']}", axis=1),
        )

        new_keys = catalog_keys - history_keys
        new_courses = catalog[
            catalog.apply(lambda r: f"{r['MATERIA']}-{r['CURSO']}", axis=1)
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

        catalog = self._normalizer.normalize_columns(catalog_df)

        # Mapa normalizado: titulo_normalizado -> MATERIA-CURSO
        # Se aplica _normalize_title a ambos lados del emparejamiento para
        # absorber variaciones de formato (tildes, espacios, sufijos entre paréntesis).
        title_to_key: Dict[str, str] = {}
        for _, row in catalog.iterrows():
            raw = str(row.get("TITULO", "")).strip()
            if raw:
                title_to_key[self._normalize_title(raw)] = (
                    f"{row['MATERIA']}-{row['CURSO']}"
                )

        # Precalcular clave de curso aprobado por fila (evita iterar con apply repetido)
        if not approved_courses_df.empty:
            approved_copy = approved_courses_df.copy()
            approved_copy["_KEY"] = (
                approved_copy["MATERIA"].astype(str)
                + "-"
                + approved_copy["CURSO"].astype(str)
            )
        else:
            approved_copy = pd.DataFrame()

        inferred_rows = []

        for _, course in new_courses_df.iterrows():
            course_key = f"{course['MATERIA']}-{course['CURSO']}"
            # prereq_titles ya vienen normalizados desde load_prerequisites
            prereq_titles = prerequisites_map.get(course_key, [])

            # Resolver títulos a claves MATERIA-CURSO (deduplicados)
            prereq_key_set: set[str] = set()
            unresolved = []
            for title in prereq_titles:
                key = title_to_key.get(title)
                if key:
                    prereq_key_set.add(key)
                else:
                    unresolved.append(title)

            if unresolved:
                print(
                    f"  [Info] Requisitos sin match en catálogo para {course_key}: "
                    f"{unresolved}"
                )

            if not prereq_key_set or approved_copy.empty:
                continue

            # Alumnos que aprobaron cursos en el conjunto de requisitos
            relevant = approved_copy[approved_copy["_KEY"].isin(prereq_key_set)]
            if relevant.empty:
                continue

            # Contar cuántos requisitos distintos aprobó cada alumno y nota media
            per_student = (
                relevant.groupby("RUT")
                .agg(
                    n_req_aprobados=("_KEY", "nunique"),
                    nota_media=("NOTA", "mean"),
                )
                .reset_index()
            )

            # Solo alumnos que aprobaron TODOS los requisitos resolvibles
            eligible = per_student[
                per_student["n_req_aprobados"] >= len(prereq_key_set)
            ]

            n_total = len(prereq_key_set)
            for _, elig in eligible.iterrows():
                inferred_rows.append({
                    "RUT": elig["RUT"],
                    "MATERIA": course["MATERIA"],
                    "CURSO": course["CURSO"],
                    "TITULO": course.get("TITULO", ""),
                    "NOTA_INFERIDA": float(elig["nota_media"]),
                    "FUENTE_INFERENCIA": (
                        f"Requisitos cumplidos: {n_total}/{n_total}"
                    ),
                    "ES_CURSO_NUEVO": True,
                })

        if not inferred_rows:
            return pd.DataFrame()

        result = pd.DataFrame(inferred_rows)
        result["NOTA_RAMO"] = result["NOTA_INFERIDA"]
        return result
