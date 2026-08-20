from checks.black_screen.alert_publisher import BlackAlertPublisher
from checks.black_screen.event_codec import BlackEventCodec
from core.redis_keys import RedisKeyBuilder
from models.black_live import BlackLiveEvent


class RedisBlackEventRepository:
    """Owns canonical black event persistence transactions."""

    def __init__(
        self,
        *,
        stream_id: str,
        redis_client,
        key_builder: RedisKeyBuilder,
        event_ttl_seconds: int,
        commit_ttl_seconds: int,
        alerts: BlackAlertPublisher,
    ) -> None:
        self.stream_id = stream_id
        self.redis = redis_client
        self.keys = key_builder
        self.event_ttl_seconds = event_ttl_seconds
        self.commit_ttl_seconds = commit_ttl_seconds
        self.alerts = alerts
        self.codec = BlackEventCodec()

    def encode(self, event: BlackLiveEvent) -> str:
        return self.codec.encode(event)

    def load_open(self, variant_stable_id: str) -> BlackLiveEvent | None:
        raw = self.redis.get(
            self.keys.black_open_event(self.stream_id, variant_stable_id)
        )
        return self.codec.decode(raw) if raw else None

    def mark_committed(self, commit_key: str) -> None:
        self.redis.set(commit_key, "1", ex=self.commit_ttl_seconds)

    def persist_open(
        self,
        event: BlackLiveEvent,
        *,
        alert: bool,
        commit_key: str | None,
    ) -> None:
        payload = self.codec.encode(event)
        pipeline = self.redis.pipeline(transaction=True)
        pipeline.set(
            self.keys.black_open_event(
                self.stream_id, event.variant_stable_id
            ),
            payload,
            ex=self.event_ttl_seconds,
        )
        pipeline.set(
            self.keys.black_event(self.stream_id, event.event_id),
            payload,
            ex=self.event_ttl_seconds,
        )
        if alert:
            self.alerts.add_event(
                pipeline,
                event=event,
                state="OPEN",
                reason="continuous_black",
            )
        self._add_commit(pipeline, commit_key)
        pipeline.execute()

    def resolve_long(
        self,
        event: BlackLiveEvent,
        *,
        reason: str,
        alert_on_resolution: bool,
        commit_key: str | None,
    ) -> None:
        pipeline = self.redis.pipeline(transaction=True)
        pipeline.set(
            self.keys.black_event(self.stream_id, event.event_id),
            self.codec.encode(event),
            ex=self.event_ttl_seconds,
        )
        pipeline.delete(
            self.keys.black_open_event(
                self.stream_id, event.variant_stable_id
            )
        )
        if alert_on_resolution:
            self.alerts.add_event(
                pipeline,
                event=event,
                state="OPEN",
                reason="threshold_reached_on_resolution",
            )
            self.alerts.add_event(
                pipeline, event=event, state="RESOLVED", reason=reason
            )
        elif event.long_alert_sent:
            self.alerts.add_event(
                pipeline, event=event, state="RESOLVED", reason=reason
            )
        self._add_commit(pipeline, commit_key)
        pipeline.execute()

    def _add_commit(self, pipeline, commit_key: str | None) -> None:
        if commit_key is not None:
            pipeline.set(
                commit_key,
                "1",
                ex=self.commit_ttl_seconds,
            )
