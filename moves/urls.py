from django.urls import path

from moves.views.move import MoveCancelView, MoveDetailView, MoveListView, MoveRetryView

urlpatterns = [
    path("moves/", MoveListView.as_view(), name="move-list"),
    path("moves/<int:pk>/", MoveDetailView.as_view(), name="move-detail"),
    path("moves/<int:pk>/cancel/", MoveCancelView.as_view(), name="move-cancel"),
    path("moves/<int:pk>/retry/", MoveRetryView.as_view(), name="move-retry"),
]
