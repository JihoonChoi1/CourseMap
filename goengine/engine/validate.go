package engine

import (
	"sort"
	"strings"
)

// Static validation (doc §5 static rules) + SCC-based cycle/bundle detection (doc §4.2). Python engine/validate.py.

type StaticResult struct {
	Errors   []EngineError
	Bundles  [][]string     // groups of courses that must be placed together in the same term (catalog order)
	BundleOf map[string]int // course id -> index into Bundles
}

func Validate(catalog *Catalog, programs *Programs) *StaticResult {
	errs := []EngineError{}
	checkCourses(catalog, &errs)
	checkPrograms(catalog, programs, &errs)
	bundles := checkGraph(catalog, &errs)
	bundleOf := map[string]int{}
	for i, b := range bundles {
		for _, cid := range b {
			bundleOf[cid] = i
		}
	}
	return &StaticResult{Errors: errs, Bundles: bundles, BundleOf: bundleOf}
}

func checkCourses(catalog *Catalog, errs *[]EngineError) {
	seen := map[string]bool{}
	for _, c := range catalog.Courses {
		if seen[c.ID] {
			*errs = append(*errs, EngineError{"E_DUP_ID", "과목 id 중복: " + c.ID, []string{c.ID}})
		}
		seen[c.ID] = true

		if c.Credits <= 0 {
			*errs = append(*errs, EngineError{"E_BAD_CREDITS", c.ID + ": credits=" + itoa(c.Credits) + " (0보다 커야 함)", []string{c.ID}})
		}

		if len(c.Offered) == 0 {
			*errs = append(*errs, EngineError{"E_BAD_OFFERED", c.ID + ": offered가 비어 있음", []string{c.ID}})
		}
		for _, s := range c.Offered {
			if !contains(catalog.Seasons, s) {
				*errs = append(*errs, EngineError{"E_BAD_OFFERED", c.ID + ": 알 수 없는 season '" + s + "'", []string{c.ID}})
			}
		}

		for k, clauses := range [][][]string{c.Prereqs, c.Coreqs} {
			kind := "prereqs"
			if k == 1 {
				kind = "coreqs"
			}
			for _, clause := range clauses {
				if len(clause) == 0 {
					*errs = append(*errs, EngineError{"E_EMPTY_CLAUSE", c.ID + ": " + kind + "에 빈 OR clause", []string{c.ID}})
				}
				for _, x := range clause {
					if x == c.ID {
						*errs = append(*errs, EngineError{"E_SELF_REF", c.ID + ": 자기 자신을 " + kind + "로 참조", []string{c.ID}})
					} else if _, ok := catalog.ByID[x]; !ok {
						*errs = append(*errs, EngineError{"E_UNKNOWN_REF", c.ID + ": " + kind + "에 없는 과목 '" + x + "'", []string{c.ID}})
					}
				}
			}
		}
	}
}

func checkPrograms(catalog *Catalog, programs *Programs, errs *[]EngineError) {
	trackSeen := map[string]bool{programs.Degree.ID: true}
	for _, t := range programs.Tracks {
		if trackSeen[t.ID] {
			*errs = append(*errs, EngineError{"E_DUP_ID", "트랙 id 중복: " + t.ID, []string{}})
		}
		trackSeen[t.ID] = true
	}

	// Group ids become keys of Plan.chosen, so they must be globally unique across degree + all tracks
	groupSeen := map[string]bool{}
	all := append([]Program{programs.Degree}, programs.Tracks...)
	for _, p := range all {
		for _, g := range p.Groups {
			where := p.ID + "." + g.ID
			if groupSeen[g.ID] {
				*errs = append(*errs, EngineError{"E_DUP_ID", "그룹 id 중복: " + g.ID, []string{}})
			}
			groupSeen[g.ID] = true

			if g.Rule == "PICK_N" {
				if g.N < 1 || g.N > len(g.Courses) {
					*errs = append(*errs, EngineError{"E_BAD_PICK_N", where + ": n=" + itoa(g.N) + ", 과목 수=" + itoa(len(g.Courses)), []string{}})
				}
			} else if g.Rule != "ALL" {
				*errs = append(*errs, EngineError{"E_UNKNOWN_RULE", where + ": rule '" + g.Rule + "'", []string{}})
			}

			for _, cid := range g.Courses {
				if _, ok := catalog.ByID[cid]; !ok {
					*errs = append(*errs, EngineError{"E_UNKNOWN_REF", where + ": 없는 과목 '" + cid + "'", []string{cid}})
				}
			}
		}
	}
}

// ---------------------------------------------------------------------------
// Dependency graph (§4.1): if x appears in c's requirement expression, add edge x -> c.
// Edges from prereqs are strict (<), edges from coreqs are weak (<=).
// To match Python dict[str, dict[str, bool]]'s traversal order (Tarjan visit order, BFS path),
// descendant nodes are kept in a slice in first-insertion order.
// ---------------------------------------------------------------------------

