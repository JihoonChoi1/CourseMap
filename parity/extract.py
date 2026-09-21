"""Intercept the inputs the engine tests (tests/, 184 of them) hand to the engine, turning them into comparison cases.

Before the test modules are imported, engine.planner.plan_student, engine.validate.validate,
engine.verify.verify_plan, and engine.loader.parse_* are swapped for recording wrappers, then the
tests run as-is via unittest discover. The input at call time (including any limits a test
overrode via mock.patch) is serialized, and afterward each case is recomputed from its serialized
input (python_result) to confirm it matches the original call's result (a serialization check).

Calls that can't be compared are counted and excluded by reason:
- calls where scheduler._precheck was mocked (Go has no equivalent injection point)
- calls where plan_student was given a StaticResult different from validate(catalog, programs)
"""

import io
import json
import os
import sys
import unittest

from cases import (ROOT, current_limits, dumps, encode, load_case, plan_case, python_result, result_obj,
                   validate_case, verify_case)

import engine.loader  # noqa: E402
import engine.planner  # noqa: E402
import engine.scheduler  # noqa: E402
import engine.validate  # noqa: E402
import engine.verify  # noqa: E402

ORIG_PLAN = engine.planner.plan_student
ORIG_VALIDATE = engine.validate.validate
ORIG_VERIFY = engine.verify.verify_plan
ORIG_PRECHECK = engine.scheduler._precheck
ORIG_PARSE = {
    "catalog": engine.loader.parse_catalog,
    "programs": engine.loader.parse_programs,
    "student": engine.loader.parse_student,
}


def _static_dict(static) -> str:
    return dumps({"errors": [[e.code, e.message, e.courses] for e in static.errors], "bundles": static.bundles,
                  "bundle_of": sorted(static.bundle_of.items())})


def capture() -> tuple[list, dict, unittest.TestResult]:
    """Run the tests, collecting a list of (case line, original call result string) and counts per exclusion reason."""
    records = []
    skipped = {"precheck_mocked": 0, "custom_static": 0}

    def plan_student(catalog, programs, static, student):
        case = None
        if engine.scheduler._precheck is not ORIG_PRECHECK:
            skipped["precheck_mocked"] += 1
        elif _static_dict(static) != _static_dict(ORIG_VALIDATE(catalog, programs)):
            skipped["custom_static"] += 1
        else:
            case = encode(plan_case(catalog, programs, student, current_limits()))
        res = ORIG_PLAN(catalog, programs, static, student)
        if case is not None:
            records.append((case, dumps(result_obj(res))))
        return res

    def validate(catalog, programs):
        case = encode(validate_case(catalog, programs))
        static = ORIG_VALIDATE(catalog, programs)
        errors = [{"code": e.code, "message": e.message, "courses": e.courses} for e in static.errors]
        records.append((case, dumps({"errors": errors, "bundles": static.bundles})))
        return static

    def verify_plan(catalog, programs, student, plan):
        case = encode(verify_case(catalog, programs, student, plan))
        v = ORIG_VERIFY(catalog, programs, student, plan)
        records.append((case, dumps({"violations": v})))
        return v

    def parse_wrapper(which):
        orig = ORIG_PARSE[which]

        def wrapped(raw, *args):
            seasons, where = (args[0], args[1]) if which == "student" else (None, "")
            case = encode(load_case(which, raw, seasons, where))
            try:
                out = orig(raw, *args)
            except engine.loader.InputError as e:
                records.append((case, dumps({"input_error": str(e)})))
                raise
            records.append((case, dumps({"ok": True})))
            return out
        return wrapped

    engine.planner.plan_student = plan_student
    engine.validate.validate = validate
    engine.verify.verify_plan = verify_plan
    for which in ORIG_PARSE:
        setattr(engine.loader, "parse_" + which, parse_wrapper(which))
    try:
        suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"), top_level_dir=os.path.join(ROOT, "tests"))
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    finally:
        engine.planner.plan_student = ORIG_PLAN
        engine.validate.validate = ORIG_VALIDATE
        engine.verify.verify_plan = ORIG_VERIFY
        for which, fn in ORIG_PARSE.items():
            setattr(engine.loader, "parse_" + which, fn)
    return records, skipped, result


# The engine test count. If tests are added or removed, what gets recorded changes, so check and update this.
# Phase 5: 165. Phase 6: +15 (test_ubc.py) +4 (test_oracle_free.py) = 184
EXPECTED_TESTS = 184


def unit_test_cases() -> tuple[list, dict]:
    """A deduplicated list of case lines and a summary. Raises if any case fails the serialization check."""
    records, skipped, result = capture()
    if not result.wasSuccessful() or result.testsRun != EXPECTED_TESTS:
        raise RuntimeError("engine test failure or count change while recording: run=" + str(result.testsRun) + ", failures="
                           + str(len(result.failures)) + ", errors=" + str(len(result.errors)))
    lines = []
    seen = {}
    roundtrip_bad = []
    kinds = {}
    for case, direct in records:
        if case in seen:
            continue
        seen[case] = True
        if python_result(case) != direct:
            roundtrip_bad.append(case)
            continue
        lines.append(case)
        kind = json.loads(case)["kind"]
        kinds[kind] = kinds.get(kind, 0) + 1
    if roundtrip_bad:
        raise RuntimeError("recomputing after serialization differs from the original call: " + str(len(roundtrip_bad)) + " case(s), e.g.: "
                           + roundtrip_bad[0][:300])
    summary = {"tests_run": result.testsRun, "calls": len(records), "unique": len(lines), "kinds": kinds,
               "skipped": skipped}
    return lines, summary


if __name__ == "__main__":
    lines, summary = unit_test_cases()
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
