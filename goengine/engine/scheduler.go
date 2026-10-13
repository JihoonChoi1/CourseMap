package engine

import (
	"sort"
	"strings"
)

// Places one target course set into terms (Python engine/scheduler.py).
//
// 1) pre-checks 2) greedy 3) exhaustive search if greedy fails 4) if found, shrink one term at a time to confirm the minimum.
// Exhaustive-search nodes are counted at the same point as in Python (right before trying one subset), so hitting the limit agrees between the two.

type ScheduleResult struct {
	Feasible        bool
	TermOf          map[string]int // placed course -> term index
	Errors          []EngineError
	UnplacedCredits int
	TermsUsed       int  // index of the last term with a course + 1
	TotalCredits    int  // total credits being scheduled
	LowerBoundTerms int  // minimum term count ignoring the credit cap (for this target set)
	Proven          bool // when infeasible, whether infeasibility has been proven (always true when feasible)
	MinTermsProven  bool // when feasible, whether TermsUsed is proven minimal (always true when infeasible)
}

type searchStatus int

const (
	statusFound searchStatus = iota
	statusNone
	statusLimit
)

func Schedule(catalog *Catalog, cal *Calendar, capacity int, completed map[string]bool, courseIDs []string,
	static *StaticResult, limits Limits) *ScheduleResult {
	g := BuildUnitGraph(catalog, courseIDs, completed, static.Bundles, static.BundleOf)
	es, crit := ComputeES(g, catalog, cal, completed)
	tail := ComputeTail(g, cal, es)
	desc := ComputeDescendants(g, es)

	errs := precheck(g, cal, capacity, es, crit)
	unitTerm := greedy(g, catalog, cal, capacity, completed, tail, desc)
	proven := true
	minProven := true
	if len(errs) == 0 {
		budget := limits.ExactNodeLimit
		status := statusFound
		if countUnplaced(unitTerm) > 0 {
			var found []int
			found, status = exactWithin(g, catalog, cal, capacity, completed, cal.NumTerms, &budget)
			if status == statusFound {
				unitTerm = found
			} else if status == statusNone {
				errs = []EngineError{exactProofError(g, cal, unitTerm)}
			} else {
				proven = false
			}
		}
		if status == statusFound {
			unitTerm, minProven = shrink(g, catalog, cal, capacity, completed, es, unitTerm, &budget)
		}
	}

	termOf := map[string]int{}
	termsUsed := 0
	unplacedCredits := 0
	total := 0
	for u, unit := range g.Units {
		total += unit.Credits
		if unitTerm[u] < 0 {
			unplacedCredits += unit.Credits
			continue
		}
		for _, cid := range unit.Courses {
			termOf[cid] = unitTerm[u]
		}
		if unitTerm[u]+1 > termsUsed {
			termsUsed = unitTerm[u] + 1
		}
	}

	if unplacedCredits > 0 && len(errs) == 0 {
		errs = diagnose(g, catalog, cal, capacity, completed, unitTerm, limits.ExactNodeLimit)
	}

	lower := 0
	for _, v := range es {
		if v+1 > lower {
			lower = v + 1
		}
	}
	return &ScheduleResult{
		Feasible:        unplacedCredits == 0 && len(errs) == 0,
		TermOf:          termOf,
		Errors:          errs,
		UnplacedCredits: unplacedCredits,
		TermsUsed:       termsUsed,
		TotalCredits:    total,
		LowerBoundTerms: lower,
		Proven:          proven,
		MinTermsProven:  minProven,
	}
}

func countUnplaced(unitTerm []int) int {
	n := 0
	for _, t := range unitTerm {
		if t < 0 {
			n++
		}
	}
	return n
}

// ---------------------------------------------------------------------------
// Pre-checks
// ---------------------------------------------------------------------------

