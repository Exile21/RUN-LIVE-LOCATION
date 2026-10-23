from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase

from tracking import state
from tracking.models import Event, Participant
from tracking.tests.helpers import NOW, make_event, make_participant


class FreshnessTests(TestCase):
    def test_live_and_stale_boundary(self):
        event = make_event()
        participant = make_participant(
            event,
            position_at=NOW - timedelta(seconds=120),
            latitude=1.2,
            longitude=2.3,
        )
        self.assertEqual(state.tracking_status(participant, now=NOW), "live")
        participant.position_at = NOW - timedelta(seconds=121)
        self.assertEqual(state.tracking_status(participant, now=NOW), "stale")

    def test_expiry_overrides_freshness(self):
        event = make_event()
        participant = make_participant(
            event,
            position_at=NOW,
            live_expires_at=NOW,
            latitude=1.2,
            longitude=2.3,
        )
        self.assertEqual(state.tracking_status(participant, now=NOW), "expired")

    def test_waiting_expired_stopped_and_future(self):
        event = make_event()
        waiting = make_participant(event, user_id=2001)
        self.assertEqual(state.tracking_status(waiting, now=NOW), "waiting")
        ended = make_participant(
            event,
            user_id=2002,
            session_ended=True,
            position_at=NOW,
            latitude=1.2,
            longitude=2.3,
        )
        self.assertEqual(state.tracking_status(ended, now=NOW), "expired")
        withdrawn = make_participant(event, user_id=2003, withdrawn=True)
        self.assertEqual(state.tracking_status(withdrawn, now=NOW), "stopped")
        future = make_participant(
            event,
            user_id=2004,
            position_at=NOW + timedelta(minutes=5),
            latitude=1.2,
            longitude=2.3,
        )
        self.assertEqual(state.tracking_status(future, now=NOW), "stale")


class LifecycleTests(TestCase):
    def test_open_close_and_token_rotation(self):
        event = make_event(status=Event.Status.DRAFT, group_id=-1001)
        old_token = event.spectator_token
        opened = state.open_event(event.pk, now=NOW)
        self.assertEqual(opened.status, Event.Status.OPEN)
        self.assertNotEqual(state.rotate_spectator_token(event.pk), old_token)
        participant = make_participant(
            event,
            position_at=NOW,
            latitude=1.2,
            longitude=2.3,
            accuracy=10,
            heading=90,
        )
        closed = state.close_event(event.pk, now=NOW)
        self.assertEqual(closed.status, Event.Status.CLOSED)
        participant.refresh_from_db()
        self.assertTrue(participant.withdrawn)
        self.assertIsNone(participant.latitude)
        self.assertIsNone(participant.longitude)
        self.assertIsNone(participant.accuracy)
        self.assertIsNone(participant.heading)
        self.assertIsNone(participant.position_at)
        self.assertIsNone(participant.live_expires_at)
        self.assertFalse(participant.pending_nonce)
        self.assertEqual(state.close_event(event.pk, now=NOW).status, Event.Status.CLOSED)

    def test_close_draft_raises(self):
        event = make_event(status=Event.Status.DRAFT)
        with self.assertRaises(state.StateConflict):
            state.close_event(event.pk, now=NOW)

    def test_closed_event_cannot_reopen(self):
        event = make_event(status=Event.Status.CLOSED)
        with self.assertRaises(state.StateConflict):
            state.open_event(event.pk, now=NOW)

    def test_one_open_event_per_group(self):
        make_event(group_id=-1005)
        other = make_event(
            status=Event.Status.DRAFT,
            name="Second",
            group_id=-1005,
        )
        with self.assertRaises(state.StateConflict):
            state.open_event(other.pk, now=NOW)

    def test_unique_participation_and_active_tracking(self):
        event = make_event()
        make_participant(event, user_id=3001)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_participant(event, user_id=3001, display_name="Duplicate")
        other = make_event(name="Other", group_id=-10077)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_participant(other, user_id=3001)

    def test_open_requires_group(self):
        event = make_event(status=Event.Status.DRAFT, group_id=None)
        with self.assertRaises(state.StateConflict):
            state.open_event(event.pk, now=NOW)


