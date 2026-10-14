#!/usr/bin/env python3
"""Record a real CourseMap backend session for the portfolio website's replay demo.

Starts the real stack (Django API + Celery worker on the Go engine + MySQL + Redis + S3-compatible storage),
sends a fixed list of HTTP requests, and saves every response exactly as the server returned it, with the
time it arrived. Then runs the Python/Go parity check and saves its output lines. The website replays this
file; it never edits it.

    docker compose up -d --wait                # MySQL, Redis, S3 (Docker must be running)
    .venv/bin/python demo/capture_demo.py      # -> demo/transcript.json, about 1.5 minutes
    .venv/bin/python demo/capture_demo.py --skip-parity --out /tmp/x.json

Isolation: it uses its own database (coursemap_demo, recreated each run and dropped at the end), its own
Celery queue and its own bucket, so the coursemap database used by ./run.sh web is never touched.
The only change to a response: export.url (a presigned S3 URL that carries the local access key id and a
signature) is replaced with null. The script downloads that URL first and records whether it worked.
"""
import argparse
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "bin", "python")
CELERY = [PY, "-m", "celery"]   # not .venv/bin/celery: its shebang breaks if the folder was moved
GO_BIN = os.path.join(ROOT, "goengine", "bin", "courseplan")
DATA_DIR = "data/ubc"
DEMO_DB = "coursemap_demo"
DEMO_QUEUE = "coursemap_demo"
DEMO_BUCKET = "coursemap-demo"

# The tour, in order. Each GET is answered synchronously by the Python engine; the job runs on the worker (Go).
SYNC_STEPS = [
    ("plan", "GET", "/api/students/U2/plan"),                  # feasible: a five-term plan
    ("not_offered", "GET", "/api/students/U5/plan"),           # infeasible: MATH 101 is W2-only, one W1 term left
    ("over_cap", "GET", "/api/students/U4/plan"),              # infeasible at 6 credits/term; says the cap it needs
    ("raised_cap", "GET", "/api/students/U4/plan?max_credits=10"),   # the cap the previous answer named
]
JOB_PATH = "/api/students/U1/plan-jobs"                        # a first-year student: the longest plan
POLL_INTERVAL = 0.05
HEADERS_KEPT = ("X-Candidates", "X-Used-Fallback", "X-Min-Terms-Proven", "Location")


def sh(*cmd, cwd=ROOT):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Session:
    def __init__(self, base: str):
        self.base = base
        self.t0 = time.monotonic()

    def call(self, method: str, path: str) -> dict:
        req = urllib.request.Request(self.base + path, method=method, data=b"" if method == "POST" else None)
        start = time.monotonic()
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            status, headers, raw = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            status, headers, raw = e.code, e.headers, e.read()
        ms = (time.monotonic() - start) * 1000
        return {
            "t": round(start - self.t0, 3),
            "ms": round(ms, 1),
            "method": method,
            "path": path,
            "status": status,
            "headers": {k: headers[k] for k in HEADERS_KEPT if headers.get(k) is not None},
            "body": json.loads(raw.decode("utf-8")),
        }


def redact_export_url(rec: dict) -> dict:
    export = rec["body"].get("export") if isinstance(rec["body"], dict) else None
    if export and export.get("url"):
        export["url"] = None
    return rec


def mysql_root():
    import pymysql
    return pymysql.connect(host=os.environ.get("MYSQL_HOST", "127.0.0.1"), port=int(os.environ.get("MYSQL_PORT", "3306")),
                           user="root", password=os.environ.get("MYSQL_ROOT_PASSWORD", "root"), autocommit=True)


def reset_demo_db(drop_only=False):
    con = mysql_root()
    try:
        with con.cursor() as c:
            c.execute("DROP DATABASE IF EXISTS `" + DEMO_DB + "`")
            if not drop_only:
                c.execute("CREATE DATABASE `" + DEMO_DB + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                c.execute("GRANT ALL PRIVILEGES ON `" + DEMO_DB + "`.* TO 'coursemap'@'%'")
            c.execute("SELECT VERSION()")
            return c.fetchone()[0]
    finally:
        con.close()


def wait_for(check, timeout, what):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.2)
    sys.exit(what + ": not ready within " + str(timeout) + " s")


