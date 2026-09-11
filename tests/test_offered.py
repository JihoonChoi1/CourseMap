"""Term-specific offerings: a one-year-apart chain, E_NOT_OFFERED, insufficient chain length."""

import unittest

from helpers import F, S, EngineTestCase, all_group, catalog, course, errors_with, programs, student, term_of


class OfferedTest(EngineTestCase, unittest.TestCase):
    def fall_chain(self, n=3):
        cs = [course("F1", offered=F)]
        for i in range(2, n + 1):
            cs.append(course("F" + str(i), offered=F, prereqs=[["F" + str(i - 1)]]))
        return catalog(cs)

    def test_fall_only_chain_is_one_year_apart(self):
        res = self.plan(self.fall_chain(), programs([all_group("G", ["F3"])]), student(num_terms=8))
        self.assertFeasible(res)
        t = term_of(res.plan)
        self.assertEqual([t["F1"], t["F2"], t["F3"]], [0, 2, 4])
        for cid in ("F1", "F2", "F3"):
            self.assertEqual(res.plan["terms"][t[cid]]["season"], "FALL")
        self.assertEqual([term["year"] for term in res.plan["terms"]], [2026, 2027, 2027, 2028, 2028])

    def test_fall_only_chain_starting_in_spring(self):
        res = self.plan(self.fall_chain(2), programs([all_group("G", ["F2"])]),
                        student(num_terms=8, start=(2027, "SPRING")))
        self.assertFeasible(res)
        t = term_of(res.plan)
        self.assertEqual([t["F1"], t["F2"]], [1, 3])
        self.assertEqual(res.plan["terms"][0]["courses"], [])
        self.assertEqual(res.plan["terms"][0]["credits"], 0)

    def test_fall_then_spring_is_one_term_apart(self):
        cat = catalog([course("A", offered=F), course("B", offered=S, prereqs=[["A"]])])
        res = self.plan(cat, programs([all_group("G", ["B"])]), student(num_terms=2))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"A": 0, "B": 1})

    def test_not_offered_in_remaining_terms(self):
        cat = catalog([course("A", offered=F), course("B", offered=S)])
        res = self.plan(cat, programs([all_group("G", ["A", "B"])]), student(num_terms=1, start=(2028, "SPRING")))
        self.assertInfeasible(res, "E_NOT_OFFERED")
        errs = res.plan["errors"]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["courses"], ["A"])
        self.assertIn("2028 SPRING", errs[0]["message"])

    def test_not_offered_resolved_by_one_more_term(self):
        cat = catalog([course("A", offered=F), course("B", offered=S)])
        res = self.plan(cat, programs([all_group("G", ["A", "B"])]), student(num_terms=2, start=(2028, "SPRING")))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"B": 0, "A": 1})

    def test_non_target_unoffered_course_is_ignored(self):
        cat = catalog([course("A"), course("B", offered=F)])
        res = self.plan(cat, programs([all_group("G", ["A"])]), student(num_terms=1, start=(2028, "SPRING")))
        self.assertFeasible(res)

    def test_chain_too_long_reports_root_only(self):
        # F1(0) -> F2(2) -> F3(4). With 2 terms, it's already infeasible starting at F2; the downstream F3 isn't reported separately
        res = self.plan(self.fall_chain(), programs([all_group("G", ["F3"])]), student(num_terms=2))
        self.assertInfeasible(res, "E_INFEASIBLE")
        errs = res.plan["errors"]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["courses"], ["F2"])
        self.assertIn("최단 체인", errs[0]["message"])
        self.assertIn("F1", errs[0]["message"])
        self.assertNotIn("E_NOT_OFFERED", [e["code"] for e in errs])

    def test_chain_too_long_by_one_step(self):
        res = self.plan(self.fall_chain(), programs([all_group("G", ["F3"])]), student(num_terms=4))
        self.assertInfeasible(res, "E_INFEASIBLE")
        self.assertEqual(res.plan["errors"][0]["courses"], ["F3"])
        self.assertIn("F1(2026 FALL) → F2(2027 FALL) → F3(2028 FALL)", res.plan["errors"][0]["message"])
        self.assertFeasible(self.plan(self.fall_chain(), programs([all_group("G", ["F3"])]), student(num_terms=5)))

    def test_restricted_course_takes_its_only_slot_under_tight_cap(self):
        # cap 3, 2 terms (F, S): FALL-only A must occupy the FALL term
        cat = catalog([course("B"), course("A", offered=F)])
        res = self.plan(cat, programs([all_group("G", ["A", "B"])]), student(num_terms=2, cap=3))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"A": 0, "B": 1})

    def test_not_offered_blocks_before_other_prechecks(self):
        # when E_NOT_OFFERED is present, chain/credit checks are skipped (avoids spilling downstream errors)
        cat = catalog([course("A", offered=F), course("B", offered=S, prereqs=[["A"]])])
        res = self.plan(cat, programs([all_group("G", ["B"])]), student(num_terms=1, start=(2028, "SPRING")))
        self.assertEqual([e["code"] for e in res.plan["errors"]], ["E_NOT_OFFERED"])
        self.assertEqual(len(errors_with(res.plan["errors"], "E_INFEASIBLE")), 0)


if __name__ == "__main__":
    unittest.main()
