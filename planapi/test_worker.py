"""Phase 4 integration test: a real Redis broker + a real Celery worker process (prefork).
Phase 5: the same test also runs against a Go-engine worker (PLAN_ENGINE=go) (RealWorkerGoTest).

    docker compose up -d
    python3 manage.py test planapi.test_worker

Skipped if Redis can't be reached (the skip reason is printed).
The worker is launched with MYSQL_DATABASE pointed at the test DB (test_coursemap) and a dedicated
queue so it doesn't mix with a dev worker.
Since the worker is a separate process, test data must actually be committed, so TransactionTestCase is used.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import redis
from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase, override_settings

from planapi.tests import load_fixture

QUEUE = "coursemap-test-" + str(os.getpid())
WAIT_SECONDS = 30  # until every job finishes (real data takes a few ms per job)


def redis_error() -> str | None:
    try:
        redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=1).ping()
    except redis.RedisError as e:
        return str(e)
    return None


@override_settings(CELERY_TASK_DEFAULT_QUEUE=QUEUE)
class RealWorkerTest(TransactionTestCase):
    WORKER_ENV = {}  # extra environment variables to add only to the worker process (RealWorkerGoTest)

    @classmethod
    def setUpClass(cls):
        err = redis_error()
        if err is not None:
            raise unittest.SkipTest("Redis unreachable (" + settings.CELERY_BROKER_URL + "): " + err)
        super().setUpClass()
        env = dict(os.environ)
        env["MYSQL_DATABASE"] = connection.settings_dict["NAME"]
        env["CELERY_QUEUE"] = QUEUE
        env["S3_EXPORT_ENABLED"] = "0"  # export is only enabled in RealWorkerExportTest (test_s3.py)
        env.update(cls.WORKER_ENV)
        cls.log = tempfile.NamedTemporaryFile(mode="w+", suffix=".log")
        cls.worker = subprocess.Popen(
            [sys.executable, "-m", "celery", "-A", "config", "worker", "-l", "info", "-c", "2",
             "-n", QUEUE + "@%h", "--without-gossip", "--without-mingle", "--without-heartbeat"],
            cwd=str(settings.BASE_DIR), env=env, stdout=cls.log, stderr=subprocess.STDOUT,
        )

    @classmethod
    def tearDownClass(cls):
        cls.worker.terminate()
        try:
            cls.worker.wait(timeout=20)
        except subprocess.TimeoutExpired:
            cls.worker.kill()
            cls.worker.wait()
        cls.log.close()
        super().tearDownClass()

    def setUp(self):
        load_fixture()

    def worker_log(self) -> str:
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def wait_done(self, job_id: str, deadline: float) -> dict:
        while time.monotonic() < deadline:
            body = self.client.get("/api/plan-jobs/" + job_id).json()
            if body["status"] in ("SUCCESS", "FAILED"):
                return body
            if self.worker.poll() is not None:
                self.fail("worker process exited\n" + self.worker_log())
            time.sleep(0.1)
        self.fail("job " + job_id + " did not finish within the time limit\n" + self.worker_log())

    def test_worker_results_match_sync(self):
        cases = []
        for sid in ["S1", "S2", "S3", "S4"]:
            for params in [{}, {"max_credits": 7, "num_terms": 6}]:
                cases.append((sid, params))

        created = []
        for sid, params in cases:
            query = "&".join(k + "=" + str(v) for k, v in params.items())
            resp = self.client.post("/api/students/" + sid + "/plan-jobs?" + query)
            self.assertEqual(resp.status_code, 202, resp.content)
            created.append((sid, params, resp.json()["job_id"]))

        deadline = time.monotonic() + WAIT_SECONDS
        for sid, params, job_id in created:
            with self.subTest(student=sid, **params):
                body = self.wait_done(job_id, deadline)
                self.assertEqual(body["status"], "SUCCESS", body["error"])
                sync = self.client.get("/api/students/" + sid + "/plan", params)
                self.assertEqual(json.dumps(body["plan"]), json.dumps(sync.json()))  # includes key order
                self.assertEqual(body["engine"]["candidates"], int(sync["X-Candidates"]))
                self.assertEqual(body["engine"]["used_fallback"], sync["X-Used-Fallback"] == "true")
                if body["plan"]["feasible"]:
                    self.assertEqual(body["engine"]["min_terms_proven"], sync["X-Min-Terms-Proven"] == "true")
                else:
                    self.assertIsNone(body["engine"]["min_terms_proven"])

        # a finished job is reused for the same input (no new job is enqueued)
        sid, params, job_id = created[0]
        again = self.client.post("/api/students/" + sid + "/plan-jobs")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json(), {"job_id": job_id, "status": "SUCCESS"})


class RealWorkerGoTest(RealWorkerTest):
    """Phase 5: the same integration test against a PLAN_ENGINE=go worker. The result must match the sync (Python) response."""

    @classmethod
    def setUpClass(cls):
        if not os.access(settings.GO_ENGINE_BIN, os.X_OK):
            raise unittest.SkipTest("Go engine binary missing: " + settings.GO_ENGINE_BIN)
        # a wrapper around the real binary that logs one line per call, to confirm the worker actually computed via Go
        cls.calls = tempfile.NamedTemporaryFile(mode="w+", suffix=".calls")
        cls.wrapper = tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False)
        cls.wrapper.write("#!/bin/sh\necho call >> " + cls.calls.name + "\nexec " + settings.GO_ENGINE_BIN + ' "$@"\n')
        cls.wrapper.close()
        os.chmod(cls.wrapper.name, 0o755)
        cls.WORKER_ENV = {"PLAN_ENGINE": "go", "GO_ENGINE_BIN": cls.wrapper.name}
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        os.unlink(cls.wrapper.name)
        cls.calls.close()

    def test_worker_results_match_sync(self):
        super().test_worker_results_match_sync()
        self.calls.seek(0)
        self.assertEqual(len(self.calls.read().splitlines()), 8)  # all 8 jobs computed via Go
