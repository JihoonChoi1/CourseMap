"""Python (reference) <-> Go engine equivalence comparison.

    python3 parity/run.py                  # full comparison (includes building Go)
    python3 parity/run.py --write-fixtures # after comparing, also refresh the go test fixtures (parity/fixtures/*.jsonl)
    python3 parity/run.py --quick          # fewer fuzz cases, for speed
    python3 parity/run.py --go-bin PATH    # compare against a different Go binary (e.g. a copy with a deliberate bug)

1) For each case set, byte-compare the Python result string against the Go (courseplan --batch) result string.
2) CLI: run cli.py and courseplan with the same arguments and compare stdout, stderr, and exit code.
3) Timing: compare the engine time spent processing the same case set (parsing case JSON + parsing
   input + computing + serializing the result, excluding process startup).
Any mismatch at all results in exit code 1.
"""

import argparse
import json
import os
import subprocess
import sys
import time

from cases import (ROOT, cycle_cases, encode, fuzz_large_cases, fuzz_limit_cases, fuzz_load_cases, fuzz_messy_cases,
                   fuzz_small_cases, fuzz_verify_cases, grid_cases, limit_sweep_cases, or_sweep_cases,
                   python_result, string_cases, ubc_grid_cases, ubc_limit_cases)
from extract import unit_test_cases

GO_DIR = os.path.join(ROOT, "goengine")
GO_BIN = os.path.join(GO_DIR, "bin", "courseplan")
FIXTURE_DIR = os.path.join(ROOT, "parity", "fixtures")
# number of cases to put in the fixtures go test reads (None = all). A full comparison run always regenerates from run.py.
FIXTURE_CAP = {"unit_tests": None, "grid": None, "fuzz_limits": 600, "fuzz_messy": 600, "fuzz_load": 1000,
               "fuzz_verify": 600, "limit_sweep": 31 * 40, "strings": 600, "cycles": 600, "or_sweep": None,
               "heavy": None, "ubc_grid": None, "ubc_limits": None}


def build_go():
    subprocess.run(["go", "build", "-o", GO_BIN, "./cmd/courseplan"], cwd=GO_DIR, check=True)


def run_go(lines: list) -> tuple[list, float]:
    proc = subprocess.run([GO_BIN, "--batch", "--timing"], input="\n".join(lines) + "\n", capture_output=True,
                          text=True)
    out = proc.stdout.split("\n")
    if out and out[-1] == "":
        out.pop()
    timing = json.loads(proc.stderr.strip().split("\n")[-1])
    if len(out) != len(lines):
        raise RuntimeError("Go output line count mismatch: " + str(len(out)) + " != " + str(len(lines)) + "\n" + proc.stderr)
    return out, timing["seconds"]


def compare(name: str, lines: list) -> dict:
    t0 = time.perf_counter()
    expected = [python_result(line) for line in lines]
    py_sec = time.perf_counter() - t0
    got, go_sec = run_go(lines)
    mismatches = []
    for i in range(len(lines)):
        if expected[i] != got[i]:
            mismatches.append(i)
    kinds = {}
    for line in lines:
        k = json.loads(line)["kind"]
        kinds[k] = kinds.get(k, 0) + 1
    for i in mismatches[:3]:
        print("  [" + name + "] mismatch #" + str(i), file=sys.stderr)
        print("    case: " + lines[i][:400], file=sys.stderr)
        print("    py  : " + expected[i][:600], file=sys.stderr)
        print("    go  : " + got[i][:600], file=sys.stderr)
    return {"name": name, "cases": len(lines), "kinds": kinds, "mismatches": len(mismatches), "py_sec": py_sec,
            "go_sec": go_sec, "expected": expected}


def _feasible_count(expected: list) -> tuple[int, int]:
    feasible = 0
    plans = 0
    for e in expected:
        obj = json.loads(e)
        if "plan" in obj:
            plans += 1
            if obj["plan"]["feasible"]:
                feasible += 1
    return plans, feasible


def cli_invocations() -> list:
    out = [["--all"], ["S1"], ["S2"], ["S3"], ["S4"], ["S3", "--max-credits", "7"], ["S4", "--num-terms", "2"],
           ["NOPE"], ["--all", "--max-credits", "0"], ["S1", "--num-terms=3", "--max-credits=9"]]
    for cap in range(3, 17):
        for n in range(1, 9):
            out.append(["--all", "--max-credits", str(cap), "--num-terms", str(n)])
    # Phase 6 real data: course ids with spaces ("CPSC 110"), W1/W2 terms
    ubc = ["--data-dir", "data/ubc"]
    out += [ubc + ["--all"], ubc + ["U1"], ubc + ["U4", "--max-credits", "9", "--num-terms", "6"],
            ubc + ["U5", "--num-terms", "3"]]
    for cap in (6, 12, 15, 18):
        for n in (2, 4, 8):
            out.append(ubc + ["--all", "--max-credits", str(cap), "--num-terms", str(n)])
    return out


