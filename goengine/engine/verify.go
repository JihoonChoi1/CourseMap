package engine

import "strings"

// Independently checks whether a Plan actually honors the constraints in doc §3 (Python engine/verify.py).
// Doesn't use the engine's internal structures — only looks at the raw catalog/programs/student and the Plan. Returns a list of violations.

// VerifyPlan checks a Plan value (either engine output or JSON read from elsewhere).
// chosen is read-only here, so its order doesn't matter.
func VerifyPlan(catalog *Catalog, programs *Programs, student *Student, plan Plan) []string {
	v := []string{}
	if !plan.Feasible {
		return []string{"feasible=false인 plan은 검증 대상이 아님"}
	}

	completed := map[string]bool{}
	for _, cid := range student.Completed {
		completed[cid] = true
	}

	// term order/labels, credit sums, cap
	terms := plan.Terms
	if len(terms) > student.NumTerms {
		v = append(v, "학기 수 "+itoa(len(terms))+" > num_terms "+itoa(student.NumTerms))
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
			v = append(v, "학기 "+itoa(t)+": "+itoa(term.Year)+" "+term.Season+" (기대 "+itoa(expYear)+" "+expSeason+")")
		}
		total := 0
		for _, cid := range term.Courses {
			c, ok := catalog.ByID[cid]
			if !ok {
				v = append(v, cid+": 카탈로그에 없는 과목")
				continue
			}
			if _, dup := termOf[cid]; dup {
				v = append(v, cid+": 중복 배치")
			} else {
				termOrder = append(termOrder, cid)
			}
			if completed[cid] {
				v = append(v, cid+": 이미 이수한 과목을 배치")
			}
			termOf[cid] = t
			total += c.Credits
			if !contains(c.Offered, term.Season) {
				v = append(v, cid+": "+term.Season+" 미개설")
			}
		}
		if total != term.Credits {
			v = append(v, "학기 "+itoa(t)+": credits 필드 "+itoa(term.Credits)+" != 실제 "+itoa(total))
		}
		if total > student.MaxCredits {
			v = append(v, "학기 "+itoa(t)+": "+itoa(total)+"학점 > 상한 "+itoa(student.MaxCredits))
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
					v = append(v, cid+": "+kind+" ["+strings.Join(clause, " | ")+"] 미충족")
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
		v = append(v, "트랙 없음: "+student.Track)
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
			v = append(v, "그룹 "+g.ID+": "+itoa(have)+"/"+itoa(need)+" 충족")
		}
		if g.Rule == "PICK_N" {
			chosen, _ := plan.Chosen.Get(g.ID)
			for _, cid := range chosen {
				_, placed := termOf[cid]
				if !contains(g.Courses, cid) || !(completed[cid] || placed) {
					v = append(v, "chosen."+g.ID+": "+cid+"는 그룹 과목이 아니거나 이수/배치되지 않음")
				}
			}
			if len(chosen) < g.N {
				v = append(v, "chosen."+g.ID+": "+itoa(len(chosen))+"개 < n="+itoa(g.N))
			}
		}
	}
	return v
}
