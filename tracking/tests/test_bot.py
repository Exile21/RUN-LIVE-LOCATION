import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from tracking import bot, state
from tracking.models import Event, Participant
from tracking.snapshots import event_snapshot
from tracking.tests.helpers import NOW, location_message, make_event, make_participant

User = get_user_model()


@override_settings(
    TELEGRAM_BOT_TOKEN="test-token",
    TELEGRAM_BOT_USERNAME="runline_bot",
    TELEGRAM_WEBHOOK_SECRET="hook-secret",
)
class BotTestCase(TestCase):
    def setUp(self):
        self.sent = []
        self.verify = patch("tracking.telegram.verify_member", return_value=True)
        self.verify_mock = self.verify.start()
        self.telegram = patch("tracking.telegram.telegram_call")
        self.telegram_mock = self.telegram.start()
        self.telegram_mock.side_effect = self._telegram
        self.addCleanup(self.verify.stop)
        self.addCleanup(self.telegram.stop)

    def _telegram(self, method, payload):
        self.sent.append((method, payload))
        return True

    def event(self, **kwargs):
        return make_event(**kwargs)

    def start_message(self, event, user_id=4001, update_id=10, sent_at=None):
        sent_at = sent_at or NOW - timedelta(seconds=5)
        return {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": int(sent_at.timestamp()),
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": user_id, "is_bot": False, "first_name": "Alex"},
                "text": f"/start e_{event.enrollment_token}",
            },
        }

    def callback(self, participant, update_id=20):
        return {
            "update_id": update_id,
            "callback_query": {
                "id": f"callback-{update_id}",
                "from": {"id": participant.telegram_user_id, "is_bot": False},
                "message": {
                    "message_id": 2,
                    "chat": {"id": participant.telegram_user_id, "type": "private"},
                },
                "data": f"agree:{participant.public_id.hex}:{participant.pending_nonce}",
            },
        }

    def consent(self, event, user_id=4001):
        bot.handle_update(self.start_message(event, user_id), now=NOW)
        participant = Participant.objects.get(event=event, telegram_user_id=user_id)
        bot.handle_update(self.callback(participant), now=NOW)
        participant.refresh_from_db()
        return participant


