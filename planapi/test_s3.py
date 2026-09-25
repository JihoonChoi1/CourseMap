"""Phase 6 S3 export tests. Actually uses the local S3-compatible server (the s3 service) from docker compose.

    docker compose up -d
    python3 manage.py test planapi.test_s3

Skipped if S3 can't be reached (the skip reason is printed). Each test run creates a process-specific bucket and deletes it afterward.
"""

import json
import os
import urllib.error
import urllib.request
import unittest
from io import StringIO
from unittest import mock

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import override_settings

from planapi import s3export, test_async, test_worker
from planapi.models import PlanJob

BUCKET = "coursemap-test-" + str(os.getpid())
UBC_DIR = str(settings.BASE_DIR / "data" / "ubc")


def s3_error() -> str | None:
    try:
        s3export.client().list_buckets()
    except (BotoCoreError, ClientError) as e:
        return type(e).__name__ + ": " + str(e)
    return None


def create_bucket():
    with override_settings(S3_BUCKET=BUCKET):
        s3export.ensure_bucket()


def delete_bucket():
    s3 = s3export.client()
    try:
        for obj in s3.list_objects_v2(Bucket=BUCKET).get("Contents", []):
            s3.delete_object(Bucket=BUCKET, Key=obj["Key"])
        s3.delete_bucket(Bucket=BUCKET)
    except ClientError:
        pass


def clear_bucket():
    s3 = s3export.client()
    for obj in s3.list_objects_v2(Bucket=BUCKET).get("Contents", []):
        s3.delete_object(Bucket=BUCKET, Key=obj["Key"])


def get_object(key: str) -> tuple[bytes, str]:
    o = s3export.client().get_object(Bucket=BUCKET, Key=key)
    return o["Body"].read(), o["ContentType"]


def keys(prefix: str = "") -> list[str]:
    out = s3export.client().list_objects_v2(Bucket=BUCKET, Prefix=prefix).get("Contents", [])
    return sorted(o["Key"] for o in out)


