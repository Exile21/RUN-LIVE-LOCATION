import math
import secrets
from datetime import UTC, datetime, timedelta

from django.db import IntegrityError, transaction

from .models import Event, Participant

STALE_AFTER = timedelta(seconds=120)
PENDING_TTL = timedelta(minutes=30)
CONSENT_VERSION = "2026-10-21"
INDEFINITE_LIVE_PERIOD = 0x7FFFFFFF
MIN_LIVE_PERIOD = 60
MAX_LIVE_PERIOD = 86400
FUTURE_TOLERANCE = timedelta(seconds=30)
LOCATION_FIELDS = (
    "latitude",
    "longitude",
    "accuracy",
    "position_at",
    "received_at",
    "live_expires_at",
    "session_ended",
)


class StateConflict(Exception):
    pass


class StaleUpdate(StateConflict):
    pass


class AlreadyTracking(StateConflict):
    pass


class InvalidLocation(ValueError):
    pass


def _newer(sent_at, update_id, control_at, control_update_id) -> bool:
    if control_at is None:
        return True
    if sent_at > control_at:
        return True
    if sent_at < control_at:
        return False
    return update_id > control_update_id


def _clear_position(participant: Participant) -> None:
    participant.latitude = None
    participant.longitude = None
    participant.accuracy = None
    participant.position_at = None
    participant.received_at = None
    participant.live_expires_at = None
    participant.session_ended = True


def tracking_status(participant: Participant, *, now: datetime) -> str:
    if participant.withdrawn or participant.consented_at is None:
        return "stopped"
    if participant.session_ended:
        return "expired"
    if participant.live_expires_at is not None and participant.live_expires_at <= now:
        return "expired"
    if participant.position_at is None:
        return "waiting"
    if participant.position_at > now + FUTURE_TOLERANCE:
        return "stale"
    if participant.position_at >= now - STALE_AFTER:
        return "live"
    return "stale"


def open_event(event_id: int, *, now: datetime) -> Event:
    with transaction.atomic():
        event = Event.objects.get(pk=event_id)
        if event.status != Event.Status.DRAFT:
            raise StateConflict("Only a draft event can be opened.")
        if event.group_id is None:
            raise StateConflict("Set a Telegram group before opening the event.")
        if event.schedule_starts_at is None or event.schedule_ends_at is None:
            raise StateConflict("Set the event schedule before opening.")
        if event.schedule_ends_at <= event.schedule_starts_at:
            raise StateConflict("End time must be after start time.")
        if Event.objects.filter(status=Event.Status.OPEN, group_id=event.group_id).exclude(pk=event.pk).exists():
            raise StateConflict("That Telegram group already belongs to an open event.")
        event.status = Event.Status.OPEN
        event.opened_at = now
        event.save(update_fields=["status", "opened_at"])
        return event


def close_event(event_id: int, *, now: datetime) -> Event:
    with transaction.atomic():
        event = Event.objects.get(pk=event_id)
        if event.status == Event.Status.CLOSED:
            return event
        if event.status != Event.Status.OPEN:
            raise StateConflict("Only an open event can be closed.")
        event.status = Event.Status.CLOSED
        event.closed_at = now
        event.save(update_fields=["status", "closed_at"])
        for participant in event.participants.all():
            _clear_position(participant)
            participant.withdrawn = True
            participant.pending_nonce = ""
            participant.pending_at = None
            participant.save(
                update_fields=["withdrawn", "pending_nonce", "pending_at", *LOCATION_FIELDS]
            )
        return event


def rotate_spectator_token(event_id: int) -> str:
    with transaction.atomic():
        event = Event.objects.get(pk=event_id)
        event.spectator_token = secrets.token_urlsafe(24)
        event.save(update_fields=["spectator_token"])
        return event.spectator_token


def begin_onboarding(
    event_id: int,
    user_id: int,
    chat_id: int,
    display_name: str,
    update_id: int,
    *,
    sent_at: datetime,
    now: datetime,
) -> Participant:
    with transaction.atomic():
        event = Event.objects.get(pk=event_id)
        if event.status != Event.Status.OPEN:
            raise StateConflict("This event is not open for runners.")
        if Participant.objects.filter(
            telegram_user_id=user_id,
            withdrawn=False,
            consented_at__isnull=False,
        ).exclude(event=event).exists():
            raise AlreadyTracking("Stop your current event before joining another.")
        participant, _ = Participant.objects.get_or_create(
            event=event,
            telegram_user_id=user_id,
            defaults={
                "private_chat_id": chat_id,
                "display_name": display_name,
                "withdrawn": True,
                "session_ended": True,
            },
        )
        if participant.consented_at is not None and not participant.withdrawn:
            return participant
        if not _newer(
            sent_at,
            update_id,
            participant.control_at,
            participant.control_update_id,
        ):
            raise StaleUpdate("Older onboarding message ignored.")
        if participant.consented_at is None:
            participant.display_name = display_name
        participant.private_chat_id = chat_id
        participant.withdrawn = True
        participant.pending_nonce = secrets.token_urlsafe(12)
        participant.pending_at = now
        participant.control_at = sent_at
        participant.control_update_id = update_id
        participant.location_update_id = -1
        _clear_position(participant)
        participant.save(
            update_fields=[
                "private_chat_id",
                "display_name",
                "withdrawn",
                "pending_nonce",
                "pending_at",
                "control_at",
                "control_update_id",
                "location_update_id",
                *LOCATION_FIELDS,
            ]
        )
        return participant


