"""data/*.json scenario regressions (doc §6.5) + the §6.4 case run over the full catalog."""

import copy
import unittest

from helpers import DATA_DIR, EngineTestCase, all_group, pick_group, term_of
from engine.loader import load_all
from engine.model import Program, Programs
from engine.validate import validate


class ScenarioTest(EngineTestCase, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat, cls.progs, students = load_all(DATA_DIR)
        cls.students = {}
        for s in students:
            cls.students[s.id] = s

    def student(self, sid, **overrides):
        s = copy.copy(self.students[sid])
        for k in overrides:
            setattr(s, k, overrides[k])
        return s

    def test_dataset_passes_static_validation(self):
        res = validate(self.cat, self.progs)
        self.assertEqual(res.errors, [])
        self.assertEqual(res.bundles, [["CS101", "CS101L"]])
        self.assertEqual(len(self.cat.courses), 27)
        self.assertEqual(sum(c.credits for c in self.cat.courses), 82)

    def test_S1_feasible_in_6_terms(self):
        res = self.plan(self.cat, self.progs, self.student("S1"))
        self.assertFeasible(res)
        self.assertEqual(len(res.plan["terms"]), 6)
        self.assertEqual(res.lower_bound_terms, 6)
        self.assertEqual(res.candidates, 18)
        t = term_of(res.plan)
        self.assertEqual(t["CS101"], t["CS101L"])
        self.assertGreaterEqual(t["CS310"] - t["CS210"], 2)  # FALL-only -> FALL-only

    def test_S2_feasible_in_4_terms(self):
        res = self.plan(self.cat, self.progs, self.student("S2"))
        self.assertFeasible(res)
        self.assertEqual(len(res.plan["terms"]), 4)
        self.assertEqual(res.candidates, 9)
        for term in res.plan["terms"]:
            self.assertLessEqual(term["credits"], 12)

    def test_S3_infeasible_single_term_overload(self):
        res = self.plan(self.cat, self.progs, self.student("S3"))
        self.assertInfeasible(res, "E_INFEASIBLE")
        first = res.plan["errors"][0]
        self.assertEqual(sorted(first["courses"]), ["CS371", "CS490"])
        self.assertIn("2028 SPRING", first["message"])
        self.assertIn("최소 학기 상한은 7", first["message"])
        self.assertIn("2개 모두 실패", res.plan["errors"][-1]["message"])

    def test_S3_feasible_with_cap_7(self):
        res = self.plan(self.cat, self.progs, self.student("S3", max_credits=7))
        self.assertFeasible(res)
        t = term_of(res.plan)
        self.assertEqual(t["CS371"], 1)
        self.assertEqual(t["CS490"], 1)

    def test_S4_not_offered(self):
        res = self.plan(self.cat, self.progs, self.student("S4"))
        self.assertInfeasible(res, "E_NOT_OFFERED")
        self.assertEqual(len(res.plan["errors"]), 1)
        self.assertEqual(res.plan["errors"][0]["courses"], ["CS310"])

    def test_S4_feasible_with_2_terms(self):
        res = self.plan(self.cat, self.progs, self.student("S4", num_terms=2))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"CS490": 0, "CS310": 1})

    def test_choice_changes_cost_cs411_cs340(self):
        # §6.4: taking CS411 (required) pulls in CS340 via closure, which fills the major elective
        g = self.progs.degree.groups
        track = Program(id="COMPILER", name="COMPILER", groups=[all_group("CMP_CORE", ["CS330", "CS411"])])
        progs = Programs(degree=Program(id="CS_BS", name="CS_BS", groups=g), tracks=[track])
        res = self.plan(self.cat, progs, self.student("S2", track="COMPILER", num_terms=6))
        self.assertFeasible(res)
        self.assertEqual(res.candidates, 1)
        self.assertEqual(res.plan["chosen"]["CS_ELECTIVE"], ["CS330", "CS340"])
        t = term_of(res.plan)
        self.assertLess(t["CS340"], t["CS411"])
        self.assertLess(t["CS210"], t["CS411"])

    def test_choice_changes_cost_cs411_as_pick(self):
        # If CS411 is a track pick (PICK 1), the combination that chooses CS340 as a major elective
        # reduces CS411's cost. Removes the CS420/CS410 alternatives to force choosing CS411, and checks
        # whether the elective combination that includes CS340 is selected.
        g = self.progs.degree.groups
        track = Program(id="PL", name="PL", groups=[pick_group("PL_ELECTIVE", 1, ["CS411"])])
        progs = Programs(degree=Program(id="CS_BS", name="CS_BS", groups=g), tracks=[track])
        res = self.plan(self.cat, progs, self.student("S2", track="PL", num_terms=6))
        self.assertFeasible(res)
        self.assertIn("CS340", res.plan["chosen"]["CS_ELECTIVE"])
        self.assertEqual(len(res.plan["chosen"]["CS_ELECTIVE"]), 2)


if __name__ == "__main__":
    unittest.main()
