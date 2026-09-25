"""Phase 6: load real data (data/ubc) into the DB via load_data --data-dir, and check the sync endpoint matches the file-engine result.

Confirms that data with spaces in course ids ("CPSC 110") and term names W1/W2 survives a round trip through the DB unchanged.
"""

import copy
import json
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from engine.loader import load_all
from engine.planner import plan_student
from engine.validate import validate

UBC_DIR = str(settings.BASE_DIR / "data" / "ubc")


class UbcSyncEndpointTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        out = StringIO()
        call_command("load_data", "--data-dir", UBC_DIR, stdout=out, stderr=StringIO())
        cls.load_output = out.getvalue()

    def test_load_output(self):
        self.assertIn("과목 30개, 트랙 3개, 학생 6명", self.load_output)

    def test_matches_file_engine(self):
        catalog, programs, students = load_all(UBC_DIR)
        static = validate(catalog, programs)
        for s in students:
            for params in [{}, {"max_credits": 9}, {"num_terms": 3}]:
                with self.subTest(student=s.id, **params):
                    resp = self.client.get("/api/students/" + s.id + "/plan", params)
                    self.assertEqual(resp.status_code, 200)
                    st = copy.copy(s)
                    st.max_credits = params.get("max_credits", s.max_credits)
                    st.num_terms = params.get("num_terms", s.num_terms)
                    expected = plan_student(catalog, programs, static, st)
                    self.assertEqual(json.dumps(resp.json()), json.dumps(expected.plan))  # includes key order
                    self.assertEqual(resp["X-Candidates"], str(expected.candidates))
                    self.assertEqual(resp["X-Used-Fallback"], "true" if expected.used_fallback else "false")

    def test_fallback_header_on_real_data(self):
        resp = self.client.get("/api/students/U1/plan")
        self.assertEqual(resp["X-Used-Fallback"], "true")
        self.assertEqual(resp["X-Min-Terms-Proven"], "false")
        self.assertEqual(resp.json()["terms"][0]["season"], "W1")
        self.assertIn("CPSC 110", resp.json()["terms"][0]["courses"])
