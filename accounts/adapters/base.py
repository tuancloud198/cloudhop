from abc import abstractmethod
from functools import cached_property

from common.cloud import ProviderAdapter, load_credential_file

from ..models import Account


class CredentialAdapter(ProviderAdapter):
    """Reads and checks an Account's credential against its cloud provider."""

    def __init__(self, account: Account):
        self.account = account

    @classmethod
    def for_account(cls, account: Account) -> "CredentialAdapter":
        return cls.for_provider(account.provider)(account)

    @cached_property
    def credential(self) -> dict:
        return load_credential_file(self.account.credential_ref)

    @abstractmethod
    def parse_identity(self) -> dict:
        """Return Account fields derived from the credential (at least external_id).

        Raises InvalidCredential if the credential is unusable.
        """

    @abstractmethod
    def validate(self) -> None:
        """Check the account's credentials against the cloud provider.

        Raises InvalidCredential if they do not work.
        """
