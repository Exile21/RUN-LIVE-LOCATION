from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from tracking.telegram import TelegramError, TelegramSetupError
from tracking.tests.helpers import make_event


@override_settings(
    DEBUG=False,
    ALLOWED_HOSTS=["tracking.example.com"],
    PUBLIC_BASE_URL="https://tracking.example.com",
    TELEGRAM_BOT_TOKEN="test-token",
    TELEGRAM_WEBHOOK_SECRET="hook_secret-123",
)
class RegisterWebhookTests(TestCase):
    @patch("tracking.telegram.telegram_call")
    def test_registers_expected_webhook(self, telegram_call):
        telegram_call.return_value = True
        call_command("register_webhook", stdout=StringIO())
        telegram_call.assert_called_once()
        method, payload = telegram_call.call_args.args
        self.assertEqual(method, "setWebhook")
        self.assertEqual(payload["url"], "https://tracking.example.com/telegram/webhook/")
        self.assertEqual(
            payload["allowed_updates"],
            ["message", "edited_message", "callback_query", "chat_member"],
        )
        self.assertEqual(payload["max_connections"], 1)
        self.assertIs(payload["drop_pending_updates"], False)
        self.assertEqual(payload["secret_token"], "hook_secret-123")

    @override_settings(PUBLIC_BASE_URL="http://tracking.example.com")
    @patch("tracking.telegram.telegram_call")
    def test_rejects_non_https(self, telegram_call):
        with self.assertRaises(CommandError):
            call_command("register_webhook", stdout=StringIO())
        telegram_call.assert_not_called()

    @override_settings(PUBLIC_BASE_URL="https://other.example.com")
    @patch("tracking.telegram.telegram_call")
    def test_rejects_host_not_in_allowed_hosts(self, telegram_call):
        with self.assertRaises(CommandError):
            call_command("register_webhook", stdout=StringIO())
        telegram_call.assert_not_called()

    @override_settings(TELEGRAM_BOT_TOKEN="")
    @patch("tracking.telegram.telegram_call")
    def test_rejects_missing_token(self, telegram_call):
        with self.assertRaises(CommandError):
            call_command("register_webhook", stdout=StringIO())
        telegram_call.assert_not_called()

    @override_settings(TELEGRAM_WEBHOOK_SECRET="")
    @patch("tracking.telegram.telegram_call")
    def test_rejects_missing_secret(self, telegram_call):
        with self.assertRaises(CommandError):
            call_command("register_webhook", stdout=StringIO())
        telegram_call.assert_not_called()

    @override_settings(TELEGRAM_WEBHOOK_SECRET="bad secret!")
    @patch("tracking.telegram.telegram_call")
    def test_rejects_invalid_secret_characters(self, telegram_call):
        with self.assertRaises(CommandError):
            call_command("register_webhook", stdout=StringIO())
        telegram_call.assert_not_called()


