package engine

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// Converts JSON values into model structs (Python engine/loader.py).
//
// This only checks "shape" (field presence, type). The check order and messages match Python.
// Input is the plain value DecodeJSON produces (map[string]any, []any, string, json.Number, bool, nil).

type InputError struct {
	Msg string
}

func (e *InputError) Error() string { return e.Msg }

func inputErr(msg string) error { return &InputError{Msg: msg} }

// DecodeJSON: reads numbers as json.Number so integer and float literals are distinguished like Python.
func DecodeJSON(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil, errors.New("JSON 뒤에 불필요한 데이터가 있음")
	}
	return v, nil
}

// kind: "int" | "str" | "list" | "dict"
func field(obj any, key, kind, where string) (any, error) {
	m, ok := obj.(map[string]any)
	if !ok {
		return nil, inputErr(where + ": '" + key + "' 필드 없음")
	}
	value, ok := m[key]
	if !ok {
		return nil, inputErr(where + ": '" + key + "' 필드 없음")
	}
	switch kind {
	case "int":
		// Python: bool is a subtype of int, so it must be filtered out explicitly
		if _, isBool := value.(bool); isBool {
			return nil, inputErr(where + ": '" + key + "'는 int여야 함")
		}
		num, isNum := value.(json.Number)
		if !isNum || strings.ContainsAny(string(num), ".eE") {
			return nil, inputErr(where + ": '" + key + "' 타입 오류")
		}
		n, err := strconv.ParseInt(string(num), 10, 64)
		if err != nil {
			// Python int has no size limit. This is the one place behavior differs from Python (docs/phase5_go_port.md).
			return nil, inputErr(where + ": '" + key + "' 정수 범위 초과")
		}
		return int(n), nil
	case "str":
		if _, isStr := value.(string); !isStr {
			return nil, inputErr(where + ": '" + key + "' 타입 오류")
		}
	case "list":
		if _, isList := value.([]any); !isList {
			return nil, inputErr(where + ": '" + key + "' 타입 오류")
		}
	case "dict":
		if _, isDict := value.(map[string]any); !isDict {
			return nil, inputErr(where + ": '" + key + "' 타입 오류")
		}
	}
	return value, nil
}

func fieldInt(obj any, key, where string) (int, error) {
	v, err := field(obj, key, "int", where)
	if err != nil {
		return 0, err
	}
	return v.(int), nil
}

func fieldStr(obj any, key, where string) (string, error) {
	v, err := field(obj, key, "str", where)
	if err != nil {
		return "", err
	}
	return v.(string), nil
}

func fieldList(obj any, key, where string) ([]any, error) {
	v, err := field(obj, key, "list", where)
	if err != nil {
		return nil, err
	}
	return v.([]any), nil
}

func strList(obj any, key, where string) ([]string, error) {
	values, err := fieldList(obj, key, where)
	if err != nil {
		return nil, err
	}
	out := []string{}
	for _, v := range values {
		s, ok := v.(string)
		if !ok {
			return nil, inputErr(where + ": '" + key + "'는 문자열 리스트여야 함")
		}
		out = append(out, s)
	}
	return out, nil
}

func cnf(obj any, key, where string) ([][]string, error) {
	clauses, err := fieldList(obj, key, where)
	if err != nil {
		return nil, err
	}
	out := [][]string{}
	for _, clause := range clauses {
		list, ok := clause.([]any)
		if !ok {
			return nil, inputErr(where + ": '" + key + "'는 [][]string이어야 함")
		}
		cl := []string{}
		for _, v := range list {
			s, ok := v.(string)
			if !ok {
				return nil, inputErr(where + ": '" + key + "'는 [][]string이어야 함")
			}
			cl = append(cl, s)
		}
		out = append(out, cl)
	}
	return out, nil
}

func readJSON(path string) (any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		// the text inside the parentheses (the OS error text) differs from Python's
		return nil, inputErr(path + ": 읽을 수 없음 (" + err.Error() + ")")
	}
	v, err := DecodeJSON(data)
	if err != nil {
		return nil, inputErr(path + ": JSON 파싱 실패 (" + err.Error() + ")")
	}
	return v, nil
}

func LoadCatalog(path string) (*Catalog, error) {
	raw, err := readJSON(path)
	if err != nil {
		return nil, err
	}
	return ParseCatalog(raw)
}

func ParseCatalog(raw any) (*Catalog, error) {
	seasons, err := strList(raw, "seasons", "catalog")
	if err != nil {
		return nil, err
	}
	if len(seasons) == 0 {
		return nil, inputErr("catalog: 'seasons'가 비어 있음")
	}
	rawCourses, err := fieldList(raw, "courses", "catalog")
	if err != nil {
		return nil, err
	}
	courses := []*Course{}
	for i, rc := range rawCourses {
		where := "catalog.courses[" + itoa(i) + "]"
		c := &Course{}
		if c.ID, err = fieldStr(rc, "id", where); err != nil {
			return nil, err
		}
		if c.Title, err = fieldStr(rc, "title", where); err != nil {
			return nil, err
		}
		if c.Credits, err = fieldInt(rc, "credits", where); err != nil {
			return nil, err
		}
		if c.Offered, err = strList(rc, "offered", where); err != nil {
			return nil, err
		}
		if c.Prereqs, err = cnf(rc, "prereqs", where); err != nil {
			return nil, err
		}
		if c.Coreqs, err = cnf(rc, "coreqs", where); err != nil {
			return nil, err
		}
		courses = append(courses, c)
	}
	return NewCatalog(seasons, courses), nil
}

