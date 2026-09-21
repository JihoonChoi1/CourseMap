package parity

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// Shared fixture comparison: each line of parity/fixtures/*.jsonl is {"case", "expected"}, where expected is
// the Python engine's result (regenerated with `python3 parity/run.py --write-fixtures`). The Go result must
// match byte-for-byte. The fixtures contain every input the 165 engine tests fed the engine (unit_tests),
// the real-data grid, and a subset of the fuzz cases.

const fixtureDir = "../../parity/fixtures"

func TestFixtures(t *testing.T) {
	files, err := filepath.Glob(filepath.Join(fixtureDir, "*.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if len(files) == 0 {
		t.Fatal("no fixtures: python3 parity/run.py --write-fixtures")
	}
	for _, path := range files {
		t.Run(filepath.Base(path), func(t *testing.T) {
			f, err := os.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer f.Close()
			sc := bufio.NewScanner(f)
			sc.Buffer(make([]byte, 1<<20), 1<<26)
			n := 0
			bad := 0
			for sc.Scan() {
				n++
				var fx struct {
					Case     json.RawMessage `json:"case"`
					Expected string          `json:"expected"`
				}
				if err := json.Unmarshal(sc.Bytes(), &fx); err != nil {
					t.Fatalf("line %d: %v", n, err)
				}
				got, err := RunCase(fx.Case)
				if err != nil {
					t.Fatalf("line %d: %v", n, err)
				}
				if got != fx.Expected {
					bad++
					if bad <= 3 {
						t.Errorf("line %d mismatch\n case: %.300s\n   py: %.500s\n   go: %.500s", n, fx.Case, fx.Expected, got)
					}
				}
			}
			if err := sc.Err(); err != nil {
				t.Fatal(err)
			}
			if bad > 0 {
				t.Errorf("%d/%d mismatch", bad, n)
			}
			t.Logf("%d cases", n)
		})
	}
}