func precheck(g *UnitGraph, cal *Calendar, capacity int, es, crit []int) []EngineError {
	last := cal.NumTerms - 1
	horizon := []string{}
	for t := 0; t < min(cal.NumTerms, len(cal.Seasons)); t++ {
		horizon = append(horizon, SeasonAt(cal, t))
	}
	horizonLabels := []string{}
	for t := 0; t < cal.NumTerms; t++ {
		horizonLabels = append(horizonLabels, TermLabel(cal, t))
	}

	errs := []EngineError{}
	for _, unit := range g.Units {
		offeredInHorizon := false
		for _, s := range unit.Offered {
			if contains(horizon, s) {
				offeredInHorizon = true
			}
		}
		if !offeredInHorizon {
			for _, cid := range unit.Courses {
				errs = append(errs, EngineError{
					"E_NOT_OFFERED",
					cid + ": " + strings.Join(unit.Offered, "/") + " only, but the remaining terms (" + strings.Join(horizonLabels, ", ") +
						") include none of them",
					[]string{cid},
				})
			}
		}
		if unit.Credits > capacity {
			if len(unit.Courses) > 1 {
				errs = append(errs, EngineError{
					"E_BUNDLE_OVER_CAP",
					"Bundle {" + strings.Join(unit.Courses, ", ") + "} totals " + itoa(unit.Credits) + " credits > per-term cap " + itoa(capacity),
					append([]string{}, unit.Courses...),
				})
			} else {
				errs = append(errs, EngineError{
					"E_COURSE_OVER_CAP",
					unit.ID + ": " + itoa(unit.Credits) + " credits > per-term cap " + itoa(capacity),
					append([]string{}, unit.Courses...),
				})
			}
		}
	}
	if len(errs) > 0 {
		return errs // skip the checks below — they'd only spill derived consequences of the causes above
	}

	// prereq chain length: only look at the earliest Unit that is the actual cause
	for u, unit := range g.Units {
		if es[u] <= last {
			continue
		}
		if crit[u] >= 0 && es[crit[u]] > last {
			continue
		}
		chain := []string{}
		v := u
		for v >= 0 {
			chain = append(chain, g.Units[v].ID+"("+TermLabel(cal, es[v])+")")
			v = crit[v]
		}
		for i, j := 0, len(chain)-1; i < j; i, j = i+1, j-1 {
			chain[i], chain[j] = chain[j], chain[i]
		}
		errs = append(errs, EngineError{
			"E_INFEASIBLE",
			unit.ID + ": the prerequisite chain and term offerings make it available no earlier than " + TermLabel(cal, es[u]) + ", but the plan ends in " +
				TermLabel(cal, last) + ". Shortest chain: " + strings.Join(chain, " → "),
			append([]string{}, unit.Courses...),
		})
	}
	if len(errs) > 0 {
		return errs
	}

	// credit demand vs. supply per term window
	ls := ComputeLS(g, cal)
	fill := []int{}
	for t := 0; t < cal.NumTerms; t++ {
		credits := []int{}
		for u := range g.Units {
			if es[u] <= t && t <= ls[u] && contains(g.Units[u].Offered, SeasonAt(cal, t)) {
				credits = append(credits, g.Units[u].Credits)
			}
		}
		fill = append(fill, maxSubsetSum(credits, capacity))
	}

	minCap := 0
	found := false
	var bestA, bestB, bestLoad, bestSupply int
	var bestMembers []int
	for a := 0; a < cal.NumTerms; a++ {
		for b := a; b < cal.NumTerms; b++ {
			load := 0
			members := []int{}
			for u := range g.Units {
				if es[u] >= a && ls[u] <= b {
					load += g.Units[u].Credits
					members = append(members, u)
				}
			}
			span := b - a + 1
			need := pyFloorDiv(load+span-1, span)
			if need > minCap {
				minCap = need
			}
			supply := 0
			for t := a; t <= b; t++ {
				supply += fill[t]
			}
			if load <= supply {
				continue
			}
			// narrowest window; if tied on width, the one with the bigger overage becomes the representative
			if !found || span < bestB-bestA+1 || (span == bestB-bestA+1 && load-supply > bestLoad-bestSupply) {
				found = true
				bestA, bestB, bestLoad, bestMembers, bestSupply = a, b, load, members, supply
			}
		}
	}
	if found {
		a, b, members, supply := bestA, bestB, bestMembers, bestSupply
		sort.SliceStable(members, func(i, j int) bool {
			if es[members[i]] != es[members[j]] {
				return es[members[i]] < es[members[j]]
			}
			return members[i] < members[j]
		})
		parts := []string{}
		courses := []string{}
		for _, u := range members {
			parts = append(parts, g.Units[u].ID+"("+itoa(g.Units[u].Credits)+")")
			courses = append(courses, g.Units[u].Courses...)
		}
		span := b - a + 1
		var where string
		if a == b {
			where = "courses that must be placed in " + TermLabel(cal, a)
		} else {
			where = "courses that must be placed within " + TermLabel(cal, a) + " ~ " + TermLabel(cal, b) + " (" + itoa(span) + " terms)"
		}
		var limit string
		if bestLoad > capacity*span { // easier to read when a plain "term count × cap" explains it
			if span == 1 {
				limit = "per-term cap " + itoa(capacity)
			} else {
				limit = itoa(span) + " terms × cap " + itoa(capacity) + " = " + itoa(supply)
			}
		} else {
			fills := []string{}
			for t := a; t <= b; t++ {
				fills = append(fills, itoa(fill[t]))
			}
			limit = "at most " + itoa(supply) + " credits that can actually fit (per-term max given course credit sizes: " + strings.Join(fills, "+") +
				", cap " + itoa(capacity) + ")"
		}
		var hint string
		if minCap > capacity {
			hint = ". This combination needs a per-term cap of at least " + itoa(minCap)
		} else {
			hint = ". The cap is enough on average, but course credit sizes keep the terms from being filled to the cap"
		}
		errs = append(errs, EngineError{
			"E_INFEASIBLE",
			"Not enough room: " + where + ": " + strings.Join(parts, " + ") + " = " + itoa(bestLoad) + " credits > " + limit + hint,
			courses,
		})
	}
	return errs
}

