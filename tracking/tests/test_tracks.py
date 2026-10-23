from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracking import state
from tracking.models import Event, TrackPoint
from tracking.tests.helpers import NOW, location_message, make_event, make_participant

User = get_user_model()


class TrackRecordingTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.participant = make_participant(self.event, user_id=4001)

    def test_accepted_positions_are_recorded_and_close_keeps_them(self):
        self.assertTrue(
            state.apply_location(
                4001,
                location_message(user_id=4001, message_id=100, date=NOW - timedelta(seconds=10)),
                10,
                edited=False,
                now=NOW,
            )
        )
        self.assertEqual(TrackPoint.objects.count(), 1)

        state.close_event(self.event.pk, now=NOW)

        self.assertEqual(TrackPoint.objects.count(), 1)
        self.participant.refresh_from_db()
        self.assertIsNone(self.participant.latitude)

    def test_old_consent_version_is_not_recorded(self):
        self.participant.consent_version = "2026-10-21"
        self.participant.save(update_fields=["consent_version"])

        self.assertTrue(
            state.apply_location(
                4001,
                location_message(user_id=4001, message_id=100, date=NOW - timedelta(seconds=10)),
                10,
                edited=False,
                now=NOW,
            )
        )
        self.assertFalse(TrackPoint.objects.exists())

    def test_stop_deletes_the_recorded_track(self):
        state.apply_location(
            4001,
            location_message(user_id=4001, message_id=100, date=NOW - timedelta(seconds=10)),
            10,
            edited=False,
            now=NOW,
        )
        self.assertEqual(TrackPoint.objects.count(), 1)

        state.withdraw_runner(4001, 20, sent_at=NOW, now=NOW)

        self.assertFalse(TrackPoint.objects.exists())


class TrackReviewTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="StrongPass-123")
        self.other = User.objects.create_user("other", password="StrongPass-123")
        self.event = make_event(
            name="Recorded event",
            status=Event.Status.CLOSED,
            closed_at=NOW,
            schedule_starts_at=NOW - timedelta(hours=2),
            schedule_ends_at=NOW - timedelta(hours=1),
        )
        self.participant = make_participant(self.event, user_id=5001, user=self.owner)
        for index, latitude in enumerate((1.01, 1.03, 1.05), start=1):
            TrackPoint.objects.create(
                participant=self.participant,
                latitude=latitude,
                longitude=2.01,
                accuracy=10,
                position_at=NOW - timedelta(minutes=index),
                received_at=NOW,
                update_id=index,
            )
        self.url = reverse("runner_track", args=[self.participant.public_id])

    def test_owner_can_review_own_track(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recorded event")
        self.assertContains(response, "Point 3 of 3")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("noindex", response["X-Robots-Tag"])

        dashboard = self.client.get(reverse("runner_dashboard"))
        self.assertContains(dashboard, self.url)

    def test_other_users_and_anonymous_cannot_review_the_track(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)

        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class TrackRetentionTests(TestCase):
    def test_purge_deletes_only_tracks_closed_more_than_30_days_ago(self):
        now = timezone.now()
        old_event = make_event(
            name="Old",
            group_id=-2001,
            status=Event.Status.CLOSED,
            closed_at=now - timedelta(days=31),
        )
        recent_event = make_event(
            name="Recent",
            group_id=-2002,
            status=Event.Status.CLOSED,
            closed_at=now - timedelta(days=29),
        )
        open_event = make_event(name="Open", group_id=-2003)
        old = make_participant(old_event, user_id=6001)
        recent = make_participant(recent_event, user_id=6002)
        open_participant = make_participant(open_event, user_id=6003)

        for participant in (old, recent, open_participant):
            TrackPoint.objects.create(
                participant=participant,
                latitude=1.0,
                longitude=2.0,
                position_at=NOW,
                received_at=NOW,
                update_id=1,
            )

        call_command("purge_track_points", verbosity=0)

        self.assertFalse(
            TrackPoint.objects.filter(participant=old).exists()
        )
        self.assertTrue(
            TrackPoint.objects.filter(participant=recent).exists()
        )
        self.assertTrue(
            TrackPoint.objects.filter(participant=open_participant).exists()
        )
