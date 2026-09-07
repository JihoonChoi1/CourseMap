"""A second exhaustive-search oracle, independent of both the engine and the other oracle (for real-data scale, Phase 6).

tests/oracle.py expands every n-combination for each PICK_N group and then checks placement for
each target set. When the elective pool is large (like UBC data, e.g. choosing 6 of 16), the
number of target sets grows into the millions and becomes unusable. This oracle never fixes a
target set at all.

Runs a term-ordered DFS, where each term only tries subsets that are "maximal" (no placeable
course can be added), and success is reached once every group (degree requirements + track) is
satisfied after some term finishes.

Considering only maximal subsets is still complete: given a placement P, if a course X placeable
in term t (offered, prereqs satisfied, within cap, coreqs satisfied) sits later than t in P, move
it earlier to t; if it isn't in P at all, add it at t. Moving it earlier only creates slack for
courses that depend on X as a prereq/coreq, and adding a course never breaks any constraint (groups
only require "at least n", and there's no total credit cap or mutual-exclusion in the model). Doing
this repeatedly from the earliest term onward yields a placement where every term is maximal.

Pruning (only when it's certain that "infeasible from here"):
- the max of: credits of not-yet-taken ALL-group courses, + for each PICK_N group (shortfall count
  minus that group's not-yet-completed ALL courses) × the pool's minimum credits
  > remaining terms × cap
- already failed at the same (term, completed set)
"""


def exists_plan_free(catalog, groups, completed_list, start_season, num_terms, cap, node_limit=None):
    """True if a placement exists, False if not. None if node_limit (number of subsets tried) is exceeded."""
    found = find_plan_free(catalog, groups, completed_list, start_season, num_terms, cap, node_limit)
    if found is None:
        return None
    return found is not False


def find_plan_free(catalog, groups, completed_list, start_season, num_terms, cap, node_limit=None):
    """A single placement (a per-term list of course ids, in catalog order), or False. None if the limit is exceeded.

    Since a placement is filled with maximal subsets, it can include courses not actually needed for graduation (useful for counterexample checking).
    """
    completed = set(completed_list)
    ids = [c.id for c in catalog.courses if c.id not in completed]
    bit = {}
    for i, cid in enumerate(ids):
        bit[cid] = 1 << i
    for cid in completed:
        bit[cid] = 0  # counted separately from have_mask below
    credits = [catalog.by_id[cid].credits for cid in ids]
    n = len(ids)
    p = len(catalog.seasons)
    s0 = catalog.seasons.index(start_season)
    season = [catalog.seasons[(s0 + t) % p] for t in range(num_terms)]

    # group satisfaction: completed courses are counted up front
    done_count = []
    for g in groups:
        k = 0
        for cid in g.courses:
            if cid in completed:
                k += 1
        done_count.append(k)
    g_masks = []
    for g in groups:
        m = 0
        for cid in g.courses:
            m |= bit.get(cid, 0)
        g_masks.append(m)
    g_need = [len(g.courses) if g.rule == "ALL" else g.n for g in groups]
    all_mask = 0
    for g in groups:
        if g.rule == "ALL":
            for cid in g.courses:
                all_mask |= bit.get(cid, 0)
    pick_min_credit = []
    for gi, g in enumerate(groups):
        mc = None
        for cid in g.courses:
            if cid not in completed:
                c = catalog.by_id[cid].credits
                if mc is None or c < mc:
                    mc = c
        pick_min_credit.append(mc or 0)

    # requirement bitmasks: an alternative mask per clause. A clause is skipped if a completed alternative satisfies it
    pre = []
    co = []
    for cid in ids:
        c = catalog.by_id[cid]
        ps = []
        cs = []
        for dst, clauses in ((ps, c.prereqs), (cs, c.coreqs)):
            for clause in clauses:
                if any(x in completed for x in clause):
                    continue
                m = 0
                for x in clause:
                    m |= bit.get(x, 0)
                dst.append(m)  # m == 0 means never satisfiable (every alternative is a course not in the catalog)
        pre.append(ps)
        co.append(cs)

    def popcount(x):
        return bin(x).count("1")

    def satisfied(mask):
        for gi in range(len(groups)):
            if done_count[gi] + popcount(mask & g_masks[gi]) < g_need[gi]:
                return False
        return True

    def lower_bound(mask):
        rest_all = all_mask & ~mask
        lb_all = 0
        for i in range(n):
            if rest_all & (1 << i):
                lb_all += credits[i]
        best = lb_all
        for gi, g in enumerate(groups):
            if g.rule == "ALL":
                continue
            need = g_need[gi] - done_count[gi] - popcount(mask & g_masks[gi])
            need -= popcount(rest_all & g_masks[gi])  # the portion already covered by ALL courses that must be taken anyway
            if need > 0:
                v = lb_all + need * pick_min_credit[gi]
                if v > best:
                    best = v
        return best

    failed = set()
    budget = [node_limit if node_limit is not None else -1]
    path = []  # per-term selection mask along the successful path

    def dfs(t, mask):
        if satisfied(mask):
            return True
        if t >= num_terms:
            return False
        if lower_bound(mask) > (num_terms - t) * cap:
            return False
        if (t, mask) in failed:
            return False

        cand = []
        for i in range(n):
            if mask & (1 << i) or credits[i] > cap or season[t] not in catalog.by_id[ids[i]].offered:
                continue
            if all(m & mask for m in pre[i]):
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
            if budget[0] >= 0:
                budget[0] -= 1
                if budget[0] < 0:
                    raise _Limit()
            now = mask | chosen
            ok = True
            for i in cand:
                if chosen & (1 << i) and not all(m & now for m in co[i]):
                    ok = False
                    break
            if not ok:
                continue
            maximal = True
            for i in cand:
                if chosen & (1 << i) or used + credits[i] > cap:
                    continue
                if all(m & (now | (1 << i)) for m in co[i]):
                    maximal = False
                    break
            if not maximal:
                continue
            path.append(chosen)
            if dfs(t + 1, now):
                return True
            path.pop()
        failed.add((t, mask))
        return False

    try:
        ok = dfs(0, 0)
    except _Limit:
        return None
    if not ok:
        return False
    terms = []
    for chosen in path:
        terms.append([ids[i] for i in range(n) if chosen & (1 << i)])
    return terms


class _Limit(Exception):
    pass


def min_terms_free(catalog, groups, completed_list, start_season, max_terms, cap, node_limit=None):
    """The shortest number of terms in 1..max_terms for which a placement exists. 0 if none, None if the limit is exceeded."""
    for t in range(1, max_terms + 1):
        r = exists_plan_free(catalog, groups, completed_list, start_season, t, cap, node_limit)
        if r is None:
            return None
        if r:
            return t
    return 0
