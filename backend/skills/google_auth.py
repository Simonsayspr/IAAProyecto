"""
Autenticacion con Google Cloud via Service Account.
"""

from google.oauth2.service_account import Credentials

DEFAULT_SCOPE = "https://spreadsheets.google.com/feeds"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


class GoogleAuth:
    """
    Genera credenciales de Service Account para acceder a Google Sheets.

    Uso:
        auth = GoogleAuth(service_account_dict, scope)
        credentials = auth.get_credentials()
    """

    def __init__(self, service_account_dict: dict, scope: str = DEFAULT_SCOPE):
        """
        Parameters
        ----------
        service_account_dict : dict
            Contenido del archivo JSON de Service Account de Google Cloud.
        scope : str
            URL de alcance de la API. Por defecto Google Sheets + Drive.
        """
        self._credentials = Credentials.from_service_account_info(
            service_account_dict,
            scopes=[scope, DRIVE_SCOPE],
        )

    def get_credentials(self) -> Credentials:
        return self._credentials
