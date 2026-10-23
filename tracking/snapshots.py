from datetime import UTC, datetime

from .models import Event
from .state import tracking_status


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def event_snapshot(event: Event, *, staff: bool, now: datetime) -> dict:
    runners = []
    if event.status != Event.Status.CLOSED:
        if event.status == Event.Status.DRAFT:
            participants = event.participants.order_by("created_at", "id")
        else:
            participants = event.participants.filter(consented_at__isnull=False)
            if not staff:
                participants = participants.filter(withdrawn=False)
            participants = participants.order_by("created_at", "id")
        for index, participant in enumerate(participants, start=1):
            if event.status == Event.Status.DRAFT:
                status = "registered"
            else:
                status = tracking_status(participant, now=now)
            visible = status in {"live", "stale"}
            row = {
                "id": str(participant.public_id),
                "number": f"{index:02d}",
                "name": participant.display_name,
                "status": status,
                "latitude": participant.latitude if visible else None,
                "longitude": participant.longitude if visible else None,
                "accuracy": participant.accuracy if visible else None,
                "position_at": _iso(participant.position_at) if visible else None,
            }
            if staff:
                row["laps"] = participant.laps
            runners.append(row)
    summary = {
        "participants": len(runners),
        "live": sum(1 for runner in runners if runner["status"] == "live"),
        "stale": sum(1 for runner in runners if runner["status"] == "stale"),
    }
    return {
        "event": {"name": event.name, "status": event.status},
        "route_segments": event.route_segments,
        "server_time": _iso(now),
        "runners": runners,
        "summary": summary,
    }
