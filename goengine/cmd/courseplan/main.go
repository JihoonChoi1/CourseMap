// courseplan: Go engine CLI. The default mode produces the same I/O (stdout, stderr, exit code) as cli.py.
//
//	courseplan S1                      # Plan JSON to stdout
//	courseplan S3 --max-credits 7      # override part of a student's settings for experimentation
//	courseplan --all                   # all students
//	courseplan --stdin-snapshot        # one snapshot on stdin -> result JSON (called by the Celery worker)
//	courseplan --batch [--timing]      # parity cases on stdin (NDJSON) -> results (NDJSON) (called by parity/run.py)
//
// Default mode exit codes: 0 all feasible & verified / 1 some infeasible / 2 input error / 3 verification failure (engine bug)
// --stdin-snapshot exit codes: 0 success / 2 malformed input (message on stderr)
package main

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"time"

	"coursemap/goengine/engine"
	"coursemap/goengine/parity"
)

type options struct {
	studentID     string
	hasStudentID  bool
	all           bool
	dataDir       string
	maxCredits    *int
	numTerms      *int
	stdinSnapshot bool
	batch         bool
	timing        bool
}

const usage = "usage: courseplan [-h] [--all] [--data-dir DATA_DIR] [--max-credits MAX_CREDITS] [--num-terms NUM_TERMS] [student_id]\n" +
	"       courseplan --stdin-snapshot\n" +
	"       courseplan --batch [--timing]\n"

func usageError(msg string) int {
	fmt.Fprint(os.Stderr, usage)
	fmt.Fprintln(os.Stderr, "courseplan: error: "+msg)
	return 2
}

func parseArgs(args []string) (*options, int) {
	o := &options{dataDir: "data"}
	for i := 0; i < len(args); i++ {
		a := args[i]
		name, value, hasValue := strings.Cut(a, "=")
		needValue := func() (string, bool) {
			if hasValue {
				return value, true
			}
			if i+1 >= len(args) {
				return "", false
			}
			i++
			return args[i], true
		}
		switch name {
		case "-h", "--help":
			fmt.Print(usage)
			return nil, 0
		case "--all":
			o.all = true
		case "--stdin-snapshot":
			o.stdinSnapshot = true
		case "--batch":
			o.batch = true
		case "--timing":
			o.timing = true
		case "--data-dir":
			v, ok := needValue()
			if !ok {
				return nil, usageError("argument --data-dir: expected one argument")
			}
			o.dataDir = v
		case "--max-credits", "--num-terms":
			v, ok := needValue()
			if !ok {
				return nil, usageError("argument " + name + ": expected one argument")
			}
			n, err := strconv.Atoi(v)
			if err != nil {
				return nil, usageError("argument " + name + ": invalid int value: '" + v + "'")
			}
			if name == "--max-credits" {
				o.maxCredits = &n
			} else {
				o.numTerms = &n
			}
		default:
			if strings.HasPrefix(a, "--") {
				return nil, usageError("unrecognized arguments: " + a)
			}
			if o.hasStudentID {
				return nil, usageError("unrecognized arguments: " + a)
			}
			o.studentID = a
			o.hasStudentID = true
		}
	}
	return o, -1
}

func main() {
	o, code := parseArgs(os.Args[1:])
	if o == nil {
		os.Exit(code)
	}
	switch {
	case o.stdinSnapshot:
		os.Exit(runSnapshot())
	case o.batch:
		os.Exit(runBatch(o.timing))
	default:
		os.Exit(runCLI(o))
	}
}

// runSnapshot: for the Celery worker. stdin snapshot -> PlanResult JSON (read by planapi/goengine.py).
func runSnapshot() int {
	data, err := io.ReadAll(os.Stdin)
	if err != nil {
		fmt.Fprintln(os.Stderr, "stdin 읽기 실패: "+err.Error())
		return 2
	}
	catalog, programs, student, err := parity.ParseSnapshot(data)
	if err != nil {
		fmt.Fprintln(os.Stderr, "입력 오류: "+err.Error())
		return 2
	}
	res := engine.PlanStudent(catalog, programs, engine.Validate(catalog, programs), student, engine.DefaultLimits)
	fmt.Println(engine.Dumps(res.ResultJSON(), -1))
	return 0
}

