import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from tracking import telegram
from tracking.models import Event
from tracking.state import StateConflict, close_event, open_event


class Command(BaseCommand):
    help = "Open or close events when their scheduled start or end time passes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Keep checking at a fixed interval.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=30,
            help="Seconds between checks with --loop (default 30).",
        )

    def handle(self, *args, **options):
        if options["loop"]:
            interval = max(5, options["interval"])
            while True:
                self._check(timezone.now())
                time.sleep(interval)
        else:
            self._check(timezone.now())

    def _check(self, now):
        opened = []
        closed = []
        skipped = []

        due_open = Event.objects.filter(
            status=Event.Status.OPEN,
            schedule_ends_at__lte=now,
        ).order_by("schedule_ends_at", "pk")
        for event in due_open:
            try:
                close_event(event.pk, now=now)
                closed.append(event.pk)
            except StateConflict as exc:
                skipped.append((event.pk, str(exc)))

        due_draft = Event.objects.filter(
            status=Event.Status.DRAFT,
            schedule_starts_at__lte=now,
            schedule_ends_at__gt=now,
        ).order_by("schedule_starts_at", "pk")
        for event in due_draft:
            try:
                if event.group_id is None:
                    raise StateConflict("Set a Telegram group before opening the event.")
                if Event.objects.filter(
                    status=Event.Status.OPEN, group_id=event.group_id
                ).exists():
                    raise StateConflict("Another event is already open for this group.")
                telegram.verify_group(event.group_id)
                open_event(event.pk, now=now)
                opened.append(event.pk)
            except (StateConflict, telegram.TelegramError) as exc:
                skipped.append((event.pk, str(exc)))

        for event_id in closed:
            self.stdout.write(f"Closed event {event_id}.")
        for event_id in opened:
            self.stdout.write(f"Opened event {event_id}.")
        for event_id, reason in skipped:
            self.stderr.write(f"Skipped event {event_id}: {reason}")
