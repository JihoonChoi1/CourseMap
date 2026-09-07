"""Shared test fixture builders.

Functions for building small synthetic catalogs directly within tests. Builds a Catalog using the
same rules as the loader (by_id keeps the first id, index reflects file order).
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.model import Catalog, Course, Group, Program, Programs, Student  # noqa: E402
from engine.planner import plan_student  # noqa: E402
from engine.validate import validate  # noqa: E402
from engine.verify import verify_plan  # noqa: E402
from oracle import exists_plan  # noqa: E402

DATA_DIR = os.path.join(ROOT, "data")
SF = ["SPRING", "FALL"]
S = ["SPRING"]
F = ["FALL"]


def course(cid, credits=3, offered=None, prereqs=None, coreqs=None) -> Course:
    return Course(
        id=cid,
        title=cid,
        credits=credits,
        offered=list(SF if offered is None else offered),
        prereqs=[list(c) for c in (prereqs or [])],
        coreqs=[list(c) for c in (coreqs or [])],
    )


def catalog(courses, seasons=None) -> Catalog:
    by_id = {}
    index = {}
    for i, c in enumerate(courses):
        if c.id not in by_id:
            by_id[c.id] = c
            index[c.id] = i
    return Catalog(seasons=list(SF if seasons is None else seasons), courses=list(courses), by_id=by_id, index=index)


def all_group(gid, courses) -> Group:
    return Group(id=gid, name=gid, rule="ALL", n=0, courses=list(courses))


def pick_group(gid, n, courses) -> Group:
    return Group(id=gid, name=gid, rule="PICK_N", n=n, courses=list(courses))


def programs(degree_groups, tracks=None) -> Programs:
    """tracks: {track_id: [Group]}. If omitted, a single track 'T' with no groups."""
    if tracks is None:
        tracks = {"T": []}
    track_list = []
    for tid in tracks:
        track_list.append(Program(id=tid, name=tid, groups=list(tracks[tid])))
    return Programs(degree=Program(id="DEG", name="DEG", groups=list(degree_groups)), tracks=track_list)


def student(num_terms=8, cap=15, completed=None, start=(2026, "FALL"), track="T", sid="ST") -> Student:
    return Student(
        id=sid,
        name=sid,
        track=track,
        completed=list(completed or []),
        start_year=start[0],
        start_season=start[1],
        num_terms=num_terms,
        max_credits=cap,
    )


def term_of(plan: dict) -> dict:
    """plan JSON -> {course id: term index}."""
    out = {}
    for t, term in enumerate(plan["terms"]):
        for cid in term["courses"]:
            out[cid] = t
    return out


def codes(errors) -> list:
    """Just the codes from a list of EngineErrors or error dicts."""
    out = []
    for e in errors:
        out.append(e["code"] if isinstance(e, dict) else e.code)
    return out


def errors_with(errors, code: str) -> list:
    out = []
    for e in errors:
        if (e["code"] if isinstance(e, dict) else e.code) == code:
            out.append(e)
    return out


def cycle_path(message: str) -> list:
    """Extract the 'A → B → A' path from an E_CYCLE message."""
    m = re.search(r"\): ([^.]+)\.", message)
    if m is None:
        raise AssertionError("no path found in E_CYCLE message: " + message)
    return m.group(1).split(" → ")


class EngineTestCase:
    """Helper mixin used alongside unittest.TestCase."""

    def plan(self, cat, progs, stu, oracle=True):
        """Static validation -> plan_student. If feasible, always double-checks with verify_plan.

        Also checks that feasibility matches the exhaustive-search oracle, if the track/completed
        courses are valid. oracle=False is only used in tests that deliberately break the search
        limit etc. so the engine can't find an answer.
        """
        static = validate(cat, progs)
        self.assertEqual(static.errors, [], "fixture must pass static validation")
        res = plan_student(cat, progs, static, stu)
        plan = res.plan
        track = None
        for t in progs.tracks:
            if t.id == stu.track:
                track = t
        if oracle and track is not None and all(cid in cat.by_id for cid in stu.completed):
            exists = exists_plan(cat, progs.degree.groups + track.groups, stu.completed, stu.start_season,
                                 stu.num_terms, stu.max_credits)
            self.assertEqual(plan["feasible"], exists, "mismatch with oracle: " + repr(plan["errors"]))
        if plan["feasible"]:
            self.assertEqual(plan["errors"], [])
            violations = verify_plan(cat, progs, stu, plan)
            self.assertEqual(violations, [], "verify_plan violation")
        else:
            self.assertEqual(plan["terms"], [])
            self.assertEqual(plan["chosen"], {})
            self.assertGreater(len(plan["errors"]), 0)
            for e in plan["errors"]:
                self.assertEqual(sorted(e.keys()), ["code", "courses", "message"])
        return res

    def assertFeasible(self, res):
        self.assertTrue(res.plan["feasible"], "infeasible: " + repr(res.plan["errors"]))

    def assertInfeasible(self, res, code):
        self.assertFalse(res.plan["feasible"])
        self.assertIn(code, codes(res.plan["errors"]))
