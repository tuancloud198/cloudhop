from accounts.adapters.base import CredentialAdapter
from accounts.models.account import Account


def resolve_account(account: Account) -> Account:
    """Fill identity fields parsed from credential_ref and check them against the cloud provider.

    Raises CloudAPIError (usually InvalidCredential), with the reason, if the credential
    cannot be parsed or does not work.
    """
    adapter = CredentialAdapter.for_account(account)
    for field, value in adapter.parse_identity().items():
        setattr(account, field, value)
    adapter.validate()
    return account
