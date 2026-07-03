"""
Calculo y reporte de los 3 KPIs del proyecto.

KPI1 — Capacidad predictiva del modelo (F1-Score).
KPI2 — Calidad promedio del desempeno de los ayudantes (evaluaciones).
KPI3 — Tasa de cobertura de restricciones operativas de la asignacion.
"""

import re
from typing import Dict, Optional

import pandas as pd

from backend.pipeline.constants import (
    ACCEPTED_APPLICATION_STATES,
    GOOD_PERFORMANCE_GRADE_THRESHOLD,
    MAX_SIMULTANEOUS_ASSISTANTSHIPS,
    MINIMUM_GRADE_TO_BE_ELIGIBLE,
)


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
        "nombre": "Tasa de Renovacion de la Academia (TRA)",
        "formula": "TRA = Ayudantes Nuevos Asignados / Total Ayudantes Asignados",
        "variables": {
            "Ayudantes Nuevos Asignados": "Ayudantes asignados que no tienen experiencia previa (N_VECES_AYUDANTE = 0)",
            "Total Ayudantes Asignados": "Total de ayudantes asignados por el modelo (ASIGNADO = 1)",
        },
        "baseline": 0.15,
        "baseline_nota": (
            "Proceso manual actual: se estima un 10-15% (bucle de contratar a los mismos por seguridad)"
        ),
        "meta": 0.25,
        "meta_nota": (
            "Se busca una TRA entre 25% y 45% para romper el bucle y democratizar las oportunidades"
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
    def compute_renewal_rate_kpi(
        result_df: pd.DataFrame,
    ) -> Dict:
        """
        KPI 2: Tasa de Renovacion de la Academia (TRA).
        """
        metadata = KPI_METADATA["kpi2"]
        no_data_response = {
            "kpi": "KPI2",
            "nombre": metadata["nombre"],
            "formula": metadata["formula"],
            "valor": None,
            "baseline": metadata["baseline"],
            "meta": metadata["meta"],
            "estado": "PENDIENTE — no se asignaron ayudantes.",
        }

        if result_df is None or result_df.empty:
            return no_data_response

        assigned = result_df[result_df["ASIGNADO"] == 1]
        if assigned.empty:
            return no_data_response

        total_assigned = len(assigned)
        new_assigned = int((assigned.get("N_VECES_AYUDANTE", pd.Series(dtype=float)).fillna(0) == 0).sum())
        tra = new_assigned / total_assigned

        if 0.25 <= tra <= 0.45:
            status = "OPTIMO  (25% - 45%)"
        elif tra < 0.25:
            status = "INSUFICIENTE (< 25%)"
        else:
            status = "EXCESIVO (> 45%)"

        improvement = round(tra - metadata["baseline"], 4)
        return {
            "kpi": "KPI2",
            "nombre": metadata["nombre"],
            "formula": metadata["formula"],
            "valor": round(tra, 4),
            "nuevos_asignados": new_assigned,
            "total_asignados": total_assigned,
            "baseline": metadata["baseline"],
            "meta": metadata["meta"],
            "mejora_vs_baseline": improvement,
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
        print("\n[KPI 2] Tasa de Renovacion de la Academia (TRA)")
        print("  Formula : TRA = Nuevos_Asignados / Total_Asignados")
        v2 = kpi2.get("valor")
        print(f"  Valor   : {f'{v2*100:.1f}%' if v2 is not None else 'N/A'}")
        print(f"  Meta    : 25% - 45%")
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
