from common.cloud import InvalidCredential
from common.cloud.gcp import GCPClient

from ..models import Account
from .base import CredentialAdapter

PROJECT_URL = "https://cloudresourcemanager.googleapis.com/v3/projects/{project_id}"


class GCPCredentialAdapter(CredentialAdapter):
    provider = Account.Provider.GCP

    def parse_identity(self) -> dict:
        info = self.credential
        if info.get("type") != "service_account":
            raise InvalidCredential("credential file is not a GCP service account key")
        missing = [key for key in ("client_id", "project_id") if not info.get(key)]
        if missing:
            raise InvalidCredential(f"credential file is missing {', '.join(missing)}")
        return {"external_id": info["client_id"], "project_id": info["project_id"]}

    def validate(self) -> None:
        """Check that the service account can read its GCP project."""
        client = GCPClient(self.credential, self.account.project_id)
        state = client.get(PROJECT_URL, InvalidCredential).get("state")
        if state != "ACTIVE":
            raise InvalidCredential(
                f"GCP project {self.account.project_id} is not active (state: {state})"
            )
