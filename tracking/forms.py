import calendar
import re
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.utils import timezone

from .gpx import MAX_GPX_BYTES, parse_gpx
from .models import Event, TelegramConfig

User = get_user_model()
JOIN_TOKEN_PATTERN = re.compile(r"(?:start=e_|/join/)([A-Za-z0-9_-]{16,})")
RECURRENCE_CHOICES = (
    ("none", "Does not repeat"),
    ("daily", "Daily"),
    ("weekly", "Weekly"),
    ("biweekly", "Every 2 weeks"),
    ("monthly", "Monthly"),
)


def schedule_field(label: str) -> forms.DateTimeField:
    return forms.DateTimeField(
        required=True,
        label=label,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"],
        help_text=f"Local time ({settings.TIME_ZONE}).",
    )


def _add_months(value, months: int):
    local = timezone.localtime(value)
    month_index = local.month - 1 + months
    year = local.year + month_index // 12
    month = month_index % 12 + 1
    day = min(local.day, calendar.monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day)


def _shift_schedule(value, recurrence: str, count: int):
    if value is None:
        return None
    if recurrence == "daily":
        return value + timedelta(days=count)
    if recurrence == "weekly":
        return value + timedelta(days=7 * count)
    if recurrence == "biweekly":
        return value + timedelta(days=14 * count)
    if recurrence == "monthly":
        return _add_months(value, count)
    return value


def join_token(value: str) -> str:
    value = (value or "").strip()
    match = JOIN_TOKEN_PATTERN.search(value)
    if match:
        return match.group(1)
    if value and "/" not in value and "?" not in value and " " not in value:
        return value.strip("/")
    return ""


class RunnerSignupForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("username",)


class JoinEventForm(forms.Form):
    code = forms.CharField(
        label="Event link or code",
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Paste the event link",
                "autocomplete": "off",
                "autocapitalize": "none",
                "spellcheck": "false",
            }
        ),
    )

    def clean_code(self):
        token = join_token(self.cleaned_data["code"])
        event = (
            Event.objects.filter(status=Event.Status.OPEN, enrollment_token=token).first()
            if token
            else None
        )
        if event is None:
            raise ValidationError("This event link is not active.")
        self.event = event
        return token


class EventForm(forms.ModelForm):
    schedule_starts_at = schedule_field("Starts")
    schedule_ends_at = schedule_field("Ends")
    route_file = forms.FileField(
        required=False,
        help_text="GPX track or route, up to 2 MiB. Required when creating an event.",
    )
    recurrence = forms.ChoiceField(
        choices=RECURRENCE_CHOICES,
        required=False,
        initial="none",
        label="Repeats",
        help_text="Creates draft events with the same route.",
    )
    occurrences = forms.IntegerField(
        required=False,
        initial=4,
        min_value=2,
        max_value=52,
        label="Total events",
        help_text="Includes this first event. Maximum 52.",
    )

    class Meta:
        model = Event
        fields = ["name", "schedule_starts_at", "schedule_ends_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_new = self.instance.pk is None
        self.created_events = []

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk and self.instance.status == Event.Status.CLOSED:
            raise ValidationError("A closed event cannot be edited.")
        starts_at = cleaned.get("schedule_starts_at")
        ends_at = cleaned.get("schedule_ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            raise ValidationError("End time must be after start time.")
        if not self.instance.pk:
            config = TelegramConfig.load()
            if config is None or config.group_id is None:
                raise ValidationError(
                    "Set the Telegram group in Admin -> Telegram settings before creating an event."
                )
            if (cleaned.get("recurrence") or "none") != "none" and not cleaned.get(
                "occurrences"
            ):
                self.add_error("occurrences", "Choose how many events to create.")
        if cleaned.get("route_file") is None and not self.instance.route_segments:
            raise ValidationError("Upload a GPX route.")
        return cleaned

    def clean_route_file(self):
        upload = self.cleaned_data.get("route_file")
        if upload is None:
            return None
        data = upload.read(MAX_GPX_BYTES + 1)
        return parse_gpx(data)

    def save(self, commit=True):
        event = super().save(commit=False)
        segments = self.cleaned_data.get("route_file")
        if segments is not None:
            event.route_segments = segments
        if not event.group_id:
            config = TelegramConfig.load()
            if config is not None:
                event.group_id = config.group_id
        if commit:
            event.save()
            self.created_events.append(event)
            recurrence = self.cleaned_data.get("recurrence") or "none"
            occurrences = self.cleaned_data.get("occurrences") or 1
            if self.is_new and recurrence != "none":
                for index in range(1, occurrences):
                    clone = Event.objects.create(
                        name=event.name,
                        status=Event.Status.DRAFT,
                        group_id=event.group_id,
                        schedule_starts_at=_shift_schedule(
                            event.schedule_starts_at, recurrence, index
                        ),
                        schedule_ends_at=_shift_schedule(
                            event.schedule_ends_at, recurrence, index
                        ),
                        route_segments=event.route_segments,
                    )
                    self.created_events.append(clone)
            self.save_m2m()
        return event


class RestartEventForm(forms.Form):
    schedule_starts_at = schedule_field("New start")
    schedule_ends_at = schedule_field("New end")

    def __init__(self, *args, event: Event, **kwargs):
        self.event = event
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            now = timezone.localtime(timezone.now()) + timedelta(days=7)
            original = (
                timezone.localtime(event.schedule_starts_at)
                if event.schedule_starts_at
                else now
            )
            starts_at = now.replace(
                hour=original.hour,
                minute=original.minute,
                second=0,
                microsecond=0,
            )
            if event.schedule_starts_at and event.schedule_ends_at:
                duration = event.schedule_ends_at - event.schedule_starts_at
            else:
                duration = timedelta(hours=2)
            self.initial.update(
                {
                    "schedule_starts_at": starts_at,
                    "schedule_ends_at": starts_at + duration,
                }
            )

    def clean(self):
        cleaned = super().clean()
        starts_at = cleaned.get("schedule_starts_at")
        ends_at = cleaned.get("schedule_ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            raise ValidationError("End time must be after start time.")
        return cleaned

    def save(self):
        return Event.objects.create(
            name=self.event.name,
            status=Event.Status.DRAFT,
            group_id=self.event.group_id,
            schedule_starts_at=self.cleaned_data["schedule_starts_at"],
            schedule_ends_at=self.cleaned_data["schedule_ends_at"],
            route_segments=self.event.route_segments,
        )
