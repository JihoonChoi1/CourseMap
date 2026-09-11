"""Whether verify_plan itself catches violations: deliberately break a valid plan to check."""

import copy
import unittest

from helpers import F, EngineTestCase, all_group, catalog, course, pick_group, programs, student, term_of
from engine.verify import verify_plan


def move(plan, cid, t):
    """Move cid to term t and recompute the credits field (so no other violation gets mixed in)."""
    for term in plan["terms"]:
        if cid in term["courses"]:
            term["courses"].remove(cid)
    plan["terms"][t]["courses"].append(cid)


def fix_credits(cat, plan):
    for term in plan["terms"]:
        term["credits"] = sum(cat.by_id[c].credits for c in term["courses"])


class VerifyPlanTest(EngineTestCase, unittest.TestCase):
    def setUp(self):
        # 4 terms starting FALL. A -> B (prereq), K coreq B (one-way), FO is FALL-only, PICK 1 of E1/E2
        self.cat = catalog([
            course("A"), course("B", prereqs=[["A"]]), course("K", 1, coreqs=[["B"]]),
            course("FO", offered=F), course("E1"), course("E2"), course("DONE"),
        ])
        self.progs = programs([all_group("REQ", ["B", "K", "FO"]), pick_group("P", 1, ["E1", "E2"])])
        self.stu = student(num_terms=4, cap=7, completed=["DONE"])
        res = self.plan(self.cat, self.progs, self.stu)
        self.assertFeasible(res)
        self.good = res.plan
        self.t = term_of(self.good)

    def broken(self, mutate, stu=None):
        plan = copy.deepcopy(self.good)
        mutate(plan)
        return verify_plan(self.cat, self.progs, stu or self.stu, plan)

    def assertViolation(self, violations, *keywords):
        self.assertGreater(len(violations), 0, "failed to catch the violation")
        joined = "\n".join(violations)
        for k in keywords:
            self.assertIn(k, joined)

    def test_good_plan_passes(self):
        self.assertEqual(verify_plan(self.cat, self.progs, self.stu, self.good), [])

    def test_prereq_same_term(self):
        def m(p):
            move(p, "B", self.t["A"])
            move(p, "K", self.t["A"])
            fix_credits(self.cat, p)
        self.assertViolation(self.broken(m, student(num_terms=4, cap=99, completed=["DONE"])), "B: prereq [A]")

    def test_coreq_after_partner(self):
        def m(p):
            move(p, "K", self.t["B"] - 1)
            fix_credits(self.cat, p)
        self.assertViolation(self.broken(m, student(num_terms=4, cap=99, completed=["DONE"])), "K: coreq [B]")

    def test_not_offered(self):
        spring = [i for i, term in enumerate(self.good["terms"]) if term["season"] == "SPRING"][0]

        def m(p):
            move(p, "FO", spring)
            fix_credits(self.cat, p)
        self.assertViolation(self.broken(m, student(num_terms=4, cap=99, completed=["DONE"])), "FO: SPRING 미개설")

    def test_over_cap(self):
        self.assertViolation(self.broken(lambda p: None, student(num_terms=4, cap=3, completed=["DONE"])), "> 상한 3")

    def test_credits_field_mismatch(self):
        def m(p):
            p["terms"][0]["credits"] += 1
        self.assertViolation(self.broken(m), "credits 필드")

    def test_duplicate_placement(self):
        def m(p):
            p["terms"][-1]["courses"].append(p["terms"][0]["courses"][0])
            fix_credits(self.cat, p)
        self.assertViolation(self.broken(m, student(num_terms=4, cap=99, completed=["DONE"])), "중복 배치")

    def test_completed_course_placed(self):
        def m(p):
            p["terms"][0]["courses"].append("DONE")
            fix_credits(self.cat, p)
        self.assertViolation(self.broken(m, student(num_terms=4, cap=99, completed=["DONE"])), "DONE: 이미 이수한")

    def test_unknown_course(self):
        def m(p):
            p["terms"][0]["courses"].append("GHOST")
        self.assertViolation(self.broken(m), "GHOST: 카탈로그에 없는")

    def test_required_course_missing(self):
        def m(p):
            for term in p["terms"]:
                if "FO" in term["courses"]:
                    term["courses"].remove("FO")
            fix_credits(self.cat, p)
        self.assertViolation(self.broken(m), "그룹 REQ: 2/3")

    def test_pick_n_unmet(self):
        def m(p):
            for term in p["terms"]:
                for e in ("E1", "E2"):
                    if e in term["courses"]:
                        term["courses"].remove(e)
            fix_credits(self.cat, p)
        self.assertViolation(self.broken(m), "그룹 P: 0/1")

    def test_chosen_missing(self):
        def m(p):
            p["chosen"] = {}
        self.assertViolation(self.broken(m), "chosen.P: 0개 < n=1")

    def test_chosen_not_placed(self):
        picked = self.good["chosen"]["P"][0]
        other = "E2" if picked == "E1" else "E1"

        def m(p):
            p["chosen"]["P"] = [other]
        self.assertViolation(self.broken(m), "chosen.P: " + other)

    def test_chosen_outside_group(self):
        def m(p):
            p["chosen"]["P"].append("A")
        self.assertViolation(self.broken(m), "chosen.P: A")

    def test_wrong_term_label(self):
        def m(p):
            p["terms"][0]["season"] = "SPRING"
        self.assertViolation(self.broken(m), "학기 0: 2026 SPRING (기대 2026 FALL)")

    def test_wrong_year(self):
        def m(p):
            p["terms"][1]["year"] += 1
        self.assertViolation(self.broken(m), "학기 1:")

    def test_too_many_terms(self):
        self.assertViolation(self.broken(lambda p: None, student(num_terms=len(self.good["terms"]) - 1, cap=7,
                                                                 completed=["DONE"])), "num_terms")

    def test_infeasible_plan_is_not_verified(self):
        def m(p):
            p["feasible"] = False
        self.assertEqual(self.broken(m), ["feasible=false인 plan은 검증 대상이 아님"])

    def test_or_prereq_needs_only_one_alternative(self):
        cat = catalog([course("X"), course("Y"), course("C", prereqs=[["X", "Y"]])])
        progs = programs([all_group("G", ["C"])])
        stu = student(num_terms=2)
        plan = {"student_id": "ST", "feasible": True, "chosen": {}, "errors": [], "terms": [
            {"year": 2026, "season": "FALL", "courses": ["Y"], "credits": 3},
            {"year": 2027, "season": "SPRING", "courses": ["C"], "credits": 3},
        ]}
        self.assertEqual(verify_plan(cat, progs, stu, plan), [])
        plan["terms"][0]["courses"] = []
        plan["terms"][0]["credits"] = 0
        self.assertViolation(verify_plan(cat, progs, stu, plan), "C: prereq [X | Y]")


if __name__ == "__main__":
    unittest.main()
