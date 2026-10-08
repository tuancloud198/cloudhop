from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounts.models import Account
from billing.adapters import BillingAdapter
from billing.models import AccountBilling, BillingAccount, Budget, BudgetStatus
from common.cloud import CloudAPIError

__all__ = ["AccountNotUsable", "budget_covers", "spend_status", "sync_billing"]

# Pulls per sync, each returning up to 100 notifications
MAX_PULLS = 20
CENT = Decimal("0.01")
# How far back a cold start asks for notifications again; the longest a Pub/Sub topic can keep them
REPLAY_PERIOD = timedelta(days=31)


class AccountNotUsable(Exception):
    """The account is inactive or its credential was never validated."""


def sync_billing(account_id: int, replay: bool = False) -> dict:
    """Find the billing account paying for the account, read its budgets, and store the
    budget notifications waiting in its Pub/Sub subscription.

    With replay, or on a cold start (none of the billing account's notifications stored
    yet), the subscription is first replayed from REPLAY_PERIOD ago, so notifications
    already acknowledged (e.g. by another CloudHop) come back if the provider still
    keeps them. Those already stored are not stored again.

    Only finding the billing account is required. Its details, its budgets and the
    notifications each need more access; when one cannot be read, the reason is added
    to warnings and the sync goes on.

    Returns {"billing": AccountBilling, "warnings": [str], "received": int}.
    Raises Account.DoesNotExist, AccountNotUsable, or common.cloud.CloudAPIError.
    """
    account = Account.objects.get(pk=account_id)
    if not account.is_active or not account.is_valid:
        raise AccountNotUsable(f"account {account_id} is inactive or not validated")

    adapter = BillingAdapter.for_account(account)
    info = adapter.billing_info()

    billing_account, warnings, received = None, [], 0
    if info["billing_account_id"]:
        billing_account, _ = BillingAccount.objects.get_or_create(
            provider=account.provider, external_id=info["billing_account_id"]
        )
        try:
            details = adapter.get_billing_account(billing_account.external_id)
        except CloudAPIError as exc:
            warnings.append(f"Cannot read the billing account: {exc}")
        else:
            for field, value in details.items():
                setattr(billing_account, field, value)
            billing_account.save()

        try:
            budgets = adapter.list_budgets(billing_account.external_id)
        except CloudAPIError as exc:
            warnings.append(f"Cannot list budgets: {exc}")
        else:
            _store_budgets(billing_account, budgets)

        if billing_account.pubsub_subscription:
            if replay or not BudgetStatus.objects.filter(budget__billing_account=billing_account).exists():
                try:
                    adapter.replay_budget_updates(billing_account.pubsub_subscription, timezone.now() - REPLAY_PERIOD)
                except CloudAPIError as exc:
                    # Pulling still gets what is waiting
                    warnings.append(f"Cannot replay earlier budget notifications: {exc}")
            try:
                received = _pull_updates(adapter, account.provider, billing_account.pubsub_subscription)
            except CloudAPIError as exc:
                warnings.append(f"Cannot pull budget notifications: {exc}")

    billing, _ = AccountBilling.objects.update_or_create(
        account=account,
        defaults={
            "billing_account": billing_account,
            "billing_enabled": info["billing_enabled"],
            "project_number": info["project_number"],
            "synced_at": timezone.now(),
        },
    )
    return {"billing": billing, "warnings": warnings, "received": received}


def spend_status(account: Account) -> dict | None:
    """Spend of the budget covering the account that is closest to being used up.

    Returns None when the account has no billing account, or no budget covering it
    has reported spend yet. Otherwise a dict with budget_id, budget_name, spent,
    amount, currency, ratio (spent / amount), interval_start and as_of (when the
    provider reported it; spend lags actual usage by hours).
    """
    billing = AccountBilling.objects.filter(account=account).select_related("billing_account").first()
    if billing is None or billing.billing_account is None:
        return None

    candidates = []
    for budget in billing.billing_account.budgets.filter(is_active=True):
        if not budget_covers(budget, billing):
            continue
        latest = budget.statuses.order_by("-published_at").first()
        if latest is None or latest.budget_amount <= 0:
            continue
        candidates.append({
            "budget_id": budget.pk,
            "budget_name": budget.name or budget.external_id,
            "spent": latest.cost_amount,
            "amount": latest.budget_amount,
            "currency": latest.currency,
            "ratio": float(latest.cost_amount / latest.budget_amount),
            "interval_start": latest.interval_start,
            "as_of": latest.published_at,
        })
    return max(candidates, key=lambda status: status["ratio"], default=None)


def budget_covers(budget: Budget, billing: AccountBilling) -> bool:
    """Whether the budget counts the account's spend; a budget with no projects counts all of them."""
    # GCP names a budget's projects by number, but accept the project ID too
    return not budget.projects or bool({billing.project_number, billing.account.project_id} & set(budget.projects))


def _store_budgets(billing_account: BillingAccount, budgets: list[dict]) -> None:
    with transaction.atomic():
        stored = []
        for data in budgets:
            budget, _ = Budget.objects.update_or_create(
                billing_account=billing_account,
                external_id=data["external_id"],
                defaults={**data, "is_active": True},
            )
            stored.append(budget.pk)
        billing_account.budgets.filter(is_active=True).exclude(pk__in=stored).update(
            is_active=False, updated_at=timezone.now()
        )


def _pull_updates(adapter: BillingAdapter, provider: str, subscription: str) -> int:
    """Store pending budget notifications, then acknowledge them. Returns how many were stored."""
    received = 0
    for _ in range(MAX_PULLS):
        updates, ack_ids = adapter.pull_budget_updates(subscription)
        if not ack_ids:
            break
        with transaction.atomic():
            for update in updates:
                received += _store_update(provider, update)
        # If this fails the messages come back on the next pull; message_id keeps them from being stored twice
        adapter.acknowledge(subscription, ack_ids)
    return received


def _store_update(provider: str, update: dict) -> int:
    # One subscription may receive budgets of other billing accounts too
    billing_account, _ = BillingAccount.objects.get_or_create(
        provider=provider, external_id=update["billing_account_id"]
    )
    # Known only from its notifications when the budgets cannot be listed
    budget, _ = Budget.objects.get_or_create(
        billing_account=billing_account,
        external_id=update["budget_id"],
        defaults={
            "name": update["budget_name"],
            "amount": update["budget_amount"].quantize(CENT),
            "currency": update["currency"],
        },
    )
    _, created = BudgetStatus.objects.get_or_create(
        message_id=update["message_id"],
        defaults={
            "budget": budget,
            "cost_amount": update["cost_amount"].quantize(CENT),
            "budget_amount": update["budget_amount"].quantize(CENT),
            "currency": update["currency"],
            "interval_start": update["interval_start"],
            "threshold_exceeded": update["threshold_exceeded"],
            "forecast_threshold_exceeded": update["forecast_threshold_exceeded"],
            "published_at": update["published_at"],
        },
    )
    return int(created)
