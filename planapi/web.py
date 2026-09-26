"""A single web page (/). Pick a student, generate a plan, and show it term by term.

The page calls the existing API (/api/students/{id}/plan, /api/students/{id}/plan-jobs,
/api/plan-jobs/{id}) as-is. This module only feeds the template the reference data it needs for
display (student list, course names/credits, track/group names).
"""

from django.conf import settings
from django.shortcuts import render

from planapi import convert, models


def page_data() -> dict | None:
    """Summary of the data loaded in the DB. None if the catalog is missing or broken."""
    try:
        raw_catalog, raw_programs = convert.catalog_programs_raw()
    except convert.StoredDataError:
        return None
    courses = {}
    for c in raw_catalog["courses"]:
        if c["id"] not in courses:
            courses[c["id"]] = {"title": c["title"], "credits": c["credits"], "offered": c["offered"]}
    groups = {}
    for p in [raw_programs["degree"]] + raw_programs["tracks"]:
        for g in p["groups"]:
            groups[g["id"]] = g["name"]
    tracks = {}
    for t in raw_programs["tracks"]:
        tracks[t["id"]] = t["name"]
    students = []
    for s in models.Student.objects.order_by("id"):
        students.append({
            "id": s.code, "name": s.name, "track": s.track, "completed": s.completed,
            "start": str(s.start_year) + " " + s.start_season,
            "num_terms": s.num_terms, "max_credits": s.max_credits_per_term,
        })
    return {"degree": raw_programs["degree"]["name"], "courses": courses, "groups": groups, "tracks": tracks,
            "students": students, "presign_minutes": settings.S3_PRESIGN_EXPIRES // 60}


def index(request):
    return render(request, "planapi/index.html", {"data": page_data()})