class OnboardingTests(TestCase):
    def setUp(self):
        self.event = make_event()

    def begin(self, user_id=4001):
        return state.begin_onboarding(
            self.event.id,
            user_id,
            user_id,
            "Alex",
            100,
            sent_at=NOW - timedelta(minutes=1),
            now=NOW,
        )

    def test_consent_requires_current_nonce(self):
        participant = self.begin()
        self.assertTrue(participant.withdrawn)
        self.assertTrue(participant.pending_nonce)
        old_nonce = participant.pending_nonce
        self.assertFalse(
            state.grant_consent(participant.public_id, 4001, "wrong", 101, now=NOW)
        )
        self.assertTrue(
            state.grant_consent(participant.public_id, 4001, old_nonce, 101, now=NOW)
        )
        participant.refresh_from_db()
        self.assertFalse(participant.withdrawn)
        self.assertIsNotNone(participant.consented_at)
        self.assertFalse(participant.pending_nonce)
        self.assertFalse(
            state.grant_consent(participant.public_id, 4001, old_nonce, 102, now=NOW)
        )

    def test_new_onboarding_invalidates_old_nonce(self):
        participant = self.begin()
        old_nonce = participant.pending_nonce
        participant = state.begin_onboarding(
            self.event.id,
            4001,
            4001,
            "Alex",
            102,
            sent_at=NOW,
            now=NOW,
        )
        self.assertNotEqual(participant.pending_nonce, old_nonce)
        self.assertFalse(
            state.grant_consent(participant.public_id, 4001, old_nonce, 103, now=NOW)
        )

    def test_expired_pending_challenge(self):
        participant = self.begin()
        nonce = participant.pending_nonce
        self.assertFalse(
            state.grant_consent(
                participant.public_id,
                4001,
                nonce,
                101,
                now=NOW + timedelta(minutes=31),
            )
        )
        participant.refresh_from_db()
        self.assertFalse(participant.pending_nonce)

    def test_consent_with_old_callback_after_stop_is_rejected(self):
        participant = self.begin()
        nonce = participant.pending_nonce
        self.assertTrue(
            state.grant_consent(participant.public_id, 4001, nonce, 101, now=NOW)
        )
        state.withdraw_runner(
            4001,
            102,
            sent_at=NOW,
            now=NOW,
        )
        self.assertFalse(
            state.grant_consent(participant.public_id, 4001, nonce, 103, now=NOW)
        )
        participant.refresh_from_db()
        self.assertTrue(participant.withdrawn)
        self.assertIsNone(participant.latitude)

    def test_old_stop_cannot_withdraw_newer_onboarding(self):
        participant = self.begin()
        nonce = participant.pending_nonce
        self.assertTrue(
            state.grant_consent(participant.public_id, 4001, nonce, 101, now=NOW)
        )
        state.withdraw_runner(4001, 102, sent_at=NOW, now=NOW)
        newer = state.begin_onboarding(
            self.event.id,
            4001,
            4001,
            "Alex",
            103,
            sent_at=NOW + timedelta(minutes=1),
            now=NOW + timedelta(minutes=1),
        )
        state.withdraw_runner(
            4001,
            100,
            sent_at=NOW - timedelta(minutes=1),
            now=NOW + timedelta(minutes=2),
        )
        newer.refresh_from_db()
        self.assertTrue(newer.withdrawn)
        self.assertTrue(newer.pending_nonce)

    def test_active_runner_in_other_event_is_rejected(self):
        other = make_event(name="Other event", group_id=-10099)
        make_participant(other, user_id=5001)
        with self.assertRaises(state.AlreadyTracking):
            state.begin_onboarding(
                self.event.id,
                5001,
                5001,
                "Runner",
                10,
                sent_at=NOW,
                now=NOW,
            )

    def test_already_consented_runner_is_returned_without_new_challenge(self):
        participant = self.begin()
        self.assertTrue(
            state.grant_consent(
                participant.public_id,
                4001,
                participant.pending_nonce,
                101,
                now=NOW,
            )
        )
        participant.refresh_from_db()
        consent_time = participant.consented_at
        again = state.begin_onboarding(
            self.event.id,
            4001,
            4001,
            "Changed",
            102,
            sent_at=NOW + timedelta(minutes=1),
            now=NOW + timedelta(minutes=1),
        )
        self.assertFalse(again.withdrawn)
        self.assertEqual(again.consented_at, consent_time)
        self.assertEqual(again.display_name, "Alex")

    def test_stale_onboarding_is_ignored(self):
        participant = self.begin()
        state.grant_consent(participant.public_id, 4001, participant.pending_nonce, 101, now=NOW)
        state.withdraw_runner(4001, 105, sent_at=NOW, now=NOW)
        with self.assertRaises(state.StaleUpdate):
            state.begin_onboarding(
                self.event.id,
                4001,
                4001,
                "Alex",
                104,
                sent_at=NOW - timedelta(minutes=1),
                now=NOW,
            )
        self.assertFalse(Participant.objects.get(pk=participant.pk).pending_nonce)
