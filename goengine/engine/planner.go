package engine

import "strings"

// Builds a Plan for one student (Python engine/planner.py):
// runtime checks -> candidate generation -> per-candidate scheduling -> selection by objective function.

type Term struct {
	Year    int
	Season  string
	Courses []string
	Credits int
}

// Plan: shape from doc §2.6. JSON key order is student_id, feasible, terms, chosen, errors.
type Plan struct {
	StudentID string
	Feasible  bool
	Terms     []Term
	Chosen    OrderedLists
	Errors    []EngineError
}

type PlanResult struct {
	Plan            Plan
	Candidates      int          // number of target-set candidates evaluated
	UsedFallback    bool         // whether combinatorial explosion forced the greedy candidate choice
	Picks           OrderedLists // PICK_N choices of the final (or representative) candidate
	LowerBoundTerms int          // minimum term count ignoring the credit cap, for the final candidate
	TotalCredits    int
	MinTermsProven  bool // when feasible, whether "no earlier-graduating placement exists" has been proven
}

func MakeCalendar(catalog *Catalog, student *Student) *Calendar {
	startIdx := -1
	for i, s := range catalog.Seasons {
		if s == student.StartSeason {
			startIdx = i
			break
		}
	}
	if startIdx < 0 {
		panic("start_season이 seasons에 없음 (ParseStudent를 통과했다면 발생하지 않음)")
	}
	return &Calendar{Seasons: catalog.Seasons, StartYear: student.StartYear, StartIdx: startIdx, NumTerms: student.NumTerms}
}

func infeasible(student *Student, errs []EngineError) *PlanResult {
	plan := Plan{StudentID: student.ID, Feasible: false, Terms: []Term{}, Errors: append([]EngineError{}, errs...)}
	return &PlanResult{Plan: plan}
}

func PlanStudent(catalog *Catalog, programs *Programs, static *StaticResult, student *Student, limits Limits) *PlanResult {
	if len(static.Errors) > 0 {
		return infeasible(student, static.Errors)
	}

	var track *Program
	for i := range programs.Tracks {
		if programs.Tracks[i].ID == student.Track {
			track = &programs.Tracks[i] // as in Python, keep the last match
		}
	}
	if track == nil {
		return infeasible(student, []EngineError{{"E_UNKNOWN_TRACK", student.ID + ": 트랙 '" + student.Track + "' 없음", []string{}}})
	}

	completed := map[string]bool{}
	unknown := []string{}
	for _, cid := range student.Completed {
		if _, ok := catalog.ByID[cid]; !ok {
			unknown = append(unknown, cid)
		}
		completed[cid] = true
	}
	if len(unknown) > 0 {
		return infeasible(student, []EngineError{{
			"E_UNKNOWN_REF", student.ID + ": completed에 없는 과목 " + strings.Join(unknown, ", "), unknown}})
	}

	cal := MakeCalendar(catalog, student)
	groups := append(append([]Group{}, programs.Degree.Groups...), track.Groups...)

	// for OR alternative tie-breaks: ES computed over all non-completed courses
	rest := []string{}
	for _, c := range catalog.Courses {
		if !completed[c.ID] {
			rest = append(rest, c.ID)
		}
	}
	gAll := BuildUnitGraph(catalog, rest, completed, static.Bundles, static.BundleOf)
	esUnits, _ := ComputeES(gAll, catalog, cal, completed)
	esGlobal := map[string]int{}
	for _, cid := range rest {
		esGlobal[cid] = esUnits[gAll.UnitOf[cid]]
	}

	candidates, usedFallback := GenerateCandidates(catalog, groups, completed, esGlobal, limits.MaxCandidates)

	bestI := -1
	var bestRes *ScheduleResult
	unproven := 0
	allExact := !usedFallback // whether every candidate's verdict was exact (i.e. the min-term guarantee holds)
	for i, cand := range candidates {
		res := Schedule(catalog, cal, student.MaxCredits, completed, cand.Courses, static, limits)
		if !res.Proven {
			unproven++
			allExact = false
		}
		if res.Feasible && !res.MinTermsProven {
			allExact = false
		}
		if bestRes == nil || better(res, bestRes) {
			bestI = i
			bestRes = res
		}
	}

	best := candidates[bestI]
	if !bestRes.Feasible {
		errs := append([]EngineError{}, bestRes.Errors...)
		if len(candidates) > 1 {
			errs = append(errs, EngineError{
				"E_INFEASIBLE",
				"PICK_N/OR 선택 조합 " + itoa(len(candidates)) + "개 모두 실패. 위 원인은 가장 근접한 조합 기준: " +
					picksLabel(best.Picks),
				[]string{},
			})
		}
		// the two cases below are NOT a proof of "infeasible" — flag them even if the representative cause looks like proof.
		if unproven > 0 && bestRes.Proven {
			errs = append(errs, EngineError{
				"E_INFEASIBLE",
				"주의: 조합 " + itoa(unproven) + "개는 전수탐색 한도를 넘어 판정하지 못함 — 불가능이 증명된 것은 아님",
				[]string{},
			})
		}
		if usedFallback {
			errs = append(errs, EngineError{
				"E_INFEASIBLE",
				"주의: PICK_N/OR 선택 조합이 " + itoa(limits.MaxCandidates) + "개를 넘어 비용 기준 조합 1개만 시도함 " +
					"— 다른 조합으로는 가능할 수 있으므로 불가능이 증명된 것은 아님",
				[]string{},
			})
		}
		res := infeasible(student, errs)
		res.Candidates = len(candidates)
		res.UsedFallback = usedFallback
		res.Picks = best.Picks
		res.LowerBoundTerms = bestRes.LowerBoundTerms
		res.TotalCredits = bestRes.TotalCredits
		return res
	}

	return &PlanResult{
		Plan:            buildPlan(catalog, cal, student, groups, completed, bestRes),
		Candidates:      len(candidates),
		UsedFallback:    usedFallback,
		Picks:           best.Picks,
		LowerBoundTerms: bestRes.LowerBoundTerms,
		TotalCredits:    bestRes.TotalCredits,
		MinTermsProven:  allExact,
	}
}

