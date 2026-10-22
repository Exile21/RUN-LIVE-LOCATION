from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from .gpx import MAX_GPX_BYTES, parse_gpx
from .models import Event, TelegramConfig


class EventForm(forms.ModelForm):
    schedule_starts_at = forms.DateTimeField(
        required=True,
        label="Starts",
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"],
        help_text=f"Local time ({settings.TIME_ZONE}).",
    )
    schedule_ends_at = forms.DateTimeField(
        required=True,
        label="Ends",
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"],
        help_text=f"Local time ({settings.TIME_ZONE}).",
    )
    route_file = forms.FileField(
        required=False,
        help_text="GPX track or route, up to 2 MiB. Required when creating an event.",
    )

    class Meta:
        model = Event
        fields = ["name", "schedule_starts_at", "schedule_ends_at"]

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
            self.save_m2m()
        return event
