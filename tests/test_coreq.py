"""Corequisites: mutual-coreq bundles, one-way coreq, E_BUNDLE_NO_TERM, E_BUNDLE_OVER_CAP."""

import unittest

from helpers import (F, S, EngineTestCase, all_group, catalog, codes, course, programs, student, term_of)
from engine.validate import validate


def lab_pair(lec_offered=None, lab_offered=None, lec_credits=3, lab_credits=1):
    return [
        course("LEC", lec_credits, offered=lec_offered, coreqs=[["LAB"]]),
        course("LAB", lab_credits, offered=lab_offered, coreqs=[["LEC"]]),
    ]


class CoreqTest(EngineTestCase, unittest.TestCase):
    def test_mutual_coreq_bundle_same_term(self):
        cat = catalog(lab_pair() + [course("NEXT", prereqs=[["LEC"]])])
        res = self.plan(cat, programs([all_group("G", ["NEXT"])]), student(num_terms=3))
        self.assertFeasible(res)
        t = term_of(res.plan)
        self.assertEqual(t["LEC"], t["LAB"])
        self.assertLess(t["LEC"], t["NEXT"])

    def test_bundle_partner_pulled_in_by_closure(self):
        res = self.plan(catalog(lab_pair()), programs([all_group("G", ["LEC"])]), student(num_terms=1))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"LEC": 0, "LAB": 0})

    def test_bundle_fits_exactly_at_cap(self):
        res = self.plan(catalog(lab_pair()), programs([all_group("G", ["LEC"])]), student(num_terms=1, cap=4))
        self.assertFeasible(res)
        self.assertEqual(res.plan["terms"][0]["credits"], 4)

    def test_bundle_member_completed(self):
        res = self.plan(catalog(lab_pair()), programs([all_group("G", ["LEC", "LAB"])]),
                        student(num_terms=1, completed=["LAB"]))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"LEC": 0})

    def test_bundle_uses_offered_intersection(self):
        cat = catalog(lab_pair(lec_offered=["SPRING", "FALL"], lab_offered=F))
        res = self.plan(cat, programs([all_group("G", ["LEC"])]), student(num_terms=2, start=(2027, "SPRING")))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"LEC": 1, "LAB": 1})

    def test_bundle_of_three(self):
        cat = catalog([course("Z1", coreqs=[["Z2"]]), course("Z2", coreqs=[["Z3"]]), course("Z3", 1, coreqs=[["Z1"]])])
        res = self.plan(cat, programs([all_group("G", ["Z2"])]), student(num_terms=2, cap=7))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"Z1": 0, "Z2": 0, "Z3": 0})

    def test_one_way_coreq_same_term_allowed(self):
        cat = catalog([course("A"), course("B", coreqs=[["A"]])])
        res = self.plan(cat, programs([all_group("G", ["A", "B"])]), student(num_terms=1))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"A": 0, "B": 0})

    def test_one_way_coreq_earlier_term_allowed(self):
        cat = catalog([course("A"), course("B", coreqs=[["A"]])])
        res = self.plan(cat, programs([all_group("G", ["A", "B"])]), student(num_terms=2, cap=3))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"A": 0, "B": 1})

    def test_one_way_coreq_never_before_partner(self):
        # B is offered FALL only, A SPRING only: B can never come before A, so starting SPRING for 2 terms gives A(0), B(1)
        cat = catalog([course("A", offered=S), course("B", offered=F, coreqs=[["A"]])])
        res = self.plan(cat, programs([all_group("G", ["B"])]), student(num_terms=2, start=(2027, "SPRING")))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"A": 0, "B": 1})
        # starting FALL for 2 terms (F, S) would require B before A, so it's infeasible
        res = self.plan(cat, programs([all_group("G", ["B"])]), student(num_terms=2, start=(2026, "FALL")))
        self.assertInfeasible(res, "E_INFEASIBLE")

    def test_coreq_combined_with_prereq(self):
        # the MATH201 pattern: M201 has M101 as prereq + M102 as coreq, and M102 has M101 as prereq
        cat = catalog([course("M101"), course("M102", prereqs=[["M101"]]),
                       course("M201", prereqs=[["M101"]], coreqs=[["M102"]])])
        res = self.plan(cat, programs([all_group("G", ["M201"])]), student(num_terms=2))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"M101": 0, "M102": 1, "M201": 1})

    def test_or_coreq(self):
        cat = catalog([course("A", 1), course("B", 4), course("C", coreqs=[["A", "B"]])])
        res = self.plan(cat, programs([all_group("G", ["C"])]), student(num_terms=1))
        self.assertFeasible(res)
        self.assertEqual(term_of(res.plan), {"A": 0, "C": 0})  # the cheaper alternative

    def test_bundle_no_term(self):
        cat = catalog(lab_pair(lec_offered=S, lab_offered=F))
        res = validate(cat, programs([]))
        self.assertEqual(codes(res.errors), ["E_BUNDLE_NO_TERM"])
        self.assertEqual(res.errors[0].courses, ["LEC", "LAB"])
        self.assertEqual(res.bundles, [])

    def test_bundle_over_cap(self):
        cat = catalog(lab_pair(lab_credits=3))
        res = self.plan(cat, programs([all_group("G", ["LEC"])]), student(num_terms=4, cap=5))
        self.assertInfeasible(res, "E_BUNDLE_OVER_CAP")
        errs = res.plan["errors"]
        self.assertEqual(codes(errs), ["E_BUNDLE_OVER_CAP"])
        self.assertEqual(errs[0]["courses"], ["LEC", "LAB"])
        self.assertIn("6학점", errs[0]["message"])

    def test_bundle_under_cap_but_members_individually_fit(self):
        # each course is within the cap on its own, but if the sum exceeds it it's E_BUNDLE_OVER_CAP, not E_COURSE_OVER_CAP
        cat = catalog(lab_pair(lec_credits=3, lab_credits=3))
        res = self.plan(cat, programs([all_group("G", ["LEC", "LAB"])]), student(num_terms=4, cap=3))
        self.assertEqual(codes(res.plan["errors"]), ["E_BUNDLE_OVER_CAP"])


if __name__ == "__main__":
    unittest.main()
