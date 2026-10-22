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
