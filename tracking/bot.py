import uuid
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.utils import timezone

from . import state, telegram
from .models import Event, Participant

FUTURE_TOLERANCE = timedelta(seconds=30)
PRIVATE_HELP = (
    "Send /start from your event's link to join a live map. "
    "Runners must join the event group first. Use /stop to leave."
)
SHARE_INSTRUCTIONS = (
    "You are on the live map. Open the attachment menu, choose Location, then Share Live Location. "
    "Your latest position will be visible to anyone with the event link. Use /stop to leave."
)


def enrollment_link(event: Event) -> str:
    username = settings.TELEGRAM_BOT_USERNAME
    if not username:
        raise telegram.TelegramConfigError("TELEGRAM_BOT_USERNAME is not configured.")
    return f"https://t.me/{username}?start=e_{event.enrollment_token}"


def _timestamp(value, *, now: datetime) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return now
    parsed = datetime.fromtimestamp(value, tz=UTC)
    return now if parsed > now + FUTURE_TOLERANCE else parsed


def _send(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text[:4000]}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    telegram.telegram_call("sendMessage", payload)


def _answer(callback_id: str, text: str, *, alert: bool = False) -> None:
    telegram.telegram_call(
        "answerCallbackQuery",
        {"callback_query_id": callback_id, "text": text[:200], "show_alert": alert},
    )


def _display_name(user: dict) -> str:
    name = user.get("first_name")
    name = name.strip()[:160] if isinstance(name, str) and name.strip() else "Runner"
    return name


def _active_participant(user_id: int) -> Participant | None:
    return (
        Participant.objects.select_related("event")
        .filter(telegram_user_id=user_id, withdrawn=False, consented_at__isnull=False)
        .first()
    )


def _consent_message(participant: Participant) -> str:
    return (
        f"Joining {participant.event.name} as {participant.display_name}.\n\n"
        "Your display name and latest location will be visible to anyone with this event's link, "
        "including people outside the group. Send /stop in this private chat to remove yourself. "
        "We keep no location history and clear saved coordinates when the event closes. Tracking "
        "may be delayed or inaccurate. This app clears its own saved coordinates; Telegram keeps "
        "its copy of the location message in your chat.\n\n"
        "After you agree, open Telegram's attachment menu, choose Location, then Share Live Location. "
        "A one-time location pin is not tracked."
    )


def _consent_keyboard(participant: Participant) -> dict:
    payload = f"agree:{participant.public_id.hex}:{participant.pending_nonce}"
    return {"inline_keyboard": [[{"text": "Agree and share", "callback_data": payload}]]}


def _send_consent(participant: Participant) -> None:
    _send(
        participant.private_chat_id,
        _consent_message(participant),
        _consent_keyboard(participant),
    )


def _start_payload(text: str) -> str:
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _handle_start(update_id: int, message: dict, user: dict, chat: dict, text: str, now: datetime) -> None:
    payload = _start_payload(text)
    user_id = user.get("id")
    chat_id = chat.get("id")
    if not isinstance(user_id, int) or chat_id != user_id:
        return
    if not payload.startswith("e_"):
        _send(chat_id, PRIVATE_HELP)
        return
    event = Event.objects.filter(status=Event.Status.OPEN, enrollment_token=payload[2:]).first()
    if event is None or event.group_id is None:
        _send(chat_id, "This event link is not active.")
        return
    if not telegram.verify_member(event.group_id, user_id):
        _send(chat_id, "Join the event's Telegram group first, then open this link again.")
        return
    try:
        participant = state.begin_onboarding(
            event.id,
            user_id,
            chat_id,
            _display_name(user),
            update_id,
            sent_at=_timestamp(message.get("date"), now=now),
            now=now,
        )
    except state.AlreadyTracking:
        _send(chat_id, "You are already tracking another event. Send /stop there first.")
        return
    except state.StaleUpdate:
        participant = Participant.objects.filter(event=event, telegram_user_id=user_id).first()
        if (
            participant is not None
            and participant.pending_nonce
            and participant.pending_at is not None
            and now - participant.pending_at <= state.PENDING_TTL
        ):
            _send_consent(participant)
        return
    except state.StateConflict as exc:
        _send(chat_id, str(exc))
        return
    if participant.consented_at is not None and not participant.withdrawn:
        _send(
            chat_id,
            "You are already sharing for this event. Send Live Location when ready. Use /stop to leave.",
        )
        return
    _send_consent(participant)


