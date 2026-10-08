from abc import abstractmethod
from datetime import datetime
from functools import cached_property

from accounts.models.account import Account
from common.cloud.credentials import load_credential_file
from common.cloud.registry import ProviderAdapter


class BillingAdapter(ProviderAdapter):
    """Reads what pays for an Account: its billing account, the budgets set on it,
    and the spend updates the provider sends for those budgets.
    """

    def __init__(self, account: Account):
        self.account = account

    @classmethod
    def for_account(cls, account: Account) -> "BillingAdapter":
        return cls.for_provider(account.provider)(account)

    @cached_property
    def credential(self) -> dict:
        return load_credential_file(self.account.credential_ref)

    @abstractmethod
    def billing_info(self) -> dict:
        """Return the billing account paying for the Account.

        The dict has billing_account_id (None when there is none), billing_enabled
        and project_number. Raises CloudAPIError if the provider call fails.
        """

    @abstractmethod
    def get_billing_account(self, billing_account_id: str) -> dict:
        """Return the billing account's name, currency and is_open.

        Raises CloudAPIError if the provider call fails, e.g. without access to the billing account.
        """

    @abstractmethod
    def list_budgets(self, billing_account_id: str) -> list[dict]:
        """Return the budgets set on the billing account, normalized to Budget fields.

        Raises CloudAPIError if the provider call fails, e.g. without access to the billing account.
        """

    @abstractmethod
    def pull_budget_updates(self, subscription: str) -> tuple[list[dict], list[str]]:
        """Fetch pending budget notifications from the subscription, without acknowledging them.

        Returns (updates, ack_ids). Each update has billing_account_id, budget_id,
        budget_name, message_id and the BudgetStatus fields. ack_ids covers every
        message received, including malformed ones left out of updates.
        Raises CloudAPIError if the provider call fails.
        """

    @abstractmethod
    def replay_budget_updates(self, subscription: str, since: datetime) -> None:
        """Make the notifications published since `since` deliverable again, acknowledged ones included.

        Only those the provider still keeps come back. Raises CloudAPIError if the
        provider call fails, e.g. when it keeps no acknowledged messages.
        """

    @abstractmethod
    def acknowledge(self, subscription: str, ack_ids: list[str]) -> None:
        """Remove handled messages from the subscription. Raises CloudAPIError if the call fails."""
