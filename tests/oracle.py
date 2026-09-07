"""An exhaustive-search oracle independent of the engine (for property testing only).

Decides exactly whether "a placement exists" using only the raw catalog, without touching the
engine module's Unit/ES/priority machinery at all. Only use it on small inputs (dozens of courses,
8 terms or fewer).

1) Enumerate target sets: every n-combination for each PICK_N group × every "alternative that
   actually satisfies this clause" for each OR clause in a requirement. If a placement P exists,
   the set S ⊆ P formed by following the choices P made is guaranteed to be enumerated, and
   restricting P to S is still valid, so "every S is infeasible ⇒ the whole thing is infeasible" holds.
2) For a single set S, run a term-ordered DFS. For each term, only try subsets that are "maximal"
   (no more placeable course remains). Completeness is preserved because deferring a placeable
   course to a later term never helps (moving it earlier only needs that term's cap checked, and
   only loosens ordering constraints).
"""

import itertools


def _and_close(catalog, selected, completed):
    stack = list(selected)
    out = set(selected)
    while stack:
        x = stack.pop()
        c = catalog.by_id[x]
        for clauses in (c.prereqs, c.coreqs):
            for clause in clauses:
                if len(clause) == 1 and clause[0] not in completed and clause[0] not in out:
                    out.add(clause[0])
                    stack.append(clause[0])
    return out


def target_sets(catalog, groups, completed):
    """Every candidate target course set (excluding completed). A deduplicated list of frozensets."""
    base = set()
    pick_options = []
    for g in groups:
        if g.rule == "ALL":
            for cid in g.courses:
                if cid not in completed:
                    base.add(cid)
        else:
            opts = []
            for combo in itertools.combinations(g.courses, g.n):
                opts.append([cid for cid in combo if cid not in completed])
            pick_options.append(opts)

    results = set()

    def expand(selected, resolved):
        selected = _and_close(catalog, selected, completed)
        for cid in sorted(selected):
            c = catalog.by_id[cid]
            for kind, clauses in (("p", c.prereqs), ("c", c.coreqs)):
                for i, clause in enumerate(clauses):
                    key = (cid, kind, i)
                    if len(clause) < 2 or key in resolved:
                        continue
                    if any(x in completed for x in clause):
                        continue
                    for x in clause:
                        expand(selected | {x}, resolved | {key})
                    return
        results.add(frozenset(selected))

    for combo in itertools.product(*pick_options):
        seeds = set(base)
        for chosen in combo:
            seeds.update(chosen)
        expand(seeds, frozenset())
    return sorted(results, key=lambda s: (len(s), sorted(s)))


def set_feasible(catalog, courses, completed, start_season, num_terms, cap):
    """Can every course in courses be placed within num_terms terms?"""
    ids = sorted(courses)
    n = len(ids)
    if n == 0:
        return True
    bit = {cid: 1 << i for i, cid in enumerate(ids)}
    credits = [catalog.by_id[cid].credits for cid in ids]
    total = sum(credits)
    p = len(catalog.seasons)
    s0 = catalog.seasons.index(start_season)
    season = [catalog.seasons[(s0 + t) % p] for t in range(num_terms)]

    # requirements as bitmasks: for each clause, (satisfied by completion?, alternative mask)
    reqs = []
    for cid in ids:
        c = catalog.by_id[cid]
        pre = []
        co = []
        for dst, clauses in ((pre, c.prereqs), (co, c.coreqs)):
            for clause in clauses:
                if any(x in completed for x in clause):
                    continue
                m = 0
                for x in clause:
                    m |= bit.get(x, 0)
                dst.append(m)
        reqs.append((pre, co))

    full = (1 << n) - 1
    failed = set()

    def remaining_credits(mask):
        r = 0
        for i in range(n):
            if not mask & (1 << i):
                r += credits[i]
        return r

    def dfs(t, mask):
        if mask == full:
            return True
        if t >= num_terms:
            return False
        if remaining_credits(mask) > (num_terms - t) * cap:
            return False
        if (t, mask) in failed:
            return False

        # unplaced courses that are offered this term and whose prereqs are satisfied
        cand = []
        for i in range(n):
            if mask & (1 << i) or season[t] not in catalog.by_id[ids[i]].offered:
                continue
            if all(m & mask for m in reqs[i][0]):
                cand.append(i)

        subsets = []

        def gen(k, chosen, used):
            if k == len(cand):
                subsets.append((chosen, used))
                return
            i = cand[k]
            if used + credits[i] <= cap:
                gen(k + 1, chosen | (1 << i), used + credits[i])
            gen(k + 1, chosen, used)

        gen(0, 0, 0)
        subsets.sort(key=lambda x: -x[1])
        for chosen, used in subsets:
            now = mask | chosen
            ok = True
            for i in cand:
                if chosen & (1 << i) and not all(m & now for m in reqs[i][1]):
                    ok = False
                    break
            if not ok:
                continue
            # maximality: skip this subset if there's a course that could still be added on its own
            maximal = True
            for i in cand:
                if chosen & (1 << i) or used + credits[i] > cap:
                    continue
                if all(m & (now | (1 << i)) for m in reqs[i][1]):
                    maximal = False
                    break
            if not maximal:
                continue
            if dfs(t + 1, now):
                return True
        failed.add((t, mask))
        return False

    if total > num_terms * cap:
        return False
    return dfs(0, 0)


def exists_plan(catalog, groups, completed_list, start_season, num_terms, cap):
    completed = set(completed_list)
    for s in target_sets(catalog, groups, completed):
        if set_feasible(catalog, s, completed, start_season, num_terms, cap):
            return True
    return False