// maxSubsetSum: the largest subset sum of credits that is at most capacity (0/1 knapsack).
func maxSubsetSum(credits []int, capacity int) int {
	reach := make([]bool, capacity+1)
	reach[0] = true
	for _, c := range credits {
		for x := capacity; x >= c; x-- {
			if reach[x-c] {
				reach[x] = true
			}
		}
	}
	for x := capacity; x >= 0; x-- {
		if reach[x] {
			return x
		}
	}
	return 0
}

// ---------------------------------------------------------------------------
// Greedy placement
// ---------------------------------------------------------------------------

// ready: can Unit u be placed in term t? (prereq: before t; coreq: at or before t, or the same Unit).
func ready(g *UnitGraph, catalog *Catalog, u, t int, completed map[string]bool, unitTerm []int) bool {
	for _, cid := range g.Units[u].Courses {
		c := catalog.ByID[cid]
		for _, rk := range reqKinds(c) {
			for _, clause := range rk.clauses {
				ok := false
				for _, x := range clause {
					if completed[x] {
						ok = true
					} else if ux, in := g.UnitOf[x]; in {
						if ux == u && rk.delta == 0 {
							ok = true
						} else if unitTerm[ux] >= 0 && unitTerm[ux]+rk.delta <= t {
							ok = true
						}
					}
				}
				if !ok {
					return false
				}
			}
		}
	}
	return true
}

// priority: higher is placed first. Lexicographic comparison (see Python's _priority).
func priority(g *UnitGraph, catalog *Catalog, cal *Calendar, u, t int, tail [][]int, desc []int) []int {
	unit := g.Units[u]
	gap, sNext := NextOfferedGap(cal, unit.Offered, t)
	deferred := INF
	if gap < INF {
		deferred = gap + tail[u][sNext]
	}
	return []int{deferred, tail[u][SeasonIdxAt(cal, t)], unit.Credits, desc[u], -catalog.Index[unit.Courses[0]]}
}

func greater(a, b []int) bool {
	for i := range a {
		if a[i] != b[i] {
			return a[i] > b[i]
		}
	}
	return false
}

func greedy(g *UnitGraph, catalog *Catalog, cal *Calendar, capacity int, completed map[string]bool,
	tail [][]int, desc []int) []int {
	unitTerm := make([]int, len(g.Units))
	for i := range unitTerm {
		unitTerm[i] = -1
	}
	remaining := len(g.Units)
	for t := 0; t < cal.NumTerms; t++ {
		if remaining == 0 {
			break
		}
		season := SeasonAt(cal, t)
		used := 0
		for {
			best := -1
			var bestKey []int
			for u, unit := range g.Units {
				if unitTerm[u] >= 0 || !contains(unit.Offered, season) || used+unit.Credits > capacity {
					continue
				}
				if !ready(g, catalog, u, t, completed, unitTerm) {
					continue
				}
				key := priority(g, catalog, cal, u, t, tail, desc)
				if best == -1 || greater(key, bestKey) {
					best = u
					bestKey = key
				}
			}
			if best == -1 {
				break
			}
			unitTerm[best] = t
			used += g.Units[best].Credits
			remaining--
		}
	}
	return unitTerm
}

// ---------------------------------------------------------------------------
// Exhaustive search (same maximal-combination DFS + failed-state memo as Python)
// ---------------------------------------------------------------------------

