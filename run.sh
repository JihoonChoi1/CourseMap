#!/usr/bin/env bash
# One script to run everything.
#
#   ./run.sh web      web UI: set up -> load data -> start worker + server -> open the browser (Ctrl+C to stop)
#   ./run.sh          demo: set up -> CLI on real UBC data -> API (sync/async) -> check the S3 export
#   ./run.sh test     all tests: engine, Django, Go, Python/Go parity
#   ./run.sh all      demo + all tests
#
# web and demo replace the database contents with the real UBC data. For the fictional data: DATA_DIR=data ./run.sh web
# Set PORT to change the API server port (default 8000).
set -euo pipefail

cd "$(dirname "$0")"
PY=.venv/bin/python
PORT="${PORT:-8000}"
DATA_DIR="${DATA_DIR:-data/ubc}"
LOG_DIR="$(mktemp -d)"
PIDS=()

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
fail() { printf '\033[1;31mfailed: %s\033[0m\n' "$1" >&2; exit 1; }

cleanup() {
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT

setup() {
  step "Set up (docker compose, packages, migrations, S3 bucket, Go build)"
  [ -x "$PY" ] || fail "no .venv (run python3 -m venv .venv, then try again)"
  docker compose up -d --wait >/dev/null 2>&1 || fail "docker compose up failed (is Docker running?)"
  "$PY" -m pip install -q --disable-pip-version-check -r requirements.txt
  "$PY" manage.py migrate --noinput >/dev/null
  for _ in $(seq 1 20); do
    "$PY" manage.py ensure_s3_bucket >/dev/null 2>&1 && break
    sleep 1
  done
  "$PY" manage.py ensure_s3_bucket >/dev/null || fail "cannot connect to S3"
  (cd goengine && go build -o bin/courseplan ./cmd/courseplan)
}

demo() {
  step "CLI: 6 students on real UBC data (summary on stderr, Plan JSON omitted)"
  python3 cli.py --all --data-dir data/ubc >/dev/null || true   # exit code 1 if any student is infeasible

  step "Load the real UBC data into the database"
  "$PY" manage.py load_data --data-dir data/ubc

  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "port $PORT is in use (pick another, e.g. PORT=8001 ./run.sh)"
  fi
  step "Start the Celery worker + API server (logs: $LOG_DIR)"
  "$PY" -m celery -A config worker -l warning -c 1 >"$LOG_DIR/worker.log" 2>&1 &
  PIDS+=($!)
  "$PY" manage.py runserver "$PORT" --noreload >"$LOG_DIR/server.log" 2>&1 &
  PIDS+=($!)
  for _ in $(seq 1 30); do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/api/students/U3/plan" && break
    sleep 0.5
  done

  step "Sync API: GET /api/students/U3/plan"
  curl -s -D "$LOG_DIR/headers" "http://127.0.0.1:$PORT/api/students/U3/plan" >"$LOG_DIR/sync.json"
  grep -i '^x-' "$LOG_DIR/headers" || true
  "$PY" - "$LOG_DIR/sync.json" <<'EOF'
import json, sys
plan = json.load(open(sys.argv[1]))
print("feasible:", plan["feasible"])
for t in plan["terms"]:
    print("  %d %s  %2d credits  %s" % (t["year"], t["season"], t["credits"], ", ".join(t["courses"])))
EOF

  step "Async API: POST /api/students/U1/plan-jobs -> wait for the result"
  JOB_ID=$(curl -s -X POST "http://127.0.0.1:$PORT/api/students/U1/plan-jobs" |
           "$PY" -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
  echo "job_id: $JOB_ID"
  for _ in $(seq 1 60); do
    curl -s "http://127.0.0.1:$PORT/api/plan-jobs/$JOB_ID" >"$LOG_DIR/job.json"
    STATUS=$("$PY" -c 'import json,sys; j=json.load(open(sys.argv[1])); e=j["export"] or {}; print(j["status"], e.get("status"))' "$LOG_DIR/job.json")
    case "$STATUS" in
      "SUCCESS SUCCESS"|"SUCCESS FAILED"|FAILED*) break ;;
    esac
    sleep 0.5
  done

  step "Check the S3 export (download via the presigned URL in the job response)"
  "$PY" - "$LOG_DIR/job.json" <<'EOF'
import json, sys, urllib.request
job = json.load(open(sys.argv[1]))
print("job status:", job["status"], "| engine:", job["engine"])
ex = job["export"]
if job["status"] != "SUCCESS" or ex is None or ex["status"] != "SUCCESS":
    print("export:", ex)
    sys.exit("S3 export failed")
print("export: s3://%s/%s (%s)" % (ex["bucket"], ex["key"], ex["exported_at"]))
doc = json.loads(urllib.request.urlopen(ex["url"], timeout=5).read())
print("downloaded object keys:", list(doc))
print("plan matches:", doc["plan"] == job["plan"], "| terms:", len(doc["plan"]["terms"]))
print("presigned URL (valid for 1 hour):")
print(ex["url"])
EOF

  step "UBC grid measurement (6 students × cap 3-18 × terms 1-8)"
  python3 scripts/measure_ubc.py

  echo
  echo "Demo done. The database now holds the real UBC data (to revert: $PY manage.py load_data)"
}

web() {
  step "Load data into the database ($DATA_DIR)"
  "$PY" manage.py load_data --data-dir "$DATA_DIR"
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "port $PORT is in use (pick another, e.g. PORT=8001 ./run.sh web)"
  fi
  step "Start the Celery worker (logs: $LOG_DIR/worker.log)"
  "$PY" -m celery -A config worker -l warning -c 1 >"$LOG_DIR/worker.log" 2>&1 &
  PIDS+=($!)
  step "Web UI: http://127.0.0.1:$PORT/  (stop: Ctrl+C)"
  ( for _ in $(seq 1 30); do
      curl -s -o /dev/null "http://127.0.0.1:$PORT/" && { command -v open >/dev/null && open "http://127.0.0.1:$PORT/"; break; }
      sleep 0.5
    done ) &
  "$PY" manage.py runserver "$PORT"
}

tests() {
  step "Engine tests"
  python3 -m unittest discover -s tests 2>&1 | tail -3
  step "Django tests (uses MySQL, Redis, S3)"
  "$PY" manage.py test planapi --noinput 2>&1 | grep -E '^(Ran|OK|FAILED)'
  step "Go tests"
  (cd goengine && go test -count=1 ./...)
  step "Python/Go parity check (about 1 minute)"
  python3 parity/run.py | tail -4
}

case "${1:-demo}" in
  web)  setup; web ;;
  demo) setup; demo ;;
  test) setup; tests ;;
  all)  setup; demo; tests ;;
  *)    echo "usage: ./run.sh [web|demo|test|all]" >&2; exit 2 ;;
esac
