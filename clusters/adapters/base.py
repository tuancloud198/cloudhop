from abc import abstractmethod
from functools import cached_property

from accounts.models.account import Account
from common.cloud.credentials import load_credential_file
from common.cloud.registry import ProviderAdapter


class ClusterAdapter(ProviderAdapter):
    """Reads an Account's Kubernetes clusters from its cloud provider."""

    def __init__(self, account: Account):
        self.account = account

    @classmethod
    def for_account(cls, account: Account) -> "ClusterAdapter":
        return cls.for_provider(account.provider)(account)

    @cached_property
    def credential(self) -> dict:
        return load_credential_file(self.account.credential_ref)

    @abstractmethod
    def list_clusters(self) -> list[dict]:
        """Return the account's Kubernetes clusters, normalized to Clusters fields.

        Each dict has external_id, name, location, status, kubernetes_version and spec.
        Raises CloudAPIError if the provider call fails.
        """
