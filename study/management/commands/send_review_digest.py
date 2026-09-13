from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from study.email_delivery import get_delivery
from study.services.digest import deliver_digest, prepare_owner_digest, preview_owner_digest


class Command(BaseCommand):
    help = "Prepare and deliver the verified owner's current Singapore-day review digest."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show the selection without creating a Digest or contacting a provider.")

    def handle(self, *args, **options):
        now = timezone.now()
        try:
            if options["dry_run"]:
                preview = preview_owner_digest(now=now)
                if preview is None:
                    self.stdout.write("No eligible owner digest at this time.")
                    return
                self.stdout.write(f"Dry run: {preview['owner']} / {preview['day']} / {len(preview['articles'])} articles")
                for article in preview["articles"]:
                    self.stdout.write(f"{article['title']} — {article['url']}")
                return
            digest = prepare_owner_digest(now=now)
            if digest is None:
                self.stdout.write("No eligible owner digest at this time.")
                return
            status = deliver_digest(digest.pk, now=timezone.now(), delivery=get_delivery())
            digest.refresh_from_db(fields=("last_error",))
            detail = f" ({digest.last_error})" if digest.last_error else ""
            self.stdout.write(f"Owner digest {digest.day.isoformat()}: {status}{detail}")
        except (ImproperlyConfigured, ValueError) as exc:
            raise CommandError("Digest configuration or clock is invalid.") from exc
