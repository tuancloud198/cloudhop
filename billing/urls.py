from django.urls import path

from billing.views import AccountBillingSyncView, AccountBillingView, BillingAccountDetailView

urlpatterns = [
    path("accounts/<int:account_id>/billing/", AccountBillingView.as_view(), name="account-billing"),
    path("accounts/<int:account_id>/billing/sync/", AccountBillingSyncView.as_view(), name="account-billing-sync"),
    path("billing-accounts/<int:pk>/", BillingAccountDetailView.as_view(), name="billing-account-detail"),
]