// shrink: repeats the exhaustive search over horizons one term shorter than best to find the minimum-term placement.
func shrink(g *UnitGraph, catalog *Catalog, cal *Calendar, capacity int, completed map[string]bool,
	es []int, best []int, budget *int) ([]int, bool) {
	lower := 0
	for _, v := range es {
		if v+1 > lower {
			lower = v + 1
		}
	}
	terms := termsUsed(best) - 1
	for terms >= lower {
		found, status := exactWithin(g, catalog, cal, capacity, completed, terms, budget)
		if status == statusNone {
			return best, true
		}
		if status == statusLimit {
			return best, false
		}
		best = found
		terms = termsUsed(best) - 1
	}
	return best, true
}

func termsUsed(unitTerm []int) int {
	n := 0
	for _, t := range unitTerm {
		if t+1 > n {
			n = t + 1
		}
	}
	return n
}

// exactWithin: finds a placement that fits everything within terms 0..horizon-1.
func exactWithin(g *UnitGraph, catalog *Catalog, cal *Calendar, capacity int, completed map[string]bool,
	horizon int, budget *int) ([]int, searchStatus) {
	short := &Calendar{Seasons: cal.Seasons, StartYear: cal.StartYear, StartIdx: cal.StartIdx, NumTerms: horizon}
	ls := ComputeLS(g, short)
	unitTerm := make([]int, len(g.Units))
	for i := range unitTerm {
		unitTerm[i] = -1
	}
	failed := map[string]bool{}
	status := exactDFS(g, catalog, cal, capacity, completed, horizon, ls, 0, unitTerm, failed, budget)
	return unitTerm, status
}

func stateKey(t int, unitTerm []int) string {
	var b strings.Builder
	b.WriteString(itoa(t))
	b.WriteByte(':')
	for _, x := range unitTerm {
		if x >= 0 {
			b.WriteByte('1')
		} else {
			b.WriteByte('0')
		}
	}
	return b.String()
}

type subset struct {
	chosen []int
	used   int
}

func exactDFS(g *UnitGraph, catalog *Catalog, cal *Calendar, capacity int, completed map[string]bool,
	horizon int, ls []int, t int, unitTerm []int, failed map[string]bool, budget *int) searchStatus {
	remaining := 0
	for u, unit := range g.Units {
		if unitTerm[u] < 0 {
			remaining += unit.Credits
			if ls[u] < t {
				return statusNone // the latest term it could be placed in has already passed
			}
		}
	}
	if remaining == 0 {
		return statusFound
	}
	if t >= horizon || remaining > (horizon-t)*capacity {
		return statusNone
	}
	key := stateKey(t, unitTerm)
	if failed[key] {
		return statusNone
	}

	// candidates for this term: unplaced Units that are offered and whose prereqs are satisfied by earlier terms (coreqs are checked once the combination is fixed)
	season := SeasonAt(cal, t)
	cand := []int{}
	for u, unit := range g.Units {
		if unitTerm[u] < 0 && contains(unit.Offered, season) && unit.Credits <= capacity &&
			prereqsDone(g, catalog, u, t, completed, unitTerm) {
			cand = append(cand, u)
		}
	}

	subsets := []subset{}
	genSubsets(g, cand, 0, []int{}, 0, capacity, &subsets)
	sort.SliceStable(subsets, func(i, j int) bool { return subsets[i].used > subsets[j].used })
	hitLimit := false
	for _, s := range subsets {
		*budget--
		if *budget < 0 {
			return statusLimit
		}
		for _, u := range s.chosen {
			unitTerm[u] = t
		}
		ok := true
		for _, u := range s.chosen {
			if !ready(g, catalog, u, t, completed, unitTerm) {
				ok = false
				break
			}
		}
		if ok {
			// maximality: skip this combination if some other Unit could still be added
			for _, v := range cand {
				if unitTerm[v] >= 0 || s.used+g.Units[v].Credits > capacity {
					continue
				}
				unitTerm[v] = t
				addable := ready(g, catalog, v, t, completed, unitTerm)
				unitTerm[v] = -1
				if addable {
					ok = false
					break
				}
			}
		}
		if ok {
			res := exactDFS(g, catalog, cal, capacity, completed, horizon, ls, t+1, unitTerm, failed, budget)
			if res == statusFound {
				return statusFound
			}
			if res == statusLimit {
				hitLimit = true
			}
		}
		for _, u := range s.chosen {
			unitTerm[u] = -1
		}
		if hitLimit {
			return statusLimit
		}
	}
	failed[key] = true
	return statusNone
}

