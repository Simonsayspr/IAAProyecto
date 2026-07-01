"""
Score deterministico (sin Machine Learning).

Normaliza cada variable del candidato a [0, 1] y aplica los pesos configurados
para producir un puntaje de aptitud comparable entre alumnos.
"""

from typing import Dict, Optional

import pandas as pd

from backend.pipeline.constants import DETERMINISTIC_SCORE_WEIGHTS


def compute_deterministic_score(
    candidates: pd.DataFrame,
    weights: Optional[Dict[str, float]] = None,
) -> pd.Series:
    """Calcula un score ponderado [0,1] normalizando cada variable y aplicando pesos."""
    index = candidates.index
    weights = weights or DETERMINISTIC_SCORE_WEIGHTS

    grade_normalized = (
        candidates.get("NOTA_RAMO", pd.Series(0.0, index=index)).fillna(0) / 7.0
    )
    gpa_normalized = (
        candidates.get("PGA", pd.Series(0.0, index=index)).fillna(0) / 7.0
    )
    experience_normalized = (
        candidates.get("N_VECES_AYUDANTE", pd.Series(0, index=index))
        .fillna(0).clip(0, 4) / 4.0
    )
    curriculum_progress = (
        candidates.get("AVANCE_MALLA", pd.Series(0.0, index=index))
        .fillna(0).clip(0, 1)
    )
    # Carga baja = mejor (invertir: 0 ramos=1.0, 8+ ramos=0.0)
    current_load = candidates.get("CARGA_ACTUAL", pd.Series(0, index=index)).fillna(0)
    load_availability = (1.0 - current_load.clip(0, 8) / 8.0)
    is_current_applicant = (
        candidates.get("POSTULANTE_ACTUAL", pd.Series(False, index=index))
        .fillna(False).astype(float)
    )

    return (
        weights.get("NOTA_RAMO", 0)           * grade_normalized
        + weights.get("PGA", 0)               * gpa_normalized
        + weights.get("N_VECES_AYUDANTE", 0)  * experience_normalized
        + weights.get("AVANCE_MALLA", 0)      * curriculum_progress
        + weights.get("CARGA_ACTUAL", 0)      * load_availability
        + weights.get("POSTULANTE_ACTUAL", 0) * is_current_applicant
    ).clip(0.0, 1.0)
