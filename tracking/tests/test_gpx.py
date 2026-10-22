from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from tracking.forms import EventForm
from tracking.gpx import MAX_POINTS, parse_gpx
from tracking.models import Event, TelegramConfig
from tracking.tests.helpers import make_event, make_telegram_config

FIXTURE = Path(__file__).parent / "fixtures" / "two_segments.gpx"


def gpx_with_points(count: int) -> bytes:
    points = "".join(
        f'<trkpt lat="1.{index % 10}" lon="2.{index % 10}"></trkpt>'
        for index in range(count)
    )
    return (
        '<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><trkseg>{points}</trkseg></trk></gpx>"
    ).encode()


class GpxTests(TestCase):
    def test_segments_remain_separate(self):
        segments = parse_gpx(FIXTURE.read_bytes())
        self.assertEqual([len(segment) for segment in segments], [2, 2])
        self.assertEqual([segment[0] for segment in segments], [[1.0, 2.0], [3.0, 4.0]])

    def test_route_points_are_supported(self):
        data = b"""<gpx xmlns="http://www.topografix.com/GPX/1/1">
        <rte><rtept lat="5" lon="6"></rtept><rtept lat="5.1" lon="6.1"></rtept></rte>
        </gpx>"""
        self.assertEqual(parse_gpx(data), [[[5.0, 6.0], [5.1, 6.1]]])

    def test_singleton_segments_are_discarded(self):
        data = b"""<gpx xmlns="http://www.topografix.com/GPX/1/1">
        <trk><trkseg><trkpt lat="1" lon="2"></trkpt></trkseg></trk>
        <trk><trkseg><trkpt lat="3" lon="4"></trkpt><trkpt lat="3.1" lon="4.1"></trkpt></trkseg></trk>
        </gpx>"""
        self.assertEqual(parse_gpx(data), [[[3.0, 4.0], [3.1, 4.1]]])

    def test_rejects_invalid_coordinates(self):
        for lat, lon in (("91", "2"), ("1", "181"), ("nan", "2"), ("1", "inf")):
            with self.subTest(lat=lat, lon=lon):
                data = f'<gpx><trk><trkseg><trkpt lat="{lat}" lon="{lon}"></trkpt><trkpt lat="1" lon="2"></trkpt></trkseg></trk></gpx>'.encode()
                with self.assertRaises(ValidationError):
                    parse_gpx(data)
        with self.assertRaises(ValidationError):
            parse_gpx(b"<gpx><trk><trkseg><trkpt lon='2'/><trkpt lat='1' lon='2'/></trkseg></trk></gpx>")

    def test_rejects_malformed_and_non_gpx(self):
        with self.assertRaises(ValidationError):
            parse_gpx(b"<gpx><trk>")
        with self.assertRaises(ValidationError):
            parse_gpx(b"<html></html>")
        with self.assertRaises(ValidationError):
            parse_gpx(b"")

    def test_rejects_dtd_and_entities(self):
        data = b'<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY x "boom">]><gpx><trk><trkseg><trkpt lat="1" lon="2"/><trkpt lat="1" lon="2"/></trkseg></trk></gpx>'
        with self.assertRaises(ValidationError):
            parse_gpx(data)

    def test_utf16_dtd_is_rejected(self):
        data = (
            '<?xml version="1.0" encoding="utf-16"?>'
            '<!DOCTYPE gpx [<!ENTITY x "boom">]>'
            '<gpx><trk><trkseg><trkpt lat="1" lon="2"/>'
            '<trkpt lat="1" lon="2"/></trkseg></trk></gpx>'
        ).encode("utf-16")
        with self.assertRaises(ValidationError):
            parse_gpx(data)

    def test_rejects_oversized_file_and_point_count(self):
        with self.assertRaises(ValidationError):
            parse_gpx(b"x" * (2 * 1024 * 1024 + 1))
        with self.assertRaises(ValidationError):
            parse_gpx(gpx_with_points(MAX_POINTS + 1))


class EventFormTests(TestCase):
    def test_create_requires_route_and_saves_segments(self):
        form = EventForm(data={"name": "New", "group_id": "-100123"})
        self.assertFalse(form.is_valid())
        self.assertIn("Upload a GPX route.", form.non_field_errors())

        upload = SimpleUploadedFile("route.gpx", FIXTURE.read_bytes(), content_type="application/gpx+xml")
        form = EventForm(
            data={"name": "New", "group_id": "-100123"},
            files={"route_file": upload},
        )
        self.assertTrue(form.is_valid(), form.errors)
        event = form.save()
        self.assertEqual(event.route_segments, [[[1.0, 2.0], [1.1, 2.1]], [[3.0, 4.0], [3.1, 4.1]]])

    def test_edit_without_file_keeps_route(self):
        event = make_event(status=Event.Status.DRAFT)
        original = event.route_segments
        form = EventForm(data={"name": "Renamed", "group_id": str(event.group_id)}, instance=event)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.route_segments, original)
        self.assertEqual(saved.name, "Renamed")

    def test_invalid_replacement_leaves_route_unchanged(self):
        event = make_event(status=Event.Status.DRAFT)
        original = event.route_segments
        upload = SimpleUploadedFile("bad.gpx", b"<gpx><trk>", content_type="application/gpx+xml")
        form = EventForm(
            data={"name": "Broken", "group_id": str(event.group_id)},
            files={"route_file": upload},
            instance=event,
        )
        self.assertFalse(form.is_valid())
        event.refresh_from_db()
        self.assertEqual(event.route_segments, original)
        self.assertEqual(event.name, "Test event")

    def test_closed_event_cannot_be_edited(self):
        event = make_event(status=Event.Status.CLOSED)
        form = EventForm(data={"name": "Changed", "group_id": str(event.group_id)}, instance=event)
        self.assertFalse(form.is_valid())

    def test_open_event_group_is_locked(self):
        event = make_event()
        form = EventForm(
            data={"name": "Changed", "group_id": "-1009999999999"},
            instance=event,
        )
        self.assertTrue(form.fields["group_id"].disabled)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.group_id, event.group_id)
        self.assertEqual(saved.name, "Changed")
