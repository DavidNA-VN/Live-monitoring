import json
from datetime import datetime

from models.audio_loss import (
    AudioLossCause,
    AudioLossEventStatus,
    AudioLossLiveEvent,
)


class AudioLossEventCodec:
    @staticmethod
    def encode(event: AudioLossLiveEvent) -> str:
        return json.dumps(
            {
                "event_id": event.event_id,
                "stream_id": event.stream_id,
                "variant_id": event.variant_id,
                "variant_stable_id": event.variant_stable_id,
                "discontinuity_sequence": event.discontinuity_sequence,
                "timeline_generation": event.timeline_generation,
                "start_media_revision": event.start_media_revision,
                "last_media_revision": event.last_media_revision,
                "start_sequence": event.start_sequence,
                "end_sequence": event.end_sequence,
                "start_offset": event.start_offset,
                "end_offset": event.end_offset,
                "start_program_time": (
                    event.start_program_time.isoformat()
                    if event.start_program_time
                    else None
                ),
                "end_program_time": (
                    event.end_program_time.isoformat()
                    if event.end_program_time
                    else None
                ),
                "duration": event.duration,
                "last_segment_duration": event.last_segment_duration,
                "primary_cause": event.primary_cause.value,
                "causes_seen": [cause.value for cause in event.causes_seen],
                "affected_segment_count": event.affected_segment_count,
                "status": event.status.value,
                "alert_sent": event.alert_sent,
                "resolution_reason": event.resolution_reason,
                "audio_group": event.audio_group,
                "rendition_name": event.rendition_name,
                "language": event.language,
                "rendition_default": event.rendition_default,
                "rendition_autoselect": event.rendition_autoselect,
                "hls_stable_rendition_id": event.hls_stable_rendition_id,
            },
            separators=(",", ":"),
        )

    @staticmethod
    def decode(raw: str) -> AudioLossLiveEvent:
        data = json.loads(raw)
        return AudioLossLiveEvent(
            event_id=data["event_id"],
            stream_id=data["stream_id"],
            variant_id=data["variant_id"],
            variant_stable_id=data["variant_stable_id"],
            discontinuity_sequence=int(data["discontinuity_sequence"]),
            timeline_generation=int(data.get("timeline_generation", 0)),
            start_media_revision=data.get("start_media_revision", ""),
            last_media_revision=data.get("last_media_revision", ""),
            start_sequence=int(data["start_sequence"]),
            end_sequence=int(data["end_sequence"]),
            start_offset=float(data["start_offset"]),
            end_offset=float(data["end_offset"]),
            start_program_time=AudioLossEventCodec._time(
                data.get("start_program_time")
            ),
            end_program_time=AudioLossEventCodec._time(
                data.get("end_program_time")
            ),
            duration=float(data["duration"]),
            last_segment_duration=float(data["last_segment_duration"]),
            primary_cause=AudioLossCause(data["primary_cause"]),
            causes_seen=[
                AudioLossCause(cause) for cause in data.get("causes_seen", [])
            ],
            affected_segment_count=int(data.get("affected_segment_count", 1)),
            status=AudioLossEventStatus(data["status"]),
            alert_sent=bool(data["alert_sent"]),
            resolution_reason=data.get("resolution_reason"),
            audio_group=data.get("audio_group"),
            rendition_name=data.get("rendition_name"),
            language=data.get("language"),
            rendition_default=bool(data.get("rendition_default", False)),
            rendition_autoselect=bool(
                data.get("rendition_autoselect", False)
            ),
            hls_stable_rendition_id=data.get("hls_stable_rendition_id"),
        )

    @staticmethod
    def _time(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None
