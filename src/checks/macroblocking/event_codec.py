from __future__ import annotations

import json
import math
from datetime import datetime

from models.macroblocking import (
    MacroblockingAlertRecoveryState,
    MacroblockingEventStatus,
    MacroblockingLiveEvent,
)


class MacroblockingEventCodec:
    FIELDS = frozenset(
        {
            "event_id", "stream_id", "variant_id", "variant_stable_id",
            "timeline_generation", "discontinuity_sequence", "start_sequence",
            "end_sequence", "start_offset", "end_offset", "start_program_time",
            "end_program_time", "duration", "evidence_duration",
            "last_segment_duration", "average_affected_area_ratio",
            "peak_affected_area_ratio", "average_blocking_confidence",
            "peak_blocking_confidence", "average_boundary_support_ratio",
            "start_media_revision", "last_media_revision",
            "affected_segment_count", "status", "alert_sent",
            "resolution_reason", "detection_closed", "start_segment_uri",
            "end_segment_uri", "coverage_complete",
        }
    )

    @staticmethod
    def encode(event: MacroblockingLiveEvent) -> str:
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
                "start_program_time": MacroblockingEventCodec._time(
                    event.start_program_time
                ),
                "end_program_time": MacroblockingEventCodec._time(
                    event.end_program_time
                ),
                "duration": event.duration,
                "evidence_duration": event.evidence_duration,
                "last_segment_duration": event.last_segment_duration,
                "average_affected_area_ratio": event.average_affected_area_ratio,
                "peak_affected_area_ratio": event.peak_affected_area_ratio,
                "average_blocking_confidence": event.average_blocking_confidence,
                "peak_blocking_confidence": event.peak_blocking_confidence,
                "average_boundary_support_ratio": (
                    event.average_boundary_support_ratio
                ),
                "start_media_revision": event.start_media_revision,
                "last_media_revision": event.last_media_revision,
                "affected_segment_count": event.affected_segment_count,
                "status": event.status.value,
                "alert_sent": event.alert_sent,
                "resolution_reason": event.resolution_reason,
                "detection_closed": event.detection_closed,
                "start_segment_uri": event.start_segment_uri,
                "end_segment_uri": event.end_segment_uri,
                "coverage_complete": event.coverage_complete,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def decode(raw: str) -> MacroblockingLiveEvent:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("payload must be an object")
            extra = set(data) - MacroblockingEventCodec.FIELDS
            if extra:
                raise ValueError(f"unexpected event fields: {sorted(extra)}")
            booleans = {}
            for name, default in (
                ("alert_sent", False),
                ("detection_closed", False),
                ("coverage_complete", True),
            ):
                value = data.get(name, default)
                if not isinstance(value, bool):
                    raise ValueError(f"{name} must be a JSON boolean")
                booleans[name] = value
            event = MacroblockingLiveEvent(
                event_id=MacroblockingEventCodec._text(data, "event_id"),
                stream_id=MacroblockingEventCodec._text(data, "stream_id"),
                variant_id=MacroblockingEventCodec._text(data, "variant_id"),
                variant_stable_id=MacroblockingEventCodec._text(
                    data, "variant_stable_id"
                ),
                timeline_generation=MacroblockingEventCodec._integer(
                    data, "timeline_generation"
                ),
                discontinuity_sequence=MacroblockingEventCodec._integer(
                    data, "discontinuity_sequence"
                ),
                start_sequence=MacroblockingEventCodec._integer(
                    data, "start_sequence"
                ),
                end_sequence=MacroblockingEventCodec._integer(data, "end_sequence"),
                start_offset=MacroblockingEventCodec._number(data, "start_offset"),
                end_offset=MacroblockingEventCodec._number(data, "end_offset"),
                start_program_time=MacroblockingEventCodec._parse_time(
                    data.get("start_program_time")
                ),
                end_program_time=MacroblockingEventCodec._parse_time(
                    data.get("end_program_time")
                ),
                duration=MacroblockingEventCodec._number(data, "duration"),
                evidence_duration=MacroblockingEventCodec._number(
                    data, "evidence_duration", default=data.get("duration")
                ),
                last_segment_duration=MacroblockingEventCodec._number(
                    data, "last_segment_duration"
                ),
                average_affected_area_ratio=MacroblockingEventCodec._number(
                    data, "average_affected_area_ratio"
                ),
                peak_affected_area_ratio=MacroblockingEventCodec._number(
                    data, "peak_affected_area_ratio"
                ),
                average_blocking_confidence=MacroblockingEventCodec._number(
                    data, "average_blocking_confidence"
                ),
                peak_blocking_confidence=MacroblockingEventCodec._number(
                    data, "peak_blocking_confidence"
                ),
                average_boundary_support_ratio=MacroblockingEventCodec._number(
                    data, "average_boundary_support_ratio"
                ),
                start_media_revision=MacroblockingEventCodec._text(
                    data, "start_media_revision", allow_empty=True
                ),
                last_media_revision=MacroblockingEventCodec._text(
                    data, "last_media_revision", allow_empty=True
                ),
                affected_segment_count=MacroblockingEventCodec._integer(
                    data, "affected_segment_count"
                ),
                status=MacroblockingEventStatus(data["status"]),
                resolution_reason=MacroblockingEventCodec._optional_text(
                    data.get("resolution_reason")
                ),
                start_segment_uri=MacroblockingEventCodec._text(
                    data, "start_segment_uri", allow_empty=True
                ),
                end_segment_uri=MacroblockingEventCodec._text(
                    data, "end_segment_uri", allow_empty=True
                ),
                **booleans,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid macroblocking event payload") from exc
        return event

    @staticmethod
    def _text(data, name, *, allow_empty=False) -> str:
        value = data[name]
        if not isinstance(value, str) or (not allow_empty and not value):
            raise ValueError(f"{name} must be a string")
        return value

    @staticmethod
    def _integer(data, name) -> int:
        value = data[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
        return value

    @staticmethod
    def _optional_text(value) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ValueError("optional text must be null or a non-empty string")
        return value

    @staticmethod
    def _number(data, name, *, default=None) -> float:
        value = data.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        return value

    @staticmethod
    def _time(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _parse_time(value) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("program time must be a string")
        return datetime.fromisoformat(value)


class MacroblockingAlertRecoveryCodec:
    FIELDS = frozenset(
        {
            "alert_event_id", "recovery_pending", "healthy_segments_observed",
            "last_observed_sequence", "timeline_generation",
            "discontinuity_sequence",
        }
    )

    @staticmethod
    def encode(state: MacroblockingAlertRecoveryState) -> str:
        return json.dumps(
            {
                "alert_event_id": state.alert_event_id,
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
    def decode(raw: str) -> MacroblockingAlertRecoveryState:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("payload must be an object")
            extra = set(data) - MacroblockingAlertRecoveryCodec.FIELDS
            if extra:
                raise ValueError(f"unexpected recovery fields: {sorted(extra)}")
            pending = data.get("recovery_pending", False)
            if not isinstance(pending, bool):
                raise ValueError("recovery_pending must be a JSON boolean")
            return MacroblockingAlertRecoveryState(
                alert_event_id=MacroblockingEventCodec._text(
                    data, "alert_event_id"
                ),
                recovery_pending=pending,
                healthy_segments_observed=MacroblockingEventCodec._integer(
                    data, "healthy_segments_observed"
                ),
                last_observed_sequence=MacroblockingEventCodec._integer(
                    data, "last_observed_sequence"
                ),
                timeline_generation=MacroblockingEventCodec._integer(
                    data, "timeline_generation"
                ),
                discontinuity_sequence=MacroblockingEventCodec._integer(
                    data, "discontinuity_sequence"
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid macroblocking recovery payload") from exc
