from __future__ import annotations

from typing import Any

from checks.black_screen.alert_publisher import BlackAlertPublisher
from checks.black_screen.event_codec import BlackEventCodec
from checks.black_screen.redis_keys import BlackScreenRedisKeys
from models.black_live import BlackLiveEvent


class RedisBlackEventRepository:
    """Owns canonical black event persistence transactions."""

    def __init__(
        self,
        *,
        storage_id: str,
        redis_client: Any,
        black_keys: BlackScreenRedisKeys,
        event_ttl_seconds: int,
        commit_ttl_seconds: int,
        alerts: BlackAlertPublisher,
    ) -> None:
        self.storage_id = storage_id
        self.redis = redis_client
        self.keys = black_keys
        self.event_ttl_seconds = event_ttl_seconds
        self.commit_ttl_seconds = commit_ttl_seconds
        self.alerts = alerts
        self.codec = BlackEventCodec()

    def encode(self, event: BlackLiveEvent) -> str:
        return self.codec.encode(event)

    def load_open(self, variant_stable_id: str) -> BlackLiveEvent | None:
        raw = self.redis.get(
            self.keys.open_event(self.storage_id, variant_stable_id)
        )
        return self.codec.decode(raw) if raw else None

    def load_event(
        self,
        variant_stable_id: str,
        event_id: str,
    ) -> BlackLiveEvent | None:
        raw = self.redis.get(
            self.keys.event(self.storage_id, variant_stable_id, event_id)
        )
        return self.codec.decode(raw) if raw else None

    def mark_committed(
        self,
        commit_key: str,
        *,
        pipeline: Any = None,
    ) -> None:
        owns_pipe = pipeline is None
        pipe = self.redis.pipeline(transaction=True) if owns_pipe else pipeline
        self._add_commit(pipe, commit_key)
        if owns_pipe:
            pipe.execute()

    def persist_open(
        self,
        event: BlackLiveEvent,
        *,
        alert: bool,
        commit_key: str | None,
        pipeline: Any = None,
    ) -> None:
        payload = self.codec.encode(event)
        owns_pipe = pipeline is None
        pipe = self.redis.pipeline(transaction=True) if owns_pipe else pipeline
        pipe.set(
            self.keys.open_event(
                self.storage_id, event.variant_stable_id
            ),
            payload,
            ex=self.event_ttl_seconds,
        )
        pipe.set(
            self.keys.event(
                self.storage_id,
                event.variant_stable_id,
                event.event_id,
            ),
            payload,
            ex=self.event_ttl_seconds,
        )
        if alert:
            self.alerts.add_event(
                pipe,
                event=event,
                state="OPEN",
                reason="continuous_black",
            )
        self._add_commit(pipe, commit_key)
        if owns_pipe:
            pipe.execute()

    def close_canonical(
        self,
        event: BlackLiveEvent,
        *,
        alert_open: bool = False,
        commit_key: str | None = None,
        pipeline: Any = None,
    ) -> None:
        owns_pipe = pipeline is None
        pipe = self.redis.pipeline(transaction=True) if owns_pipe else pipeline
        pipe.set(
            self.keys.event(
                self.storage_id,
                event.variant_stable_id,
                event.event_id,
            ),
            self.codec.encode(event),
            ex=self.event_ttl_seconds,
        )
        pipe.delete(
            self.keys.open_event(
                self.storage_id, event.variant_stable_id
            )
        )
        if alert_open:
            self.alerts.add_event(
                pipe,
                event=event,
                state="OPEN",
                reason="threshold_reached_on_resolution",
            )
        self._add_commit(pipe, commit_key)
        if owns_pipe:
            pipe.execute()

    def resolve_continuous_alert(
        self,
        *,
        event: BlackLiveEvent,
        reason: str = "healthy_segment_confirmed",
        pipeline: Any = None,
    ) -> None:
        owns_pipe = pipeline is None
        pipe = self.redis.pipeline(transaction=True) if owns_pipe else pipeline
        self.alerts.add_event(
            pipe,
            event=event,
            state="RESOLVED",
            reason=reason,
        )
        if owns_pipe:
            pipe.execute()

    def resolve_long(
        self,
        event: BlackLiveEvent,
        *,
        reason: str,
        alert_on_resolution: bool,
        commit_key: str | None,
        pipeline: Any = None,
    ) -> None:
        owns_pipe = pipeline is None
        pipe = self.redis.pipeline(transaction=True) if owns_pipe else pipeline
        pipe.set(
            self.keys.event(
                self.storage_id,
                event.variant_stable_id,
                event.event_id,
            ),
            self.codec.encode(event),
            ex=self.event_ttl_seconds,
        )
        pipe.delete(
            self.keys.open_event(
                self.storage_id, event.variant_stable_id
            )
        )
        if alert_on_resolution:
            self.alerts.add_event(
                pipe,
                event=event,
                state="OPEN",
                reason="threshold_reached_on_resolution",
            )
            self.alerts.add_event(
                pipe, event=event, state="RESOLVED", reason=reason
            )
        elif event.long_alert_sent:
            self.alerts.add_event(
                pipe, event=event, state="RESOLVED", reason=reason
            )
        self._add_commit(pipe, commit_key)
        if owns_pipe:
            pipe.execute()

    def close_unknown(
        self,
        event: BlackLiveEvent,
        *,
        commit_key: str | None,
        pipeline: Any = None,
    ) -> None:
        """Close observed media state without claiming content recovery."""
        owns_pipe = pipeline is None
        pipe = self.redis.pipeline(transaction=True) if owns_pipe else pipeline
        pipe.set(
            self.keys.event(
                self.storage_id,
                event.variant_stable_id,
                event.event_id,
            ),
            self.codec.encode(event),
            ex=self.event_ttl_seconds,
        )
        pipe.delete(
            self.keys.open_event(
                self.storage_id, event.variant_stable_id
            )
        )
        self._add_commit(pipe, commit_key)
        if owns_pipe:
            pipe.execute()

    def _add_commit(self, pipeline: Any, commit_key: str | None) -> None:
        if commit_key is not None:
            pipeline.set(
                commit_key,
                "1",
                ex=self.commit_ttl_seconds,
            )
