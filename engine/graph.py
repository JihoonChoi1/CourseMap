"""The scheduling-unit (Unit) graph and term computations.

For a given course set, build a DAG where each bundle is collapsed into a
single Unit, and compute the following on top of it (all relaxations that
ignore the credit cap):

- ES   (earliest start) : the earliest term a placement can happen in, considering only the prerequisite chain + term offerings
- LS   (latest start)   : the latest term it can be placed in and still finish by the plan's last term
- tail : the number of additional terms needed until the descendant chain finishes, if this Unit is placed in season s

For a catalog that has passed static validation, the SCC-collapsed graph is always a DAG.
"""

from dataclasses import dataclass

from engine.model import INF, Calendar, Catalog, Unit, season_at, season_idx_at


@dataclass
class Edge:
    frm: int      # unit index
    to: int
    delta: int    # prereq=1 (starting the following term), coreq=0 (same term allowed)
    hard: bool    # True if this is the only alternative in the set for an OR clause
    alts: list[int]  # all alternative units from the clause that produced this edge (just [frm] if hard)


@dataclass
class UnitGraph:
    units: list[Unit]
    unit_of: dict[str, int]    # course id -> unit index
    succs: list[list[Edge]]
    preds: list[list[Edge]]
    order: list[int]           # topological order


def build_unit_graph(catalog: Catalog, course_ids: list[str], completed: dict[str, bool],
                     bundles: list[list[str]], bundle_of: dict[str, int]) -> UnitGraph:
    in_set = {}
    for cid in course_ids:
        in_set[cid] = True

    units = []
    unit_of = {}
    for cid in course_ids:
        if cid in unit_of:
            continue
        members = [cid]
        if cid in bundle_of:
            members = []
            for m in bundles[bundle_of[cid]]:
                if in_set.get(m, False):
                    members.append(m)
        credits = 0
        for m in members:
            credits += catalog.by_id[m].credits
        offered = []
        for s in catalog.seasons:
            ok = True
            for m in members:
                if s not in catalog.by_id[m].offered:
                    ok = False
            if ok:
                offered.append(s)
        idx = len(units)
        units.append(Unit(id="+".join(members), courses=members, credits=credits, offered=offered))
        for m in members:
            unit_of[m] = idx

    succs = []
    preds = []
    for _ in units:
        succs.append([])
        preds.append([])
    for v, unit in enumerate(units):
        for cid in unit.courses:
            c = catalog.by_id[cid]
            for delta, clauses in ((1, c.prereqs), (0, c.coreqs)):
                for clause in clauses:
                    if _clause_done(clause, completed):
                        continue
                    alt_units = []
                    internal = False
                    for x in clause:
                        if x not in unit_of:
                            continue
                        if unit_of[x] == v:
                            internal = True
                        elif unit_of[x] not in alt_units:
                            alt_units.append(unit_of[x])
                    if internal:
                        continue  # coreq within a bundle: automatically satisfied by same-term placement
                    for a in alt_units:
                        e = Edge(frm=a, to=v, delta=delta, hard=len(alt_units) == 1, alts=alt_units)
                        succs[a].append(e)
                        preds[v].append(e)

    return UnitGraph(units=units, unit_of=unit_of, succs=succs, preds=preds, order=_topo_order(units, preds, succs))


def _clause_done(clause: list[str], completed: dict[str, bool]) -> bool:
    for x in clause:
        if completed.get(x, False):
            return True
    return False


def _topo_order(units: list[Unit], preds: list[list[Edge]], succs: list[list[Edge]]) -> list[int]:
    """Kahn's algorithm. For a deterministic result, always pick the smallest index among those with in-degree 0."""
    indeg = []
    for p in preds:
        indeg.append(len(p))
    done = []
    for _ in units:
        done.append(False)
    order = []
    while len(order) < len(units):
        pick = -1
        for u in range(len(units)):
            if not done[u] and indeg[u] == 0:
                pick = u
                break
        if pick == -1:
            raise RuntimeError("unit graph has a cycle (should not happen if static validation passed)")
        done[pick] = True
        order.append(pick)
        for e in succs[pick]:
            indeg[e.to] -= 1
    return order


def first_offered_from(cal: Calendar, offered: list[str], t: int) -> int:
    """The first term >= t that is in offered. Ignores the term range (num_terms)."""
    if t >= INF:
        return INF
    for d in range(len(cal.seasons)):
        if season_at(cal, t + d) in offered:
            return t + d
    return INF


def last_offered_until(cal: Calendar, offered: list[str], t: int) -> int:
    """The last term <= t that is in offered (may be negative)."""
    for d in range(len(cal.seasons)):
        if season_at(cal, t - d) in offered:
            return t - d
    return -INF


