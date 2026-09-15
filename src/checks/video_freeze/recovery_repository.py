from __future__ import annotations

from typing import Any

from checks.video_freeze.event_codec import VideoFreezeAlertRecoveryCodec
from checks.video_freeze.redis_keys import VideoFreezeRedisKeys
from models.freeze import VideoFreezeAlertRecoveryState


class RedisVideoFreezeAlertRecoveryRepository:
    """Owns bounded persistence for pending freeze-alert recovery."""

    def __init__(
        self,
        *,
        storage_id: str,
        redis_client: Any,
        freeze_keys: VideoFreezeRedisKeys,
        ttl_seconds: int = 86_400,
    ) -> None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise TypeError("ttl_seconds must be an int")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        self.storage_id = storage_id
        self.redis = redis_client
        self.keys = freeze_keys
        self.ttl_seconds = ttl_seconds

    def load(self, variant_stable_id: str) -> VideoFreezeAlertRecoveryState | None:
        raw = self.redis.get(
            self.keys.alert_recovery(self.storage_id, variant_stable_id)
        )
        return VideoFreezeAlertRecoveryCodec.decode(raw) if raw else None

    def save(self, pipeline: Any, variant_stable_id: str, state: VideoFreezeAlertRecoveryState) -> None:
        pipeline.set(
            self.keys.alert_recovery(self.storage_id, variant_stable_id),
            VideoFreezeAlertRecoveryCodec.encode(state),
            ex=self.ttl_seconds,
        )

    def delete(self, pipeline: Any, variant_stable_id: str) -> None:
        pipeline.delete(
            self.keys.alert_recovery(self.storage_id, variant_stable_id)
        )
