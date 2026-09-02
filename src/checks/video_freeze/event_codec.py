import json
from datetime import datetime

from models.freeze import (
    VideoFreezeEventStatus,
    VideoFreezeLiveEvent,
    VideoFreezeSeverity,
)


class VideoFreezeEventCodec:
    @staticmethod
    def encode(event: VideoFreezeLiveEvent) -> str:
        return json.dumps(
            {
                "event_id": event.event_id,
                "stream_id": event.stream_id,
                "variant_id": event.variant_id,
                "variant_stable_id": event.variant_stable_id,
                "timeline_generation": event.timeline_generation,
                "discontinuity_sequence": event.discontinuity_sequence,
                "start_sequence": event.start_sequence,
                "end_sequence": event.end_sequence,
                "start_offset": event.start_offset,
                "end_offset": event.end_offset,
                "start_program_time": VideoFreezeEventCodec._encode_time(
                    event.start_program_time
                ),
                "end_program_time": VideoFreezeEventCodec._encode_time(
                    event.end_program_time
                ),
                "duration": event.duration,
                "last_segment_duration": event.last_segment_duration,
                "start_media_revision": event.start_media_revision,
                "last_media_revision": event.last_media_revision,
                "affected_segment_count": event.affected_segment_count,
                "status": event.status.value,
                "highest_severity": (
                    event.highest_severity.value
                    if event.highest_severity is not None
                    else None
                ),
                "warning_sent": event.warning_sent,
                "alert_sent": event.alert_sent,
                "resolution_reason": event.resolution_reason,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def decode(raw: str) -> VideoFreezeLiveEvent:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("video-freeze event payload must be an object")
        severity = data.get("highest_severity")
        return VideoFreezeLiveEvent(
            event_id=str(data["event_id"]),
            stream_id=str(data["stream_id"]),
            variant_id=str(data["variant_id"]),
            variant_stable_id=str(data["variant_stable_id"]),
            timeline_generation=int(data.get("timeline_generation", 0)),
            discontinuity_sequence=int(data["discontinuity_sequence"]),
            start_sequence=int(data["start_sequence"]),
            end_sequence=int(data["end_sequence"]),
            start_offset=float(data["start_offset"]),
            end_offset=float(data["end_offset"]),
            start_program_time=VideoFreezeEventCodec._decode_time(
                data.get("start_program_time")
            ),
            end_program_time=VideoFreezeEventCodec._decode_time(
                data.get("end_program_time")
            ),
            duration=float(data["duration"]),
            last_segment_duration=float(data["last_segment_duration"]),
            start_media_revision=str(data.get("start_media_revision", "")),
            last_media_revision=str(data.get("last_media_revision", "")),
            affected_segment_count=int(data.get("affected_segment_count", 1)),
            status=VideoFreezeEventStatus(
                data.get("status", VideoFreezeEventStatus.OPEN.value)
            ),
            highest_severity=(
                VideoFreezeSeverity(severity) if severity is not None else None
            ),
            warning_sent=bool(data.get("warning_sent", False)),
            alert_sent=bool(data.get("alert_sent", False)),
            resolution_reason=data.get("resolution_reason"),
        )

    @staticmethod
    def _encode_time(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _decode_time(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None