@override_settings(
    DEBUG=False,
    ALLOWED_HOSTS=["tracking.example.com"],
    PUBLIC_BASE_URL="https://tracking.example.com",
    TELEGRAM_BOT_TOKEN="test-token",
    TELEGRAM_BOT_USERNAME="runline_bot",
    TELEGRAM_WEBHOOK_SECRET="hook_secret-123",
)
class CheckTelegramTests(TestCase):
    def setUp(self):
        self.event = make_event()

    def test_reports_bot_webhook_and_group(self):
        def handler(method, payload):
            if method == "getMe":
                return {"id": 999, "username": "runline_bot"}
            if method == "getWebhookInfo":
                return {
                    "url": "https://tracking.example.com/telegram/webhook/",
                    "pending_update_count": 0,
                }
            raise AssertionError(method)

        output = StringIO()
        with patch("tracking.telegram.telegram_call", side_effect=handler), patch(
            "tracking.telegram.verify_group", return_value=None
        ):
            call_command("check_telegram", stdout=output)
        text = output.getvalue()
        self.assertIn("@runline_bot", text)
        self.assertIn("ready", text)
        self.assertIn("Telegram setup checks passed", text)
        self.assertNotIn("test-token", text)
        self.assertNotIn(self.event.enrollment_token, text)

    @override_settings(TELEGRAM_BOT_USERNAME="different_bot")
    def test_reports_bot_username_mismatch(self):
        def handler(method, payload):
            if method == "getMe":
                return {"id": 999, "username": "runline_bot"}
            if method == "getWebhookInfo":
                return {
                    "url": "https://tracking.example.com/telegram/webhook/",
                    "pending_update_count": 0,
                }
            raise AssertionError(method)

        with patch("tracking.telegram.telegram_call", side_effect=handler), patch(
            "tracking.telegram.verify_group", return_value=None
        ):
            with self.assertRaises(CommandError):
                call_command("check_telegram", stdout=StringIO())

    def test_reports_setup_failure(self):
        def handler(method, payload):
            if method == "getMe":
                return {"id": 999, "username": "runline_bot"}
            if method == "getWebhookInfo":
                return {
                    "url": "https://tracking.example.com/telegram/webhook/",
                    "pending_update_count": 0,
                }
            raise AssertionError(method)

        with patch("tracking.telegram.telegram_call", side_effect=handler), patch(
            "tracking.telegram.verify_group",
            side_effect=TelegramSetupError("The bot must be a group administrator."),
        ):
            with self.assertRaises(CommandError):
                call_command("check_telegram", stdout=StringIO())

    def test_reports_webhook_host_mismatch(self):
        def handler(method, payload):
            if method == "getMe":
                return {"id": 999, "username": "runline_bot"}
            if method == "getWebhookInfo":
                return {"url": "https://elsewhere.example.com/telegram/webhook/", "pending_update_count": 0}
            raise AssertionError(method)

        with patch("tracking.telegram.telegram_call", side_effect=handler), patch(
            "tracking.telegram.verify_group", return_value=None
        ):
            with self.assertRaises(CommandError):
                call_command("check_telegram", stdout=StringIO())

    def test_bot_api_failure_is_a_command_error(self):
        with patch("tracking.telegram.telegram_call", side_effect=TelegramError("unreachable")):
            with self.assertRaises(CommandError):
                call_command("check_telegram", stdout=StringIO())


@override_settings(
    TELEGRAM_BOT_TOKEN="test-token",
    TELEGRAM_BOT_USERNAME="runline_bot",
)
class PollTelegramTests(TestCase):
    def test_once_processes_a_group_command(self):
        calls = []

        def handler(method, payload, **kwargs):
            calls.append((method, payload, kwargs))
            if method == "getWebhookInfo":
                return {"url": ""}
            if method == "getUpdates":
                return [
                    {
                        "update_id": 42,
                        "message": {
                            "message_id": 1,
                            "date": 1,
                            "chat": {"id": -100123, "type": "supergroup"},
                            "text": "/groupid",
                        },
                    }
                ]
            raise AssertionError(method)

        output = StringIO()
        with patch("tracking.telegram.telegram_call", side_effect=handler), patch(
            "tracking.bot.handle_update"
        ) as handle_update:
            call_command("poll_telegram", "--once", stdout=output)
        handle_update.assert_called_once()
        self.assertIn("Group ID: -100123", output.getvalue())
        get_updates = [call for call in calls if call[0] == "getUpdates"][0]
        self.assertEqual(get_updates[1]["timeout"], 25)
        self.assertIn("my_chat_member", get_updates[1]["allowed_updates"])
        self.assertEqual(get_updates[2]["timeout"], 30)

    def test_refuses_when_webhook_is_active(self):
        with patch("tracking.telegram.telegram_call", return_value={"url": "https://example.com/hook"}):
            with self.assertRaises(CommandError):
                call_command("poll_telegram", "--once", stdout=StringIO())

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_refuses_without_token(self):
        with self.assertRaises(CommandError):
            call_command("poll_telegram", "--once", stdout=StringIO())
