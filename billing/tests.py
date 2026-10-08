import base64
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest import mock

from rest_framework.test import APITestCase

from accounts.models.account import Account
from billing.models.account_billing import AccountBilling
from billing.models.billing_account import BillingAccount
from billing.models.budget import Budget
from billing.models.budget_status import BudgetStatus
from billing.services.billing import spend_status
from billing.tasks import sync_account_billing, sync_all_billing
from common.cloud.errors import CloudAPIError, CloudTimeout

BILLING_ID = "0123AB-CDEF01-234567"
SUBSCRIPTION = "projects/demo-project/subscriptions/cloudhop-budget"


def notification(message_id, cost, budget_id="budget-1", ack_id=None):
    payload = {
        "budgetDisplayName": "credits",
        "costAmount": cost,
        "costIntervalStart": "2026-10-01T07:00:00Z",
        "budgetAmount": 300.0,
        "budgetAmountType": "SPECIFIED_AMOUNT",
        "alertThresholdExceeded": 0.5,
        "currencyCode": "USD",
    }
    return {
        "ackId": ack_id or f"ack-{message_id}",
        "message": {
            "data": base64.b64encode(json.dumps(payload).encode()).decode(),
            "attributes": {"billingAccountId": BILLING_ID, "budgetId": budget_id, "schemaVersion": "1.0"},
            "messageId": message_id,
            "publishTime": "2026-10-07T08:00:00.123456789Z",
        },
    }


