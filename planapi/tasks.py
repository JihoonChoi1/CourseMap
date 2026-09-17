"""Celery task (Phase 4). The engine knows nothing about Celery — this just calls views' sync flow (compute) directly.

Engine selection (Phase 5): if settings.PLAN_ENGINE is "go", goengine.compute is called as a
subprocess instead of views.compute. Result verification (verify_plan) and failure handling are
the same in both cases.

S3 export (Phase 6): once the job is stored as SUCCESS, if settings.S3_EXPORT_ENABLED the result is
uploaded via planapi.s3export. An upload failure doesn't change the job status (only
export_status=FAILED is recorded; retry with manage.py export_plan_jobs).

Failure handling:
- No retries. The engine is deterministic, so the same input always produces the same result.
  A FAILED job is excluded from reuse, so resending the same request creates a new job (manual retry).
- Hitting the soft time limit (120s) marks the job FAILED with TIMEOUT.
- The hard time limit (150s) is a safety net for when even soft handling stalls. In that case Celery
  kills the worker process without notifying the task code, so the job stays RUNNING (automatic
  detection of stuck jobs is out of scope here).
"""

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings

from planapi import goengine, jobs, s3export, views
from planapi.models import PlanJob

SOFT_TIME_LIMIT = 120
HARD_TIME_LIMIT = 150


@shared_task(name="planapi.run_plan_job", soft_time_limit=SOFT_TIME_LIMIT, time_limit=HARD_TIME_LIMIT)
def run_plan_job(job_id: str):
    if not jobs.claim(job_id):
        return
    snapshot = PlanJob.objects.values_list("input", flat=True).get(id=job_id)
    try:
        catalog, programs, student = views.parse_snapshot(snapshot)
        if settings.PLAN_ENGINE == "go":
            res, violations = goengine.compute(snapshot, catalog, programs, student)
        else:
            res, violations = views.compute(catalog, programs, student)
    except SoftTimeLimitExceeded:
        jobs.finish_failed(job_id, "TIMEOUT", "계산이 " + str(SOFT_TIME_LIMIT) + "초 제한을 넘어 중단됨")
        return
    except Exception as e:
        jobs.finish_failed(job_id, "WORKER_ERROR", type(e).__name__ + ": " + str(e))
        return

    if len(violations) > 0:
        jobs.finish_failed(job_id, "VERIFY_FAILED", views.VERIFY_FAILED_DETAIL, violations)
        return
    jobs.finish_success(job_id, res)
    if settings.S3_EXPORT_ENABLED:
        try:
            s3export.export_job(job_id)
        except SoftTimeLimitExceeded:
            # computation already finished, so leave the job as SUCCESS
            PlanJob.objects.filter(id=job_id).update(export_status=PlanJob.EXPORT_FAILED,
                                                     export_error="S3 업로드 중 soft time limit 초과")
        except Exception as e:  # boto exceptions are handled inside export_job. Other exceptions also don't change job status
            PlanJob.objects.filter(id=job_id).update(export_status=PlanJob.EXPORT_FAILED,
                                                     export_error=type(e).__name__ + ": " + str(e))
