from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from study.models import Asset
from study.storage import private_media_root, remove_asset


class Command(BaseCommand):
    help = "Remove private files that are not represented by an Asset after a 24-hour grace period."

    def handle(self, *args, **options):
        root = private_media_root()
        if not root.exists():
            return
        referenced = set(Asset.objects.values_list("storage_key", flat=True))
        cutoff = timezone.now().timestamp() - timedelta(hours=24).total_seconds()
        removed = 0
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            key = path.relative_to(root).as_posix()
            if key not in referenced and path.stat().st_mtime < cutoff:
                remove_asset(key)
                removed += 1
        self.stdout.write(f"Removed {removed} unreferenced assets.")