class FakeGCPClient:
    """Answers GCP REST calls from canned responses; a CloudAPIError value is raised instead."""

    responses = {}
    pulls = []
    pull_requests = []
    seeks = []
    seek_response = {}
    acknowledged = []

    def __init__(self, service_account_info, project_id):
        self.project_id = project_id

    def get(self, url, error_cls=CloudAPIError):
        return self._answer(url.format(project_id=self.project_id))

    def post(self, url, body, error_cls=CloudAPIError, timeout=10):
        url = url.format(project_id=self.project_id)
        if url.endswith(":pull"):
            self.pull_requests.append(body)
            response = self.pulls.pop(0) if self.pulls else {}
            if isinstance(response, Exception):
                raise response
            return response
        if url.endswith(":seek"):
            if isinstance(self.seek_response, Exception):
                raise self.seek_response
            self.seeks.append(body["time"])
            return {}
        if url.endswith(":acknowledge"):
            self.acknowledged.extend(body["ackIds"])
            return {}
        return self._answer(url)

    def _answer(self, url):
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class BillingSyncTests(APITestCase):
    def setUp(self):
        self.account = Account.objects.create(
            name="demo", provider="gcp", external_id="1234", project_id="demo-project",
            credential_ref="/unused.json", is_valid=True,
        )
        FakeGCPClient.responses = {
            "https://cloudbilling.googleapis.com/v1/projects/demo-project/billingInfo": {
                "billingAccountName": f"billingAccounts/{BILLING_ID}",
                "billingEnabled": True,
            },
            "https://cloudresourcemanager.googleapis.com/v3/projects/demo-project": {"name": "projects/111"},
            f"https://cloudbilling.googleapis.com/v1/billingAccounts/{BILLING_ID}": {
                "displayName": "My Billing Account", "currencyCode": "USD", "open": True,
            },
            f"https://billingbudgets.googleapis.com/v1/billingAccounts/{BILLING_ID}/budgets": {
                "budgets": [{
                    "name": f"billingAccounts/{BILLING_ID}/budgets/budget-1",
                    "displayName": "credits",
                    "budgetFilter": {
                        "projects": ["projects/111"],
                        "creditTypesTreatment": "EXCLUDE_ALL_CREDITS",
                        "customPeriod": {
                            "startDate": {"year": 2026, "month": 10, "day": 7},
                            "endDate": {"year": 2027, "month": 1, "day": 5},
                        },
                    },
                    "amount": {"specifiedAmount": {"currencyCode": "USD", "units": "300", "nanos": 500000000}},
                    "notificationsRule": {"pubsubTopic": "projects/demo-project/topics/cloudhop-budget"},
                }],
            },
        }
        FakeGCPClient.pulls = []
        FakeGCPClient.pull_requests = []
        FakeGCPClient.seeks = []
        FakeGCPClient.seek_response = {}
        FakeGCPClient.acknowledged = []
        for target, value in [
            ("billing.adapters.gcp.GCPClient", FakeGCPClient),
            ("billing.adapters.base.load_credential_file", mock.Mock(return_value={})),
        ]:
            patch = mock.patch(target, value)
            patch.start()
            self.addCleanup(patch.stop)

    def sync(self):
        return self.client.post(f"/api/v1/accounts/{self.account.pk}/billing/sync/")

    def test_sync_stores_billing_account_and_budgets(self):
        response = self.sync()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["warnings"], [])
        billing = AccountBilling.objects.get(account=self.account)
        self.assertEqual((billing.billing_account.name, billing.project_number), ("My Billing Account", "111"))
        budget = Budget.objects.get()
        self.assertEqual(budget.amount, Decimal("300.50"))
        self.assertEqual((budget.period, budget.start_date, budget.end_date),
                         ("custom", date(2026, 10, 7), date(2027, 1, 5)))
        self.assertEqual((budget.projects, budget.credit_treatment), (["111"], "EXCLUDE_ALL_CREDITS"))
        self.assertTrue(response.data["billing_account"]["budgets"][0]["covers_account"])

    def test_sync_pulls_notifications_and_acknowledges_them(self):
        BillingAccount.objects.create(provider="gcp", external_id=BILLING_ID, pubsub_subscription=SUBSCRIPTION)
        malformed = {"ackId": "ack-bad", "message": {"messageId": "bad", "data": "not json"}}
        FakeGCPClient.pulls = [{"receivedMessages": [notification("m1", 150.256), malformed]}, {}]

        response = self.sync()

        self.assertEqual(response.data["received"], 1)
        self.assertEqual(FakeGCPClient.acknowledged, ["ack-m1", "ack-bad"])
        status = BudgetStatus.objects.get()
        self.assertEqual((status.cost_amount, status.threshold_exceeded), (Decimal("150.26"), 0.5))
        spend = response.data["spend"]
        self.assertEqual((spend["spent"], spend["amount"], spend["ratio"]), ("150.26", "300.00", 150.26 / 300))

    def test_pull_waits_for_messages(self):
        BillingAccount.objects.create(provider="gcp", external_id=BILLING_ID, pubsub_subscription=SUBSCRIPTION)
        FakeGCPClient.pulls = [{"receivedMessages": [notification("m1", 10)]}, CloudTimeout("GCP did not answer within 5s")]

        response = self.sync()

        # returnImmediately makes Pub/Sub answer empty while messages are waiting
        self.assertNotIn("returnImmediately", FakeGCPClient.pull_requests[0])
        # A pull that times out found nothing more; it is not a failure
        self.assertEqual((response.data["received"], response.data["warnings"]), (1, []))
        self.assertEqual(FakeGCPClient.acknowledged, ["ack-m1"])

    def test_cold_start_replays_the_subscription(self):
        BillingAccount.objects.create(provider="gcp", external_id=BILLING_ID, pubsub_subscription=SUBSCRIPTION)
        FakeGCPClient.pulls = [{"receivedMessages": [notification("m1", 10)]}, {}]

        with mock.patch("billing.services.billing.timezone.now", return_value=datetime(2026, 10, 8, 5, tzinfo=UTC)):
            response = self.sync()

        # Back by REPLAY_PERIOD, before pulling
        self.assertEqual(FakeGCPClient.seeks, ["2026-09-07T05:00:00Z"])
        self.assertEqual(response.data["received"], 1)

    def test_no_replay_once_notifications_are_stored(self):
        BillingAccount.objects.create(provider="gcp", external_id=BILLING_ID, pubsub_subscription=SUBSCRIPTION)
        FakeGCPClient.pulls = [{"receivedMessages": [notification("m1", 10)]}, {}, {}]
        self.sync()

        self.sync()

        self.assertEqual(len(FakeGCPClient.seeks), 1)

    def test_replay_on_request(self):
        BillingAccount.objects.create(provider="gcp", external_id=BILLING_ID, pubsub_subscription=SUBSCRIPTION)
        FakeGCPClient.pulls = [{"receivedMessages": [notification("m1", 10)]}, {}, {"receivedMessages": [notification("m1", 10, ack_id="again")]}, {}]
        self.sync()

        response = self.client.post(f"/api/v1/accounts/{self.account.pk}/billing/sync/", {"replay": True}, format="json")

        self.assertEqual(len(FakeGCPClient.seeks), 2)
        # Already stored before the replay
        self.assertEqual((response.data["received"], BudgetStatus.objects.count()), (0, 1))

    def test_failed_replay_is_a_warning_and_still_pulls(self):
        BillingAccount.objects.create(provider="gcp", external_id=BILLING_ID, pubsub_subscription=SUBSCRIPTION)
        FakeGCPClient.seek_response = CloudAPIError("permission denied on GCP project demo-project: pubsub.subscriptions.consume")
        FakeGCPClient.pulls = [{"receivedMessages": [notification("m1", 10)]}, {}]

        response = self.sync()

        self.assertIn("Cannot replay earlier budget notifications", response.data["warnings"][0])
        self.assertEqual(response.data["received"], 1)

    def test_redelivered_notification_is_stored_once(self):
        BillingAccount.objects.create(provider="gcp", external_id=BILLING_ID, pubsub_subscription=SUBSCRIPTION)
        FakeGCPClient.pulls = [{"receivedMessages": [notification("m1", 10)]}, {"receivedMessages": [notification("m1", 10, ack_id="again")]}, {}]

        response = self.sync()

        self.assertEqual(response.data["received"], 1)
        self.assertEqual(BudgetStatus.objects.count(), 1)

    def test_missing_billing_access_is_a_warning(self):
        denied = CloudAPIError("permission denied on GCP project demo-project: billing.budgets.list")
        FakeGCPClient.responses[f"https://cloudbilling.googleapis.com/v1/billingAccounts/{BILLING_ID}"] = denied
        FakeGCPClient.responses[f"https://billingbudgets.googleapis.com/v1/billingAccounts/{BILLING_ID}/budgets"] = denied

        response = self.sync()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["warnings"]), 2)
        self.assertIn("Cannot list budgets", response.data["warnings"][1])
        self.assertEqual(response.data["billing_account"]["external_id"], BILLING_ID)

    def test_failed_pull_is_a_warning(self):
        BillingAccount.objects.create(provider="gcp", external_id=BILLING_ID, pubsub_subscription=SUBSCRIPTION)
        FakeGCPClient.pulls = [CloudAPIError("not found on GCP (project demo-project): Resource not found")]

        response = self.sync()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Cannot pull budget notifications", response.data["warnings"][0])

    def test_project_without_billing_account(self):
        FakeGCPClient.responses["https://cloudbilling.googleapis.com/v1/projects/demo-project/billingInfo"] = {
            "billingAccountName": "", "billingEnabled": False,
        }

        response = self.sync()

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["billing_account"])
        self.assertFalse(BillingAccount.objects.exists())

    def test_spend_status_skips_budgets_of_other_projects(self):
        self.sync()
        budget = Budget.objects.get()
        other = Budget.objects.create(
            billing_account=budget.billing_account, external_id="other", projects=["999"], amount=10,
        )
        for target, cost, message_id in [(budget, 30, "a"), (other, 9, "b")]:
            BudgetStatus.objects.create(
                budget=target, message_id=message_id, cost_amount=cost, budget_amount=target.amount,
                currency="USD", interval_start="2026-10-01T00:00Z", published_at="2026-10-07T00:00Z",
            )

        status = spend_status(self.account)

        self.assertEqual(status["budget_id"], budget.pk)


