"""Independently check that a Plan actually satisfies the constraints in doc §3.

Doesn't use the engine's internal structures (Unit, ES, etc.) — only looks at the raw
catalog/programs/student and the Plan JSON. Returns violations as a list of strings (empty list = passes).
"""

from engine.model import Catalog, Programs, Student


def verify_plan(catalog: Catalog, programs: Programs, student: Student, plan: dict) -> list[str]:
    v = []
    if not plan.get("feasible", False):
        return ["feasible=false인 plan은 검증 대상이 아님"]

    completed = {}
    for cid in student.completed:
        completed[cid] = True

    # Term order/labels, credit totals, cap
    terms = plan["terms"]
    if len(terms) > student.num_terms:
        v.append("학기 수 " + str(len(terms)) + " > num_terms " + str(student.num_terms))
    p = len(catalog.seasons)
    start_idx = catalog.seasons.index(student.start_season)
    term_of = {}
    for t, term in enumerate(terms):
        idx = start_idx + t
        exp_year = student.start_year + idx // p
        exp_season = catalog.seasons[idx % p]
        if term["year"] != exp_year or term["season"] != exp_season:
            v.append("학기 " + str(t) + ": " + str(term["year"]) + " " + term["season"] + " (기대 "
                     + str(exp_year) + " " + exp_season + ")")
        total = 0
        for cid in term["courses"]:
            if cid not in catalog.by_id:
                v.append(cid + ": 카탈로그에 없는 과목")
                continue
            if cid in term_of:
                v.append(cid + ": 중복 배치")
            if completed.get(cid, False):
                v.append(cid + ": 이미 이수한 과목을 배치")
            term_of[cid] = t
            total += catalog.by_id[cid].credits
            if term["season"] not in catalog.by_id[cid].offered:
                v.append(cid + ": " + term["season"] + " 미개설")
        if total != term["credits"]:
            v.append("학기 " + str(t) + ": credits 필드 " + str(term["credits"]) + " != 실제 " + str(total))
        if total > student.max_credits:
            v.append("학기 " + str(t) + ": " + str(total) + "학점 > 상한 " + str(student.max_credits))

    # prereq (strictly before) / coreq (same term or before)
    for cid in term_of:
        c = catalog.by_id[cid]
        t = term_of[cid]
        for strict, clauses in ((True, c.prereqs), (False, c.coreqs)):
            for clause in clauses:
                ok = False
                for x in clause:
                    if completed.get(x, False):
                        ok = True
                    elif x in term_of:
                        if (strict and term_of[x] < t) or (not strict and term_of[x] <= t):
                            ok = True
                if not ok:
                    kind = "prereq" if strict else "coreq"
                    v.append(cid + ": " + kind + " [" + " | ".join(clause) + "] 미충족")

    # Degree requirements + track groups
    track = None
    for tr in programs.tracks:
        if tr.id == student.track:
            track = tr
    if track is None:
        v.append("트랙 없음: " + student.track)
        return v
    for g in programs.degree.groups + track.groups:
        have = 0
        for cid in g.courses:
            if completed.get(cid, False) or cid in term_of:
                have += 1
        need = len(g.courses) if g.rule == "ALL" else g.n
        if have < need:
            v.append("그룹 " + g.id + ": " + str(have) + "/" + str(need) + " 충족")
        if g.rule == "PICK_N":
            chosen = plan["chosen"].get(g.id, [])
            for cid in chosen:
                if cid not in g.courses or not (completed.get(cid, False) or cid in term_of):
                    v.append("chosen." + g.id + ": " + cid + "는 그룹 과목이 아니거나 이수/배치되지 않음")
            if len(chosen) < g.n:
                v.append("chosen." + g.id + ": " + str(len(chosen)) + "개 < n=" + str(g.n))
    return v
