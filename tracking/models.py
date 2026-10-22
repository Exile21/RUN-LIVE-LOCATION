import secrets
import uuid

from django.db import models


def new_token() -> str:
    return secrets.token_urlsafe(24)


class Event(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        OPEN = "open"
        CLOSED = "closed"

    name = models.CharField(max_length=160)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    group_id = models.BigIntegerField(null=True, blank=True)
    schedule_starts_at = models.DateTimeField(null=True, blank=True)
    schedule_ends_at = models.DateTimeField(null=True, blank=True)
    route_segments = models.JSONField(default=list)
    enrollment_token = models.CharField(max_length=64, unique=True, default=new_token)
    spectator_token = models.CharField(max_length=64, unique=True, default=new_token)
    created_at = models.DateTimeField(auto_now_add=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["group_id"],
                condition=models.Q(status="open", group_id__isnull=False),
                name="one_open_event_per_group",
            )
        ]

    def __str__(self):
        return self.name


class TelegramConfig(models.Model):
    group_id = models.BigIntegerField(
        help_text="Telegram group ID shared by new events, for example -1001234567890."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Telegram settings"
        verbose_name_plural = "Telegram settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        return cls.objects.filter(pk=1).first()

    def __str__(self):
        return f"Telegram group {self.group_id}"


class Participant(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="participants")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    telegram_user_id = models.BigIntegerField()
    private_chat_id = models.BigIntegerField()
    display_name = models.CharField(max_length=160)
    pending_nonce = models.CharField(max_length=64, blank=True, default="")
    pending_at = models.DateTimeField(null=True, blank=True)
    consented_at = models.DateTimeField(null=True, blank=True)
    consent_version = models.CharField(max_length=16, blank=True, default="")
    withdrawn = models.BooleanField(default=True)
    control_at = models.DateTimeField(null=True, blank=True)
    control_update_id = models.BigIntegerField(default=-1)
    consent_update_id = models.BigIntegerField(default=-1)
    location_update_id = models.BigIntegerField(default=-1)
    location_message_id = models.BigIntegerField(null=True, blank=True)
    location_message_at = models.DateTimeField(null=True, blank=True)
    session_ended = models.BooleanField(default=False)
    live_expires_at = models.DateTimeField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    accuracy = models.FloatField(null=True, blank=True)
    position_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "telegram_user_id"],
                name="one_participation_per_event_user",
            ),
            models.UniqueConstraint(
                fields=["telegram_user_id"],
                condition=models.Q(withdrawn=False, consented_at__isnull=False),
                name="one_active_tracking_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.display_name} ({self.event})"
