"""Diamond dependencies: a shared ancestor placed only once, the joining course after every path."""

import unittest

from helpers import F, EngineTestCase, all_group, catalog, course, programs, student, term_of


def placements(plan, cid):
    n = 0
    for term in plan["terms"]:
        n += term["courses"].count(cid)
    return n


class DiamondTest(EngineTestCase, unittest.TestCase):
    def diamond(self, extra=None, bot_prereqs=None):
        cs = [
            course("TOP"),
            course("L", prereqs=[["TOP"]]),
            course("R", prereqs=[["TOP"]]),
            course("BOT", prereqs=bot_prereqs or [["L"], ["R"]]),
        ]
        return catalog(cs + (extra or []))

    def assertDiamondOrder(self, plan, top, sides, bot):
        t = term_of(plan)
        self.assertEqual(placements(plan, top), 1)
        for s in sides:
            self.assertEqual(placements(plan, s), 1)
            self.assertLess(t[top], t[s])
            self.assertLess(t[s], t[bot])

    def test_basic_diamond_only_bottom_is_target(self):
        # BOT alone is the target. TOP/L/R must be pulled in via prerequisite closure.
        res = self.plan(self.diamond(), programs([all_group("G", ["BOT"])]), student(num_terms=4))
        self.assertFeasible(res)
        self.assertDiamondOrder(res.plan, "TOP", ["L", "R"], "BOT")
        self.assertEqual(sorted(term_of(res.plan)), ["BOT", "L", "R", "TOP"])
        self.assertEqual(len(res.plan["terms"]), 3)

    def test_diamond_with_cap_splitting_branches(self):
        # with a cap of 3, even if L and R split into different terms, BOT still comes after both
        res = self.plan(self.diamond(), programs([all_group("G", ["BOT"])]), student(num_terms=4, cap=3))
        self.assertFeasible(res)
        self.assertDiamondOrder(res.plan, "TOP", ["L", "R"], "BOT")
        t = term_of(res.plan)
        self.assertNotEqual(t["L"], t["R"])
        self.assertEqual(t["BOT"], 3)

    def test_unbalanced_diamond_waits_for_longer_branch(self):
        cs = [
            course("TOP"),
            course("L1", prereqs=[["TOP"]]),
            course("L2", prereqs=[["L1"]]),
            course("R", prereqs=[["TOP"]]),
            course("BOT", prereqs=[["L2"], ["R"]]),
        ]
        res = self.plan(catalog(cs), programs([all_group("G", ["BOT"])]), student(num_terms=5))
        self.assertFeasible(res)
        t = term_of(res.plan)
        self.assertDiamondOrder(res.plan, "TOP", ["L1", "R"], "BOT")
        self.assertLess(t["L2"], t["BOT"])
        self.assertEqual(t["BOT"], 3)

    def test_diamond_branch_with_offered_constraint(self):
        # a miniature CS102 -> {CS201, CS210(F)} -> CS310(F): one branch is FALL-only
        cs = [
            course("TOP"),
            course("L", prereqs=[["TOP"]]),
            course("R", offered=F, prereqs=[["TOP"]]),
            course("BOT", offered=F, prereqs=[["L"], ["R"]]),
        ]
        res = self.plan(catalog(cs), programs([all_group("G", ["BOT"])]), student(num_terms=6))
        self.assertFeasible(res)
        self.assertDiamondOrder(res.plan, "TOP", ["L", "R"], "BOT")
        t = term_of(res.plan)
        # starting 2026 FALL: TOP 0(F), R 2(F), BOT 4(F)
        self.assertEqual([t["TOP"], t["R"], t["BOT"]], [0, 2, 4])

    def test_redundant_transitive_prereq(self):
        # §6.4 redundant prereq: BOT lists both L and L's prerequisite TOP explicitly
        res = self.plan(self.diamond(bot_prereqs=[["L"], ["R"], ["TOP"]]), programs([all_group("G", ["BOT"])]),
                        student(num_terms=4))
        self.assertFeasible(res)
        self.assertDiamondOrder(res.plan, "TOP", ["L", "R"], "BOT")

    def test_diamond_with_completed_top(self):
        res = self.plan(self.diamond(), programs([all_group("G", ["BOT"])]), student(num_terms=3, completed=["TOP"]))
        self.assertFeasible(res)
        t = term_of(res.plan)
        self.assertNotIn("TOP", t)
        self.assertEqual(t["L"], 0)
        self.assertEqual(t["R"], 0)
        self.assertEqual(t["BOT"], 1)

    def test_double_diamond_shared_middle(self):
        # TOP -> {A, B} -> MID -> {C, D} -> BOT
        cs = [
            course("TOP"),
            course("A", prereqs=[["TOP"]]), course("B", prereqs=[["TOP"]]),
            course("MID", prereqs=[["A"], ["B"]]),
            course("C", prereqs=[["MID"]]), course("D", prereqs=[["MID"]]),
            course("BOT", prereqs=[["C"], ["D"]]),
        ]
        res = self.plan(catalog(cs), programs([all_group("G", ["BOT", "A"])]), student(num_terms=6, cap=6))
        self.assertFeasible(res)
        self.assertDiamondOrder(res.plan, "TOP", ["A", "B"], "MID")
        self.assertDiamondOrder(res.plan, "MID", ["C", "D"], "BOT")
        self.assertEqual(len(res.plan["terms"]), 5)


if __name__ == "__main__":
    unittest.main()