def compute_es(g: UnitGraph, catalog: Catalog, cal: Calendar, completed: dict[str, bool]) -> tuple[list[int], list[int]]:
    """ES, and for each Unit the predecessor Unit that determined its ES (critical predecessor, -1 if none).

    An OR clause is computed using the earliest alternative in the set (valid as a lower bound since it's a relaxation).
    """
    es = []
    crit = []
    for _ in g.units:
        es.append(0)
        crit.append(-1)
    for v in g.order:
        lower = 0
        lower_from = -1
        for cid in g.units[v].courses:
            c = catalog.by_id[cid]
            for delta, clauses in ((1, c.prereqs), (0, c.coreqs)):
                for clause in clauses:
                    if _clause_done(clause, completed):
                        continue
                    best = INF
                    best_from = -1
                    for x in clause:
                        if x not in g.unit_of:
                            continue
                        ux = g.unit_of[x]
                        if ux == v:
                            cand = 0
                        elif es[ux] >= INF:
                            cand = INF
                        else:
                            cand = es[ux] + delta
                        if cand < best:
                            best = cand
                            best_from = ux if ux != v else -1
                    if best > lower:
                        lower = best
                        lower_from = best_from
        es[v] = first_offered_from(cal, g.units[v].offered, lower)
        crit[v] = lower_from
    return es, crit


def compute_ls(g: UnitGraph, cal: Calendar) -> list[int]:
    """LS. Only hard edges (requirements with a single alternative) are propagated backward."""
    ls = []
    for _ in g.units:
        ls.append(0)
    for i in range(len(g.order) - 1, -1, -1):
        u = g.order[i]
        upper = cal.num_terms - 1
        for e in g.succs[u]:
            if e.hard and ls[e.to] - e.delta < upper:
                upper = ls[e.to] - e.delta
        ls[u] = last_offered_until(cal, g.units[u].offered, upper)
    return ls


def is_binding(e: Edge, es: list[int]) -> bool:
    """For priority computation (tail, descendant count), does this edge count as "e.frm makes e.to wait"?

    An AND requirement (hard) always does. For an OR clause, we assume the alternative with the
    earliest ES satisfies the clause, and only count edges coming from that alternative (all of
    them, if there's a tie).
    Example: for student S2, CS420's [CS310 | CS330] has CS330 (ES 2028 SPRING) earlier than
    CS310 (2028 FALL), so the CS310 -> CS420 edge is not counted in CS310's tail length.
    """
    if e.hard:
        return True
    earliest = INF
    for a in e.alts:
        if es[a] < earliest:
            earliest = es[a]
    return es[e.frm] <= earliest


def compute_tail(g: UnitGraph, cal: Calendar, es: list[int]) -> list[list[int]]:
    """tail[u][s]: the distance to the last term of the descendant chain if u is placed in the term at season index s.

    Reflects descendants' term offerings, so a 1-year gap like "CS210(F) -> CS310(F)" is included in
    the length. Only OR edges that are is_binding are followed.
    """
    p = len(cal.seasons)
    tail = []
    for _ in g.units:
        row = []
        for _ in range(p):
            row.append(0)
        tail.append(row)
    for i in range(len(g.order) - 1, -1, -1):
        u = g.order[i]
        for s in range(p):
            worst = 0
            for e in g.succs[u]:
                if not is_binding(e, es):
                    continue
                best = INF
                for d in range(e.delta, e.delta + p):
                    s2 = (s + d) % p
                    if cal.seasons[s2] in g.units[e.to].offered:
                        if d + tail[e.to][s2] < best:
                            best = d + tail[e.to][s2]
                if best > worst:
                    worst = best
            tail[u][s] = worst
    return tail


def compute_descendants(g: UnitGraph, es: list[int]) -> list[int]:
    """The number of descendant Units reachable from each Unit (via is_binding edges)."""
    reach = []
    for _ in g.units:
        reach.append({})
    for i in range(len(g.order) - 1, -1, -1):
        u = g.order[i]
        for e in g.succs[u]:
            if not is_binding(e, es):
                continue
            reach[u][e.to] = True
            for w in reach[e.to]:
                reach[u][w] = True
    counts = []
    for r in reach:
        counts.append(len(r))
    return counts


def next_offered_gap(cal: Calendar, offered: list[str], t: int) -> tuple[int, int]:
    """The gap d (>=1) to the next term after t that's offered, and that term's season index."""
    for d in range(1, len(cal.seasons) + 1):
        if season_at(cal, t + d) in offered:
            return d, season_idx_at(cal, t + d)
    return INF, 0
