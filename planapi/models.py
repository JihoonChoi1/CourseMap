"""Tables that mirror data/*.json directly.

Semantic validation of engine input (duplicate ids, references to missing courses, cycles, etc.
from doc §5) is done by the engine, not by DB constraints. That's why course id is not unique, and
fields that point at courses (prereqs, coreqs, offered, Group.courses, Student.completed) are not
FKs but JSON lists of id strings — if the DB rejected them first, we'd lose the chance to report
them as engine error codes (E_DUP_ID, E_UNKNOWN_REF, etc.).
Order affects engine results (deterministic sorting, candidate order), so lists preserve file order via position.
"""

import uuid

from django.db import models


class CatalogMeta(models.Model):
    """Top-level catalog.json settings. Exactly one row after loading."""
    seasons = models.JSONField()  # []string, term order within a year


class Course(models.Model):
    position = models.PositiveIntegerField()             # order within catalog.json courses[]
    code = models.CharField(max_length=32, db_index=True)  # course id (uniqueness is judged by the engine's E_DUP_ID)
    title = models.CharField(max_length=200)
    credits = models.IntegerField()
    offered = models.JSONField()                          # []string
    prereqs = models.JSONField()                          # [][]string CNF (doc §2.2)
    coreqs = models.JSONField()                           # [][]string CNF

    class Meta:
        ordering = ["position"]


class Program(models.Model):
    DEGREE = "DEGREE"
    TRACK = "TRACK"

    kind = models.CharField(max_length=8)                 # DEGREE (exactly 1) | TRACK
    position = models.PositiveIntegerField()              # order within tracks[] (0 for degree)
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=200)

    class Meta:
        ordering = ["kind", "position"]


class Group(models.Model):
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="groups")
    position = models.PositiveIntegerField()              # order within program.groups[]
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=200)
    rule = models.CharField(max_length=16)                # "ALL" | "PICK_N" (anything else is the engine's E_UNKNOWN_RULE)
    n = models.IntegerField()
    courses = models.JSONField()                          # []string

    class Meta:
        ordering = ["program", "position"]


class Student(models.Model):
    code = models.CharField(max_length=32, unique=True)  # student id (endpoint lookup key)
    name = models.CharField(max_length=200)
    track = models.CharField(max_length=32)              # a nonexistent track is the engine's E_UNKNOWN_TRACK
    completed = models.JSONField()                       # []string
    start_year = models.IntegerField()
    start_season = models.CharField(max_length=16)
    num_terms = models.IntegerField()
    max_credits_per_term = models.IntegerField()


class PlanJob(models.Model):
    """Asynchronous plan job (Phase 4). Status and results are stored here (no Celery result backend is used).

    input is a snapshot taken when the job was created (catalog, programs, and the student with
    overrides applied — same shape as data/*.json). The worker computes from this snapshot alone,
    so even if load_data runs in the meantime, the result reflects the data as of the request.
    The student is kept as a code string rather than an FK, since load_data deletes and recreates
    all student rows.
    """
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student_code = models.CharField(max_length=32)
    input = models.JSONField()                           # {"catalog", "programs", "student"} snapshot
    input_hash = models.CharField(max_length=64, db_index=True)  # sha256 of the normalized input JSON
    # Reuse key. Equal to input_hash while PENDING/RUNNING/SUCCESS; set to NULL once FAILED (excluded from reuse).
    # MySQL's unique index allows multiple NULLs, so the DB guarantees "at most one live job per input".
    active_key = models.CharField(max_length=64, null=True, unique=True)
    status = models.CharField(max_length=8)
    # Values that go straight into the response are stored as JSON "text". MySQL's JSON type
    # reorders object keys on storage (e.g. Plan would come back with terms, chosen, ... in a
    # different order), so text is required to preserve the same key order as the sync response.
    # Reading and writing is done by planapi.jobs.
    params = models.TextField()                          # {"max_credits": int|null, "num_terms": int|null} request values
    plan = models.TextField(null=True)                   # SUCCESS: Plan (doc §2.6)
    engine = models.TextField(null=True)                 # SUCCESS: {candidates, used_fallback, min_terms_proven}
    error = models.TextField(null=True)                  # FAILED: {code, detail[, violations]}
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    # Phase 6: S3 export (planapi.s3export). Only applies to SUCCESS jobs. Independent of job
    # status — if the upload fails, the job still stays SUCCESS and only this field becomes FAILED.
    EXPORT_PENDING = "PENDING"
    EXPORT_SUCCESS = "SUCCESS"
    EXPORT_FAILED = "FAILED"

    export_status = models.CharField(max_length=8, null=True)  # NULL (not applicable/disabled) | PENDING | SUCCESS | FAILED
    export_key = models.CharField(max_length=512, null=True)   # S3 object key (if an attempt was made)
    export_error = models.TextField(null=True)                 # FAILED: exception summary (never includes credentials)
    exported_at = models.DateTimeField(null=True)              # SUCCESS: upload timestamp (matches the object's exported_at)
