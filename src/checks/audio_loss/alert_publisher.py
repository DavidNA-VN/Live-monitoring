from datetime import datetime, timezone

from core.alert_stream import AlertSink, RedisAlertStream
from core.redis_keys import AlertRedisKeys, RuntimeRedisKeys
from models.alert import AlertCategory, AlertEnvelope, deterministic_alert_id
from models.audio_loss import AudioLossLiveEvent


class AudioLossAlertPublisher:
    def __init__(
        self,
        *,
        stream_id: str,
        alert_keys: AlertRedisKeys,
        runtime_keys: RuntimeRedisKeys,
        threshold_dbfs: float,
        threshold_duration: float,
        stream_max_length: int = 10_000,
        alert_sink: AlertSink | None = None,
    ) -> None:
        self.stream_id = stream_id
        self.threshold_dbfs = threshold_dbfs
        self.threshold_duration = threshold_duration
        self.runtime_keys = runtime_keys
        self.stream = alert_sink or RedisAlertStream(
            alert_keys=alert_keys,
            runtime_keys=runtime_keys,
            max_length=stream_max_length,
        )

    def add_event(
        self,
        pipeline,
        *,
        event: AudioLossLiveEvent,
        state: str,
        reason: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        attributes = {
            "duration": f"{event.duration:.6f}",
            "threshold_dbfs": f"{self.threshold_dbfs:g}",
            "threshold_duration": f"{self.threshold_duration:g}",
            "primary_cause": event.primary_cause.value,
            "causes_seen": ",".join(cause.value for cause in event.causes_seen),
            "start_sequence": str(event.start_sequence),
            "end_sequence": str(event.end_sequence),
            "affected_segment_count": str(event.affected_segment_count),
            "variant_stable_id": event.variant_stable_id,
            "timeline_generation": str(event.timeline_generation),
            "start_media_revision": event.start_media_revision,
            "last_media_revision": event.last_media_revision,
            "channel_mode": "all_channels",
        }
        optional_attributes = {
            "audio_group": event.audio_group,
            "rendition_name": event.rendition_name,
            "language": event.language,
            "hls_stable_rendition_id": event.hls_stable_rendition_id,
        }
        attributes.update(
            {
                key: value
                for key, value in optional_attributes.items()
                if value is not None
            }
        )
        attributes["rendition_default"] = str(
            event.rendition_default
        ).lower()
        attributes["rendition_autoselect"] = str(
            event.rendition_autoselect
        ).lower()
        envelope = AlertEnvelope(
            alert_id=deterministic_alert_id(
                stream_id=self.stream_id,
                event_id=event.event_id,
                state=state,
                reason=reason,
                revision=(
                    f"{event.variant_stable_id}:"
                    f"{event.end_sequence}:"
                    f"{event.last_media_revision}"
                ),
            ),
            event_id=event.event_id,
            category=AlertCategory.CONTENT,
            event_type="AUDIO_LOSS",
            state=state,
            stream_id=self.stream_id,
            check="audio_loss",
            variant_id=event.variant_id,
            occurred_at=(
                event.end_program_time or event.start_program_time or now
            ),
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
            "audio_loss_open_total"
            if state == "OPEN"
            else "audio_loss_resolved_total"
        )
        metrics_key = self.runtime_keys.metrics(self.stream_id)
        pipeline.hincrby(metrics_key, metric, 1)
        pipeline.expire(metrics_key, 120)
