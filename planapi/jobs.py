"""PlanJob creation/reuse, status transitions, response shape. Computation itself is views.compute (same flow as sync)."""

import hashlib
import json

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from engine.planner import PlanResult

from planapi import s3export
from planapi.models import PlanJob


def input_hash(snapshot: dict) -> str:
    """sha256 of the normalized JSON for a snapshot (student with overrides applied + catalog + programs).

    Only dict keys are sorted; list order is preserved (since course/track order affects engine
    results). Since the data itself is embedded in the hash, changing content via load_data
    changes the key too.
    """
    text = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_or_reuse(student_code: str, params: dict, snapshot: dict) -> tuple[PlanJob, bool]:
    """Return the existing live job (PENDING/RUNNING/SUCCESS) for the same input if there is one, otherwise a new PENDING job."""
    key = input_hash(snapshot)
    existing = PlanJob.objects.filter(active_key=key).first()
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():
            job = PlanJob.objects.create(student_code=student_code, params=_dump(params), input=snapshot,
                                         input_hash=key, active_key=key, status=PlanJob.PENDING)
        return job, True
    except IntegrityError:
        # a concurrent request with the same input got created first (active_key is unique)
        existing = PlanJob.objects.filter(active_key=key).first()
        if existing is None:
            raise
        return existing, False


def claim(job_id: str) -> bool:
    """PENDING -> RUNNING. Returns False if the job is already being processed, already finished (duplicate delivery), or deleted."""
    n = PlanJob.objects.filter(id=job_id, status=PlanJob.PENDING).update(status=PlanJob.RUNNING,
                                                                         started_at=timezone.now())
    return n == 1


def finish_success(job_id: str, res: PlanResult):
    # If export is enabled, mark it PENDING in the same update. A lookup right after SUCCESS but
    # before export completes can then know "it's about to be uploaded" (the export result is
    # later flipped to SUCCESS/FAILED by planapi.s3export).
    export_status = PlanJob.EXPORT_PENDING if settings.S3_EXPORT_ENABLED else None
    PlanJob.objects.filter(id=job_id).update(status=PlanJob.SUCCESS, plan=_dump(res.plan),
                                             engine=_dump(engine_dict(res)), finished_at=timezone.now(),
                                             export_status=export_status)


def finish_failed(job_id: str, code: str, detail: str, violations: list[str] | None = None):
    error = {"code": code, "detail": detail}
    if violations is not None:
        error["violations"] = violations
    # clear active_key so the next request with the same input creates a new job (manual retry)
    PlanJob.objects.filter(id=job_id).update(status=PlanJob.FAILED, error=_dump(error), active_key=None,
                                             finished_at=timezone.now())


def engine_dict(res: PlanResult) -> dict:
    """Values corresponding to the sync response's X- headers. min_terms_proven is only meaningful when feasible."""
    proven = None
    if res.plan["feasible"]:
        proven = res.min_terms_proven
    return {"candidates": res.candidates, "used_fallback": res.used_fallback, "min_terms_proven": proven}


def job_dict(job: PlanJob) -> dict:
    return {
        "job_id": str(job.id),
        "status": job.status,
        "student_id": job.student_code,
        "params": _load(job.params),
        "created_at": _iso(job.created_at),
        "finished_at": _iso(job.finished_at),
        "plan": _load(job.plan),
        "engine": _load(job.engine),
        "error": _load(job.error),
        "export": export_dict(job),
    }


def export_dict(job: PlanJob) -> dict | None:
    """S3 export status (Phase 6). None for jobs that never attempted export (pre-SUCCESS, FAILED, export disabled).

    url is a presigned GET URL, freshly signed on every lookup, only present when SUCCESS.
    """
    if job.export_status is None:
        return None
    url = None
    if job.export_status == PlanJob.EXPORT_SUCCESS:
        url = s3export.presigned_url(job.export_key)
    return {"status": job.export_status, "bucket": settings.S3_BUCKET, "key": job.export_key, "url": url,
            "exported_at": _iso(job.exported_at), "error": job.export_error}


def _dump(value) -> str:
    """JSON text that preserves key order (see models.PlanJob)."""
    return json.dumps(value, ensure_ascii=False)


def _load(text: str | None):
    if text is None:
        return None
    return json.loads(text)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")
