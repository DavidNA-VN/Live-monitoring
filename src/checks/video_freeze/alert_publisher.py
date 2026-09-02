from datetime import datetime, timezone

from checks.video_freeze.repeated_state import RepeatedFreezeAlert
from core.alert_stream import AlertSink, RedisAlertStream
from core.redis_keys import AlertRedisKeys, RuntimeRedisKeys
from models.alert import AlertCategory, AlertEnvelope, deterministic_alert_id
from models.freeze import VideoFreezeLiveEvent, VideoFreezeSeverity
from policies.video_freeze import VideoFreezeAlertPolicy


class VideoFreezeAlertPublisher:
    def __init__(
        self,
        *,
        storage_id: str,
        external_stream_id: str,
        alert_keys: AlertRedisKeys,
        runtime_keys: RuntimeRedisKeys,
        policy: VideoFreezeAlertPolicy,
        stream_max_length: int = 10_000,
        alert_sink: AlertSink | None = None,
        metrics_ttl_seconds: int = 120,
    ) -> None:
        self.storage_id = storage_id
        self.external_stream_id = external_stream_id
        self.runtime_keys = runtime_keys
        self.policy = policy
        self.metrics_ttl_seconds = metrics_ttl_seconds
        self.stream = alert_sink or RedisAlertStream(
            storage_id=storage_id,
            alert_keys=alert_keys,
            runtime_keys=runtime_keys,
            max_length=stream_max_length,
        )

    def add_event(
        self,
        pipeline,
        *,
        event: VideoFreezeLiveEvent,
        state: str,
        severity: VideoFreezeSeverity,
        reason: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        attributes = {
            "severity": severity.value,
            "duration": f"{event.duration:.6f}",
            "warning_duration": f"{self.policy.warning_duration:g}",
            "alert_duration": f"{self.policy.alert_duration:g}",
            "start_sequence": str(event.start_sequence),
            "end_sequence": str(event.end_sequence),
            "affected_segment_count": str(event.affected_segment_count),
            "timeline_generation": str(event.timeline_generation),
            "start_media_revision": event.start_media_revision,
            "last_media_revision": event.last_media_revision,
        }
        envelope = AlertEnvelope(
            alert_id=deterministic_alert_id(
                stream_id=self.storage_id,
                event_id=event.event_id,
                state=state,
                reason=reason,
                revision=(
                    f"{event.variant_stable_id}:{event.end_sequence}:"
                    f"{event.last_media_revision}:{severity.value}"
                ),
            ),
            event_id=event.event_id,
            category=AlertCategory.CONTENT,
            event_type="VIDEO_FREEZE",
            state=state,
            stream_id=self.external_stream_id,
            check="video_freeze",
            variant_id=event.variant_id,
            variant_stable_id=event.variant_stable_id,
            occurred_at=event.end_program_time or event.start_program_time or now,
            emitted_at=now,
            event_started_at=event.start_program_time,
            event_ended_at=(
                event.end_program_time if state == "RESOLVED" else None
            ),
            reason=reason,
            attributes=attributes,
        )
        self.stream.append(pipeline, envelope)
        metric = (
            "video_freeze_resolved_total"
            if state == "RESOLVED"
            else (
                "video_freeze_alert_total"
                if severity is VideoFreezeSeverity.ALERT
                else "video_freeze_warning_total"
            )
        )
        self._metric(pipeline, metric)

    def add_repeated(
        self,
        pipeline,
        *,
        alert: RepeatedFreezeAlert,
        variant_id: str,
        variant_stable_id: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        envelope = AlertEnvelope(
            alert_id=deterministic_alert_id(
                stream_id=self.storage_id,
                event_id=alert.event_id,
                state=alert.state,
                reason=alert.reason,
                revision=f"{alert.latest_event_id}:{alert.occurrences}",
            ),
            event_id=alert.event_id,
            category=AlertCategory.CONTENT,
            event_type="REPEATED_VIDEO_FREEZE",
            state=alert.state,
            stream_id=self.external_stream_id,
            check="video_freeze",
            variant_id=variant_id,
            variant_stable_id=variant_stable_id,
            occurred_at=now,
            emitted_at=now,
            reason=alert.reason,
            attributes={
                "severity": VideoFreezeSeverity.ALERT.value,
                "occurrences": str(alert.occurrences),
                "total_freeze_duration": f"{alert.total_duration:.6f}",
                "latest_event_id": alert.latest_event_id,
                "window_seconds": f"{self.policy.repeated_window:g}",
            },
        )
        self.stream.append(pipeline, envelope)
        if alert.state != "RESOLVED":
            self._metric(pipeline, "video_freeze_alert_total")
        else:
            self._metric(pipeline, "video_freeze_resolved_total")

    def add_interrupted_metric(self, pipeline) -> None:
        self._metric(pipeline, "video_freeze_interrupted_total")

    def _metric(self, pipeline, name: str) -> None:
        key = self.runtime_keys.metrics(self.storage_id)
        pipeline.hincrby(key, name, 1)
        pipeline.expire(key, self.metrics_ttl_seconds)
