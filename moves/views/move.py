from django.db import transaction
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.cloud.errors import CloudAPIError
from moves.models.move import Move
from moves.serializers.move import MoveCreateSerializer, MoveDetailSerializer, MoveSerializer
from moves.services.move import InvalidMove, cancel_move, create_move, retry_move
from moves.tasks import advance_move


class MoveListView(generics.ListAPIView):
    """GET: moves, newest first. POST: start a move; it runs in the background."""

    serializer_class = MoveSerializer
    queryset = Move.objects.select_related('source_cluster__account_id', 'target_cluster__account_id').order_by('-pk')

    def post(self, request):
        data = MoveCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            move = create_move(**data.validated_data)
        except (InvalidMove, CloudAPIError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        transaction.on_commit(lambda: advance_move.delay(move.pk))
        return Response(MoveDetailSerializer(move).data, status=status.HTTP_201_CREATED)


class MoveDetailView(generics.RetrieveAPIView):
    """GET: one move with its log."""

    serializer_class = MoveDetailSerializer
    queryset = Move.objects.select_related('source_cluster__account_id', 'target_cluster__account_id')


class MoveCancelView(APIView):
    """POST: stop the move after its current step; nothing it did is undone."""

    def post(self, request, pk):
        try:
            move = cancel_move(pk)
        except Move.DoesNotExist:
            return Response({'detail': f'move {pk} not found'}, status=status.HTTP_404_NOT_FOUND)
        except InvalidMove as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(MoveDetailSerializer(move).data)


class MoveRetryView(APIView):
    """POST: run a failed move again from the step it failed at."""

    def post(self, request, pk):
        try:
            move = retry_move(pk)
        except Move.DoesNotExist:
            return Response({'detail': f'move {pk} not found'}, status=status.HTTP_404_NOT_FOUND)
        except InvalidMove as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        transaction.on_commit(lambda: advance_move.delay(move.pk))
        return Response(MoveDetailSerializer(move).data)
