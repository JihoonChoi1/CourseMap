"""Invoke the Go engine (Phase 5). Used by the Celery worker in place of views.compute when PLAN_ENGINE=go.

The job snapshot (shaped like data/*.json) is passed as stdin to `courseplan --stdin-snapshot`, and
the output JSON is restored into a PlanResult. Independent verification (verify_plan) is done on
the Python side — the reference implementation's verifier checks the Go result.

If the soft time limit (SoftTimeLimitExceeded) fires while waiting on subprocess.run, subprocess.run
kills the child process before the exception propagates. So no Go process is left behind after
TIMEOUT handling (confirmed in test_goengine.py).
"""

import json
import subprocess

from django.conf import settings

from engine.model import Catalog, Programs, Student
from engine.planner import PlanResult
from engine.verify import verify_plan


class GoEngineError(Exception):
    """The Go engine exited with a nonzero code (e.g. malformed input). The worker records this as WORKER_ERROR."""


def plan(snapshot: dict) -> PlanResult:
    proc = subprocess.run([settings.GO_ENGINE_BIN, "--stdin-snapshot"], input=json.dumps(snapshot, ensure_ascii=False),
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise GoEngineError("courseplan 종료 코드 " + str(proc.returncode) + ": " + proc.stderr.strip())
    out = json.loads(proc.stdout)
    return PlanResult(plan=out["plan"], candidates=out["candidates"], used_fallback=out["used_fallback"],
                      picks=out["picks"], lower_bound_terms=out["lower_bound_terms"],
                      total_credits=out["total_credits"], min_terms_proven=out["min_terms_proven"])


def compute(snapshot: dict, catalog: Catalog, programs: Programs, student: Student) -> tuple[PlanResult, list[str]]:
    """Same return value as views.compute. catalog/programs/student are the same snapshot read via the Python loader (for verification)."""
    res = plan(snapshot)
    violations = []
    if res.plan["feasible"]:
        violations = verify_plan(catalog, programs, student, res.plan)
    return res, violations
