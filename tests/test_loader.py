"""Student/catalog input shape errors raise loader's InputError, not a Plan (confirmed decision 5)."""

import json
import os
import tempfile
import unittest

from helpers import SF
from engine.loader import InputError, load_catalog, load_programs, load_students


def good_student(**overrides):
    s = {"id": "S", "name": "S", "track": "T", "completed": [], "start_term": {"year": 2026, "season": "FALL"},
         "num_terms": 4, "max_credits_per_term": 15}
    s.update(overrides)
    return s


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, obj, name="x.json", raw=None):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps(obj))
        return path

    def students(self, s):
        return load_students(self.write({"students": [s]}), SF)

    def test_valid_student(self):
        out = self.students(good_student())
        self.assertEqual(out[0].start_season, "FALL")
        self.assertEqual(out[0].max_credits, 15)

    def test_unknown_start_season(self):
        with self.assertRaisesRegex(InputError, "SUMMER"):
            self.students(good_student(start_term={"year": 2026, "season": "SUMMER"}))

    def test_num_terms_below_one(self):
        with self.assertRaisesRegex(InputError, "num_terms"):
            self.students(good_student(num_terms=0))

    def test_max_credits_below_one(self):
        with self.assertRaisesRegex(InputError, "max_credits_per_term"):
            self.students(good_student(max_credits_per_term=0))

    def test_bool_is_not_int(self):
        with self.assertRaisesRegex(InputError, "num_terms"):
            self.students(good_student(num_terms=True))

    def test_missing_field(self):
        s = good_student()
        del s["track"]
        with self.assertRaisesRegex(InputError, "track"):
            self.students(s)

    def test_completed_must_be_string_list(self):
        with self.assertRaises(InputError):
            self.students(good_student(completed=["A", 1]))

    def test_catalog_cnf_shape(self):
        c = {"id": "A", "title": "A", "credits": 3, "offered": SF, "prereqs": ["B"], "coreqs": []}
        with self.assertRaisesRegex(InputError, r"\[\]\[\]string"):
            load_catalog(self.write({"seasons": SF, "courses": [c]}))

    def test_catalog_empty_seasons(self):
        with self.assertRaisesRegex(InputError, "seasons"):
            load_catalog(self.write({"seasons": [], "courses": []}))

    def test_catalog_keeps_first_duplicate_and_file_order(self):
        cs = [{"id": i, "title": i, "credits": cr, "offered": SF, "prereqs": [], "coreqs": []}
              for i, cr in (("B", 3), ("A", 3), ("B", 4))]
        cat = load_catalog(self.write({"seasons": SF, "courses": cs}))
        self.assertEqual(len(cat.courses), 3)
        self.assertEqual(cat.by_id["B"].credits, 3)
        self.assertEqual(cat.index, {"B": 0, "A": 1})

    def test_programs_group_n_type(self):
        g = {"id": "G", "name": "G", "rule": "PICK_N", "n": "2", "courses": []}
        with self.assertRaisesRegex(InputError, "'n'"):
            load_programs(self.write({"degree": {"id": "D", "name": "D", "groups": [g]}, "tracks": []}))

    def test_invalid_json(self):
        with self.assertRaisesRegex(InputError, "JSON"):
            load_catalog(self.write(None, raw="{not json"))

    def test_missing_file(self):
        with self.assertRaisesRegex(InputError, "읽을 수 없음"):
            load_catalog(os.path.join(self.tmp.name, "nope.json"))


if __name__ == "__main__":
    unittest.main()
