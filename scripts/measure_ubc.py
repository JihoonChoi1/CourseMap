"""Grid measurement over real data (data/ubc) (Phase 6). Measures the item docs/debug_log.md left as "re-measure on real data".

    python3 scripts/measure_ubc.py            # print a table
    python3 scripts/measure_ubc.py --json     # per-case results as JSON (stdout)

Grid: 6 students × credit cap 3-18 × remaining terms 1-8 = 768 cells.
For each case:
- the engine (plan_student) result, run time, candidate count, whether it fell back, whether minimum terms is guaranteed
- the raw PICK_N combination count (before comparing against MAX_CANDIDATES, the same computation as generate_candidates' total)
- exhaustive-search node usage (the max amount used out of the EXACT_NODE_LIMIT budget, per target set) and whether the limit was hit
- the independent oracle's (tests/oracle_free.py) minimum terms, and whether its placement (witness) passes verify_plan
The engine itself is never modified. Node usage is measured by wrapping scheduler._exact_within.
"""

import argparse
import copy
import json
import os
import sys
import time
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine import scheduler  # noqa: E402
from engine.loader import load_all  # noqa: E402
from engine.planner import plan_student  # noqa: E402
from engine.targets import _count_in, add_with_closure, combinations  # noqa: E402
from engine.validate import validate  # noqa: E402
from engine.verify import verify_plan  # noqa: E402
from oracle_free import find_plan_free  # noqa: E402

DATA_DIR = os.path.join(ROOT, "data", "ubc")
CAPS = range(3, 19)
TERMS = range(1, 9)


def pick_combos(catalog, groups, completed_list) -> int:
    """The total that generate_candidates compares against MAX_CANDIDATES (PICK_N selection combination count, before OR expansion)."""
    completed = {}
    for cid in completed_list:
        completed[cid] = True
    base = {}
    for g in groups:
        if g.rule == "ALL":
            for cid in g.courses:
                add_with_closure(catalog, cid, base, completed)
    total = 1
    for g in groups:
        if g.rule != "PICK_N":
            continue
        need = g.n - _count_in(g, base, completed)
        if need > 0:
            pool = [cid for cid in g.courses if not completed.get(cid, False) and not base.get(cid, False)]
            total *= len(combinations(pool, need))
    return total


def run_engine(cat, progs, static, st):
    """(PlanResult, seconds, the max node count used for a single target set)."""
    used = [0]
    real = scheduler._exact_within

    def wrapped(g, catalog, cal, cap, completed, horizon, budget):
        before = budget[0]
        out = real(g, catalog, cal, cap, completed, horizon, budget)
        # budget is shared within a single target set (one schedule() call). The cumulative amount spent within it = LIMIT - remaining
        spent = scheduler.EXACT_NODE_LIMIT - min(budget[0], before)
        if spent > used[0]:
            used[0] = spent
        return out

    with mock.patch.object(scheduler, "_exact_within", wrapped):
        t0 = time.perf_counter()
        res = plan_student(cat, progs, static, st)
        sec = time.perf_counter() - t0
    return res, sec, used[0]


def witness_plan(cat, progs, st, terms):
    """oracle placement -> Plan JSON shape (input for verify_plan)."""
    p = len(cat.seasons)
    s0 = cat.seasons.index(st.start_season)
    out_terms = []
    taken = set(st.completed)
    for t, ids in enumerate(terms):
        idx = s0 + t
        out_terms.append({"year": st.start_year + idx // p, "season": cat.seasons[idx % p], "courses": ids,
                          "credits": sum(cat.by_id[c].credits for c in ids)})
        taken.update(ids)
    track = [t for t in progs.tracks if t.id == st.track][0]
    chosen = {}
    for g in progs.degree.groups + track.groups:
        if g.rule == "PICK_N":
            chosen[g.id] = [c for c in g.courses if c in taken]
    return {"student_id": st.id, "feasible": True, "terms": out_terms, "chosen": chosen, "errors": []}


def measure():
    cat, progs, students = load_all(DATA_DIR)
    static = validate(cat, progs)
    if static.errors:
        raise SystemExit("static validation error: " + repr(static.errors))
    rows = []
    for s in students:
        track = [t for t in progs.tracks if t.id == s.track][0]
        groups = progs.degree.groups + track.groups
        combos = pick_combos(cat, groups, s.completed)
        for cap in CAPS:
            # oracle minimum terms: the first feasible term count in 1..8 (feasibility is monotonic in term count)
            t0 = time.perf_counter()
            o_min = 0
            o_plan = None
            for n in TERMS:
                found = find_plan_free(cat, groups, s.completed, s.start_season, n, cap)
                if found is not False:
                    o_min = n
                    o_plan = found
                    break
            o_sec = time.perf_counter() - t0
            o_violations = None
            if o_plan is not None:
                st = copy.copy(s)
                st.max_credits = cap
                st.num_terms = o_min
                o_violations = verify_plan(cat, progs, st, witness_plan(cat, progs, st, o_plan))
            for n in TERMS:
                st = copy.copy(s)
                st.max_credits = cap
                st.num_terms = n
                res, sec, nodes = run_engine(cat, progs, static, st)
                plan = res.plan
                violations = verify_plan(cat, progs, st, plan) if plan["feasible"] else []
                rows.append({
                    "student": s.id, "cap": cap, "num_terms": n,
                    "feasible": plan["feasible"], "terms_used": len(plan["terms"]),
                    "candidates": res.candidates, "pick_combos": combos, "used_fallback": res.used_fallback,
                    "min_terms_proven": res.min_terms_proven, "sec": sec, "max_nodes": nodes,
                    "limit_hit": nodes > scheduler.EXACT_NODE_LIMIT,
                    "unproven_msg": any("증명된 것은 아님" in e["message"] for e in plan["errors"]),
                    "error_codes": sorted(set(e["code"] for e in plan["errors"])),
                    "verify": violations,
                    "oracle_feasible": o_min != 0 and n >= o_min, "oracle_min_terms": o_min,
                    "oracle_sec": o_sec, "oracle_witness_violations": o_violations,
                })
    return rows