def _handle_stop(update_id: int, message: dict, user: dict, chat: dict, now: datetime) -> None:
    user_id = user.get("id")
    chat_id = chat.get("id")
    if not isinstance(user_id, int) or chat_id != user_id:
        return
    state.withdraw_runner(
        user_id,
        update_id,
        sent_at=_timestamp(message.get("date"), now=now),
        now=now,
    )
    _send(
        chat_id,
        "You are removed from the live map and your saved position is cleared. "
        "You can also stop sharing in Telegram from the live-location bar.",
    )


def _handle_location(update_id: int, message: dict, user: dict, chat: dict, *, edited: bool, now: datetime) -> None:
    user_id = user.get("id")
    chat_id = chat.get("id")
    if not isinstance(user_id, int) or chat_id != user_id:
        return
    participant = _active_participant(user_id)
    if participant is None:
        if not edited:
            _send(chat_id, PRIVATE_HELP)
        return
    if participant.event.status != Event.Status.OPEN:
        return
    if not telegram.verify_member(participant.event.group_id, user_id):
        state.withdraw_runner(
            user_id,
            update_id,
            sent_at=now,
            now=now,
            event_id=participant.event_id,
        )
        _send(chat_id, "You left the event group, so you were removed from the live map.")
        return
    try:
        state.apply_location(user_id, message, update_id, edited=edited, now=now)
    except state.InvalidLocation:
        if not edited:
            _send(
                chat_id,
                "Share Live Location from the attachment menu. One-time location pins "
                "and forwarded locations are not tracked.",
            )


def _handle_private_message(update_id: int, message: dict, now: datetime) -> None:
    chat = message.get("chat")
    user = message.get("from")
    if not isinstance(chat, dict) or not isinstance(user, dict):
        return
    if chat.get("type") != "private" or user.get("is_bot"):
        return
    user_id = user.get("id")
    if not isinstance(user_id, int) or chat.get("id") != user_id:
        return
    if message.get("location") is not None:
        _handle_location(update_id, message, user, chat, edited=False, now=now)
        return
    text = message.get("text")
    if not isinstance(text, str):
        return
    command = text.split(maxsplit=1)[0].split("@", 1)[0]
    if command == "/start":
        _handle_start(update_id, message, user, chat, text, now)
    elif command == "/stop":
        _handle_stop(update_id, message, user, chat, now)
    else:
        _send(user_id, PRIVATE_HELP)


def _handle_group_message(update_id: int, message: dict, now: datetime) -> None:
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("type") not in {"group", "supergroup"}:
        return
    group_id = chat.get("id")
    if not isinstance(group_id, int):
        return

    users = message.get("new_chat_members")
    if isinstance(users, list):
        event = Event.objects.filter(group_id=group_id, status=Event.Status.OPEN).first()
        if event is not None:
            names = [
                _display_name(user)
                for user in users
                if isinstance(user, dict) and not user.get("is_bot")
            ]
            if names:
                _send(
                    group_id,
                    f"Welcome to {event.name}. To appear on the live map, open the bot privately, "
                    f"agree, then choose Share Live Location: {enrollment_link(event)}",
                )

    left = message.get("left_chat_member")
    if isinstance(left, dict) and isinstance(left.get("id"), int):
        event = Event.objects.filter(group_id=group_id, status=Event.Status.OPEN).first()
        if event is not None:
            state.withdraw_runner(
                left["id"],
                update_id,
                sent_at=_timestamp(message.get("date"), now=now),
                now=now,
                event_id=event.id,
            )

    text = message.get("text")
    if not isinstance(text, str):
        return
    command = text.split(maxsplit=1)[0].split("@", 1)[0]
    if command == "/groupid":
        _send(group_id, f"Telegram group ID: {group_id}")


