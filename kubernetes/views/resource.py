from rest_framework import generics, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from clusters.models import Clusters
from common.cloud import CloudAPIError, InvalidCredential, UnsupportedProvider
from kubernetes.models import KubeResource
from kubernetes.serializers import KubeResourceSerializer
from kubernetes.services import ClusterNotUsable, resource_summary, sync_resources


class ResourceSyncView(APIView):
    """POST: fetch every native Kubernetes object in the cluster and store them."""

    def post(self, request, cluster_id):
        try:
            result = sync_resources(cluster_id)
        except Clusters.DoesNotExist:
            return Response({'detail': f'cluster {cluster_id} not found'}, status=status.HTTP_404_NOT_FOUND)
        except (ClusterNotUsable, InvalidCredential, UnsupportedProvider) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except CloudAPIError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(result)


class ResourceListView(generics.ListAPIView):
    """GET: stored objects of the cluster, filtered by ?group=, ?kind=, ?namespace= and ?search= (in name)."""

    serializer_class = KubeResourceSerializer
    # A cluster can hold thousands of objects
    pagination_class = LimitOffsetPagination

    def get_queryset(self):
        queryset = KubeResource.objects.filter(cluster_id=self.kwargs['cluster_id'])
        for field in ('group', 'kind', 'namespace'):
            value = self.request.query_params.get(field)
            if value is not None:
                queryset = queryset.filter(**{field: value})
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(name__icontains=search)
        return queryset.order_by('group', 'kind', 'namespace', 'name')


class ResourceSummaryView(APIView):
    """GET: kinds (with counts) and namespaces of the cluster's stored objects, and when they were synced."""

    def get(self, request, cluster_id):
        if not Clusters.objects.filter(pk=cluster_id).exists():
            return Response({'detail': f'cluster {cluster_id} not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response(resource_summary(cluster_id))