// better: is a better than b? Ties keep the earlier candidate (determinism).
func better(a, b *ScheduleResult) bool {
	if a.Feasible != b.Feasible {
		return a.Feasible
	}
	if a.Feasible {
		// objective: 1st priority earliest graduating term, 2nd priority fewest extra credits taken
		if a.TermsUsed != b.TermsUsed {
			return a.TermsUsed < b.TermsUsed
		}
		return a.TotalCredits < b.TotalCredits
	}
	// both failed: use the one with fewer unplaced credits (closest to succeeding) as the representative cause
	if a.UnplacedCredits != b.UnplacedCredits {
		return a.UnplacedCredits < b.UnplacedCredits
	}
	return a.TotalCredits < b.TotalCredits
}

func picksLabel(picks OrderedLists) string {
	parts := []string{}
	for i, gid := range picks.Keys {
		parts = append(parts, gid+"=["+strings.Join(picks.Vals[i], ", ")+"]")
	}
	if len(parts) == 0 {
		return "(선택 없음)"
	}
	return strings.Join(parts, ", ")
}

func buildPlan(catalog *Catalog, cal *Calendar, student *Student, groups []Group, completed map[string]bool,
	res *ScheduleResult) Plan {
	terms := []Term{}
	for t := 0; t < res.TermsUsed; t++ {
		ids := []string{}
		credits := 0
		for cid, tt := range res.TermOf {
			if tt == t {
				ids = append(ids, cid)
				credits += catalog.ByID[cid].Credits
			}
		}
		terms = append(terms, Term{Year: YearAt(cal, t), Season: SeasonAt(cal, t), Courses: SortByCatalog(catalog, ids), Credits: credits})
	}

	// courses that satisfy a PICK_N group (completed + planned). One course can appear in several groups (§7-1).
	chosen := OrderedLists{}
	for _, g := range groups {
		if g.Rule != "PICK_N" {
			continue
		}
		list := []string{}
		for _, cid := range g.Courses {
			_, placed := res.TermOf[cid]
			if completed[cid] || placed {
				list = append(list, cid)
			}
		}
		chosen.Set(g.ID, list)
	}

	return Plan{StudentID: student.ID, Feasible: true, Terms: terms, Chosen: chosen, Errors: []EngineError{}}
}

// ---------------------------------------------------------------------------
// JSON conversion (same key order as the Python dict)
// ---------------------------------------------------------------------------

func ErrorJSON(e EngineError) *JObj {
	courses := e.Courses
	if courses == nil {
		courses = []string{}
	}
	return Obj().Set("code", e.Code).Set("message", e.Message).Set("courses", courses)
}

func ErrorsJSON(errs []EngineError) []any {
	out := []any{}
	for _, e := range errs {
		out = append(out, ErrorJSON(e))
	}
	return out
}

func (p Plan) JSON() *JObj {
	terms := []any{}
	for _, t := range p.Terms {
		terms = append(terms, Obj().Set("year", t.Year).Set("season", t.Season).Set("courses", t.Courses).Set("credits", t.Credits))
	}
	return Obj().Set("student_id", p.StudentID).Set("feasible", p.Feasible).Set("terms", terms).
		Set("chosen", p.Chosen).Set("errors", ErrorsJSON(p.Errors))
}

// ResultJSON: the Plan plus internal engine info. The output shape for the parity tests and the Celery integration (--stdin-snapshot).
func (r *PlanResult) ResultJSON() *JObj {
	return Obj().Set("plan", r.Plan.JSON()).Set("candidates", r.Candidates).Set("used_fallback", r.UsedFallback).
		Set("picks", r.Picks).Set("lower_bound_terms", r.LowerBoundTerms).Set("total_credits", r.TotalCredits).
		Set("min_terms_proven", r.MinTermsProven)
}
