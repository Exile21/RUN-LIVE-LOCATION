import secrets
import uuid

from django.conf import settings
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

    @property
    def has_registrations(self) -> bool:
        if not hasattr(self, "_has_registrations"):
            count = getattr(self, "participant_count", None)
            self._has_registrations = (
                self.participants.exists() if count is None else count > 0
            )
        return self._has_registrations

    @property
    def display_status(self) -> str:
        if self.status == self.Status.DRAFT and self.has_registrations:
            return "Registration open"
        return self.get_status_display()

    @property
    def display_status_key(self) -> str:
        if self.status == self.Status.DRAFT and self.has_registrations:
            return "registration"
        return self.status


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
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="event_participations",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    telegram_user_id = models.BigIntegerField(null=True, blank=True)
    private_chat_id = models.BigIntegerField(null=True, blank=True)
    display_name = models.CharField(max_length=160)
    laps = models.PositiveIntegerField(default=0)
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
    heading = models.PositiveSmallIntegerField(null=True, blank=True)
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
                fields=["event", "user"],
                condition=models.Q(user__isnull=False),
                name="one_registration_per_event_user",
            ),
            models.UniqueConstraint(
                fields=["telegram_user_id"],
                condition=models.Q(withdrawn=False, consented_at__isnull=False),
                name="one_active_tracking_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.display_name} ({self.event})"


class TrackPoint(models.Model):
    participant = models.ForeignKey(
        Participant, on_delete=models.CASCADE, related_name="track_points"
    )
    latitude = models.FloatField()
    longitude = models.FloatField()
    accuracy = models.FloatField(null=True, blank=True)
    position_at = models.DateTimeField()
    received_at = models.DateTimeField()
    update_id = models.BigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["participant", "update_id"],
                name="one_track_point_per_update",
            )
        ]
        indexes = [
            models.Index(
                fields=["participant", "position_at"],
                name="track_point_time_idx",
            )
        ]

    def __str__(self):
        return f"{self.participant} at {self.position_at}"
