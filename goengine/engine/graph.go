package engine

import "strings"

// The scheduling-unit (Unit) graph and term computations (Python engine/graph.py).
//
// - ES   (earliest start) : the earliest term a placement can happen in, considering only the prerequisite chain + term offerings
// - LS   (latest start)   : the latest term it can be placed in and still finish by the plan's last term
// - tail : the number of additional terms needed until the descendant chain finishes, if this Unit is placed in season s

type Edge struct {
	From  int // unit index
	To    int
	Delta int   // prereq=1 (starting the following term), coreq=0 (same term allowed)
	Hard  bool  // true if this is the only alternative in the set for an OR clause
	Alts  []int // all alternative units from the clause that produced this edge
}

type UnitGraph struct {
	Units  []Unit
	UnitOf map[string]int // course id -> unit index
	Succs  [][]*Edge
	Preds  [][]*Edge
	Order  []int // topological order
}

// reqKinds: iterates Python's ((1, c.prereqs), (0, c.coreqs)).
func reqKinds(c *Course) [2]struct {
	delta   int
	clauses [][]string
} {
	return [2]struct {
		delta   int
		clauses [][]string
	}{{1, c.Prereqs}, {0, c.Coreqs}}
}

func BuildUnitGraph(catalog *Catalog, courseIDs []string, completed map[string]bool,
	bundles [][]string, bundleOf map[string]int) *UnitGraph {
	inSet := map[string]bool{}
	for _, cid := range courseIDs {
		inSet[cid] = true
	}

	units := []Unit{}
	unitOf := map[string]int{}
	for _, cid := range courseIDs {
		if _, ok := unitOf[cid]; ok {
			continue
		}
		members := []string{cid}
		if b, ok := bundleOf[cid]; ok {
			members = []string{}
			for _, m := range bundles[b] {
				if inSet[m] {
					members = append(members, m)
				}
			}
		}
		credits := 0
		for _, m := range members {
			credits += catalog.ByID[m].Credits
		}
		offered := []string{}
		for _, s := range catalog.Seasons {
			ok := true
			for _, m := range members {
				if !contains(catalog.ByID[m].Offered, s) {
					ok = false
				}
			}
			if ok {
				offered = append(offered, s)
			}
		}
		idx := len(units)
		units = append(units, Unit{ID: strings.Join(members, "+"), Courses: members, Credits: credits, Offered: offered})
		for _, m := range members {
			unitOf[m] = idx
		}
	}

	succs := make([][]*Edge, len(units))
	preds := make([][]*Edge, len(units))
	for v, unit := range units {
		for _, cid := range unit.Courses {
			c := catalog.ByID[cid]
			for _, rk := range reqKinds(c) {
				for _, clause := range rk.clauses {
					if clauseDone(clause, completed) {
						continue
					}
					altUnits := []int{}
					internal := false
					for _, x := range clause {
						ux, ok := unitOf[x]
						if !ok {
							continue
						}
						if ux == v {
							internal = true
						} else if !containsInt(altUnits, ux) {
							altUnits = append(altUnits, ux)
						}
					}
					if internal {
						continue // coreq within a bundle: automatically satisfied by same-term placement
					}
					for _, a := range altUnits {
						e := &Edge{From: a, To: v, Delta: rk.delta, Hard: len(altUnits) == 1, Alts: altUnits}
						succs[a] = append(succs[a], e)
						preds[v] = append(preds[v], e)
					}
				}
			}
		}
	}

	return &UnitGraph{Units: units, UnitOf: unitOf, Succs: succs, Preds: preds, Order: topoOrder(units, preds, succs)}
}

func containsInt(list []int, x int) bool {
	for _, v := range list {
		if v == x {
			return true
		}
	}
	return false
}

func clauseDone(clause []string, completed map[string]bool) bool {
	for _, x := range clause {
		if completed[x] {
			return true
		}
	}
	return false
}

// topoOrder: Kahn's algorithm. For a deterministic result, always pick the smallest index among those with in-degree 0.
func topoOrder(units []Unit, preds, succs [][]*Edge) []int {
	indeg := make([]int, len(units))
	for i, p := range preds {
		indeg[i] = len(p)
	}
	done := make([]bool, len(units))
	order := []int{}
	for len(order) < len(units) {
		pick := -1
		for u := range units {
			if !done[u] && indeg[u] == 0 {
				pick = u
				break
			}
		}
		if pick == -1 {
			panic("unit graph has a cycle (should not happen if static validation passed)")
		}
		done[pick] = true
		order = append(order, pick)
		for _, e := range succs[pick] {
			indeg[e.To]--
		}
	}
	return order
}

// FirstOfferedFrom: the first term >= t that is in offered. Ignores the term range (NumTerms).
func FirstOfferedFrom(cal *Calendar, offered []string, t int) int {
	if t >= INF {
		return INF
	}
	for d := 0; d < len(cal.Seasons); d++ {
		if contains(offered, SeasonAt(cal, t+d)) {
			return t + d
		}
	}
	return INF
}

