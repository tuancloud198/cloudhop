import json
import logging
import tempfile

import requests

from common.cloud import CloudAPIError

logger = logging.getLogger(__name__)

PAGE_SIZE = 500


class KubeAPIError(CloudAPIError):
    """A call to a Kubernetes API server failed; the message is safe to show to the user."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class KubeClient:
    """Calls a Kubernetes API server with a bearer token. Reads, plus the few writes moves need.

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
        return self._request("GET", path, params=params)

    def post(self, path: str, body: dict) -> dict:
        """Create the object in body under a collection path and return it.

        Raises KubeAPIError if the call fails; status_code 409 when it already exists.
        """
        return self._request("POST", path, json=body)

    def patch(self, path: str, body: dict) -> dict:
        """Merge body into the object at path (JSON merge patch) and return it.

        Raises KubeAPIError if the call fails.
        """
        return self._request(
            "PATCH", path, data=json.dumps(body), headers={"Content-Type": "application/merge-patch+json"}
        )

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._session.request(method, f"{self.endpoint}{path}", timeout=30, **kwargs)
        except requests.RequestException as exc:
            logger.warning("Cannot reach Kubernetes API %s: %s", self.endpoint, exc)
            raise KubeAPIError(f"cannot reach Kubernetes API at {self.endpoint}")

        if response.status_code not in (200, 201):
            logger.warning(
                "Kubernetes request %s %s failed: %s %s",
                method, path, response.status_code, response.text[:200],
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
