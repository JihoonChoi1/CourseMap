"""Place a single target course set into terms.

1) Precheck (catch cases where "infeasible" can be proven via the relaxation that ignores the credit cap)
   - E_NOT_OFFERED      : no offering season at all in the remaining terms
   - E_COURSE_OVER_CAP / E_BUNDLE_OVER_CAP
   - E_INFEASIBLE(chain) : ES falls past the last term (also reports the shortest prerequisite-chain path)
   - E_INFEASIBLE(credits) : the total credits of courses that must fall within term window [a,b]
                          (ES>=a, LS<=b) exceeds (b-a+1) x cap. Overload from courses pinned to a
                          single term (S3) is also caught here.
2) Greedy placement: walk terms from the earliest, and repeatedly insert the highest-priority
   placeable Unit as long as the cap allows it (same-term coreqs are resolved by looking at
   whichever course went in first).
3) If greedy fails, search again exhaustively (_exact). If a placement exists it will always be
   found; if not, infeasibility is proven. Only when the search limit (EXACT_NODE_LIMIT) is
   exceeded does it remain "not proven", at which point the cause is diagnosed per unplaced course.
4) Once a placement is found (whether by greedy or exhaustive search), repeatedly exhaustive-search
   a one-term-shorter horizon and shrink (_shrink) until a shorter one is proven infeasible. The
   result is the minimum number of terms for this target set. If the limit is exceeded, the
   shortest placement found so far is used and min_terms_proven=False is set.
"""

from dataclasses import dataclass

from engine.graph import (UnitGraph, build_unit_graph, compute_descendants, compute_es, compute_ls,
                          compute_tail, next_offered_gap)
from engine.model import INF, Calendar, Catalog, EngineError, season_at, season_idx_at, term_label
from engine.validate import StaticResult

# Upper bound on the number of (per-term course combination) nodes explored during exhaustive
# search. If exceeded, the result is reported as "not proven".
EXACT_NODE_LIMIT = 200000


@dataclass
class ScheduleResult:
    feasible: bool
    term_of: dict[str, int]      # placed course -> term index
    errors: list[EngineError]
    unplaced_credits: int
    terms_used: int              # index of the last term with a course + 1
    total_credits: int           # total credits across all target courses
    lower_bound_terms: int       # minimum number of terms needed ignoring the credit cap (for this target set)
    proven: bool                 # if infeasible, whether infeasibility was proven (always True if feasible)
    min_terms_proven: bool       # if feasible, whether terms_used is proven minimal (always True if infeasible)


def schedule(catalog: Catalog, cal: Calendar, cap: int, completed: dict[str, bool],
             course_ids: list[str], static: StaticResult) -> ScheduleResult:
    g = build_unit_graph(catalog, course_ids, completed, static.bundles, static.bundle_of)
    es, crit = compute_es(g, catalog, cal, completed)
    tail = compute_tail(g, cal, es)
    desc = compute_descendants(g, es)

    errors = _precheck(g, cal, cap, es, crit)
    unit_term = _greedy(g, catalog, cal, cap, completed, tail, desc)
    proven = True
    min_proven = True
    if len(errors) == 0:
        budget = [EXACT_NODE_LIMIT]
        status = "found"
        if _count_unplaced(unit_term) > 0:
            found, status = _exact_within(g, catalog, cal, cap, completed, cal.num_terms, budget)
            if status == "found":
                unit_term = found
            elif status == "none":
                errors = [_exact_proof_error(g, cal, unit_term)]
            else:
                proven = False
        if status == "found":
            unit_term, min_proven = _shrink(g, catalog, cal, cap, completed, es, unit_term, budget)

    term_of = {}
    terms_used = 0
    unplaced_credits = 0
    total = 0
    for u, unit in enumerate(g.units):
        total += unit.credits
        if unit_term[u] < 0:
            unplaced_credits += unit.credits
            continue
        for cid in unit.courses:
            term_of[cid] = unit_term[u]
        if unit_term[u] + 1 > terms_used:
            terms_used = unit_term[u] + 1

    if unplaced_credits > 0 and len(errors) == 0:
        errors = _diagnose(g, catalog, cal, cap, completed, unit_term)

    lower = 0
    for v in es:
        if v + 1 > lower:
            lower = v + 1
    return ScheduleResult(
        feasible=unplaced_credits == 0 and len(errors) == 0,
        term_of=term_of,
        errors=errors,
        unplaced_credits=unplaced_credits,
        terms_used=terms_used,
        total_credits=total,
        lower_bound_terms=lower,
        proven=proven,
        min_terms_proven=min_proven,
    )