def compare_cli() -> dict:
    mismatches = []
    for args in cli_invocations():
        py = subprocess.run([sys.executable, os.path.join(ROOT, "cli.py")] + args, capture_output=True, cwd=ROOT)
        go = subprocess.run([GO_BIN] + args, capture_output=True, cwd=ROOT)
        if (py.returncode, py.stdout, py.stderr) != (go.returncode, go.stdout, go.stderr):
            mismatches.append(" ".join(args))
    for m in mismatches[:3]:
        print("  [cli] mismatch: " + m, file=sys.stderr)
    return {"invocations": len(cli_invocations()), "mismatches": len(mismatches)}


def read_inputs(name: str) -> list:
    """Pre-found input (parity/inputs/<name>.jsonl, see cases.py)."""
    with open(os.path.join(ROOT, "parity", "inputs", name + ".jsonl"), encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f if line.strip()]


def write_fixture(name: str, lines: list, expected: list):
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(os.path.join(FIXTURE_DIR, name + ".jsonl"), "w", encoding="utf-8") as f:
        for line, exp in zip(lines, expected):
            f.write(json.dumps({"case": json.loads(line), "expected": exp}, ensure_ascii=False,
                               separators=(",", ":")) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-fixtures", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--go-bin", help="compare against this binary without building (for validating the harness itself)")
    args = ap.parse_args()
    scale = 10 if args.quick else 1

    global GO_BIN
    if args.go_bin:
        GO_BIN = os.path.abspath(args.go_bin)
    else:
        build_go()
    unit_lines, unit_summary = unit_test_cases()
    print("engine test recording: " + json.dumps(unit_summary, ensure_ascii=False), file=sys.stderr)

    sets = [
        ("unit_tests", unit_lines),
        ("grid", [encode(c) for c in grid_cases()]),
        ("fuzz_small", [encode(c) for c in fuzz_small_cases(5000 // scale, 1)]),
        ("fuzz_limits", [encode(c) for c in fuzz_limit_cases(3000 // scale, 2)]),
        ("fuzz_large", [encode(c) for c in fuzz_large_cases(1000 // scale, 3)]),
        ("fuzz_messy", [encode(c) for c in fuzz_messy_cases(1500 // scale, 4)]),
        ("fuzz_load", [encode(c) for c in fuzz_load_cases(1000 // scale, 5)]),
        ("fuzz_verify", [encode(c) for c in fuzz_verify_cases(1500 // scale, 6)]),
        ("limit_sweep", [encode(c) for c in limit_sweep_cases(200 // scale, 7)]),
        ("strings", [encode(c) for c in string_cases(1000 // scale, 8)]),
        ("cycles", [encode(c) for c in cycle_cases(1000 // scale, 9)]),
        ("or_sweep", [encode(c) for c in or_sweep_cases()]),
        ("heavy", read_inputs("heavy")),
        ("ubc_grid", [encode(c) for c in ubc_grid_cases()]),
        ("ubc_limits", [encode(c) for c in ubc_limit_cases()]),
    ]
    results = []
    failed = False
    for name, lines in sets:
        r = compare(name, lines)
        plans, feasible = _feasible_count(r["expected"])
        results.append(r)
        failed = failed or r["mismatches"] > 0
        ratio = r["py_sec"] / r["go_sec"] if r["go_sec"] > 0 else float("inf")
        print("%-11s cases=%6d mismatches=%d plans=%5d feasible=%5d  python=%7.2fs go=%6.2fs (x%.1f)  %s" % (
            name, r["cases"], r["mismatches"], plans, feasible, r["py_sec"], r["go_sec"], ratio,
            json.dumps(r["kinds"])))
        if args.write_fixtures and name in FIXTURE_CAP:
            cap = FIXTURE_CAP[name] or len(lines)
            write_fixture(name, lines[:cap], r["expected"][:cap])

    cli = compare_cli()
    failed = failed or cli["mismatches"] > 0
    print("cli         invocations=%d mismatches=%d (stdout, stderr, exit code)" % (cli["invocations"], cli["mismatches"]))
    total = sum(r["cases"] for r in results)
    bad = sum(r["mismatches"] for r in results)
    print("total       cases=%d mismatches=%d" % (total, bad))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
