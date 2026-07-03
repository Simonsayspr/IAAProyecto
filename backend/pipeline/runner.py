"""
Funciones de orquestacion del pipeline (API publica consumida por la API REST).

run_pipeline_deterministic — cruce de datos, filtros y score ponderado (sin ML).
run_pipeline_ai            — XGBoost + ILP + KPIs sobre candidatos ya armados.
"""

from typing import Dict, List, Optional

import pandas as pd

from backend.pipeline.constants import (
    DETERMINISTIC_SCORE_WEIGHTS,
    MINIMUM_GRADE_TO_BE_ELIGIBLE,
    WEIGHT_PRESETS,
)
from backend.pipeline.candidate_builder import EligibleCandidateBuilder
from backend.pipeline.scoring import compute_deterministic_score
from backend.pipeline.scorer import XGBoostScorer
from backend.pipeline.optimizer import AssignmentOptimizer
from backend.pipeline.kpi_reporter import KPIReporter


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
    Aplica XGBoost + ILP sobre candidatos ya procesados.

    Retorna {result, kpi1, kpi2, kpi3, feature_importance}.
    """
    print("\n--- Pipeline IA (RF + ILP) ---")
    if candidates.empty:
        return {}

    scorer = XGBoostScorer()
    scorer.train(candidates)
    scores = scorer.score(candidates)

    optimizer = AssignmentOptimizer()
    result = optimizer.optimize(candidates, scores)

    reporter = KPIReporter()
    kpi1 = reporter.compute_predictive_capability_kpi(scorer.f1_score_)
    kpi2 = reporter.compute_renewal_rate_kpi(result)
    kpi3 = reporter.compute_constraint_coverage_kpi(result)
    reporter.print_report(kpi1, kpi2, kpi3, result)

    return {
        "result":             result,
        "kpi1":               kpi1,
        "kpi2":               kpi2,
        "kpi3":               kpi3,
        "feature_importance": scorer.feature_importance_,
    }
