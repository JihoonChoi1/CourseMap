//go:build js && wasm

// wasm: the Go engine compiled to WebAssembly for the browser demo (demo/).
//
// It registers one global function, coursemapPlan(snapshotJSON) -> resultJSON, taking the same
// snapshot the Celery worker hands to `courseplan --stdin-snapshot` ({catalog, programs, student}).
// The result is the --stdin-snapshot output plus "violations" (VerifyPlan on a feasible plan, so the
// page can show that the plan was checked independently) or, for malformed input, {"input_error": msg}.
package main

import (
	"syscall/js"

	"coursemap/goengine/engine"
	"coursemap/goengine/parity"
)

func plan(this js.Value, args []js.Value) any {
	if len(args) != 1 || args[0].Type() != js.TypeString {
		return engine.Dumps(engine.Obj().Set("input_error", "coursemapPlan expects one JSON string"), -1)
	}
	catalog, programs, student, err := parity.ParseSnapshot([]byte(args[0].String()))
	if err != nil {
		return engine.Dumps(engine.Obj().Set("input_error", err.Error()), -1)
	}
	res := engine.PlanStudent(catalog, programs, engine.Validate(catalog, programs), student, engine.DefaultLimits)
	violations := []string{}
	if res.Plan.Feasible {
		violations = engine.VerifyPlan(catalog, programs, student, res.Plan)
	}
	return engine.Dumps(res.ResultJSON().Set("violations", violations), -1)
}

func main() {
	js.Global().Set("coursemapPlan", js.FuncOf(plan))
	select {} // keep the Go runtime alive so the page can keep calling coursemapPlan
}