def inputs_summary() -> dict:
    """The input data the plans refer to (copied from data/ubc), so the site can label course ids and students."""
    def load(name):
        with open(os.path.join(ROOT, DATA_DIR, name)) as f:
            return json.load(f)
    catalog, programs, students = load("catalog.json"), load("programs.json"), load("students.json")
    return {
        "data_dir": DATA_DIR,
        "courses": [{"id": c["id"], "title": c["title"], "credits": c["credits"], "offered": c["offered"]}
                    for c in catalog["courses"]],
        "tracks": [{"id": t["id"], "name": t["name"]} for t in programs["tracks"]],
        "groups": [{"id": g["id"], "name": g["name"]}
                   for g in programs["degree"]["groups"] + [g for t in programs["tracks"] for g in t["groups"]]],
        "students": [{"id": s["id"], "name": s["name"], "track": s["track"], "completed": s["completed"],
                      "start_term": s["start_term"], "num_terms": s["num_terms"],
                      "max_credits_per_term": s["max_credits_per_term"]} for s in students["students"]],
    }


def run_parity(t_origin: float) -> dict:
    """python3 parity/run.py: every output line with its arrival time (seconds from the start of the run)."""
    cmd = ["python3", "parity/run.py"]
    start = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                            env={**os.environ, "PYTHONUNBUFFERED": "1"})
    lines = []
    for line in proc.stdout:
        lines.append({"t": round(time.monotonic() - start, 3), "text": line.rstrip("\n")})
    code = proc.wait()
    return {"command": " ".join(cmd), "exit_code": code, "started_t": round(start - t_origin, 3),
            "python": sh("python3", "--version"), "lines": lines}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "demo", "transcript.json"))
    ap.add_argument("--skip-parity", action="store_true", help="leave out the ~1 minute Python/Go parity run")
    args = ap.parse_args()
    if not os.path.exists(PY):
        sys.exit("no .venv (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)")

    print("building the Go engine ...", file=sys.stderr)
    subprocess.run(["go", "build", "-o", GO_BIN, "./cmd/courseplan"], cwd=os.path.join(ROOT, "goengine"), check=True)

    tmp = tempfile.mkdtemp(prefix="coursemap-demo-")
    # Every Go engine call goes through this wrapper, so the transcript can show the worker really ran Go.
    go_calls = os.path.join(tmp, "go_calls.log")
    wrapper = os.path.join(tmp, "courseplan")
    with open(wrapper, "w") as f:
        f.write('#!/bin/sh\necho "$*" >> "' + go_calls + '"\nexec "' + GO_BIN + '" "$@"\n')
    os.chmod(wrapper, 0o755)

    env = {**os.environ, "MYSQL_DATABASE": DEMO_DB, "CELERY_QUEUE": DEMO_QUEUE, "S3_BUCKET": DEMO_BUCKET,
           "PLAN_ENGINE": "go", "GO_ENGINE_BIN": wrapper, "PYTHONUNBUFFERED": "1"}
    os.environ.update({k: env[k] for k in ("MYSQL_DATABASE", "CELERY_QUEUE", "S3_BUCKET", "PLAN_ENGINE", "GO_ENGINE_BIN")})

    sys.path.insert(0, ROOT)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    import redis
    from django.conf import settings
    from planapi import s3export

    procs = []
    worker_log = open(os.path.join(tmp, "worker.log"), "w")
    session = None
    try:
        print("preparing the demo database, bucket and data ...", file=sys.stderr)
        mysql_version = reset_demo_db()
        for cmd in (["migrate", "--noinput"], ["load_data", "--data-dir", DATA_DIR], ["ensure_s3_bucket"]):
            subprocess.run([PY, "manage.py", *cmd], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)

        port = free_port()
        procs.append(subprocess.Popen([*CELERY, "-A", "config", "worker", "-l", "info", "-c", "1", "-Q", DEMO_QUEUE,
                                       "-n", "coursemap-demo@%h"], cwd=ROOT, env=env, stdout=worker_log,
                                      stderr=subprocess.STDOUT))
        procs.append(subprocess.Popen([PY, "manage.py", "runserver", str(port), "--noreload"], cwd=ROOT, env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        base = "http://127.0.0.1:" + str(port)

        def server_up():
            try:
                urllib.request.urlopen(base + "/", timeout=1)
                return True
            except Exception:
                return False

        def worker_up():
            with open(worker_log.name) as f:
                return " ready." in f.read()

        wait_for(server_up, 30, "API server")
        wait_for(worker_up, 30, "Celery worker")

        session = Session(base)
        steps = []
        for name, method, path in SYNC_STEPS:
            print(method + " " + path, file=sys.stderr)
            steps.append({"id": name, "requests": [session.call(method, path)]})

        print("POST " + JOB_PATH + " (then poll until the job and its export finish)", file=sys.stderr)
        post = session.call("POST", JOB_PATH)
        job_id = post["body"]["job_id"]
        polls = []
        final = None
        end = time.monotonic() + 60
        while time.monotonic() < end:
            rec = session.call("GET", "/api/plan-jobs/" + job_id)
            body = rec["body"]
            done = body["status"] in ("SUCCESS", "FAILED") and (body["export"] is None or body["export"]["status"] != "PENDING")
            if done:
                export = body.get("export") or {}
                check = {"downloaded": False}
                if export.get("url"):
                    doc = json.loads(urllib.request.urlopen(export["url"], timeout=5).read())
                    check = {"downloaded": True, "keys": list(doc), "plan_matches_job": doc["plan"] == body["plan"]}
                final = check
            polls.append(redact_export_url(rec))
            if done:
                break
            time.sleep(POLL_INTERVAL)
        else:
            sys.exit("job " + job_id + " did not finish within 60 s")
        steps.append({"id": "job", "requests": [post] + polls, "export_check": final})

        print("POST " + JOB_PATH + " again (same input)", file=sys.stderr)
        steps.append({"id": "job_again", "requests": [session.call("POST", JOB_PATH)]})

        with open(go_calls) as f:
            go_invocations = [line.strip() for line in f if line.strip()]
        celery_version = sh(*CELERY, "--version")
        redis_version = redis.Redis.from_url(settings.CELERY_BROKER_URL).info()["redis_version"]
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        worker_log.close()
        # leave nothing behind: the demo objects, the demo database, the temp folder
        try:
            s3 = s3export.client()
            for obj in s3.list_objects_v2(Bucket=DEMO_BUCKET).get("Contents", []):
                s3.delete_object(Bucket=DEMO_BUCKET, Key=obj["Key"])
            s3.delete_bucket(Bucket=DEMO_BUCKET)
        except Exception as e:
            print("cleanup: could not remove the demo bucket: " + str(e), file=sys.stderr)
        reset_demo_db(drop_only=True)
        shutil.rmtree(tmp, ignore_errors=True)

    parity = None
    if not args.skip_parity:
        print("python3 parity/run.py (about a minute) ...", file=sys.stderr)
        parity = run_parity(session.t0)

    commit = sh("git", "rev-parse", "--short", "HEAD")
    dirty = bool(sh("git", "status", "--porcelain", "--untracked-files=no"))
    doc = {
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "commit": commit,
        "branch": sh("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": dirty,
        "versions": {
            "python": sh(PY, "--version"),
            "django": sh(PY, "-c", "import django; print(django.get_version())"),
            "celery": celery_version,
            "go": sh("go", "version"),
            "mysql": mysql_version,
            "redis": redis_version,
            "s3": "RustFS (docker compose service s3)",
        },
        "setup": {
            "sync_engine": "python (in the Django request)",
            "job_engine": "go (Celery worker, PLAN_ENGINE=go; the result is verified by the Python verifier)",
            "go_invocations": go_invocations,
            "redacted": ["export.url: the presigned S3 URL is replaced with null (it carries the local access key id "
                         "and a signature). export_check records that it was downloaded first."],
        },
        "inputs": inputs_summary(),
        "stops": steps,   # not "steps": the site reserves that key for {input, lines} terminal steps
        "parity": parity,
    }
    with open(args.out, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print("wrote " + args.out + ": " + str(len(steps)) + " stops, commit " + commit + (" (DIRTY)" if dirty else ""),
          file=sys.stderr)


if __name__ == "__main__":
    main()
