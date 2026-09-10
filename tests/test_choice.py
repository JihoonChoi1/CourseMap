"""Elective groups (PICK_N) and prereq OR-alternative selection."""

import unittest
from unittest import mock

from helpers import (F, S, EngineTestCase, all_group, catalog, codes, course, pick_group, programs, student,
                     term_of)


class PickNTest(EngineTestCase, unittest.TestCase):
    def electives(self):
        return catalog([course("E1"), course("E2"), course("E3")])

    def test_filled_by_completed(self):
        res = self.plan(self.electives(), programs([pick_group("P", 2, ["E1", "E2", "E3"])]),
                        student(num_terms=1, completed=["E1", "E3"]))
        self.assertFeasible(res)
        self.assertEqual(res.plan["terms"], [])
        self.assertEqual(res.plan["chosen"], {"P": ["E1", "E3"]})
        self.assertEqual(res.candidates, 1)
        self.assertEqual(res.picks, {"P": []})

    def test_filled_partly_by_other_group(self):
        res = self.plan(self.electives(), programs([all_group("REQ", ["E2"]), pick_group("P", 2, ["E1", "E2", "E3"])]),
                        student(num_terms=1))
        self.assertFeasible(res)
        placed = term_of(res.plan)
        self.assertEqual(len(placed), 2)
        self.assertIn("E2", placed)
        self.assertEqual(len(res.picks["P"]), 1)
        self.assertNotIn("E2", res.picks["P"])
        self.assertEqual(res.plan["chosen"]["P"], sorted(placed, key=lambda c: ["E1", "E2", "E3"].index(c)))

    def test_filled_by_completed_plus_other_group(self):
        res = self.plan(self.electives(), programs([all_group("REQ", ["E2"]), pick_group("P", 2, ["E1", "E2", "E3"])]),
                        student(num_terms=1, completed=["E3"]))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"E2": 0})
        self.assertEqual(res.plan["chosen"], {"P": ["E2", "E3"]})

    def test_filled_by_prereq_closure_of_other_group(self):
        # E1, pulled in as a prerequisite of REQ, fills P
        cat = catalog([course("E1"), course("E2"), course("X", prereqs=[["E1"]])])
        res = self.plan(cat, programs([all_group("REQ", ["X"]), pick_group("P", 1, ["E1", "E2"])]), student(num_terms=2))
        self.assertFeasible(res)
        self.assertEqual(sorted(term_of(res.plan)), ["E1", "X"])
        self.assertEqual(res.plan["chosen"], {"P": ["E1"]})

    def test_same_course_satisfies_two_groups(self):
        cat = catalog([course("A"), course("B"), course("C")])
        progs = programs([pick_group("G1", 1, ["A", "B"])], {"T": [pick_group("G2", 1, ["A", "C"])]})
        res = self.plan(cat, progs, student(num_terms=1))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"A": 0})
        self.assertEqual(res.plan["chosen"], {"G1": ["A"], "G2": ["A"]})

    def test_choice_changes_prereq_cost(self):
        # a miniature §6.4 CS411/CS340. K411 needs K340 -> choosing K340 in ELEC saves cost.
        # since every combination ties at 3 terms, the 2nd-priority objective (fewest extra credits) decides.
        cat = catalog([
            course("BASE"),
            course("K340", prereqs=[["BASE"]]),
            course("K350", prereqs=[["BASE"]]),
            course("P", prereqs=[["BASE"]]),
            course("K411", 4, prereqs=[["K340"]]),
            course("K420", 4, prereqs=[["P"]]),
        ])
        progs = programs([all_group("REQ", ["BASE"]), pick_group("ELEC", 1, ["K340", "K350"])],
                         {"T": [pick_group("TRK", 1, ["K411", "K420"])]})
        res = self.plan(cat, progs, student(num_terms=4))
        self.assertFeasible(res)
        self.assertEqual(res.candidates, 4)
        self.assertEqual(res.picks, {"ELEC": ["K340"], "TRK": ["K411"]})
        self.assertEqual(res.total_credits, 3 + 3 + 4)
        self.assertEqual(res.plan["chosen"], {"ELEC": ["K340"], "TRK": ["K411"]})

    def test_objective_prefers_earlier_finish_over_fewer_credits(self):
        # CHEAP has fewer credits but a longer chain, finishing one term later
        cat = catalog([course("Q1", 1), course("Q2", 1, prereqs=[["Q1"]]), course("CHEAP", 1, prereqs=[["Q2"]]),
                       course("BIG", 6)])
        res = self.plan(cat, programs([pick_group("P", 1, ["CHEAP", "BIG"])]), student(num_terms=4))
        self.assertFeasible(res)
        self.assertEqual(res.picks, {"P": ["BIG"]})
        self.assertEqual(len(res.plan["terms"]), 1)

    def test_objective_uses_credits_when_terms_tie(self):
        cat = catalog([course("Q1", 1), course("CHEAP", 1, prereqs=[["Q1"]]), course("BIG", 6)])
        res = self.plan(cat, programs([all_group("REQ", ["Q1"]), pick_group("P", 1, ["BIG", "CHEAP"])]),
                        student(num_terms=4, cap=6))
        self.assertFeasible(res)
        # BIG: cap of 6 rules out the same term as Q1 -> 2 terms, 7 credits / CHEAP: Q1(0) + CHEAP(1) -> 2 terms, 2 credits
        self.assertEqual(res.picks, {"P": ["CHEAP"]})

    def test_all_combinations_fail_reports_closest(self):
        cat = catalog([course("E1", offered=F), course("E2", 4, offered=F)])
        res = self.plan(cat, programs([pick_group("P", 1, ["E1", "E2"])]),
                        student(num_terms=1, start=(2028, "SPRING")))
        self.assertInfeasible(res, "E_NOT_OFFERED")
        self.assertEqual(res.candidates, 2)
        errs = res.plan["errors"]
        self.assertEqual(codes(errs), ["E_NOT_OFFERED", "E_INFEASIBLE"])
        self.assertEqual(errs[0]["courses"], ["E1"])  # the one with fewer unplaced credits is the representative
        self.assertIn("2개 모두 실패", errs[1]["message"])
        self.assertIn("P=[E1]", errs[1]["message"])

    def test_one_combination_feasible_others_not(self):
        cat = catalog([course("E1", offered=F), course("E2", offered=["SPRING"])])
        res = self.plan(cat, programs([pick_group("P", 1, ["E1", "E2"])]),
                        student(num_terms=1, start=(2028, "SPRING")))
        self.assertFeasible(res)
        self.assertEqual(res.picks, {"P": ["E2"]})


