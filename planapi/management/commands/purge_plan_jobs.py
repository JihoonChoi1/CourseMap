"""Delete old PlanJobs. There's no automatic schedule, so run this manually when needed.

    python3 manage.py purge_plan_jobs --days 7     # delete jobs older than 7 days (regardless of status)

If a job still queued gets deleted, the worker simply won't find it and does nothing.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from planapi.models import PlanJob


class Command(BaseCommand):
    help = "Delete PlanJobs older than N days"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, required=True)

    def handle(self, *args, **options):
        days = options["days"]
        if days < 0:
            raise CommandError("--days는 0 이상이어야 함")
        cutoff = timezone.now() - timedelta(days=days)
        n, _ = PlanJob.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write("삭제: job " + str(n) + "개 (" + cutoff.isoformat() + " 이전 생성)")
