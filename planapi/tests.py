"""Phase 3 API tests. Run against MySQL (test_coursemap).

    python3 manage.py test planapi

The core criterion: results computed through the DB must match results from running the engine
directly on the files (data/*.json, the same path as the CLI).
"""

import json
import os
import tempfile
from io import StringIO
from unittest import mock

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import TestCase

from engine.loader import load_all
from engine.planner import plan_student
from engine.validate import validate

from planapi import convert, models


def load_fixture():
    call_command("load_data", stdout=StringIO(), stderr=StringIO())


def file_plan(student_id, max_credits=None, num_terms=None):
    catalog, programs, students = load_all(str(settings.DATA_DIR))
    for s in students:
        if s.id == student_id:
            if max_credits is not None:
                s.max_credits = max_credits
            if num_terms is not None:
                s.num_terms = num_terms
            return plan_student(catalog, programs, validate(catalog, programs), s)
    raise KeyError(student_id)


class PlanEndpointTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        load_fixture()

    def get(self, student_id, **params):
        return self.client.get("/api/students/" + student_id + "/plan", params)

    def test_matches_file_engine_for_all_students(self):
        _, _, students = load_all(str(settings.DATA_DIR))
        cases = [{}, {"max_credits": 7}, {"num_terms": 2}, {"max_credits": 9, "num_terms": 6}]
        for s in students:
            for params in cases:
                with self.subTest(student=s.id, **params):
                    resp = self.get(s.id, **params)
                    self.assertEqual(resp.status_code, 200)
                    expected = file_plan(s.id, params.get("max_credits"), params.get("num_terms"))
                    self.assertEqual(resp.json(), expected.plan)
                    self.assertEqual(resp["X-Candidates"], str(expected.candidates))

    def test_feasible_plan_shape_and_headers(self):
        resp = self.get("S1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/json")
        body = resp.json()
        self.assertEqual(set(body), {"student_id", "feasible", "terms", "chosen", "errors"})
        self.assertTrue(body["feasible"])
        self.assertEqual(body["student_id"], "S1")
        self.assertEqual(body["errors"], [])
        self.assertEqual(body["terms"][0]["year"], 2026)
        self.assertEqual(body["terms"][0]["season"], "FALL")
        self.assertEqual(resp["X-Min-Terms-Proven"], "true")
        self.assertEqual(resp["X-Used-Fallback"], "false")

    def test_infeasible_is_200_with_errors(self):
        resp = self.get("S3")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["feasible"])
        self.assertEqual(body["terms"], [])
        self.assertEqual(body["chosen"], {})
        self.assertIn("E_INFEASIBLE", [e["code"] for e in body["errors"]])
        for e in body["errors"]:
            self.assertEqual(set(e), {"code", "message", "courses"})
        self.assertNotIn("X-Min-Terms-Proven", resp)

    def test_not_offered_is_200(self):
        body = self.get("S4").json()
        self.assertFalse(body["feasible"])
        self.assertIn("E_NOT_OFFERED", [e["code"] for e in body["errors"]])

    def test_overrides_make_feasible(self):
        self.assertTrue(self.get("S3", max_credits=7).json()["feasible"])
        self.assertTrue(self.get("S4", num_terms=2).json()["feasible"])

    def test_override_does_not_persist(self):
        self.get("S3", max_credits=7)
        self.assertEqual(models.Student.objects.get(code="S3").max_credits_per_term, 6)
        self.assertFalse(self.get("S3").json()["feasible"])

    def test_unknown_student_404(self):
        resp = self.get("NOPE")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("NOPE", resp.json()["detail"])

    def test_bad_override_400(self):
        bad = [("max_credits", "abc"), ("max_credits", "1.5"), ("max_credits", ""), ("max_credits", "0"),
               ("num_terms", "0"), ("num_terms", "-1"), ("num_terms", "x")]
        for param, value in bad:
            with self.subTest(param=param, value=value):
                resp = self.get("S1", **{param: value})
                self.assertEqual(resp.status_code, 400)
                self.assertIn("detail", resp.json())

    def test_unknown_student_takes_precedence_over_bad_param(self):
        self.assertEqual(self.get("NOPE", num_terms="x").status_code, 404)

    def test_static_validation_error_is_200(self):
        # add CS101 <- CS102 prereq -> CS101 -> CS102 -> CS101 cycle
        c = models.Course.objects.get(code="CS101")
        c.prereqs = [["CS102"]]
        c.save()
        resp = self.get("S1")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["feasible"])
        self.assertIn("E_CYCLE", [e["code"] for e in body["errors"]])

    def test_duplicate_course_id_reported_by_engine(self):
        models.Course.objects.create(position=999, code="CS101", title="dup", credits=3, offered=["FALL"],
                                     prereqs=[], coreqs=[])
        body = self.get("S1").json()
        self.assertIn("E_DUP_ID", [e["code"] for e in body["errors"]])

    def test_unknown_track_is_200(self):
        models.Student.objects.filter(code="S1").update(track="NOPE")
        body = self.get("S1").json()
        self.assertEqual([e["code"] for e in body["errors"]], ["E_UNKNOWN_TRACK"])

    def test_data_not_loaded_500(self):
        models.CatalogMeta.objects.all().delete()
        resp = self.get("S1")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("load_data", resp.json()["detail"])

    def test_corrupted_stored_data_500(self):
        models.Course.objects.filter(code="CS101").update(prereqs=["CS102"])  # not [][]string
        self.assertEqual(self.get("S1").status_code, 500)

    def test_verify_failure_500(self):
        with mock.patch("planapi.views.verify_plan", return_value=["가짜 위반"]):
            resp = self.get("S1")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["violations"], ["가짜 위반"])


class ConvertTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        load_fixture()

    def test_roundtrip_equals_files(self):
        f_catalog, f_programs, f_students = load_all(str(settings.DATA_DIR))
        d_catalog, d_programs = convert.load_catalog_programs()
        self.assertEqual(d_catalog, f_catalog)      # includes order, Korean titles, CNF
        self.assertEqual(d_programs, f_programs)
        for s in f_students:
            row = models.Student.objects.get(code=s.id)
            self.assertEqual(convert.load_student(row, d_catalog.seasons, {}), s)


class LoadDataTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def copy_data(self, edit_students=None):
        for name in ["catalog.json", "programs.json", "students.json"]:
            with open(os.path.join(settings.DATA_DIR, name), encoding="utf-8") as f:
                raw = json.load(f)
            if name == "students.json" and edit_students is not None:
                edit_students(raw["students"])
            with open(os.path.join(self.tmp.name, name), "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False)
        return self.tmp.name

    def counts(self):
        return (models.CatalogMeta.objects.count(), models.Course.objects.count(), models.Program.objects.count(),
                models.Group.objects.count(), models.Student.objects.count())

    def test_reload_replaces(self):
        load_fixture()
        first = self.counts()
        load_fixture()
        self.assertEqual(self.counts(), first)
        self.assertEqual(first, (1, 27, 4, 10, 4))

    def test_bad_input_leaves_db_untouched(self):
        load_fixture()
        before = self.counts()

        def bad(students):
            students[0]["num_terms"] = 0
        with self.assertRaisesRegex(CommandError, "num_terms"):
            call_command("load_data", data_dir=self.copy_data(bad), stdout=StringIO())
        self.assertEqual(self.counts(), before)

    def test_duplicate_student_rejected(self):
        def dup(students):
            students.append(dict(students[0]))
        with self.assertRaisesRegex(CommandError, "S1"):
            call_command("load_data", data_dir=self.copy_data(dup), stdout=StringIO())
        self.assertEqual(models.Student.objects.count(), 0)

    def test_missing_dir(self):
        with self.assertRaises(CommandError):
            call_command("load_data", data_dir=os.path.join(self.tmp.name, "nope"), stdout=StringIO())
