"""Credit cap: E_COURSE_OVER_CAP, single-term pinned overload, window-total shortage, cases where
credit combinations can't fill the cap, and the diagnosis path when greedy fails."""

import unittest
from unittest import mock

from helpers import (F, S, EngineTestCase, all_group, catalog, codes, course, pick_group, programs, student,
                     term_of)


class CapacityTest(EngineTestCase, unittest.TestCase):
    def test_course_over_cap(self):
        cat = catalog([course("A", 4), course("B", 2)])
        res = self.plan(cat, programs([all_group("G", ["A", "B"])]), student(num_terms=4, cap=3))
        self.assertEqual(codes(res.plan["errors"]), ["E_COURSE_OVER_CAP"])
        self.assertEqual(res.plan["errors"][0]["courses"], ["A"])

    def test_over_cap_course_outside_targets_is_ignored(self):
        cat = catalog([course("A", 3), course("HUGE", 20)])
        res = self.plan(cat, programs([all_group("G", ["A"])]), student(num_terms=1, cap=3))
        self.assertFeasible(res)

    def test_over_cap_prereq_pulled_by_closure(self):
        cat = catalog([course("HUGE", 5), course("A", prereqs=[["HUGE"]])])
        res = self.plan(cat, programs([all_group("G", ["A"])]), student(num_terms=4, cap=4))
        self.assertEqual(codes(res.plan["errors"]), ["E_COURSE_OVER_CAP"])
        self.assertEqual(res.plan["errors"][0]["courses"], ["HUGE"])

    def test_exactly_at_cap_is_allowed(self):
        cat = catalog([course("A", 4), course("B", 3)])
        res = self.plan(cat, programs([all_group("G", ["A", "B"])]), student(num_terms=1, cap=7))
        self.assertFeasible(res)
        self.assertEqual(res.plan["terms"][0]["credits"], 7)

    def pinned(self):
        # a miniature S3: 2 terms starting FALL, A(S,3) and B(S,4) can only go in SPRING (term 1)
        return catalog([course("A", 3, offered=S), course("B", 4, offered=S), course("C", 3, offered=F)])

    def test_single_term_overload(self):
        res = self.plan(self.pinned(), programs([all_group("G", ["A", "B", "C"])]), student(num_terms=2, cap=6))
        self.assertInfeasible(res, "E_INFEASIBLE")
        errs = res.plan["errors"]
        self.assertEqual(len(errs), 1)
        self.assertEqual(sorted(errs[0]["courses"]), ["A", "B"])
        self.assertIn("학점 부족", errs[0]["message"])
        self.assertIn("2027 SPRING에 반드시 배치", errs[0]["message"])
        self.assertIn("최소 학기 상한은 7", errs[0]["message"])

    def test_single_term_overload_resolved_by_cap(self):
        res = self.plan(self.pinned(), programs([all_group("G", ["A", "B", "C"])]), student(num_terms=2, cap=7))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"C": 0, "A": 1, "B": 1})

    def test_interval_total_shortage(self):
        # 5 courses x 3 credits = 15 > 2 terms x 6
        cat = catalog([course("A"), course("B"), course("C"), course("D"), course("E")])
        res = self.plan(cat, programs([all_group("G", ["A", "B", "C", "D", "E"])]), student(num_terms=2, cap=6))
        self.assertInfeasible(res, "E_INFEASIBLE")
        msg = res.plan["errors"][0]["message"]
        self.assertIn("학점 부족", msg)
        self.assertIn("2학기 × 상한 6 = 12", msg)
        self.assertIn("최소 학기 상한은 8", msg)
        self.assertFeasible(self.plan(cat, programs([all_group("G", ["A", "B", "C", "D", "E"])]),
                                      student(num_terms=2, cap=9)))

    def test_interval_shortage_inside_longer_horizon(self):
        # the cause is a narrower window than the total (15 > 4x3): T1-T3 (9 credits) after the
        # R1->R2 chain can only go in the last 2 terms, but 2x3 = 6. The narrowest violating window must be reported.
        cat = catalog([course("R1"), course("R2", prereqs=[["R1"]]),
                       course("T1", prereqs=[["R2"]]), course("T2", prereqs=[["R2"]]), course("T3", prereqs=[["R2"]])])
        res = self.plan(cat, programs([all_group("G", ["T1", "T2", "T3"])]), student(num_terms=4, cap=3))
        self.assertInfeasible(res, "E_INFEASIBLE")
        msg = res.plan["errors"][0]["message"]
        self.assertIn("2027 FALL ~ 2028 SPRING (2학기)", msg)
        self.assertEqual(sorted(res.plan["errors"][0]["courses"]), ["T1", "T2", "T3"])

    def test_credit_combination_cannot_fill_cap(self):
        # 3,3,3,3,4 = 16 = 2 terms x cap 8. But the max per term is 3+4=7 or 3+3=6 -> 14 < 16
        cat = catalog([course("A"), course("B"), course("C"), course("D"), course("E", 4)])
        res = self.plan(cat, programs([all_group("G", ["A", "B", "C", "D", "E"])]), student(num_terms=2, cap=8))
        self.assertInfeasible(res, "E_INFEASIBLE")
        msg = res.plan["errors"][0]["message"]
        self.assertIn("과목 학점 조합상", msg)
        self.assertIn("최대 7+7", msg)
        self.assertNotIn("최소 학기 상한", msg)

    def test_greedy_packs_to_exact_cap(self):
        # 4,4,3,3 in 2 terms with cap 7: only feasible if paired as 4+3 / 4+3
        cat = catalog([course("A", 4), course("B", 4), course("C"), course("D")])
        res = self.plan(cat, programs([all_group("G", ["A", "B", "C", "D"])]), student(num_terms=2, cap=7))
        self.assertFeasible(res)
        self.assertEqual([t["credits"] for t in res.plan["terms"]], [7, 7])


