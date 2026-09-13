"""Load data/*.json into the DB. Existing data is fully replaced (in one transaction).

    python3 manage.py load_data                 # default: the project's data/
    python3 manage.py load_data --data-dir DIR

Shape checking is done by the engine loader (same rules as the CLI). If the shape is wrong,
nothing changes and it fails. Static validation errors (cycles, etc.) don't block loading — they're
reported as Plan.errors on request. Only a warning is printed here.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from engine.loader import InputError, load_all
from engine.validate import validate

from planapi.convert import replace_all


class Command(BaseCommand):
    help = "Load data/*.json (catalog, programs, students) into the DB (replacing existing data)"

    def add_arguments(self, parser):
        parser.add_argument("--data-dir", default=str(settings.DATA_DIR))

    def handle(self, *args, **options):
        try:
            catalog, programs, students = load_all(options["data_dir"])
        except InputError as e:
            raise CommandError("입력 오류: " + str(e))

        seen = {}
        for s in students:
            if s.id in seen:
                raise CommandError("학생 id 중복: " + s.id)
            seen[s.id] = True

        with transaction.atomic():
            replace_all(catalog, programs, students)

        self.stdout.write("적재 완료: 과목 " + str(len(catalog.courses)) + "개, 트랙 " + str(len(programs.tracks))
                          + "개, 학생 " + str(len(students)) + "명")
        static = validate(catalog, programs)
        for e in static.errors:
            self.stderr.write("경고(정적 검증): " + e.code + ": " + e.message)
