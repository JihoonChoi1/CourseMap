"""Export Plan results to S3 (Phase 6).

Target: SUCCESS jobs (includes infeasible Plans, since those are also SUCCESS). FAILED jobs are never uploaded.
Object: s3://{S3_BUCKET}/plans/{student_id}/{job_id}.json, UTF-8 JSON
    {"job_id", "student_id", "params", "plan", "engine", "input", "exported_at"}
    plan, engine, params match the job-lookup response values (including key order); input is the job's input snapshot
    ({"catalog", "programs", "student"}, shaped like data/*.json).
Timing: right after the worker stores the job as SUCCESS (planapi.tasks). Retries/batch processing: manage.py export_plan_jobs.
Failure: the job stays SUCCESS; only export_status=FAILED and export_error record the reason. Re-uploading to the
     same key overwrites it, so retries are idempotent.
Access: the job-lookup response's export.url (a presigned GET URL, valid for S3_PRESIGN_EXPIRES seconds). Freshly signed on every lookup.

Endpoint/credentials come from settings' S3_*. Locally this is the S3-compatible server from docker
compose; on real AWS, S3_ENDPOINT_URL is left empty and boto3's default credential chain is used.
Error messages only include the exception type and message, never key values.
"""

import json

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.utils import timezone

from planapi.models import PlanJob

# Kept short so it finishes within the worker's soft time limit (120s). Even if S3 is down, job processing isn't blocked for long.
_CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "path"}, connect_timeout=2, read_timeout=10,
                 retries={"mode": "standard", "max_attempts": 2})


def client():
    return boto3.client("s3", endpoint_url=settings.S3_ENDPOINT_URL, region_name=settings.S3_REGION,
                        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
                        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY, config=_CONFIG)


def object_key(student_id: str, job_id: str) -> str:
    return "plans/" + student_id + "/" + job_id + ".json"


def document(job: PlanJob, exported_at: str) -> dict:
    return {
        "job_id": str(job.id),
        "student_id": job.student_code,
        "params": json.loads(job.params),
        "plan": json.loads(job.plan),
        "engine": json.loads(job.engine),
        "input": job.input,
        "exported_at": exported_at,
    }


def export_job(job_id: str) -> bool:
    """Upload one SUCCESS job and update its export fields. Returns True on success, False if the job doesn't exist or isn't SUCCESS."""
    job = PlanJob.objects.filter(id=job_id, status=PlanJob.SUCCESS).first()
    if job is None:
        return False
    key = object_key(job.student_code, str(job.id))
    now = timezone.now()
    body = json.dumps(document(job, _iso(now)), ensure_ascii=False, indent=2).encode("utf-8")
    try:
        client().put_object(Bucket=settings.S3_BUCKET, Key=key, Body=body,
                            ContentType="application/json; charset=utf-8")
    except (BotoCoreError, ClientError) as e:
        PlanJob.objects.filter(id=job.id).update(export_status=PlanJob.EXPORT_FAILED, export_key=key,
                                                 export_error=type(e).__name__ + ": " + str(e), exported_at=None)
        return False
    PlanJob.objects.filter(id=job.id).update(export_status=PlanJob.EXPORT_SUCCESS, export_key=key, export_error=None,
                                             exported_at=now)
    return True


def presigned_url(key: str) -> str | None:
    """Presigned URL for GET. Signing is a local computation and doesn't touch the network. Returns None if there are no credentials."""
    try:
        return client().generate_presigned_url("get_object", Params={"Bucket": settings.S3_BUCKET, "Key": key},
                                               ExpiresIn=settings.S3_PRESIGN_EXPIRES)
    except BotoCoreError:
        return None


def ensure_bucket() -> bool:
    """Create the bucket if it doesn't exist. Returns True if it was newly created."""
    s3 = client()
    try:
        s3.head_bucket(Bucket=settings.S3_BUCKET)
        return False
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
            raise
    s3.create_bucket(Bucket=settings.S3_BUCKET)
    return True


def _iso(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z")
