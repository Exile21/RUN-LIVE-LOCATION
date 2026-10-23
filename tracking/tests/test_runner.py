from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tracking import telegram
from tracking.forms import JoinEventForm
from tracking.models import Event, Participant
from tracking.tests.helpers import make_event, make_participant

User = get_user_model()


@override_settings(TELEGRAM_BOT_USERNAME="runline_bot")
class RunnerAccountTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("staff", password="password", is_staff=True)
        self.runner = User.objects.create_user("runner", password="StrongPass-123")
        self.event = make_event(
            status=Event.Status.OPEN,
            schedule_starts_at=timezone.now() - timedelta(hours=1),
            schedule_ends_at=timezone.now() + timedelta(hours=1),
        )

    def test_signup_creates_a_runner_account_and_opens_dashboard(self):
        response = self.client.get(reverse("signup"))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("signup"),
            {
                "username": "newrunner",
                "password1": "StrongPass-123",
                "password2": "StrongPass-123",
            },
        )
        self.assertRedirects(response, reverse("runner_dashboard"))
        user = User.objects.get(username="newrunner")
        self.assertFalse(user.is_staff)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("runner_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_anonymous_join_goes_to_signup_then_back_to_the_event(self):
        join_url = reverse("join_event", args=[self.event.enrollment_token])
        response = self.client.get(join_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("signup"), response["Location"])
        self.assertIn("next=", response["Location"])

        response = self.client.post(
            reverse("signup") + "?next=" + join_url,
            {
                "username": "joinedrunner",
                "password1": "StrongPass-123",
                "password2": "StrongPass-123",
                "next": join_url,
            },
        )
        self.assertRedirects(response, join_url)
        self.assertContains(self.client.get(join_url), self.event.name)

    def test_login_with_next_returns_to_the_original_join_page(self):
        join_url = reverse("join_event", args=[self.event.enrollment_token])
        response = self.client.post(
            reverse("login") + "?next=" + join_url,
            {"username": "runner", "password": "StrongPass-123", "next": join_url},
        )
        self.assertRedirects(response, join_url)

    def test_signup_ignores_an_external_next_url(self):
        response = self.client.post(
            reverse("signup") + "?next=https://example.com/",
            {
                "username": "saferunner",
                "password1": "StrongPass-123",
                "password2": "StrongPass-123",
                "next": "https://example.com/",
            },
        )
        self.assertRedirects(response, reverse("runner_dashboard"))

    def test_staff_can_send_the_join_link_to_the_telegram_group(self):
        self.client.force_login(self.staff)
        with patch("tracking.views.telegram.telegram_call") as send:
            response = self.client.post(
                reverse("event_share_telegram", args=[self.event.pk]), follow=True
            )
        send.assert_called_once()
        method, payload = send.call_args.args
        self.assertEqual(method, "sendMessage")
        self.assertEqual(payload["chat_id"], self.event.group_id)
        self.assertIn(
            reverse("join_event", args=[self.event.enrollment_token]), payload["text"]
        )
        self.assertContains(response, "sent to the Telegram group")

    def test_draft_event_accepts_early_registration_and_shares_its_link(self):
        draft = make_event(
            name="Upcoming",
            status=Event.Status.DRAFT,
            schedule_starts_at=timezone.now() + timedelta(hours=2),
            schedule_ends_at=timezone.now() + timedelta(hours=4),
        )
        self.assertTrue(JoinEventForm({"code": draft.enrollment_token}).is_valid())

        self.client.force_login(self.staff)
        self.assertContains(
            self.client.get(reverse("event_detail", args=[draft.pk])), ">Draft<"
        )

        self.client.force_login(self.runner)
        join_url = reverse("join_event", args=[draft.enrollment_token])
        self.assertEqual(self.client.get(join_url).status_code, 200)
        self.assertRedirects(self.client.post(join_url), reverse("runner_dashboard"))
        self.assertTrue(Participant.objects.filter(event=draft, user=self.runner).exists())

        dashboard = self.client.get(reverse("runner_dashboard"))
        self.assertContains(dashboard, "Upcoming")
        self.assertContains(dashboard, "Registration open")
        self.assertContains(dashboard, "Telegram opens when the event starts")
        self.assertNotIn("start=p_", dashboard.content.decode())
        map_url = reverse("spectator_detail", args=[draft.spectator_token])
        self.assertContains(dashboard, f'href="{map_url}"')
        self.assertEqual(self.client.get(map_url).status_code, 200)

        self.client.force_login(self.staff)
        self.assertContains(
            self.client.get(reverse("event_detail", args=[draft.pk])), "Registration open"
        )
        listing = self.client.get(reverse("event_list"))
        self.assertContains(listing, "Registration open")
        self.assertContains(listing, 'data-event-status="registration"')
        self.assertContains(listing, 'data-status="registration"')
        with patch("tracking.views.telegram.telegram_call") as send:
            response = self.client.post(
                reverse("event_share_telegram", args=[draft.pk]), follow=True
            )
        send.assert_called_once()
        self.assertIn(join_url, send.call_args.args[1]["text"])
        self.assertContains(response, "sent to the Telegram group")
        self.assertContains(response, "Send to Telegram")
        self.assertNotContains(response, "Spectator link")

    def test_share_button_reports_telegram_failure_and_requires_staff(self):
        self.client.force_login(self.staff)
        with patch(
            "tracking.views.telegram.telegram_call",
            side_effect=telegram.TelegramError("Telegram is down."),
        ):
            response = self.client.post(
                reverse("event_share_telegram", args=[self.event.pk]), follow=True
            )
        self.assertContains(response, "Could not send the event to Telegram")

        self.client.force_login(self.runner)
        self.assertEqual(
            self.client.post(
                reverse("event_share_telegram", args=[self.event.pk])
            ).status_code,
            302,
        )

    def test_event_info_shows_live_location_guide(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("event_detail", args=[self.event.pk]))
        self.assertContains(response, "How to share Live Location")
        self.assertContains(response, "not the group")

    def test_runner_join_creates_a_registration_and_claim_link(self):
        self.client.force_login(self.runner)
        response = self.client.get(reverse("join_event", args=[self.event.enrollment_token]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.event.name)

        response = self.client.post(reverse("join_event", args=[self.event.enrollment_token]))
        self.assertRedirects(response, reverse("runner_dashboard"))
        participant = Participant.objects.get(event=self.event, user=self.runner)
        self.assertTrue(participant.withdrawn)
        self.assertIsNone(participant.telegram_user_id)

        dashboard = self.client.get(reverse("runner_dashboard"))
        self.assertContains(dashboard, self.event.name)
        self.assertContains(dashboard, "Connect Telegram")
        self.assertContains(dashboard, f"start=p_{participant.public_id.hex}")
        self.assertContains(dashboard, "How to share Live Location")
        self.assertContains(dashboard, "not the group")
        self.assertContains(dashboard, "Elapsed")
        self.assertContains(dashboard, "Laps")

    def test_join_form_accepts_pasted_web_and_telegram_links(self):
        for value in (
            f"https://example.com/join/{self.event.enrollment_token}/",
            f"https://t.me/runline_bot?start=e_{self.event.enrollment_token}",
            self.event.enrollment_token,
        ):
            with self.subTest(value=value):
                form = JoinEventForm({"code": value})
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["code"], self.event.enrollment_token)

    def test_closed_event_appears_in_past_events(self):
        Participant.objects.create(
            event=self.event,
            user=self.runner,
            display_name="Runner",
            withdrawn=True,
            session_ended=True,
        )
        self.event.status = Event.Status.CLOSED
        self.event.schedule_starts_at = timezone.now() - timedelta(hours=2)
        self.event.schedule_ends_at = timezone.now() - timedelta(hours=1)
        self.event.save(update_fields=["status", "schedule_starts_at", "schedule_ends_at"])
        self.client.force_login(self.runner)
        response = self.client.get(reverse("runner_dashboard"))
        self.assertContains(response, "Past events")
        self.assertContains(response, self.event.name)
        self.assertContains(response, "Final duration")

    def test_unopened_draft_whose_window_passed_shows_ended(self):
        self.event.status = Event.Status.DRAFT
        self.event.schedule_starts_at = timezone.now() - timedelta(hours=2)
        self.event.schedule_ends_at = timezone.now() - timedelta(hours=1)
        self.event.save(
            update_fields=["status", "schedule_starts_at", "schedule_ends_at"]
        )
        Participant.objects.create(
            event=self.event, user=self.runner, display_name="Runner"
        )
        self.client.force_login(self.runner)
        response = self.client.get(reverse("runner_dashboard"))
        self.assertContains(response, "Past events")
        self.assertContains(response, ">Ended<")
        self.assertNotContains(response, "Registration open")

    def test_leave_removes_a_registration_but_not_active_tracking(self):
        participant = Participant.objects.create(
            event=self.event,
            user=self.runner,
            display_name="Runner",
            withdrawn=True,
            session_ended=True,
        )
        self.client.force_login(self.runner)
        response = self.client.post(reverse("runner_leave", args=[participant.public_id]))
        self.assertRedirects(response, reverse("runner_dashboard"))
        self.assertFalse(Participant.objects.filter(pk=participant.pk).exists())

        tracked = make_participant(self.event, user_id=9001, user=self.runner)
        response = self.client.post(reverse("runner_leave", args=[tracked.public_id]))
        self.assertRedirects(response, reverse("runner_dashboard"))
        self.assertTrue(Participant.objects.filter(pk=tracked.pk).exists())

    def test_staff_can_add_a_lap_and_spectators_do_not_see_counts(self):
        participant = make_participant(self.event, user_id=9002, user=self.runner)
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("participant_add_lap", args=[self.event.pk, participant.public_id])
        )
        self.assertRedirects(
            response,
            reverse("event_detail", args=[self.event.pk]) + "?tab=runners",
            fetch_redirect_response=False,
        )
        participant.refresh_from_db()
        self.assertEqual(participant.laps, 1)

        staff_snapshot = self.client.get(
            reverse("staff_snapshot", args=[self.event.pk])
        ).json()
        self.assertEqual(staff_snapshot["runners"][0]["laps"], 1)
        spectator_snapshot = self.client.get(
            reverse("spectator_snapshot", args=[self.event.spectator_token])
        ).json()
        self.assertNotIn("laps", spectator_snapshot["runners"][0])

    def test_runner_cannot_open_staff_pages_or_add_laps(self):
        participant = make_participant(self.event, user_id=9003, user=self.runner)
        self.client.force_login(self.runner)
        self.assertEqual(self.client.get(reverse("event_list")).status_code, 302)
        response = self.client.post(
            reverse("participant_add_lap", args=[self.event.pk, participant.public_id])
        )
        self.assertEqual(response.status_code, 302)
        participant.refresh_from_db()
        self.assertEqual(participant.laps, 0)
