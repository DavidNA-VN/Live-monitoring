from checks.video_freeze.alert_publisher import VideoFreezeAlertPublisher
from checks.video_freeze.event_codec import VideoFreezeEventCodec
from checks.video_freeze.redis_keys import VideoFreezeRedisKeys
from models.freeze import VideoFreezeLiveEvent, VideoFreezeSeverity


class RedisVideoFreezeEventRepository:
    """Queues freeze state, alerts and segment commits atomically."""

    def __init__(
        self,
        *,
        storage_id: str,
        redis_client,
        freeze_keys: VideoFreezeRedisKeys,
        event_ttl_seconds: int,
        commit_ttl_seconds: int,
        alerts: VideoFreezeAlertPublisher,
    ) -> None:
        if event_ttl_seconds <= 0 or commit_ttl_seconds <= 0:
            raise ValueError("event and commit TTLs must be > 0")
        self.storage_id = storage_id
        self.redis = redis_client
        self.keys = freeze_keys
        self.event_ttl_seconds = event_ttl_seconds
        self.commit_ttl_seconds = commit_ttl_seconds
        self.alerts = alerts
        self.codec = VideoFreezeEventCodec()

    def load_open(self, variant_stable_id: str) -> VideoFreezeLiveEvent | None:
        raw = self.redis.get(
            self.keys.open_event(self.storage_id, variant_stable_id)
        )
        return self.codec.decode(raw) if raw else None

    def queue_persist_open(
        self,
        pipeline,
        *,
        event: VideoFreezeLiveEvent,
        notification: tuple[str, VideoFreezeSeverity, str] | None,
    ) -> None:
        payload = self.codec.encode(event)
        pipeline.set(
            self.keys.open_event(self.storage_id, event.variant_stable_id),
            payload,
            ex=self.event_ttl_seconds,
        )
        pipeline.set(
            self.keys.event(
                self.storage_id,
                event.variant_stable_id,
                event.event_id,
            ),
            payload,
            ex=self.event_ttl_seconds,
        )
        if notification is not None:
            state, severity, reason = notification
            self.alerts.add_event(
                pipeline,
                event=event,
                state=state,
                severity=severity,
                reason=reason,
            )

    def queue_resolve(
        self,
        pipeline,
        *,
        event: VideoFreezeLiveEvent,
        opening_notification: tuple[
            str, VideoFreezeSeverity, str
        ] | None,
        resolution_severity: VideoFreezeSeverity | None,
        reason: str,
    ) -> None:
        pipeline.set(
            self.keys.event(
                self.storage_id,
                event.variant_stable_id,
                event.event_id,
            ),
            self.codec.encode(event),
            ex=self.event_ttl_seconds,
        )
        pipeline.delete(
            self.keys.open_event(self.storage_id, event.variant_stable_id)
        )
        if opening_notification is not None:
            state, severity, opening_reason = opening_notification
            self.alerts.add_event(
                pipeline,
                event=event,
                state=state,
                severity=severity,
                reason=opening_reason,
            )
        if resolution_severity is not None:
            self.alerts.add_event(
                pipeline,
                event=event,
                state="RESOLVED",
                severity=resolution_severity,
                reason=reason,
            )
        if reason == "observation_gap":
            self.alerts.add_interrupted_metric(pipeline)

    def queue_commit(self, pipeline, commit_key: str) -> None:
        pipeline.set(commit_key, "1", ex=self.commit_ttl_seconds)
