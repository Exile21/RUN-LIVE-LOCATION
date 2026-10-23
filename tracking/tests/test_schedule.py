from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from tracking.models import Event
from tracking.telegram import TelegramError
from tracking.tests.helpers import make_event


class EventScheduleCommandTests(TestCase):
    def run_command(self):
        stdout = StringIO()
        stderr = StringIO()
        call_command("run_event_schedule", stdout=stdout, stderr=stderr)
        return stdout.getvalue(), stderr.getvalue()

    @patch("tracking.management.commands.run_event_schedule.telegram.verify_group")
    def test_opens_due_draft_and_closes_due_open(self, verify_group):
        now = timezone.now()
        due_open = make_event(
            status=Event.Status.OPEN,
            group_id=-1001,
            schedule_starts_at=now - timedelta(hours=2),
            schedule_ends_at=now - timedelta(minutes=1),
        )
        due_draft = make_event(
            status=Event.Status.DRAFT,
            group_id=-1002,
            schedule_starts_at=now - timedelta(minutes=1),
            schedule_ends_at=now + timedelta(hours=1),
        )
        future_draft = make_event(
            status=Event.Status.DRAFT,
            group_id=-1003,
            schedule_starts_at=now + timedelta(hours=1),
            schedule_ends_at=now + timedelta(hours=2),
        )
        future_open = make_event(
            status=Event.Status.OPEN,
            group_id=-1004,
            schedule_starts_at=now - timedelta(hours=1),
            schedule_ends_at=now + timedelta(hours=1),
        )
        missed_draft = make_event(
            status=Event.Status.DRAFT,
            group_id=-1005,
            schedule_starts_at=now - timedelta(hours=2),
            schedule_ends_at=now - timedelta(hours=1),
        )

        stdout, stderr = self.run_command()

        due_open.refresh_from_db()
        due_draft.refresh_from_db()
        future_draft.refresh_from_db()
        future_open.refresh_from_db()
        missed_draft.refresh_from_db()
        self.assertEqual(due_open.status, Event.Status.CLOSED)
        self.assertEqual(due_draft.status, Event.Status.OPEN)
        self.assertEqual(future_draft.status, Event.Status.DRAFT)
        self.assertEqual(future_open.status, Event.Status.OPEN)
        self.assertEqual(missed_draft.status, Event.Status.DRAFT)
        self.assertIn(f"Closed event {due_open.pk}", stdout)
        self.assertIn(f"Opened event {due_draft.pk}", stdout)
        verify_group.assert_called_once_with(due_draft.group_id)
        self.assertEqual(stderr, "")

    @patch("tracking.management.commands.run_event_schedule.telegram.verify_group")
    def test_closes_before_opening_the_next_event_in_the_same_group(self, verify_group):
        now = timezone.now()
        group_id = -1010
        previous = make_event(
            status=Event.Status.OPEN,
            group_id=group_id,
            schedule_starts_at=now - timedelta(hours=2),
            schedule_ends_at=now - timedelta(minutes=1),
        )
        following = make_event(
            status=Event.Status.DRAFT,
            group_id=group_id,
            schedule_starts_at=now - timedelta(minutes=1),
            schedule_ends_at=now + timedelta(hours=1),
        )

        self.run_command()

        previous.refresh_from_db()
        following.refresh_from_db()
        self.assertEqual(previous.status, Event.Status.CLOSED)
        self.assertEqual(following.status, Event.Status.OPEN)
        verify_group.assert_called_once_with(group_id)

    @patch("tracking.management.commands.run_event_schedule.telegram.verify_group")
    def test_skips_a_draft_when_another_event_is_still_open(self, verify_group):
        now = timezone.now()
        group_id = -1020
        current = make_event(
            status=Event.Status.OPEN,
            group_id=group_id,
            schedule_starts_at=now - timedelta(hours=1),
            schedule_ends_at=now + timedelta(hours=1),
        )
        draft = make_event(
            status=Event.Status.DRAFT,
            group_id=group_id,
            schedule_starts_at=now - timedelta(minutes=1),
            schedule_ends_at=now + timedelta(hours=2),
        )

        _, stderr = self.run_command()

        current.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual(current.status, Event.Status.OPEN)
        self.assertEqual(draft.status, Event.Status.DRAFT)
        self.assertIn("Another event is already open", stderr)
        verify_group.assert_not_called()

    @patch("tracking.management.commands.run_event_schedule.telegram.verify_group")
    def test_telegram_failure_leaves_the_draft_for_retry(self, verify_group):
        now = timezone.now()
        draft = make_event(
            status=Event.Status.DRAFT,
            group_id=-1030,
            schedule_starts_at=now - timedelta(minutes=1),
            schedule_ends_at=now + timedelta(hours=1),
        )
        verify_group.side_effect = TelegramError("Telegram is down.")

        _, stderr = self.run_command()

        draft.refresh_from_db()
        self.assertEqual(draft.status, Event.Status.DRAFT)
        self.assertIn("Telegram is down", stderr)

    @patch("tracking.management.commands.run_event_schedule.telegram.verify_group")
    def test_running_twice_does_not_repeat_transitions(self, verify_group):
        now = timezone.now()
        draft = make_event(
            status=Event.Status.DRAFT,
            group_id=-1040,
            schedule_starts_at=now - timedelta(minutes=1),
            schedule_ends_at=now + timedelta(hours=1),
        )

        self.run_command()
        self.run_command()

        draft.refresh_from_db()
        self.assertEqual(draft.status, Event.Status.OPEN)
        verify_group.assert_called_once_with(draft.group_id)