class OnboardingTests(BotTestCase):
    def test_new_member_welcome_contains_enrollment_link(self):
        event = self.event()
        update = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": int(NOW.timestamp()),
                "chat": {"id": event.group_id, "type": "supergroup"},
                "from": {"id": 777, "is_bot": True, "first_name": "Runline"},
                "new_chat_members": [{"id": 500, "is_bot": False, "first_name": "Sam"}],
            },
        }
        bot.handle_update(update, now=NOW)
        self.assertEqual(len(self.sent), 1)
        method, payload = self.sent[0]
        self.assertEqual(method, "sendMessage")
        self.assertIn(f"start=e_{event.enrollment_token}", payload["text"])
        self.assertIn("Share Live Location", payload["text"])

    def test_groupid_does_not_change_event(self):
        event = self.event()
        update = {
            "update_id": 2,
            "message": {
                "message_id": 2,
                "date": int(NOW.timestamp()),
                "chat": {"id": event.group_id, "type": "supergroup"},
                "from": {"id": 501, "is_bot": False, "first_name": "Sam"},
                "text": "/groupid",
            },
        }
        bot.handle_update(update, now=NOW)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.OPEN)
        self.assertEqual(self.sent[-1][1]["text"], f"Telegram group ID: {event.group_id}")

    def test_start_creates_pending_consent_challenge(self):
        event = self.event()
        bot.handle_update(self.start_message(event), now=NOW)
        participant = Participant.objects.get(event=event, telegram_user_id=4001)
        self.assertTrue(participant.withdrawn)
        self.assertTrue(participant.pending_nonce)
        self.assertIn("Joining Test event as Alex", self.sent[-1][1]["text"])
        self.assertIn("anyone with this event's link", self.sent[-1][1]["text"])
        self.assertIn("Telegram does not let this bot start it", self.sent[-1][1]["text"])
        self.assertIn("Share Live Location", self.sent[-1][1]["text"])
        self.assertIn("callback_data", self.sent[-1][1]["reply_markup"]["inline_keyboard"][0][0])

    def test_plain_start_explains_how_to_share_live_location(self):
        event = self.event()
        update = self.start_message(event)
        update["message"]["text"] = "/start"
        bot.handle_update(update, now=NOW)
        self.assertIn("Share Live Location", self.sent[-1][1]["text"])
        self.assertIn("attachment icon", self.sent[-1][1]["text"])

    def test_retried_start_resends_pending_consent(self):
        event = self.event()
        update = self.start_message(event)
        bot.handle_update(update, now=NOW)
        participant = Participant.objects.get(event=event, telegram_user_id=4001)
        nonce = participant.pending_nonce
        sent_count = len(self.sent)
        bot.handle_update(update, now=NOW)
        self.assertGreater(len(self.sent), sent_count)
        payload = self.sent[-1][1]
        self.assertEqual(payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[2], nonce)

    def test_retried_consent_callback_resends_instructions(self):
        event = self.event()
        bot.handle_update(self.start_message(event), now=NOW)
        participant = Participant.objects.get(event=event, telegram_user_id=4001)
        callback = self.callback(participant)
        bot.handle_update(callback, now=NOW)
        sent_count = len(self.sent)
        bot.handle_update(callback, now=NOW + timedelta(seconds=1))
        self.assertGreater(len(self.sent), sent_count)
        self.assertIn("Share Live Location", self.sent[-1][1]["text"])

    def test_nonmember_cannot_consent(self):
        event = self.event()
        self.verify_mock.return_value = False
        bot.handle_update(self.start_message(event), now=NOW)
        self.assertFalse(Participant.objects.filter(event=event).exists())
        self.assertIn("Join the event's Telegram group", self.sent[-1][1]["text"])

    def test_swapped_sender_is_rejected(self):
        event = self.event()
        update = self.start_message(event)
        update["message"]["chat"]["id"] = 9999
        bot.handle_update(update, now=NOW)
        self.assertFalse(Participant.objects.filter(event=event).exists())
        self.assertFalse(self.sent)

    def test_duplicate_callback_does_not_reset_consent(self):
        event = self.event()
        participant = self.consent(event)
        consented_at = participant.consented_at
        old_callback = {
            "update_id": 21,
            "callback_query": {
                "id": "callback-duplicate",
                "from": {"id": 4001, "is_bot": False},
                "message": {
                    "message_id": 2,
                    "chat": {"id": 4001, "type": "private"},
                },
                "data": f"agree:{participant.public_id.hex}:deadbeef",
            },
        }
        bot.handle_update(old_callback, now=NOW + timedelta(seconds=1))
        participant.refresh_from_db()
        self.assertEqual(participant.consented_at, consented_at)

    def test_stop_clears_position_and_old_consent_cannot_revive(self):
        event = self.event()
        participant = self.consent(event)
        bot.handle_update(
            {
                "update_id": 30,
                "message": location_message(
                    user_id=4001,
                    message_id=100,
                    date=NOW,
                    latitude=1.2,
                    longitude=2.3,
                ),
            },
            now=NOW,
        )
        participant.refresh_from_db()
        self.assertIsNotNone(participant.latitude)
        bot.handle_update(
            {
                "update_id": 31,
                "message": {
                    "message_id": 31,
                    "date": int(NOW.timestamp()),
                    "chat": {"id": 4001, "type": "private"},
                    "from": {"id": 4001, "is_bot": False, "first_name": "Alex"},
                    "text": "/stop",
                },
            },
            now=NOW,
        )
        participant.refresh_from_db()
        self.assertTrue(participant.withdrawn)
        self.assertIsNone(participant.latitude)
        self.assertIsNone(participant.position_at)
        bot.handle_update(self.callback(participant, update_id=32), now=NOW + timedelta(seconds=1))
        participant.refresh_from_db()
        self.assertTrue(participant.withdrawn)

    def test_group_removal_withdraws_runner(self):
        event = self.event()
        participant = self.consent(event)
        bot.handle_update(
            {
                "update_id": 50,
                "message": location_message(
                    user_id=4001,
                    message_id=100,
                    date=NOW,
                    latitude=1.2,
                    longitude=2.3,
                ),
            },
            now=NOW,
        )
        participant.refresh_from_db()
        self.assertIsNotNone(participant.latitude)
        update = {
            "update_id": 51,
            "chat_member": {
                "chat": {"id": event.group_id, "type": "supergroup"},
                "new_chat_member": {
                    "user": {"id": 4001, "is_bot": False, "first_name": "Alex"},
                    "status": "left",
                },
                "date": int(NOW.timestamp()),
            },
        }
        bot.handle_update(update, now=NOW)
        participant.refresh_from_db()
        self.assertTrue(participant.withdrawn)
        self.assertIsNone(participant.latitude)
        self.assertIsNone(participant.position_at)

    def test_old_private_lifecycle_does_not_affect_new_event(self):
        first = self.event(name="First", group_id=-100100)
        second = self.event(name="Second", group_id=-100200)
        first_participant = self.consent(first)
        bot.handle_update(
            {
                "update_id": 40,
                "message": {
                    "message_id": 40,
                    "date": int(NOW.timestamp()),
                    "chat": {"id": 4001, "type": "private"},
                    "from": {"id": 4001, "is_bot": False, "first_name": "Alex"},
                    "text": "/stop",
                },
            },
            now=NOW,
        )
        second_participant = self.consent(second)
        old_stop = {
            "update_id": 41,
            "message": {
                "message_id": 41,
                "date": int((NOW - timedelta(minutes=5)).timestamp()),
                "chat": {"id": 4001, "type": "private"},
                "from": {"id": 4001, "is_bot": False, "first_name": "Alex"},
                "text": "/stop",
            },
        }
        bot.handle_update(old_stop, now=NOW + timedelta(minutes=10))
        second_participant.refresh_from_db()
        self.assertFalse(second_participant.withdrawn)
        self.assertFalse(second_participant.pending_nonce)


