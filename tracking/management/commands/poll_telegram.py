import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tracking import bot, telegram

ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "callback_query",
    "chat_member",
    "my_chat_member",
]


class Command(BaseCommand):
    help = "Poll Telegram for local development. Do not run while a webhook is registered."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Fetch and process one batch, then exit.",
        )

    def _group_hint(self, update: dict) -> None:
        message = update.get("message")
        if isinstance(message, dict):
            chat = message.get("chat")
            text = message.get("text")
            if (
                isinstance(chat, dict)
                and chat.get("type") in {"group", "supergroup"}
                and isinstance(text, str)
                and text.split(maxsplit=1)[0].split("@", 1)[0] == "/groupid"
            ):
                self.stdout.write(f"Group ID: {chat.get('id')}")
        member = update.get("my_chat_member")
        if isinstance(member, dict):
            chat = member.get("chat")
            status = (member.get("new_chat_member") or {}).get("status")
            if isinstance(chat, dict):
                self.stdout.write(f"Bot status in {chat.get('id')}: {status}")

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("TELEGRAM_BOT_TOKEN is not configured.")
        try:
            info = telegram.telegram_call("getWebhookInfo", {})
        except telegram.TelegramError as exc:
            raise CommandError(str(exc)) from None
        if isinstance(info, dict) and info.get("url"):
            raise CommandError(
                "A Telegram webhook is registered. Polling would conflict with it. "
                "Use the webhook in production, or remove the local webhook first."
            )

        self.stdout.write("Polling Telegram. Press Ctrl-C to stop.")
        offset = None
        while True:
            payload = {"timeout": 25, "allowed_updates": ALLOWED_UPDATES}
            if offset is not None:
                payload["offset"] = offset
            try:
                updates = telegram.telegram_call("getUpdates", payload, timeout=30)
            except telegram.TelegramError as exc:
                if options["once"]:
                    raise CommandError(str(exc)) from None
                self.stderr.write(f"Telegram polling failed: {exc}")
                time.sleep(3)
                continue
            if not isinstance(updates, list):
                if options["once"]:
                    raise CommandError("Telegram returned an invalid update batch.")
                time.sleep(3)
                continue

            next_offset = offset
            failure = None
            for update in updates:
                if not isinstance(update, dict):
                    continue
                update_id = update.get("update_id")
                if isinstance(update_id, bool) or not isinstance(update_id, int):
                    continue
                try:
                    bot.handle_update(update, now=timezone.now())
                except (ValueError, KeyError, TypeError):
                    self.stderr.write(f"Skipped invalid update {update_id}.")
                except telegram.TelegramError as exc:
                    failure = exc
                    break
                next_offset = update_id + 1
                self._group_hint(update)

            if failure is not None:
                if options["once"]:
                    raise CommandError(str(failure)) from None
                self.stderr.write(f"Update processing failed: {failure}")
                time.sleep(3)
                continue
            offset = next_offset
            if options["once"]:
                break