def _count_unplaced(unit_term: list[int]) -> int:
    n = 0
    for t in unit_term:
        if t < 0:
            n += 1
    return n


# ---------------------------------------------------------------------------
# Precheck
# ---------------------------------------------------------------------------

def _precheck(g: UnitGraph, cal: Calendar, cap: int, es: list[int], crit: list[int]) -> list[EngineError]:
    last = cal.num_terms - 1
    horizon = []
    for t in range(min(cal.num_terms, len(cal.seasons))):
        horizon.append(season_at(cal, t))
    horizon_labels = []
    for t in range(cal.num_terms):
        horizon_labels.append(term_label(cal, t))

    errors = []
    for unit in g.units:
        offered_in_horizon = False
        for s in unit.offered:
            if s in horizon:
                offered_in_horizon = True
        if not offered_in_horizon:
            for cid in unit.courses:
                errors.append(EngineError(
                    "E_NOT_OFFERED",
                    cid + ": " + "/".join(unit.offered) + " 개설 과목인데 남은 학기(" + ", ".join(horizon_labels)
                    + ")에 해당 학기가 없음",
                    [cid],
                ))
        if unit.credits > cap:
            if len(unit.courses) > 1:
                errors.append(EngineError(
                    "E_BUNDLE_OVER_CAP",
                    "번들 {" + ", ".join(unit.courses) + "} 합계 " + str(unit.credits) + "학점 > 학기 상한 " + str(cap),
                    list(unit.courses),
                ))
            else:
                errors.append(EngineError(
                    "E_COURSE_OVER_CAP",
                    unit.id + ": " + str(unit.credits) + "학점 > 학기 상한 " + str(cap),
                    list(unit.courses),
                ))
    if len(errors) > 0:
        return errors  # skip the checks below, since they'd only spill out downstream consequences of the above cause

    # Prerequisite chain length: only look at the earliest root-cause Unit (its descendants are
    # naturally pushed later too, so they're skipped)
    for u, unit in enumerate(g.units):
        if es[u] <= last:
            continue
        if crit[u] >= 0 and es[crit[u]] > last:
            continue
        chain = []
        v = u
        while v >= 0:
            chain.append(g.units[v].id + "(" + term_label(cal, es[v]) + ")")
            v = crit[v]
        chain.reverse()
        errors.append(EngineError(
            "E_INFEASIBLE",
            unit.id + ": 선수과목 체인과 개설 학기상 빨라야 " + term_label(cal, es[u]) + "에 수강 가능하지만 계획은 "
            + term_label(cal, last) + "까지. 최단 체인: " + " → ".join(chain),
            list(unit.courses),
        ))
    if len(errors) > 0:
        return errors

    # Credit demand per term window: any Unit whose [ES, LS] window falls entirely within [a, b]
    # must be placed in that window.
    # Supply: the max credits term t can actually fill = the largest subset-sum, capped, of the
    # Units placeable at t.
    # (e.g. with only 3- and 4-credit courses, a cap of 8 still tops out at 7 in a term with no 4+4 combination)
    ls = compute_ls(g, cal)
    fill = []
    for t in range(cal.num_terms):
        credits = []
        for u in range(len(g.units)):
            if es[u] <= t <= ls[u] and season_at(cal, t) in g.units[u].offered:
                credits.append(g.units[u].credits)
        fill.append(_max_subset_sum(credits, cap))

    min_cap = 0
    best = []       # [a, b, load, member units, supply]
    for a in range(cal.num_terms):
        for b in range(a, cal.num_terms):
            load = 0
            members = []
            for u in range(len(g.units)):
                if es[u] >= a and ls[u] <= b:
                    load += g.units[u].credits
                    members.append(u)
            span = b - a + 1
            need = (load + span - 1) // span
            if need > min_cap:
                min_cap = need
            supply = 0
            for t in range(a, b + 1):
                supply += fill[t]
            if load <= supply:
                continue
            # Prefer the narrowest window; among equal widths, the one with the larger overflow
            if len(best) == 0 or span < best[1] - best[0] + 1 or (
                    span == best[1] - best[0] + 1 and load - supply > best[2] - best[4]):
                best = [a, b, load, members, supply]
    if len(best) > 0:
        a = best[0]
        b = best[1]
        members = best[3]
        supply = best[4]
        members.sort(key=lambda x: (es[x], x))
        parts = []
        courses = []
        for u in members:
            parts.append(g.units[u].id + "(" + str(g.units[u].credits) + ")")
            for cid in g.units[u].courses:
                courses.append(cid)
        span = b - a + 1
        if a == b:
            where = term_label(cal, a) + "에 반드시 배치해야 하는 과목"
        else:
            where = term_label(cal, a) + " ~ " + term_label(cal, b) + " (" + str(span) + "학기) 안에 반드시 배치해야 하는 과목"
        if best[2] > cap * span:  # if simply "number of terms × cap" already explains it, that's more readable
            limit = "학기 상한 " + str(cap) if span == 1 else str(span) + "학기 × 상한 " + str(cap) + " = " + str(supply)
        else:
            fills = []
            for t in range(a, b + 1):
                fills.append(str(fill[t]))
            limit = ("채울 수 있는 최대 " + str(supply) + "학점 (과목 학점 조합상 학기별 최대 " + "+".join(fills)
                     + ", 상한 " + str(cap) + ")")
        if min_cap > cap:
            hint = ". 이 선택 조합에 필요한 최소 학기 상한은 " + str(min_cap) + " 이상"
        else:
            hint = ". 평균으로는 상한이 충분하지만 과목 학점 조합상 학기마다 상한을 꽉 채울 수 없음"
        errors.append(EngineError(
            "E_INFEASIBLE",
            "학점 부족: " + where + " " + " + ".join(parts) + " = " + str(best[2]) + "학점 > " + limit + hint,
            courses,
        ))
    return errors


