package engine

import (
	"sort"
	"strings"
)

// Pins down the target course set (Python engine/targets.py).
//
// Sets like selected/completed are kept as map[string]bool, but anywhere order affects the result,
// the code always runs it through SortByCatalog (Python does the same). That way Go's random map
// iteration never leaks into the output.

type Candidate struct {
	Picks   OrderedLists // PICK_N group id -> newly chosen courses
	Courses []string     // courses to schedule (catalog order, completed ones excluded)
}

// AddWithClosure: transitively adds cid, plus any prereq/coreq with only one alternative (i.e. AND), to selected.
func AddWithClosure(catalog *Catalog, cid string, selected, completed map[string]bool) {
	stack := []string{cid}
	for len(stack) > 0 {
		x := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if completed[x] || selected[x] {
			continue
		}
		selected[x] = true
		c := catalog.ByID[x]
		for _, clauses := range [][][]string{c.Prereqs, c.Coreqs} {
			for _, clause := range clauses {
				if len(clause) == 1 {
					stack = append(stack, clause[0])
				}
			}
		}
	}
}

// MarginalCredits: the credits newly added by adding cid (including any AND prerequisites pulled in with it).
func MarginalCredits(catalog *Catalog, cid string, selected, completed map[string]bool) int {
	tmp := copySet(selected)
	AddWithClosure(catalog, cid, tmp, completed)
	total := 0
	for x := range tmp {
		if !selected[x] {
			total += catalog.ByID[x].Credits
		}
	}
	return total
}

func esOr(esGlobal map[string]int, cid string) int {
	if v, ok := esGlobal[cid]; ok {
		return v
	}
	return INF
}

// lessKey: the < of Python's (lexicographic) list comparison.
func lessKey(a, b []int) bool {
	for i := range a {
		if a[i] != b[i] {
			return a[i] < b[i]
		}
	}
	return false
}

// cheapest: minimum added credits -> earliest ES -> catalog order.
// As in Python, "" marks "none yet" (so the result is the same even if a course id happens to be "").
func cheapest(catalog *Catalog, options []string, selected, completed map[string]bool, esGlobal map[string]int) string {
	best := ""
	var bestKey []int
	for _, x := range options {
		key := []int{MarginalCredits(catalog, x, selected, completed), esOr(esGlobal, x), catalog.Index[x]}
		if best == "" || lessKey(key, bestKey) {
			best = x
			bestKey = key
		}
	}
	return best
}

// ResolveOr: fills each not-yet-satisfied OR clause of courses already in the set, one at a time, with its cheapest alternative.
func ResolveOr(catalog *Catalog, selected, completed map[string]bool, esGlobal map[string]int) {
	for {
		var target []string
	scan:
		for _, cid := range SortByCatalog(catalog, keys(selected)) {
			c := catalog.ByID[cid]
			for _, clauses := range [][][]string{c.Prereqs, c.Coreqs} {
				for _, clause := range clauses {
					if len(clause) < 2 {
						continue
					}
					satisfied := false
					for _, x := range clause {
						if completed[x] || selected[x] {
							satisfied = true
						}
					}
					if !satisfied {
						target = clause
						break scan
					}
				}
			}
		}
		if len(target) == 0 {
			return
		}
		AddWithClosure(catalog, cheapest(catalog, target, selected, completed, esGlobal), selected, completed)
	}
}

// Combinations: every way to pick k items from pool (lexicographic, index-based).
func Combinations(pool []string, k int) [][]string {
	out := [][]string{}
	if k > len(pool) {
		return out
	}
	idx := make([]int, k)
	for i := range idx {
		idx[i] = i
	}
	for {
		combo := make([]string, 0, k)
		for _, i := range idx {
			combo = append(combo, pool[i])
		}
		out = append(out, combo)
		// find the rightmost position that can still be incremented
		i := k - 1
		for i >= 0 && idx[i] == len(pool)-k+i {
			i--
		}
		if i < 0 {
			return out
		}
		idx[i]++
		for j := i + 1; j < k; j++ {
			idx[j] = idx[j-1] + 1
		}
	}
}