class FallbackTest(EngineTestCase, unittest.TestCase):
    def fixture(self):
        cat = catalog([course("P1"), course("E1", prereqs=[["P1"]]), course("E2", 4), course("E3"), course("E4"),
                       course("S1"), course("S2")])
        progs = programs([pick_group("ELEC", 2, ["E1", "E2", "E3", "E4"])], {"T": [pick_group("SIDE", 1, ["S1", "S2"])]})
        return cat, progs

    def test_enumerates_when_under_limit(self):
        cat, progs = self.fixture()
        res = self.plan(cat, progs, student(num_terms=3))
        self.assertFeasible(res)
        self.assertFalse(res.used_fallback)
        self.assertEqual(res.candidates, 6 * 2)

    def test_greedy_fallback_when_over_limit(self):
        cat, progs = self.fixture()
        with mock.patch("engine.targets.MAX_CANDIDATES", 11):
            res = self.plan(cat, progs, student(num_terms=3))
        self.assertFeasible(res)
        self.assertTrue(res.used_fallback)
        self.assertEqual(res.candidates, 1)
        # marginal cost: E1 pulls in P1 for 6 total, E2 is 4 -> E3, E4 (3) come first
        self.assertEqual(res.picks, {"ELEC": ["E3", "E4"], "SIDE": ["S1"]})

    def test_fallback_marginal_cost_counts_prereq_closure(self):
        cat = catalog([course("P1", 2), course("E1", 2, prereqs=[["P1"]]), course("E2", 3)])
        progs = programs([pick_group("ELEC", 1, ["E1", "E2"])])
        with mock.patch("engine.targets.MAX_CANDIDATES", 1):
            res = self.plan(cat, progs, student(num_terms=3))
        self.assertTrue(res.used_fallback)
        self.assertEqual(res.picks, {"ELEC": ["E2"]})  # E1 is 2+2=4 > 3

    def test_fallback_shared_course_across_groups(self):
        # if the course chosen in G1 also fills G2, G2 needs no additional selection
        cat = catalog([course("A", 1), course("B", 2), course("C", 2)])
        progs = programs([pick_group("G1", 1, ["A", "B"])], {"T": [pick_group("G2", 1, ["A", "C"])]})
        with mock.patch("engine.targets.MAX_CANDIDATES", 1):
            res = self.plan(cat, progs, student(num_terms=1))
        self.assertFeasible(res)
        self.assertTrue(res.used_fallback)
        self.assertEqual(res.picks, {"G1": ["A"], "G2": []})
        self.assertEqual(res.plan["chosen"], {"G1": ["A"], "G2": ["A"]})

    def test_fallback_infeasible_still_reports(self):
        cat, progs = self.fixture()
        with mock.patch("engine.targets.MAX_CANDIDATES", 1):
            res = self.plan(cat, progs, student(num_terms=1, cap=3))
        self.assertTrue(res.used_fallback)
        self.assertInfeasible(res, "E_INFEASIBLE")