func loadProgram(raw any, where string) (Program, error) {
	// same order as Python: check groups first, then id and name
	rawGroups, err := fieldList(raw, "groups", where)
	if err != nil {
		return Program{}, err
	}
	groups := []Group{}
	for i, rg := range rawGroups {
		gw := where + ".groups[" + itoa(i) + "]"
		g := Group{}
		if g.ID, err = fieldStr(rg, "id", gw); err != nil {
			return Program{}, err
		}
		if g.Name, err = fieldStr(rg, "name", gw); err != nil {
			return Program{}, err
		}
		if g.Rule, err = fieldStr(rg, "rule", gw); err != nil {
			return Program{}, err
		}
		if g.N, err = fieldInt(rg, "n", gw); err != nil {
			return Program{}, err
		}
		if g.Courses, err = strList(rg, "courses", gw); err != nil {
			return Program{}, err
		}
		groups = append(groups, g)
	}
	p := Program{Groups: groups}
	if p.ID, err = fieldStr(raw, "id", where); err != nil {
		return Program{}, err
	}
	if p.Name, err = fieldStr(raw, "name", where); err != nil {
		return Program{}, err
	}
	return p, nil
}

func LoadPrograms(path string) (*Programs, error) {
	raw, err := readJSON(path)
	if err != nil {
		return nil, err
	}
	return ParsePrograms(raw)
}

func ParsePrograms(raw any) (*Programs, error) {
	rawDegree, err := field(raw, "degree", "dict", "programs")
	if err != nil {
		return nil, err
	}
	degree, err := loadProgram(rawDegree, "programs.degree")
	if err != nil {
		return nil, err
	}
	rawTracks, err := fieldList(raw, "tracks", "programs")
	if err != nil {
		return nil, err
	}
	tracks := []Program{}
	for i, rt := range rawTracks {
		t, err := loadProgram(rt, "programs.tracks["+itoa(i)+"]")
		if err != nil {
			return nil, err
		}
		tracks = append(tracks, t)
	}
	return &Programs{Degree: degree, Tracks: tracks}, nil
}

func LoadStudents(path string, seasons []string) ([]*Student, error) {
	raw, err := readJSON(path)
	if err != nil {
		return nil, err
	}
	list, err := fieldList(raw, "students", "students")
	if err != nil {
		return nil, err
	}
	out := []*Student{}
	for i, rs := range list {
		s, err := ParseStudent(rs, seasons, "students["+itoa(i)+"]")
		if err != nil {
			return nil, err
		}
		out = append(out, s)
	}
	return out, nil
}

func ParseStudent(rs any, seasons []string, where string) (*Student, error) {
	start, err := field(rs, "start_term", "dict", where)
	if err != nil {
		return nil, err
	}
	s := &Student{}
	if s.ID, err = fieldStr(rs, "id", where); err != nil {
		return nil, err
	}
	if s.Name, err = fieldStr(rs, "name", where); err != nil {
		return nil, err
	}
	if s.Track, err = fieldStr(rs, "track", where); err != nil {
		return nil, err
	}
	if s.Completed, err = strList(rs, "completed", where); err != nil {
		return nil, err
	}
	if s.StartYear, err = fieldInt(start, "year", where+".start_term"); err != nil {
		return nil, err
	}
	if s.StartSeason, err = fieldStr(start, "season", where+".start_term"); err != nil {
		return nil, err
	}
	if s.NumTerms, err = fieldInt(rs, "num_terms", where); err != nil {
		return nil, err
	}
	if s.MaxCredits, err = fieldInt(rs, "max_credits_per_term", where); err != nil {
		return nil, err
	}
	if !contains(seasons, s.StartSeason) {
		return nil, inputErr(where + ": 알 수 없는 start_term.season '" + s.StartSeason + "'")
	}
	if s.NumTerms < 1 {
		return nil, inputErr(where + ": num_terms는 1 이상이어야 함")
	}
	if s.MaxCredits < 1 {
		return nil, inputErr(where + ": max_credits_per_term은 1 이상이어야 함")
	}
	return s, nil
}

func LoadAll(dataDir string) (*Catalog, *Programs, []*Student, error) {
	catalog, err := LoadCatalog(filepath.Join(dataDir, "catalog.json"))
	if err != nil {
		return nil, nil, nil, err
	}
	programs, err := LoadPrograms(filepath.Join(dataDir, "programs.json"))
	if err != nil {
		return nil, nil, nil, err
	}
	students, err := LoadStudents(filepath.Join(dataDir, "students.json"), catalog.Seasons)
	if err != nil {
		return nil, nil, nil, err
	}
	return catalog, programs, students, nil
}