@override_settings(S3_BUCKET=BUCKET, S3_EXPORT_ENABLED=True)
class ExportTest(test_async.AsyncTestCase):
    S3_EXPORT = True

    @classmethod
    def setUpClass(cls):
        err = s3_error()
        if err is not None:
            raise unittest.SkipTest("S3 unreachable (" + str(settings.S3_ENDPOINT_URL) + "): " + err)
        create_bucket()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        delete_bucket()

    def setUp(self):
        super().setUp()
        clear_bucket()

    def test_success_exports_document(self):
        body = self.run_job("S1", max_credits=12)
        self.assertEqual(body["status"], "SUCCESS")
        ex = body["export"]
        self.assertEqual(ex["status"], "SUCCESS")
        self.assertEqual(ex["bucket"], BUCKET)
        self.assertEqual(ex["key"], "plans/S1/" + body["job_id"] + ".json")
        self.assertIsNone(ex["error"])
        self.assertTrue(ex["exported_at"].endswith("Z"))

        raw, content_type = get_object(ex["key"])
        self.assertEqual(content_type, "application/json; charset=utf-8")
        doc = json.loads(raw.decode("utf-8"))
        self.assertEqual(list(doc), ["job_id", "student_id", "params", "plan", "engine", "input", "exported_at"])
        self.assertEqual(doc["job_id"], body["job_id"])
        self.assertEqual(doc["student_id"], "S1")
        self.assertEqual(json.dumps(doc["params"]), json.dumps(body["params"]))
        self.assertEqual(json.dumps(doc["plan"]), json.dumps(body["plan"]))  # includes key order
        self.assertEqual(json.dumps(doc["engine"]), json.dumps(body["engine"]))
        self.assertEqual(doc["input"], PlanJob.objects.get(id=body["job_id"]).input)
        self.assertEqual(doc["input"]["student"]["max_credits_per_term"], 12)
        self.assertEqual(doc["exported_at"], ex["exported_at"])
        self.assertIn("미적분학 I", raw.decode("utf-8"))  # ensure_ascii=False, UTF-8

    def test_presigned_url_downloads_same_object(self):
        body = self.run_job("S2")
        url = body["export"]["url"]
        self.assertIn("X-Amz-Signature=", url)
        self.assertIn("X-Amz-Expires=" + str(settings.S3_PRESIGN_EXPIRES), url)
        raw, _ = get_object(body["export"]["key"])
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.read(), raw)
        # tampering with the signature gets rejected
        bad = url.replace("X-Amz-Signature=", "X-Amz-Signature=00")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(bad, timeout=5)
        self.assertGreaterEqual(cm.exception.code, 400)

    def test_url_is_signed_per_request(self):
        body = self.run_job("S1")
        again = self.job(body["job_id"]).json()
        self.assertEqual(again["export"]["key"], body["export"]["key"])
        self.assertIsNotNone(again["export"]["url"])

    def test_infeasible_plan_is_exported(self):
        body = self.run_job("S3")
        self.assertFalse(body["plan"]["feasible"])
        self.assertEqual(body["export"]["status"], "SUCCESS")
        doc = json.loads(get_object(body["export"]["key"])[0])
        self.assertFalse(doc["plan"]["feasible"])
        self.assertIsNone(doc["engine"]["min_terms_proven"])

    def test_failed_job_is_not_exported(self):
        with mock.patch("planapi.views.verify_plan", return_value=["가짜 위반"]):
            body = self.run_job("S1")
        self.assertEqual(body["status"], "FAILED")
        self.assertIsNone(body["export"])
        self.assertEqual(keys(), [])

    def test_reused_job_is_not_reuploaded(self):
        with mock.patch.object(s3export, "export_job", wraps=s3export.export_job) as spy:
            first = self.run_job("S1")
            second = self.run_job("S1")
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(keys(), [first["export"]["key"]])

    def test_upload_failure_keeps_job_success_and_command_retries(self):
        with override_settings(S3_ENDPOINT_URL="http://127.0.0.1:1"):
            body = self.run_job("S1")
        self.assertEqual(body["status"], "SUCCESS")
        self.assertTrue(body["plan"]["feasible"])
        ex = body["export"]
        self.assertEqual(ex["status"], "FAILED")
        self.assertIn("EndpointConnectionError", ex["error"])
        self.assertIsNone(ex["url"])
        self.assertIsNone(ex["exported_at"])
        self.assertEqual(keys(), [])

        out = StringIO()
        call_command("export_plan_jobs", stdout=out, stderr=StringIO())
        self.assertIn("성공 1개, 실패 0개", out.getvalue())
        again = self.job(body["job_id"]).json()
        self.assertEqual(again["export"]["status"], "SUCCESS")
        self.assertIsNone(again["export"]["error"])
        self.assertEqual(keys(), [ex["key"]])

    def test_command_reports_failure(self):
        body = self.run_job("S1")
        clear_bucket()
        with override_settings(S3_ENDPOINT_URL="http://127.0.0.1:1"):
            with self.assertRaises(CommandError):
                call_command("export_plan_jobs", "--all", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(self.job(body["job_id"]).json()["export"]["status"], "FAILED")

    def test_command_job_id(self):
        body = self.run_job("S1")
        clear_bucket()
        call_command("export_plan_jobs", "--job-id", body["job_id"], stdout=StringIO())
        self.assertEqual(keys(), [body["export"]["key"]])
        with self.assertRaises(CommandError):
            call_command("export_plan_jobs", "--job-id", "not-a-uuid", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("export_plan_jobs", "--job-id", "00000000-0000-0000-0000-000000000000", stdout=StringIO())

    def test_default_command_skips_already_exported(self):
        self.run_job("S1")
        with mock.patch.object(s3export, "export_job") as spy:
            call_command("export_plan_jobs", stdout=StringIO())
        spy.assert_not_called()

    def test_bad_credentials_error_does_not_leak_secret(self):
        secret = "wrong-secret-value-for-test"
        with override_settings(S3_SECRET_ACCESS_KEY=secret):
            body = self.run_job("S1")
        self.assertEqual(body["status"], "SUCCESS")
        self.assertEqual(body["export"]["status"], "FAILED")
        self.assertNotIn(secret, body["export"]["error"])
        self.assertNotIn(settings.S3_SECRET_ACCESS_KEY, body["export"]["error"])

    def test_pending_between_success_and_upload(self):
        with mock.patch.object(s3export, "export_job"):
            body = self.run_job("S1")
        self.assertEqual(body["status"], "SUCCESS")
        self.assertEqual(body["export"]["status"], "PENDING")
        self.assertIsNone(body["export"]["url"])

    def test_unexpected_export_error_keeps_job_success(self):
        with mock.patch.object(s3export, "export_job", side_effect=RuntimeError("boom")):
            body = self.run_job("S1")
        self.assertEqual(body["status"], "SUCCESS")
        self.assertEqual(body["export"]["status"], "FAILED")
        self.assertIn("boom", body["export"]["error"])

    def test_export_disabled(self):
        with override_settings(S3_EXPORT_ENABLED=False):
            body = self.run_job("S1")
        self.assertIsNone(body["export"])
        self.assertEqual(keys(), [])

    def test_ubc_job_is_exported(self):
        call_command("load_data", "--data-dir", UBC_DIR, stdout=StringIO(), stderr=StringIO())
        body = self.run_job("U1")
        self.assertEqual(body["status"], "SUCCESS")
        sync = self.client.get("/api/students/U1/plan")
        self.assertEqual(json.dumps(body["plan"]), json.dumps(sync.json()))
        doc = json.loads(get_object(body["export"]["key"])[0])
        self.assertEqual(body["export"]["key"], "plans/U1/" + body["job_id"] + ".json")
        self.assertEqual(doc["input"]["catalog"]["seasons"], ["W1", "W2"])
        self.assertEqual(json.dumps(doc["plan"]), json.dumps(sync.json()))


class RealWorkerExportTest(test_worker.RealWorkerTest):
    """Whether a real Redis + prefork worker uploads to S3 after finishing a job (the worker process uses a test-only bucket)."""

    @classmethod
    def setUpClass(cls):
        err = s3_error()
        if err is not None:
            raise unittest.SkipTest("S3 unreachable (" + str(settings.S3_ENDPOINT_URL) + "): " + err)
        create_bucket()
        cls.WORKER_ENV = {"S3_EXPORT_ENABLED": "1", "S3_BUCKET": BUCKET}
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        delete_bucket()

    def test_worker_results_match_sync(self):
        super().test_worker_results_match_sync()
        with override_settings(S3_BUCKET=BUCKET):
            jobs = list(PlanJob.objects.all())
            self.assertEqual(len(jobs), 8)
            for job in jobs:
                with self.subTest(job=str(job.id)):
                    body = self.client.get("/api/plan-jobs/" + str(job.id)).json()
                    self.assertEqual(body["status"], "SUCCESS")
                    self.assertEqual(body["export"]["status"], "SUCCESS", body["export"])
                    doc = json.loads(get_object(body["export"]["key"])[0])
                    self.assertEqual(json.dumps(doc["plan"]), json.dumps(body["plan"]))
            self.assertEqual(len(keys()), 8)