def claim_registration(
    public_id,
    user_id: int,
    chat_id: int,
) -> Participant | None:
    """Attach a Telegram account to a web registration, merging a prior tracker."""
    with transaction.atomic():
        participant = (
            Participant.objects.select_for_update()
            .select_related("event")
            .filter(public_id=public_id, user__isnull=False)
            .first()
        )
        if participant is None or participant.event.status != Event.Status.OPEN:
            return None
        if participant.telegram_user_id not in {None, user_id}:
            return None
        existing = (
            Participant.objects.select_for_update()
            .filter(event_id=participant.event_id, telegram_user_id=user_id)
            .exclude(pk=participant.pk)
            .first()
        )
        if existing is not None:
            if existing.user_id not in {None, participant.user_id}:
                return None
            owner_id = participant.user_id
            extra_laps = participant.laps
            participant.delete()
            existing.user_id = owner_id
            existing.laps += extra_laps
            existing.save(update_fields=["user", "laps"])
            return existing
        participant.telegram_user_id = user_id
        participant.private_chat_id = chat_id
        participant.save(update_fields=["telegram_user_id", "private_chat_id"])
        return participant


def grant_consent(
    public_id,
    user_id: int,
    nonce: str,
    update_id: int,
    *,
    now: datetime,
) -> bool:
    try:
        with transaction.atomic():
            participant = (
                Participant.objects.select_related("event")
                .filter(public_id=public_id, telegram_user_id=user_id)
                .first()
            )
            if participant is None or participant.event.status != Event.Status.OPEN:
                return False
            if not participant.pending_nonce:
                return False
            try:
                nonce_matches = bool(nonce) and secrets.compare_digest(
                    nonce, participant.pending_nonce
                )
            except TypeError:
                nonce_matches = False
            if not nonce_matches:
                return False
            if participant.pending_at is None or now - participant.pending_at > PENDING_TTL:
                participant.pending_nonce = ""
                participant.pending_at = None
                participant.save(update_fields=["pending_nonce", "pending_at"])
                return False
            if Participant.objects.filter(
                telegram_user_id=user_id,
                withdrawn=False,
                consented_at__isnull=False,
            ).exclude(pk=participant.pk).exists():
                return False
            participant.consented_at = now
            participant.consent_version = CONSENT_VERSION
            participant.consent_update_id = update_id
            participant.pending_nonce = ""
            participant.pending_at = None
            participant.withdrawn = False
            participant.location_update_id = -1
            participant.latitude = None
            participant.longitude = None
            participant.accuracy = None
            participant.position_at = None
            participant.received_at = None
            participant.live_expires_at = None
            participant.session_ended = False
            participant.save(
                update_fields=[
                    "consented_at",
                    "consent_version",
                    "consent_update_id",
                    "pending_nonce",
                    "pending_at",
                    "withdrawn",
                    "location_update_id",
                    *LOCATION_FIELDS,
                ]
            )
            return True
    except IntegrityError:
        return False


def withdraw_runner(
    user_id: int,
    update_id: int,
    *,
    sent_at: datetime,
    now: datetime,
    event_id: int | None = None,
) -> None:
    with transaction.atomic():
        query = Participant.objects.filter(telegram_user_id=user_id, withdrawn=False)
        if event_id is not None:
            query = query.filter(event_id=event_id)
        participant = query.filter(consented_at__isnull=False).first()
        if participant is None:
            return
        if not _newer(
            sent_at,
            update_id,
            participant.control_at,
            participant.control_update_id,
        ):
            return
        participant.withdrawn = True
        participant.pending_nonce = ""
        participant.pending_at = None
        participant.control_at = sent_at
        participant.control_update_id = update_id
        _clear_position(participant)
        participant.save(
            update_fields=[
                "withdrawn",
                "pending_nonce",
                "pending_at",
                "control_at",
                "control_update_id",
                *LOCATION_FIELDS,
            ]
        )


def _int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidLocation(f"{name} must be an integer")
    return value