class OrAlternativeTest(EngineTestCase, unittest.TestCase):
    def test_reuses_alternative_already_in_set(self):
        # C is (A | B). A is cheaper, but B is already in REQ, so A is never added
        cat = catalog([course("A", 1), course("B", 3), course("C", prereqs=[["A", "B"]])])
        res = self.plan(cat, programs([all_group("REQ", ["B", "C"])]), student(num_terms=2))
        self.assertFeasible(res)
        self.assertEqual(sorted(term_of(res.plan)), ["B", "C"])

    def test_reuses_alternative_pulled_by_closure(self):
        # if B is pulled in as an AND prerequisite of D, C's (A | B) is satisfied by B
        cat = catalog([course("A", 1), course("B", 3), course("C", prereqs=[["A", "B"]]), course("D", prereqs=[["B"]])])
        res = self.plan(cat, programs([all_group("REQ", ["C", "D"])]), student(num_terms=2))
        self.assertFeasible(res)
        self.assertNotIn("A", term_of(res.plan))

    def test_completed_alternative_satisfies_clause(self):
        cat = catalog([course("A", 1), course("B", 3), course("C", prereqs=[["A", "B"]])])
        res = self.plan(cat, programs([all_group("REQ", ["C"])]), student(num_terms=1, completed=["B"]))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"C": 0})

    def test_picks_cheapest_including_closure(self):
        # A is 1 credit but drags in a 5-credit prerequisite PA -> B (3) is cheaper
        cat = catalog([course("PA", 5), course("A", 1, prereqs=[["PA"]]), course("B", 3),
                       course("C", prereqs=[["A", "B"]])])
        res = self.plan(cat, programs([all_group("REQ", ["C"])]), student(num_terms=3))
        self.assertFeasible(res)
        self.assertEqual(sorted(term_of(res.plan)), ["B", "C"])

    def test_tie_broken_by_earliest_start(self):
        # A and B are both 3 credits. A is FALL-only, so it's late if starting in SPRING -> B
        cat = catalog([course("A", offered=F), course("B"), course("C", prereqs=[["A", "B"]])])
        res = self.plan(cat, programs([all_group("REQ", ["C"])]), student(num_terms=3, start=(2027, "SPRING")))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"B": 0, "C": 1})

    def test_nested_or_resolution(self):
        # C is (A | B), and A is (X | Y). The newly introduced OR in A must also be expanded.
        # B's side also needs 3 terms due to prereq Z -> term count ties, so the lowest-credit option Y+A+C(5) must be picked
        cat = catalog([course("X", 2), course("Y", 1), course("A", 1, prereqs=[["X", "Y"]]),
                       course("Z", 1), course("B", 5, prereqs=[["Z"]]), course("C", prereqs=[["A", "B"]])])
        res = self.plan(cat, programs([all_group("REQ", ["C"])]), student(num_terms=3))
        self.assertFeasible(res)
        self.assertEqual(res.candidates, 3)  # {X,A,C}, {Y,A,C}, {Z,B,C}
        self.assertEqual(term_of(res.plan), {"Y": 0, "A": 1, "C": 2})

    def test_or_alternative_that_finishes_earlier_wins(self):
        # same as above, but if B has no prerequisite, B+C finishes in 2 terms, so B is chosen despite more credits
        cat = catalog([course("X", 2), course("Y", 1), course("A", 1, prereqs=[["X", "Y"]]), course("B", 5),
                       course("C", prereqs=[["A", "B"]])])
        res = self.plan(cat, programs([all_group("REQ", ["C"])]), student(num_terms=3))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"B": 0, "C": 1})