func countIn(g *Group, selected, completed map[string]bool) int {
	n := 0
	for _, cid := range g.Courses {
		if completed[cid] || selected[cid] {
			n++
		}
	}
	return n
}

// satMul: stands in for Python's unbounded integer multiplication. The result saturates at limit
// (since the combination count is only ever compared against MaxCandidates, limit = MaxCandidates + 1
// gives the same verdict).
func satMul(a, b, limit int) int {
	if a == 0 || b == 0 {
		return 0
	}
	if a >= limit || b >= limit || a > limit/b {
		return limit
	}
	return min(a*b, limit)
}

// GenerateCandidates: the candidate list, plus whether combinatorial explosion forced a greedy fallback.
func GenerateCandidates(catalog *Catalog, groups []Group, completed map[string]bool, esGlobal map[string]int,
	maxCandidates int) ([]Candidate, bool) {
	base := map[string]bool{}
	for _, g := range groups {
		if g.Rule == "ALL" {
			for _, cid := range g.Courses {
				AddWithClosure(catalog, cid, base, completed)
			}
		}
	}

	pickGroups := []*Group{}
	options := [][][]string{} // options[i] = the possible choices for pickGroups[i]
	total := 1
	for i := range groups {
		g := &groups[i]
		if g.Rule != "PICK_N" {
			continue
		}
		need := g.N - countIn(g, base, completed)
		opts := [][]string{{}}
		if need > 0 {
			pool := []string{}
			for _, cid := range g.Courses {
				if !completed[cid] && !base[cid] {
					pool = append(pool, cid)
				}
			}
			opts = Combinations(pool, need)
		}
		pickGroups = append(pickGroups, g)
		options = append(options, opts)
		total = satMul(total, len(opts), maxCandidates+1)
	}

	if total > maxCandidates {
		return []Candidate{greedyCandidate(catalog, base, pickGroups, completed, esGlobal)}, true
	}

	// expand the cartesian product of each group's choices, one group at a time
	combos := [][][]string{{}}
	for _, opts := range options {
		next := [][][]string{}
		for _, prefix := range combos {
			for _, o := range opts {
				c := append(append([][]string{}, prefix...), o)
				next = append(next, c)
			}
		}
		combos = next
	}

	candidates := []Candidate{}
	seen := map[string]bool{}
	for _, combo := range combos {
		selected := copySet(base)
		picks := OrderedLists{}
		for i, g := range pickGroups {
			picks.Set(g.ID, combo[i])
			for _, cid := range combo[i] {
				AddWithClosure(catalog, cid, selected, completed)
			}
		}
		expanded, overflow := ExpandOr(catalog, selected, completed, esGlobal, maxCandidates)
		if overflow {
			return []Candidate{greedyCandidate(catalog, base, pickGroups, completed, esGlobal)}, true
		}
		for _, sel := range expanded {
			courses := SortByCatalog(catalog, keys(sel))
			key := strings.Join(courses, ",")
			if seen[key] {
				continue // a different choice converged to the same course set
			}
			seen[key] = true
			candidates = append(candidates, Candidate{Picks: picks, Courses: courses})
		}
		if len(candidates) > maxCandidates {
			return []Candidate{greedyCandidate(catalog, base, pickGroups, completed, esGlobal)}, true
		}
	}
	return candidates, false
}

func setKey(catalog *Catalog, selected map[string]bool) string {
	return strings.Join(SortByCatalog(catalog, keys(selected)), ",")
}

