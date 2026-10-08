from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models.account import Account
from clusters.models.cluster import Clusters
from clusters.serializers.cluster import ClusterSerializer
from clusters.services.cluster import AccountNotUsable, sync_clusters
from common.cloud.errors import CloudAPIError, InvalidCredential, UnsupportedProvider


class ClusterListView(generics.ListAPIView):
    """GET: the account's stored clusters, active ones only unless ?include_inactive=true."""

    serializer_class = ClusterSerializer

    def get_queryset(self):
        queryset = Clusters.objects.filter(account_id=self.kwargs['account_id'])
        if self.request.query_params.get('include_inactive') != 'true':
            queryset = queryset.filter(is_active=True)
        return queryset.order_by('name')


class ClusterDetailView(generics.RetrieveAPIView):
    """GET: one stored cluster."""

    queryset = Clusters.objects.all()
    serializer_class = ClusterSerializer


class ClusterSyncView(APIView):
    """POST: fetch the account's clusters from its cloud provider and store them."""

    def post(self, request, account_id):
        try:
            clusters = sync_clusters(account_id)
        except Account.DoesNotExist:
            return Response({'detail': f'account {account_id} not found'}, status=status.HTTP_404_NOT_FOUND)
        except (AccountNotUsable, InvalidCredential, UnsupportedProvider) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except CloudAPIError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(ClusterSerializer(clusters, many=True).data)
