"""
Modelo predictivo (XGBoost) para estimar la aptitud de un ayudante.

XGBoost es un *gradient boosting* de arboles: entrena arboles de forma
secuencial donde cada uno corrige el error residual del conjunto anterior,
optimizando logloss con regularizacion. A diferencia del Random Forest (que
promedia arboles independientes), suele dar mayor precision en datos tabulares.

Si la hoja de Postulaciones trae evaluaciones reales las usa como etiquetas;
de lo contrario simula etiquetas a partir de la nota del ramo. Cuando hay pocos
datos cae a un score deterministico.
"""

import warnings
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, f1_score
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from backend.pipeline.constants import GOOD_PERFORMANCE_GRADE_THRESHOLD, RANDOM_STATE
from backend.pipeline.scoring import compute_deterministic_score

warnings.filterwarnings("ignore")


class XGBoostScorer:
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
        # Arboles poco profundos + learning rate bajo para no sobreajustar en un
        # dataset chico/mediano. XGBoost maneja NaN nativamente y no requiere escalar.
        self.model = XGBClassifier(
            n_estimators=300,
            learning_rate=0.05,
            max_depth=4,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            objective="binary:logistic",
            eval_metric="logloss",
            random_state=random_state,
            n_jobs=-1,
        )
        self.f1_score_: Optional[float] = None
        self.feature_importance_: Optional[pd.Series] = None
        self._is_trained = False

    def generate_training_labels(self, candidates: pd.DataFrame) -> pd.Series:
        """
        Genera etiquetas de entrenamiento.

        Prioriza evaluaciones reales (PROM_EVAL_PREVIA) si hay suficientes datos.
        Fallback: simula con NOTA_RAMO >= umbral + 5% de ruido.
        """
        evaluation_column = "PROM_EVAL_PREVIA"
        if evaluation_column in candidates.columns:
            real_evaluations = candidates[evaluation_column].dropna()
            has_enough_real_data = len(real_evaluations) >= 0.5 * len(candidates)

            if has_enough_real_data:
                labels = (
                    candidates[evaluation_column] >= self.performance_threshold
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
        """
        Extrae las columnas de features disponibles.

        No se rellenan los NaN: XGBoost aprende la mejor direccion por defecto
        para los valores faltantes.
        """
        available_features = [
            col for col in self.FEATURE_COLUMNS if col in candidates.columns
        ]
        return candidates[available_features].copy()

    @staticmethod
    def _imbalance_weight(labels: pd.Series) -> float:
        """Calcula scale_pos_weight = negativos / positivos para clases desbalanceadas."""
        n_positive = int((labels == 1).sum())
        n_negative = int((labels == 0).sum())
        return n_negative / n_positive if n_positive else 1.0

    def train(self, candidates: pd.DataFrame) -> "XGBoostScorer":
        """Entrena el modelo y calcula F1-score en test set (80/20)."""
        feature_matrix = self._extract_feature_matrix(candidates)
        labels = self.generate_training_labels(candidates)

        if len(candidates) < 30 or labels.nunique() < 2:
            print("  [Aviso] Pocos datos o una sola clase; se usara scoring heuristico.")
            return self

        x_train, x_test, y_train, y_test = train_test_split(
            feature_matrix, labels,
            test_size=0.20,
            random_state=self.random_state,
            stratify=labels,
        )

        self.model.set_params(scale_pos_weight=self._imbalance_weight(y_train))
        self.model.fit(x_train, y_train)
        y_predicted = self.model.predict(x_test)

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
        if not self._is_trained:
            return compute_deterministic_score(candidates)

        feature_matrix = self._extract_feature_matrix(candidates)
        return pd.Series(
            self.model.predict_proba(feature_matrix)[:, 1],
            index=candidates.index,
        )
