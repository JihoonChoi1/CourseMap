"""Property test: a 448-cell grid of 4 data/*.json students × cap 3-16 × terms 1-8.

- if feasible, verify_plan passes
- if infeasible, the exhaustive-search oracle also says infeasible (= 0 cases missed by greedy)
- if feasible, the oracle also says feasible (a sanity check on the oracle itself)
"""

import copy
import unittest

from helpers import DATA_DIR
from engine.loader import load_all
from engine.planner import plan_student
from engine.validate import validate
from engine.verify import verify_plan
from oracle import exists_plan

CAPS = range(3, 17)
TERMS = range(1, 9)


class GridPropertyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat, cls.progs, cls.students = load_all(DATA_DIR)
        cls.static = validate(cls.cat, cls.progs)
        cls.cases = []  # (student, cap, num_terms, plan result, oracle feasible)
        for s in cls.students:
            groups = cls.progs.degree.groups + [t for t in cls.progs.tracks if t.id == s.track][0].groups
            for cap in CAPS:
                for n in TERMS:
                    st = copy.copy(s)
                    st.max_credits = cap
                    st.num_terms = n
                    res = plan_student(cls.cat, cls.progs, cls.static, st)
                    exists = exists_plan(cls.cat, groups, st.completed, st.start_season, n, cap)
                    cls.cases.append((st, cap, n, res, exists))

    def label(self, st, cap, n):
        return st.id + " cap=" + str(cap) + " num_terms=" + str(n)

    def test_grid_size_and_counts(self):
        self.assertEqual(len(self.cases), 448)
        feasible = sum(1 for c in self.cases if c[3].plan["feasible"])
        self.assertEqual(feasible, 246)
        self.assertEqual(len(self.cases) - feasible, 202)

    def test_feasible_plans_pass_verify(self):
        for st, cap, n, res, _ in self.cases:
            if res.plan["feasible"]:
                with self.subTest(case=self.label(st, cap, n)):
                    self.assertEqual(verify_plan(self.cat, self.progs, st, res.plan), [])

    def test_greedy_never_misses_a_feasible_instance(self):
        missed = []
        for st, cap, n, res, exists in self.cases:
            if exists and not res.plan["feasible"]:
                missed.append(self.label(st, cap, n))
        self.assertEqual(missed, [])

    def test_oracle_agrees_on_feasible(self):
        for st, cap, n, res, exists in self.cases:
            if res.plan["feasible"]:
                with self.subTest(case=self.label(st, cap, n)):
                    self.assertTrue(exists)

    def test_infeasible_results_are_proven(self):
        # when the oracle says infeasible, a "not proven" (greedy-limitation) message must never appear
        for st, cap, n, res, exists in self.cases:
            if not res.plan["feasible"]:
                with self.subTest(case=self.label(st, cap, n)):
                    for e in res.plan["errors"]:
                        self.assertNotIn("증명된 것은 아님", e["message"])

    def test_feasible_plans_use_minimum_terms(self):
        # 1st-priority objective "earliest graduation": the result's term count equals the oracle's minimum, and the engine proved it too.
        min_terms = {}
        for st, cap, n, res, exists in self.cases:
            key = (st.id, cap)
            if exists and (key not in min_terms or n < min_terms[key]):
                min_terms[key] = n
        for st, cap, n, res, _ in self.cases:
            if res.plan["feasible"]:
                with self.subTest(case=self.label(st, cap, n)):
                    self.assertEqual(len(res.plan["terms"]), min_terms[(st.id, cap)])
                    self.assertTrue(res.min_terms_proven)


if __name__ == "__main__":
    unittest.main()
