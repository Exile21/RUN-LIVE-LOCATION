from django.core.management.base import BaseCommand
from django.utils import timezone

from tracking.models import Event, TrackPoint
from tracking.state import TRACK_RETENTION


class Command(BaseCommand):
    help = "Delete recorded runner tracks 30 days after their event closed."

    def handle(self, *args, **options):
        cutoff = timezone.now() - TRACK_RETENTION
        deleted, _ = TrackPoint.objects.filter(
            participant__event__status=Event.Status.CLOSED,
            participant__event__closed_at__lt=cutoff,
        ).delete()
        self.stdout.write(f"Deleted {deleted} track points.")