func prereqsDone(g *UnitGraph, catalog *Catalog, u, t int, completed map[string]bool, unitTerm []int) bool {
	for _, cid := range g.Units[u].Courses {
		for _, clause := range catalog.ByID[cid].Prereqs {
			ok := false
			for _, x := range clause {
				if completed[x] {
					ok = true
				} else if ux, in := g.UnitOf[x]; in && unitTerm[ux] >= 0 && unitTerm[ux] < t {
					ok = true
				}
			}
			if !ok {
				return false
			}
		}
	}
	return true
}

// genSubsets: every subset of cand whose credit sum is at most capacity (including the empty set). Same order as Python's _subsets.
func genSubsets(g *UnitGraph, cand []int, i int, chosen []int, used, capacity int, out *[]subset) {
	if i == len(cand) {
		*out = append(*out, subset{append([]int{}, chosen...), used})
		return
	}
	u := cand[i]
	if used+g.Units[u].Credits <= capacity {
		genSubsets(g, cand, i+1, append(chosen, u), used+g.Units[u].Credits, capacity, out)
	}
	genSubsets(g, cand, i+1, chosen, used, capacity, out)
}

func exactProofError(g *UnitGraph, cal *Calendar, greedyTerm []int) EngineError {
	left := []string{}
	for u, unit := range g.Units {
		if greedyTerm[u] < 0 {
			left = append(left, unit.Courses...)
		}
	}
	return EngineError{
		"E_INFEASIBLE",
		"Exhaustive search found no placement that finishes by " + TermLabel(cal, cal.NumTerms-1) + " " +
			"(term offerings, prerequisites, and the credit cap interact in a way credit totals alone don't show). " +
			"Courses left over by the greedy placement: " + strings.Join(left, ", "),
		left,
	}
}

// ---------------------------------------------------------------------------
// Failure diagnosis (when exhaustive search exceeds its limit)
// ---------------------------------------------------------------------------

func diagnose(g *UnitGraph, catalog *Catalog, cal *Calendar, capacity int, completed map[string]bool,
	unitTerm []int, exactNodeLimit int) []EngineError {
	load := make([]int, cal.NumTerms)
	for u, unit := range g.Units {
		if unitTerm[u] >= 0 {
			load[unitTerm[u]] += unit.Credits
		}
	}

	unplaced := []string{}
	details := []EngineError{}
	for u, unit := range g.Units {
		if unitTerm[u] >= 0 {
			continue
		}
		unplaced = append(unplaced, unit.Courses...)

		// if all its prereqs are placed, compute from when it could have been taken
		readyFrom := 0
		blocked := false
		for _, cid := range unit.Courses {
			c := catalog.ByID[cid]
			for _, rk := range reqKinds(c) {
				for _, clause := range rk.clauses {
					best := INF
					for _, x := range clause {
						if completed[x] {
							best = 0
						} else if ux, in := g.UnitOf[x]; in {
							if ux == u {
								best = min(best, 0)
							} else if unitTerm[ux] >= 0 {
								best = min(best, unitTerm[ux]+rk.delta)
							}
						}
					}
					if best >= INF {
						blocked = true
					} else if best > readyFrom {
						readyFrom = best
					}
				}
			}
		}
		if blocked {
			continue // a prerequisite is unplaced -> this is a derived consequence, so exclude it from the cause list
		}

		slots := []string{}
		for t := readyFrom; t < cal.NumTerms; t++ {
			if contains(unit.Offered, SeasonAt(cal, t)) {
				slots = append(slots, TermLabel(cal, t)+" "+itoa(load[t])+"/"+itoa(capacity))
			}
		}
		var msg string
		if len(slots) == 0 {
			msg = unit.ID + ": prerequisites are met from " + TermLabel(cal, readyFrom) + ", but no " +
				strings.Join(unit.Offered, "/") + " term remains in the plan after that"
		} else {
			msg = unit.ID + "(" + itoa(unit.Credits) + " credits): prerequisites are met from " + TermLabel(cal, readyFrom) +
				", but every later term it is offered in is already at the credit cap [" + strings.Join(slots, ", ") + "]"
		}
		details = append(details, EngineError{"E_INFEASIBLE", msg, append([]string{}, unit.Courses...)})
	}

	summary := EngineError{
		"E_INFEASIBLE",
		"No placement found. Unplaced: " + strings.Join(unplaced, ", ") +
			". (Exhaustive search stopped after exceeding its limit of " + itoa(exactNodeLimit) + " nodes, so infeasibility is not proven)",
		unplaced,
	}
	return append([]EngineError{summary}, details...)
}
