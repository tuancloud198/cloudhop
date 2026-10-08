from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Account
from billing.models import AccountBilling, BillingAccount
from billing.serializers import AccountBillingSerializer, BillingAccountSerializer
from billing.services import AccountNotUsable, sync_billing
from common.cloud import CloudAPIError, InvalidCredential, UnsupportedProvider


class AccountBillingView(APIView):
    """GET: the account's stored billing; synced_at is null before the first billing sync."""

    def get(self, request, account_id):
        if not Account.objects.filter(pk=account_id).exists():
            return Response({'detail': f'account {account_id} not found'}, status=status.HTTP_404_NOT_FOUND)
        billing = AccountBilling.objects.filter(account_id=account_id).first()
        if billing is None:
            return Response({
                'account': account_id, 'billing_account': None, 'billing_enabled': False,
                'project_number': '', 'synced_at': None, 'spend': None,
            })
        return Response(AccountBillingSerializer(billing).data)


class AccountBillingSyncView(APIView):
    """POST: read the account's billing account and budgets, and pull budget notifications.

    {"replay": true} first asks the provider again for the notifications it still keeps,
    acknowledged ones included. The response adds warnings (what could not be read) and
    received (notifications stored).
    """

    def post(self, request, account_id):
        try:
            result = sync_billing(account_id, replay=request.data.get('replay') is True)
        except Account.DoesNotExist:
            return Response({'detail': f'account {account_id} not found'}, status=status.HTTP_404_NOT_FOUND)
        except (AccountNotUsable, InvalidCredential, UnsupportedProvider) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except CloudAPIError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({
            **AccountBillingSerializer(result['billing']).data,
            'warnings': result['warnings'],
            'received': result['received'],
        })


class BillingAccountDetailView(generics.RetrieveUpdateAPIView):
    """GET: one billing account with its budgets. PATCH: set pubsub_subscription."""

    queryset = BillingAccount.objects.all()
    serializer_class = BillingAccountSerializer
    http_method_names = ['get', 'patch', 'head', 'options']