def _max_subset_sum(credits: list[int], cap: int) -> int:
    """The maximum subset-sum of credits that is <= cap (0/1 knapsack, O(len * cap))."""
    reach = [True]
    for _ in range(cap):
        reach.append(False)
    for c in credits:
        for x in range(cap, c - 1, -1):
            if reach[x - c]:
                reach[x] = True
    for x in range(cap, -1, -1):
        if reach[x]:
            return x
    return 0


# ---------------------------------------------------------------------------
# Greedy placement
# ---------------------------------------------------------------------------

def _ready(g: UnitGraph, catalog: Catalog, u: int, t: int, completed: dict[str, bool],
           unit_term: list[int]) -> bool:
    """Can Unit u be placed in term t (prereq: before t, coreq: at or before t or the same Unit)?"""
    for cid in g.units[u].courses:
        c = catalog.by_id[cid]
        for delta, clauses in ((1, c.prereqs), (0, c.coreqs)):
            for clause in clauses:
                ok = False
                for x in clause:
                    if completed.get(x, False):
                        ok = True
                    elif x in g.unit_of:
                        ux = g.unit_of[x]
                        if ux == u and delta == 0:
                            ok = True
                        elif unit_term[ux] >= 0 and unit_term[ux] + delta <= t:
                            ok = True
                if not ok:
                    return False
    return True


def _priority(g: UnitGraph, catalog: Catalog, cal: Calendar, u: int, t: int,
              tail: list[list[int]], desc: list[int]) -> list[int]:
    """Larger placed first. Lexicographic comparison.

    [0] the point (relative to t) the descendant chain would finish if we defer this to its next
        offered term instead of placing it now = the "cost of deferring", combining remaining
        chain length with the term-offering constraint in one number
    [1] the descendant chain length if placed now
    [2] credits (filling with the larger ones first leaves fewer gaps under the cap)
    [3] number of descendant courses (prefer unblocking more courses)
    [4] catalog order (for determinism)
    """
    unit = g.units[u]
    gap, s_next = next_offered_gap(cal, unit.offered, t)
    deferred = INF
    if gap < INF:
        deferred = gap + tail[u][s_next]
    return [deferred, tail[u][season_idx_at(cal, t)], unit.credits, desc[u], -catalog.index[unit.courses[0]]]


def _greater(a: list[int], b: list[int]) -> bool:
    for i in range(len(a)):
        if a[i] != b[i]:
            return a[i] > b[i]
    return False


def _greedy(g: UnitGraph, catalog: Catalog, cal: Calendar, cap: int, completed: dict[str, bool],
            tail: list[list[int]], desc: list[int]) -> list[int]:
    unit_term = []
    for _ in g.units:
        unit_term.append(-1)
    remaining = len(g.units)
    for t in range(cal.num_terms):
        if remaining == 0:
            break
        season = season_at(cal, t)
        used = 0
        while True:
            best = -1
            best_key = []
            for u, unit in enumerate(g.units):
                if unit_term[u] >= 0 or season not in unit.offered or used + unit.credits > cap:
                    continue
                if not _ready(g, catalog, u, t, completed, unit_term):
                    continue
                key = _priority(g, catalog, cal, u, t, tail, desc)
                if best == -1 or _greater(key, best_key):
                    best = u
                    best_key = key
            if best == -1:
                break
            unit_term[best] = t
            used += g.units[best].credits
            remaining -= 1
    return unit_term


