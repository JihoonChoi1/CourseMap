"""Create the settings.S3_BUCKET bucket if it doesn't exist (Phase 6, for initializing a local S3-compatible server).

    python3 manage.py ensure_s3_bucket
"""

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from planapi import s3export


class Command(BaseCommand):
    help = "Create the S3 export bucket (no-op if it already exists)"

    def handle(self, *args, **options):
        try:
            created = s3export.ensure_bucket()
        except (BotoCoreError, ClientError) as e:
            raise CommandError("failed to check/create the bucket: " + type(e).__name__ + ": " + str(e))
        where = str(settings.S3_ENDPOINT_URL or "AWS S3")
        self.stdout.write(("created: " if created else "already exists: ") + settings.S3_BUCKET + " (" + where + ")")