// LastOfferedUntil: the last term <= t that is in offered (may be negative).
func LastOfferedUntil(cal *Calendar, offered []string, t int) int {
	for d := 0; d < len(cal.Seasons); d++ {
		if contains(offered, SeasonAt(cal, t-d)) {
			return t - d
		}
	}
	return -INF
}

// ComputeES: ES, and for each Unit the predecessor Unit that determined its ES (critical predecessor, -1 if none).
func ComputeES(g *UnitGraph, catalog *Catalog, cal *Calendar, completed map[string]bool) ([]int, []int) {
	es := make([]int, len(g.Units))
	crit := make([]int, len(g.Units))
	for i := range crit {
		crit[i] = -1
	}
	for _, v := range g.Order {
		lower := 0
		lowerFrom := -1
		for _, cid := range g.Units[v].Courses {
			c := catalog.ByID[cid]
			for _, rk := range reqKinds(c) {
				for _, clause := range rk.clauses {
					if clauseDone(clause, completed) {
						continue
					}
					best := INF
					bestFrom := -1
					for _, x := range clause {
						ux, ok := g.UnitOf[x]
						if !ok {
							continue
						}
						var cand int
						if ux == v {
							cand = 0
						} else if es[ux] >= INF {
							cand = INF
						} else {
							cand = es[ux] + rk.delta
						}
						if cand < best {
							best = cand
							if ux != v {
								bestFrom = ux
							} else {
								bestFrom = -1
							}
						}
					}
					if best > lower {
						lower = best
						lowerFrom = bestFrom
					}
				}
			}
		}
		es[v] = FirstOfferedFrom(cal, g.Units[v].Offered, lower)
		crit[v] = lowerFrom
	}
	return es, crit
}

// ComputeLS: only hard edges (requirements with a single alternative) are propagated backward.
func ComputeLS(g *UnitGraph, cal *Calendar) []int {
	ls := make([]int, len(g.Units))
	for i := len(g.Order) - 1; i >= 0; i-- {
		u := g.Order[i]
		upper := cal.NumTerms - 1
		for _, e := range g.Succs[u] {
			if e.Hard && ls[e.To]-e.Delta < upper {
				upper = ls[e.To] - e.Delta
			}
		}
		ls[u] = LastOfferedUntil(cal, g.Units[u].Offered, upper)
	}
	return ls
}

// IsBinding: for priority computation (tail, descendant count), does this edge count as "e.From makes e.To wait"?
func IsBinding(e *Edge, es []int) bool {
	if e.Hard {
		return true
	}
	earliest := INF
	for _, a := range e.Alts {
		if es[a] < earliest {
			earliest = es[a]
		}
	}
	return es[e.From] <= earliest
}

// ComputeTail: tail[u][s] = the distance to the last term of the descendant chain if u is placed in the term at season index s.
func ComputeTail(g *UnitGraph, cal *Calendar, es []int) [][]int {
	p := len(cal.Seasons)
	tail := make([][]int, len(g.Units))
	for i := range tail {
		tail[i] = make([]int, p)
	}
	for i := len(g.Order) - 1; i >= 0; i-- {
		u := g.Order[i]
		for s := 0; s < p; s++ {
			worst := 0
			for _, e := range g.Succs[u] {
				if !IsBinding(e, es) {
					continue
				}
				best := INF
				for d := e.Delta; d < e.Delta+p; d++ {
					s2 := (s + d) % p
					if contains(g.Units[e.To].Offered, cal.Seasons[s2]) {
						if d+tail[e.To][s2] < best {
							best = d + tail[e.To][s2]
						}
					}
				}
				if best > worst {
					worst = best
				}
			}
			tail[u][s] = worst
		}
	}
	return tail
}

// ComputeDescendants: the number of descendant Units reachable from each Unit (via IsBinding edges).
func ComputeDescendants(g *UnitGraph, es []int) []int {
	reach := make([]map[int]bool, len(g.Units))
	for i := range reach {
		reach[i] = map[int]bool{}
	}
	for i := len(g.Order) - 1; i >= 0; i-- {
		u := g.Order[i]
		for _, e := range g.Succs[u] {
			if !IsBinding(e, es) {
				continue
			}
			reach[u][e.To] = true
			for w := range reach[e.To] {
				reach[u][w] = true
			}
		}
	}
	counts := make([]int, len(reach))
	for i, r := range reach {
		counts[i] = len(r)
	}
	return counts
}

// NextOfferedGap: the gap d (>=1) to the next term after t that's offered, and that term's season index.
func NextOfferedGap(cal *Calendar, offered []string, t int) (int, int) {
	for d := 1; d <= len(cal.Seasons); d++ {
		if contains(offered, SeasonAt(cal, t+d)) {
			return d, SeasonIdxAt(cal, t+d)
		}
	}
	return INF, 0
}
