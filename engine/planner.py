"""Build one student's Plan: runtime checks -> generate candidates -> place each candidate -> pick by objective."""

from dataclasses import dataclass

from engine.graph import build_unit_graph, compute_es
from engine.model import (Calendar, Catalog, EngineError, Group, Programs, Student, error_dict, season_at,
                          sort_by_catalog, year_at)
from engine.scheduler import ScheduleResult, schedule
from engine import targets
from engine.targets import Candidate, generate_candidates
from engine.validate import StaticResult


@dataclass
class PlanResult:
    plan: dict                  # shape from doc §2.6 (for JSON output)
    candidates: int             # number of target-set candidates evaluated
    used_fallback: bool         # whether a greedy candidate selection was used due to combination explosion
    picks: dict[str, list[str]] # the PICK_N selections of the final (or representative) candidate
    lower_bound_terms: int      # for the final candidate, the minimum number of terms ignoring the credit cap
    total_credits: int
    min_terms_proven: bool = False  # if feasible, whether "no placement graduates earlier than this" is proven


def make_calendar(catalog: Catalog, student: Student) -> Calendar:
    return Calendar(
        seasons=catalog.seasons,
        start_year=student.start_year,
        start_idx=catalog.seasons.index(student.start_season),
        num_terms=student.num_terms,
    )


def _infeasible(student: Student, errors: list[EngineError]) -> PlanResult:
    plan = {"student_id": student.id, "feasible": False, "terms": [], "chosen": {}, "errors": []}
    for e in errors:
        plan["errors"].append(error_dict(e))
    return PlanResult(plan=plan, candidates=0, used_fallback=False, picks={}, lower_bound_terms=0, total_credits=0)


def plan_student(catalog: Catalog, programs: Programs, static: StaticResult, student: Student) -> PlanResult:
    if len(static.errors) > 0:
        return _infeasible(student, static.errors)

    track = None
    for t in programs.tracks:
        if t.id == student.track:
            track = t
    if track is None:
        return _infeasible(student, [EngineError("E_UNKNOWN_TRACK", student.id + ": 트랙 '" + student.track + "' 없음")])

    completed = {}
    unknown = []
    for cid in student.completed:
        if cid not in catalog.by_id:
            unknown.append(cid)
        completed[cid] = True
    if len(unknown) > 0:
        return _infeasible(student, [EngineError(
            "E_UNKNOWN_REF", student.id + ": completed에 없는 과목 " + ", ".join(unknown), unknown)])

    cal = make_calendar(catalog, student)
    groups = programs.degree.groups + track.groups

    # For OR-alternative tie-breaking: ES computed over every non-completed course
    rest = []
    for c in catalog.courses:
        if not completed.get(c.id, False):
            rest.append(c.id)
    g_all = build_unit_graph(catalog, rest, completed, static.bundles, static.bundle_of)
    es_units, _ = compute_es(g_all, catalog, cal, completed)
    es_global = {}
    for cid in rest:
        es_global[cid] = es_units[g_all.unit_of[cid]]

    candidates, used_fallback = generate_candidates(catalog, groups, completed, es_global)

    best_i = -1
    best_res = None
    unproven = 0
    all_exact = not used_fallback  # whether every candidate's verdict was exact (i.e. minimum terms guaranteed)
    for i, cand in enumerate(candidates):
        res = schedule(catalog, cal, student.max_credits, completed, cand.courses, static)
        if not res.proven:
            unproven += 1
            all_exact = False
        if res.feasible and not res.min_terms_proven:
            all_exact = False
        if best_res is None or _better(res, best_res):
            best_i = i
            best_res = res

    best = candidates[best_i]
    if not best_res.feasible:
        errors = list(best_res.errors)
        if len(candidates) > 1:
            errors.append(EngineError(
                "E_INFEASIBLE",
                "PICK_N/OR 선택 조합 " + str(len(candidates)) + "개 모두 실패. 위 원인은 가장 근접한 조합 기준: "
                + _picks_label(best.picks),
            ))
        # The two cases below are not proof of "infeasible". Flag them even if the representative
        # cause looks like a proof.
        if unproven > 0 and best_res.proven:
            errors.append(EngineError(
                "E_INFEASIBLE",
                "주의: 조합 " + str(unproven) + "개는 전수탐색 한도를 넘어 판정하지 못함 — 불가능이 증명된 것은 아님",
            ))
        if used_fallback:
            errors.append(EngineError(
                "E_INFEASIBLE",
                "주의: PICK_N/OR 선택 조합이 " + str(targets.MAX_CANDIDATES) + "개를 넘어 비용 기준 조합 1개만 시도함 "
                + "— 다른 조합으로는 가능할 수 있으므로 불가능이 증명된 것은 아님",
            ))
        res = _infeasible(student, errors)
        res.candidates = len(candidates)
        res.used_fallback = used_fallback
        res.picks = best.picks
        res.lower_bound_terms = best_res.lower_bound_terms
        res.total_credits = best_res.total_credits
        return res

    return PlanResult(
        plan=_build_plan(catalog, cal, student, groups, completed, best_res),
        candidates=len(candidates),
        used_fallback=used_fallback,
        picks=best.picks,
        lower_bound_terms=best_res.lower_bound_terms,
        total_credits=best_res.total_credits,
        min_terms_proven=all_exact,
    )


def _better(a: ScheduleResult, b: ScheduleResult) -> bool:
    """Is a better than b? Keep the earlier candidate on a tie (determinism)."""
    if a.feasible != b.feasible:
        return a.feasible
    if a.feasible:
        # Objective: 1st priority earliest graduation term, 2nd priority fewest extra credits taken
        if a.terms_used != b.terms_used:
            return a.terms_used < b.terms_used
        return a.total_credits < b.total_credits
    # Both infeasible: use the one with fewer unplaced credits (closest to feasible) as the representative cause
    if a.unplaced_credits != b.unplaced_credits:
        return a.unplaced_credits < b.unplaced_credits
    return a.total_credits < b.total_credits


def _picks_label(picks: dict[str, list[str]]) -> str:
    parts = []
    for gid in picks:
        parts.append(gid + "=[" + ", ".join(picks[gid]) + "]")
    if len(parts) == 0:
        return "(선택 없음)"
    return ", ".join(parts)


def _build_plan(catalog: Catalog, cal: Calendar, student: Student, groups: list[Group],
                completed: dict[str, bool], res: ScheduleResult) -> dict:
    terms = []
    for t in range(res.terms_used):
        ids = []
        credits = 0
        for cid in res.term_of:
            if res.term_of[cid] == t:
                ids.append(cid)
                credits += catalog.by_id[cid].credits
        terms.append({
            "year": year_at(cal, t),
            "season": season_at(cal, t),
            "courses": sort_by_catalog(catalog, ids),
            "credits": credits,
        })

    # Courses satisfying PICK_N groups (completed + planned). A course can appear in multiple groups (§7-1).
    chosen = {}
    for g in groups:
        if g.rule != "PICK_N":
            continue
        chosen[g.id] = []
        for cid in g.courses:
            if completed.get(cid, False) or cid in res.term_of:
                chosen[g.id].append(cid)

    return {"student_id": student.id, "feasible": True, "terms": terms, "chosen": chosen, "errors": []}
