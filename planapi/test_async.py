"""Phase 4 async API tests (Celery eager mode — the task runs synchronously within the request, no Redis/worker needed).

    python3 manage.py test planapi

The core criterion: the async result (plan, engine) must match the sync response (body, X- headers) for the same input.
Integration tests using real Redis + a worker live in test_worker.py.
"""

import json
import uuid
from datetime import timedelta
from io import StringIO
from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from kombu.exceptions import OperationalError

from config.celery import app as celery_app
from engine.loader import load_all

from planapi import jobs, models, tasks
from planapi.tests import load_fixture


def sync_expected(resp) -> tuple[dict, dict]:
    """sync response -> the expected (plan, engine) values for the async job."""
    proven = None
    if "X-Min-Terms-Proven" in resp:
        proven = resp["X-Min-Terms-Proven"] == "true"
    engine = {"candidates": int(resp["X-Candidates"]), "used_fallback": resp["X-Used-Fallback"] == "true",
              "min_terms_proven": proven}
    return resp.json(), engine


class AsyncTestCase(TestCase):
    # S3 export (Phase 6) is disabled by default. Only the tests that verify export (test_s3.py) enable it, using a test-only bucket.
    S3_EXPORT = False

    @classmethod
    def setUpTestData(cls):
        load_fixture()

    def setUp(self):
        old = celery_app.conf.task_always_eager
        celery_app.conf.task_always_eager = True
        self.addCleanup(setattr, celery_app.conf, "task_always_eager", old)
        if not self.S3_EXPORT:
            p = override_settings(S3_EXPORT_ENABLED=False)
            p.enable()
            self.addCleanup(p.disable)

    def post(self, student_id, **params):
        query = ""
        if params:
            query = "?" + "&".join(k + "=" + str(v) for k, v in params.items())
        return self.client.post("/api/students/" + student_id + "/plan-jobs" + query)

    def job(self, job_id):
        return self.client.get("/api/plan-jobs/" + job_id)

    def run_job(self, student_id, **params) -> dict:
        resp = self.post(student_id, **params)
        self.assertIn(resp.status_code, (200, 202))
        got = self.job(resp.json()["job_id"])
        self.assertEqual(got.status_code, 200)
        return got.json()

    def no_dispatch(self):
        """Prevent the task from being enqueued (for observing PENDING state or running the task directly)."""
        p = mock.patch.object(tasks.run_plan_job, "apply_async")
        m = p.start()
        self.addCleanup(p.stop)
        return m


class AsyncMatchesSyncTest(AsyncTestCase):
    def test_matches_sync_for_all_students(self):
        _, _, students = load_all(str(settings.DATA_DIR))
        cases = [{}, {"max_credits": 7}, {"num_terms": 2}, {"max_credits": 9, "num_terms": 6}]
        for s in students:
            for params in cases:
                with self.subTest(student=s.id, **params):
                    sync = self.client.get("/api/students/" + s.id + "/plan", params)
                    self.assertEqual(sync.status_code, 200)
                    plan, engine = sync_expected(sync)
                    body = self.run_job(s.id, **params)
                    self.assertEqual(body["status"], "SUCCESS")
                    self.assertEqual(body["plan"], plan)
                    # must match the sync response even in key order (MySQL's JSON type reorders keys)
                    self.assertEqual(json.dumps(body["plan"]), json.dumps(plan))
                    self.assertEqual(body["engine"], engine)
                    self.assertIsNone(body["error"])


