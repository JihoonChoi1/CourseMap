package engine

import "testing"

// Directly checks the spots where language differences between Python and Go make the port easy to get wrong.
// Expected values were obtained by running the same expression in Python 3.14.
// (Equivalence of the engine's own behavior is the parity package's fixture comparison's job.)

func TestPyModAndFloorDiv(t *testing.T) {
	cases := []struct{ a, b, mod, div int }{
		{-1, 2, 1, -1}, {-3, 2, 1, -2}, {5, 2, 1, 2}, {-4, 2, 0, -2},
		{-7, 3, 2, -3}, {7, -3, -2, -3}, {0, 2, 0, 0}, {-INF - 1, 2, 1, -536870913},
	}
	for _, c := range cases {
		if got := pyMod(c.a, c.b); got != c.mod {
			t.Errorf("pyMod(%d, %d) = %d, Python %d", c.a, c.b, got, c.mod)
		}
		if got := pyFloorDiv(c.a, c.b); got != c.div {
			t.Errorf("pyFloorDiv(%d, %d) = %d, Python %d", c.a, c.b, got, c.div)
		}
	}
}

func TestSeasonAtNegativeTerm(t *testing.T) {
	// LastOfferedUntil can see a negative term. With Go's %, the index would come out negative.
	cal := &Calendar{Seasons: []string{"SPRING", "FALL"}, StartYear: 2026, StartIdx: 1, NumTerms: 1}
	// StartIdx 1 (FALL), t=-2 -> index -1: Python -1 % 2 == 1 (FALL), 2026 + (-1 // 2) == 2025
	if got := SeasonAt(cal, -2); got != "FALL" {
		t.Errorf("SeasonAt(-2) = %s", got)
	}
	if got := TermLabel(cal, -2); got != "2025 FALL" {
		t.Errorf("TermLabel(-2) = %s", got)
	}
	if got := LastOfferedUntil(cal, []string{"SPRING"}, 0); got != -1 {
		t.Errorf("LastOfferedUntil = %d", got)
	}
}

func sample() *JObj {
	return Obj().
		Set("s", "A\"B\\ \t\n\r\b\f\x00\x1b\x7f  한글 😀 <&>").
		Set("l", []string{}).
		Set("d", Obj()).
		Set("n", []any{1, -2, true, false, nil, []string{"x"}}).
		Set("o", Obj().Set("k", Obj().Set("z", []any{})))
}

func TestDumpsMatchesPython(t *testing.T) {
	// json.dumps(v, ensure_ascii=False)
	want := "{\"s\": \"A\\\"B\\\\ \\t\\n\\r\\b\\f\\u0000\\u001b\x7f  한글 😀 <&>\", \"l\": [], \"d\": {}, " +
		"\"n\": [1, -2, true, false, null, [\"x\"]], \"o\": {\"k\": {\"z\": []}}}"
	if got := Dumps(sample(), -1); got != want {
		t.Errorf("Dumps(-1)\n got %q\nwant %q", got, want)
	}
	// json.dumps(v, ensure_ascii=False, indent=2)
	want = "{\n  \"s\": \"A\\\"B\\\\ \\t\\n\\r\\b\\f\\u0000\\u001b\x7f  한글 😀 <&>\",\n  \"l\": [],\n  \"d\": {},\n" +
		"  \"n\": [\n    1,\n    -2,\n    true,\n    false,\n    null,\n    [\n      \"x\"\n    ]\n  ],\n" +
		"  \"o\": {\n    \"k\": {\n      \"z\": []\n    }\n  }\n}"
	if got := Dumps(sample(), 2); got != want {
		t.Errorf("Dumps(2)\n got %q\nwant %q", got, want)
	}
}

func TestJObjSetKeepsPosition(t *testing.T) {
	// Python dict: assigning to an existing key keeps its position and only changes the value
	o := Obj().Set("a", 1).Set("b", 2).Set("a", 3)
	if got := Dumps(o, -1); got != `{"a": 3, "b": 2}` {
		t.Errorf("got %s", got)
	}
	var l OrderedLists
	l.Set("x", []string{"1"})
	l.Set("y", nil)
	l.Set("x", []string{"2"})
	if got := Dumps(l, -1); got != `{"x": ["2"], "y": []}` {
		t.Errorf("got %s", got)
	}
}

func TestSatMul(t *testing.T) {
	limit := 257
	cases := []struct{ a, b, want int }{
		{1, 256, 256}, {2, 128, 256}, {16, 17, 257}, {257, 1, 257}, {1 << 40, 1 << 40, 257}, {0, 5, 0}, {5, 0, 0},
	}
	for _, c := range cases {
		if got := satMul(c.a, c.b, limit); got != c.want {
			t.Errorf("satMul(%d, %d) = %d, want %d", c.a, c.b, got, c.want)
		}
	}
}

func TestDecodeJSONKeepsIntFloatDistinction(t *testing.T) {
	// Python json: 3 -> int, 3.0 / 3e0 -> float (type error), true -> bool (must be int)
	for raw, want := range map[string]string{
		`{"n": 3}`:    "",
		`{"n": 3.0}`:  "x: 'n' 타입 오류",
		`{"n": 3e0}`:  "x: 'n' 타입 오류",
		`{"n": true}`: "x: 'n'는 int여야 함",
		`{"n": "3"}`:  "x: 'n' 타입 오류",
		`{"m": 3}`:    "x: 'n' 필드 없음",
		`[3]`:         "x: 'n' 필드 없음",
	} {
		v, err := DecodeJSON([]byte(raw))
		if err != nil {
			t.Fatal(err)
		}
		_, err = fieldInt(v, "n", "x")
		got := ""
		if err != nil {
			got = err.Error()
		}
		if got != want {
			t.Errorf("%s: got %q, want %q", raw, got, want)
		}
	}
}
