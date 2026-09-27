# CourseMap

CourseMap builds term-by-term course plans from a course catalog, degree requirements, and a student's completed courses. It accounts for prerequisites, corequisites, course offerings, and a per-term credit limit. When a plan cannot fit within the available terms, it returns the reasons it found instead of a partial schedule.

The repository includes a Python CLI, a small web interface, a Django API, and a Go version of the planning engine. The web interface and API use MySQL; background jobs run through Celery and Redis and can export their results to S3-compatible storage.

## Try the CLI

The CLI only needs Python. It reads the sample JSON files in `data/` by default.

```bash
python3 cli.py S1
python3 cli.py S3 --max-credits 12 --num-terms 4
python3 cli.py --all --data-dir data/ubc
```

The plan is printed as JSON on stdout; a short summary and verification result go to stderr. Exit code `0` means every requested plan is feasible and verified, `1` means at least one is infeasible, `2` means the input is invalid, and `3` means a generated plan failed verification.

## Run the web app

You'll need Python, Go 1.27, Docker with Compose, and an available port 8000. Create a virtual environment once, then use the included script to start the local services, load the data, build the Go engine, and run the web server and Celery worker.

```bash
python3 -m venv .venv
./run.sh web
```

Open <http://127.0.0.1:8000/>. The script loads `data/ubc` by default and stops the server and worker when you press Ctrl+C. To use the smaller fictional dataset or a different server port:

```bash
DATA_DIR=data PORT=8001 ./run.sh web
```

`./run.sh` runs a CLI/API/export demo instead. `./run.sh test` runs the Python, Django, Go, and cross-language parity tests. The setup starts MySQL, Redis, and local S3-compatible storage in Docker; those containers remain up after the script exits.

## API

The API reads students and catalog data from MySQL, so start the web app or load data before calling it.

| Method | Path | Result |
| --- | --- | --- |
| `GET` | `/api/students/{id}/plan` | Compute a plan immediately. |
| `POST` | `/api/students/{id}/plan-jobs` | Queue a plan and return its job ID. |
| `GET` | `/api/plan-jobs/{job_id}` | Check the job, plan, and export status. |

Both planning endpoints accept optional `max_credits` and `num_terms` query parameters. For example:

```bash
curl 'http://127.0.0.1:8000/api/students/U3/plan?max_credits=15&num_terms=4'
curl -X POST 'http://127.0.0.1:8000/api/students/U1/plan-jobs'
curl 'http://127.0.0.1:8000/api/plan-jobs/<job_id>'
```

A plan includes `student_id`, `feasible`, `terms`, `chosen`, and `errors`. An infeasible plan still returns HTTP 200; check `feasible` and `errors` in the response. A successful background job can include a temporary download URL for its exported JSON. The synchronous endpoint uses the Python engine; background jobs use Python by default and can use Go with `PLAN_ENGINE=go`.

## Data and scope

Each dataset has three files: `catalog.json` (courses and requirements between courses), `programs.json` (degree and track groups), and `students.json` (completed courses and planning limits). The planner supports required groups (`ALL`) and choose-a-number groups (`PICK_N`). A prerequisite or corequisite is represented as an outer AND of inner OR lists: `[["A", "B"], ["C"]]` means `(A or B) and C`.

`data/` is a fictional CS program for examples and tests. `data/ubc/` contains a selected set of UBC Vancouver CS courses and six sample student records. Its modeled requirements cover the CS major and project-defined career tracks, **not a complete UBC degree audit**. Some calendar rules and course alternatives are simplified or omitted; [data/ubc/SOURCES.md](data/ubc/SOURCES.md) records the sources and each conversion decision. Check current university requirements before using a plan for course registration.

Loading a dataset into the web app replaces the existing catalog, programs, and students in its database:

```bash
.venv/bin/python manage.py load_data --data-dir data/ubc
```

The CLI can read either dataset directly with `--data-dir`, without changing the database.