class BillingTaskTests(APITestCase):
    def test_schedule_queues_usable_accounts_only(self):
        usable = Account.objects.create(
            name="usable", provider="gcp", external_id="1", project_id="usable", credential_ref="/unused.json", is_valid=True,
        )
        Account.objects.create(name="unvalidated", provider="gcp", external_id="2", project_id="other", credential_ref="/unused.json")

        with mock.patch.object(sync_account_billing, "apply_async") as apply_async:
            sync_all_billing()

        apply_async.assert_called_once_with((usable.pk,), expires=15 * 60)

    def test_sync_warnings_are_logged(self):
        result = {"billing": None, "warnings": ["Cannot list budgets: permission denied"], "received": 0}

        with mock.patch("billing.tasks.sync_billing", return_value=result), self.assertLogs("billing.tasks", "WARNING") as logs:
            sync_account_billing(1)

        self.assertIn("Cannot list budgets", logs.output[0])


class BillingAccountUpdateTests(APITestCase):
    def setUp(self):
        self.billing_account = BillingAccount.objects.create(provider="gcp", external_id=BILLING_ID)
        self.url = f"/api/v1/billing-accounts/{self.billing_account.pk}/"

    def test_set_subscription(self):
        response = self.client.patch(self.url, {"pubsub_subscription": f" {SUBSCRIPTION} "}, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.billing_account.refresh_from_db()
        self.assertEqual(self.billing_account.pubsub_subscription, SUBSCRIPTION)

    def test_rejects_short_subscription_name(self):
        response = self.client.patch(self.url, {"pubsub_subscription": "cloudhop-budget"}, format="json")

        self.assertEqual(response.status_code, 400)

    def test_other_fields_are_read_only(self):
        self.client.patch(self.url, {"external_id": "changed", "name": "x"}, format="json")

        self.billing_account.refresh_from_db()
        self.assertEqual((self.billing_account.external_id, self.billing_account.name), (BILLING_ID, ""))
