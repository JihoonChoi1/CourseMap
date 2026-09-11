"""§5 static rules, §4.4 cycles/bundles, runtime reference errors."""

import unittest

from helpers import (EngineTestCase, all_group, catalog, codes, course, cycle_path, errors_with, pick_group,
                     programs, student)
from engine.planner import plan_student
from engine.validate import validate


def check(courses, groups=None, tracks=None):
    return validate(catalog(courses), programs(groups or [], tracks))


class StaticRulesTest(unittest.TestCase):
    def test_clean_catalog_has_no_errors(self):
        res = check([course("A"), course("B", prereqs=[["A"]]), course("C", coreqs=[["A", "B"]])],
                    [all_group("G", ["C"]), pick_group("P", 1, ["A", "B"])])
        self.assertEqual(res.errors, [])
        self.assertEqual(res.bundles, [])

    def test_dup_course_id(self):
        res = check([course("A"), course("A", credits=4)])
        self.assertEqual(codes(res.errors), ["E_DUP_ID"])
        self.assertEqual(res.errors[0].courses, ["A"])

    def test_dup_group_id_across_degree_and_track(self):
        res = check([course("A")], [all_group("G", ["A"])], {"T": [pick_group("G", 1, ["A"])]})
        self.assertEqual(codes(res.errors), ["E_DUP_ID"])

    def test_dup_track_id(self):
        res = check([course("A")], [], {"T": [], "U": []})
        self.assertEqual(res.errors, [])
        progs = programs([], {"T": []})
        progs.tracks.append(progs.tracks[0])
        self.assertEqual(codes(validate(catalog([course("A")]), progs).errors), ["E_DUP_ID"])

    def test_track_id_equal_to_degree_id(self):
        res = check([course("A")], [], {"DEG": []})
        self.assertEqual(codes(res.errors), ["E_DUP_ID"])

    def test_unknown_ref_in_prereq_coreq_and_group(self):
        res = check([course("A", prereqs=[["NOPE1"]]), course("B", coreqs=[["A", "NOPE2"]])],
                    [all_group("G", ["A", "NOPE3"])])
        self.assertEqual(codes(res.errors), ["E_UNKNOWN_REF"] * 3)
        messages = " ".join(e.message for e in res.errors)
        for name in ("NOPE1", "NOPE2", "NOPE3"):
            self.assertIn(name, messages)

    def test_self_ref(self):
        res = check([course("A", prereqs=[["A"]]), course("B", coreqs=[["B"]]), course("C", prereqs=[["A", "C"]])])
        self.assertEqual(codes(res.errors), ["E_SELF_REF"] * 3)
        self.assertEqual([e.courses for e in res.errors], [["A"], ["B"], ["C"]])

    def test_bad_credits(self):
        res = check([course("A", credits=0), course("B", credits=-3), course("C", credits=1)])
        self.assertEqual(codes(res.errors), ["E_BAD_CREDITS"] * 2)
        self.assertEqual([e.courses for e in res.errors], [["A"], ["B"]])

    def test_bad_offered(self):
        res = check([course("A", offered=[]), course("B", offered=["SUMMER"]), course("C", offered=["FALL", "WINTER"])])
        self.assertEqual(codes(res.errors), ["E_BAD_OFFERED"] * 3)
        self.assertEqual([e.courses for e in res.errors], [["A"], ["B"], ["C"]])

    def test_empty_clause(self):
        res = check([course("A", prereqs=[[]]), course("B", coreqs=[["A"], []])])
        self.assertEqual(codes(res.errors), ["E_EMPTY_CLAUSE"] * 2)

    def test_bad_pick_n(self):
        cs = [course("A"), course("B")]
        self.assertEqual(codes(check(cs, [pick_group("P", 0, ["A", "B"])]).errors), ["E_BAD_PICK_N"])
        self.assertEqual(codes(check(cs, [pick_group("P", 3, ["A", "B"])]).errors), ["E_BAD_PICK_N"])
        self.assertEqual(codes(check(cs, [pick_group("P", -1, ["A", "B"])]).errors), ["E_BAD_PICK_N"])
        self.assertEqual(check(cs, [pick_group("P", 2, ["A", "B"])]).errors, [])

    def test_all_group_ignores_n(self):
        self.assertEqual(check([course("A")], [all_group("G", ["A"])]).errors, [])

    def test_unknown_rule(self):
        g = pick_group("P", 1, ["A"])
        g.rule = "ANY"
        self.assertEqual(codes(check([course("A")], [g]).errors), ["E_UNKNOWN_RULE"])

    def test_errors_are_accumulated_not_first_only(self):
        res = check([course("A", credits=0, offered=[], prereqs=[[]]), course("A")])
        self.assertEqual(sorted(codes(res.errors)), ["E_BAD_CREDITS", "E_BAD_OFFERED", "E_DUP_ID", "E_EMPTY_CLAUSE"])


def _is_edge(cat, x, c):
    """Does x appear in c's requirement expression (prereq or coreq) = edge x -> c."""
    for clauses in (cat.by_id[c].prereqs, cat.by_id[c].coreqs):
        for clause in clauses:
            if x in clause:
                return True
    return False


