"""Seed-fixed random-catalog property tests (checked against the oracle).

- The engine's feasible/infeasible verdict = the oracle's verdict (a feasible one is always found; an
  infeasible one is reported without a "not proven" caveat)
- If the engine says feasible, verify_plan passes, and the term count equals the oracle's minimum
- For a single target set: if the oracle says feasible, schedule() must also find it (precheck
  soundness + exhaustive-search completeness)
- Candidate generation is complete: if the oracle says feasible, one of the engine's candidates
  must be a feasible course set (regression for docs/debug_log.md BUG-001)
- No crashes
"""

import random
import unittest

from helpers import F, S, SF, all_group, catalog, course, pick_group, programs, student
from engine.graph import build_unit_graph, compute_es
from engine.model import sort_by_catalog
from engine.planner import make_calendar, plan_student
from engine.scheduler import schedule
from engine.targets import generate_candidates
from engine.validate import validate
from engine.verify import verify_plan
from oracle import exists_plan, set_feasible, target_sets

SEED = 20260927
INSTANCES = 1500


def random_instance(rng):
    n = rng.randint(3, 9)
    ids = ["C" + str(i) for i in range(n)]
    cs = []
    for i, cid in enumerate(ids):
        offered = rng.choice([SF, SF, SF, S, F])
        prereqs = []
        coreqs = []
        earlier = ids[:i]  # only references earlier courses -> mostly a DAG (bundles handled separately below)
        if earlier:
            for _ in range(rng.choice([0, 0, 1, 1, 2])):
                prereqs.append(rng.sample(earlier, min(rng.choice([1, 1, 1, 2]), len(earlier))))
            if rng.random() < 0.2:
                coreqs.append(rng.sample(earlier, 1))
        cs.append(course(cid, rng.choice([1, 2, 3, 3, 3, 4]), offered, prereqs, coreqs))
    if rng.random() < 0.15:
        a, b = cs[-2], cs[-1]
        if not any(a.id in cl for cl in b.prereqs):
            a.coreqs.append([b.id])
            b.coreqs.append([a.id])
    groups = [all_group("REQ", rng.sample(ids, rng.randint(1, min(3, n))))]
    if rng.random() < 0.7:
        pool = rng.sample(ids, rng.randint(2, min(4, n)))
        groups.append(pick_group("P1", rng.randint(1, len(pool)), pool))
    track = []
    if rng.random() < 0.5:
        pool = rng.sample(ids, rng.randint(1, min(3, n)))
        track.append(pick_group("P2", rng.randint(1, len(pool)), pool))
    stu = student(num_terms=rng.randint(1, 5), cap=rng.randint(2, 9), completed=rng.sample(ids, rng.randint(0, n // 3)),
                  start=(2026, rng.choice(["SPRING", "FALL"])))
    return catalog(cs), programs(groups, {"T": track}), stu


class FuzzTest(unittest.TestCase):
    def test_random_instances_against_oracle(self):
        rng = random.Random(SEED)
        checked = 0
        feasible = 0
        for i in range(INSTANCES):
            cat, progs, stu = random_instance(rng)
            static = validate(cat, progs)
            if len(static.errors) > 0:
                continue
            checked += 1
            groups = progs.degree.groups + progs.tracks[0].groups
            res = plan_student(cat, progs, static, stu)
            exists = exists_plan(cat, groups, stu.completed, stu.start_season, stu.num_terms, stu.max_credits)
            with self.subTest(instance=i):
                self.assertEqual(res.plan["feasible"], exists)
                if res.plan["feasible"]:
                    feasible += 1
                    self.assertEqual(verify_plan(cat, progs, stu, res.plan), [])
                    # minimum-term guarantee: the oracle also says infeasible with one fewer term
                    used = len(res.plan["terms"])
                    self.assertTrue(res.min_terms_proven)
                    if used > 1:
                        self.assertFalse(exists_plan(cat, groups, stu.completed, stu.start_season, used - 1,
                                                     stu.max_credits))
                else:
                    for e in res.plan["errors"]:
                        self.assertNotIn("증명된 것은 아님", e["message"])
        # check the generator produces a meaningful distribution (the test would be vacuous if everything were a static error/infeasible)
        self.assertGreater(checked, INSTANCES * 0.9)
        self.assertGreater(feasible, checked * 0.3)

    def test_schedule_always_finds_feasible_target_set(self):
        rng = random.Random(SEED + 1)
        proofs_checked = 0
        for i in range(INSTANCES):
            cat, progs, stu = random_instance(rng)
            static = validate(cat, progs)
            if len(static.errors) > 0:
                continue
            completed = {}
            for cid in stu.completed:
                completed[cid] = True
            cal = make_calendar(cat, stu)
            groups = progs.degree.groups + progs.tracks[0].groups
            for s in target_sets(cat, groups, set(stu.completed)):
                if not set_feasible(cat, s, set(stu.completed), stu.start_season, stu.num_terms, stu.max_credits):
                    continue
                proofs_checked += 1
                r = schedule(cat, cal, stu.max_credits, completed, sort_by_catalog(cat, list(s)), static)
                with self.subTest(instance=i, target=sorted(s)):
                    self.assertTrue(r.feasible, repr([e.message for e in r.errors]))
        self.assertGreater(proofs_checked, 500)

    def test_candidates_cover_a_feasible_target_set(self):
        rng = random.Random(SEED + 2)
        covered = 0
        for i in range(INSTANCES):
            cat, progs, stu = random_instance(rng)
            static = validate(cat, progs)
            if len(static.errors) > 0:
                continue
            groups = progs.degree.groups + progs.tracks[0].groups
            completed = {}
            for cid in stu.completed:
                completed[cid] = True
            feasible_sets = {}
            for s in target_sets(cat, groups, set(stu.completed)):
                if set_feasible(cat, s, set(stu.completed), stu.start_season, stu.num_terms, stu.max_credits):
                    feasible_sets[",".join(sort_by_catalog(cat, list(s)))] = True
            if len(feasible_sets) == 0:
                continue
            # generate candidates the same way planner.plan_student does
            cal = make_calendar(cat, stu)
            rest = [c.id for c in cat.courses if c.id not in completed]
            g = build_unit_graph(cat, rest, completed, static.bundles, static.bundle_of)
            es, _ = compute_es(g, cat, cal, completed)
            es_global = {}
            for cid in rest:
                es_global[cid] = es[g.unit_of[cid]]
            candidates, used_fallback = generate_candidates(cat, groups, completed, es_global)
            if used_fallback:
                continue
            with self.subTest(instance=i):
                self.assertTrue(any(",".join(c.courses) in feasible_sets for c in candidates))
            covered += 1
        self.assertGreater(covered, 500)


if __name__ == "__main__":
    unittest.main()
