from rest_framework.routers import DefaultRouter

from accounts.views.account import AccountViewSet

router = DefaultRouter()
router.register("accounts", AccountViewSet, basename="account")

urlpatterns = router.urls