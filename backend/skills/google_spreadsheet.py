"""
Lectura de Google Sheets / Google Drive como pandas DataFrames.

Soporta dos tipos de archivo:
  - Google Sheets nativo: se lee con la Sheets API v4 (gspread).
  - Office file (.xlsx/.xlsm) subido a Drive: se descarga via Drive API y se
    parsea con pandas. Ocurre cuando la URL apunta a un archivo que NO fue
    convertido a formato Sheets al subirlo a Drive.

Ambas rutas aplican detección automática de la fila de encabezados real,
lo que permite manejar archivos con filas de título encima de los datos
(común en exports de Banner/SAF).
"""

import io
import re

import gspread
import pandas as pd
from gspread.exceptions import APIError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials


# Palabras clave de columnas esperadas en las planillas Banner/SAF
_KNOWN_HEADERS = {
    "RUT", "NRC", "MATERIA", "CURSO", "NOTA", "PERIODO", "NOMBRE",
    "PATERNO", "MATERNO", "PROGRAMA", "CARRERA", "ESTADO", "TITULO",
    "INSCRITOS", "CUPOS", "CREDITOS", "LUNES", "MARTES", "MIERCOLES",
    "JUEVES", "VIERNES", "SABADO", "PROFESOR", "STATUS", "PROMEDIO",
    "INGRESO", "CATALOGO", "ESCUELA", "SECC", "CALIFICABLE", "CAMPUS",
    "ORIGEN", "RAZON", "TIPO", "SOLICITUD", "EVALUACION", "EVALUACIÓN",
}


def _extract_file_id(url: str) -> str:
    match = re.search(r"/(?:spreadsheets|file)/d/([a-zA-Z0-9_-]+)", url)
    if not match:
        raise ValueError(f"No se pudo extraer el file ID de: {url}")
    return match.group(1)


def _smart_dataframe(rows: list) -> pd.DataFrame:
    """
    Dado una lista de filas (listas de valores), detecta la fila de
    encabezados real y retorna un DataFrame limpio.

    Maneja archivos con filas de título por encima de los datos reales,
    común en exports de Banner/SAF.
    """
    if not rows:
        return pd.DataFrame()

    # Buscar entre las primeras 15 filas la que tenga más coincidencias
    # con nombres de columna conocidos
    best_row = 0
    best_score = -1

    for i, row in enumerate(rows[:15]):
        vals_upper = {re.sub(r"\s+", " ", str(v).strip().upper()) for v in row}
        # Puntaje: cuántos tokens de la fila coinciden con columnas conocidas
        score = sum(
            1
            for v in vals_upper
            if any(k in v or v in k for k in _KNOWN_HEADERS)
        )
        if score > best_score:
            best_score = score
            best_row = i

    if best_row >= len(rows) - 1:
        return pd.DataFrame()

    headers = rows[best_row]
    data = rows[best_row + 1:]

    df = pd.DataFrame(data, columns=headers)

    # Descartar filas completamente vacías
    df = df[
        ~df.apply(lambda r: r.astype(str).str.strip().eq("").all(), axis=1)
    ].reset_index(drop=True)

    return df


class GoogleSpreadsheetSkill:
    """
    Abre un Google Spreadsheet (nativo o Office file) y expone sus hojas
    como DataFrames de pandas.

    Uso:
        skill = GoogleSpreadsheetSkill(credentials, spreadsheet_url)
        df = skill.get_dataframe("Nombre de la hoja")
    """

    def __init__(self, credentials: Credentials, spreadsheet_url: str):
        self._credentials = credentials          # guardamos para Drive API
        self._client = gspread.authorize(credentials)
        self._url = spreadsheet_url
        self._spreadsheet = None                 # apertura lazy (Sheets API)
        self._excel_cache: dict | None = None    # cache del fallback Drive

    # ── Sheets API (Google Sheets nativo) ──────────────────────────────────

    def _open(self) -> gspread.Spreadsheet:
        if self._spreadsheet is None:
            self._spreadsheet = self._client.open_by_url(self._url)
        return self._spreadsheet

    # ── Drive API fallback (Office files .xlsx / .xlsm) ───────────────────

    def _download_all_sheets(self) -> dict[str, pd.DataFrame]:
        """
        Descarga el archivo via Drive API y retorna {nombre_hoja: DataFrame}.

        Intenta primero exportar como xlsx (Google Sheets nativos), luego
        descarga el archivo en su formato original (para .xlsx/.xlsm subidos
        sin convertir).
        """
        if self._excel_cache is not None:
            return self._excel_cache

        file_id = _extract_file_id(self._url)
        session = AuthorizedSession(self._credentials)

        # Intento 1: export Sheets → xlsx (solo funciona para Google Sheets nativos)
        export_url = (
            f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
            "?mimeType=application%2Fvnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        resp = session.get(export_url)

        # Intento 2: descarga directa del archivo en su formato original.
        # Un Office file (.xlsx/.xlsm) subido sin convertir no es exportable, por lo
        # que /export responde 400/403/404/415; en ese caso usamos ?alt=media.
        if resp.status_code != 200:
            download_url = (
                f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
            )
            resp = session.get(download_url)

        if resp.status_code == 403:
            raise PermissionError(
                "403 al descargar archivo de Google Drive. "
                "La cuenta de servicio no puede descargar el archivo. "
                "Verifica que el 'Google Drive API' esté habilitado en "
                "console.cloud.google.com/apis/library y que la planilla esté "
                "compartida con el correo de la cuenta de servicio."
            )

        resp.raise_for_status()

        # Leer como Excel sin asumir fila de encabezado (header=None)
        # para que _smart_dataframe detecte la fila correcta
        raw: dict[str, pd.DataFrame] = pd.read_excel(
            io.BytesIO(resp.content),
            sheet_name=None,
            header=None,
            dtype=str,
        )

        self._excel_cache = {}
        for name, df in raw.items():
            rows = df.values.tolist()
            self._excel_cache[name] = _smart_dataframe(rows)

        return self._excel_cache

    def _find_sheet(self, worksheet_name: str) -> pd.DataFrame:
        """Busca la hoja más parecida al nombre solicitado."""
        sheets = self._download_all_sheets()
        name_lower = worksheet_name.lower()

        if worksheet_name in sheets:
            return sheets[worksheet_name]

        for name, df in sheets.items():
            if name_lower in name.lower() or name.lower() in name_lower:
                return df

        if sheets:
            return next(iter(sheets.values()))

        return pd.DataFrame()

    # ── Interfaz pública ───────────────────────────────────────────────────

    def get_dataframe(self, worksheet_name: str) -> pd.DataFrame:
        """
        Retorna el contenido de una hoja como DataFrame con detección
        automática de la fila de encabezados.
        """
        try:
            spreadsheet = self._open()
            ws = spreadsheet.worksheet(worksheet_name)
            rows = ws.get_all_values()

            if not rows or len(rows) < 2:
                return pd.DataFrame()

            return _smart_dataframe(rows)

        except APIError as exc:
            msg = str(exc)
            if "400" in msg and "Office file" in msg:
                return self._find_sheet(worksheet_name)
            raise