# ---------------------------------------------------------------------------
# Exhaustive search (find a placement when greedy fails + confirm the minimum term count)
#
# DFS that fills terms from the earliest one. For each term, only "maximal" combinations (no
# further Unit can be added) are tried. Deferring a placeable Unit to a later term never helps
# (moving it earlier only loosens the ordering constraints, and the cap for that term has already
# been checked), so trying only maximal combinations is guaranteed to find a placement if one
# exists.
# Failed states of (term, set of placed Units) are memoized and never searched again.
# ---------------------------------------------------------------------------

def _shrink(g: UnitGraph, catalog: Catalog, cal: Calendar, cap: int, completed: dict[str, bool],
            es: list[int], best: list[int], budget: list[int]) -> tuple[list[int], bool]:
    """Repeat exhaustive search with a horizon one term shorter than best to find the minimum-term placement.

    Returns (minimum-term placement, whether minimality is proven). No need to go below the
    prerequisite-chain/term-offering lower bound (max ES + 1).
    """
    lower = 0
    for v in es:
        if v + 1 > lower:
            lower = v + 1
    terms = _terms_used(best) - 1
    while terms >= lower:
        found, status = _exact_within(g, catalog, cal, cap, completed, terms, budget)
        if status == "none":
            return best, True
        if status == "limit":
            return best, False
        best = found
        terms = _terms_used(best) - 1
    return best, True


def _terms_used(unit_term: list[int]) -> int:
    n = 0
    for t in unit_term:
        if t + 1 > n:
            n = t + 1
    return n


def _exact_within(g: UnitGraph, catalog: Catalog, cal: Calendar, cap: int, completed: dict[str, bool],
                  horizon: int, budget: list[int]) -> tuple[list[int], str]:
    """Find a placement that fits everything within terms 0..horizon-1."""
    short = Calendar(seasons=cal.seasons, start_year=cal.start_year, start_idx=cal.start_idx, num_terms=horizon)
    ls = compute_ls(g, short)
    unit_term = []
    for _ in g.units:
        unit_term.append(-1)
    failed = {}
    status = _exact_dfs(g, catalog, cal, cap, completed, horizon, ls, 0, unit_term, failed, budget)
    return unit_term, status


def _state_key(t: int, unit_term: list[int]) -> str:
    parts = [str(t), ":"]
    for x in unit_term:
        parts.append("1" if x >= 0 else "0")
    return "".join(parts)


def _exact_dfs(g: UnitGraph, catalog: Catalog, cal: Calendar, cap: int, completed: dict[str, bool],
               horizon: int, ls: list[int], t: int, unit_term: list[int], failed: dict[str, bool],
               budget: list[int]) -> str:
    remaining = 0
    for u, unit in enumerate(g.units):
        if unit_term[u] < 0:
            remaining += unit.credits
            if ls[u] < t:
                return "none"  # already past the latest term it must be placed in
    if remaining == 0:
        return "found"
    if t >= horizon or remaining > (horizon - t) * cap:
        return "none"
    key = _state_key(t, unit_term)
    if failed.get(key, False):
        return "none"

    # Candidates for this term: unplaced Units that are offered and whose prereqs are satisfied by
    # a prior term (coreqs are checked after the combination is fixed)
    season = season_at(cal, t)
    cand = []
    for u, unit in enumerate(g.units):
        if unit_term[u] < 0 and season in unit.offered and unit.credits <= cap and _prereqs_done(
                g, catalog, u, t, completed, unit_term):
            cand.append(u)

    subsets = []
    _subsets(g, cand, 0, [], 0, cap, subsets)
    subsets.sort(key=lambda s: -s[1])  # combinations that fill the most first (just a search-order heuristic, doesn't affect completeness)
    hit_limit = False
    for chosen, used in subsets:
        budget[0] -= 1
        if budget[0] < 0:
            return "limit"
        for u in chosen:
            unit_term[u] = t
        ok = True
        for u in chosen:
            if not _ready(g, catalog, u, t, completed, unit_term):
                ok = False
                break
        if ok:
            # Maximality: if one more Unit could still be added, skip this combination (the
            # combination with it added is tried separately)
            for v in cand:
                if unit_term[v] >= 0 or used + g.units[v].credits > cap:
                    continue
                unit_term[v] = t
                addable = _ready(g, catalog, v, t, completed, unit_term)
                unit_term[v] = -1
                if addable:
                    ok = False
                    break
        if ok:
            res = _exact_dfs(g, catalog, cal, cap, completed, horizon, ls, t + 1, unit_term, failed, budget)
            if res == "found":
                return "found"
            if res == "limit":
                hit_limit = True
        for u in chosen:
            unit_term[u] = -1
        if hit_limit:
            return "limit"
    failed[key] = True
    return "none"


