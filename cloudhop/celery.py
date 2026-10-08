import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cloudhop.settings")

app = Celery("cloudhop")
# Settings named CELERY_* configure it, e.g. CELERY_BROKER_URL
app.config_from_object("django.conf:settings", namespace="CELERY")
# Finds the tasks.py of each installed app
app.autodiscover_tasks()
