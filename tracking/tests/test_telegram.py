import io
import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.test import TestCase, override_settings

from tracking import telegram


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self, size=-1):
        return self.payload if size < 0 else self.payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@override_settings(TELEGRAM_BOT_TOKEN="secret-token", TELEGRAM_BOT_USERNAME="runline_bot")
class TelegramClientTests(TestCase):
    def setUp(self):
        telegram._bot_id = None

    def tearDown(self):
        telegram._bot_id = None

    @patch("tracking.telegram.urlopen")
    def test_posts_json_and_returns_result(self, urlopen):
        captured = {}

        def handler(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse({"ok": True, "result": {"message_id": 7}})

        urlopen.side_effect = handler
        result = telegram.telegram_call("sendMessage", {"chat_id": 1, "text": "hello"})
        self.assertEqual(result, {"message_id": 7})
        self.assertEqual(captured["timeout"], 5)
        self.assertEqual(captured["request"].method, "POST")
        self.assertEqual(json.loads(captured["request"].data), {"chat_id": 1, "text": "hello"})
        self.assertIn("secret-token", captured["request"].full_url)

    @patch("tracking.telegram.urlopen")
    def test_transport_failures_are_sanitized(self, urlopen):
        raw = b'{"ok":false,"description":"Bad request for secret-token"}'
        urlopen.side_effect = HTTPError(
            "https://api.telegram.org/botsecret-token/getMe",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(raw),
        )
        with self.assertRaises(telegram.TelegramError) as raised:
            telegram.telegram_call("getMe", {})
        self.assertNotIn("secret-token", str(raised.exception))
        self.assertNotIn("https://api.telegram.org", str(raised.exception))
        self.assertIn("HTTP 429", str(raised.exception))

        urlopen.side_effect = URLError(
            "failed https://api.telegram.org/botsecret-token/getMe"
        )
        with self.assertRaises(telegram.TelegramError) as raised:
            telegram.telegram_call("getMe", {})
        self.assertNotIn("secret-token", str(raised.exception))
        self.assertNotIn("https://api.telegram.org", str(raised.exception))

    @patch("tracking.telegram.urlopen")
    def test_invalid_responses_are_errors(self, urlopen):
        urlopen.return_value = FakeResponse({"ok": False, "description": "denied secret-token"})
        with self.assertRaises(telegram.TelegramError) as raised:
            telegram.telegram_call("getMe", {})
        self.assertNotIn("secret-token", str(raised.exception))

        class BadResponse(FakeResponse):
            def __init__(self):
                self.payload = b"not json"

        urlopen.return_value = BadResponse()
        with self.assertRaises(telegram.TelegramError):
            telegram.telegram_call("getMe", {})

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_missing_token_is_configuration_error(self):
        with self.assertRaises(telegram.TelegramConfigError):
            telegram.telegram_call("getMe", {})

    @patch("tracking.telegram.urlopen")
    def test_verify_group_and_member_statuses(self, urlopen):
        calls = []

        def handler(request, timeout):
            method = request.full_url.rsplit("/", 1)[-1]
            payload = json.loads(request.data)
            calls.append((method, payload))
            if method == "getMe":
                return FakeResponse({"ok": True, "result": {"id": 999, "username": "runline_bot"}})
            if method == "getChat":
                return FakeResponse({"ok": True, "result": {"type": "supergroup"}})
            if method == "getChatMember":
                if payload["user_id"] == 999:
                    return FakeResponse({"ok": True, "result": {"status": "administrator"}})
                statuses = {
                    1: {"status": "member"},
                    2: {"status": "administrator"},
                    3: {"status": "creator"},
                    4: {"status": "restricted", "is_member": True},
                    5: {"status": "restricted", "is_member": False},
                    6: {"status": "left"},
                    7: {"status": "kicked"},
                    8: {"status": "mystery"},
                }
                return FakeResponse({"ok": True, "result": statuses[payload["user_id"]]})
            raise AssertionError(method)

        urlopen.side_effect = handler
        telegram.verify_group(-100123)
        self.assertTrue(telegram.verify_member(-100123, 1))
        self.assertTrue(telegram.verify_member(-100123, 2))
        self.assertTrue(telegram.verify_member(-100123, 3))
        self.assertTrue(telegram.verify_member(-100123, 4))
        self.assertFalse(telegram.verify_member(-100123, 5))
        self.assertFalse(telegram.verify_member(-100123, 6))
        self.assertFalse(telegram.verify_member(-100123, 7))
        with self.assertRaises(telegram.TelegramSetupError):
            telegram.verify_member(-100123, 8)
        self.assertEqual(sum(1 for method, _ in calls if method == "getMe"), 1)

    @patch("tracking.telegram.urlopen")
    def test_demoted_bot_cannot_verify_members(self, urlopen):
        def handler(request, timeout):
            method = request.full_url.rsplit("/", 1)[-1]
            if method == "getMe":
                return FakeResponse({"ok": True, "result": {"id": 999}})
            return FakeResponse({"ok": True, "result": {"status": "member"}})

        urlopen.side_effect = handler
        with self.assertRaises(telegram.TelegramSetupError):
            telegram.verify_member(-100123, 1)
        with self.assertRaises(telegram.TelegramSetupError):
            telegram.verify_group(-100123)

    @patch("tracking.telegram.urlopen")
    def test_private_chat_is_not_a_group(self, urlopen):
        def handler(request, timeout):
            return FakeResponse({"ok": True, "result": {"type": "private"}})

        urlopen.side_effect = handler
        with self.assertRaises(telegram.TelegramSetupError):
            telegram.verify_group(1)
