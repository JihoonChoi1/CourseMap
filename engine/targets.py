"""Finalize target course sets.

1) base = the courses in ALL groups + the transitive closure of their AND prerequisites
2) For each PICK_N group, build every combination of choosing exactly the "count still needed
   (need)":
   - Courses already covered by base or by completion count toward that number (a single course
     may satisfy multiple groups at once, §7-1).
   - If the number of combinations is <= MAX_CANDIDATES, make all of them candidates; the
     scheduler actually places each one, and the planner picks by the objective (earliest
     graduation -> fewest credits).
   - If it's exceeded, fall back to a single greedy candidate that minimizes marginal cost
     (including the credits of extra prerequisites pulled in).
3) For each combination, pull in prerequisites transitively, and for each OR clause, expand every
   "alternative that would satisfy this clause" into its own separate candidate (expand_or). Using
   an alternative already in the set is also one of the choices — because if that alternative is
   late timing-wise, adding a different one may be better. The cheapest alternative is expanded
   first, so on a tie in the objective, the same candidate is picked as before (single
   lowest-cost choice). If the candidate count after expanding OR exceeds MAX_CANDIDATES, fall
   back to the single greedy candidate from step 2.
"""

from dataclasses import dataclass

from engine.model import INF, Catalog, Group, sort_by_catalog

MAX_CANDIDATES = 256


@dataclass
class Candidate:
    picks: dict[str, list[str]]  # PICK_N group id -> newly chosen courses
    courses: list[str]           # target courses to place (catalog order, excludes completed)


def add_with_closure(catalog: Catalog, cid: str, selected: dict[str, bool], completed: dict[str, bool]):
    """Add cid, and transitively its single-alternative (=AND) prereqs/coreqs, to selected."""
    stack = [cid]
    while len(stack) > 0:
        x = stack.pop()
        if completed.get(x, False) or selected.get(x, False):
            continue
        selected[x] = True
        c = catalog.by_id[x]
        for clauses in (c.prereqs, c.coreqs):
            for clause in clauses:
                if len(clause) == 1:
                    stack.append(clause[0])


def marginal_credits(catalog: Catalog, cid: str, selected: dict[str, bool], completed: dict[str, bool]) -> int:
    """Additional credits incurred by adding cid (including AND prerequisites pulled in with it)."""
    tmp = dict(selected)
    add_with_closure(catalog, cid, tmp, completed)
    total = 0
    for x in tmp:
        if not selected.get(x, False):
            total += catalog.by_id[x].credits
    return total


def _cheapest(catalog: Catalog, options: list[str], selected: dict[str, bool], completed: dict[str, bool],
              es_global: dict[str, int]) -> str:
    """Fewest additional credits -> earliest ES -> catalog order."""
    best = ""
    best_key = []
    for x in options:
        key = [marginal_credits(catalog, x, selected, completed), es_global.get(x, INF), catalog.index[x]]
        if best == "" or key < best_key:
            best = x
            best_key = key
    return best


def resolve_or(catalog: Catalog, selected: dict[str, bool], completed: dict[str, bool], es_global: dict[str, int]):
    """Fill in, one at a time, each not-yet-satisfied OR clause of a course in the set, using the cheapest alternative.

    Filling one in brings in a new course, so start over from scratch each time (fixed point).
    If an alternative is already in the set, treat the clause as satisfied at zero extra cost.
    """
    while True:
        target = []
        for cid in sort_by_catalog(catalog, list(selected.keys())):
            c = catalog.by_id[cid]
            for clauses in (c.prereqs, c.coreqs):
                for clause in clauses:
                    if len(clause) < 2:
                        continue
                    satisfied = False
                    for x in clause:
                        if completed.get(x, False) or selected.get(x, False):
                            satisfied = True
                    if not satisfied:
                        target = clause
                        break
                if len(target) > 0:
                    break
            if len(target) > 0:
                break
        if len(target) == 0:
            return
        add_with_closure(catalog, _cheapest(catalog, target, selected, completed, es_global), selected, completed)


def combinations(pool: list[str], k: int) -> list[list[str]]:
    """All combinations of choosing k items from pool (lexicographic, index-based)."""
    out = []
    if k > len(pool):
        return out
    idx = []
    for i in range(k):
        idx.append(i)
    while True:
        combo = []
        for i in idx:
            combo.append(pool[i])
        out.append(combo)
        # find the rightmost position that can still be incremented
        i = k - 1
        while i >= 0 and idx[i] == len(pool) - k + i:
            i -= 1
        if i < 0:
            return out
        idx[i] += 1
        for j in range(i + 1, k):
            idx[j] = idx[j - 1] + 1


def _count_in(group: Group, selected: dict[str, bool], completed: dict[str, bool]) -> int:
    n = 0
    for cid in group.courses:
        if completed.get(cid, False) or selected.get(cid, False):
            n += 1
    return n


