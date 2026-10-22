from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tracking import telegram
from tracking.models import Event


class Command(BaseCommand):
    help = "Check Telegram bot, webhook, and open event group access."

    def handle(self, *args, **options):
        failures = []
        try:
            me = telegram.telegram_call("getMe", {})
            if not isinstance(me, dict):
                raise telegram.TelegramError("Telegram returned an invalid bot identity.")
            self.stdout.write(
                f"Bot: @{me.get('username', '?')} (id {me.get('id', '?')})"
            )
            if (
                settings.TELEGRAM_BOT_USERNAME
                and me.get("username") != settings.TELEGRAM_BOT_USERNAME
            ):
                failures.append("Bot username does not match TELEGRAM_BOT_USERNAME.")
        except telegram.TelegramError as exc:
            raise CommandError(str(exc)) from None

        expected_url = ""
        if settings.PUBLIC_BASE_URL:
            expected_url = settings.PUBLIC_BASE_URL.rstrip("/") + "/telegram/webhook/"
        try:
            info = telegram.telegram_call("getWebhookInfo", {})
            if not isinstance(info, dict):
                raise telegram.TelegramError("Telegram returned invalid webhook information.")
            url = str(info.get("url", ""))
            pending = info.get("pending_update_count", 0)
            self.stdout.write(f"Webhook: {url or '(not set)'}")
            self.stdout.write(f"Pending updates: {pending}")
            if info.get("last_error_message"):
                error = str(info["last_error_message"]).replace(
                    settings.TELEGRAM_BOT_TOKEN, "[redacted]"
                )
                self.stdout.write(f"Last webhook error: {error}")
            if expected_url:
                self.stdout.write(f"Expected webhook: {expected_url}")
                if url != expected_url:
                    failures.append("Webhook URL does not match PUBLIC_BASE_URL.")
        except telegram.TelegramError as exc:
            failures.append(str(exc))

        for event in Event.objects.filter(status=Event.Status.OPEN).order_by("pk"):
            if event.group_id is None:
                failures.append(f"Event {event.pk} has no group ID.")
                continue
            try:
                telegram.verify_group(event.group_id)
                self.stdout.write(
                    self.style.SUCCESS(f"Event {event.pk} group {event.group_id}: ready")
                )
            except telegram.TelegramError as exc:
                failures.append(f"Event {event.pk} group {event.group_id}: {exc}")
                self.stdout.write(
                    self.style.ERROR(f"Event {event.pk} group {event.group_id}: {exc}")
                )

        if failures:
            raise CommandError("Telegram check failed: " + " ".join(failures))
        self.stdout.write(self.style.SUCCESS("Telegram setup checks passed."))
