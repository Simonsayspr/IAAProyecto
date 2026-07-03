"""
Asignacion optima de ayudantes mediante Programacion Lineal Entera (ILP).

Maximiza el score total de las asignaciones respetando la cobertura requerida
por seccion y la carga maxima de ayudantias por alumno.
"""

from typing import Dict

import pandas as pd
import pulp

from backend.pipeline.constants import MAX_SIMULTANEOUS_ASSISTANTSHIPS


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