def _handle_callback(update_id: int, callback: dict, now: datetime) -> None:
    callback_id = callback.get("id")
    user = callback.get("from")
    message = callback.get("message")
    data = callback.get("data")
    if not isinstance(callback_id, str) or not isinstance(user, dict) or user.get("is_bot"):
        return
    if not isinstance(message, dict) or not isinstance(data, str):
        _answer(callback_id, "Consent button is no longer active.")
        return
    chat = message.get("chat")
    user_id = user.get("id")
    if (
        not isinstance(chat, dict)
        or chat.get("type") != "private"
        or not isinstance(user_id, int)
        or chat.get("id") != user_id
    ):
        _answer(callback_id, "Open the bot in a private chat.")
        return
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "agree":
        _answer(callback_id, "Unknown action.")
        return
    try:
        public_id = uuid.UUID(hex=parts[1])
    except (ValueError, TypeError):
        _answer(callback_id, "Consent button is no longer active.")
        return
    nonce = parts[2]
    participant = (
        Participant.objects.select_related("event")
        .filter(
            public_id=public_id,
            telegram_user_id=user_id,
            private_chat_id=user_id,
        )
        .first()
    )
    if participant is None or participant.event.status != Event.Status.OPEN:
        _answer(callback_id, "Consent button is no longer active.")
        return
    if not telegram.verify_member(participant.event.group_id, user_id):
        _answer(callback_id, "Join the event group before agreeing.")
        return
    granted = state.grant_consent(public_id, user_id, nonce, update_id, now=now)
    if not granted:
        repeated = Participant.objects.filter(
            public_id=public_id,
            telegram_user_id=user_id,
            private_chat_id=user_id,
            consent_update_id=update_id,
            consented_at__isnull=False,
            withdrawn=False,
        ).exists()
        if repeated:
            _answer(callback_id, "Consent saved. Share Live Location when ready.")
            _send(user_id, SHARE_INSTRUCTIONS)
            return
        _answer(callback_id, "This consent button is no longer active. Send /start for a new one.")
        return
    _answer(callback_id, "Consent saved. Share Live Location when ready.")
    try:
        telegram.telegram_call(
            "editMessageReplyMarkup",
            {
                "chat_id": user_id,
                "message_id": message.get("message_id"),
                "reply_markup": {"inline_keyboard": []},
            },
        )
    except telegram.TelegramError:
        pass
    _send(user_id, SHARE_INSTRUCTIONS)


def _handle_chat_member(update_id: int, update: dict, now: datetime) -> None:
    chat = update.get("chat")
    member = update.get("new_chat_member")
    if not isinstance(chat, dict) or not isinstance(member, dict):
        return
    group_id = chat.get("id")
    user = member.get("user")
    status = member.get("status")
    if not isinstance(group_id, int) or not isinstance(user, dict):
        return
    removed = status in {"left", "kicked"} or (
        status == "restricted" and member.get("is_member") is False
    )
    if not removed or not isinstance(user.get("id"), int):
        return
    event = Event.objects.filter(group_id=group_id, status=Event.Status.OPEN).first()
    if event is None:
        return
    state.withdraw_runner(
        user["id"],
        update_id,
        sent_at=_timestamp(update.get("date"), now=now),
        now=now,
        event_id=event.id,
    )


def handle_update(update: dict, *, now: datetime | None = None) -> None:
    if not isinstance(update, dict):
        raise ValueError("update must be an object")
    update_id = update.get("update_id")
    if isinstance(update_id, bool) or not isinstance(update_id, int):
        raise ValueError("update_id must be an integer")
    now = now or timezone.now()
    if "message" in update:
        message = update["message"]
        if not isinstance(message, dict):
            raise ValueError("message must be an object")
        chat = message.get("chat")
        if isinstance(chat, dict) and chat.get("type") == "private":
            _handle_private_message(update_id, message, now)
        else:
            _handle_group_message(update_id, message, now)
    elif "edited_message" in update:
        message = update["edited_message"]
        if not isinstance(message, dict):
            raise ValueError("edited_message must be an object")
        chat = message.get("chat")
        user = message.get("from")
        if (
            isinstance(chat, dict)
            and chat.get("type") == "private"
            and isinstance(user, dict)
            and not user.get("is_bot")
        ):
            _handle_location(update_id, message, user, chat, edited=True, now=now)
    elif "callback_query" in update:
        callback = update["callback_query"]
        if not isinstance(callback, dict):
            raise ValueError("callback_query must be an object")
        _handle_callback(update_id, callback, now)
    elif "chat_member" in update:
        member_update = update["chat_member"]
        if not isinstance(member_update, dict):
            raise ValueError("chat_member must be an object")
        _handle_chat_member(update_id, member_update, now)
