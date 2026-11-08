import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
app = Celery("club_platform", task_cls="club.system_task:SystemGuardedTask")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Register durable contest task tracking in publishers and workers.
import club.contest_task_monitor  # noqa: E402,F401
