"""Data structures used throughout the engine.

Keep only flat fields (string, int, list, dict) so this can be ported 1:1 to
a Go struct. Use module-level functions instead of methods.
"""

from dataclasses import dataclass, field

INF = 1 << 30  # "unreachable" term index


@dataclass
class Course:
    id: str
    title: str
    credits: int
    offered: list[str]        # names of the seasons it's offered in
    prereqs: list[list[str]]  # CNF: outer AND, inner OR (doc §2.2)
    coreqs: list[list[str]]


@dataclass
class Catalog:
    seasons: list[str]        # term order within a year (e.g. SPRING, FALL)
    courses: list[Course]     # preserves file order
    by_id: dict[str, Course]  # first occurrence wins on duplicate id
    index: dict[str, int]     # id -> position in file (for deterministic sorting)


@dataclass
class Group:
    id: str
    name: str
    rule: str                 # "ALL" | "PICK_N"
    n: int
    courses: list[str]


@dataclass
class Program:
    id: str
    name: str
    groups: list[Group]


@dataclass
class Programs:
    degree: Program
    tracks: list[Program]


@dataclass
class Student:
    id: str
    name: str
    track: str
    completed: list[str]
    start_year: int
    start_season: str
    num_terms: int
    max_credits: int


@dataclass
class EngineError:
    code: str                 # code from doc §5
    message: str
    courses: list[str] = field(default_factory=list)


@dataclass
class Unit:
    """A scheduling unit. Several courses if it's a bundle (mutual-coreq SCC), otherwise 1 course."""
    id: str                   # member course ids joined with '+'
    courses: list[str]
    credits: int
    offered: list[str]        # intersection of member courses' offered seasons


@dataclass
class Calendar:
    """Conversion info between term index t (0..num_terms-1) and (year, season)."""
    seasons: list[str]
    start_year: int
    start_idx: int            # position of start_season within seasons
    num_terms: int


def season_idx_at(cal: Calendar, t: int) -> int:
    return (cal.start_idx + t) % len(cal.seasons)


def season_at(cal: Calendar, t: int) -> str:
    return cal.seasons[season_idx_at(cal, t)]


def year_at(cal: Calendar, t: int) -> int:
    return cal.start_year + (cal.start_idx + t) // len(cal.seasons)


def term_label(cal: Calendar, t: int) -> str:
    return str(year_at(cal, t)) + " " + season_at(cal, t)


def sort_by_catalog(catalog: Catalog, ids: list[str]) -> list[str]:
    out = list(ids)
    out.sort(key=lambda cid: catalog.index.get(cid, INF))
    return out


def error_dict(e: EngineError) -> dict:
    return {"code": e.code, "message": e.message, "courses": e.courses}
