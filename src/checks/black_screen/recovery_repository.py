from __future__ import annotations

from typing import Any

from checks.black_screen.event_codec import BlackAlertRecoveryCodec
from checks.black_screen.redis_keys import BlackScreenRedisKeys
from models.black_live import BlackAlertRecoveryState


class RedisBlackAlertRecoveryRepository:
    """Owns persistence and lifecycle queries for alert recovery state."""

    def __init__(
        self,
        *,
        storage_id: str,
        redis_client: Any,
        black_keys: BlackScreenRedisKeys,
        ttl_seconds: int = 86_400,
        codec: type[BlackAlertRecoveryCodec] = BlackAlertRecoveryCodec,
    ) -> None:
        self.storage_id = storage_id
        self.redis = redis_client
        self.keys = black_keys
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
            raise TypeError("ttl_seconds must be an int")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        self.ttl_seconds = ttl_seconds
        self.codec = codec

    def load(self, variant_stable_id: str) -> BlackAlertRecoveryState | None:
        key = self.keys.alert_recovery(self.storage_id, variant_stable_id)
        raw = self.redis.get(key)
        if not raw:
            return None
        return self.codec.decode(raw)

    def save(
        self,
        pipeline: Any,
        variant_stable_id: str,
        state: BlackAlertRecoveryState,
    ) -> None:
        key = self.keys.alert_recovery(self.storage_id, variant_stable_id)
        payload = self.codec.encode(state)
        pipeline.set(key, payload, ex=self.ttl_seconds)

    def delete(
        self,
        pipeline: Any,
        variant_stable_id: str,
    ) -> None:
        key = self.keys.alert_recovery(self.storage_id, variant_stable_id)
        pipeline.delete(key)
