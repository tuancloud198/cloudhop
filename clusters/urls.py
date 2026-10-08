from django.urls import path

from clusters.views import ClusterDetailView, ClusterListView, ClusterSyncView

urlpatterns = [
    path("accounts/<int:account_id>/clusters/", ClusterListView.as_view(), name="cluster-list"),
    path("accounts/<int:account_id>/clusters/sync/", ClusterSyncView.as_view(), name="cluster-sync"),
    path("clusters/<int:pk>/", ClusterDetailView.as_view(), name="cluster-detail"),
]