class AsyncShapeTest(AsyncTestCase):
    def test_post_202_with_location(self):
        resp = self.post("S1", max_credits=12)
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(set(body), {"job_id", "status"})
        self.assertEqual(resp["Location"], "/api/plan-jobs/" + body["job_id"])
        uuid.UUID(body["job_id"])

    def test_success_shape(self):
        body = self.run_job("S1", max_credits=12)
        self.assertEqual(set(body), {"job_id", "status", "student_id", "params", "created_at", "finished_at",
                                     "plan", "engine", "error", "export"})
        self.assertIsNone(body["export"])  # export disabled (Phase 6; its shape when enabled is in test_s3.py)
        self.assertEqual(body["student_id"], "S1")
        self.assertEqual(list(body["params"].items()), [("max_credits", 12), ("num_terms", None)])
        self.assertEqual(list(body["engine"]), ["candidates", "used_fallback", "min_terms_proven"])
        self.assertEqual(set(body["plan"]), {"student_id", "feasible", "terms", "chosen", "errors"})
        self.assertTrue(body["created_at"].endswith("Z"))
        self.assertTrue(body["finished_at"].endswith("Z"))
        self.assertIs(body["engine"]["min_terms_proven"], True)

    def test_infeasible_is_success_with_null_min_terms(self):
        body = self.run_job("S3")
        self.assertEqual(body["status"], "SUCCESS")
        self.assertFalse(body["plan"]["feasible"])
        self.assertIn("E_INFEASIBLE", [e["code"] for e in body["plan"]["errors"]])
        self.assertIsNone(body["engine"]["min_terms_proven"])

    def test_static_validation_error_is_success(self):
        models.Course.objects.filter(code="CS101").update(prereqs=[["CS102"]])
        body = self.run_job("S1")
        self.assertEqual(body["status"], "SUCCESS")
        self.assertIn("E_CYCLE", [e["code"] for e in body["plan"]["errors"]])

    def test_pending_shape(self):
        self.no_dispatch()
        job_id = self.post("S1").json()["job_id"]
        body = self.job(job_id).json()
        self.assertEqual(body["status"], "PENDING")
        self.assertIsNone(body["plan"])
        self.assertIsNone(body["engine"])
        self.assertIsNone(body["error"])
        self.assertIsNone(body["finished_at"])

    def test_unknown_job_404(self):
        for job_id in [str(uuid.uuid4()), "not-a-uuid"]:
            with self.subTest(job_id=job_id):
                resp = self.job(job_id)
                self.assertEqual(resp.status_code, 404)
                self.assertIn("detail", resp.json())

    def test_get_on_create_url_not_allowed(self):
        self.assertEqual(self.client.get("/api/students/S1/plan-jobs").status_code, 405)


class AsyncRequestErrorTest(AsyncTestCase):
    """400/404/500 are all decided synchronously at job creation, without creating a job (same rules as the sync endpoint)."""

    def assert_rejected(self, resp, status):
        self.assertEqual(resp.status_code, status)
        self.assertIn("detail", resp.json())
        self.assertEqual(models.PlanJob.objects.count(), 0)

    def test_unknown_student_404(self):
        self.assert_rejected(self.post("NOPE"), 404)

    def test_bad_override_400(self):
        bad = [("max_credits", "abc"), ("max_credits", "1.5"), ("max_credits", ""), ("max_credits", "0"),
               ("num_terms", "0"), ("num_terms", "-1"), ("num_terms", "x")]
        for param, value in bad:
            with self.subTest(param=param, value=value):
                self.assert_rejected(self.post("S1", **{param: value}), 400)

    def test_unknown_student_takes_precedence_over_bad_param(self):
        self.assert_rejected(self.post("NOPE", num_terms="x"), 404)

    def test_data_not_loaded_500(self):
        models.CatalogMeta.objects.all().delete()
        self.assert_rejected(self.post("S1"), 500)

    def test_corrupted_stored_data_500(self):
        models.Course.objects.filter(code="CS101").update(prereqs=["CS102"])
        self.assert_rejected(self.post("S1"), 500)

    def test_enqueue_failure_503_leaves_no_job(self):
        m = self.no_dispatch()
        m.side_effect = OperationalError("connection refused")
        self.assert_rejected(self.post("S1"), 503)


class AsyncReuseTest(AsyncTestCase):
    def test_same_input_reuses_job(self):
        first = self.post("S1", max_credits=12)
        second = self.post("S1", max_credits=12)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), {"job_id": first.json()["job_id"], "status": "SUCCESS"})
        self.assertEqual(second["Location"], first["Location"])
        self.assertEqual(models.PlanJob.objects.count(), 1)

    def test_pending_job_is_reused(self):
        m = self.no_dispatch()
        first = self.post("S1")
        second = self.post("S1")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), {"job_id": first.json()["job_id"], "status": "PENDING"})
        self.assertEqual(m.call_count, 1)

    def test_different_input_new_job(self):
        ids = set()
        for params in [{}, {"max_credits": 12}, {"num_terms": 7}]:
            resp = self.post("S1", **params)
            self.assertEqual(resp.status_code, 202)
            ids.add(resp.json()["job_id"])
        self.assertEqual(len(ids), 3)

    def test_data_change_new_job(self):
        first = self.post("S1").json()["job_id"]
        models.Course.objects.filter(code="CS101").update(title="바뀐 제목")
        resp = self.post("S1")
        self.assertEqual(resp.status_code, 202)
        self.assertNotEqual(resp.json()["job_id"], first)

    def test_key_is_effective_input_not_raw_params(self):
        # S1's stored cap is 15, so max_credits=15 has the same effective input as a request with no override -> the same job
        first = self.post("S1").json()["job_id"]
        resp = self.post("S1", max_credits=15)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["job_id"], first)

    def test_failed_job_is_not_reused(self):
        with mock.patch("planapi.views.verify_plan", return_value=["가짜 위반"]):
            first = self.post("S1").json()["job_id"]
        self.assertEqual(self.job(first).json()["status"], "FAILED")
        resp = self.post("S1")
        self.assertEqual(resp.status_code, 202)
        self.assertNotEqual(resp.json()["job_id"], first)
        self.assertEqual(self.job(resp.json()["job_id"]).json()["status"], "SUCCESS")


