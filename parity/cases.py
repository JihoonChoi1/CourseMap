"""Python (reference implementation) <-> Go comparison cases: serialization, expected-value computation, input generators.

A case is one line of JSON, in the same format as RunCase on the Go side (goengine/parity/parity.go).

    plan      {"kind": "plan", "snapshot": {"catalog", "programs", "student"}, "limits": {...}}
    validate  {"kind": "validate", "catalog", "programs"}
    verify    {"kind": "verify", "snapshot": {...}, "plan": {...}}
    load      {"kind": "load", "which": "catalog"|"programs"|"student", "raw", "seasons", "where"}

python_result(line) returns the result of running the same case through the Python engine as a
json.dumps(..., ensure_ascii=False) string, byte-compared against the Go result. Since the
snapshot has the same shape as data/*.json (= PlanJob input), both implementations go through the
loader (parse_*) first.
"""

import copy
import json
import os
import random
import sys
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine import scheduler, targets  # noqa: E402
from engine.loader import InputError, load_all, parse_catalog, parse_programs, parse_student  # noqa: E402
from engine.planner import plan_student  # noqa: E402
from engine.validate import validate  # noqa: E402
from engine.verify import verify_plan  # noqa: E402

DATA_DIR = os.path.join(ROOT, "data")
UBC_DIR = os.path.join(ROOT, "data", "ubc")


# ---------------------------------------------------------------------------
# Serialization: model structs -> data/*.json shape
# ---------------------------------------------------------------------------

def catalog_raw(cat) -> dict:
    courses = []
    for c in cat.courses:
        courses.append({"id": c.id, "title": c.title, "credits": c.credits, "offered": list(c.offered),
                        "prereqs": [list(x) for x in c.prereqs], "coreqs": [list(x) for x in c.coreqs]})
    return {"seasons": list(cat.seasons), "courses": courses}


def _program_raw(p) -> dict:
    groups = []
    for g in p.groups:
        groups.append({"id": g.id, "name": g.name, "rule": g.rule, "n": g.n, "courses": list(g.courses)})
    return {"id": p.id, "name": p.name, "groups": groups}


def programs_raw(progs) -> dict:
    return {"degree": _program_raw(progs.degree), "tracks": [_program_raw(t) for t in progs.tracks]}


def student_raw(s) -> dict:
    return {"id": s.id, "name": s.name, "track": s.track, "completed": list(s.completed),
            "start_term": {"year": s.start_year, "season": s.start_season},
            "num_terms": s.num_terms, "max_credits_per_term": s.max_credits}


def snapshot(cat, progs, stu) -> dict:
    return {"catalog": catalog_raw(cat), "programs": programs_raw(progs), "student": student_raw(stu)}


DEFAULT_LIMITS = {"exact_node_limit": 200000, "max_candidates": 256}


def current_limits() -> dict:
    """The current limits, including any values a test has overridden via mock.patch."""
    return {"exact_node_limit": scheduler.EXACT_NODE_LIMIT, "max_candidates": targets.MAX_CANDIDATES}


def plan_case(cat, progs, stu, limits=None) -> dict:
    return {"kind": "plan", "snapshot": snapshot(cat, progs, stu), "limits": dict(limits or DEFAULT_LIMITS)}


def validate_case(cat, progs) -> dict:
    return {"kind": "validate", "catalog": catalog_raw(cat), "programs": programs_raw(progs)}


def verify_case(cat, progs, stu, plan) -> dict:
    return {"kind": "verify", "snapshot": snapshot(cat, progs, stu), "plan": copy.deepcopy(plan)}


def load_case(which, raw, seasons=None, where="") -> dict:
    return {"kind": "load", "which": which, "raw": copy.deepcopy(raw), "seasons": list(seasons or []), "where": where}


