import re

from rest_framework import serializers

from billing.models.account_billing import AccountBilling
from billing.models.billing_account import BillingAccount
from billing.models.budget import Budget
from billing.models.budget_status import BudgetStatus
from billing.services.billing import budget_covers, spend_status

SUBSCRIPTION_PATTERN = re.compile(r"projects/[^/{}\s]+/subscriptions/[A-Za-z][\w.~+%-]{2,254}")


class BudgetStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = BudgetStatus
        fields = [
            'cost_amount', 'budget_amount', 'currency', 'interval_start',
            'threshold_exceeded', 'forecast_threshold_exceeded', 'published_at',
        ]
        read_only_fields = fields


class BudgetSerializer(serializers.ModelSerializer):
    latest_status = serializers.SerializerMethodField()
    # Needs the AccountBilling as "billing" in the serializer context; null without it
    covers_account = serializers.SerializerMethodField()

    class Meta:
        model = Budget
        fields = [
            'id', 'external_id', 'name', 'amount', 'currency', 'period', 'start_date', 'end_date',
            'credit_treatment', 'projects', 'has_other_filters', 'pubsub_topic', 'is_active',
            'latest_status', 'covers_account', 'updated_at',
        ]
        read_only_fields = fields

    def get_latest_status(self, budget):
        latest = budget.statuses.order_by('-published_at').first()
        return BudgetStatusSerializer(latest).data if latest else None

    def get_covers_account(self, budget):
        billing = self.context.get('billing')
        return budget_covers(budget, billing) if billing else None


class BillingAccountSerializer(serializers.ModelSerializer):
    budgets = serializers.SerializerMethodField()

    class Meta:
        model = BillingAccount
        fields = [
            'id', 'provider', 'external_id', 'name', 'currency', 'is_open',
            'pubsub_subscription', 'budgets', 'updated_at',
        ]
        read_only_fields = [field for field in fields if field != 'pubsub_subscription']

    def get_budgets(self, billing_account):
        budgets = billing_account.budgets.filter(is_active=True).order_by('name')
        return BudgetSerializer(budgets, many=True, context=self.context).data

    def validate_pubsub_subscription(self, value):
        value = value.strip()
        if value and not SUBSCRIPTION_PATTERN.fullmatch(value):
            raise serializers.ValidationError(
                'give the full name, e.g. projects/my-project/subscriptions/cloudhop-budget'
            )
        return value


class AccountBillingSerializer(serializers.ModelSerializer):
    billing_account = serializers.SerializerMethodField()
    # The covering budget closest to being used up; see billing.services.spend_status
    spend = serializers.SerializerMethodField()

    class Meta:
        model = AccountBilling
        fields = ['account', 'billing_account', 'billing_enabled', 'project_number', 'synced_at', 'spend']
        read_only_fields = fields

    def get_billing_account(self, billing):
        if billing.billing_account is None:
            return None
        context = {**self.context, 'billing': billing}
        return BillingAccountSerializer(billing.billing_account, context=context).data

    def get_spend(self, billing):
        status = spend_status(billing.account)
        if status is None:
            return None
        return {
            **status,
            'spent': str(status['spent']),
            'amount': str(status['amount']),
        }