// nextOrClause: the first not-yet-decided OR clause, in catalog order. ("", nil) if there is none.
func nextOrClause(catalog *Catalog, selected, decided, completed map[string]bool) (string, []string) {
	for _, cid := range SortByCatalog(catalog, keys(selected)) {
		c := catalog.ByID[cid]
		for k, clauses := range [][][]string{c.Prereqs, c.Coreqs} {
			kind := "p"
			if k == 1 {
				kind = "c"
			}
			for i, clause := range clauses {
				if len(clause) < 2 {
					continue
				}
				key := cid + "/" + kind + "/" + itoa(i)
				if decided[key] {
					continue
				}
				done := false
				for _, x := range clause {
					if completed[x] {
						done = true // no alternative beats one already completed (t = -inf)
					}
				}
				if done {
					continue
				}
				return key, clause
			}
		}
	}
	return "", nil
}

// orOptions: alternatives that would satisfy the clause, in preference order: one already in the set
// (zero added cost) first, then the rest ordered by added credits -> ES -> catalog order.
func orOptions(catalog *Catalog, clause []string, selected, completed map[string]bool, esGlobal map[string]int) []string {
	out := []string{}
	for _, x := range clause {
		if selected[x] {
			out = append(out, x)
			break
		}
	}
	rest := []string{}
	for _, x := range clause {
		if !selected[x] {
			rest = append(rest, x)
		}
	}
	restKeys := make(map[string][]int, len(rest))
	for _, x := range rest {
		restKeys[x] = []int{MarginalCredits(catalog, x, selected, completed), esOr(esGlobal, x), catalog.Index[x]}
	}
	sort.SliceStable(rest, func(i, j int) bool { return lessKey(restKeys[rest[i]], restKeys[rest[j]]) })
	return append(out, rest...)
}

type orState struct {
	sel     map[string]bool
	decided map[string]bool
}

// ExpandOr: the course sets from every way of deciding one alternative per OR clause (deduplicated,
// in preference order). If the results exceed limit, returns (results so far, true).
func ExpandOr(catalog *Catalog, selected, completed map[string]bool, esGlobal map[string]int,
	limit int) ([]map[string]bool, bool) {
	out := []map[string]bool{}
	outSeen := map[string]bool{}
	visited := map[string]bool{}
	stack := []orState{{selected, map[string]bool{}}}
	for len(stack) > 0 {
		st := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		decidedIDs := keys(st.decided)
		sort.Strings(decidedIDs)
		state := setKey(catalog, st.sel) + "|" + strings.Join(decidedIDs, ",")
		if visited[state] {
			continue
		}
		visited[state] = true
		if len(visited) > limit*64 {
			return out, true // safety valve for when explored states blow up faster than results
		}

		key, clause := nextOrClause(catalog, st.sel, st.decided, completed)
		if key == "" {
			k := setKey(catalog, st.sel)
			if !outSeen[k] {
				outSeen[k] = true
				out = append(out, st.sel)
				if len(out) > limit {
					return out, true
				}
			}
			continue
		}

		options := orOptions(catalog, clause, st.sel, completed, esGlobal)
		// since this is a stack, push in reverse preference order so the most-preferred alternative expands first
		for i := len(options) - 1; i >= 0; i-- {
			nsel := copySet(st.sel)
			AddWithClosure(catalog, options[i], nsel, completed)
			nd := copySet(st.decided)
			nd[key] = true
			stack = append(stack, orState{nsel, nd})
		}
	}
	return out, false
}

func greedyCandidate(catalog *Catalog, base map[string]bool, pickGroups []*Group, completed map[string]bool,
	esGlobal map[string]int) Candidate {
	selected := copySet(base)
	picks := OrderedLists{}
	for _, g := range pickGroups {
		chosen := []string{}
		picks.Set(g.ID, chosen)
		for countIn(g, selected, completed) < g.N {
			pool := []string{}
			for _, cid := range g.Courses {
				if !completed[cid] && !selected[cid] {
					pool = append(pool, cid)
				}
			}
			best := cheapest(catalog, pool, selected, completed, esGlobal)
			chosen = append(chosen, best)
			picks.Set(g.ID, chosen)
			AddWithClosure(catalog, best, selected, completed)
		}
	}
	ResolveOr(catalog, selected, completed, esGlobal)
	return Candidate{Picks: picks, Courses: SortByCatalog(catalog, keys(selected))}
}
