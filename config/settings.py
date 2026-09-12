"""Phase 3-6 settings. MySQL + sync/async endpoints (Celery + Redis) + selectable Go engine + S3 export. Values can be overridden via environment variables."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "rest_framework",
    "planapi",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"

# Uses a single web page (planapi/templates/planapi/index.html). No static files; CSS/JS live inline in the template.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {},
    }
]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ.get("MYSQL_DATABASE", "coursemap"),
        "USER": os.environ.get("MYSQL_USER", "coursemap"),
        "PASSWORD": os.environ.get("MYSQL_PASSWORD", "coursemap"),
        "HOST": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "PORT": os.environ.get("MYSQL_PORT", "3306"),
        "OPTIONS": {"charset": "utf8mb4"},
        "TEST": {"NAME": "test_coursemap", "CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

# Local data directory (default for manage.py load_data)
DATA_DIR = BASE_DIR / "data"

# No auth/sessions. JSON responses only.
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
}

# ---- Phase 4: Celery ----
# Redis is used only as the broker. Job state and results are stored in the MySQL PlanJob table, so no result backend is configured.
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# The queue plan jobs are pushed to and workers consume from. Workers also consume this queue by default.
# Integration tests use a different queue name so a running dev worker doesn't pick up test jobs.
CELERY_TASK_DEFAULT_QUEUE = os.environ.get("CELERY_QUEUE", "coursemap")
# A single job can take tens of seconds in the worst case, so limit each worker process to prefetching 1 job at a time
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# ---- Phase 5: Go engine ----
# The engine used by async jobs (Celery workers). "python" (default, reference implementation) or "go" (invokes GO_ENGINE_BIN as a subprocess).
# The sync endpoint always uses the Python engine. Evidence that the two engines produce the same results lives in parity/run.py.
PLAN_ENGINE = os.environ.get("PLAN_ENGINE", "python")
if PLAN_ENGINE not in ("python", "go"):
    raise ImproperlyConfigured("PLAN_ENGINE must be python or go: " + PLAN_ENGINE)
# Build with: cd goengine && go build -o bin/courseplan ./cmd/courseplan
GO_ENGINE_BIN = os.environ.get("GO_ENGINE_BIN", str(BASE_DIR / "goengine" / "bin" / "courseplan"))

# ---- Phase 6: S3 export ----
# When an async job reaches SUCCESS, the worker uploads {job_id, student_id, params, plan, engine, input, exported_at}
# to s3://S3_BUCKET/plans/{student_id}/{job_id}.json (planapi.s3export). If the upload fails, the job still stays
# SUCCESS and only the export status is recorded as FAILED. Retry with: manage.py export_plan_jobs
S3_EXPORT_ENABLED = os.environ.get("S3_EXPORT_ENABLED", "1") == "1"
S3_BUCKET = os.environ.get("S3_BUCKET", "coursemap-plans")
# Defaults to the local S3-compatible server from docker compose. When pointing at real AWS S3, set S3_ENDPOINT_URL to an empty string.
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "http://127.0.0.1:9000") or None
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
# The defaults are local-container-only dev values (matching docker-compose.yml, treated the same as MYSQL_PASSWORD).
# On real AWS, leave S3_ACCESS_KEY_ID/S3_SECRET_ACCESS_KEY empty to use boto3's default credential chain
# (AWS_* environment variables, ~/.aws, IAM roles). Never put real keys in this file or the repo.
S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID", "coursemap-dev") or None
S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY", "coursemap-dev-secret") or None
# Validity period (seconds) for the presigned GET URL included in job-lookup responses
S3_PRESIGN_EXPIRES = int(os.environ.get("S3_PRESIGN_EXPIRES", "3600"))
