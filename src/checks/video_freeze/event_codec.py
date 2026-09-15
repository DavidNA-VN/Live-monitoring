import base64
import json
from datetime import datetime

from models.freeze import (
    VideoFreezeAlertRecoveryState,
    VideoFreezeAlertType,
    VideoFreezeEventStatus,
    VideoFreezeLiveEvent,
    VideoFreezeSeverity,
)
from models.frame_fingerprint import BoundaryFrameFingerprint


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
                "reference_segment_duration": event.reference_segment_duration,
                "detection_closed": event.detection_closed,
                "last_boundary_fingerprint": (
                    VideoFreezeEventCodec._encode_fingerprint(
                        event.last_boundary_fingerprint
                    )
                ),
                "start_segment_uri": event.start_segment_uri,
                "end_segment_uri": event.end_segment_uri,
                "coverage_complete": event.coverage_complete,
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
            reference_segment_duration=float(
                data.get(
                    "reference_segment_duration",
                    data["last_segment_duration"],
                )
            ),
            detection_closed=(data.get("detection_closed") is True),
            last_boundary_fingerprint=(
                VideoFreezeEventCodec._decode_fingerprint(
                    data.get("last_boundary_fingerprint")
                )
            ),
            start_segment_uri=str(data.get("start_segment_uri", "")),
            end_segment_uri=str(data.get("end_segment_uri", "")),
            coverage_complete=data.get("coverage_complete", True) is True,
        )

    @staticmethod
    def _encode_fingerprint(
        value: BoundaryFrameFingerprint | None,
    ) -> dict[str, object] | None:
        if value is None:
            return None
        return {
            "algorithm": value.algorithm,
            "width": value.width,
            "height": value.height,
            "pixels_base64": base64.b64encode(value.pixels).decode("ascii"),
            "is_black": value.is_black,
        }

    @staticmethod
    def _decode_fingerprint(value: object) -> BoundaryFrameFingerprint | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("boundary fingerprint must be an object")
        try:
            pixels = base64.b64decode(
                str(value["pixels_base64"]), validate=True
            )
            is_black = value["is_black"]
            if not isinstance(is_black, bool):
                raise TypeError("fingerprint is_black must be a boolean")
            return BoundaryFrameFingerprint(
                algorithm=str(value["algorithm"]),
                width=int(value["width"]),
                height=int(value["height"]),
                pixels=pixels,
                is_black=is_black,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid boundary fingerprint") from exc

    @staticmethod
    def _encode_time(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _decode_time(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None


class VideoFreezeAlertRecoveryCodec:
    @staticmethod
    def encode(state: VideoFreezeAlertRecoveryState) -> str:
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
            sort_keys=True,
        )

    @staticmethod
    def decode(raw: str) -> VideoFreezeAlertRecoveryState:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("video-freeze recovery payload must be an object")
        pending = data.get("recovery_pending", False)
        if not isinstance(pending, bool):
            raise ValueError("recovery_pending must be a JSON boolean")
        integers = {
            "healthy_segments_observed": data.get("healthy_segments_observed", 0),
            "last_observed_sequence": data.get("last_observed_sequence", -1),
            "timeline_generation": data.get("timeline_generation", 0),
            "discontinuity_sequence": data.get("discontinuity_sequence", 0),
        }
        for name, value in integers.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be a JSON integer")
        return VideoFreezeAlertRecoveryState(
            alert_event_id=str(data["alert_event_id"]),
            alert_type=VideoFreezeAlertType(data["alert_type"]),
            recovery_pending=pending,
            **integers,
        )