def _prereqs_done(g: UnitGraph, catalog: Catalog, u: int, t: int, completed: dict[str, bool],
                  unit_term: list[int]) -> bool:
    for cid in g.units[u].courses:
        for clause in catalog.by_id[cid].prereqs:
            ok = False
            for x in clause:
                if completed.get(x, False):
                    ok = True
                elif x in g.unit_of and unit_term[g.unit_of[x]] >= 0 and unit_term[g.unit_of[x]] < t:
                    ok = True
            if not ok:
                return False
    return True


def _subsets(g: UnitGraph, cand: list[int], i: int, chosen: list[int], used: int, cap: int,
             out: list):
    """All subsets of cand whose credit sum is <= cap (including the empty set)."""
    if i == len(cand):
        out.append((list(chosen), used))
        return
    u = cand[i]
    if used + g.units[u].credits <= cap:
        chosen.append(u)
        _subsets(g, cand, i + 1, chosen, used + g.units[u].credits, cap, out)
        chosen.pop()
    _subsets(g, cand, i + 1, chosen, used, cap, out)


def _exact_proof_error(g: UnitGraph, cal: Calendar, greedy_term: list[int]) -> EngineError:
    left = []
    for u, unit in enumerate(g.units):
        if greedy_term[u] < 0:
            for cid in unit.courses:
                left.append(cid)
    return EngineError(
        "E_INFEASIBLE",
        "모든 배치 조합을 탐색한 결과 " + term_label(cal, cal.num_terms - 1) + "까지 배치 불가능 "
        + "(개설 학기·선수과목·학점 상한이 함께 걸려 단순 학점 합계로는 드러나지 않는 경우). "
        + "그리디 배치 기준으로 남는 과목: " + ", ".join(left),
        left,
    )


# ---------------------------------------------------------------------------
# Failure diagnosis (when exhaustive search exceeds its limit)
# ---------------------------------------------------------------------------

def _diagnose(g: UnitGraph, catalog: Catalog, cal: Calendar, cap: int, completed: dict[str, bool],
              unit_term: list[int]) -> list[EngineError]:
    load = []
    for _ in range(cal.num_terms):
        load.append(0)
    for u, unit in enumerate(g.units):
        if unit_term[u] >= 0:
            load[unit_term[u]] += unit.credits

    unplaced = []
    details = []
    for u, unit in enumerate(g.units):
        if unit_term[u] >= 0:
            continue
        for cid in unit.courses:
            unplaced.append(cid)

        # If all prerequisites are placed, compute from when it could have been taken
        ready_from = 0
        blocked = False
        for cid in unit.courses:
            c = catalog.by_id[cid]
            for delta, clauses in ((1, c.prereqs), (0, c.coreqs)):
                for clause in clauses:
                    best = INF
                    for x in clause:
                        if completed.get(x, False):
                            best = 0
                        elif x in g.unit_of:
                            ux = g.unit_of[x]
                            if ux == u:
                                best = min(best, 0)
                            elif unit_term[ux] >= 0:
                                best = min(best, unit_term[ux] + delta)
                    if best >= INF:
                        blocked = True
                    elif best > ready_from:
                        ready_from = best
        if blocked:
            continue  # a prerequisite is unplaced -> this is a downstream consequence, exclude it from the cause list

        slots = []
        for t in range(ready_from, cal.num_terms):
            if season_at(cal, t) in unit.offered:
                slots.append(term_label(cal, t) + " " + str(load[t]) + "/" + str(cap))
        if len(slots) == 0:
            msg = (unit.id + ": 선수과목이 " + term_label(cal, ready_from) + "부터 충족되는데 이후 계획 기간 안에 "
                   + "/".join(unit.offered) + " 학기가 없음")
        else:
            msg = (unit.id + "(" + str(unit.credits) + "학점): 선수과목은 " + term_label(cal, ready_from)
                   + "부터 충족되지만 이후 개설 학기가 모두 학점 상한에 걸림 [" + ", ".join(slots) + "]")
        details.append(EngineError("E_INFEASIBLE", msg, list(unit.courses)))

    summary = EngineError(
        "E_INFEASIBLE",
        "배치안을 찾지 못함. 미배치: " + ", ".join(unplaced)
        + ". (전수탐색이 한도 " + str(EXACT_NODE_LIMIT) + "를 넘어 중단되었으므로 불가능이 증명된 것은 아님)",
        unplaced,
    )
    return [summary] + details
