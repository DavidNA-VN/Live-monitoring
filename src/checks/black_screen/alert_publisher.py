from datetime import datetime, timezone

from checks.black_screen.repeated_reducer import RepeatedBlackAlert
from core.alert_stream import AlertSink, RedisAlertStream
from core.redis_keys import AlertRedisKeys, RuntimeRedisKeys
from models.alert import (
    AlertCategory,
    AlertEnvelope,
    deterministic_alert_id,
)
from models.black_live import BlackLiveEvent
from policies.black_screen import BlackScreenAlertPolicy


class BlackAlertPublisher:
    """Maps black domain events to the versioned alert contract."""

    def __init__(
        self,
        *,
        stream_id: str,
        alert_keys: AlertRedisKeys,
        runtime_keys: RuntimeRedisKeys,
        stream_max_length: int = 10_000,
        alert_sink: AlertSink | None = None,
    ) -> None:
        self.stream_id = stream_id
        self.stream = alert_sink or RedisAlertStream(
            alert_keys=alert_keys,
            runtime_keys=runtime_keys,
            max_length=stream_max_length,
        )

    def add_event(
        self,
        pipeline,
        *,
        event: BlackLiveEvent,
        state: str,
        reason: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        attributes = {
            "duration": f"{event.duration:.6f}",
            "start_sequence": str(event.start_sequence),
            "end_sequence": str(event.end_sequence),
            "timeline_generation": str(event.timeline_generation),
            "start_media_revision": event.start_media_revision,
            "last_media_revision": event.last_media_revision,
        }
        envelope = AlertEnvelope(
            alert_id=deterministic_alert_id(
                stream_id=self.stream_id,
                event_id=event.event_id,
                state=state,
                reason=reason,
                revision=str(event.end_sequence),
            ),
            event_id=event.event_id,
            category=AlertCategory.CONTENT,
            event_type="BLACK_SCREEN",
            state=state,
            stream_id=self.stream_id,
            check="black_screen",
            variant_id=event.variant_id,
            occurred_at=event.end_program_time
            or event.start_program_time
            or now,
            emitted_at=now,
            event_started_at=event.start_program_time,
            event_ended_at=(
                event.end_program_time if state == "RESOLVED" else None
            ),
            reason=reason,
            attributes=attributes,
        )
        self.stream.append(pipeline, envelope)

    def add_repeated(
        self,
        pipeline,
        *,
        alert: RepeatedBlackAlert,
        variant_id: str,
        policy: BlackScreenAlertPolicy,
    ) -> None:
        now = datetime.now(timezone.utc)
        attributes = {
            "occurrences": str(alert.occurrences),
            "total_black_duration": f"{alert.total_black_duration:.6f}",
            "latest_event_id": alert.latest_event_id,
        }
        if alert.state.value != "RESOLVED":
            attributes["window_seconds"] = str(policy.repeated_window)
        envelope = AlertEnvelope(
            alert_id=deterministic_alert_id(
                stream_id=self.stream_id,
                event_id=alert.event_id,
                state=alert.state.value,
                reason=alert.reason,
                revision=str(alert.occurrences),
            ),
            event_id=alert.event_id,
            category=AlertCategory.CONTENT,
            event_type="REPEATED_BLACK_SCREEN",
            state=alert.state.value,
            stream_id=self.stream_id,
            check="black_screen",
            variant_id=variant_id,
            occurred_at=now,
            emitted_at=now,
            reason=alert.reason,
            attributes=attributes,
        )
        self.stream.append(pipeline, envelope)
