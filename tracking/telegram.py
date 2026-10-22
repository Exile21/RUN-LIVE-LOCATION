import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

MAX_RESPONSE_BYTES = 1024 * 1024
METHOD_PATTERN = re.compile(r"^[a-z][A-Za-z]+$")

_bot_id: int | None = None


class TelegramError(Exception):
    pass


class TelegramSetupError(TelegramError):
    pass


class TelegramConfigError(TelegramError):
    pass


def _configuration() -> tuple[str, str]:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise TelegramConfigError("TELEGRAM_BOT_TOKEN is not configured.")
    return token, "https://api.telegram.org"


def _description(raw: bytes, token: str) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
        description = str(payload.get("description", ""))[:200]
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        description = ""
    return description.replace(token, "[redacted]")


def telegram_call(method: str, payload: dict, *, timeout: float = 5):
    if not METHOD_PATTERN.fullmatch(method):
        raise TelegramError("Invalid Telegram API method.")
    token, base_url = _configuration()
    request = Request(
        f"{base_url}/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        try:
            raw_error = exc.read(MAX_RESPONSE_BYTES)
        except OSError:
            raw_error = b""
        description = _description(raw_error, token)
        suffix = f": {description}" if description else ""
        raise TelegramError(f"Telegram API rejected the request (HTTP {exc.code}){suffix}.") from None
    except (URLError, TimeoutError, OSError):
        raise TelegramError("Telegram API is unreachable.") from None

    if len(raw) > MAX_RESPONSE_BYTES:
        raise TelegramError("Telegram API response is too large.")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TelegramError("Telegram API returned invalid JSON.") from None
    if not isinstance(body, dict) or body.get("ok") is not True:
        description = str(body.get("description", ""))[:200] if isinstance(body, dict) else ""
        description = description.replace(token, "[redacted]")
        suffix = f": {description}" if description else ""
        raise TelegramError(f"Telegram API rejected the request{suffix}.")
    return body.get("result")


def bot_identity() -> int:
    global _bot_id
    if _bot_id is None:
        result = telegram_call("getMe", {})
        if not isinstance(result, dict) or isinstance(result.get("id"), bool):
            raise TelegramSetupError("Telegram bot identity is unavailable.")
        bot_id = result.get("id")
        if not isinstance(bot_id, int):
            raise TelegramSetupError("Telegram bot identity is unavailable.")
        _bot_id = bot_id
    return _bot_id


def _require_bot_admin(group_id: int) -> None:
    member = telegram_call("getChatMember", {"chat_id": group_id, "user_id": bot_identity()})
    if not isinstance(member, dict):
        raise TelegramSetupError("Telegram returned an invalid membership response.")
    if member.get("status") not in {"administrator", "creator"}:
        raise TelegramSetupError("The bot must be a group administrator.")


def verify_group(group_id: int) -> None:
    chat = telegram_call("getChat", {"chat_id": group_id})
    if not isinstance(chat, dict) or chat.get("type") not in {"group", "supergroup"}:
        raise TelegramSetupError("The configured chat is not a Telegram group.")
    _require_bot_admin(group_id)


def verify_member(group_id: int, user_id: int) -> bool:
    _require_bot_admin(group_id)
    member = telegram_call("getChatMember", {"chat_id": group_id, "user_id": user_id})
    if not isinstance(member, dict):
        raise TelegramSetupError("Telegram returned an invalid membership response.")
    status = member.get("status")
    if status in {"member", "administrator", "creator"}:
        return True
    if status == "restricted":
        return member.get("is_member") is True
    if status in {"left", "kicked"}:
        return False
    raise TelegramSetupError("Telegram returned an unknown membership status.")
