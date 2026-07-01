"""
Analisis de horarios: parseo de bloques horarios y deteccion de conflictos.

Convierte las celdas de horario de las planillas (ej. "13:30 -15:20") en rangos
de minutos y permite saber si la carga de un alumno choca con un curso de ayudantia.
"""

import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

from backend.pipeline.constants import CLASSROOM_ACTIVITY_TYPES, WEEKDAYS
from backend.pipeline.column_normalizer import ColumnNormalizer


class ScheduleAnalyzer:
    """Parsea horarios de cursos y detecta conflictos entre bloques."""

    @staticmethod
    def parse_time_range(time_string: str) -> Optional[Tuple[int, int]]:
        """Convierte '13:30 -15:20' a (810, 920) en minutos desde medianoche."""
        match = re.search(
            r"(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})",
            str(time_string),
        )
        if not match:
            return None
        start_hour, start_min, end_hour, end_min = map(int, match.groups())
        return start_hour * 60 + start_min, end_hour * 60 + end_min

    @staticmethod
    def extract_time_slots_from_row(row: pd.Series) -> List[Tuple[str, int, int]]:
        """Extrae lista de (dia, inicio_min, fin_min) de una fila con columnas de dia."""
        slots: List[Tuple[str, int, int]] = []
        for day in WEEKDAYS:
            raw_value = str(row.get(day, "")).strip()
            if raw_value and raw_value.lower() not in ("nan", "none", ""):
                parsed = ScheduleAnalyzer.parse_time_range(raw_value)
                if parsed:
                    slots.append((day, parsed[0], parsed[1]))
        return slots

    @staticmethod
    def has_schedule_overlap(
        slots_a: List[Tuple[str, int, int]],
        slots_b: List[Tuple[str, int, int]],
    ) -> bool:
        """Retorna True si hay solapamiento temporal entre dos listas de bloques."""
        for day_a, start_a, end_a in slots_a:
            for day_b, start_b, end_b in slots_b:
                if day_a == day_b and start_a < end_b and start_b < end_a:
                    return True
        return False

    @staticmethod
    def build_course_schedule_map(nrc_df: pd.DataFrame) -> Dict[str, List[Tuple]]:
        """Construye {NRC: [(dia, inicio, fin), ...]} para tipos CLAS/LAB/AYUD."""
        df = ColumnNormalizer.normalize_columns(nrc_df)
        schedule_by_nrc: Dict[str, List] = {}

        for _, row in df.iterrows():
            activity_type = str(row.get("TIPO", "")).strip().upper()
            if activity_type and activity_type not in CLASSROOM_ACTIVITY_TYPES:
                continue
            nrc_key = str(row.get("NRC", "")).strip()
            if not nrc_key:
                continue
            time_slots = ScheduleAnalyzer.extract_time_slots_from_row(row)
            schedule_by_nrc.setdefault(nrc_key, []).extend(time_slots)

        return schedule_by_nrc

    @staticmethod
    def build_student_schedule_map(
        enrolled_courses_df: pd.DataFrame,
        nrc_df: pd.DataFrame,
    ) -> Dict[str, List[Tuple]]:
        """Retorna {RUT: [(dia, inicio, fin), ...]} con la ocupacion horaria de cada alumno."""
        enrolled = ColumnNormalizer.normalize_columns(enrolled_courses_df)
        nrc = ColumnNormalizer.normalize_columns(nrc_df)

        if "TIPO" in nrc.columns:
            nrc = nrc[nrc["TIPO"].str.strip().str.upper().isin(CLASSROOM_ACTIVITY_TYPES)]

        enrolled["NRC"] = enrolled["NRC"].astype(str)
        nrc["NRC"] = nrc["NRC"].astype(str)

        merged = enrolled[["RUT", "NRC"]].merge(nrc, on="NRC", how="left")
        schedule_by_student: Dict[str, List] = {}

        for _, row in merged.iterrows():
            rut = str(row["RUT"])
            time_slots = ScheduleAnalyzer.extract_time_slots_from_row(row)
            schedule_by_student.setdefault(rut, []).extend(time_slots)

        return schedule_by_student