type adjList struct {
	to     []string
	strict map[string]bool
}

func buildEdges(catalog *Catalog) map[string]*adjList {
	adj := map[string]*adjList{}
	for _, c := range catalog.Courses {
		adj[c.ID] = &adjList{strict: map[string]bool{}}
	}
	for _, c := range catalog.Courses {
		for k, clauses := range [][][]string{c.Prereqs, c.Coreqs} {
			strict := k == 0
			for _, clause := range clauses {
				for _, x := range clause {
					if _, ok := catalog.ByID[x]; x == c.ID || !ok {
						continue // already reported as E_SELF_REF / E_UNKNOWN_REF
					}
					a := adj[x]
					cur, exists := a.strict[c.ID]
					if strict || !exists {
						if !exists {
							a.to = append(a.to, c.ID)
						}
						a.strict[c.ID] = strict || cur
					}
				}
			}
		}
	}
	return adj
}

func tarjan(nodes []string, adj map[string]*adjList) [][]string {
	indexOf := map[string]int{}
	low := map[string]int{}
	onStack := map[string]bool{}
	stack := []string{}
	sccs := [][]string{}
	counter := 0

	var visit func(v string)
	visit = func(v string) {
		indexOf[v] = counter
		low[v] = counter
		counter++
		stack = append(stack, v)
		onStack[v] = true
		for _, w := range adj[v].to {
			if _, seen := indexOf[w]; !seen {
				visit(w)
				low[v] = min(low[v], low[w])
			} else if onStack[w] {
				low[v] = min(low[v], indexOf[w])
			}
		}
		if low[v] == indexOf[v] {
			comp := []string{}
			for {
				w := stack[len(stack)-1]
				stack = stack[:len(stack)-1]
				onStack[w] = false
				comp = append(comp, w)
				if w == v {
					break
				}
			}
			sccs = append(sccs, comp)
		}
	}

	for _, v := range nodes {
		if _, seen := indexOf[v]; !seen {
			visit(v)
		}
	}
	return sccs
}

// pathWithin: BFS path from start -> goal, staying within the SCC. As in Python, "" marks "no previous node".
func pathWithin(start, goal string, members map[string]bool, adj map[string]*adjList) []string {
	prev := map[string]string{start: ""}
	queue := []string{start}
	head := 0
	for head < len(queue) {
		v := queue[head]
		head++
		if v == goal {
			break
		}
		for _, w := range adj[v].to {
			if _, seen := prev[w]; members[w] && !seen {
				prev[w] = v
				queue = append(queue, w)
			}
		}
	}
	path := []string{}
	v := goal
	for v != "" {
		path = append(path, v)
		v = prev[v]
	}
	for i, j := 0, len(path)-1; i < j; i, j = i+1, j-1 {
		path[i], path[j] = path[j], path[i]
	}
	return path
}

func checkGraph(catalog *Catalog, errs *[]EngineError) [][]string {
	adj := buildEdges(catalog)
	bundles := [][]string{}
	for _, comp := range tarjan(catalog.IDs, adj) {
		if len(comp) < 2 {
			continue
		}
		sort.SliceStable(comp, func(i, j int) bool { return catalog.Index[comp[i]] < catalog.Index[comp[j]] })
		members := map[string]bool{}
		for _, cid := range comp {
			members[cid] = true
		}

		strictFrom := ""
		strictTo := ""
		for _, x := range comp {
			for _, c := range adj[x].to {
				if members[c] && adj[x].strict[c] {
					strictFrom = x
					strictTo = c
					break
				}
			}
			if strictFrom != "" {
				break
			}
		}

		if strictFrom != "" {
			path := append([]string{strictFrom}, pathWithin(strictTo, strictFrom, members, adj)...)
			*errs = append(*errs, EngineError{
				"E_CYCLE",
				"순환 의존 (화살표 = 먼저 들어야 함): " + strings.Join(path, " → ") + ". " + strictFrom + "은(는) " +
					strictTo + "의 prereq(엄격히 이전)이므로 모순",
				comp,
			})
			continue
		}

		// An SCC made up of weak edges only = a bundle. It must have a common offering term.
		common := []string{}
		for _, s := range catalog.Seasons {
			ok := true
			for _, cid := range comp {
				if !contains(catalog.ByID[cid].Offered, s) {
					ok = false
					break
				}
			}
			if ok {
				common = append(common, s)
			}
		}
		if len(common) == 0 {
			*errs = append(*errs, EngineError{"E_BUNDLE_NO_TERM", "번들 {" + strings.Join(comp, ", ") + "}의 공통 개설 학기가 없음", comp})
			continue
		}
		bundles = append(bundles, comp)
	}
	return bundles
}