class ExactSearchTest(EngineTestCase, unittest.TestCase):
    """If greedy fails, search again exhaustively: guaranteed to find one if it exists, or prove infeasibility if not."""

    def greedy_trap(self):
        # 4 terms starting SPRING (S, F, S, F), cap 5. C3 is coreq with C1, C4(S) comes after C3, C5 is SPRING-only.
        # Greedy places C1+C3 (the one with the longer tail) in the first SPRING, then fails when
        # C4+C5 (6 > 5) pile up in the later SPRING.
        # The answer: S C1+C5 / F C3 / S C4 / F C2
        cat = catalog([course("C1", 1, offered=S), course("C2"), course("C3", coreqs=[["C1"]]),
                       course("C4", offered=S, prereqs=[["C3"]]), course("C5", offered=S)])
        return cat, programs([all_group("REQ", ["C1", "C2", "C4", "C5"])])

    def test_finds_plan_greedy_misses(self):
        cat, progs = self.greedy_trap()
        res = self.plan(cat, progs, student(num_terms=4, cap=5, start=(2026, "SPRING")))
        self.assertFeasible(res)
        self.assertEqual(len(res.plan["terms"]), 4)

    def test_greedy_alone_fails_on_trap(self):
        # confirms the above test passes thanks to exhaustive search: with a search limit of 0, only the greedy result remains
        cat, progs = self.greedy_trap()
        with mock.patch("engine.scheduler.EXACT_NODE_LIMIT", 0):
            res = self.plan(cat, progs, student(num_terms=4, cap=5, start=(2026, "SPRING")), oracle=False)
        self.assertInfeasible(res, "E_INFEASIBLE")
        self.assertIn("불가능이 증명된 것은 아님", res.plan["errors"][0]["message"])

    def test_min_terms_even_when_greedy_succeeds(self):
        # given 8 terms, greedy "succeeds" with a 5-term placement, but the optimum is 4 terms. Exhaustive search must confirm whether a shorter one exists.
        cat, progs = self.greedy_trap()
        res = self.plan(cat, progs, student(num_terms=8, cap=5, start=(2026, "SPRING")))
        self.assertFeasible(res)
        self.assertEqual(len(res.plan["terms"]), 4)
        self.assertTrue(res.min_terms_proven)

    def test_greedy_alone_is_not_minimal_on_trap(self):
        # confirms the above test passes thanks to minimum-term search: with limit 0, greedy's 5 terms remain and it's marked not-guaranteed
        cat, progs = self.greedy_trap()
        with mock.patch("engine.scheduler.EXACT_NODE_LIMIT", 0):
            res = self.plan(cat, progs, student(num_terms=8, cap=5, start=(2026, "SPRING")))
        self.assertFeasible(res)
        self.assertEqual(len(res.plan["terms"]), 5)
        self.assertFalse(res.min_terms_proven)

    def test_min_terms_proven_without_search_at_lower_bound(self):
        # if the greedy result already matches the prerequisite-chain lower bound, it's minimal without any search
        cat = catalog([course("A"), course("B", prereqs=[["A"]])])
        with mock.patch("engine.scheduler.EXACT_NODE_LIMIT", 0):
            res = self.plan(cat, programs([all_group("G", ["B"])]), student(num_terms=4))
        self.assertEqual(len(res.plan["terms"]), 2)
        self.assertTrue(res.min_terms_proven)

    def test_min_terms_not_proven_with_candidate_fallback(self):
        cat = catalog([course("E1"), course("E2"), course("E3")])
        with mock.patch("engine.targets.MAX_CANDIDATES", 1):
            res = self.plan(cat, programs([pick_group("P", 1, ["E1", "E2", "E3"])]), student(num_terms=2))
        self.assertTrue(res.used_fallback)
        self.assertFalse(res.min_terms_proven)

    def test_proves_infeasible_when_precheck_cannot(self):
        # credit total 6 = 2 terms x 3 passes the precheck. But C2(3) can't go in the same term as
        # C0(1), so C0 must come first, which piles C1(2) and C2(3) into the second term (5 > 3).
        cat = catalog([course("C0", 1), course("C1", 2, prereqs=[["C0"]]), course("C2", 3, coreqs=[["C0"]])])
        res = self.plan(cat, programs([all_group("REQ", ["C1", "C2"])]), student(num_terms=2, cap=3))
        self.assertInfeasible(res, "E_INFEASIBLE")
        errs = res.plan["errors"]
        self.assertEqual(len(errs), 1)
        self.assertIn("모든 배치 조합을 탐색한 결과", errs[0]["message"])
        self.assertNotIn("증명된 것은 아님", errs[0]["message"])

    def test_proves_infeasible_with_precheck_bypassed(self):
        cat = catalog([course("A", 3, offered=S), course("B", 4, offered=S), course("C", 3, offered=F)])
        with mock.patch("engine.scheduler._precheck", return_value=[]):
            res = self.plan(cat, programs([all_group("G", ["A", "B", "C"])]), student(num_terms=2, cap=6))
        self.assertInfeasible(res, "E_INFEASIBLE")
        self.assertIn("모든 배치 조합을 탐색한 결과", res.plan["errors"][0]["message"])

    def test_unproven_candidate_is_flagged_even_if_representative_is_proven(self):
        # A real case from ISSUE-002. Two candidates: candidate 1 is proven infeasible by the precheck, candidate 2 is feasible (but greedy can't solve it).
        cat = catalog([
            course("C0", 3), course("C1", 2, offered=F, prereqs=[["C0"]]), course("C2", 1, offered=F),
            course("C3", 3, offered=F), course("C4", 4, coreqs=[["C3"]]), course("C5", 3, prereqs=[["C0"]]),
            course("C6", 4, offered=S, prereqs=[["C5"]], coreqs=[["C7"]]),
            course("C7", 3, prereqs=[["C1", "C4"], ["C2"]], coreqs=[["C6"]]),
        ])
        progs = programs([all_group("REQ", ["C6", "C4", "C7"]), pick_group("P1", 2, ["C7", "C0", "C3"])],
                         {"T": [pick_group("P2", 1, ["C7", "C5", "C0"])]})
        stu = student(num_terms=4, cap=7, start=(2026, "SPRING"))
        # with exhaustive search, a placement is found in candidate 2
        self.assertFeasible(self.plan(cat, progs, stu))
        # with a search limit of 0 it can't be found, but even if the representative cause looks like a proof, a "not proven" warning must still be attached
        with mock.patch("engine.scheduler.EXACT_NODE_LIMIT", 0):
            res = self.plan(cat, progs, stu, oracle=False)
        self.assertFalse(res.plan["feasible"])
        self.assertEqual(res.candidates, 2)
        self.assertIn("전수탐색 한도를 넘어 판정하지 못함", res.plan["errors"][-1]["message"])