def encode(case: dict) -> str:
    return json.dumps(case, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Expected values (Python engine)
# ---------------------------------------------------------------------------

def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def result_obj(res) -> dict:
    """PlanResult -> a dict for comparison. Same key order as Go's engine.PlanResult.ResultJSON."""
    return {"plan": res.plan, "candidates": res.candidates, "used_fallback": res.used_fallback, "picks": res.picks,
            "lower_bound_terms": res.lower_bound_terms, "total_credits": res.total_credits,
            "min_terms_proven": res.min_terms_proven}


def parse_snapshot(snap: dict):
    """Same as planapi.views.parse_snapshot (without importing Django). The student location tag is "student(<id>)"."""
    catalog = parse_catalog(snap["catalog"])
    programs = parse_programs(snap["programs"])
    rs = snap["student"]
    sid = rs["id"] if isinstance(rs, dict) and isinstance(rs.get("id"), str) else "?"
    student = parse_student(rs, catalog.seasons, "student(" + sid + ")")
    return catalog, programs, student


def python_result(line: str) -> str:
    case = json.loads(line)
    kind = case["kind"]
    try:
        if kind == "plan":
            cat, progs, stu = parse_snapshot(case["snapshot"])
            limits = case["limits"]
            with mock.patch.object(scheduler, "EXACT_NODE_LIMIT", limits["exact_node_limit"]), \
                    mock.patch.object(targets, "MAX_CANDIDATES", limits["max_candidates"]):
                res = plan_student(cat, progs, validate(cat, progs), stu)
            return dumps(result_obj(res))
        if kind == "validate":
            cat = parse_catalog(case["catalog"])
            progs = parse_programs(case["programs"])
            static = validate(cat, progs)
            errors = [{"code": e.code, "message": e.message, "courses": e.courses} for e in static.errors]
            return dumps({"errors": errors, "bundles": static.bundles})
        if kind == "verify":
            cat, progs, stu = parse_snapshot(case["snapshot"])
            return dumps({"violations": verify_plan(cat, progs, stu, case["plan"])})
        if kind == "load":
            which = case["which"]
            if which == "catalog":
                parse_catalog(case["raw"])
            elif which == "programs":
                parse_programs(case["raw"])
            else:
                parse_student(case["raw"], case["seasons"], case["where"])
            return dumps({"ok": True})
    except InputError as e:
        return dumps({"input_error": str(e)})
    raise ValueError("unknown case kind: " + kind)


# ---------------------------------------------------------------------------
# Input generators
# ---------------------------------------------------------------------------

def grid_cases() -> list:
    """The same grid as tests/test_grid.py: 4 students × cap 3-16 × terms 1-8 = 448."""
    cat, progs, students = load_all(DATA_DIR)
    out = []
    for s in students:
        for cap in range(3, 17):
            for n in range(1, 9):
                st = copy.copy(s)
                st.max_credits = cap
                st.num_terms = n
                out.append(plan_case(cat, progs, st))
    return out


def ubc_grid_cases() -> list:
    """Real-data (data/ubc, Phase 6) grid: 6 students × cap 3-18 × terms 1-8 = 768 (same as scripts/measure_ubc.py)."""
    cat, progs, students = load_all(UBC_DIR)
    out = []
    for s in students:
        for cap in range(3, 19):
            for n in range(1, 9):
                st = copy.copy(s)
                st.max_credits = cap
                st.num_terms = n
                out.append(plan_case(cat, progs, st))
    return out


def ubc_limit_cases() -> list:
    """Real data + limit variations: raises the candidate limit to exercise the expand_or/candidate-evaluation
    path (U5, U6), and lowers the exhaustive-search limit to exercise the over-limit path. Per-student cap 9/15 × terms 1-4."""
    cat, progs, students = load_all(UBC_DIR)
    out = []
    for s in students:
        for cap in (9, 15):
            for n in range(1, 5):
                st = copy.copy(s)
                st.max_credits = cap
                st.num_terms = n
                if s.id in ("U5", "U6"):
                    out.append(plan_case(cat, progs, st, {"exact_node_limit": 200000, "max_candidates": 20000}))
                for node_limit in (0, 5, 50):
                    out.append(plan_case(cat, progs, st, {"exact_node_limit": node_limit, "max_candidates": 256}))
    return out


def fuzz_small_cases(count: int, seed: int) -> list:
    """tests/test_fuzz.py's generator (3-9 courses). Instances that produce static errors are included as-is."""
    from test_fuzz import random_instance
    rng = random.Random(seed)
    return [plan_case(*random_instance(rng)) for _ in range(count)]


def fuzz_limit_cases(count: int, seed: int) -> list:
    """Run with small search/candidate limits to compare the "limit exceeded" path and node-counting timing."""
    from test_fuzz import random_instance
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        cat, progs, stu = random_instance(rng)
        limits = {"exact_node_limit": rng.choice([0, 1, 2, 3, 5, 8, 13, 30, 100, 200000]),
                  "max_candidates": rng.choice([1, 1, 2, 3, 5, 256])}
        out.append(plan_case(cat, progs, stu, limits))
    return out


def random_large_instance(rng):
    """12-28 courses. OR clause size 1-3, mutual-coreq bundles, multiple PICK_N groups."""
    from helpers import F, S, SF, all_group, catalog, course, pick_group, programs, student
    n = rng.randint(12, 28)
    ids = ["K" + str(i).zfill(2) for i in range(n)]
    cs = []
    for i, cid in enumerate(ids):
        earlier = ids[:i]
        prereqs = []
        coreqs = []
        if earlier:
            for _ in range(rng.choice([0, 1, 1, 2, 2, 3])):
                prereqs.append(rng.sample(earlier, min(rng.choice([1, 1, 2, 3]), len(earlier))))
            if rng.random() < 0.15:
                coreqs.append(rng.sample(earlier, min(rng.choice([1, 2]), len(earlier))))
        cs.append(course(cid, rng.choice([1, 2, 3, 3, 3, 4, 4]), rng.choice([SF, SF, S, F]), prereqs, coreqs))
    for _ in range(rng.choice([0, 0, 1, 2])):
        a, b = rng.sample(cs[n // 2:], 2)
        if not any(a.id in cl for cl in b.prereqs) and not any(b.id in cl for cl in a.prereqs):
            a.coreqs.append([b.id])
            b.coreqs.append([a.id])
    groups = [all_group("REQ", rng.sample(ids, rng.randint(2, 6)))]
    for gi in range(rng.randint(0, 3)):
        pool = rng.sample(ids, rng.randint(2, 7))
        groups.append(pick_group("P" + str(gi), rng.randint(1, min(3, len(pool))), pool))
    tracks = {}
    for ti in range(rng.randint(1, 2)):
        tg = []
        if rng.random() < 0.7:
            pool = rng.sample(ids, rng.randint(2, 5))
            tg.append(pick_group("T" + str(ti) + "P", rng.randint(1, len(pool)), pool))
        tracks["TR" + str(ti)] = tg
    stu = student(num_terms=rng.randint(2, 8), cap=rng.randint(4, 16), completed=rng.sample(ids, rng.randint(0, n // 4)),
                  start=(2026, rng.choice(["SPRING", "FALL"])), track=rng.choice(list(tracks)))
    return catalog(cs), programs(groups, tracks), stu


def fuzz_large_cases(count: int, seed: int) -> list:
    rng = random.Random(seed)
    return [plan_case(*random_large_instance(rng)) for _ in range(count)]


def random_messy_instance(rng):
    """A catalog with static-validation errors mixed in: cycles, bad references, duplicate ids, etc. Later courses
    are also referenced, which is what produces cycles."""
    from helpers import F, S, SF, catalog, course, programs, student
    from engine.model import Group
    n = rng.randint(2, 10)
    ids = ["M" + str(i) for i in range(n)]
    pool = ids + ["GHOST"]
    cs = []
    for cid in ids:
        prereqs = []
        coreqs = []
        for _ in range(rng.choice([0, 1, 1, 2])):
            prereqs.append(rng.sample(pool, rng.choice([1, 1, 2])) if rng.random() > 0.03 else [])
        for _ in range(rng.choice([0, 0, 1])):
            coreqs.append(rng.sample(pool, rng.choice([1, 1, 2])))
        offered = rng.choice([SF, SF, S, F, [], ["SUMMER"]] if rng.random() < 0.1 else [SF, S, F])
        credits = rng.choice([1, 3, 3, 4, 0, -1] if rng.random() < 0.1 else [1, 2, 3, 4])
        cs.append(course(cid, credits, offered, prereqs, coreqs))
    if rng.random() < 0.1:
        cs.append(course(rng.choice(ids)))  # duplicate id
    groups = [Group(id="REQ", name="REQ", rule="ALL", n=0, courses=rng.sample(pool, rng.randint(1, 3)))]
    if rng.random() < 0.5:
        members = rng.sample(ids, rng.randint(1, min(4, n)))
        groups.append(Group(id=rng.choice(["P", "REQ"]), name="P", rule=rng.choice(["PICK_N", "PICK_N", "ANY"]),
                            n=rng.randint(0, len(members) + 1), courses=members))
    tracks = {"T": []} if rng.random() < 0.9 else {"T": [], "DEG": []}
    stu = student(num_terms=rng.randint(1, 5), cap=rng.randint(2, 12),
                  completed=rng.sample(pool, rng.randint(0, 2)), track=rng.choice(["T", "T", "T", "NOPE"]),
                  start=(2026, rng.choice(["SPRING", "FALL"])))
    return catalog(cs), programs(groups, tracks), stu


def fuzz_messy_cases(count: int, seed: int) -> list:
    """Half the cases are a catalog with static errors mixed in (validate + plan); the other half are a
    plan against a clean catalog with student-side runtime errors mixed in (a nonexistent track,
    completed courses not in the catalog, duplicate completed courses)."""
    from test_fuzz import random_instance
    rng = random.Random(seed)
    out = []
    for i in range(count):
        if i % 2 == 0:
            cat, progs, stu = random_messy_instance(rng)
            out.append(validate_case(cat, progs))
            out.append(plan_case(cat, progs, stu))
            continue
        cat, progs, stu = random_instance(rng)
        r = rng.random()
        if r < 0.3:
            stu.track = rng.choice(["NOPE", "", "DEG"])
        elif r < 0.6:
            stu.completed = stu.completed + rng.sample(["GHOST", "X1", "", "C0"], rng.randint(1, 2))
        else:
            stu.completed = stu.completed + list(stu.completed) + [c.id for c in rng.sample(cat.courses, 1)]
        out.append(plan_case(cat, progs, stu))
    return out


def _mutate_plan(rng, plan: dict, cat) -> dict:
    """Break one spot of a feasible plan (the kind of violation verify is supposed to catch)."""
    plan = copy.deepcopy(plan)
    terms = plan["terms"]
    r = rng.random()
    if r < 0.2 and len(terms) > 1:
        cid = rng.choice(rng.choice(terms)["courses"] or ["C0"])
        rng.choice(terms)["courses"].append(cid)  # a move or a duplicate
    elif r < 0.35 and terms:
        t = rng.choice(terms)
        if t["courses"]:
            t["courses"].pop(rng.randrange(len(t["courses"])))
    elif r < 0.5 and terms:
        rng.choice(terms)["credits"] += rng.choice([-1, 1, 5])
    elif r < 0.6 and terms:
        t = rng.choice(terms)
        t["year"] += rng.choice([-1, 1])
    elif r < 0.7 and terms:
        t = rng.choice(terms)
        t["season"] = "FALL" if t["season"] == "SPRING" else "SPRING"
    elif r < 0.8 and terms:
        rng.choice(terms)["courses"].append(rng.choice(["GHOST"] + [c.id for c in cat.courses]))
    elif r < 0.9 and plan["chosen"]:
        g = rng.choice(list(plan["chosen"]))
        plan["chosen"][g] = rng.choice([[], plan["chosen"][g][:1], plan["chosen"][g] + ["GHOST"]])
    elif terms:
        terms.reverse()
    return plan


def fuzz_verify_cases(count: int, seed: int) -> list:
    """Compare a small random instance's feasible plan (1 original + 2 broken) via verify."""
    from test_fuzz import random_instance
    rng = random.Random(seed)
    out = []
    while len(out) < count:
        cat, progs, stu = random_instance(rng)
        static = validate(cat, progs)
        if static.errors:
            continue
        plan = plan_student(cat, progs, static, stu).plan
        if not plan["feasible"]:
            continue
        out.append(verify_case(cat, progs, stu, plan))
        for _ in range(2):
            bad = plan
            for _ in range(rng.randint(1, 3)):  # the violation must span multiple courses for the report order to be compared too
                bad = _mutate_plan(rng, bad, cat)
            out.append(verify_case(cat, progs, stu, bad))
    return out[:count]


JUNK = [True, False, None, 1.5, 1.0, "x", "", 0, -3, 7, [], {}, ["a", 1], [[1]], [["A"], "B"], {"k": 1}]


def _mutate(rng, value):
    """Delete or replace with a different-typed value at one random position in a JSON tree."""
    value = copy.deepcopy(value)
    path = []
    node = value
    while True:
        if isinstance(node, dict) and node and rng.random() < 0.75:
            k = rng.choice(list(node))
            path.append(k)
            node = node[k]
        elif isinstance(node, list) and node and rng.random() < 0.75:
            i = rng.randrange(len(node))
            path.append(i)
            node = node[i]
        else:
            break
    if not path:
        return rng.choice(JUNK)
    parent = value
    for k in path[:-1]:
        parent = parent[k]
    if isinstance(parent, dict) and rng.random() < 0.3:
        del parent[path[-1]]
    else:
        parent[path[-1]] = copy.deepcopy(rng.choice(JUNK))
    return value


def fuzz_load_cases(count: int, seed: int) -> list:
    """Break the original data/*.json in one spot at a time, comparing the loader's shape-check order and messages."""
    rng = random.Random(seed)
    raws = {}
    for name in ("catalog", "programs", "students"):
        with open(os.path.join(DATA_DIR, name + ".json"), encoding="utf-8") as f:
            raws[name] = json.load(f)
    seasons = raws["catalog"]["seasons"]
    out = []
    for _ in range(count):
        which = rng.choice(["catalog", "programs", "student"])
        if which == "student":
            i = rng.randrange(len(raws["students"]["students"]))
            raw = raws["students"]["students"][i]
            out.append(load_case("student", _mutate(rng, raw) if rng.random() < 0.95 else raw, seasons,
                                 "students[" + str(i) + "]"))
        else:
            raw = raws[which]
            out.append(load_case(which, _mutate(rng, raw) if rng.random() < 0.95 else raw))
    return out


def random_coreq_instance(rng):
    """A small instance with many coreqs. Exhaustive search often has to discard combinations "because of a coreq"."""
    from helpers import F, S, SF, all_group, catalog, course, pick_group, programs, student
    n = rng.randint(4, 9)
    ids = ["Q" + str(i) for i in range(n)]
    cs = []
    for i, cid in enumerate(ids):
        earlier = ids[:i]
        prereqs = []
        coreqs = []
        if earlier:
            if rng.random() < 0.4:
                prereqs.append(rng.sample(earlier, min(rng.choice([1, 2]), len(earlier))))
            if rng.random() < 0.6:
                coreqs.append(rng.sample(earlier, min(rng.choice([1, 1, 2]), len(earlier))))
        cs.append(course(cid, rng.choice([1, 2, 2, 3, 3, 4]), rng.choice([SF, SF, S, F]), prereqs, coreqs))
    if rng.random() < 0.3:
        a, b = cs[-2], cs[-1]
        if not any(a.id in cl for cl in b.prereqs):
            a.coreqs.append([b.id])
            b.coreqs.append([a.id])
    groups = [all_group("REQ", rng.sample(ids, rng.randint(2, n)))]
    if rng.random() < 0.5:
        pool = rng.sample(ids, rng.randint(2, min(4, n)))
        groups.append(pick_group("P1", rng.randint(1, len(pool)), pool))
    stu = student(num_terms=rng.randint(2, 6), cap=rng.randint(3, 8), start=(2026, rng.choice(["SPRING", "FALL"])))
    return catalog(cs), programs(groups), stu


def limit_sweep_cases(instances: int, seed: int, max_limit: int = 30) -> list:
    """For each instance, run with the exhaustive-search limit raised one at a time from 0..max_limit.
    If node-counting timing differs anywhere, the result (min_terms_proven, placement, diagnostic message) diverges at some limit value."""
    rng = random.Random(seed)
    out = []
    for _ in range(instances):
        cat, progs, stu = random_coreq_instance(rng)
        mc = rng.choice([1, 2, 256])
        for limit in range(max_limit + 1):
            out.append(plan_case(cat, progs, stu, {"exact_node_limit": limit, "max_candidates": mc}))
    return out


WEIRD = ["A\"B", "back\\slash", "tab\tX", "nl\nX", "cr\rX", "bs\bX", "ff\fX", "nul\x00X", "esc\x1bX",
         "del\x7fX", "ls\u2028X", "ps\u2029X", "한글과목", "emoji😀", "é", "<&>", "/slash", "+plus", ",comma", "|bar",
         " sp ", "→arrow", "{brace}", "'q'", "\u00a0nbsp"]


def string_cases(count: int, seed: int) -> list:
    """Insert control characters, quotes, backslashes, and non-ASCII characters into course/group/track/student ids and seasons.
    Compares the result JSON's string escaping (Python json.dumps rules) and the paths where ids appear in messages."""
    from test_fuzz import random_instance
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        cat, progs, stu = random_instance(rng)
        names = rng.sample(WEIRD, len(WEIRD))
        rename = {}
        for i, c in enumerate(cat.courses):
            rename[c.id] = names[i % len(names)] + str(i)
        for c in cat.courses:
            c.id = rename[c.id]
            c.prereqs = [[rename.get(x, x) for x in cl] for cl in c.prereqs]
            c.coreqs = [[rename.get(x, x) for x in cl] for cl in c.coreqs]
        for g in progs.degree.groups + [g for t in progs.tracks for g in t.groups]:
            g.id = rng.choice(WEIRD) + g.id
            g.courses = [rename.get(x, x) for x in g.courses]
        stu.completed = [rename.get(x, x) for x in stu.completed]
        stu.id = rng.choice(WEIRD)
        if rng.random() < 0.2:
            cat.courses[0].offered = cat.courses[0].offered + [rng.choice(WEIRD)]  # E_BAD_OFFERED message
        if rng.random() < 0.1:
            stu.track = rng.choice(WEIRD)  # E_UNKNOWN_TRACK message
        from helpers import catalog as make_catalog
        cat = make_catalog(cat.courses, cat.seasons)
        out.append(plan_case(cat, progs, stu))
        if rng.random() < 0.3:
            out.append(validate_case(cat, progs))
    return out


def random_cycle_instance(rng):
    """A catalog with several independent cycles (E_CYCLE) and bundles hanging off a single shared prerequisite R.
    Which descendant Tarjan descends into first from R shapes the SCC discovery order (error/bundle order)."""
    from helpers import F, S, SF, all_group, catalog, course, programs, student
    cs = [course("R")]
    members = []
    for k in range(rng.randint(2, 4)):
        size = rng.randint(2, 3)
        ids = ["G" + str(k) + "_" + str(i) for i in range(size)]
        strict = rng.random() < 0.5
        for i, cid in enumerate(ids):
            nxt = ids[(i + 1) % size]
            c = course(cid, rng.choice([1, 2, 3]), rng.choice([SF, SF, S, F]))
            if strict and i == 0:
                c.prereqs.append([nxt])
            else:
                c.coreqs.append([nxt])
            cs.append(c)
        rng.choice([c for c in cs if c.id in ids]).prereqs.append(["R"])
        members.extend(ids)
    body = cs[1:]
    rng.shuffle(body)  # the cycle groups must be shuffled within the catalog for traversal order to show up in the result
    cs = [cs[0]] + body
    progs = programs([all_group("REQ", rng.sample(members, rng.randint(1, 3)))])
    stu = student(num_terms=rng.randint(2, 6), cap=rng.randint(4, 12), start=(2026, rng.choice(["SPRING", "FALL"])))
    return catalog(cs), progs, stu


def cycle_cases(count: int, seed: int) -> list:
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        cat, progs, stu = random_cycle_instance(rng)
        out.append(validate_case(cat, progs))
        out.append(plan_case(cat, progs, stu))
    return out


def or_sweep_cases(max_k: int = 90) -> list:
    """Course T with k copies of the same OR clause [X | Y | Z]. Sweeps k and the candidate limit L to hit
    cases right below and above expand_or's safety-valve limit (L × 64) (falls back to a single greedy
    candidate if exceeded)."""
    from helpers import all_group, catalog, course, programs, student
    out = []
    for alts in (["X", "Y"], ["X", "Y", "Z"]):
        for k in range(1, max_k + 1):
            cs = [course("X", 3), course("Y", 2), course("Z", 1), course("T", prereqs=[list(alts) for _ in range(k)])]
            for limit in (1, 2, 3, 4, 7, 8):
                out.append(plan_case(catalog(cs), programs([all_group("REQ", ["T"])]), student(num_terms=3, cap=9),
                                     {"exact_node_limit": 200000, "max_candidates": limit}))
    return out


def _heavy_instance(rng):
    from helpers import F, S, SF, all_group, catalog, course, programs, student
    n = rng.randint(18, 26)
    ids = ["H" + str(i).zfill(2) for i in range(n)]
    cs = []
    for i, cid in enumerate(ids):
        earlier = ids[:i]
        pre, co = [], []
        if earlier and rng.random() < 0.5:
            pre.append(rng.sample(earlier, 1))
        if earlier and rng.random() < 0.25:
            co.append(rng.sample(earlier, 1))
        cs.append(course(cid, rng.choice([1, 1, 2, 2, 3, 4]), rng.choice([SF, SF, S, F]), pre, co))
    groups = [all_group("REQ", rng.sample(ids, rng.randint(n // 2, n)))]
    stu = student(num_terms=rng.randint(3, 6), cap=rng.randint(6, 12), start=(2026, rng.choice(["SPRING", "FALL"])))
    return catalog(cs), programs(groups), stu


def heavy_cases(count: int, seed: int) -> list:
    """Instances that actually use up the default limit (200,000 nodes) (the worst case for performance
    comparison). Collects only 18-26-course random catalogs that hit "limit exceeded" (minimum terms
    not guaranteed, or a "not proven" message). Finding these takes minutes, so run.py uses the
    saved result in parity/inputs/heavy.jsonl instead."""
    rng = random.Random(seed)
    out = []
    while len(out) < count:
        line = encode(plan_case(*_heavy_instance(rng)))
        r = json.loads(python_result(line))
        hit = (r["plan"]["feasible"] and not r["min_terms_proven"]) or any(
            "전수탐색이 한도" in e["message"] or "전수탐색 한도" in e["message"] for e in r["plan"]["errors"])
        if hit:
            out.append(json.loads(line))
    return out


if __name__ == "__main__":
    # python3 parity/cases.py heavy  ->  regenerates parity/inputs/heavy.jsonl (seed 11, first 20)
    if sys.argv[1:] == ["heavy"]:
        path = os.path.join(ROOT, "parity", "inputs", "heavy.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for c in heavy_cases(20, 11):
                f.write(encode(c) + "\n")
        print(path)
