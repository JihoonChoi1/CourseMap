"""JSON file -> model struct conversion.

This only checks "shape" (field presence, type). Semantic validation (§5 static rules) lives in
validate.py. Input with the wrong shape can't be represented as a Plan, so an InputError is raised.

parse_* takes an already-parsed dict (same shape as a JSON file). Kept separate from load_* so
data coming from somewhere other than a file (e.g. a DB) can go through the same shape checks.
"""

import json
import os

from engine.model import Catalog, Course, Group, Program, Programs, Student


class InputError(Exception):
    pass


def _field(obj: dict, key: str, kind, where: str):
    if not isinstance(obj, dict) or key not in obj:
        raise InputError(where + ": '" + key + "' 필드 없음")
    value = obj[key]
    # bool is a subtype of int, so filter it out explicitly
    if kind is int and isinstance(value, bool):
        raise InputError(where + ": '" + key + "'는 int여야 함")
    if not isinstance(value, kind):
        raise InputError(where + ": '" + key + "' 타입 오류")
    return value


def _str_list(obj: dict, key: str, where: str) -> list[str]:
    values = _field(obj, key, list, where)
    for v in values:
        if not isinstance(v, str):
            raise InputError(where + ": '" + key + "'는 문자열 리스트여야 함")
    return list(values)


def _cnf(obj: dict, key: str, where: str) -> list[list[str]]:
    clauses = _field(obj, key, list, where)
    out = []
    for clause in clauses:
        if not isinstance(clause, list):
            raise InputError(where + ": '" + key + "'는 [][]string이어야 함")
        for v in clause:
            if not isinstance(v, str):
                raise InputError(where + ": '" + key + "'는 [][]string이어야 함")
        out.append(list(clause))
    return out


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        raise InputError(path + ": 읽을 수 없음 (" + str(e) + ")")
    except json.JSONDecodeError as e:
        raise InputError(path + ": JSON 파싱 실패 (" + str(e) + ")")


def load_catalog(path: str) -> Catalog:
    return parse_catalog(_read_json(path))


def parse_catalog(raw: dict) -> Catalog:
    seasons = _str_list(raw, "seasons", "catalog")
    if len(seasons) == 0:
        raise InputError("catalog: 'seasons'가 비어 있음")
    courses = []
    by_id = {}
    index = {}
    for i, rc in enumerate(_field(raw, "courses", list, "catalog")):
        where = "catalog.courses[" + str(i) + "]"
        c = Course(
            id=_field(rc, "id", str, where),
            title=_field(rc, "title", str, where),
            credits=_field(rc, "credits", int, where),
            offered=_str_list(rc, "offered", where),
            prereqs=_cnf(rc, "prereqs", where),
            coreqs=_cnf(rc, "coreqs", where),
        )
        courses.append(c)
        if c.id not in by_id:
            by_id[c.id] = c
            index[c.id] = i
    return Catalog(seasons=seasons, courses=courses, by_id=by_id, index=index)


def _load_program(raw: dict, where: str) -> Program:
    groups = []
    for i, rg in enumerate(_field(raw, "groups", list, where)):
        gw = where + ".groups[" + str(i) + "]"
        groups.append(Group(
            id=_field(rg, "id", str, gw),
            name=_field(rg, "name", str, gw),
            rule=_field(rg, "rule", str, gw),
            n=_field(rg, "n", int, gw),
            courses=_str_list(rg, "courses", gw),
        ))
    return Program(id=_field(raw, "id", str, where), name=_field(raw, "name", str, where), groups=groups)


def load_programs(path: str) -> Programs:
    return parse_programs(_read_json(path))


def parse_programs(raw: dict) -> Programs:
    degree = _load_program(_field(raw, "degree", dict, "programs"), "programs.degree")
    tracks = []
    for i, rt in enumerate(_field(raw, "tracks", list, "programs")):
        tracks.append(_load_program(rt, "programs.tracks[" + str(i) + "]"))
    return Programs(degree=degree, tracks=tracks)


def load_students(path: str, seasons: list[str]) -> list[Student]:
    raw = _read_json(path)
    out = []
    for i, rs in enumerate(_field(raw, "students", list, "students")):
        out.append(parse_student(rs, seasons, "students[" + str(i) + "]"))
    return out


def parse_student(rs: dict, seasons: list[str], where: str) -> Student:
    start = _field(rs, "start_term", dict, where)
    s = Student(
        id=_field(rs, "id", str, where),
        name=_field(rs, "name", str, where),
        track=_field(rs, "track", str, where),
        completed=_str_list(rs, "completed", where),
        start_year=_field(start, "year", int, where + ".start_term"),
        start_season=_field(start, "season", str, where + ".start_term"),
        num_terms=_field(rs, "num_terms", int, where),
        max_credits=_field(rs, "max_credits_per_term", int, where),
    )
    if s.start_season not in seasons:
        raise InputError(where + ": 알 수 없는 start_term.season '" + s.start_season + "'")
    if s.num_terms < 1:
        raise InputError(where + ": num_terms는 1 이상이어야 함")
    if s.max_credits < 1:
        raise InputError(where + ": max_credits_per_term은 1 이상이어야 함")
    return s


def load_all(data_dir: str):
    catalog = load_catalog(os.path.join(data_dir, "catalog.json"))
    programs = load_programs(os.path.join(data_dir, "programs.json"))
    students = load_students(os.path.join(data_dir, "students.json"), catalog.seasons)
    return catalog, programs, students
