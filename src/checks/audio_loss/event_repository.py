from checks.audio_loss.alert_publisher import AudioLossAlertPublisher
from checks.audio_loss.event_codec import AudioLossEventCodec
from checks.audio_loss.redis_keys import AudioLossRedisKeys
from models.audio_loss import AudioLossLiveEvent


class RedisAudioLossEventRepository:
    """Queues canonical audio-loss state and alerts into one transaction."""

    def __init__(
        self,
        *,
        stream_id: str,
        redis_client,
        audio_keys: AudioLossRedisKeys,
        event_ttl_seconds: int,
        commit_ttl_seconds: int,
        alerts: AudioLossAlertPublisher,
    ) -> None:
        if event_ttl_seconds <= 0:
            raise ValueError("event_ttl_seconds must be > 0")
        if commit_ttl_seconds <= 0:
            raise ValueError("commit_ttl_seconds must be > 0")
        self.stream_id = stream_id
        self.redis = redis_client
        self.keys = audio_keys
        self.event_ttl_seconds = event_ttl_seconds
        self.commit_ttl_seconds = commit_ttl_seconds
        self.alerts = alerts
        self.codec = AudioLossEventCodec()

    def load_open(self, variant_stable_id: str) -> AudioLossLiveEvent | None:
        raw = self.redis.get(
            self.keys.open_event(self.stream_id, variant_stable_id)
        )
        return self.codec.decode(raw) if raw else None

    def queue_persist_open(
        self,
        pipeline,
        *,
        event: AudioLossLiveEvent,
        alert: bool,
    ) -> None:
        payload = self.codec.encode(event)
        pipeline.set(
            self.keys.open_event(self.stream_id, event.variant_stable_id),
            payload,
            ex=self.event_ttl_seconds,
        )
        pipeline.set(
            self.keys.event(
                self.stream_id,
                event.variant_stable_id,
                event.event_id,
            ),
            payload,
            ex=self.event_ttl_seconds,
        )
        if alert:
            self.alerts.add_event(
                pipeline,
                event=event,
                state="OPEN",
                reason=event.primary_cause.value,
            )

    def queue_resolve(
        self,
        pipeline,
        *,
        event: AudioLossLiveEvent,
        reason: str,
        alert_on_resolution: bool,
    ) -> None:
        pipeline.set(
            self.keys.event(
                self.stream_id,
                event.variant_stable_id,
                event.event_id,
            ),
            self.codec.encode(event),
            ex=self.event_ttl_seconds,
        )
        pipeline.delete(
            self.keys.open_event(self.stream_id, event.variant_stable_id)
        )
        if alert_on_resolution:
            self.alerts.add_event(
                pipeline,
                event=event,
                state="OPEN",
                reason=event.primary_cause.value,
            )
            self.alerts.add_event(
                pipeline,
                event=event,
                state="RESOLVED",
                reason=reason,
            )
        elif event.alert_sent:
            self.alerts.add_event(
                pipeline,
                event=event,
                state="RESOLVED",
                reason=reason,
            )

    def queue_commit(self, pipeline, commit_key: str) -> None:
        pipeline.set(
            commit_key,
            "1",
            ex=self.commit_ttl_seconds,
        )
