from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tracking import telegram

ALLOWED_UPDATES = ["message", "edited_message", "callback_query", "chat_member"]


class Command(BaseCommand):
    help = "Register the Telegram webhook at PUBLIC_BASE_URL."

    def handle(self, *args, **options):
        base_url = settings.PUBLIC_BASE_URL
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise CommandError("PUBLIC_BASE_URL must be an HTTPS URL.")
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("TELEGRAM_BOT_TOKEN is not configured.")
        if (
            not settings.DEBUG
            and parsed.hostname not in settings.ALLOWED_HOSTS
            and "*" not in settings.ALLOWED_HOSTS
        ):
            raise CommandError("PUBLIC_BASE_URL host is not in DJANGO_ALLOWED_HOSTS.")
        secret = settings.TELEGRAM_WEBHOOK_SECRET
        if not secret:
            raise CommandError("TELEGRAM_WEBHOOK_SECRET is not configured.")
        if not 1 <= len(secret) <= 256:
            raise CommandError("TELEGRAM_WEBHOOK_SECRET must be 1 to 256 characters.")
        if not set(secret) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"):
            raise CommandError("TELEGRAM_WEBHOOK_SECRET may only use A-Z, a-z, 0-9, _ and -.")

        url = base_url.rstrip("/") + "/telegram/webhook/"
        try:
            telegram.telegram_call(
                "setWebhook",
                {
                    "url": url,
                    "secret_token": secret,
                    "allowed_updates": ALLOWED_UPDATES,
                    "max_connections": 1,
                    "drop_pending_updates": False,
                },
            )
        except telegram.TelegramError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(self.style.SUCCESS(f"Webhook registered at {url}"))
