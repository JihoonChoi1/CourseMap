"""CLI entry point.

    python3 cli.py S1                      # Plan JSON to stdout
    python3 cli.py S3 --max-credits 7      # experiment with an overridden student setting
    python3 cli.py --all                   # all students

stdout: Plan JSON (an object for a single student, an array for --all)
stderr: summary + verify_plan (independent verification) result
exit code: 0 all feasible & verified / 1 includes infeasible / 2 input error / 3 verification failure (engine bug)
"""

import argparse
import json
import os
import sys

from engine.loader import InputError, load_all
from engine.planner import plan_student
from engine.validate import validate
from engine.verify import verify_plan


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a graduation course plan (greedy + exhaustive-search fallback engine)")
    ap.add_argument("student_id", nargs="?", help="student id from students.json")
    ap.add_argument("--all", action="store_true", help="run for all students")
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    ap.add_argument("--max-credits", type=int, help="override the per-term credit cap")
    ap.add_argument("--num-terms", type=int, help="override the number of remaining terms")
    args = ap.parse_args()
    if not args.all and args.student_id is None:
        ap.error("student_id or --all is required")

    try:
        catalog, programs, students = load_all(args.data_dir)
    except InputError as e:
        print("input error: " + str(e), file=sys.stderr)
        return 2

    targets = []
    for s in students:
        if args.all or s.id == args.student_id:
            targets.append(s)
    if len(targets) == 0:
        print("no such student: " + str(args.student_id), file=sys.stderr)
        return 2

    static = validate(catalog, programs)
    plans = []
    code = 0
    for s in targets:
        if args.max_credits is not None:
            s.max_credits = args.max_credits
        if args.num_terms is not None:
            s.num_terms = args.num_terms
        if s.max_credits < 1 or s.num_terms < 1:
            print(s.id + ": --max-credits/--num-terms must be at least 1", file=sys.stderr)
            return 2

        res = plan_student(catalog, programs, static, s)
        plans.append(res.plan)

        head = ("[" + s.id + "] track=" + s.track + " cap=" + str(s.max_credits) + " num_terms=" + str(s.num_terms)
                + " | " + str(res.candidates) + " candidates" + (" (greedy fallback)" if res.used_fallback else ""))
        print(head, file=sys.stderr)
        if res.plan["feasible"]:
            print("  feasible: used " + str(len(res.plan["terms"])) + " terms (lower bound ignoring cap: "
                  + str(res.lower_bound_terms) + " terms), placed " + str(res.total_credits) + " credits total", file=sys.stderr)
            if res.min_terms_proven:
                print("  minimum terms: proven — no placement graduates earlier than this", file=sys.stderr)
            else:
                print("  minimum terms: not proven — exhaustive search limit exceeded or combination explosion, only partially searched", file=sys.stderr)
            violations = verify_plan(catalog, programs, s, res.plan)
            if len(violations) == 0:
                print("  verify: OK — term offerings/prereq/coreq/credit cap/duplicates/group requirements all satisfied", file=sys.stderr)
            else:
                code = 3
                print("  verify: FAIL", file=sys.stderr)
                for line in violations:
                    print("    - " + line, file=sys.stderr)
        else:
            if code == 0:
                code = 1
            print("  infeasible:", file=sys.stderr)
            for e in res.plan["errors"]:
                print("    - " + e["code"] + ": " + e["message"], file=sys.stderr)

    out = plans if args.all else plans[0]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
