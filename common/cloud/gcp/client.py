import logging

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2 import service_account

from ..errors import CloudAPIError, InvalidCredential

logger = logging.getLogger(__name__)

# GKE rejects the read-only scope; actual access is limited by the service account's IAM roles
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class GCPClient:
    """Calls GCP REST APIs for one project as a service account."""

    def __init__(self, service_account_info: dict, project_id: str):
        self.service_account_info = service_account_info
        self.project_id = project_id

    def access_token(self) -> str:
        """Log in as the service account and return an OAuth access token.

        Raises InvalidCredential if login fails.
        """
        try:
            credentials = self._credentials()
            credentials.refresh(Request())
        except (GoogleAuthError, ValueError) as exc:
            logger.warning("GCP auth failed for project %s: %s", self.project_id, exc)
            raise InvalidCredential(f"GCP authentication failed: {exc}")
        return credentials.token

    def get(self, url: str, error_cls: type[CloudAPIError] = CloudAPIError) -> dict:
        """GET a URL (formatted with project_id) and return the JSON body.

        Raises InvalidCredential if authentication fails, error_cls for any other failure.
        """
        return self._request("GET", url, None, error_cls)

    def post(self, url: str, body: dict, error_cls: type[CloudAPIError] = CloudAPIError) -> dict:
        """POST a JSON body to a URL (formatted with project_id) and return the JSON body.

        Raises InvalidCredential if authentication fails, error_cls for any other failure.
        """
        return self._request("POST", url, body, error_cls)

    def _request(self, method: str, url: str, body: dict | None, error_cls: type[CloudAPIError]) -> dict:
        try:
            session = AuthorizedSession(self._credentials())
            response = session.request(method, url.format(project_id=self.project_id), json=body, timeout=10)
        except (GoogleAuthError, ValueError) as exc:
            logger.warning("GCP auth failed for project %s: %s", self.project_id, exc)
            raise InvalidCredential(f"GCP authentication failed: {exc}")
        except OSError as exc:
            logger.warning("Cannot reach GCP for project %s: %s", self.project_id, exc)
            raise error_cls("cannot reach GCP, try again later")

        if response.status_code != 200:
            logger.warning(
                "GCP request %s %s failed for project %s: %s %s",
                method, url, self.project_id, response.status_code, response.text[:200],
            )
            raise error_cls(self._error_reason(response))

        return response.json()

    def _credentials(self) -> service_account.Credentials:
        return service_account.Credentials.from_service_account_info(
            self.service_account_info, scopes=SCOPES
        )

    def _error_reason(self, response) -> str:
        try:
            message = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = response.reason
        if response.status_code == 403:
            return f"permission denied on GCP project {self.project_id}: {message}"
        if response.status_code == 404:
            return f"not found on GCP (project {self.project_id}): {message}"
        return f"GCP returned {response.status_code} for project {self.project_id}: {message}"
