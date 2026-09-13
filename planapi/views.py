"""Plan endpoints. One synchronous (Phase 3) + two asynchronous (Phase 4).

## Sync: GET /api/students/{student_id}/plan?max_credits=N&num_terms=N

Body: Plan JSON (doc §2.6, same as the CLI output). Internal engine info is given only via headers, not the body.
    X-Candidates          number of target-set candidates evaluated
    X-Used-Fallback       whether only a single cost-based candidate was tried due to combination explosion (true/false)
    X-Min-Terms-Proven    only when feasible. Whether it's proven no placement graduates earlier than this (true/false)

Status codes:
    200  Plan (feasible, infeasible, and static validation errors all go here — errors live in Plan.errors)
    400  malformed query parameter (corresponds to a loader InputError)       {"detail": ...}
    404  unknown student                                                     {"detail": ...}
    500  catalog not loaded/malformed, or independent verification (verify) failed   {"detail": ...}

## Async: POST /api/students/{student_id}/plan-jobs?max_credits=N&num_terms=N

400/404/500 (data not loaded/malformed) are decided right here using the same rules as sync, and no
job is created in that case. If it passes, a job is created with a snapshot of the data as of the
request and handed to the worker (planapi.tasks.run_plan_job).
    202  new job created                             {"job_id", "status"} + Location
    200  a job with the same input (snapshot hash) already exists as PENDING/RUNNING/SUCCESS, so it's reused (same body/headers)
    503  couldn't enqueue the job (Redis). No job is left behind.   {"detail": ...}

## Async: GET /api/plan-jobs/{job_id}

    200  {"job_id", "status", "student_id", "params", "created_at", "finished_at", "plan", "engine", "error", "export"}
         status: PENDING | RUNNING | SUCCESS | FAILED
         SUCCESS  plan = the Plan as-is (infeasible is also SUCCESS), engine = {candidates, used_fallback, min_terms_proven}
                  (min_terms_proven is null if infeasible — same as the header being omitted in sync)
         FAILED   error = {code, detail[, violations]}. Only occurs for cases that would have been 500 in sync.
                  code: VERIFY_FAILED (engine result failed independent verification) | TIMEOUT (soft time limit) | WORKER_ERROR (exception)
         export   (Phase 6) S3 export status. Present only for SUCCESS jobs; null otherwise (or if export is disabled).
                  {"status": PENDING|SUCCESS|FAILED, "bucket", "key", "url", "exported_at", "error"}
                  url = presigned GET URL (only when SUCCESS, freshly signed on every lookup). Export failing doesn't affect job status, which stays SUCCESS.
    404  unknown job                                 {"detail": ...}

Sync and async share the same flow (prepare -> compute). The worker restores the snapshot prepare
built via parse_snapshot, then calls compute.
"""

import re
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.urls import reverse
from kombu.exceptions import OperationalError
from rest_framework.response import Response
from rest_framework.views import APIView

from engine.loader import InputError, parse_catalog, parse_programs, parse_student
from engine.model import Catalog, Programs, Student as EngineStudent
from engine.planner import PlanResult, plan_student
from engine.validate import validate
from engine.verify import verify_plan

from planapi import convert, jobs, models

INT_RE = re.compile(r"^-?[0-9]+$")

# query parameter -> student field (same as the CLI's --max-credits, --num-terms)
OVERRIDES = [("max_credits", "max_credits_per_term"), ("num_terms", "num_terms")]


