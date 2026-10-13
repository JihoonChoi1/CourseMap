"""Static validation (doc §5 static rules) + SCC-based cycle/bundle detection (doc §4.2).

Looks only at the catalog and programs. Checks that need student info happen in planner/scheduler.
"""

from dataclasses import dataclass

from engine.model import Catalog, EngineError, Programs


@dataclass
class StaticResult:
    errors: list[EngineError]
    bundles: list[list[str]]   # groups of courses that must be placed together in the same term (catalog order)
    bundle_of: dict[str, int]  # course id -> index into bundles


def validate(catalog: Catalog, programs: Programs) -> StaticResult:
    errors = []
    _check_courses(catalog, errors)
    _check_programs(catalog, programs, errors)
    bundles = _check_graph(catalog, errors)
    bundle_of = {}
    for i, b in enumerate(bundles):
        for cid in b:
            bundle_of[cid] = i
    return StaticResult(errors=errors, bundles=bundles, bundle_of=bundle_of)


def _check_courses(catalog: Catalog, errors: list[EngineError]):
    seen = {}
    for c in catalog.courses:
        if c.id in seen:
            errors.append(EngineError("E_DUP_ID", "Duplicate course id: " + c.id, [c.id]))
        seen[c.id] = True

        if c.credits <= 0:
            errors.append(EngineError("E_BAD_CREDITS", c.id + ": credits=" + str(c.credits) + " (must be greater than 0)", [c.id]))

        if len(c.offered) == 0:
            errors.append(EngineError("E_BAD_OFFERED", c.id + ": offered is empty", [c.id]))
        for s in c.offered:
            if s not in catalog.seasons:
                errors.append(EngineError("E_BAD_OFFERED", c.id + ": unknown season '" + s + "'", [c.id]))

        for kind, clauses in (("prereqs", c.prereqs), ("coreqs", c.coreqs)):
            for clause in clauses:
                if len(clause) == 0:
                    errors.append(EngineError("E_EMPTY_CLAUSE", c.id + ": " + kind + " has an empty OR clause", [c.id]))
                for x in clause:
                    if x == c.id:
                        errors.append(EngineError("E_SELF_REF", c.id + ": lists itself as its own " + kind, [c.id]))
                    elif x not in catalog.by_id:
                        errors.append(EngineError("E_UNKNOWN_REF", c.id + ": " + kind + " references an unknown course '" + x + "'", [c.id]))


def _check_programs(catalog: Catalog, programs: Programs, errors: list[EngineError]):
    track_seen = {programs.degree.id: True}
    for t in programs.tracks:
        if t.id in track_seen:
            errors.append(EngineError("E_DUP_ID", "Duplicate track id: " + t.id))
        track_seen[t.id] = True

    # Group ids become keys of Plan.chosen, so they must be globally unique across degree + all tracks
    group_seen = {}
    all_programs = [programs.degree] + programs.tracks
    for p in all_programs:
        for g in p.groups:
            where = p.id + "." + g.id
            if g.id in group_seen:
                errors.append(EngineError("E_DUP_ID", "Duplicate group id: " + g.id))
            group_seen[g.id] = True

            if g.rule == "PICK_N":
                if g.n < 1 or g.n > len(g.courses):
                    errors.append(EngineError("E_BAD_PICK_N", where + ": n=" + str(g.n) + ", course count=" + str(len(g.courses))))
            elif g.rule != "ALL":
                errors.append(EngineError("E_UNKNOWN_RULE", where + ": rule '" + g.rule + "'"))

            for cid in g.courses:
                if cid not in catalog.by_id:
                    errors.append(EngineError("E_UNKNOWN_REF", where + ": unknown course '" + cid + "'", [cid]))


# ---------------------------------------------------------------------------
# Dependency graph (§4.1): if x appears in c's requirement expression, add edge x -> c.
# Edges from prereqs are strict (<), edges from coreqs are weak (<=).
# Every alternative of an OR clause is added as an edge (conservative, §4.3).
# ---------------------------------------------------------------------------

def _build_edges(catalog: Catalog) -> dict[str, dict[str, bool]]:
    """adj[x][c] = whether it's strict. If a pair has both strict and weak, strict wins."""
    adj = {}
    for c in catalog.courses:
        adj[c.id] = {}
    for c in catalog.courses:
        for strict, clauses in ((True, c.prereqs), (False, c.coreqs)):
            for clause in clauses:
                for x in clause:
                    if x == c.id or x not in catalog.by_id:
                        continue  # already reported as E_SELF_REF / E_UNKNOWN_REF
                    if strict or c.id not in adj[x]:
                        adj[x][c.id] = strict or adj[x].get(c.id, False)
    return adj


def _tarjan(nodes: list[str], adj: dict[str, dict[str, bool]]) -> list[list[str]]:
    index_of = {}
    low = {}
    on_stack = {}
    stack = []
    sccs = []
    counter = [0]

    def visit(v: str):
        index_of[v] = counter[0]
        low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack[v] = True
        for w in adj[v]:
            if w not in index_of:
                visit(w)
                low[v] = min(low[v], low[w])
            elif on_stack.get(w, False):
                low[v] = min(low[v], index_of[w])
        if low[v] == index_of[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                comp.append(w)
                if w == v:
                    break
            sccs.append(comp)

    for v in nodes:
        if v not in index_of:
            visit(v)
    return sccs


def _path_within(start: str, goal: str, members: dict[str, bool], adj: dict[str, dict[str, bool]]) -> list[str]:
    """BFS path from start -> goal, staying within the SCC."""
    prev = {start: ""}
    queue = [start]
    head = 0
    while head < len(queue):
        v = queue[head]
        head += 1
        if v == goal:
            break
        for w in adj[v]:
            if members.get(w, False) and w not in prev:
                prev[w] = v
                queue.append(w)
    path = []
    v = goal
    while v != "":
        path.append(v)
        v = prev[v]
    path.reverse()
    return path


def _check_graph(catalog: Catalog, errors: list[EngineError]) -> list[list[str]]:
    nodes = list(catalog.by_id.keys())
    adj = _build_edges(catalog)
    bundles = []
    for comp in _tarjan(nodes, adj):
        if len(comp) < 2:
            continue
        comp.sort(key=lambda cid: catalog.index[cid])
        members = {}
        for cid in comp:
            members[cid] = True

        strict_from = ""
        strict_to = ""
        for x in comp:
            for c in adj[x]:
                if members.get(c, False) and adj[x][c]:
                    strict_from = x
                    strict_to = c
                    break
            if strict_from != "":
                break

        if strict_from != "":
            path = [strict_from] + _path_within(strict_to, strict_from, members, adj)
            errors.append(EngineError(
                "E_CYCLE",
                "Circular dependency (arrow = must be taken first): " + " → ".join(path) + ". " + strict_from + " is a prereq (strictly earlier) of "
                + strict_to + ", so the cycle is a contradiction",
                comp,
            ))
            continue

        # An SCC made up of weak edges only = a bundle. It must have a common offering term.
        common = []
        for s in catalog.seasons:
            ok = True
            for cid in comp:
                if s not in catalog.by_id[cid].offered:
                    ok = False
                    break
            if ok:
                common.append(s)
        if len(common) == 0:
            errors.append(EngineError("E_BUNDLE_NO_TERM", "Bundle {" + ", ".join(comp) + "} has no term in which all its courses are offered", comp))
            continue
        bundles.append(comp)
    return bundles
