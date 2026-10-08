import base64
import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import quote

from django.utils.dateparse import parse_datetime

from accounts.models.account import Account
from billing.adapters.base import BillingAdapter
from billing.models.budget import Budget
from common.cloud.errors import CloudTimeout
from common.cloud.gcp.client import GCPClient

logger = logging.getLogger(__name__)

PROJECT_URL = "https://cloudresourcemanager.googleapis.com/v3/projects/{project_id}"
BILLING_INFO_URL = (
    "https://cloudbilling.googleapis.com/v1/projects/{project_id}/billingInfo"
)
BILLING_ACCOUNT_URL = (
    "https://cloudbilling.googleapis.com/v1/billingAccounts/{billing_account_id}"
)
BUDGETS_URL = "https://billingbudgets.googleapis.com/v1/billingAccounts/{billing_account_id}/budgets"
PUBSUB_URL = "https://pubsub.googleapis.com/v1/{subscription}:{action}"

CALENDAR_PERIODS = {
    "MONTH": Budget.Period.MONTH,
    "QUARTER": Budget.Period.QUARTER,
    "YEAR": Budget.Period.YEAR,
}
# Budget filters that narrow it below whole projects
OTHER_FILTERS = ("resourceAncestors", "services", "subaccounts", "labels")
# Seconds a pull waits for messages; Pub/Sub holds an empty pull open until messages arrive
PULL_WAIT = 20


class GCPBillingAdapter(BillingAdapter):
    provider = Account.Provider.GCP

    @property
    def client(self) -> GCPClient:
        return GCPClient(self.credential, self.account.project_id)

    def billing_info(self) -> dict:
        """The project's Cloud Billing account. Needs only resourcemanager.projects.get."""
        client = self.client
        info = client.get(BILLING_INFO_URL)
        project = client.get(PROJECT_URL)
        return {
            # "billingAccounts/012345-ABCDEF-678901", or "" when billing is off
            "billing_account_id": info.get("billingAccountName", "").removeprefix(
                "billingAccounts/"
            )
            or None,
            "billing_enabled": info.get("billingEnabled", False),
            "project_number": project["name"].removeprefix("projects/"),
        }

    def get_billing_account(self, billing_account_id: str) -> dict:
        """Needs billing.accounts.get on the billing account, e.g. Billing Account Viewer."""
        data = self.client.get(
            BILLING_ACCOUNT_URL.format(billing_account_id=billing_account_id)
        )
        return {
            "name": data.get("displayName", ""),
            "currency": data.get("currencyCode", ""),
            "is_open": data.get("open", False),
        }

    def list_budgets(self, billing_account_id: str) -> list[dict]:
        """Needs billing.budgets.list on the billing account, e.g. Billing Account Viewer."""
        client = self.client
        url = BUDGETS_URL.format(billing_account_id=billing_account_id)
        budgets, page_token = [], ""
        while True:
            data = client.get(
                f"{url}?pageToken={quote(page_token, safe='')}" if page_token else url
            )
            budgets.extend(
                self._normalize_budget(budget) for budget in data.get("budgets", [])
            )
            page_token = data.get("nextPageToken")
            if not page_token:
                return budgets

    def pull_budget_updates(self, subscription: str) -> tuple[list[dict], list[str]]:
        """Needs pubsub.subscriptions.consume on the subscription, e.g. Pub/Sub Subscriber."""
        try:
            data = self.client.post(
                PUBSUB_URL.format(subscription=subscription, action="pull"),
                # Not returnImmediately: with it Pub/Sub often answers empty while messages are waiting
                {"maxMessages": 100},
                timeout=PULL_WAIT,
            )
        except CloudTimeout:
            # Nothing arrived while waiting; messages the dropped pull may have taken come back after their ack deadline
            return [], []
        updates, ack_ids = [], []
        for received in data.get("receivedMessages", []):
            ack_ids.append(received["ackId"])
            try:
                updates.append(self._normalize_update(received["message"]))
            except (KeyError, TypeError, ValueError) as exc:
                # Acknowledged anyway, or it would come back on every pull
                logger.warning(
                    "Ignoring malformed budget notification %s: %s",
                    received["message"].get("messageId"),
                    exc,
                )
        return updates, ack_ids

    def replay_budget_updates(self, subscription: str, since: datetime) -> None:
        """Needs pubsub.subscriptions.consume on the subscription, e.g. Pub/Sub Subscriber.

        Seeking back finds only the messages Pub/Sub kept: those still within the topic's
        message retention, or the subscription's when it retains acknowledged messages.
        """
        self.client.post(
            PUBSUB_URL.format(subscription=subscription, action="seek"),
            {"time": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")},
        )

    def acknowledge(self, subscription: str, ack_ids: list[str]) -> None:
        self.client.post(
            PUBSUB_URL.format(subscription=subscription, action="acknowledge"),
            {"ackIds": ack_ids},
        )

    def _normalize_budget(self, budget: dict) -> dict:
        budget_filter = budget.get("budgetFilter", {})
        amount = budget.get("amount", {}).get("specifiedAmount")
        custom = budget_filter.get("customPeriod")
        return {
            # billingAccounts/<id>/budgets/<budget id>
            "external_id": budget["name"].rsplit("/", 1)[-1],
            "name": budget.get("displayName", ""),
            # lastPeriodAmount budgets follow last period's spend, with no fixed amount
            "amount": _money(amount) if amount else None,
            "currency": amount.get("currencyCode", "") if amount else "",
            # A budget with neither period tracks the calendar month
            "period": Budget.Period.CUSTOM
            if custom
            else CALENDAR_PERIODS.get(
                budget_filter.get("calendarPeriod"), Budget.Period.MONTH
            ),
            "start_date": _date(custom.get("startDate")) if custom else None,
            "end_date": _date(custom.get("endDate")) if custom else None,
            "credit_treatment": budget_filter.get("creditTypesTreatment", ""),
            "projects": [
                project.removeprefix("projects/")
                for project in budget_filter.get("projects", [])
            ],
            "has_other_filters": any(budget_filter.get(key) for key in OTHER_FILTERS),
            "pubsub_topic": budget.get("notificationsRule", {}).get("pubsubTopic", ""),
        }

    def _normalize_update(self, message: dict) -> dict:
        # Format: https://cloud.google.com/billing/docs/how-to/budgets-programmatic-notifications
        attributes = message["attributes"]
        payload = json.loads(base64.b64decode(message["data"]))
        return {
            "billing_account_id": attributes["billingAccountId"],
            "budget_id": attributes["budgetId"],
            "budget_name": payload.get("budgetDisplayName", ""),
            "message_id": message["messageId"],
            "cost_amount": Decimal(str(payload["costAmount"])),
            "budget_amount": Decimal(str(payload["budgetAmount"])),
            "currency": payload["currencyCode"],
            "interval_start": _datetime(payload["costIntervalStart"]),
            "threshold_exceeded": payload.get("alertThresholdExceeded"),
            "forecast_threshold_exceeded": payload.get("forecastThresholdExceeded"),
            "published_at": _datetime(message["publishTime"]),
        }


def _money(money: dict) -> Decimal:
    """google.type.Money: whole units as a string, plus nanos (10^-9 units)."""
    return Decimal(money.get("units", "0")) + Decimal(money.get("nanos", 0)) / 10**9


def _date(value: dict | None) -> date | None:
    """google.type.Date; a zero or missing year means the date is open."""
    if not value or not value.get("year"):
        return None
    return date(value["year"], value["month"], value["day"])


def _datetime(value: str):
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError(f"not a timestamp: {value!r}")
    return parsed
