from abc import abstractmethod
from functools import cached_property

from accounts.models.account import Account
from common.cloud.credentials import load_credential_file
from common.cloud.registry import ProviderAdapter


class BucketAdapter(ProviderAdapter):
    """Reads the object storage bucket a Velero move goes through, as the account it belongs in."""

    # The role on the bucket that gives identity() everything missing_backup_access() asks for
    backup_role: str

    def __init__(self, account: Account):
        self.account = account

    @classmethod
    def for_account(cls, account: Account) -> "BucketAdapter":
        return cls.for_provider(account.provider)(account)

    @cached_property
    def credential(self) -> dict:
        return load_credential_file(self.account.credential_ref)

    @abstractmethod
    def identity(self) -> str:
        """The identity CloudHop uses in this account, as the provider's IAM names it.

        Velero reuses it, with the same key, to reach the bucket.
        """

    @abstractmethod
    def get_bucket(self, name: str) -> dict | None:
        """Return the bucket as {name, location, in_account, owner}, or None if no bucket has that name.

        in_account tells whether it belongs to this account; owner names what it belongs to.
        Raises CloudAPIError with status_code 403 when the bucket exists but this account may
        not read it (the message says what to grant), CloudAPIError for any other failure.
        """

    @abstractmethod
    def missing_backup_access(self, name: str) -> list[str]:
        """The permissions on the bucket's objects that Velero needs and identity() lacks; [] if none.

        Raises CloudAPIError if the provider cannot be asked.
        """
