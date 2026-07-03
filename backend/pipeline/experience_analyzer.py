"""
Features de experiencia como ayudante a partir de la hoja de Postulaciones.

Distingue postulaciones historicas (periodos anteriores o ya aceptadas) de las
del periodo actual, y resume por RUT cuantas veces fue ayudante, su evaluacion
promedio y si esta postulando ahora.
"""

from typing import Optional, Tuple

import pandas as pd

from backend.pipeline.constants import (
    ACCEPTED_APPLICATION_STATES,
    ACTIVE_APPLICATION_STATES,
)
from backend.pipeline.column_normalizer import ColumnNormalizer


class ExperienceAnalyzer:
    """Extrae features de experiencia como ayudante desde la hoja de postulaciones."""

    COLUMN_ALIASES = {
        "PERIODO":           ["PERIODO"],
        "ESTADO":            ["ESTADO"],
        "EVALUACION":        ["EVALUACION", "EVALUACIÓN"],
        "TIPO DE AYUDANTE":  ["TIPO DE AYUDANTE"],
        "MATERIA":           ["MATERIA"],
        "CURSO":             ["CURSO"],
        "PROFESOR":          ["PROFESOR"],
    }

    def __init__(self):
        self._normalizer = ColumnNormalizer()

    def build_experience_features(
        self,
        applications_df: Optional[pd.DataFrame],
        current_period: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Genera features de experiencia por RUT desde postulaciones.

        Columnas retornadas:
            EXPERIENCIA_PREVIA  (bool)  — fue aceptado en al menos 1 periodo anterior
            N_VECES_AYUDANTE   (int)   — cuantas veces fue aceptado historicamente
            PROM_EVAL_PREVIA   (float) — promedio de evaluaciones historicas
            POSTULANTE_ACTUAL  (bool)  — postulacion activa en el periodo actual
        """
        if applications_df is None or applications_df.empty:
            return pd.DataFrame(columns=["RUT"])

        df = self._normalizer.normalize_columns(applications_df)
        df = self._map_column_aliases(df)
        df = self._clean_numeric_columns(df)
        df = self._clean_period_column(df)
        df = self._normalize_application_state(df)
        df["FUE_ACEPTADO"] = df["ESTADO_NORM"].isin(ACCEPTED_APPLICATION_STATES)

        historical_df, current_period_df = self._split_by_period(df, current_period)
        experience_summary = self._aggregate_historical_experience(historical_df)
        experience_summary = self._add_current_applicant_flag(
            experience_summary, current_period_df,
        )

        experience_summary["EXPERIENCIA_PREVIA"] = (
            experience_summary["EXPERIENCIA_PREVIA"].fillna(False)
        )
        experience_summary["POSTULANTE_ACTUAL"] = (
            experience_summary["POSTULANTE_ACTUAL"].fillna(False)
        )
        experience_summary["N_VECES_AYUDANTE"] = (
            experience_summary.get("N_VECES_AYUDANTE", pd.Series(dtype=float))
            .fillna(0).astype(int)
        )
        experience_summary["RUT"] = experience_summary["RUT"].astype(str)
        return experience_summary

    def _map_column_aliases(self, df: pd.DataFrame) -> pd.DataFrame:
        """Renombra columnas segun alias conocidos (tildes, variaciones)."""
        for canonical_name, variants in self.COLUMN_ALIASES.items():
            for variant in variants:
                if variant in df.columns and canonical_name not in df.columns:
                    df = df.rename(columns={variant: canonical_name})
        return df

    def _clean_numeric_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Convierte EVALUACION a numerico."""
        if "EVALUACION" in df.columns:
            df["EVALUACION"] = self._normalizer.parse_european_decimal(df["EVALUACION"])
        return df

    @staticmethod
    def _clean_period_column(df: pd.DataFrame) -> pd.DataFrame:
        """Limpia PERIODO: elimina '.0' de casteo float y espacios."""
        df["PERIODO"] = (
            df["PERIODO"].astype(str)
            .str.replace(r"\.0$", "", regex=True)
            .str.strip()
        )
        return df

    @staticmethod
    def _normalize_application_state(df: pd.DataFrame) -> pd.DataFrame:
        """Normaliza ESTADO: mayusculas y sin tildes para matching confiable."""
        raw_state = (
            df.get("ESTADO", pd.Series(dtype=str))
            .astype(str).str.strip().str.upper()
        )
        for accented_char, plain_char in zip("ÁÉÍÓÚ", "AEIOU"):
            raw_state = raw_state.str.replace(accented_char, plain_char, regex=False)
        df["ESTADO_NORM"] = raw_state
        return df

    @staticmethod
    def _split_by_period(
        df: pd.DataFrame, current_period: Optional[str],
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Separa registros historicos (periodos anteriores o aceptados) de los actuales."""
        if not current_period:
            return df.copy(), pd.DataFrame()

        historical = df[df["PERIODO"] < str(current_period)]
        current = df[df["PERIODO"] == str(current_period)]
        return historical, current

    @staticmethod
    def _aggregate_historical_experience(
        historical_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Agrega estadisticas de experiencia por RUT sobre registros aceptados."""
        accepted_records = historical_df[historical_df["FUE_ACEPTADO"]]

        if accepted_records.empty:
            return pd.DataFrame(columns=["RUT", "EXPERIENCIA_PREVIA"])

        summary = (
            accepted_records.sort_values("PERIODO")
            .groupby("RUT")
            .agg(
                N_VECES_AYUDANTE=("PERIODO", "count"),
                PROM_EVAL_PREVIA=("EVALUACION", "mean"),
            )
            .reset_index()
        )
        summary["EXPERIENCIA_PREVIA"] = True
        return summary

    @staticmethod
    def _add_current_applicant_flag(
        experience_df: pd.DataFrame,
        current_period_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Agrega POSTULANTE_ACTUAL=True si el alumno tiene una postulacion activa
        en el periodo actual (estado Aceptado o Pendiente; excluye Rechazado/Eliminada).
        """
        active = current_period_df
        if not current_period_df.empty and "ESTADO_NORM" in current_period_df.columns:
            active = current_period_df[
                current_period_df["ESTADO_NORM"].isin(ACTIVE_APPLICATION_STATES)
            ]

        if active.empty:
            experience_df["POSTULANTE_ACTUAL"] = False
            return experience_df

        current_applicants = active[["RUT"]].drop_duplicates().copy()
        current_applicants["POSTULANTE_ACTUAL"] = True
        return experience_df.merge(current_applicants, on="RUT", how="outer")

    def build_current_application_status(
        self,
        applications_df: Optional[pd.DataFrame],
        current_period: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Retorna estado de postulacion del periodo actual por (RUT, MATERIA, CURSO).

        Columnas: RUT, MATERIA, CURSO, ESTADO_POSTULACION, TIPO_AYUDANTE_POST,
                  PROFESOR_POST, N_ACEPTADAS_ACTUAL
        """
        empty_result = pd.DataFrame(
            columns=["RUT", "MATERIA", "CURSO", "ESTADO_POSTULACION"],
        )
        if applications_df is None or applications_df.empty:
            return empty_result

        df = self._normalizer.normalize_columns(applications_df)
        df = self._map_column_aliases(df)
        df = self._clean_period_column(df)

        if not current_period:
            return empty_result

        current = df[df["PERIODO"] == str(current_period)].copy()
        if current.empty:
            return empty_result

        current["ESTADO_POSTULACION"] = (
            current.get("ESTADO", pd.Series(dtype=str))
            .fillna("").astype(str).str.strip()
        )
        current["TIPO_AYUDANTE_POST"] = (
            current.get("TIPO DE AYUDANTE", pd.Series("", index=current.index))
            .fillna("").astype(str).str.strip()
        )
        current["PROFESOR_POST"] = (
            current.get("PROFESOR", pd.Series("", index=current.index))
            .fillna("").astype(str).str.strip()
        )

        # Contar cuantas aceptadas tiene cada RUT en el periodo actual
        state_normalized = current["ESTADO_POSTULACION"].str.strip().str.upper()
        for accented_char, plain_char in zip("ÁÉÍÓÚ", "AEIOU"):
            state_normalized = state_normalized.str.replace(accented_char, plain_char, regex=False)
        current["_FUE_ACEPTADA"] = state_normalized.isin(ACCEPTED_APPLICATION_STATES)

        accepted_counts = (
            current[current["_FUE_ACEPTADA"]]
            .groupby("RUT")
            .size()
            .reset_index(name="N_ACEPTADAS_ACTUAL")
        )

        result = current[
            ["RUT", "MATERIA", "CURSO", "ESTADO_POSTULACION",
             "TIPO_AYUDANTE_POST", "PROFESOR_POST"]
        ].drop_duplicates(subset=["RUT", "MATERIA", "CURSO"], keep="first")

        result = result.merge(accepted_counts, on="RUT", how="left")
        result["N_ACEPTADAS_ACTUAL"] = (
            result["N_ACEPTADAS_ACTUAL"].fillna(0).astype(int)
        )

        return result.reset_index(drop=True)
