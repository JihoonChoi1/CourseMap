"""Tests for the web page (/). Since the page calls the existing API, this only checks that the page renders and the reference data is embedded."""

import json
import re
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from planapi import models

UBC_DIR = str(settings.BASE_DIR / "data" / "ubc")


def page_data(resp) -> dict:
    m = re.search(r'<script id="page-data" type="application/json">(.*?)</script>', resp.content.decode(), re.S)
    return json.loads(m.group(1))


class WebPageTest(TestCase):
    def test_no_data_shows_guidance(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "불러올 데이터가 없습니다")
        self.assertContains(resp, "load_data")

    def test_embeds_students_and_courses(self):
        call_command("load_data", "--data-dir", UBC_DIR, stdout=StringIO(), stderr=StringIO())
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        data = page_data(resp)
        self.assertEqual([s["id"] for s in data["students"]], ["U1", "U2", "U3", "U4", "U5", "U6"])
        self.assertEqual(data["courses"]["CPSC 110"], {"title": "Computation, Programs, and Programming",
                                                       "credits": 4, "offered": ["W1", "W2"]})
        self.assertEqual(data["groups"]["LINEAR_ALGEBRA"], "MATH 111 or 221")
        self.assertIn("AI_ML", data["tracks"])
        self.assertEqual(data["presign_minutes"], settings.S3_PRESIGN_EXPIRES // 60)

    def test_data_is_escaped(self):
        call_command("load_data", stdout=StringIO(), stderr=StringIO())
        models.Student.objects.filter(code="S1").update(name="</script><script>alert(1)</script>")
        resp = self.client.get("/")
        self.assertNotContains(resp, "</script><script>alert(1)")
        self.assertEqual(page_data(resp)["students"][0]["name"], "</script><script>alert(1)</script>")