class RegistrationClaimTests(BotTestCase):
    def _runner(self, username="runner"):
        return User.objects.create_user(username, password="StrongPass-123")

    def _registration(self, event, user):
        return Participant.objects.create(
            event=event,
            user=user,
            display_name="Runner",
            withdrawn=True,
            session_ended=True,
        )

    def test_personal_start_claims_a_web_registration(self):
        event = self.event()
        runner = self._runner()
        registration = self._registration(event, runner)
        update = self.start_message(event, user_id=5001)
        update["message"]["text"] = f"/start p_{registration.public_id.hex}"

        bot.handle_update(update, now=NOW)

        registration.refresh_from_db()
        self.assertEqual(registration.telegram_user_id, 5001)
        self.assertEqual(registration.private_chat_id, 5001)
        self.assertTrue(registration.pending_nonce)
        self.assertEqual(registration.user, runner)
        self.assertIn("Joining Test event as Alex", self.sent[-1][1]["text"])

    def test_claim_merges_an_existing_telegram_tracker_and_keeps_laps(self):
        event = self.event()
        runner = self._runner()
        registration = self._registration(event, runner)
        tracked = make_participant(event, user_id=5001, laps=2)
        update = self.start_message(event, user_id=5001)
        update["message"]["text"] = f"/start p_{registration.public_id.hex}"

        bot.handle_update(update, now=NOW)

        self.assertFalse(Participant.objects.filter(pk=registration.pk).exists())
        tracked.refresh_from_db()
        self.assertEqual(tracked.user, runner)
        self.assertEqual(tracked.laps, 2)
        self.assertEqual(Participant.objects.filter(event=event).count(), 1)
        self.assertIn("already sharing", self.sent[-1][1]["text"])

    def test_claimed_link_rejects_another_telegram_account(self):
        event = self.event()
        registration = self._registration(event, self._runner())
        registration.telegram_user_id = 5001
        registration.private_chat_id = 5001
        registration.save(update_fields=["telegram_user_id", "private_chat_id"])
        update = self.start_message(event, user_id=5002)
        update["message"]["text"] = f"/start p_{registration.public_id.hex}"

        bot.handle_update(update, now=NOW)

        registration.refresh_from_db()
        self.assertEqual(registration.telegram_user_id, 5001)
        self.assertEqual(self.sent[-1][1]["text"], "This runner link is no longer active.")


class LocationTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.participant = make_participant(self.event, user_id=4001)
        self.now = NOW
        self.initial = location_message(user_id=4001, message_id=100, date=NOW - timedelta(seconds=10))
        self.assertTrue(
            state.apply_location(4001, self.initial, 10, edited=False, now=NOW)
        )

    def edited(self, **kwargs):
        defaults = {
            "user_id": 4001,
            "message_id": 100,
            "date": NOW - timedelta(seconds=10),
            "edit_date": NOW,
            "latitude": 1.2,
            "longitude": 2.3,
        }
        defaults.update(kwargs)
        return location_message(**defaults)

    def test_new_live_message_and_edit_update_latest_position(self):
        self.assertTrue(state.apply_location(4001, self.edited(), 11, edited=True, now=NOW))
        participant = Participant.objects.get(pk=self.participant.pk)
        self.assertEqual(participant.latitude, 1.2)
        self.assertEqual(participant.longitude, 2.3)
        self.assertEqual(Participant.objects.filter(event=self.event).count(), 1)

    def test_heading_is_saved_and_shared_in_the_snapshot(self):
        self.assertTrue(
            state.apply_location(4001, self.edited(heading=135), 11, edited=True, now=NOW)
        )
        participant = Participant.objects.get(pk=self.participant.pk)
        self.assertEqual(participant.heading, 135)
        rows = event_snapshot(self.event, staff=False, now=NOW)["runners"]
        self.assertEqual(rows[0]["heading"], 135)

        without_heading = self.edited(
            edit_date=NOW + timedelta(seconds=5),
            latitude=1.3,
            longitude=2.4,
        )
        self.assertTrue(
            state.apply_location(4001, without_heading, 12, edited=True, now=NOW)
        )
        participant.refresh_from_db()
        self.assertIsNone(participant.heading)

    def test_invalid_heading_is_rejected(self):
        for heading in (0, 361, "north"):
            with self.subTest(heading=heading):
                with self.assertRaises(state.InvalidLocation):
                    state.apply_location(
                        4001,
                        self.edited(heading=heading),
                        11,
                        edited=True,
                        now=NOW,
                    )

    def test_duplicate_and_out_of_order_positions_do_not_regress(self):
        self.assertTrue(state.apply_location(4001, self.edited(), 11, edited=True, now=NOW))
        self.assertFalse(
            state.apply_location(
                4001,
                self.edited(edit_date=NOW, latitude=9.9, longitude=9.9),
                10,
                edited=True,
                now=NOW,
            )
        )
        self.assertFalse(
            state.apply_location(
                4001,
                self.edited(edit_date=NOW - timedelta(seconds=5), latitude=8.8, longitude=8.8),
                12,
                edited=True,
                now=NOW,
            )
        )
        participant = Participant.objects.get(pk=self.participant.pk)
        self.assertEqual(participant.latitude, 1.2)

    def test_old_message_id_forwarded_static_and_pre_consent_are_rejected(self):
        old = location_message(user_id=4001, message_id=90, date=NOW, latitude=7.7, longitude=7.7)
        self.assertFalse(state.apply_location(4001, old, 12, edited=False, now=NOW))

        forwarded = self.initial.copy()
        forwarded["forward_origin"] = {"type": "user"}
        with self.assertRaises(state.InvalidLocation):
            state.apply_location(4001, forwarded, 13, edited=False, now=NOW)

        static = location_message(user_id=4001, message_id=101, date=NOW)
        static["location"].pop("live_period")
        with self.assertRaises(state.InvalidLocation):
            state.apply_location(4001, static, 13, edited=False, now=NOW)

        pre_consent = location_message(
            user_id=4001,
            message_id=102,
            date=NOW - timedelta(hours=2),
            latitude=6.6,
            longitude=6.6,
        )
        self.assertFalse(state.apply_location(4001, pre_consent, 13, edited=False, now=NOW))

    def test_invalid_values_are_rejected(self):
        for latitude, longitude in ((float("nan"), 2.0), (91.0, 2.0), (1.0, 181.0)):
            with self.subTest(latitude=latitude, longitude=longitude):
                with self.assertRaises(state.InvalidLocation):
                    state.apply_location(
                        4001,
                        location_message(
                            user_id=4001,
                            message_id=110,
                            date=NOW,
                            latitude=latitude,
                            longitude=longitude,
                        ),
                        14,
                        edited=False,
                        now=NOW,
                    )
        future = location_message(
            user_id=4001,
            message_id=111,
            date=NOW + timedelta(minutes=5),
            latitude=1.2,
            longitude=2.3,
        )
        with self.assertRaises(state.InvalidLocation):
            state.apply_location(4001, future, 15, edited=False, now=NOW)

    def test_indefinite_live_period_and_expired_session(self):
        indefinite = location_message(
            user_id=4001,
            message_id=120,
            date=NOW,
            live_period=0x7FFFFFFF,
        )
        self.assertTrue(state.apply_location(4001, indefinite, 16, edited=False, now=NOW))
        participant = Participant.objects.get(pk=self.participant.pk)
        self.assertIsNone(participant.live_expires_at)
        self.assertEqual(state.tracking_status(participant, now=NOW), "live")

        participant.live_expires_at = NOW - timedelta(seconds=1)
        participant.save(update_fields=["live_expires_at"])
        self.assertFalse(
            state.apply_location(4001, self.edited(message_id=120), 17, edited=True, now=NOW)
        )
        participant.refresh_from_db()
        self.assertIsNone(participant.position_at)
        self.assertTrue(participant.session_ended)

    def test_stop_blocks_delayed_edits_until_new_consent_and_message(self):
        state.withdraw_runner(4001, 20, sent_at=NOW, now=NOW)
        self.assertFalse(state.apply_location(4001, self.edited(), 21, edited=True, now=NOW))
        participant = Participant.objects.get(pk=self.participant.pk)
        self.assertIsNone(participant.latitude)

    def test_withdrawal_during_membership_check_wins(self):
        def pull_membership(group_id, user_id):
            state.withdraw_runner(user_id, 30, sent_at=NOW, now=NOW)
            return True

        with patch("tracking.telegram.verify_member", side_effect=pull_membership):
            bot.handle_update(
                {
                    "update_id": 31,
                    "edited_message": self.edited(),
                },
                now=NOW,
            )
        participant = Participant.objects.get(pk=self.participant.pk)
        self.assertTrue(participant.withdrawn)
        self.assertIsNone(participant.latitude)
        self.assertIsNone(participant.position_at)


