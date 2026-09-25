"""Phase 6 real-data (data/ubc: 24 UBC CPSC courses + 6 MATH/STAT courses) tests.

1) Data: passes shape/static validation, transcription record (SOURCES.md) matches the course list
2) Current results for scenarios U1-U6
3) A 768-cell grid (6 students × cap 3-18 × terms 1-8) checked against the independent oracle (oracle_free)
   - What the spec guarantees: verify passes, a "feasible" verdict is always correct, and any
     infeasibility/minimum-term claim marked as proven is always correct
   - What the spec does not guarantee (the fallback path): the counts of missed cases and
     non-minimal cases are pinned down as-is.
     With real data, PICK_N combinations exceed MAX_CANDIDATES and mostly fall back
     (docs/debug_log.md ISSUE-006). If the engine is fixed and these numbers change, update both
     the test and debug_log together (NOTE-003: a known limitation asserts current behavior).
"""

import copy
import os
import re
import unittest

from helpers import ROOT
from engine.loader import load_all
from engine.planner import plan_student
from engine.validate import validate
from engine.verify import verify_plan
from oracle_free import find_plan_free

UBC_DIR = os.path.join(ROOT, "data", "ubc")
CAPS = range(3, 19)
TERMS = range(1, 9)


def groups_of(progs, s):
    return progs.degree.groups + [t for t in progs.tracks if t.id == s.track][0].groups


def unproven(plan) -> bool:
    return any("증명된 것은 아님" in e["message"] for e in plan["errors"])


class UbcDataTest(unittest.TestCase):
    def test_loads_and_validates(self):
        cat, progs, students = load_all(UBC_DIR)
        self.assertEqual(cat.seasons, ["W1", "W2"])
        self.assertEqual(len(cat.courses), 30)
        self.assertEqual(sum(1 for c in cat.courses if c.id.startswith("CPSC ")), 24)
        static = validate(cat, progs)
        self.assertEqual(static.errors, [])
        self.assertEqual(static.bundles, [])
        self.assertEqual([t.id for t in progs.tracks], ["AI_ML", "SYSTEMS", "SOFTWARE"])
        self.assertEqual([s.id for s in students], ["U1", "U2", "U3", "U4", "U5", "U6"])

    def test_every_course_is_documented_in_sources(self):
        cat, _, _ = load_all(UBC_DIR)
        with open(os.path.join(UBC_DIR, "SOURCES.md"), encoding="utf-8") as f:
            text = f.read()
        documented = re.findall(r"^### ((?:CPSC|MATH|STAT) \d{3})\b", text, re.M)
        self.assertEqual(documented, [c.id for c in cat.courses])


class UbcScenarioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat, cls.progs, students = load_all(UBC_DIR)
        cls.static = validate(cls.cat, cls.progs)
        cls.students = {s.id: s for s in students}

    def run_student(self, sid, **override):
        s = copy.copy(self.students[sid])
        for k, v in override.items():
            setattr(s, k, v)
        res = plan_student(self.cat, self.progs, self.static, s)
        if res.plan["feasible"]:
            self.assertEqual(verify_plan(self.cat, self.progs, s, res.plan), [])
        return s, res

    def oracle_terms(self, s):
        for n in TERMS:
            if find_plan_free(self.cat, groups_of(self.progs, s), s.completed, s.start_season, n, s.max_credits):
                return n
        return 0

    def test_u1_freshman_fallback_is_one_term_longer_than_optimal(self):
        s, res = self.run_student("U1")
        self.assertTrue(res.plan["feasible"])
        self.assertTrue(res.used_fallback)
        self.assertEqual(res.candidates, 1)
        self.assertFalse(res.min_terms_proven)
        self.assertEqual(len(res.plan["terms"]), 6)
        self.assertEqual(self.oracle_terms(s), 5)  # known limitation (ISSUE-006)

    def test_u2_fallback_is_one_term_longer_than_optimal(self):
        s, res = self.run_student("U2")
        self.assertTrue(res.used_fallback)
        self.assertEqual(len(res.plan["terms"]), 5)
        self.assertEqual(self.oracle_terms(s), 4)  # known limitation (ISSUE-006)

    def test_u3_math111_student_still_needs_math221_for_cpsc340(self):
        s, res = self.run_student("U3")
        self.assertTrue(res.plan["feasible"])
        self.assertEqual(len(res.plan["terms"]), 3)
        self.assertEqual(self.oracle_terms(s), 3)
        placed = [c for t in res.plan["terms"] for c in t["courses"]]
        self.assertIn("MATH 221", placed)  # MATH 111 isn't in CPSC 340's prerequisite list (credit exclusion is ignored)
        self.assertFalse(res.min_terms_proven)  # the result is minimal, but it can't be proven since it's a fallback

    def test_u4_part_time_is_infeasible_but_not_proven(self):
        s, res = self.run_student("U4")
        self.assertFalse(res.plan["feasible"])
        self.assertTrue(res.used_fallback)
        self.assertTrue(unproven(res.plan))
        self.assertEqual(self.oracle_terms(s), 5)  # actually infeasible with 4 terms, feasible with 5
        s5, res5 = self.run_student("U4", num_terms=5)
        self.assertFalse(res5.plan["feasible"])  # the fallback combination still can't find it even at 5 terms (known limitation, ISSUE-006)
        self.assertTrue(unproven(res5.plan))

    def test_u5_math101_is_winter2_only(self):
        _, res = self.run_student("U5")
        self.assertFalse(res.plan["feasible"])
        self.assertEqual([e["code"] for e in res.plan["errors"]], ["E_NOT_OFFERED"])
        self.assertEqual(res.plan["errors"][0]["courses"], ["MATH 101"])
        _, res3 = self.run_student("U5", num_terms=3)
        self.assertTrue(res3.plan["feasible"])
        self.assertTrue(res3.min_terms_proven)
        self.assertEqual([t["courses"] for t in res3.plan["terms"]], [[], ["MATH 101"], ["MATH 200", "STAT 251"]])
        self.assertEqual([t["year"] for t in res3.plan["terms"]], [2029, 2029, 2030])
        self.assertEqual([t["season"] for t in res3.plan["terms"]], ["W1", "W2", "W1"])

    def test_u6_starts_in_winter2(self):
        s, res = self.run_student("U6")
        self.assertTrue(res.plan["feasible"])
        self.assertEqual(res.plan["terms"][0]["season"], "W2")
        self.assertEqual(len(res.plan["terms"]), self.oracle_terms(s))


class UbcGridTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat, cls.progs, students = load_all(UBC_DIR)
        cls.static = validate(cls.cat, cls.progs)
        cls.cases = []  # (student, cap, num_terms, PlanResult, oracle minimum terms (0 = infeasible within 8 terms))
        cls.witness_violations = []
        for s in students:
            groups = groups_of(cls.progs, s)
            for cap in CAPS:
                o_min = 0
                for n in TERMS:
                    terms = find_plan_free(cls.cat, groups, s.completed, s.start_season, n, cap)
                    if terms is not False:
                        o_min = n
                        cls.check_witness(s, cap, n, terms)
                        break
                for n in TERMS:
                    st = copy.copy(s)
                    st.max_credits = cap
                    st.num_terms = n
                    cls.cases.append((st, cap, n, plan_student(cls.cat, cls.progs, cls.static, st), o_min))

    @classmethod
    def check_witness(cls, s, cap, n, terms):
        """The placement the oracle found (as a witness) must also pass verify_plan — evidence for the oracle's own side."""
        st = copy.copy(s)
        st.max_credits = cap
        st.num_terms = n
        p = len(cls.cat.seasons)
        s0 = cls.cat.seasons.index(s.start_season)
        taken = set(s.completed)
        out = []
        for t, ids in enumerate(terms):
            out.append({"year": s.start_year + (s0 + t) // p, "season": cls.cat.seasons[(s0 + t) % p], "courses": ids,
                        "credits": sum(cls.cat.by_id[c].credits for c in ids)})
            taken.update(ids)
        chosen = {}
        for g in groups_of(cls.progs, s):
            if g.rule == "PICK_N":
                chosen[g.id] = [c for c in g.courses if c in taken]
        plan = {"student_id": s.id, "feasible": True, "terms": out, "chosen": chosen, "errors": []}
        v = verify_plan(cls.cat, cls.progs, st, plan)
        if v:
            cls.witness_violations.append((s.id, cap, n, v))

    def label(self, st, cap, n):
        return st.id + " cap=" + str(cap) + " num_terms=" + str(n)

    def test_grid_size_and_counts(self):
        self.assertEqual(len(self.cases), 768)
        self.assertEqual(sum(1 for c in self.cases if c[3].plan["feasible"]), 373)
        self.assertEqual(sum(1 for c in self.cases if c[4] and c[2] >= c[4]), 441)  # oracle feasible

    def test_oracle_witnesses_pass_verify(self):
        self.assertEqual(self.witness_violations, [])

    def test_feasible_plans_pass_verify(self):
        for st, cap, n, res, _ in self.cases:
            if res.plan["feasible"]:
                with self.subTest(case=self.label(st, cap, n)):
                    self.assertEqual(verify_plan(self.cat, self.progs, st, res.plan), [])

    def test_feasible_is_never_wrong(self):
        for st, cap, n, res, o_min in self.cases:
            if res.plan["feasible"]:
                with self.subTest(case=self.label(st, cap, n)):
                    self.assertTrue(o_min != 0 and n >= o_min)

    def test_proven_claims_are_correct(self):
        # what the spec guarantees: if infeasible is claimed without a "not proven" caveat, it really is infeasible; if min_terms_proven, it really is minimal
        for st, cap, n, res, o_min in self.cases:
            with self.subTest(case=self.label(st, cap, n)):
                if not res.plan["feasible"] and not unproven(res.plan):
                    self.assertFalse(o_min != 0 and n >= o_min)
                if res.plan["feasible"] and res.min_terms_proven:
                    self.assertEqual(len(res.plan["terms"]), o_min)

    def test_no_fallback_means_exact(self):
        for st, cap, n, res, o_min in self.cases:
            if not res.used_fallback:
                with self.subTest(case=self.label(st, cap, n)):
                    self.assertEqual(res.plan["feasible"], o_min != 0 and n >= o_min)
                    if res.plan["feasible"]:
                        self.assertTrue(res.min_terms_proven)
                        self.assertEqual(len(res.plan["terms"]), o_min)

    def test_known_fallback_gap_counts(self):
        # current behavior of the fallback path (ISSUE-006). A spec-allowed exception, always flagged in the result.
        fallback = [c for c in self.cases if c[3].used_fallback]
        missed = [c for c in self.cases if c[4] and c[2] >= c[4] and not c[3].plan["feasible"]]
        longer = [c for c in self.cases if c[3].plan["feasible"] and len(c[3].plan["terms"]) > c[4]]
        self.assertEqual(len(fallback), 640)
        self.assertEqual(len(missed), 68)
        self.assertEqual(len(longer), 215)
        for st, cap, n, res, _ in missed:
            with self.subTest(case=self.label(st, cap, n)):
                self.assertTrue(res.used_fallback)
                self.assertTrue(unproven(res.plan))  # every missed case is flagged "not proven"
        for st, cap, n, res, _ in longer:
            with self.subTest(case=self.label(st, cap, n)):
                self.assertTrue(res.used_fallback)
                self.assertFalse(res.min_terms_proven)  # every non-minimal case is flagged "not guaranteed"


if __name__ == "__main__":
    unittest.main()