class GreedyFailureDiagnosisTest(EngineTestCase, unittest.TestCase):
    """The diagnosis message for when exhaustive search exceeds its limit. Confirmed by disabling the precheck and the search limit."""

    def run_bypassed(self, cat, progs, stu):
        with mock.patch("engine.scheduler._precheck", return_value=[]):
            with mock.patch("engine.scheduler.EXACT_NODE_LIMIT", 0):
                return self.plan(cat, progs, stu)

    def test_diagnose_when_search_is_cut_off(self):
        cat = catalog([course("A", 3, offered=S), course("B", 4, offered=S), course("C", 3, offered=F)])
        res = self.run_bypassed(cat, programs([all_group("G", ["A", "B", "C"])]), student(num_terms=2, cap=6))
        self.assertInfeasible(res, "E_INFEASIBLE")
        errs = res.plan["errors"]
        self.assertIn("불가능이 증명된 것은 아님", errs[0]["message"])
        self.assertEqual(errs[0]["courses"], ["A"])  # B(4) is placed first, leaving A unplaced
        self.assertEqual(len(errs), 2)
        self.assertIn("학점 상한에 걸림", errs[1]["message"])
        self.assertIn("2027 SPRING 4/6", errs[1]["message"])

    def test_diagnose_skips_blocked_descendants(self):
        # both A and B have descendants so they tie in priority, and the larger-credit B goes in first -> A is unplaced, and DA is downstream of A
        cat = catalog([course("A", 3, offered=S), course("B", 4, offered=S), course("C", 3, offered=F),
                       course("DA", 1, offered=F, prereqs=[["A"]]), course("DB", 1, offered=F, prereqs=[["B"]])])
        res = self.run_bypassed(cat, programs([all_group("G", ["C", "DA", "DB"])]), student(num_terms=3, cap=6))
        errs = res.plan["errors"]
        self.assertEqual(sorted(errs[0]["courses"]), ["A", "DA"])
        detail_courses = [e["courses"] for e in errs[1:]]
        self.assertEqual(detail_courses, [["A"]])  # DA is downstream of A, so it's excluded from the cause list


if __name__ == "__main__":
    unittest.main()
