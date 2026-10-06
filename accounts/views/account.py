from rest_framework import mixins, viewsets

from ..models import Account
from ..serializers import AccountSerializer
from ..services import delete_credential_file


class AccountViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,    # INSERT
    mixins.UpdateModelMixin,    # UPDATE
    mixins.DestroyModelMixin,   # DELETE
    viewsets.GenericViewSet,
):
    queryset = Account.objects.order_by('name')
    serializer_class = AccountSerializer

    def perform_destroy(self, instance):
        instance.delete()
        delete_credential_file(instance.credential_ref)
