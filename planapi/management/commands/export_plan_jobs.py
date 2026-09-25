"""Upload SUCCESS jobs' Plans to S3 (Phase 6). For jobs where the worker's automatic export failed, or that finished before export was enabled.

    python3 manage.py export_plan_jobs                 # every SUCCESS job whose export isn't SUCCESS (NULL/PENDING/FAILED)
    python3 manage.py export_plan_jobs --job-id UUID   # a specific job (re-uploads even if already uploaded)
    python3 manage.py export_plan_jobs --all           # re-upload every SUCCESS job

Safe to run multiple times since it overwrites the same key. Exit code 1 if any upload fails.
"""

import uuid

from django.core.management.base import BaseCommand, CommandError

from planapi import s3export
from planapi.models import PlanJob


class Command(BaseCommand):
    help = "Export SUCCESS jobs' Plans to S3 (retrying failures)"

    def add_arguments(self, parser):
        parser.add_argument("--job-id")
        parser.add_argument("--all", action="store_true")

    def handle(self, *args, **options):
        qs = PlanJob.objects.filter(status=PlanJob.SUCCESS)
        if options["job_id"]:
            try:
                key = uuid.UUID(options["job_id"])
            except ValueError:
                raise CommandError("job id 형식 오류: " + options["job_id"])
            qs = qs.filter(id=key)
            if not qs.exists():
                raise CommandError("SUCCESS job 없음: " + options["job_id"])
        elif not options["all"]:
            qs = qs.exclude(export_status=PlanJob.EXPORT_SUCCESS)
        ok = 0
        failed = 0
        for job_id in qs.order_by("created_at").values_list("id", flat=True):
            if s3export.export_job(str(job_id)):
                ok += 1
            else:
                failed += 1
                err = PlanJob.objects.values_list("export_error", flat=True).get(id=job_id)
                self.stderr.write("실패: " + str(job_id) + ": " + str(err))
        self.stdout.write("export 완료: 성공 " + str(ok) + "개, 실패 " + str(failed) + "개")
        if failed > 0:
            raise CommandError("export 실패 " + str(failed) + "개")