class OrTimingRegressionTest(EngineTestCase, unittest.TestCase):
    """Regression test for docs/debug_log.md BUG-001.

    The old resolve_or picked only the single cheapest alternative for each OR clause. If that
    alternative was late timing-wise, it would report infeasibility as if "proven" even though a
    different alternative would have made it feasible.
    """

    def test_cheaper_or_alternative_is_too_late(self):
        # T is (X | Y). X (2 credits) is cheaper, but being FALL-only + needing prereq P means
        # it isn't available until 2027 FALL -> too late.
        # Taking Y (FALL, no prereq) in 2026 FALL lets T be taken in 2027 SPRING.
        cat = catalog([course("P"), course("X", 2, offered=F, prereqs=[["P"]]), course("Y", 3, offered=F),
                       course("T", 1, offered=S, prereqs=[["X", "Y"]])])
        res = self.plan(cat, programs([all_group("REQ", ["P", "T"])]), student(num_terms=2))
        self.assertFeasible(res)
        self.assertEqual(res.candidates, 2)
        self.assertEqual(term_of(res.plan), {"P": 0, "Y": 0, "T": 1})

    def test_reused_or_alternative_is_too_late(self):
        # T is (A | B). B is already in the set at zero extra cost, but it's late since it comes after Q. Adding A makes it feasible.
        cat = catalog([course("Q"), course("B", prereqs=[["Q"]]), course("A"),
                       course("T", offered=S, prereqs=[["A", "B"]])])
        res = self.plan(cat, programs([all_group("REQ", ["B", "T"])]), student(num_terms=2))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"Q": 0, "A": 0, "B": 1, "T": 1})

    def test_or_in_coreq_is_also_expanded(self):
        # K is coreq with (L1 | L2). L1 is cheaper, but being SPRING-only means only L2 works for a 1-term FALL plan
        cat = catalog([course("L1", 1, offered=S), course("L2", 2), course("K", coreqs=[["L1", "L2"]])])
        res = self.plan(cat, programs([all_group("REQ", ["K"])]), student(num_terms=1))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"L2": 0, "K": 0})

    def test_or_expansion_overflow_falls_back_to_cheapest(self):
        # 2 OR-expanded candidates {B,C}, {A,B,C} > limit of 1 -> a single greedy candidate (same as the old lowest-cost selection)
        cat = catalog([course("A", 1), course("B", 3), course("C", prereqs=[["A", "B"]])])
        with mock.patch("engine.targets.MAX_CANDIDATES", 1):
            res = self.plan(cat, programs([all_group("REQ", ["B", "C"])]), student(num_terms=2))
        self.assertFeasible(res)
        self.assertTrue(res.used_fallback)
        self.assertEqual(res.candidates, 1)
        self.assertEqual(sorted(term_of(res.plan)), ["B", "C"])


if __name__ == "__main__":
    unittest.main()
