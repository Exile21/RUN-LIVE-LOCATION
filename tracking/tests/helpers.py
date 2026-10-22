from datetime import UTC, datetime, timedelta

from django.utils import timezone

from tracking.models import Event, Participant, TelegramConfig

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def make_telegram_config(group_id=-1001234567890):
    config = TelegramConfig.load()
    if config is None:
        return TelegramConfig.objects.create(group_id=group_id)
    config.group_id = group_id
    config.save(update_fields=["group_id", "updated_at"])
    return config


def make_event(**kwargs):
    defaults = {
        "name": "Test event",
        "group_id": -1001234567890,
        "status": Event.Status.OPEN,
        "schedule_starts_at": timezone.now() + timedelta(hours=1),
        "schedule_ends_at": timezone.now() + timedelta(hours=2),
        "route_segments": [[[1.0, 2.0], [1.1, 2.1]]],
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def make_participant(event, user_id=1001, **kwargs):
    defaults = {
        "telegram_user_id": user_id,
        "private_chat_id": user_id,
        "display_name": "Alex",
        "withdrawn": False,
        "consented_at": NOW - timedelta(hours=1),
        "consent_version": "2026-10-21",
        "control_at": NOW - timedelta(hours=1, minutes=5),
        "control_update_id": 10,
        "consent_update_id": 11,
        "session_ended": False,
        "live_expires_at": NOW + timedelta(hours=1),
    }
    defaults.update(kwargs)
    return Participant.objects.create(event=event, **defaults)


def location_message(
    user_id=1001,
    message_id=100,
    *,
    date=NOW - timedelta(seconds=10),
    edit_date=None,
    latitude=1.05,
    longitude=2.05,
    live_period=3600,
    **kwargs,
):
    message = {
        "message_id": message_id,
        "date": int(date.timestamp()),
        "chat": {"id": user_id, "type": "private"},
        "from": {"id": user_id, "is_bot": False, "first_name": "Alex"},
        "location": {
            "latitude": latitude,
            "longitude": longitude,
            "live_period": live_period,
        },
    }
    if edit_date is not None:
        message["edit_date"] = int(edit_date.timestamp())
    message.update(kwargs)
    return message
