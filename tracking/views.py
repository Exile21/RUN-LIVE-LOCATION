import json
import secrets
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import bot, telegram
from .forms import EventForm
from .models import Event, Participant, TelegramConfig
from .snapshots import event_snapshot
from .state import StateConflict, close_event, open_event, rotate_spectator_token

MAX_WEBHOOK_BYTES = 256 * 1024


def _active_staff(user) -> bool:
    return bool(user.is_authenticated and user.is_active and user.is_staff)


def staff_page(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not _active_staff(request.user):
            return redirect_to_login(request.get_full_path())
        return view(request, *args, **kwargs)

    return wrapper


def staff_api(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_active:
            return JsonResponse({"error": "authentication required"}, status=401)
        if not request.user.is_staff:
            return JsonResponse({"error": "staff access required"}, status=403)
        return view(request, *args, **kwargs)

    return wrapper


def _private(response):
    response["Cache-Control"] = "no-store"
    return response


def _private_page(response, *, index: bool = False):
    _private(response)
    if index:
        response["X-Robots-Tag"] = "noindex"
    return response


@never_cache
@staff_page
def event_list(request):
    now = timezone.now()
    events = Event.objects.all().order_by("-created_at")
    upcoming = (
        Event.objects.filter(
            status=Event.Status.DRAFT,
            schedule_starts_at__gt=now,
        )
        .order_by("schedule_starts_at")
        .first()
    )
    return _private_page(
        render(
            request,
            "tracking/event_list.html",
            {"events": events, "upcoming": upcoming},
        )
    )


@never_cache
@staff_page
def event_create(request):
    if request.method == "POST":
        form = EventForm(request.POST, request.FILES)
        if form.is_valid():
            event = form.save()
            messages.success(request, "Event created.")
            return redirect("event_detail", pk=event.pk)
    else:
        form = EventForm()
    return _private_page(
        render(
            request,
            "tracking/event_form.html",
            {"form": form, "telegram_config": TelegramConfig.load()},
        )
    )


@never_cache
@staff_page
def event_edit(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    if event.status == Event.Status.CLOSED:
        messages.error(request, "A closed event cannot be edited.")
        return redirect("event_detail", pk=event.pk)
    if request.method == "POST":
        form = EventForm(request.POST, request.FILES, instance=event)
        if form.is_valid():
            form.save()
            messages.success(request, "Event updated.")
            return redirect("event_detail", pk=event.pk)
    else:
        form = EventForm(instance=event)
    return _private_page(
        render(
            request,
            "tracking/event_form.html",
            {"form": form, "event": event, "telegram_config": TelegramConfig.load()},
        )
    )


def _event_map(request, event: Event, *, staff: bool):
    if staff:
        spectator_url = request.build_absolute_uri(
            reverse("spectator_detail", args=[event.spectator_token])
        )
        enrollment_url = ""
        if settings.TELEGRAM_BOT_USERNAME:
            enrollment_url = (
                f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}"
                f"?start=e_{event.enrollment_token}"
            )
        snapshot_url = reverse("staff_snapshot", args=[event.pk])
    else:
        spectator_url = ""
        enrollment_url = ""
        snapshot_url = reverse("spectator_snapshot", args=[event.spectator_token])
    snapshot = event_snapshot(event, staff=staff, now=timezone.now())
    return render(
        request,
        "tracking/event_map.html",
        {
            "event": event,
            "is_staff_view": staff,
            "snapshot": snapshot,
            "snapshot_url": snapshot_url,
            "spectator_url": spectator_url,
            "enrollment_url": enrollment_url,
            "brand_url": reverse("event_list")
            if staff
            else reverse("spectator_detail", args=[event.spectator_token]),
        },
    )


@never_cache
@staff_page
def event_detail(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    return _private_page(_event_map(request, event, staff=True))


def _spectator_event(token: str) -> Event:
    return get_object_or_404(
        Event,
        spectator_token=token,
        status__in=[Event.Status.OPEN, Event.Status.CLOSED],
    )


@never_cache
def spectator_detail(request, token: str):
    event = _spectator_event(token)
    return _private_page(_event_map(request, event, staff=False), index=True)


@staff_api
@never_cache
def staff_snapshot(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    response = JsonResponse(event_snapshot(event, staff=True, now=timezone.now()))
    return _private(response)


@never_cache
def spectator_snapshot(request, token: str):
    event = _spectator_event(token)
    response = JsonResponse(event_snapshot(event, staff=False, now=timezone.now()))
    _private(response)
    response["X-Robots-Tag"] = "noindex"
    return response


@never_cache
@staff_page
@require_POST
def event_open(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    try:
        if event.group_id is None:
            raise ValidationError("Set a Telegram group before opening.")
        telegram.verify_group(event.group_id)
        open_event(event.pk, now=timezone.now())
        messages.success(request, "Event opened and Telegram setup verified.")
    except (telegram.TelegramError, ValidationError, StateConflict) as exc:
        messages.error(request, f"Could not open the event: {exc}")
    return redirect("event_detail", pk=event.pk)


@never_cache
@staff_page
@require_POST
def event_close(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    try:
        close_event(event.pk, now=timezone.now())
        messages.success(request, "Event closed and saved runner positions cleared.")
    except StateConflict as exc:
        messages.error(request, f"Could not close the event: {exc}")
    return redirect("event_detail", pk=event.pk)


@never_cache
@staff_page
@require_POST
def event_rotate_link(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    rotate_spectator_token(event.pk)
    messages.success(request, "The spectator link was rotated. Old links no longer work.")
    return redirect("event_detail", pk=event.pk)


@never_cache
@staff_page
@require_POST
def participant_rename(request, pk: int, public_id):
    event = get_object_or_404(Event, pk=pk)
    participant = get_object_or_404(Participant, event=event, public_id=public_id)
    name = (request.POST.get("display_name") or "").strip()
    if not name or len(name) > 160:
        messages.error(request, "Display name must be between 1 and 160 characters.")
    else:
        participant.display_name = name
        participant.save(update_fields=["display_name"])
        messages.success(request, "Runner name updated.")
    return redirect("event_detail", pk=event.pk)


@csrf_exempt
@require_POST
def telegram_webhook(request):
    expected = settings.TELEGRAM_WEBHOOK_SECRET
    if not expected:
        return HttpResponse(status=503)
    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    try:
        matches = secrets.compare_digest(provided, expected)
    except TypeError:
        matches = False
    if not matches:
        return HttpResponse(status=403)
    content_length = request.META.get("CONTENT_LENGTH")
    if content_length not in {None, ""}:
        try:
            if int(content_length) > MAX_WEBHOOK_BYTES:
                return HttpResponse(status=413)
        except (TypeError, ValueError):
            return HttpResponse(status=400)
    try:
        body = request.body
    except RequestDataTooBig:
        return HttpResponse(status=413)
    if len(body) > MAX_WEBHOOK_BYTES:
        return HttpResponse(status=413)
    try:
        update = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HttpResponse(status=400)
    if not isinstance(update, dict):
        return HttpResponse(status=400)
    try:
        bot.handle_update(update, now=timezone.now())
    except (ValueError, KeyError, TypeError):
        return HttpResponse(status=400)
    except telegram.TelegramError:
        return HttpResponse(status=503)
    return HttpResponse(status=200)