def summarize(rows):
    def count(pred):
        return sum(1 for r in rows if pred(r))

    print("grid: " + str(len(rows)) + " cells (6 students × cap 3-18 × terms 1-8)")
    print("  engine feasible " + str(count(lambda r: r["feasible"])) + " / infeasible " + str(count(lambda r: not r["feasible"])))
    print("  oracle feasible " + str(count(lambda r: r["oracle_feasible"])))
    print("  verify_plan violations (engine feasible): " + str(count(lambda r: r["verify"])))
    print("  oracle witness placement verify violations: " + str(sum(1 for r in rows if r["oracle_witness_violations"])))
    print("  engine feasible but oracle infeasible (engine or oracle bug): "
          + str(count(lambda r: r["feasible"] and not r["oracle_feasible"])))
    print("  missed cases (oracle feasible, engine infeasible): " + str(count(lambda r: r["oracle_feasible"] and not r["feasible"])))
    print("    of those, missing the 'not proven' flag: "
          + str(count(lambda r: r["oracle_feasible"] and not r["feasible"] and not r["unproven_msg"])))
    print("  not minimum terms (engine terms > oracle minimum): "
          + str(count(lambda r: r["feasible"] and r["terms_used"] > r["oracle_min_terms"])))
    print("    of those, min_terms_proven=True (claimed guaranteed but wrong): "
          + str(count(lambda r: r["feasible"] and r["terms_used"] > r["oracle_min_terms"] and r["min_terms_proven"])))
    pairs = [r for r in rows if r["num_terms"] == max(TERMS) and r["oracle_min_terms"] > 0]
    print("  of " + str(len(pairs)) + " (student, cap) pairs (oracle feasible within " + str(max(TERMS)) + " terms), at num_terms=" + str(max(TERMS))
          + " the engine fails to find the minimum: " + str(sum(1 for r in pairs if not r["feasible"] or r["terms_used"] > r["oracle_min_terms"]))
          + " (reported infeasible " + str(sum(1 for r in pairs if not r["feasible"])) + ", +1 term "
          + str(sum(1 for r in pairs if r["feasible"] and r["terms_used"] == r["oracle_min_terms"] + 1)) + ", +2 terms or more "
          + str(sum(1 for r in pairs if r["feasible"] and r["terms_used"] >= r["oracle_min_terms"] + 2)) + ")")
    print("  fallback " + str(count(lambda r: r["used_fallback"])) + ", exhaustive-search limit hit "
          + str(count(lambda r: r["limit_hit"])))
    secs = sorted(r["sec"] for r in rows)
    print("  engine time: median %.4fs, max %.4fs, total %.2fs" % (secs[len(secs) // 2], secs[-1], sum(secs)))
    print("  exhaustive-search nodes: max " + str(max(r["max_nodes"] for r in rows)) + " / limit " + str(scheduler.EXACT_NODE_LIMIT))
    print()
    print("per student:")
    print("  stu   pick_combos     candidates(range)  fallback  feasible  missed  non-min  maxnodes  engine_max(s)  oracle_total(s)")
    for sid in sorted(set(r["student"] for r in rows)):
        rs = [r for r in rows if r["student"] == sid]
        o_total = sum(r["oracle_sec"] for r in rs if r["num_terms"] == 1)
        print("  %-4s %11d  %6d~%-6d  %4d  %4d/%-4d  %4d  %6d  %8d  %10.4f  %12.2f" % (
            sid, rs[0]["pick_combos"], min(r["candidates"] for r in rs), max(r["candidates"] for r in rs),
            sum(r["used_fallback"] for r in rs), sum(r["feasible"] for r in rs), len(rs),
            sum(1 for r in rs if r["oracle_feasible"] and not r["feasible"]),
            sum(1 for r in rs if r["feasible"] and r["terms_used"] > r["oracle_min_terms"]),
            max(r["max_nodes"] for r in rs), max(r["sec"] for r in rs), o_total))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rows = measure()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        summarize(rows)


if __name__ == "__main__":
    main()
