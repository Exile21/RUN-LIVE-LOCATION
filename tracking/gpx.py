import math

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from django.core.exceptions import ValidationError

MAX_GPX_BYTES = 2 * 1024 * 1024
MAX_POINTS = 20_000


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _coordinate(element, name: str) -> float:
    value = element.get(name)
    if value is None:
        raise ValidationError("GPX point is missing a coordinate.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValidationError("GPX coordinates must be numbers.") from None
    if not math.isfinite(number):
        raise ValidationError("GPX coordinates must be finite.")
    return number


def _segment(element) -> list[list[float]]:
    points = []
    for child in element:
        if _local_name(child.tag) != "trkpt" and _local_name(child.tag) != "rtept":
            continue
        latitude = _coordinate(child, "lat")
        longitude = _coordinate(child, "lon")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValidationError("GPX coordinates are out of range.")
        points.append([latitude, longitude])
    return points


def parse_gpx(data: bytes) -> list[list[list[float]]]:
    if not isinstance(data, (bytes, bytearray)):
        raise ValidationError("Upload a GPX file.")
    if len(data) > MAX_GPX_BYTES:
        raise ValidationError("GPX file is too large.")
    if not data.strip():
        raise ValidationError("GPX file is empty.")
    try:
        root = ElementTree.fromstring(
            bytes(data),
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (DefusedXmlException, ElementTree.ParseError, ValueError, TypeError):
        raise ValidationError("Upload a valid GPX file without DTDs or entities.") from None
    if _local_name(root.tag) != "gpx":
        raise ValidationError("Upload a GPX file.")

    segments: list[list[list[float]]] = []
    total_points = 0
    for element in root.iter():
        name = _local_name(element.tag)
        if name not in {"trkseg", "rte"}:
            continue
        segment = _segment(element)
        total_points += len(segment)
        if total_points > MAX_POINTS:
            raise ValidationError("GPX file has too many points.")
        if len(segment) >= 2:
            segments.append(segment)

    if not segments:
        raise ValidationError("GPX file must contain a track or route with at least two points.")
    return segments


def route_preview_path(segments: list[list[list[float]]]) -> str:
    """Fit validated GPX segments into a centered 320 by 200 SVG path."""
    segments = [segment for segment in segments if len(segment) >= 2]
    if not segments:
        return ""
    latitudes = [latitude for segment in segments for latitude, _ in segment]
    # shortcut: local projection for running courses; use a geodesic projection for continent-scale routes.
    longitude_scale = math.cos(math.radians((min(latitudes) + max(latitudes)) / 2))
    origin = segments[0][0][1]
    projected = [
        [(((longitude - origin + 180) % 360 - 180) * longitude_scale, -latitude)
         for latitude, longitude in segment]
        for segment in segments
    ]
    xs, ys = zip(*(point for segment in projected for point in segment))
    left, top = min(xs), min(ys)
    width, height = max(xs) - left, max(ys) - top
    if width == 0 and height == 0:
        return ""
    scale = min(288 / width if width else math.inf, 168 / height if height else math.inf)
    offset_x, offset_y = (320 - width * scale) / 2, (200 - height * scale) / 2
    return " ".join(
        " ".join(
            f'{"M" if index == 0 else "L"}{offset_x + (x - left) * scale:.2f},{offset_y + (y - top) * scale:.2f}'
            for index, (x, y) in enumerate(segment)
        )
        for segment in projected
    )
