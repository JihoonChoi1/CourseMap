// Package parity is the case runner used to check against the Python reference implementation
// (parity/run.py, parity_test.go).
//
// It takes one case line (JSON) and returns the result as a string formatted like
// Python's json.dumps(..., ensure_ascii=False). The Python side (python_result in
// parity/cases.py) compares byte-for-byte against the string it builds for the same case.
//
// Case kinds:
//
//	plan     {"kind":"plan","snapshot":{"catalog","programs","student"},"limits":{...}}
//	         -> {"plan","candidates","used_fallback","picks","lower_bound_terms","total_credits","min_terms_proven"}
//	validate {"kind":"validate","catalog","programs"}               -> {"errors","bundles"}
//	verify   {"kind":"verify","snapshot":{...},"plan":{...}}        -> {"violations"}
//	load     {"kind":"load","which":"catalog"|"programs"|"student","raw","seasons","where"} -> {"ok": true}
//
// A malformed input, for any kind, is {"input_error": message}.
package parity

import (
	"encoding/json"
	"errors"

	"coursemap/goengine/engine"
)

type caseLine struct {
	Kind     string          `json:"kind"`
	Snapshot json.RawMessage `json:"snapshot"`
	Limits   *struct {
		ExactNodeLimit int `json:"exact_node_limit"`
		MaxCandidates  int `json:"max_candidates"`
	} `json:"limits"`
	Catalog  json.RawMessage `json:"catalog"`
	Programs json.RawMessage `json:"programs"`
	Plan     json.RawMessage `json:"plan"`
	Which    string          `json:"which"`
	Raw      json.RawMessage `json:"raw"`
	Seasons  []string        `json:"seasons"`
	Where    string          `json:"where"`
}

// RunCase: one case line -> result string. Returns error only if the case itself is malformed (a harness bug).
func RunCase(line []byte) (string, error) {
	var c caseLine
	if err := json.Unmarshal(line, &c); err != nil {
		return "", err
	}
	limits := engine.DefaultLimits
	if c.Limits != nil {
		limits = engine.Limits{ExactNodeLimit: c.Limits.ExactNodeLimit, MaxCandidates: c.Limits.MaxCandidates}
	}
	switch c.Kind {
	case "plan":
		catalog, programs, student, err := ParseSnapshot(c.Snapshot)
		if err != nil {
			return inputError(err)
		}
		res := engine.PlanStudent(catalog, programs, engine.Validate(catalog, programs), student, limits)
		return engine.Dumps(res.ResultJSON(), -1), nil
	case "validate":
		catalog, programs, err := parseCatalogPrograms(c.Catalog, c.Programs)
		if err != nil {
			return inputError(err)
		}
		static := engine.Validate(catalog, programs)
		return engine.Dumps(engine.Obj().Set("errors", engine.ErrorsJSON(static.Errors)).Set("bundles", static.Bundles), -1), nil
	case "verify":
		catalog, programs, student, err := ParseSnapshot(c.Snapshot)
		if err != nil {
			return inputError(err)
		}
		plan, err := decodePlan(c.Plan)
		if err != nil {
			return "", err
		}
		return engine.Dumps(engine.Obj().Set("violations", engine.VerifyPlan(catalog, programs, student, plan)), -1), nil
	case "load":
		raw, err := engine.DecodeJSON(c.Raw)
		if err != nil {
			return "", err
		}
		switch c.Which {
		case "catalog":
			_, err = engine.ParseCatalog(raw)
		case "programs":
			_, err = engine.ParsePrograms(raw)
		case "student":
			_, err = engine.ParseStudent(raw, c.Seasons, c.Where)
		default:
			return "", errors.New("알 수 없는 load 대상: " + c.Which)
		}
		if err != nil {
			return inputError(err)
		}
		return engine.Dumps(engine.Obj().Set("ok", true), -1), nil
	}
	return "", errors.New("알 수 없는 케이스 종류: " + c.Kind)
}

func inputError(err error) (string, error) {
	var ie *engine.InputError
	if errors.As(err, &ie) {
		return engine.Dumps(engine.Obj().Set("input_error", ie.Msg), -1), nil
	}
	return "", err
}

func parseCatalogPrograms(rawCatalog, rawPrograms []byte) (*engine.Catalog, *engine.Programs, error) {
	rc, err := engine.DecodeJSON(rawCatalog)
	if err != nil {
		return nil, nil, err
	}
	rp, err := engine.DecodeJSON(rawPrograms)
	if err != nil {
		return nil, nil, err
	}
	catalog, err := engine.ParseCatalog(rc)
	if err != nil {
		return nil, nil, err
	}
	programs, err := engine.ParsePrograms(rp)
	if err != nil {
		return nil, nil, err
	}
	return catalog, programs, nil
}

// ParseSnapshot: same as planapi.views.parse_snapshot. {"catalog","programs","student"} (same shape as data/*.json).
// The student location label follows Django's convention: "student(<id>)".
func ParseSnapshot(data []byte) (*engine.Catalog, *engine.Programs, *engine.Student, error) {
	var snap struct {
		Catalog  json.RawMessage `json:"catalog"`
		Programs json.RawMessage `json:"programs"`
		Student  json.RawMessage `json:"student"`
	}
	if err := json.Unmarshal(data, &snap); err != nil {
		return nil, nil, nil, err
	}
	catalog, programs, err := parseCatalogPrograms(snap.Catalog, snap.Programs)
	if err != nil {
		return nil, nil, nil, err
	}
	rs, err := engine.DecodeJSON(snap.Student)
	if err != nil {
		return nil, nil, nil, err
	}
	id := "?"
	if m, ok := rs.(map[string]any); ok {
		if s, ok := m["id"].(string); ok {
			id = s
		}
	}
	student, err := engine.ParseStudent(rs, catalog.Seasons, "student("+id+")")
	if err != nil {
		return nil, nil, nil, err
	}
	return catalog, programs, student, nil
}

// decodePlan: the plan JSON for a verify case (Plan shape; chosen is read-only here).
func decodePlan(data []byte) (engine.Plan, error) {
	var raw struct {
		Feasible bool `json:"feasible"`
		Terms    []struct {
			Year    int      `json:"year"`
			Season  string   `json:"season"`
			Courses []string `json:"courses"`
			Credits int      `json:"credits"`
		} `json:"terms"`
		Chosen map[string][]string `json:"chosen"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return engine.Plan{}, err
	}
	plan := engine.Plan{Feasible: raw.Feasible}
	for _, t := range raw.Terms {
		plan.Terms = append(plan.Terms, engine.Term{Year: t.Year, Season: t.Season, Courses: t.Courses, Credits: t.Credits})
	}
	for k, v := range raw.Chosen {
		plan.Chosen.Set(k, v)
	}
	return plan, nil
}
