// Package engine is the Go port of the engine/ (Python) scheduling engine.
//
// Python is the reference implementation. Since the Plan JSON must be byte-identical for the same
// input, file/function layout and traversal order are matched 1:1 with Python. Where a Python
// dict's insertion order affects the result, it has been carried over into an ordered structure
// (OrderedLists, a key slice).
package engine

import "sort"

// INF is the "unreachable" term index (same value as Python's model.INF).
const INF = 1 << 30

// Limits corresponds to the Python module constants scheduler.EXACT_NODE_LIMIT and
// targets.MAX_CANDIDATES. Since Python tests override these via mock.patch, they're kept as a
// parameter here so the comparison tests can pass the same values.
type Limits struct {
	ExactNodeLimit int
	MaxCandidates  int
}

// DefaultLimits is the limit set by the engine spec (same as Python).
var DefaultLimits = Limits{ExactNodeLimit: 200000, MaxCandidates: 256}

type Course struct {
	ID      string
	Title   string
	Credits int
	Offered []string   // names of the seasons it's offered in
	Prereqs [][]string // CNF: outer AND, inner OR (doc §2.2)
	Coreqs  [][]string
}

type Catalog struct {
	Seasons []string
	Courses []*Course          // preserves file order (including duplicate ids)
	ByID    map[string]*Course // first occurrence wins on duplicate id
	Index   map[string]int     // id -> position in file (for deterministic sorting)
	IDs     []string           // ByID's keys in Python-dict insertion order (first-seen order)
}

// NewCatalog fills ByID/Index/IDs using the same rules as loader.parse_catalog.
func NewCatalog(seasons []string, courses []*Course) *Catalog {
	c := &Catalog{Seasons: seasons, Courses: courses, ByID: map[string]*Course{}, Index: map[string]int{}}
	for i, course := range courses {
		if _, ok := c.ByID[course.ID]; !ok {
			c.ByID[course.ID] = course
			c.Index[course.ID] = i
			c.IDs = append(c.IDs, course.ID)
		}
	}
	return c
}

type Group struct {
	ID      string
	Name    string
	Rule    string // "ALL" | "PICK_N"
	N       int
	Courses []string
}

type Program struct {
	ID     string
	Name   string
	Groups []Group
}

type Programs struct {
	Degree Program
	Tracks []Program
}

type Student struct {
	ID          string
	Name        string
	Track       string
	Completed   []string
	StartYear   int
	StartSeason string
	NumTerms    int
	MaxCredits  int
}

type EngineError struct {
	Code    string // code from doc §5
	Message string
	Courses []string
}

// Unit is a scheduling unit. Several courses if it's a bundle (mutual-coreq SCC), otherwise 1 course.
type Unit struct {
	ID      string // member course ids joined with '+'
	Courses []string
	Credits int
	Offered []string // intersection of member courses' offered seasons
}

// Calendar: conversion info between term index t (0..NumTerms-1) and (year, season).
type Calendar struct {
	Seasons   []string
	StartYear int
	StartIdx  int // position of start_season within Seasons
	NumTerms  int
}

// OrderedLists corresponds to Python's dict[str, list[str]] (preserving insertion order). Used for Plan.chosen and picks.
type OrderedLists struct {
	Keys []string
	Vals [][]string
}

// Set behaves like a Python dict assignment: for an existing key, only the value changes, keeping its position.
func (o *OrderedLists) Set(key string, val []string) {
	for i, k := range o.Keys {
		if k == key {
			o.Vals[i] = val
			return
		}
	}
	o.Keys = append(o.Keys, key)
	o.Vals = append(o.Vals, val)
}

func (o *OrderedLists) Get(key string) ([]string, bool) {
	for i, k := range o.Keys {
		if k == key {
			return o.Vals[i], true
		}
	}
	return nil, false
}

// pyMod, pyFloorDiv: Python's %, // (differ from Go's on negative numbers, e.g. -1 % 2 == 1)
func pyMod(a, b int) int {
	r := a % b
	if r != 0 && (r < 0) != (b < 0) {
		r += b
	}
	return r
}

func pyFloorDiv(a, b int) int {
	q := a / b
	if a%b != 0 && (a < 0) != (b < 0) {
		q--
	}
	return q
}

func SeasonIdxAt(cal *Calendar, t int) int {
	return pyMod(cal.StartIdx+t, len(cal.Seasons))
}

func SeasonAt(cal *Calendar, t int) string {
	return cal.Seasons[SeasonIdxAt(cal, t)]
}

func YearAt(cal *Calendar, t int) int {
	return cal.StartYear + pyFloorDiv(cal.StartIdx+t, len(cal.Seasons))
}

func TermLabel(cal *Calendar, t int) string {
	return itoa(YearAt(cal, t)) + " " + SeasonAt(cal, t)
}

// SortByCatalog: a stable sort by catalog order (an id not in the catalog sorts to the back via INF).
func SortByCatalog(catalog *Catalog, ids []string) []string {
	out := append([]string{}, ids...)
	sort.SliceStable(out, func(i, j int) bool {
		return indexOr(catalog, out[i]) < indexOr(catalog, out[j])
	})
	return out
}

func indexOr(catalog *Catalog, cid string) int {
	if i, ok := catalog.Index[cid]; ok {
		return i
	}
	return INF
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

func copySet(m map[string]bool) map[string]bool {
	out := make(map[string]bool, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func keys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
