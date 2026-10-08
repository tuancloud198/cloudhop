# Loaded with Django, so @shared_task binds to this app
from cloudhop.celery import celery_app

__all__ = ["celery_app"]
