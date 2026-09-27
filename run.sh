#!/usr/bin/env bash
# 한 번에 실행하는 스크립트.
#
#   ./run.sh web      웹 화면: 환경 준비 -> 데이터 적재 -> 워커 + 서버 실행 -> 브라우저 열기 (Ctrl+C로 종료)
#   ./run.sh          데모: 환경 준비 -> UBC 실데이터 CLI -> API(동기/비동기) -> S3 export 확인
#   ./run.sh test     전체 테스트: 엔진, Django, Go, Python/Go 동등성
#   ./run.sh all      데모 + 전체 테스트
#
# web과 데모는 DB 내용을 UBC 실데이터로 바꾼다. 가상 데이터로 보려면: DATA_DIR=data ./run.sh web
# API 서버 포트는 PORT 환경변수로 바꿀 수 있다 (기본 8000).
set -euo pipefail

cd "$(dirname "$0")"
PY=.venv/bin/python
PORT="${PORT:-8000}"
DATA_DIR="${DATA_DIR:-data/ubc}"
LOG_DIR="$(mktemp -d)"
PIDS=()

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
fail() { printf '\033[1;31m실패: %s\033[0m\n' "$1" >&2; exit 1; }

cleanup() {
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT

setup() {
  step "환경 준비 (docker compose, 패키지, 마이그레이션, S3 버킷, Go 빌드)"
  [ -x "$PY" ] || fail ".venv가 없음 (python3 -m venv .venv 후 다시 실행)"
  docker compose up -d --wait >/dev/null 2>&1 || fail "docker compose up 실패 (Docker 실행 여부 확인)"
  "$PY" -m pip install -q --disable-pip-version-check -r requirements.txt
  "$PY" manage.py migrate --noinput >/dev/null
  for _ in $(seq 1 20); do
    "$PY" manage.py ensure_s3_bucket >/dev/null 2>&1 && break
    sleep 1
  done
  "$PY" manage.py ensure_s3_bucket >/dev/null || fail "S3에 연결할 수 없음"
  (cd goengine && go build -o bin/courseplan ./cmd/courseplan)
}

demo() {
  step "CLI: UBC 실데이터 학생 6명 (요약은 stderr, Plan JSON은 생략)"
  python3 cli.py --all --data-dir data/ubc >/dev/null || true   # infeasible 학생이 있으면 종료 코드 1

  step "DB에 UBC 실데이터 적재"
  "$PY" manage.py load_data --data-dir data/ubc

  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "포트 $PORT 사용 중 (PORT=8001 ./run.sh 처럼 다른 포트 지정)"
  fi
  step "Celery 워커 + API 서버 시작 (로그: $LOG_DIR)"
  .venv/bin/celery -A config worker -l warning -c 1 >"$LOG_DIR/worker.log" 2>&1 &
  PIDS+=($!)
  "$PY" manage.py runserver "$PORT" --noreload >"$LOG_DIR/server.log" 2>&1 &
  PIDS+=($!)
  for _ in $(seq 1 30); do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/api/students/U3/plan" && break
    sleep 0.5
  done

  step "동기 API: GET /api/students/U3/plan"
  curl -s -D "$LOG_DIR/headers" "http://127.0.0.1:$PORT/api/students/U3/plan" >"$LOG_DIR/sync.json"
  grep -i '^x-' "$LOG_DIR/headers" || true
  "$PY" - "$LOG_DIR/sync.json" <<'EOF'
import json, sys
plan = json.load(open(sys.argv[1]))
print("feasible:", plan["feasible"])
for t in plan["terms"]:
    print("  %d %s  %2d학점  %s" % (t["year"], t["season"], t["credits"], ", ".join(t["courses"])))
EOF

  step "비동기 API: POST /api/students/U1/plan-jobs -> 결과 대기"
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

  step "S3 export 확인 (job 응답의 presigned URL로 다운로드)"
  "$PY" - "$LOG_DIR/job.json" <<'EOF'
import json, sys, urllib.request
job = json.load(open(sys.argv[1]))
print("job status:", job["status"], "| engine:", job["engine"])
ex = job["export"]
if job["status"] != "SUCCESS" or ex is None or ex["status"] != "SUCCESS":
    print("export:", ex)
    sys.exit("S3 export 실패")
print("export: s3://%s/%s (%s)" % (ex["bucket"], ex["key"], ex["exported_at"]))
doc = json.loads(urllib.request.urlopen(ex["url"], timeout=5).read())
print("다운로드한 객체 키:", list(doc))
print("Plan 일치:", doc["plan"] == job["plan"], "| 학기 수:", len(doc["plan"]["terms"]))
print("presigned URL (1시간 유효):")
print(ex["url"])
EOF

  step "UBC 격자 측정 (학생 6 × 상한 3~18 × 학기 1~8)"
  python3 scripts/measure_ubc.py

  echo
  echo "데모 끝. DB는 UBC 실데이터 상태 (되돌리기: $PY manage.py load_data)"
}

web() {
  step "DB에 데이터 적재 ($DATA_DIR)"
  "$PY" manage.py load_data --data-dir "$DATA_DIR"
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "포트 $PORT 사용 중 (PORT=8001 ./run.sh web 처럼 다른 포트 지정)"
  fi
  step "Celery 워커 시작 (로그: $LOG_DIR/worker.log)"
  .venv/bin/celery -A config worker -l warning -c 1 >"$LOG_DIR/worker.log" 2>&1 &
  PIDS+=($!)
  step "웹 화면: http://127.0.0.1:$PORT/  (종료: Ctrl+C)"
  ( for _ in $(seq 1 30); do
      curl -s -o /dev/null "http://127.0.0.1:$PORT/" && { command -v open >/dev/null && open "http://127.0.0.1:$PORT/"; break; }
      sleep 0.5
    done ) &
  "$PY" manage.py runserver "$PORT"
}

tests() {
  step "엔진 테스트"
  python3 -m unittest discover -s tests 2>&1 | tail -3
  step "Django 테스트 (MySQL, Redis, S3 사용)"
  "$PY" manage.py test planapi --noinput 2>&1 | grep -E '^(Ran|OK|FAILED)'
  step "Go 테스트"
  (cd goengine && go test -count=1 ./...)
  step "Python/Go 동등성 대조 (약 1분)"
  python3 parity/run.py | tail -4
}

case "${1:-demo}" in
  web)  setup; web ;;
  demo) setup; demo ;;
  test) setup; tests ;;
  all)  setup; demo; tests ;;
  *)    echo "사용법: ./run.sh [web|demo|test|all]" >&2; exit 2 ;;
esac
