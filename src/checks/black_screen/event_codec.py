import json
from datetime import datetime

from models.black_live import (
    BlackAlertType,
    BlackAlertRecoveryState,
    BlackEventStatus,
    BlackLiveEvent,
)


class BlackEventCodec:
    @staticmethod
    def encode(event: BlackLiveEvent) -> str:
        return json.dumps(
            {
                "event_id": event.event_id,
                "stream_id": event.stream_id,
                "variant_id": event.variant_id,
                "variant_stable_id": event.variant_stable_id,
                "discontinuity_sequence": (
                    event.discontinuity_sequence
                ),
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
                "last_segment_duration": (
                    event.last_segment_duration
                ),
                "affected_segments": event.affected_segments,
                "status": event.status.value,
                "long_alert_sent": event.long_alert_sent,
                "resolution_reason": event.resolution_reason,
                "reference_segment_duration": (
                    event.reference_segment_duration
                ),
                "detection_closed": event.detection_closed,
                "start_segment_uri": event.start_segment_uri,
                "end_segment_uri": event.end_segment_uri,
                "coverage_complete": event.coverage_complete,
            },
            separators=(",", ":"),
        )

    @staticmethod
    def decode(raw: str) -> BlackLiveEvent:
        data = json.loads(raw)
        detection_closed = data.get("detection_closed")
        if detection_closed is not None and not isinstance(
            detection_closed, bool
        ):
            raise ValueError("detection_closed must be a JSON boolean")
        status = BlackEventStatus(data["status"])
        return BlackLiveEvent(
            event_id=data["event_id"],
            stream_id=data["stream_id"],
            variant_id=data["variant_id"],
            variant_stable_id=data["variant_stable_id"],
            discontinuity_sequence=int(
                data["discontinuity_sequence"]
            ),
            timeline_generation=int(data.get("timeline_generation", 0)),
            start_media_revision=data.get("start_media_revision", ""),
            last_media_revision=data.get("last_media_revision", ""),
            start_sequence=int(data["start_sequence"]),
            end_sequence=int(data["end_sequence"]),
            start_offset=float(data["start_offset"]),
            end_offset=float(data["end_offset"]),
            start_program_time=(
                datetime.fromisoformat(
                    data["start_program_time"]
                )
                if data["start_program_time"]
                else None
            ),
            end_program_time=(
                datetime.fromisoformat(
                    data["end_program_time"]
                )
                if data["end_program_time"]
                else None
            ),
            duration=float(data["duration"]),
            last_segment_duration=float(
                data["last_segment_duration"]
            ),
            affected_segments=list(
                data["affected_segments"]
            ),
            status=status,
            long_alert_sent=bool(data["long_alert_sent"]),
            resolution_reason=data.get("resolution_reason"),
            reference_segment_duration=float(
                data.get(
                    "reference_segment_duration",
                    data.get("last_segment_duration", 0.0),
                )
            ),
            detection_closed=(
                detection_closed
                if detection_closed is not None
                else status is BlackEventStatus.RESOLVED
            ),
            start_segment_uri=str(data.get("start_segment_uri", "")),
            end_segment_uri=str(data.get("end_segment_uri", "")),
            coverage_complete=data.get("coverage_complete", True) is True,
        )


class BlackAlertRecoveryCodec:
    @staticmethod
    def encode(state: BlackAlertRecoveryState) -> str:
        return json.dumps(
            {
                "alert_event_id": state.alert_event_id,
                "alert_type": state.alert_type.value,
                "recovery_pending": state.recovery_pending,
                "healthy_segments_observed": state.healthy_segments_observed,
                "last_observed_sequence": state.last_observed_sequence,
                "timeline_generation": state.timeline_generation,
                "discontinuity_sequence": state.discontinuity_sequence,
            },
            separators=(",", ":"),
        )

    @staticmethod
    def decode(raw: str) -> BlackAlertRecoveryState:
        data = json.loads(raw)
        recovery_pending = data.get("recovery_pending", False)
        if not isinstance(recovery_pending, bool):
            raise ValueError("recovery_pending must be a JSON boolean")
        integer_fields = {
            "healthy_segments_observed": data.get(
                "healthy_segments_observed", 0
            ),
            "last_observed_sequence": data.get(
                "last_observed_sequence", -1
            ),
            "timeline_generation": data.get("timeline_generation", 0),
            "discontinuity_sequence": data.get(
                "discontinuity_sequence", 0
            ),
        }
        for name, value in integer_fields.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be a JSON integer")
        return BlackAlertRecoveryState(
            alert_event_id=data["alert_event_id"],
            alert_type=BlackAlertType(data["alert_type"]),
            recovery_pending=recovery_pending,
            healthy_segments_observed=integer_fields[
                "healthy_segments_observed"
            ],
            last_observed_sequence=integer_fields[
                "last_observed_sequence"
            ],
            timeline_generation=integer_fields["timeline_generation"],
            discontinuity_sequence=integer_fields[
                "discontinuity_sequence"
            ],
        )
