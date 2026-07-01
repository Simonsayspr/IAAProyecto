"""
Pipeline de Asignacion de Ayudantes — Facultad de Ingenieria, U. de los Andes.

Cada etapa vive en su propio modulo; este paquete reexporta la API publica que
consume la capa REST (backend/app.py).

Modulos:
    constants            — umbrales, pesos y vocabulario compartido
    column_normalizer    — limpieza y estandarizacion de columnas
    schedule_analyzer    — analisis de horarios y deteccion de conflictos
    experience_analyzer  — features de experiencia previa como ayudante
    curriculum_catalog   — avance curricular e inferencia de cursos nuevos
    candidate_builder    — construccion de la tabla de candidatos elegibles
    scoring              — score deterministico (sin ML)
    scorer               — modelo predictivo XGBoost
    optimizer            — optimizacion ILP de la asignacion
    kpi_reporter         — calculo y reporte de los KPIs
    runner               — funciones de orquestacion del pipeline
"""

from backend.pipeline.constants import (
    DETERMINISTIC_SCORE_WEIGHTS,
    GOOD_PERFORMANCE_GRADE_THRESHOLD,
    MAX_SIMULTANEOUS_ASSISTANTSHIPS,
    MINIMUM_GRADE_TO_BE_ELIGIBLE,
    STUDENTS_PER_TEACHING_ASSISTANT,
    WEIGHT_PRESETS,
    generate_synthetic_name,
)
from backend.pipeline.scoring import compute_deterministic_score
from backend.pipeline.column_normalizer import ColumnNormalizer
from backend.pipeline.schedule_analyzer import ScheduleAnalyzer
from backend.pipeline.experience_analyzer import ExperienceAnalyzer
from backend.pipeline.curriculum_catalog import CurriculumCatalogProcessor
from backend.pipeline.candidate_builder import EligibleCandidateBuilder
from backend.pipeline.scorer import XGBoostScorer
from backend.pipeline.optimizer import AssignmentOptimizer
from backend.pipeline.kpi_reporter import KPI_METADATA, KPIReporter
from backend.pipeline.runner import (
    run_pipeline_ai,
    run_pipeline_deterministic,
)

__all__ = [
    "DETERMINISTIC_SCORE_WEIGHTS",
    "GOOD_PERFORMANCE_GRADE_THRESHOLD",
    "MAX_SIMULTANEOUS_ASSISTANTSHIPS",
    "MINIMUM_GRADE_TO_BE_ELIGIBLE",
    "STUDENTS_PER_TEACHING_ASSISTANT",
    "WEIGHT_PRESETS",
    "KPI_METADATA",
    "generate_synthetic_name",
    "compute_deterministic_score",
    "ColumnNormalizer",
    "ScheduleAnalyzer",
    "ExperienceAnalyzer",
    "CurriculumCatalogProcessor",
    "EligibleCandidateBuilder",
    "XGBoostScorer",
    "AssignmentOptimizer",
    "KPIReporter",
    "run_pipeline_ai",
    "run_pipeline_deterministic",
]
