from checks.macroblocking.alert_publisher import MacroblockingAlertPublisher
from checks.macroblocking.event_codec import MacroblockingEventCodec
from checks.macroblocking.redis_keys import MacroblockingRedisKeys
from models.macroblocking import MacroblockingLiveEvent


class RedisMacroblockingEventRepository:
    def __init__(
        self,
        *,
        storage_id: str,
        redis_client,
        keys: MacroblockingRedisKeys,
        event_ttl_seconds: int,
        commit_ttl_seconds: int,
        alerts: MacroblockingAlertPublisher,
    ) -> None:
        if event_ttl_seconds <= 0 or commit_ttl_seconds <= 0:
            raise ValueError("event and commit TTLs must be > 0")
        self.storage_id = storage_id
        self.redis = redis_client
        self.keys = keys
        self.event_ttl_seconds = event_ttl_seconds
        self.commit_ttl_seconds = commit_ttl_seconds
        self.alerts = alerts

    def load_open(self, variant_stable_id: str) -> MacroblockingLiveEvent | None:
        raw = self.redis.get(
            self.keys.open_event(self.storage_id, variant_stable_id)
        )
        return MacroblockingEventCodec.decode(raw) if raw else None

    def load_event(
        self, variant_stable_id: str, event_id: str
    ) -> MacroblockingLiveEvent | None:
        raw = self.redis.get(
            self.keys.event(self.storage_id, variant_stable_id, event_id)
        )
        return MacroblockingEventCodec.decode(raw) if raw else None

    def queue_persist_open(self, pipeline, event: MacroblockingLiveEvent) -> None:
        payload = MacroblockingEventCodec.encode(event)
        pipeline.set(
            self.keys.open_event(self.storage_id, event.variant_stable_id),
            payload,
            ex=self.event_ttl_seconds,
        )
        pipeline.set(
            self.keys.event(
                self.storage_id, event.variant_stable_id, event.event_id
            ),
            payload,
            ex=self.event_ttl_seconds,
        )

    def queue_close(self, pipeline, event: MacroblockingLiveEvent) -> None:
        pipeline.set(
            self.keys.event(
                self.storage_id, event.variant_stable_id, event.event_id
            ),
            MacroblockingEventCodec.encode(event),
            ex=self.event_ttl_seconds,
        )
        pipeline.delete(
            self.keys.open_event(self.storage_id, event.variant_stable_id)
        )

    def queue_commit(self, pipeline, commit_key: str) -> None:
        pipeline.set(commit_key, "1", ex=self.commit_ttl_seconds)
