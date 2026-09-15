from __future__ import annotations

from typing import Any

from checks.macroblocking.event_codec import MacroblockingAlertRecoveryCodec
from checks.macroblocking.redis_keys import MacroblockingRedisKeys
from models.macroblocking import MacroblockingAlertRecoveryState


class RedisMacroblockingRecoveryRepository:
    def __init__(
        self,
        *,
        storage_id: str,
        redis_client: Any,
        keys: MacroblockingRedisKeys,
        ttl_seconds: int = 86_400,
    ) -> None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise TypeError("ttl_seconds must be an int")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        self.storage_id = storage_id
        self.redis = redis_client
        self.keys = keys
        self.ttl_seconds = ttl_seconds

    def load(self, variant_stable_id: str) -> MacroblockingAlertRecoveryState | None:
        raw = self.redis.get(
            self.keys.alert_recovery(self.storage_id, variant_stable_id)
        )
        return MacroblockingAlertRecoveryCodec.decode(raw) if raw else None

    def save(
        self,
        pipeline: Any,
        variant_stable_id: str,
        state: MacroblockingAlertRecoveryState,
    ) -> None:
        pipeline.set(
            self.keys.alert_recovery(self.storage_id, variant_stable_id),
            MacroblockingAlertRecoveryCodec.encode(state),
            ex=self.ttl_seconds,
        )

    def delete(self, pipeline: Any, variant_stable_id: str) -> None:
        pipeline.delete(
            self.keys.alert_recovery(self.storage_id, variant_stable_id)
        )
