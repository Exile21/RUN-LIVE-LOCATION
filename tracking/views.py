import json
import secrets
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, redirect_to_login
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.db.models import Count, F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import bot, telegram
from .forms import EventForm, JoinEventForm, RestartEventForm, RunnerSignupForm
from .gpx import route_preview_path
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


class RunnerLoginView(LoginView):
    template_name = "registration/login.html"

    def get_success_url(self):
        target = self.get_redirect_url()
        if target:
            return target
        if self.request.user.is_staff:
            return reverse("event_list")
        return reverse("runner_dashboard")


def _next_url(request) -> str:
    value = request.POST.get("next") or request.GET.get("next") or ""
    if value and url_has_allowed_host_and_scheme(
        value, {request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return ""


def runner_signup(request):
    next_url = _next_url(request)
    if request.user.is_authenticated:
        return redirect(next_url or ("event_list" if request.user.is_staff else "runner_dashboard"))
    if request.method == "POST":
        form = RunnerSignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Runner account created.")
            return redirect(next_url or "runner_dashboard")
    else:
        form = RunnerSignupForm()
    return _private_page(
        render(request, "registration/signup.html", {"form": form, "next": next_url})
    )


def _iso(value):
    if value is None:
        return ""
    return timezone.localtime(value).isoformat(timespec="seconds")


def _duration_label(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _when(value):
    return timezone.localtime(value).strftime("%d %b %Y, %H:%M").lstrip("0") if value else ""


def _participation_item(participant: Participant, *, now) -> dict:
    event = participant.event
    starts_at = event.schedule_starts_at
    ends_at = event.schedule_ends_at
    ended = event.status == Event.Status.CLOSED or (ends_at is not None and ends_at <= now)
    started = starts_at is not None and starts_at <= now
    live_start = ""
    if started and not ended:
        time_label = "Elapsed"
        time_value = _duration_label((now - starts_at).total_seconds())
        live_start = _iso(starts_at)
    elif ended and starts_at is not None:
        finish = ends_at or event.closed_at or now
        time_label = "Final duration"
        time_value = _duration_label((finish - starts_at).total_seconds())
    elif starts_at is not None:
        time_label = "Starts"
        time_value = timezone.localtime(starts_at).strftime("%d %b, %H:%M").lstrip("0")
    else:
        time_label = "Schedule"
        time_value = "Not scheduled"

    if starts_at and ends_at:
        schedule_label = f"{_when(starts_at)} – {timezone.localtime(ends_at).strftime('%H:%M')}"
    else:
        schedule_label = _when(starts_at) or "Schedule not set"

    active = (
        event.status == Event.Status.OPEN
        and participant.consented_at is not None
        and not participant.withdrawn
    )
    claim_url = ""
    if active:
        telegram_label = "Telegram connected"
    elif ended:
        telegram_label = "Telegram linked" if participant.telegram_user_id else "Telegram not connected"
    elif event.status == Event.Status.DRAFT:
        telegram_label = "Telegram opens when the event starts"
    else:
        if participant.telegram_user_id is None:
            telegram_label = "Connect Telegram"
        elif participant.consented_at is None:
            telegram_label = "Finish Telegram setup"
        else:
            telegram_label = "Reconnect Telegram"
        try:
            claim_url = bot.claim_link(participant)
        except telegram.TelegramConfigError:
            claim_url = ""

    return {
        "participant": participant,
        "event": event,
        "event_name": event.name,
        "event_status": event.status,
        "status_key": event.display_status_key,
        "status_label": event.display_status,
        "schedule_label": schedule_label,
        "time_label": time_label,
        "time_value": time_value,
        "live_start": live_start,
        "laps": participant.laps,
        "telegram_label": telegram_label,
        "claim_url": claim_url,
        "telegram_note": ""
        if claim_url or active or ended or event.status == Event.Status.DRAFT
        else "Telegram bot is not configured.",
        "can_leave": not active,
        "is_past": ended,
        "map_url": ""
        if event.status == Event.Status.DRAFT
        else reverse("spectator_detail", args=[event.spectator_token]),
        "track_url": reverse("runner_track", args=[participant.public_id])
        if ended and getattr(participant, "track_count", 0)
        else "",
    }


@never_cache
@login_required
def runner_dashboard(request):
    now = timezone.now()
    participants = (
        Participant.objects.filter(user=request.user)
        .select_related("event")
        .annotate(track_count=Count("track_points"))
        .order_by("-event__schedule_starts_at", "-created_at")
    )
    current = []
    past = []
    for participant in participants:
        item = _participation_item(participant, now=now)
        (past if item["is_past"] else current).append(item)
    return _private_page(
        render(
            request,
            "tracking/runner_dashboard.html",
            {
                "current": current,
                "past": past,
                "server_now": _iso(now),
                "brand_url": reverse("runner_dashboard"),
            },
        )
    )


@never_cache
@login_required
def runner_join(request):
    if request.method == "POST":
        form = JoinEventForm(request.POST)
        if form.is_valid():
            return redirect("join_event", token=form.cleaned_data["code"])
    else:
        form = JoinEventForm()
    return _private_page(
        render(
            request,
            "tracking/runner_join.html",
            {"form": form, "brand_url": reverse("runner_dashboard")},
        )
    )


@never_cache
def join_event(request, token: str):
    event = get_object_or_404(
        Event,
        enrollment_token=token,
        status__in=[Event.Status.DRAFT, Event.Status.OPEN],
    )
    if not request.user.is_authenticated:
        query = urlencode({"next": request.get_full_path()})
        return redirect(f"{reverse('signup')}?{query}")
    existing = Participant.objects.filter(event=event, user=request.user).first()
    if existing is not None:
        messages.info(request, f"You already joined {event.name}.")
        return redirect("runner_dashboard")
    if request.method == "POST":
        display_name = (request.user.get_full_name() or request.user.username)[:160]
        _, created = Participant.objects.get_or_create(
            event=event,
            user=request.user,
            defaults={
                "display_name": display_name,
                "withdrawn": True,
                "session_ended": True,
            },
        )
        if created:
            message = f"Joined {event.name}."
            if event.status == Event.Status.DRAFT:
                message += " You can connect Telegram when the event starts."
            else:
                message += " Connect Telegram to appear on the live map."
            messages.success(request, message)
        else:
            messages.info(request, f"You already joined {event.name}.")
        return redirect("runner_dashboard")
    return _private_page(
        render(
            request,
            "tracking/join_event.html",
            {
                "event": event,
                "route_preview_path": route_preview_path(event.route_segments),
                "brand_url": reverse("runner_dashboard"),
            },
        )
    )


@never_cache
@login_required
@require_POST
def runner_leave(request, public_id):
    participant = get_object_or_404(Participant, public_id=public_id, user=request.user)
    active = (
        participant.event.status == Event.Status.OPEN
        and participant.consented_at is not None
        and not participant.withdrawn
    )
    if active:
        messages.error(request, "Stop sharing Live Location in Telegram before leaving this event.")
    else:
        name = participant.event.name
        participant.delete()
        messages.success(request, f"You left {name}.")
    return redirect("runner_dashboard")


@never_cache
@login_required
def runner_track(request, public_id):
    participant = get_object_or_404(
        Participant.objects.select_related("event"),
        public_id=public_id,
        user=request.user,
    )
    points = list(
        participant.track_points.order_by("position_at", "id").values(
            "latitude", "longitude", "accuracy", "position_at"
        )
    )
    track = {
        "points": [
            {
                "lat": point["latitude"],
                "lon": point["longitude"],
                "accuracy": point["accuracy"],
                "at": _iso(point["position_at"]),
            }
            for point in points
        ],
        "route": participant.event.route_segments,
    }
    started_at = points[0]["position_at"] if points else None
    ended_at = points[-1]["position_at"] if points else None
    return _private_page(
        render(
            request,
            "tracking/runner_track.html",
            {
                "event": participant.event,
                "participant": participant,
                "point_count": len(points),
                "started_at": started_at,
                "ended_at": ended_at,
                "duration": _duration_label((ended_at - started_at).total_seconds())
                if points
                else "",
                "track": track,
                "brand_url": reverse("runner_dashboard"),
            },
        ),
        index=True,
    )


@never_cache
@staff_page
def event_list(request):
    events = (
        Event.objects.annotate(participant_count=Count("participants"))
        .order_by("-created_at")
    )
    for event in events:
        event.route_preview_path = route_preview_path(event.route_segments)
    return _private_page(
        render(
            request,
            "tracking/event_list.html",
            {"events": events},
        )
    )


@never_cache
@staff_page
def event_create(request):
    if request.method == "POST":
        form = EventForm(request.POST, request.FILES)
        if form.is_valid():
            event = form.save()
            if len(form.created_events) > 1:
                messages.success(
                    request, f"{len(form.created_events)} draft events created."
                )
                return redirect("event_list")
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
def event_restart(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    if event.status != Event.Status.CLOSED:
        messages.error(request, "Only a closed event can be started again.")
        return redirect("event_detail", pk=event.pk)
    if request.method == "POST":
        form = RestartEventForm(request.POST, event=event)
        if form.is_valid():
            new_event = form.save()
            messages.success(
                request,
                f"{new_event.name} scheduled again as a new draft event.",
            )
            return redirect("event_detail", pk=new_event.pk)
    else:
        form = RestartEventForm(event=event)
    return _private_page(
        render(
            request,
            "tracking/event_restart.html",
            {"event": event, "form": form},
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
            "join_url": request.build_absolute_uri(
                reverse("join_event", args=[event.enrollment_token])
            )
            if staff
            else "",
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
def event_share_telegram(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    if event.status == Event.Status.CLOSED or event.group_id is None:
        messages.error(request, "Only an upcoming or open event can be shared.")
        return redirect(f"{reverse('event_detail', args=[event.pk])}?tab=info")
    join_url = request.build_absolute_uri(
        reverse("join_event", args=[event.enrollment_token])
    )
    if event.status == Event.Status.DRAFT:
        instructions = (
            "Create a free runner account and register now. When the event starts, "
            "connect Telegram to share Live Location and appear on the map."
        )
    else:
        instructions = (
            "Create a free runner account, then connect Telegram to share Live Location "
            "and appear on the map."
        )
    text = f"Join {event.name}.\n\n{join_url}\n\n{instructions}"
    try:
        telegram.telegram_call(
            "sendMessage", {"chat_id": event.group_id, "text": text[:4000]}
        )
    except telegram.TelegramError as exc:
        messages.error(request, f"Could not send the event to Telegram: {exc}")
    else:
        messages.success(request, "Event sign-up link sent to the Telegram group.")
    return redirect(f"{reverse('event_detail', args=[event.pk])}?tab=info")


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


@never_cache
@staff_page
@require_POST
def participant_add_lap(request, pk: int, public_id):
    event = get_object_or_404(Event, pk=pk)
    participant = get_object_or_404(
        Participant, event=event, public_id=public_id, consented_at__isnull=False
    )
    Participant.objects.filter(pk=participant.pk).update(laps=F("laps") + 1)
    participant.refresh_from_db(fields=["laps"])
    messages.success(request, f"{participant.display_name}: {participant.laps} laps.")
    return redirect(f"{reverse('event_detail', args=[event.pk])}?tab=runners")


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