class CycleTest(unittest.TestCase):
    def assertCycle(self, cat, err, members, strict_pair):
        self.assertEqual(err.code, "E_CYCLE")
        self.assertEqual(sorted(err.courses), sorted(members))
        path = cycle_path(err.message)
        self.assertEqual(path[0], path[-1], "path must be closed")
        for i in range(len(path) - 1):
            self.assertTrue(_is_edge(cat, path[i], path[i + 1]), path[i] + " -> " + path[i + 1] + " has no edge")
            self.assertIn(path[i], members)
        # the strict edge that grounds the contradiction must be the first segment of the path
        self.assertEqual((path[0], path[1]), strict_pair)
        self.assertTrue(any(strict_pair[0] in cl for cl in cat.by_id[strict_pair[1]].prereqs))

    def test_strict_only_cycle_X(self):
        cat = catalog([course("X1", prereqs=[["X2"]]), course("X2", prereqs=[["X3"]]), course("X3", prereqs=[["X1"]]),
                       course("W", prereqs=[["X1"]])])
        res = validate(cat, programs([]))
        self.assertEqual(codes(res.errors), ["E_CYCLE"])
        err = res.errors[0]
        path = cycle_path(err.message)
        self.assertEqual(len(path), 4)
        self.assertEqual(set(path), {"X1", "X2", "X3"})
        self.assertCycle(cat, err, ["X1", "X2", "X3"], (path[0], path[1]))
        self.assertNotIn("W", err.courses)

    def test_weak_plus_strict_cycle_Y(self):
        cat = catalog([course("Y1", coreqs=[["Y2"]]), course("Y2", prereqs=[["Y1"]])])
        res = validate(cat, programs([]))
        self.assertEqual(codes(res.errors), ["E_CYCLE"])
        self.assertCycle(cat, res.errors[0], ["Y1", "Y2"], ("Y1", "Y2"))
        self.assertEqual(cycle_path(res.errors[0].message), ["Y1", "Y2", "Y1"])

    def test_or_clause_cycle_is_conservative_error(self):
        # §4.3: going through C is actually feasible, but it's conservatively flagged as an error
        cat = catalog([course("A", prereqs=[["B", "C"]]), course("B", prereqs=[["A"]]), course("C")])
        res = validate(cat, programs([]))
        self.assertEqual(codes(res.errors), ["E_CYCLE"])
        err = res.errors[0]
        path = cycle_path(err.message)
        self.assertCycle(cat, err, ["A", "B"], (path[0], path[1]))
        self.assertNotIn("C", err.courses)

    def test_mutual_coreq_is_bundle_Z(self):
        res = validate(catalog([course("Z1", coreqs=[["Z2"]]), course("Z2", coreqs=[["Z1"]])]), programs([]))
        self.assertEqual(res.errors, [])
        self.assertEqual(res.bundles, [["Z1", "Z2"]])
        self.assertEqual(res.bundle_of, {"Z1": 0, "Z2": 0})

    def test_coreq_ring_of_three_is_one_bundle(self):
        res = validate(catalog([course("Z3", coreqs=[["Z1"]]), course("Z1", coreqs=[["Z2"]]),
                                course("Z2", coreqs=[["Z3"]])]), programs([]))
        self.assertEqual(res.errors, [])
        self.assertEqual(res.bundles, [["Z3", "Z1", "Z2"]])  # catalog order

    def test_bundle_with_internal_prereq_is_cycle(self):
        cat = catalog([course("Z1", coreqs=[["Z2"]]), course("Z2", coreqs=[["Z1"]], prereqs=[["Z1"]])])
        res = validate(cat, programs([]))
        self.assertEqual(codes(res.errors), ["E_CYCLE"])
        self.assertCycle(cat, res.errors[0], ["Z1", "Z2"], ("Z1", "Z2"))
        self.assertEqual(res.bundles, [])

    def test_two_independent_cycles_reported_separately(self):
        cat = catalog([course("A", prereqs=[["B"]]), course("B", prereqs=[["A"]]),
                       course("C", prereqs=[["D"]]), course("D", coreqs=[["C"]])])
        res = validate(cat, programs([]))
        self.assertEqual(codes(res.errors), ["E_CYCLE", "E_CYCLE"])
        self.assertEqual(sorted(sorted(e.courses) for e in res.errors), [["A", "B"], ["C", "D"]])

    def test_self_ref_is_not_also_reported_as_cycle(self):
        res = validate(catalog([course("A", prereqs=[["A"]])]), programs([]))
        self.assertEqual(codes(res.errors), ["E_SELF_REF"])

    def test_static_errors_make_plan_infeasible(self):
        cat = catalog([course("X1", prereqs=[["X2"]]), course("X2", prereqs=[["X1"]]), course("OK")])
        progs = programs([all_group("G", ["OK"])])
        static = validate(cat, progs)
        plan = plan_student(cat, progs, static, student()).plan
        self.assertFalse(plan["feasible"])
        self.assertEqual(plan["terms"], [])
        self.assertEqual(plan["chosen"], {})
        self.assertEqual(codes(plan["errors"]), ["E_CYCLE"])
        self.assertEqual(sorted(plan["errors"][0]["courses"]), ["X1", "X2"])


class RuntimeRefTest(EngineTestCase, unittest.TestCase):
    def test_unknown_track(self):
        cat = catalog([course("A")])
        progs = programs([all_group("G", ["A"])])
        res = self.plan(cat, progs, student(track="NOPE"))
        self.assertEqual(codes(res.plan["errors"]), ["E_UNKNOWN_TRACK"])
        self.assertIn("NOPE", res.plan["errors"][0]["message"])

    def test_unknown_completed_course(self):
        cat = catalog([course("A")])
        progs = programs([all_group("G", ["A"])])
        res = self.plan(cat, progs, student(completed=["A", "GHOST"]))
        errs = errors_with(res.plan["errors"], "E_UNKNOWN_REF")
        self.assertEqual(len(res.plan["errors"]), 1)
        self.assertEqual(errs[0]["courses"], ["GHOST"])


if __name__ == "__main__":
    unittest.main()
