from __future__ import annotations

from datetime import datetime, timezone

from core.alert_stream import AlertSink, RedisAlertStream
from core.redis_keys import AlertRedisKeys, RuntimeRedisKeys
from models.alert import AlertCategory, AlertEnvelope, deterministic_alert_id
from models.macroblocking import MacroblockingLiveEvent
from policies.macroblocking import MacroblockingAlertPolicy


class MacroblockingAlertPublisher:
    def __init__(
        self,
        *,
        storage_id: str,
        external_stream_id: str,
        alert_keys: AlertRedisKeys,
        runtime_keys: RuntimeRedisKeys,
        policy: MacroblockingAlertPolicy,
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
        event: MacroblockingLiveEvent,
        state: str,
        reason: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        attributes = {
            "severity": "ALERT",
            "duration": f"{event.duration:.6f}",
            "evidence_duration": f"{event.evidence_duration:.6f}",
            "alert_duration": f"{self.policy.alert_duration:g}",
            "affected_area_threshold": (
                f"{self.policy.affected_area_threshold:.6f}"
            ),
            "average_affected_area_ratio": (
                f"{event.average_affected_area_ratio:.6f}"
            ),
            "peak_affected_area_ratio": f"{event.peak_affected_area_ratio:.6f}",
            "average_blocking_confidence": (
                f"{event.average_blocking_confidence:.6f}"
            ),
            "peak_blocking_confidence": (
                f"{event.peak_blocking_confidence:.6f}"
            ),
            "average_boundary_support_ratio": (
                f"{event.average_boundary_support_ratio:.6f}"
            ),
            "start_sequence": str(event.start_sequence),
            "end_sequence": str(event.end_sequence),
            "affected_segment_count": str(event.affected_segment_count),
            "timeline_generation": str(event.timeline_generation),
            "start_media_revision": event.start_media_revision,
            "last_media_revision": event.last_media_revision,
            "start_segment_uri": event.start_segment_uri,
            "end_segment_uri": event.end_segment_uri,
            "start_offset_seconds": f"{event.start_offset:.6f}",
            "end_offset_seconds": f"{event.end_offset:.6f}",
            "coverage_complete": str(event.coverage_complete).lower(),
        }
        envelope = AlertEnvelope(
            alert_id=deterministic_alert_id(
                stream_id=self.storage_id,
                event_id=event.event_id,
                state=state,
                reason=reason,
                revision=(
                    f"{event.variant_stable_id}:{event.end_sequence}:"
                    f"{event.last_media_revision}"
                ),
            ),
            event_id=event.event_id,
            category=AlertCategory.CONTENT,
            event_type="MACROBLOCKING",
            state=state,
            stream_id=self.external_stream_id,
            check="macroblocking",
            variant_id=event.variant_id,
            variant_stable_id=event.variant_stable_id,
            occurred_at=event.end_program_time or event.start_program_time or now,
            emitted_at=now,
            event_started_at=event.start_program_time,
            event_ended_at=event.end_program_time if state == "RESOLVED" else None,
            reason=reason,
            attributes=attributes,
        )
        self.stream.append(pipeline, envelope)
        metric = (
            "macroblocking_resolved_total"
            if state == "RESOLVED"
            else "macroblocking_alert_total"
        )
        self._metric(pipeline, metric, 1)
        if state != "RESOLVED" and event.end_program_time is not None:
            observed = event.end_program_time
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            latency = max(0.0, (now - observed.astimezone(timezone.utc)).total_seconds())
            self._metric(pipeline, "macroblocking_alert_latency_seconds_total", latency)

    def metric(self, pipeline, name: str, value: int | float = 1) -> None:
        self._metric(pipeline, name, value)

    def _metric(self, pipeline, name: str, value: int | float) -> None:
        key = self.runtime_keys.metrics(self.storage_id)
        if isinstance(value, int):
            pipeline.hincrby(key, name, value)
        else:
            pipeline.hincrbyfloat(key, name, value)
        pipeline.expire(key, self.metrics_ttl_seconds)