class RequestError(Exception):
    """A request error decided before computation. Both sync and async respond with {"detail"} and this status code."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class Prepared:
    snapshot: dict              # {"catalog", "programs", "student"} — same shape as data/*.json (input for async jobs)
    params: dict                # {"max_credits": int|None, "num_terms": int|None} request values
    catalog: Catalog
    programs: Programs
    student: EngineStudent


def prepare(student_id: str, query_params) -> Prepared:
    """Read input from the DB and check its shape. Order of checks: data not loaded (500) -> unknown student (404) -> parameters (400)."""
    try:
        raw_catalog, raw_programs = convert.catalog_programs_raw()
        catalog, programs = convert.parse_catalog_programs(raw_catalog, raw_programs)
    except convert.StoredDataError as e:
        raise RequestError(500, str(e))

    row = models.Student.objects.filter(code=student_id).first()
    if row is None:
        raise RequestError(404, "학생 없음: " + student_id)

    params = {}
    overrides = {}
    for param, field in OVERRIDES:
        params[param] = None
        raw = query_params.get(param)
        if raw is None:
            continue
        if not INT_RE.match(raw):
            raise RequestError(400, param + "는 정수여야 함: '" + raw + "'")
        params[param] = int(raw)
        overrides[field] = int(raw)
    raw_student = convert.student_raw(row, overrides)
    try:
        student = parse_student(raw_student, catalog.seasons, convert.student_where(row.code))
    except InputError as e:
        raise RequestError(400, str(e))

    snapshot = {"catalog": raw_catalog, "programs": raw_programs, "student": raw_student}
    return Prepared(snapshot=snapshot, params=params, catalog=catalog, programs=programs, student=student)


def parse_snapshot(snapshot: dict) -> tuple[Catalog, Programs, EngineStudent]:
    """Turn the snapshot prepare built into engine input. This data has already passed shape checks in prepare."""
    catalog = parse_catalog(snapshot["catalog"])
    programs = parse_programs(snapshot["programs"])
    raw_student = snapshot["student"]
    student = parse_student(raw_student, catalog.seasons, convert.student_where(raw_student["id"]))
    return catalog, programs, student


def compute(catalog: Catalog, programs: Programs, student: EngineStudent) -> tuple[PlanResult, list[str]]:
    """validate -> plan_student -> verify_plan. The second return value is the list of verify violations (an engine bug if non-empty)."""
    static = validate(catalog, programs)
    res = plan_student(catalog, programs, static, student)
    violations = []
    if res.plan["feasible"]:
        violations = verify_plan(catalog, programs, student, res.plan)
    return res, violations


VERIFY_FAILED_DETAIL = "엔진 결과가 독립 검증을 통과하지 못함 (엔진 버그)"


class StudentPlanView(APIView):
    def get(self, request, student_id: str):
        try:
            p = prepare(student_id, request.query_params)
        except RequestError as e:
            return Response({"detail": e.detail}, status=e.status)

        res, violations = compute(p.catalog, p.programs, p.student)
        if len(violations) > 0:
            return Response({"detail": VERIFY_FAILED_DETAIL, "violations": violations}, status=500)

        resp = Response(res.plan, status=200)
        resp["X-Candidates"] = str(res.candidates)
        resp["X-Used-Fallback"] = _bool(res.used_fallback)
        if res.plan["feasible"]:
            resp["X-Min-Terms-Proven"] = _bool(res.min_terms_proven)
        return resp


class StudentPlanJobView(APIView):
    def post(self, request, student_id: str):
        try:
            p = prepare(student_id, request.query_params)
        except RequestError as e:
            return Response({"detail": e.detail}, status=e.status)

        job, created = jobs.create_or_reuse(student_id, p.params, p.snapshot)
        if created:
            # The view is outside a transaction (autocommit), so the job row is already committed. The worker can read it right away.
            from planapi.tasks import run_plan_job  # imported here to avoid a cycle, since tasks imports this module
            try:
                run_plan_job.apply_async(args=[str(job.id)], queue=settings.CELERY_TASK_DEFAULT_QUEUE)
            except OperationalError as e:
                job.delete()
                return Response({"detail": "작업 큐에 넣지 못함 (Redis 연결 확인): " + str(e)}, status=503)

        resp = Response({"job_id": str(job.id), "status": job.status}, status=202 if created else 200)
        resp["Location"] = reverse("plan-job", args=[str(job.id)])
        return resp


class PlanJobView(APIView):
    def get(self, request, job_id: str):
        try:
            key = uuid.UUID(job_id)
        except ValueError:
            key = None
        job = None
        if key is not None:
            job = models.PlanJob.objects.filter(id=key).first()
        if job is None:
            return Response({"detail": "job 없음: " + job_id}, status=404)
        return Response(jobs.job_dict(job), status=200)


def _bool(v: bool) -> str:
    return "true" if v else "false"
