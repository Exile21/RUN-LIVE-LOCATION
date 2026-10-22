from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from tracking.models import Event, Participant
from tracking.telegram import TelegramError
from tracking.tests.helpers import NOW, make_event, make_participant, make_telegram_config

User = get_user_model()


class AccessTests(TestCase):
    def setUp(self):
        self.event = make_event()
        make_telegram_config(group_id=self.event.group_id)
        self.staff = User.objects.create_user(
            "staff", password="password", is_staff=True, is_active=True
        )
        self.ordinary = User.objects.create_user(
            "member", password="password", is_staff=False, is_active=True
        )
        self.inactive = User.objects.create_user(
            "inactive", password="password", is_staff=True, is_active=False
        )
        self.participant = make_participant(
            self.event,
            position_at=NOW - timedelta(seconds=10),
            latitude=1.2,
            longitude=2.3,
        )

    def test_root_redirects_to_events(self):
        response = self.client.get("/")
        self.assertRedirects(response, "/events/", fetch_redirect_response=False)

    def test_staff_pages_require_active_staff(self):
        url = reverse("event_list")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.ordinary)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.inactive)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("event_detail", args=[self.event.pk])).status_code, 200
        )

    def test_staff_snapshot_has_json_auth_statuses(self):
        url = reverse("staff_snapshot", args=[self.event.pk])
        self.assertEqual(self.client.get(url).status_code, 401)
        self.client.force_login(self.ordinary)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.staff)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])

    def test_state_changing_actions_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        detail = client.get(reverse("event_detail", args=[self.event.pk]))
        self.assertEqual(detail.status_code, 200)
        token = client.cookies["csrftoken"].value
        response = client.post(reverse("event_close", args=[self.event.pk]))
        self.assertEqual(response.status_code, 403)
        response = client.post(
            reverse("event_close", args=[self.event.pk]),
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.CLOSED)

    def test_public_token_cannot_mutate(self):
        response = self.client.post(reverse("event_close", args=[self.event.pk]))
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.OPEN)

    def test_spectator_access_and_headers(self):
        url = reverse("spectator_detail", args=[self.event.spectator_token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("noindex", response["X-Robots-Tag"])
        self.assertNotIn("Rotate link", response.content.decode())
        self.assertNotIn("Edit event", response.content.decode())
        self.assertEqual(
            self.client.get(reverse("spectator_detail", args=["unknown"])).status_code, 404
        )
        draft = make_event(name="Draft", group_id=-100999, status=Event.Status.DRAFT)
        self.assertEqual(
            self.client.get(reverse("spectator_detail", args=[draft.spectator_token])).status_code,
            404,
        )
        snapshot = self.client.get(reverse("spectator_snapshot", args=[self.event.spectator_token]))
        self.assertEqual(snapshot.status_code, 200)
        self.assertNotIn("telegram_user_id", snapshot.content.decode())

    def test_rotation_invalidates_old_links(self):
        old_token = self.event.spectator_token
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        client.get(reverse("event_detail", args=[self.event.pk]))
        csrf = client.cookies["csrftoken"].value
        response = client.post(
            reverse("event_rotate_link", args=[self.event.pk]),
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertNotEqual(self.event.spectator_token, old_token)
        self.assertEqual(
            self.client.get(reverse("spectator_detail", args=[old_token])).status_code, 404
        )
        self.assertEqual(
            self.client.get(reverse("spectator_snapshot", args=[old_token])).status_code, 404
        )
        self.assertEqual(
            self.client.get(
                reverse("spectator_detail", args=[self.event.spectator_token])
            ).status_code,
            200,
        )

    def test_closed_event_snapshot_hides_runners(self):
        self.event.status = Event.Status.CLOSED
        self.event.save(update_fields=["status"])
        self.client.force_login(self.staff)
        response = self.client.get(reverse("staff_snapshot", args=[self.event.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runners"], [])
        public = self.client.get(
            reverse("spectator_snapshot", args=[self.event.spectator_token])
        )
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()["runners"], [])
        detail = self.client.get(
            reverse("spectator_detail", args=[self.event.spectator_token])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Event closed", detail.content.decode())

    def test_opening_verifies_telegram_before_state_change(self):
        draft = make_event(name="Draft", group_id=-100777, status=Event.Status.DRAFT)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        client.get(reverse("event_detail", args=[draft.pk]))
        csrf = client.cookies["csrftoken"].value
        from unittest.mock import patch

        with patch("tracking.views.telegram.verify_group", side_effect=TelegramError("down")):
            response = client.post(
                reverse("event_open", args=[draft.pk]),
                HTTP_X_CSRFTOKEN=csrf,
            )
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.status, Event.Status.DRAFT)

        with patch("tracking.views.telegram.verify_group", return_value=None):
            response = client.post(
                reverse("event_open", args=[draft.pk]),
                HTTP_X_CSRFTOKEN=csrf,
            )
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.status, Event.Status.OPEN)

    def test_closed_event_cannot_reopen_or_edit(self):
        self.event.status = Event.Status.CLOSED
        self.event.save(update_fields=["status"])
        self.client.force_login(self.staff)
        edit = self.client.get(reverse("event_edit", args=[self.event.pk]))
        self.assertEqual(edit.status_code, 302)
        from unittest.mock import patch

        with patch("tracking.views.telegram.verify_group", return_value=None):
            response = self.client.post(reverse("event_open", args=[self.event.pk]))
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.CLOSED)

    def test_invalid_gpx_does_not_replace_route(self):
        original = self.event.route_segments
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        draft = make_event(name="Draft", group_id=-100555, status=Event.Status.DRAFT)
        client.get(reverse("event_detail", args=[draft.pk]))
        csrf = client.cookies["csrftoken"].value
        upload = SimpleUploadedFile("bad.gpx", b"<gpx><trk>", content_type="application/gpx+xml")
        response = client.post(
            reverse("event_edit", args=[draft.pk]),
            data={
                "name": "Broken",
                "schedule_starts_at": "2026-10-22T06:00",
                "schedule_ends_at": "2026-10-22T09:00",
                "route_file": upload,
            },
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 200)
        draft.refresh_from_db()
        self.assertEqual(draft.route_segments, original)
        self.assertEqual(draft.name, "Draft")

    def test_runner_names_are_escaped_in_json_script(self):
        self.participant.display_name = "</script><img src=x onerror=alert(1)>"
        self.participant.save(update_fields=["display_name"])
        self.client.force_login(self.staff)
        response = self.client.get(reverse("event_detail", args=[self.event.pk]))
        body = response.content.decode()
        self.assertNotIn("<img src=x", body)
        self.assertIn("\\u003C/script\\u003E", body)

    def test_staff_can_rename_runner(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        client.get(reverse("event_detail", args=[self.event.pk]))
        csrf = client.cookies["csrftoken"].value
        response = client.post(
            reverse("participant_rename", args=[self.event.pk, self.participant.public_id]),
            data={"display_name": "Renamed Runner"},
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 302)
        self.participant.refresh_from_db()
        self.assertEqual(self.participant.display_name, "Renamed Runner")

    def test_login_url_is_available(self):
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Sign in", response.content.decode())

    def test_event_form_validation_errors_are_rendered(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("event_create"), data={"name": "", "group_id": ""})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Upload a GPX route", response.content.decode())

    def test_upcoming_event_appears_on_event_list(self):
        from datetime import timedelta

        from django.utils import timezone

        starts_at = timezone.now() + timedelta(days=1)
        make_event(
            name="Upcoming test event",
            status=Event.Status.DRAFT,
            group_id=-100888,
            schedule_starts_at=starts_at,
            schedule_ends_at=starts_at + timedelta(hours=2),
        )
        self.client.force_login(self.staff)
        response = self.client.get(reverse("event_list"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Next scheduled event", response.content.decode())
        self.assertIn("Upcoming test event", response.content.decode())