class AsyncFailureTest(AsyncTestCase):
    def assert_failed(self, body, code):
        self.assertEqual(body["status"], "FAILED")
        self.assertEqual(body["error"]["code"], code)
        self.assertIsNone(body["plan"])
        self.assertIsNone(body["engine"])
        self.assertIsNotNone(body["finished_at"])

    def test_verify_failure(self):
        with mock.patch("planapi.views.verify_plan", return_value=["가짜 위반"]):
            body = self.run_job("S1")
        self.assert_failed(body, "VERIFY_FAILED")
        self.assertEqual(body["error"]["violations"], ["가짜 위반"])

    def test_worker_exception(self):
        with mock.patch("planapi.views.plan_student", side_effect=RuntimeError("boom")):
            body = self.run_job("S1")
        self.assert_failed(body, "WORKER_ERROR")
        self.assertIn("boom", body["error"]["detail"])
        self.assertNotIn("violations", body["error"])

    def test_soft_timeout(self):
        with mock.patch("planapi.views.plan_student", side_effect=SoftTimeLimitExceeded()):
            body = self.run_job("S1")
        self.assert_failed(body, "TIMEOUT")

    def test_time_limits(self):
        self.assertEqual(tasks.run_plan_job.soft_time_limit, 120)
        self.assertEqual(tasks.run_plan_job.time_limit, 150)


class AsyncSnapshotTest(AsyncTestCase):
    def test_worker_uses_request_time_snapshot(self):
        """Even if data changes after job creation (e.g. via load_data), the result reflects the data as of the request."""
        expected = self.client.get("/api/students/S1/plan").json()
        self.no_dispatch()
        job_id = self.post("S1").json()["job_id"]

        models.Course.objects.filter(code="CS101").update(prereqs=[["CS102"]])  # cycle
        models.Student.objects.filter(code="S1").delete()
        self.assertEqual(self.client.get("/api/students/S1/plan").status_code, 404)

        tasks.run_plan_job(job_id)
        body = self.job(job_id).json()
        self.assertEqual(body["status"], "SUCCESS")
        self.assertEqual(body["plan"], expected)

    def test_duplicate_delivery_is_ignored(self):
        self.no_dispatch()
        job_id = self.post("S1").json()["job_id"]
        tasks.run_plan_job(job_id)
        finished = models.PlanJob.objects.get(id=job_id).finished_at
        with mock.patch("planapi.views.compute") as compute:
            tasks.run_plan_job(job_id)
        compute.assert_not_called()
        self.assertEqual(models.PlanJob.objects.get(id=job_id).finished_at, finished)

    def test_deleted_job_is_ignored(self):
        self.no_dispatch()
        job_id = self.post("S1").json()["job_id"]
        models.PlanJob.objects.all().delete()
        tasks.run_plan_job(job_id)  # must finish without raising
        self.assertEqual(models.PlanJob.objects.count(), 0)

    def test_hash_ignores_key_order_but_not_list_order(self):
        a = {"x": [1, 2], "y": {"p": 1, "q": 2}}
        b = {"y": {"q": 2, "p": 1}, "x": [1, 2]}
        c = {"x": [2, 1], "y": {"p": 1, "q": 2}}
        self.assertEqual(jobs.input_hash(a), jobs.input_hash(b))
        self.assertNotEqual(jobs.input_hash(a), jobs.input_hash(c))


class PurgePlanJobsTest(AsyncTestCase):
    def test_purge_older_than_days(self):
        old = self.post("S1").json()["job_id"]
        new = self.post("S2").json()["job_id"]
        models.PlanJob.objects.filter(id=old).update(created_at=timezone.now() - timedelta(days=8))
        call_command("purge_plan_jobs", days=7, stdout=StringIO())
        self.assertEqual([str(j.id) for j in models.PlanJob.objects.all()], [new])
