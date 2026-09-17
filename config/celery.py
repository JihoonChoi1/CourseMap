"""Celery app (Phase 4). Config comes from Django settings' CELERY_* values.

Run the worker (after bringing up MySQL and Redis first with docker compose up -d):

    .venv/bin/celery -A config worker -l info

Must use the prefork pool (the default) for the soft time limit (TIMEOUT handling) to work.
The solo/threads pools don't support time limits.
"""

import os
import sys

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# On macOS, billiard spawns prefork children via spawn instead of fork. Celery 5.6 skips task
# execution setup in a spawned child if this variable is missing, causing every task to fail with
# "ValueError: not enough values to unpack (expected 3, got 0)" (the same workaround used on Windows).
if sys.platform == "darwin":
    os.environ.setdefault("FORKED_BY_MULTIPROCESSING", "1")

app = Celery("coursemap")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