func runBatch(timing bool) int {
	in := bufio.NewReaderSize(os.Stdin, 1<<20)
	out := bufio.NewWriterSize(os.Stdout, 1<<20)
	defer out.Flush()
	n := 0
	failed := 0
	var elapsed time.Duration
	for {
		line, err := in.ReadBytes('\n')
		if len(strings.TrimSpace(string(line))) > 0 {
			start := time.Now()
			result, cerr := parity.RunCase(line)
			elapsed += time.Since(start)
			if cerr != nil {
				failed++
				result = engine.Dumps(engine.Obj().Set("harness_error", cerr.Error()), -1)
			}
			out.WriteString(result)
			out.WriteByte('\n')
			n++
		}
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			fmt.Fprintln(os.Stderr, "stdin 읽기 실패: "+err.Error())
			return 2
		}
	}
	if timing {
		fmt.Fprintf(os.Stderr, "{\"cases\": %d, \"seconds\": %.6f}\n", n, elapsed.Seconds())
	}
	if failed > 0 {
		return 1
	}
	return 0
}

func runCLI(o *options) int {
	if !o.all && !o.hasStudentID {
		return usageError("student_id 또는 --all 필요")
	}

	catalog, programs, students, err := engine.LoadAll(o.dataDir)
	if err != nil {
		fmt.Fprintln(os.Stderr, "입력 오류: "+err.Error())
		return 2
	}

	targets := []*engine.Student{}
	for _, s := range students {
		if o.all || s.ID == o.studentID {
			targets = append(targets, s)
		}
	}
	if len(targets) == 0 {
		shown := o.studentID
		if !o.hasStudentID {
			shown = "None" // Python str(None)
		}
		fmt.Fprintln(os.Stderr, "학생 없음: "+shown)
		return 2
	}

	static := engine.Validate(catalog, programs)
	plans := []any{}
	code := 0
	for _, s := range targets {
		if o.maxCredits != nil {
			s.MaxCredits = *o.maxCredits
		}
		if o.numTerms != nil {
			s.NumTerms = *o.numTerms
		}
		if s.MaxCredits < 1 || s.NumTerms < 1 {
			fmt.Fprintln(os.Stderr, s.ID+": --max-credits/--num-terms는 1 이상이어야 함")
			return 2
		}

		res := engine.PlanStudent(catalog, programs, static, s, engine.DefaultLimits)
		plans = append(plans, res.Plan.JSON())

		head := "[" + s.ID + "] track=" + s.Track + " cap=" + strconv.Itoa(s.MaxCredits) + " num_terms=" + strconv.Itoa(s.NumTerms) +
			" | 후보 " + strconv.Itoa(res.Candidates) + "개"
		if res.UsedFallback {
			head += " (그리디 폴백)"
		}
		fmt.Fprintln(os.Stderr, head)
		if res.Plan.Feasible {
			fmt.Fprintln(os.Stderr, "  feasible: "+strconv.Itoa(len(res.Plan.Terms))+"학기 사용 (상한 무시 하한 "+
				strconv.Itoa(res.LowerBoundTerms)+"학기), 배치 총 "+strconv.Itoa(res.TotalCredits)+"학점")
			if res.MinTermsProven {
				fmt.Fprintln(os.Stderr, "  최소 학기: 보장 — 이보다 일찍 졸업하는 배치는 없음")
			} else {
				fmt.Fprintln(os.Stderr, "  최소 학기: 보장 안 됨 — 전수탐색 한도 초과 또는 조합 폭발로 일부만 탐색")
			}
			violations := engine.VerifyPlan(catalog, programs, s, res.Plan)
			if len(violations) == 0 {
				fmt.Fprintln(os.Stderr, "  verify: OK — 개설학기/prereq/coreq/학점상한/중복/그룹 충족 모두 만족")
			} else {
				code = 3
				fmt.Fprintln(os.Stderr, "  verify: FAIL")
				for _, line := range violations {
					fmt.Fprintln(os.Stderr, "    - "+line)
				}
			}
		} else {
			if code == 0 {
				code = 1
			}
			fmt.Fprintln(os.Stderr, "  infeasible:")
			for _, e := range res.Plan.Errors {
				fmt.Fprintln(os.Stderr, "    - "+e.Code+": "+e.Message)
			}
		}
	}

	var out any = plans
	if !o.all {
		out = plans[0]
	}
	fmt.Println(engine.Dumps(out, 2))
	return code
}
