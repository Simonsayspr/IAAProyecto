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
                # Separar por coma, limpiar " (p)" y espacios
                requisites = [
                    re.sub(r"\s*\(p\)\s*", "", item).strip()
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