def _finite(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidLocation(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidLocation(f"{name} must be finite")
    return number


def _timestamp_to_datetime(value, name: str) -> datetime:
    seconds = _int(value, name)
    if seconds <= 0:
        raise InvalidLocation(f"{name} must be positive")
    return datetime.fromtimestamp(seconds, tz=UTC)


def _send_position(participant: Participant, location: dict) -> None:
    latitude = _finite(location.get("latitude"), "latitude")
    longitude = _finite(location.get("longitude"), "longitude")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise InvalidLocation("coordinates are out of range")
    accuracy = location.get("horizontal_accuracy")
    if accuracy is not None:
        accuracy = _finite(accuracy, "horizontal_accuracy")
        if not 0 <= accuracy <= 1500:
            raise InvalidLocation("horizontal_accuracy is out of range")
    participant.latitude = latitude
    participant.longitude = longitude
    participant.accuracy = accuracy


def _valid_live_period(value) -> int | None:
    period = _int(value, "live_period")
    if period == INDEFINITE_LIVE_PERIOD:
        return None
    if not MIN_LIVE_PERIOD <= period <= MAX_LIVE_PERIOD:
        raise InvalidLocation("live_period is out of range")
    return period


def _end_session(participant: Participant) -> None:
    _clear_position(participant)
    participant.save(update_fields=list(LOCATION_FIELDS))


def apply_location(
    user_id: int,
    message: dict,
    update_id: int,
    *,
    edited: bool,
    now: datetime,
) -> bool:
    if not isinstance(message, dict):
        raise InvalidLocation("message must be an object")
    if message.get("forward_origin") or message.get("forward_from") or message.get("forward_from_chat"):
        raise InvalidLocation("forwarded locations are not accepted")
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        raise InvalidLocation("message identity is missing")
    if _int(sender.get("id"), "sender id") != user_id:
        raise InvalidLocation("sender mismatch")
    if sender.get("is_bot"):
        raise InvalidLocation("bot senders are ignored")
    if chat.get("type") != "private" or _int(chat.get("id"), "chat id") != user_id:
        raise InvalidLocation("locations must come from the runner's private chat")
    message_id = _int(message.get("message_id"), "message_id")
    if message_id <= 0:
        raise InvalidLocation("message_id is invalid")
    message_date = _timestamp_to_datetime(message.get("date"), "date")
    if message_date > now + FUTURE_TOLERANCE:
        raise InvalidLocation("message date is in the future")
    edit_date = message.get("edit_date")
    position_time = (
        _timestamp_to_datetime(edit_date, "edit_date") if edited and edit_date is not None else message_date
    )
    if position_time > now + FUTURE_TOLERANCE:
        raise InvalidLocation("position time is in the future")

    with transaction.atomic():
        participant = (
            Participant.objects.select_related("event")
            .filter(
                telegram_user_id=user_id,
                event__status=Event.Status.OPEN,
                withdrawn=False,
                consented_at__isnull=False,
            )
            .first()
        )
        if participant is None or participant.private_chat_id != user_id:
            return False

        location = message.get("location")
        if edited:
            if participant.location_message_id is None or message_id != participant.location_message_id:
                return False
            if participant.position_at is None or participant.session_ended:
                return False
            if participant.live_expires_at is not None and participant.live_expires_at <= now:
                _end_session(participant)
                return False
            if not isinstance(location, dict):
                _end_session(participant)
                return False
        else:
            if not isinstance(location, dict):
                raise InvalidLocation("this is not a location message")
            if (
                participant.location_message_id is not None
                and message_id <= participant.location_message_id
            ):
                return False
            if message_date < datetime.fromtimestamp(
                int(participant.consented_at.timestamp()), tz=UTC
            ):
                return False
            period = _valid_live_period(location.get("live_period"))
            participant.location_message_id = message_id
            participant.location_message_at = message_date
            participant.session_ended = False
            participant.live_expires_at = (
                None if period is None else message_date + timedelta(seconds=period)
            )

        _send_position(participant, location)
        if position_time > now:
            position_time = now
        if (
            edited
            and participant.position_at is not None
            and position_time == participant.position_at
            and update_id <= participant.location_update_id
        ):
            return False
        if edited and participant.position_at is not None and position_time < participant.position_at:
            return False

        if "live_period" in location and edited:
            period = _valid_live_period(location.get("live_period"))
            participant.live_expires_at = (
                None if period is None else (participant.location_message_at or message_date) + timedelta(seconds=period)
            )
        participant.position_at = position_time
        participant.received_at = now
        participant.location_update_id = update_id
        participant.save(
            update_fields=[
                "location_message_id",
                "location_message_at",
                "session_ended",
                "location_update_id",
                *LOCATION_FIELDS,
            ]
        )
        return True