def generate_candidates(catalog: Catalog, groups: list[Group], completed: dict[str, bool],
                        es_global: dict[str, int]) -> tuple[list[Candidate], bool]:
    """The candidate list, and whether a greedy fallback was used due to combination explosion."""
    base = {}
    for g in groups:
        if g.rule == "ALL":
            for cid in g.courses:
                add_with_closure(catalog, cid, base, completed)

    pick_groups = []
    options = []  # options[i] = list of possible selections for pick_groups[i]
    total = 1
    for g in groups:
        if g.rule != "PICK_N":
            continue
        need = g.n - _count_in(g, base, completed)
        opts = [[]]
        if need > 0:
            pool = []
            for cid in g.courses:
                if not completed.get(cid, False) and not base.get(cid, False):
                    pool.append(cid)
            opts = combinations(pool, need)
        pick_groups.append(g)
        options.append(opts)
        total *= len(opts)

    if total > MAX_CANDIDATES:
        return [_greedy_candidate(catalog, base, pick_groups, completed, es_global)], True

    # progressively expand the cartesian product of each group's options
    combos = [[]]
    for opts in options:
        nxt = []
        for prefix in combos:
            for o in opts:
                nxt.append(prefix + [o])
        combos = nxt

    candidates = []
    seen = {}
    for combo in combos:
        selected = dict(base)
        picks = {}
        for i, g in enumerate(pick_groups):
            picks[g.id] = combo[i]
            for cid in combo[i]:
                add_with_closure(catalog, cid, selected, completed)
        expanded, overflow = expand_or(catalog, selected, completed, es_global, MAX_CANDIDATES)
        if overflow:
            return [_greedy_candidate(catalog, base, pick_groups, completed, es_global)], True
        for sel in expanded:
            courses = sort_by_catalog(catalog, list(sel.keys()))
            key = ",".join(courses)
            if key in seen:
                continue  # a different selection converged to the same course set
            seen[key] = True
            candidates.append(Candidate(picks=picks, courses=courses))
        if len(candidates) > MAX_CANDIDATES:
            return [_greedy_candidate(catalog, base, pick_groups, completed, es_global)], True
    return candidates, False


def _set_key(catalog: Catalog, selected: dict[str, bool]) -> str:
    return ",".join(sort_by_catalog(catalog, list(selected.keys())))


def _next_or_clause(catalog: Catalog, selected: dict[str, bool], decided: dict[str, bool],
                    completed: dict[str, bool]) -> tuple[str, list[str]]:
    """The first OR clause (in catalog order) whose alternative hasn't been decided yet. ("", []) if none."""
    for cid in sort_by_catalog(catalog, list(selected.keys())):
        c = catalog.by_id[cid]
        for kind, clauses in (("p", c.prereqs), ("c", c.coreqs)):
            for i, clause in enumerate(clauses):
                if len(clause) < 2:
                    continue
                key = cid + "/" + kind + "/" + str(i)
                if decided.get(key, False):
                    continue
                done = False
                for x in clause:
                    if completed.get(x, False):
                        done = True  # no alternative can beat one already completed (t = -inf)
                if done:
                    continue
                return key, clause
    return "", []


def _or_options(catalog: Catalog, clause: list[str], selected: dict[str, bool], completed: dict[str, bool],
                es_global: dict[str, int]) -> list[str]:
    """Alternatives that would satisfy clause, in preference order: one already in the set (zero
    extra cost) first, then the rest ordered by additional credits -> ES -> catalog order. Only
    one already-in-set alternative is kept even if several exist, since the resulting set is the same."""
    out = []
    for x in clause:
        if selected.get(x, False):
            out.append(x)
            break
    rest = []
    for x in clause:
        if not selected.get(x, False):
            rest.append(x)
    rest.sort(key=lambda x: [marginal_credits(catalog, x, selected, completed), es_global.get(x, INF),
                             catalog.index[x]])
    return out + rest


def expand_or(catalog: Catalog, selected: dict[str, bool], completed: dict[str, bool], es_global: dict[str, int],
              limit: int) -> tuple[list[dict[str, bool]], bool]:
    """All course sets from expanding every way of deciding one alternative per OR clause (deduped, preference order).

    Deciding one clause can bring in a new course that introduces new OR clauses, so an explicit
    stack-based DFS runs to a fixed point. The same (set, decided clauses) state is visited only once.
    If the result exceeds limit, returns (results so far, True).
    """
    out = []
    out_seen = {}
    visited = {}
    stack = [(selected, {})]
    while len(stack) > 0:
        sel, decided = stack.pop()
        decided_ids = list(decided.keys())
        decided_ids.sort()
        state = _set_key(catalog, sel) + "|" + ",".join(decided_ids)
        if visited.get(state, False):
            continue
        visited[state] = True
        if len(visited) > limit * 64:
            return out, True  # safety valve for when explored states explode faster than results

        key, clause = _next_or_clause(catalog, sel, decided, completed)
        if key == "":
            k = _set_key(catalog, sel)
            if not out_seen.get(k, False):
                out_seen[k] = True
                out.append(sel)
                if len(out) > limit:
                    return out, True
            continue

        options = _or_options(catalog, clause, sel, completed, es_global)
        # since this is a stack, push in reverse preference order so the most-preferred alternative is expanded first
        for i in range(len(options) - 1, -1, -1):
            nsel = dict(sel)
            add_with_closure(catalog, options[i], nsel, completed)
            nd = dict(decided)
            nd[key] = True
            stack.append((nsel, nd))
    return out, False


def _greedy_candidate(catalog: Catalog, base: dict[str, bool], pick_groups: list[Group],
                      completed: dict[str, bool], es_global: dict[str, int]) -> Candidate:
    selected = dict(base)
    picks = {}
    for g in pick_groups:
        picks[g.id] = []
        while _count_in(g, selected, completed) < g.n:
            pool = []
            for cid in g.courses:
                if not completed.get(cid, False) and not selected.get(cid, False):
                    pool.append(cid)
            best = _cheapest(catalog, pool, selected, completed, es_global)
            picks[g.id].append(best)
            add_with_closure(catalog, best, selected, completed)
    resolve_or(catalog, selected, completed, es_global)
    return Candidate(picks=picks, courses=sort_by_catalog(catalog, list(selected.keys())))
