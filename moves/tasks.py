from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from moves.models.move import Move
from moves.services.move import ACTIVE, advance

# A running move is checked every few seconds; one not checked for this long lost its task
STALE_AFTER = timedelta(minutes=5)


@shared_task
def advance_move(move_id: int):
    """Run the move's current step, and queue the next run until the move is finished."""
    delay = advance(move_id)
    if delay is not None:
        advance_move.apply_async((move_id,), countdown=delay)


@shared_task
def resume_moves():
    """Queue running moves whose next run was lost, e.g. when the worker restarted; run on a schedule."""
    stale = timezone.now() - STALE_AFTER
    moves = Move.objects.filter(status__in=ACTIVE).exclude(checked_at__gte=stale).exclude(created_at__gte=stale)
    for move_id in moves.values_list("pk", flat=True):
        advance_move.delay(move_id)
