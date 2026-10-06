import logging
import tempfile

import requests

from ..cloud import CloudAPIError

logger = logging.getLogger(__name__)

PAGE_SIZE = 500


class KubeAPIError(CloudAPIError):
    """A call to a Kubernetes API server failed; the message is safe to show to the user."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class KubeClient:
    """Calls a Kubernetes API server with a bearer token.

    Use as a context manager: the CA certificate is written to a temp file for
    the lifetime of the client, since requests only verifies against a file.
    """

    def __init__(self, endpoint: str, ca_cert: str, token: str):
        self.endpoint = endpoint.rstrip("/")
        self.ca_cert = ca_cert
        self.token = token

    def __enter__(self) -> "KubeClient":
        self._ca_file = tempfile.NamedTemporaryFile("w", suffix=".pem")
        self._ca_file.write(self.ca_cert)
        self._ca_file.flush()
        self._session = requests.Session()
        self._session.verify = self._ca_file.name
        self._session.headers["Authorization"] = f"Bearer {self.token}"
        return self

    def __exit__(self, *exc_info):
        self._session.close()
        self._ca_file.close()

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET an API path (e.g. /api/v1/pods) and return the JSON body.

        Raises KubeAPIError if the call fails.
        """
        try:
            response = self._session.get(f"{self.endpoint}{path}", params=params, timeout=30)
        except requests.RequestException as exc:
            logger.warning("Cannot reach Kubernetes API %s: %s", self.endpoint, exc)
            raise KubeAPIError(f"cannot reach Kubernetes API at {self.endpoint}")

        if response.status_code != 200:
            logger.warning(
                "Kubernetes request %s failed: %s %s",
                path, response.status_code, response.text[:200],
            )
            raise KubeAPIError(self._error_reason(path, response), response.status_code)

        return response.json()

    def list(self, path: str) -> list[dict]:
        """GET every item of a list path, following pagination."""
        items = []
        params = {"limit": PAGE_SIZE}
        while True:
            data = self.get(path, params)
            items.extend(data.get("items", []))
            token = data.get("metadata", {}).get("continue")
            if not token:
                return items
            params["continue"] = token

    def _error_reason(self, path: str, response) -> str:
        try:
            message = response.json()["message"]
        except (ValueError, KeyError, TypeError):
            message = response.reason
        if response.status_code in (401, 403):
            return f"permission denied on {path}: {message}"
        return f"Kubernetes API returned {response.status_code} for {path}: {message}"
