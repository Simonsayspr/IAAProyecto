"""
Normalizacion y limpieza de DataFrames provenientes de Google Sheets.

Estandariza nombres de columnas, formato de RUT, decimales con coma (formato
europeo) y construye el nombre completo del alumno a partir de columnas sueltas.
"""

import re
from typing import List

import pandas as pd


class ColumnNormalizer:
    """Estandariza nombres de columnas, RUTs y valores numericos en DataFrames."""

    @staticmethod
    def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Convierte nombres de columnas a MAYUSCULAS y limpia el RUT."""
        df = df.copy()
        df.columns = [
            re.sub(r"\s+", " ", str(col).strip().upper())
            for col in df.columns
        ]
        if "RUT" in df.columns:
            df["RUT"] = (
                df["RUT"]
                .astype(str)
                .str.upper()
                .str.replace(r"\.0$", "", regex=True)
                .str.replace(r"[^0-9K]", "", regex=True)
                .str.strip()
            )
        return df

    @staticmethod
    def parse_european_decimal(series: pd.Series) -> pd.Series:
        """Convierte una serie con coma decimal (formato europeo) a float."""
        return (
            series.astype(str)
            .str.replace(",", ".", regex=False)
            .pipe(pd.to_numeric, errors="coerce")
        )

    @staticmethod
    def extract_full_name(df: pd.DataFrame) -> pd.Series:
        """Construye NOMBRE_COMPLETO concatenando columnas de apellido y nombre."""
        def first_non_empty_column(
            dataframe: pd.DataFrame, column_candidates: List[str],
        ) -> pd.Series:
            for name in column_candidates:
                if name in dataframe.columns:
                    column_values = dataframe[name].fillna("").astype(str).str.strip()
                    if column_values.ne("").any():
                        return column_values
            return pd.Series("", index=dataframe.index)

        last_name = first_non_empty_column(df, [
            "PATERNO", "PRIMER APELLIDO", "APELLIDO PATERNO",
            "APELLIDO 1", "APELLIDOS",
        ])
        second_last_name = first_non_empty_column(df, [
            "MATERNO", "SEGUNDO APELLIDO", "APELLIDO MATERNO", "APELLIDO 2",
        ])
        first_name = first_non_empty_column(df, [
            "NOMBRE", "NOMBRES", "PRIMER NOMBRE", "NOMBRE ALUMNO",
        ])

        full_name = last_name + " " + second_last_name + " " + first_name
        return full_name.str.replace(r"\s+", " ", regex=True).str.strip()
