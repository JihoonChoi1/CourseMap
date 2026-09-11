"""Validate the second oracle (oracle_free.py): does it agree with the original oracle (oracle.py)?

oracle_free works differently by never expanding target sets, so agreement between the two oracles
supports both. Real data (data/ubc) is too large for the original oracle to run, so only
oracle_free is used there (test_ubc.py).
"""

import random
import unittest

from helpers import DATA_DIR
from engine.loader import load_all
from engine.validate import validate
from oracle import exists_plan
from oracle_free import exists_plan_free, find_plan_free, min_terms_free
from test_fuzz import SEED, random_instance


class OracleFreeAgreesWithOracleTest(unittest.TestCase):
    def test_random_instances(self):
        rng = random.Random(SEED + 10)
        checked = 0
        feasible = 0
        for i in range(3000):
            cat, progs, stu = random_instance(rng)
            if len(validate(cat, progs).errors) > 0:
                continue
            groups = progs.degree.groups + progs.tracks[0].groups
            a = exists_plan(cat, groups, stu.completed, stu.start_season, stu.num_terms, stu.max_credits)
            b = exists_plan_free(cat, groups, stu.completed, stu.start_season, stu.num_terms, stu.max_credits)
            with self.subTest(instance=i):
                self.assertEqual(a, b)
            checked += 1
            feasible += a
        self.assertGreater(checked, 2700)
        self.assertGreater(feasible, checked * 0.3)

    def test_virtual_data_grid(self):
        cat, progs, students = load_all(DATA_DIR)
        for s in students:
            groups = progs.degree.groups + [t for t in progs.tracks if t.id == s.track][0].groups
            for cap in range(3, 17):
                for n in range(1, 9):
                    with self.subTest(student=s.id, cap=cap, num_terms=n):
                        self.assertEqual(exists_plan_free(cat, groups, s.completed, s.start_season, n, cap),
                                         exists_plan(cat, groups, s.completed, s.start_season, n, cap))

    def test_witness_respects_constraints(self):
        # whether the returned placement actually respects the constraints (verify_plan is checked against real data in test_ubc.py)
        cat, progs, students = load_all(DATA_DIR)
        s = students[0]
        groups = progs.degree.groups + [t for t in progs.tracks if t.id == s.track][0].groups
        terms = find_plan_free(cat, groups, s.completed, s.start_season, 6, 15)
        self.assertIsNot(terms, False)
        seen = set(s.completed)
        for t, ids in enumerate(terms):
            self.assertLessEqual(sum(cat.by_id[c].credits for c in ids), 15)
            for c in ids:
                for clause in cat.by_id[c].prereqs:
                    self.assertTrue(any(x in seen for x in clause), c)
            seen.update(ids)

    def test_min_terms_and_limit(self):
        cat, progs, students = load_all(DATA_DIR)
        s = students[0]
        groups = progs.degree.groups + [t for t in progs.tracks if t.id == s.track][0].groups
        self.assertEqual(min_terms_free(cat, groups, s.completed, s.start_season, 8, 15), 6)
        self.assertEqual(min_terms_free(cat, groups, s.completed, s.start_season, 5, 15), 0)
        self.assertIsNone(exists_plan_free(cat, groups, s.completed, s.start_season, 8, 15, node_limit=1))


if __name__ == "__main__":
    unittest.main()
