"""
Construccion de la tabla de candidatos elegibles (alumno x seccion).

Cruza historial academico, promedios, ramos inscritos, NRC del periodo y
postulaciones para producir los pares (alumno, seccion) que cumplen los filtros
deterministicos: aprobo la asignatura, no la esta cursando y no tiene conflicto
de horario.
"""

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from backend.pipeline.constants import (
    MINIMUM_GRADE_TO_BE_ELIGIBLE,
    STUDENTS_PER_TEACHING_ASSISTANT,
    WEEKDAYS,
)
from backend.pipeline.column_normalizer import ColumnNormalizer
from backend.pipeline.schedule_analyzer import ScheduleAnalyzer
from backend.pipeline.experience_analyzer import ExperienceAnalyzer
from backend.pipeline.curriculum_catalog import CurriculumCatalogProcessor


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
        for column_name in ["CARRERA", "PROGRAMA", "ESCUELA"]:
            if column_name in students.columns:
                students["CARRERA"] = (
                    students[column_name].fillna("").astype(str).str.strip()
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

        # Si el nombre viene vacío, dejar vacío (no generar nombre sintético)
        if "NOMBRE_COMPLETO" in students.columns:
            students["NOMBRE_COMPLETO"] = students["NOMBRE_COMPLETO"].fillna("").str.strip()

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

        Requiere que la fila tenga NRC (confirma inscripcion real, no solo
        plan curricular) y nota valida. Una fila por (RUT, MATERIA, CURSO)
        con la nota mas alta obtenida.
        """
        df = self._normalizer.normalize_columns(curriculum_df)
        df["NOTA"] = self._normalizer.parse_european_decimal(df["NOTA"])

        # NRC no nulo → el alumno se inscribió en una sección real
        if "NRC" in df.columns:
            _invalid_nrc = {"", "NAN", "NONE", "0"}
            nrc_str = df["NRC"].astype(str).str.strip().str.upper()
            passed_mask = nrc_str.notna() & ~nrc_str.isin(_invalid_nrc)
        else:
            passed_mask = pd.Series([True] * len(df), index=df.index)

        # Nota aprobatoria
        passed_mask &= df["NOTA"].notna() & (df["NOTA"] >= self.minimum_grade)

        # Origen válido
        if "ORIGEN" in df.columns:
            valid_origins = {"H", "OE", "TR"}
            passed_mask &= df["ORIGEN"].str.strip().str.upper().isin(valid_origins)

        n_total = len(df)
        approved = df[passed_mask][["RUT", "MATERIA", "CURSO", "NOTA"]].copy()
        approved = (
            approved.sort_values("NOTA", ascending=False)
            .groupby(["RUT", "MATERIA", "CURSO"], as_index=False)
            .first()
        )
        has_nrc = "NRC" in df.columns
        print(
            f"  [Malla] {n_total} filas → {passed_mask.sum()} con NRC+nota válidos"
            f"{'' if has_nrc else ' (sin columna NRC: solo nota+origen)'}"
            f" → {len(approved)} pares únicos (RUT, MATERIA, CURSO)"
        )
        return approved.reset_index(drop=True)

    # -- Cursos que necesitan ayudantes --

    def get_courses_needing_assistants(
        self, nrc_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Retorna secciones activas del periodo que necesitan ayudantes.

        Colapsa multiples filas por NRC (CLAS/LAB/AYUD) a una fila unica.
        Calcula AYUDANTES_REQUERIDOS = ceil(INSCRITOS / ratio), minimo 1.
        No hay tope superior por seccion: el limite de 3 aplica por alumno
        (carga maxima de ayudantias), no por curso.
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
        active_courses["AYUDANTES_REQUERIDOS"] = (
            np.ceil(active_courses["INSCRITOS"] / STUDENTS_PER_TEACHING_ASSISTANT)
            .clip(lower=1).astype(int)
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
    ) -> pd.DataFrame:
        """Infiere candidatos para cursos nuevos y los prepara con features.

        Fuente primaria de cursos nuevos: las claves de prerequisites_map
        (hoja 'Nueva Malla - Requisitos'). Se complementa con los cursos del
        catalogo 'Periodo' que aún no tienen historial en la malla, para cubrir
        el caso en que no se cargó la hoja de requisitos.
        """
        # Cursos con requisitos explícitos (hoja "Nueva Malla - Requisitos")
        prereq_rows = []
        for course_key in prerequisites_map:
            parts = course_key.split("-", 1)
            if len(parts) == 2:
                prereq_rows.append({"MATERIA": parts[0], "CURSO": parts[1]})
        new_from_prereqs = pd.DataFrame(prereq_rows) if prereq_rows else pd.DataFrame()

        # Cursos del catálogo sin historial (hoja "Periodo", puede estar vacía)
        new_from_catalog = self._curriculum_processor.find_new_courses_without_history(
            catalog_df, curriculum_df,
        )

        # Unión de ambas fuentes
        new_courses = pd.concat(
            [new_from_prereqs, new_from_catalog[["MATERIA", "CURSO"]] if not new_from_catalog.empty else pd.DataFrame()],
            ignore_index=True,
        ).drop_duplicates(subset=["MATERIA", "CURSO"])

        if new_courses.empty:
            return pd.DataFrame()

        # Solo los que están en el NRC del período actual
        new_in_nrc = new_courses.merge(
            courses_needing_ta[["MATERIA", "CURSO", "NRC", "TITULO",
                                "INSCRITOS", "AYUDANTES_REQUERIDOS"]],
            on=["MATERIA", "CURSO"],
            how="inner",
        )
        if new_in_nrc.empty:
            print(f"  [Cursos nuevos] {len(new_courses)} en prereqs, ninguno está en el NRC actual")
            return pd.DataFrame()
        print(f"  [Cursos nuevos] {len(new_in_nrc)} cursos con prereqs presentes en NRC")

        # Enriquecer el catálogo de títulos con los del NRC actual para que
        # los prerrequisitos que no están en "Periodo" también se resuelvan.
        nrc_titles = courses_needing_ta[["MATERIA", "CURSO", "TITULO"]].dropna(subset=["TITULO"])
        augmented_catalog = pd.concat(
            [catalog_df, nrc_titles], ignore_index=True,
        ).drop_duplicates(subset=["MATERIA", "CURSO"])

        inferred = self._curriculum_processor.infer_candidates_for_new_courses(
            new_in_nrc, prerequisites_map, augmented_catalog,
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

        # Agregar PGA y nombre completo desde la hoja de promedios
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

        student_grades_norm["NOMBRE_COMPLETO"] = self._normalizer.extract_full_name(
            student_grades_norm,
        )
        inferred = inferred.merge(
            student_grades_norm[["RUT", "NOMBRE_COMPLETO"]], on="RUT", how="left",
        )
        inferred["NOMBRE_COMPLETO"] = inferred["NOMBRE_COMPLETO"].fillna("")

        # Defaults para campos faltantes
        for column, default in [
            ("CARGA_ACTUAL", 0), ("DISPONIBLE", 1),
            ("AVANCE_MALLA", 0.0), ("N_VECES_AYUDANTE", 0),
            ("EXPERIENCIA_PREVIA", False), ("POSTULANTE_ACTUAL", False),
        ]:
            if column not in inferred.columns:
                inferred[column] = default

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

        candidates["NOMBRE_COMPLETO"] = candidates["NOMBRE_COMPLETO"].fillna("").str.strip()
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
                    "PROM_EVAL_PREVIA", "POSTULANTE_ACTUAL",
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

        # Estado de postulacion del periodo actual por (RUT, MATERIA, CURSO)
        application_status = self._experience_analyzer.build_current_application_status(
            applications_df, current_period,
        )
        if not application_status.empty:
            # Campos por ramo: solo se rellenan cuando el alumno postuló exactamente a ese curso
            per_course_cols = ["RUT", "MATERIA", "CURSO",
                               "ESTADO_POSTULACION", "TIPO_AYUDANTE_POST", "PROFESOR_POST"]
            candidates = candidates.merge(
                application_status[[c for c in per_course_cols if c in application_status.columns]],
                on=["RUT", "MATERIA", "CURSO"],
                how="left",
            )
            # N_ACEPTADAS_ACTUAL es una propiedad del alumno (no del ramo):
            # se une por RUT para que aparezca en TODOS sus registros candidato.
            n_aceptadas_by_rut = (
                application_status[["RUT", "N_ACEPTADAS_ACTUAL"]]
                .drop_duplicates("RUT")
            )
            candidates = candidates.merge(n_aceptadas_by_rut, on="RUT", how="left")

        for column, default in [
            ("ESTADO_POSTULACION", ""),
            ("TIPO_AYUDANTE_POST", ""),
            ("PROFESOR_POST", ""),
            ("N_ACEPTADAS_ACTUAL", 0),
        ]:
            if column not in candidates.columns:
                candidates[column] = default
            else:
                candidates[column] = candidates[column].fillna(default)
        candidates["N_ACEPTADAS_ACTUAL"] = (
            candidates["N_ACEPTADAS_ACTUAL"].fillna(0).astype(int)
        )

        return candidates
