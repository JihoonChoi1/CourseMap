"""Phase 5: the Celery worker's Go-engine path (PLAN_ENGINE=go). Celery eager mode.

    cd goengine && go build -o bin/courseplan ./cmd/courseplan   # build first
    python3 manage.py test planapi.test_goengine

The core criterion: the async result computed by Go (plan, engine) must match the sync response
(Python engine) for the same input. Tests that actually run Go are skipped if the binary is
missing (the skip reason is printed).
"""

import json
import os
import signal
import stat
import tempfile
import time
import unittest
from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.test import override_settings

from engine.loader import load_all

from planapi.test_async import AsyncTestCase, sync_expected


def go_binary_missing() -> str | None:
    if os.access(settings.GO_ENGINE_BIN, os.X_OK):
        return None
    return "Go engine binary missing: " + settings.GO_ENGINE_BIN + " (cd goengine && go build -o bin/courseplan ./cmd/courseplan)"


def fake_binary(body: str) -> str:
    """A shell script to substitute in place of GO_ENGINE_BIN."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False)
    f.write("#!/bin/sh\n" + body + "\n")
    f.close()
    os.chmod(f.name, os.stat(f.name).st_mode | stat.S_IXUSR)
    return f.name


@override_settings(PLAN_ENGINE="go")
class GoEngineJobTest(AsyncTestCase):
    def setUp(self):
        super().setUp()
        missing = go_binary_missing()
        if missing is not None:
            self.skipTest(missing)

    def test_matches_sync_python_for_all_students(self):
        _, _, students = load_all(str(settings.DATA_DIR))
        cases = [{}, {"max_credits": 7}, {"num_terms": 2}, {"max_credits": 9, "num_terms": 6}, {"max_credits": 3}]
        for s in students:
            for params in cases:
                with self.subTest(student=s.id, **params):
                    sync = self.client.get("/api/students/" + s.id + "/plan", params)
                    self.assertEqual(sync.status_code, 200)
                    body = self.run_job(s.id, **params)
                    self.assertEqual(body["status"], "SUCCESS", body["error"])
                    plan, engine = sync_expected(sync)
                    self.assertEqual(json.dumps(body["plan"]), json.dumps(plan))  # includes key order
                    self.assertEqual(body["engine"], engine)

    def test_go_engine_is_actually_called(self):
        with mock.patch("planapi.views.plan_student", side_effect=AssertionError("Python engine was called")):
            body = self.run_job("S1")
        self.assertEqual(body["status"], "SUCCESS", body["error"])

    def test_verify_runs_on_go_result(self):
        with mock.patch("planapi.goengine.verify_plan", return_value=["가짜 위반"]):
            body = self.run_job("S1")
        self.assertEqual(body["status"], "FAILED")
        self.assertEqual(body["error"]["code"], "VERIFY_FAILED")
        self.assertEqual(body["error"]["violations"], ["가짜 위반"])


@override_settings(PLAN_ENGINE="go")
class GoEngineFailureTest(AsyncTestCase):
    """Failures on the Go-process side. Uses a fake binary, so it runs regardless of whether Go is built."""

    def use_binary(self, path: str):
        self.addCleanup(os.unlink, path)
        p = override_settings(GO_ENGINE_BIN=path)
        p.enable()
        self.addCleanup(p.disable)

    def test_missing_binary_is_worker_error(self):
        with override_settings(GO_ENGINE_BIN="/nonexistent/courseplan"):
            body = self.run_job("S1")
        self.assertEqual(body["status"], "FAILED")
        self.assertEqual(body["error"]["code"], "WORKER_ERROR")
        self.assertIn("FileNotFoundError", body["error"]["detail"])

    def test_nonzero_exit_is_worker_error_with_stderr(self):
        self.use_binary(fake_binary("cat > /dev/null; echo '입력 오류: 가짜' >&2; exit 2"))
        body = self.run_job("S1")
        self.assertEqual(body["status"], "FAILED")
        self.assertEqual(body["error"]["code"], "WORKER_ERROR")
        self.assertEqual(body["error"]["detail"], "GoEngineError: courseplan 종료 코드 2: 입력 오류: 가짜")

    def test_soft_timeout_kills_go_process(self):
        # the worker's soft time limit works by a SIGUSR1 handler raising SoftTimeLimitExceeded.
        # raise the exception the same way while waiting on Go, and confirm no child process is left behind.
        pid_file = tempfile.NamedTemporaryFile(delete=False)
        pid_file.close()
        self.addCleanup(os.unlink, pid_file.name)
        self.use_binary(fake_binary("echo $$ > " + pid_file.name + "; exec sleep 30"))

        def raise_timeout(signum, frame):
            raise SoftTimeLimitExceeded()

        old = signal.signal(signal.SIGALRM, raise_timeout)
        self.addCleanup(signal.signal, signal.SIGALRM, old)
        signal.alarm(1)
        started = time.monotonic()
        body = self.run_job("S1")
        signal.alarm(0)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(body["status"], "FAILED")
        self.assertEqual(body["error"]["code"], "TIMEOUT")
        with open(pid_file.name) as f:
            pid = int(f.read().strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)  # already killed and reaped


@override_settings(PLAN_ENGINE="python")
class PythonEngineSettingTest(AsyncTestCase):
    def test_python_engine_does_not_call_go(self):
        with mock.patch("planapi.goengine.plan", side_effect=AssertionError("Go engine was called")):
            body = self.run_job("S1")
        self.assertEqual(body["status"], "SUCCESS", body["error"])


class EngineSettingDefaultTest(unittest.TestCase):
    @unittest.skipIf("PLAN_ENGINE" in os.environ, "PLAN_ENGINE environment variable is set")
    def test_default_engine_is_python(self):
        self.assertEqual(settings.PLAN_ENGINE, "python")