class SnapshotTests(TestCase):
    def test_duplicate_names_keep_distinct_public_ids(self):
        event = make_event()
        make_participant(
            event,
            user_id=7001,
            display_name="Alex",
            position_at=NOW,
            latitude=1.2,
            longitude=2.3,
        )
        make_participant(
            event,
            user_id=7002,
            display_name="Alex",
            position_at=NOW,
            latitude=1.4,
            longitude=2.5,
        )
        rows = event_snapshot(event, staff=False, now=NOW)["runners"]
        self.assertEqual([row["name"] for row in rows], ["Alex", "Alex"])
        self.assertEqual(len({row["id"] for row in rows}), 2)
        self.assertEqual(
            set(rows[0]),
            {"id", "number", "name", "status", "latitude", "longitude", "accuracy", "heading", "position_at"},
        )
        self.assertNotIn("telegram_user_id", rows[0])

    def test_event_isolation_and_hidden_states(self):
        event = make_event()
        other = make_event(name="Other", group_id=-100200)
        make_participant(event, user_id=7101, position_at=NOW, latitude=1.2, longitude=2.3)
        make_participant(event, user_id=7102)
        make_participant(
            event,
            user_id=7103,
            session_ended=True,
            position_at=NOW,
            latitude=1.2,
            longitude=2.3,
        )
        make_participant(other, user_id=7104, position_at=NOW, latitude=9.0, longitude=9.0)
        snapshot = event_snapshot(event, staff=False, now=NOW)
        self.assertEqual(snapshot["summary"]["live"], 1)
        self.assertEqual(len(snapshot["runners"]), 3)
        self.assertNotIn("9.0", json.dumps(snapshot))
        statuses = {row["status"] for row in snapshot["runners"]}
        self.assertEqual(statuses, {"live", "waiting", "expired"})
        for row in snapshot["runners"]:
            if row["status"] not in {"live", "stale"}:
                self.assertIsNone(row["latitude"])
                self.assertIsNone(row["longitude"])

    def test_stale_boundary_and_closed_event(self):
        event = make_event()
        make_participant(
            event,
            user_id=7201,
            position_at=NOW - timedelta(seconds=120),
            latitude=1.2,
            longitude=2.3,
        )
        make_participant(
            event,
            user_id=7202,
            position_at=NOW - timedelta(seconds=121),
            latitude=1.3,
            longitude=2.4,
        )
        snapshot = event_snapshot(event, staff=False, now=NOW)
        self.assertEqual([row["status"] for row in snapshot["runners"]], ["live", "stale"])
        event.status = Event.Status.CLOSED
        event.save(update_fields=["status"])
        closed = event_snapshot(event, staff=False, now=NOW)
        self.assertEqual(closed["runners"], [])
        self.assertEqual(closed["summary"]["participants"], 0)

    def test_withdrawn_runner_is_publicly_hidden_but_visible_to_staff(self):
        event = make_event()
        make_participant(event, user_id=7301, withdrawn=True)
        self.assertEqual(event_snapshot(event, staff=False, now=NOW)["runners"], [])
        staff_rows = event_snapshot(event, staff=True, now=NOW)["runners"]
        self.assertEqual(staff_rows[0]["status"], "stopped")
        self.assertIsNone(staff_rows[0]["latitude"])

    def test_99_participant_snapshot_is_single_query(self):
        event = make_event()
        Participant.objects.bulk_create(
            [
                Participant(
                    event=event,
                    telegram_user_id=8000 + index,
                    private_chat_id=8000 + index,
                    display_name=f"Runner {index}",
                    withdrawn=False,
                    consented_at=NOW - timedelta(hours=1),
                    consent_version="2026-10-21",
                    session_ended=False,
                    latitude=1.0 + index / 1000,
                    longitude=2.0 + index / 1000,
                    position_at=NOW,
                )
                for index in range(99)
            ]
        )
        with self.assertNumQueries(1):
            snapshot = event_snapshot(event, staff=False, now=NOW)
        self.assertEqual(len(snapshot["runners"]), 99)
        self.assertEqual(snapshot["summary"]["participants"], 99)


class WebhookTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.event = make_event()

    def post(self, update, secret="hook-secret"):
        headers = {}
        if secret is not None:
            headers["HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN"] = secret
        return self.client.post(
            reverse("telegram_webhook"),
            data=json.dumps(update),
            content_type="application/json",
            **headers,
        )

    @override_settings(TELEGRAM_WEBHOOK_SECRET="hook-secret", TELEGRAM_BOT_TOKEN="test-token")
    def test_secret_and_method_validation(self):
        self.assertEqual(self.post({"update_id": 1}, secret=None).status_code, 403)
        self.assertEqual(self.post({"update_id": 1}, secret="wrong").status_code, 403)
        self.assertEqual(self.client.get(reverse("telegram_webhook")).status_code, 405)
        self.assertEqual(self.post({"update_id": 1}).status_code, 200)

    @override_settings(TELEGRAM_WEBHOOK_SECRET="", TELEGRAM_BOT_TOKEN="test-token")
    def test_unconfigured_secret_fails_closed(self):
        self.assertEqual(self.post({"update_id": 1}).status_code, 503)

    @override_settings(TELEGRAM_WEBHOOK_SECRET="hook-secret", TELEGRAM_BOT_TOKEN="test-token")
    def test_invalid_payloads(self):
        response = self.client.post(
            reverse("telegram_webhook"),
            data=b"not json",
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="hook-secret",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.post({"update_id": "not-an-int"}).status_code, 400)
        self.assertEqual(self.post([{"update_id": 1}]).status_code, 400)

    @override_settings(TELEGRAM_WEBHOOK_SECRET="hook-secret", TELEGRAM_BOT_TOKEN="test-token")
    def test_oversized_body_is_rejected(self):
        body = b'{"update_id":1,"x":"' + b"a" * (256 * 1024) + b'"}'
        response = self.client.post(
            reverse("telegram_webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="hook-secret",
        )
        self.assertEqual(response.status_code, 413)

    @override_settings(
        TELEGRAM_WEBHOOK_SECRET="hook-secret",
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_BOT_USERNAME="runline_bot",
    )
    def test_transient_telegram_failure_is_retryable(self):
        update = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": int(NOW.timestamp()),
                "chat": {"id": 4001, "type": "private"},
                "from": {"id": 4001, "is_bot": False, "first_name": "Alex"},
                "text": f"/start e_{self.event.enrollment_token}",
            },
        }
        from tracking.telegram import TelegramError

        with patch("tracking.telegram.verify_member", side_effect=TelegramError("unreachable")):
            self.assertEqual(self.post(update).status_code, 503)
