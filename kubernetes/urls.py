from django.urls import path

from kubernetes.views.resource import ResourceListView, ResourceSummaryView, ResourceSyncView

urlpatterns = [
    path("clusters/<int:cluster_id>/resources/", ResourceListView.as_view(), name="resource-list"),
    path("clusters/<int:cluster_id>/resources/summary/", ResourceSummaryView.as_view(), name="resource-summary"),
    path("clusters/<int:cluster_id>/resources/sync/", ResourceSyncView.as_view(), name="resource-sync"),
]
