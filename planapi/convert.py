"""Conversion between DB rows <-> engine.model structs.

DB -> engine: rows are turned into a dict shaped like data/*.json, then passed to
engine.loader.parse_*. Goes through the same shape checks as file input, and keeps the engine side free of Django dependencies.
"""

from engine.loader import InputError, parse_catalog, parse_programs, parse_student
from engine.model import Catalog, Programs, Student as EngineStudent

from planapi import models


class StoredDataError(Exception):
    """The catalog/programs haven't been loaded into the DB, or their shape is broken (a server-side problem)."""


# ---- DB -> engine ----

def load_catalog_programs() -> tuple[Catalog, Programs]:
    raw_catalog, raw_programs = catalog_programs_raw()
    return parse_catalog_programs(raw_catalog, raw_programs)


def catalog_programs_raw() -> tuple[dict, dict]:
    """DB -> a dict shaped like catalog.json/programs.json. Also used as the input snapshot for async jobs."""
    meta = models.CatalogMeta.objects.first()
    if meta is None:
        raise StoredDataError("카탈로그가 적재되지 않음 (manage.py load_data 필요)")
    raw_catalog = {"seasons": meta.seasons, "courses": []}
    for c in models.Course.objects.order_by("position"):
        raw_catalog["courses"].append({
            "id": c.code, "title": c.title, "credits": c.credits,
            "offered": c.offered, "prereqs": c.prereqs, "coreqs": c.coreqs,
        })

    degrees = list(models.Program.objects.filter(kind=models.Program.DEGREE))
    if len(degrees) != 1:
        raise StoredDataError("졸업요건(DEGREE)이 " + str(len(degrees)) + "개 적재됨 (정확히 1개여야 함)")
    tracks = models.Program.objects.filter(kind=models.Program.TRACK).order_by("position")
    raw_programs = {"degree": _program_dict(degrees[0]), "tracks": [_program_dict(t) for t in tracks]}
    return raw_catalog, raw_programs


def parse_catalog_programs(raw_catalog: dict, raw_programs: dict) -> tuple[Catalog, Programs]:
    try:
        return parse_catalog(raw_catalog), parse_programs(raw_programs)
    except InputError as e:
        raise StoredDataError("저장된 데이터 형태 오류: " + str(e))


def _program_dict(p: models.Program) -> dict:
    groups = []
    for g in p.groups.order_by("position"):
        groups.append({"id": g.code, "name": g.name, "rule": g.rule, "n": g.n, "courses": g.courses})
    return {"id": p.code, "name": p.name, "groups": groups}


def load_student(row: models.Student, seasons: list[str], overrides: dict) -> EngineStudent:
    """overrides: some subset of {"num_terms": int, "max_credits_per_term": int}. A shape error raises InputError."""
    return parse_student(student_raw(row, overrides), seasons, student_where(row.code))


def student_raw(row: models.Student, overrides: dict) -> dict:
    """DB row -> a dict shaped like a students.json element (with overrides applied)."""
    raw = {
        "id": row.code, "name": row.name, "track": row.track, "completed": row.completed,
        "start_term": {"year": row.start_year, "season": row.start_season},
        "num_terms": row.num_terms, "max_credits_per_term": row.max_credits_per_term,
    }
    raw.update(overrides)
    return raw


def student_where(code: str) -> str:
    """Location tag attached to InputError messages."""
    return "student(" + code + ")"


# ---- engine -> DB (load_data) ----

def replace_all(catalog: Catalog, programs, students: list[EngineStudent]):
    """Delete all existing rows and insert new ones. The caller should wrap this in a transaction."""
    models.Group.objects.all().delete()
    models.Program.objects.all().delete()
    models.Course.objects.all().delete()
    models.CatalogMeta.objects.all().delete()
    models.Student.objects.all().delete()

    models.CatalogMeta.objects.create(seasons=catalog.seasons)
    rows = []
    for i, c in enumerate(catalog.courses):
        rows.append(models.Course(position=i, code=c.id, title=c.title, credits=c.credits,
                                  offered=c.offered, prereqs=c.prereqs, coreqs=c.coreqs))
    models.Course.objects.bulk_create(rows)

    _save_program(programs.degree, models.Program.DEGREE, 0)
    for i, t in enumerate(programs.tracks):
        _save_program(t, models.Program.TRACK, i)

    rows = []
    for s in students:
        rows.append(models.Student(code=s.id, name=s.name, track=s.track, completed=s.completed,
                                   start_year=s.start_year, start_season=s.start_season,
                                   num_terms=s.num_terms, max_credits_per_term=s.max_credits))
    models.Student.objects.bulk_create(rows)


def _save_program(p, kind: str, position: int):
    row = models.Program.objects.create(kind=kind, position=position, code=p.id, name=p.name)
    groups = []
    for i, g in enumerate(p.groups):
        groups.append(models.Group(program=row, position=i, code=g.id, name=g.name, rule=g.rule, n=g.n,
                                   courses=g.courses))
    models.Group.objects.bulk_create(groups)
