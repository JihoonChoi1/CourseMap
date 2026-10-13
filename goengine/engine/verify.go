package engine

import "strings"

// Independently checks whether a Plan actually honors the constraints in doc §3 (Python engine/verify.py).
// Doesn't use the engine's internal structures — only looks at the raw catalog/programs/student and the Plan. Returns a list of violations.

// VerifyPlan checks a Plan value (either engine output or JSON read from elsewhere).
// chosen is read-only here, so its order doesn't matter.
func VerifyPlan(catalog *Catalog, programs *Programs, student *Student, plan Plan) []string {
	v := []string{}
	if !plan.Feasible {
		return []string{"a plan with feasible=false is not verified"}
	}

	completed := map[string]bool{}
	for _, cid := range student.Completed {
		completed[cid] = true
	}

	// term order/labels, credit sums, cap
	terms := plan.Terms
	if len(terms) > student.NumTerms {
		v = append(v, "term count "+itoa(len(terms))+" > num_terms "+itoa(student.NumTerms))
	}
	p := len(catalog.Seasons)
	startIdx := -1
	for i, s := range catalog.Seasons {
		if s == student.StartSeason {
			startIdx = i
			break
		}
	}
	// term_of: keeps first-insertion order like a Python dict (the prereq check below follows this order)
	termOf := map[string]int{}
	termOrder := []string{}
	for t, term := range terms {
		idx := startIdx + t
		expYear := student.StartYear + pyFloorDiv(idx, p)
		expSeason := catalog.Seasons[pyMod(idx, p)]
		if term.Year != expYear || term.Season != expSeason {
			v = append(v, "term "+itoa(t)+": "+itoa(term.Year)+" "+term.Season+" (expected "+itoa(expYear)+" "+expSeason+")")
		}
		total := 0
		for _, cid := range term.Courses {
			c, ok := catalog.ByID[cid]
			if !ok {
				v = append(v, cid+": not in the catalog")
				continue
			}
			if _, dup := termOf[cid]; dup {
				v = append(v, cid+": placed more than once")
			} else {
				termOrder = append(termOrder, cid)
			}
			if completed[cid] {
				v = append(v, cid+": already completed but placed again")
			}
			termOf[cid] = t
			total += c.Credits
			if !contains(c.Offered, term.Season) {
				v = append(v, cid+": "+term.Season+" not offered")
			}
		}
		if total != term.Credits {
			v = append(v, "term "+itoa(t)+": credits field "+itoa(term.Credits)+" != actual "+itoa(total))
		}
		if total > student.MaxCredits {
			v = append(v, "term "+itoa(t)+": "+itoa(total)+" credits > cap "+itoa(student.MaxCredits))
		}
	}

	// prereq (strictly earlier) / coreq (same term or earlier)
	for _, cid := range termOrder {
		c := catalog.ByID[cid]
		t := termOf[cid]
		for k, clauses := range [][][]string{c.Prereqs, c.Coreqs} {
			strict := k == 0
			for _, clause := range clauses {
				ok := false
				for _, x := range clause {
					if completed[x] {
						ok = true
					} else if tx, in := termOf[x]; in {
						if (strict && tx < t) || (!strict && tx <= t) {
							ok = true
						}
					}
				}
				if !ok {
					kind := "coreq"
					if strict {
						kind = "prereq"
					}
					v = append(v, cid+": "+kind+" ["+strings.Join(clause, " | ")+"] not satisfied")
				}
			}
		}
	}

	// degree requirement + track groups
	var track *Program
	for i := range programs.Tracks {
		if programs.Tracks[i].ID == student.Track {
			track = &programs.Tracks[i]
		}
	}
	if track == nil {
		v = append(v, "unknown track: "+student.Track)
		return v
	}
	groups := append(append([]Group{}, programs.Degree.Groups...), track.Groups...)
	for _, g := range groups {
		have := 0
		for _, cid := range g.Courses {
			_, placed := termOf[cid]
			if completed[cid] || placed {
				have++
			}
		}
		need := g.N
		if g.Rule == "ALL" {
			need = len(g.Courses)
		}
		if have < need {
			v = append(v, "group "+g.ID+": "+itoa(have)+"/"+itoa(need)+" satisfied")
		}
		if g.Rule == "PICK_N" {
			chosen, _ := plan.Chosen.Get(g.ID)
			for _, cid := range chosen {
				_, placed := termOf[cid]
				if !contains(g.Courses, cid) || !(completed[cid] || placed) {
					v = append(v, "chosen."+g.ID+": "+cid+" is not in the group, or is neither completed nor placed")
				}
			}
			if len(chosen) < g.N {
				v = append(v, "chosen."+g.ID+": "+itoa(len(chosen))+" chosen < n="+itoa(g.N))
			}
		}
	}
	return v
}
