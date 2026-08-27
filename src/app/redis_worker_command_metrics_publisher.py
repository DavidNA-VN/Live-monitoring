from __future__ import annotations

import logging
from typing import Any

import redis

from core.redis_keys import WorkerRedisKeys
from models.command_metrics import CommandMetricsSnapshot

logger = logging.getLogger(__name__)


class RedisWorkerCommandMetricsPublisher:
    """Best-effort publisher that serializes worker command metrics snapshots to a Redis hash with TTL."""

    def __init__(
        self,
        *,
        redis_client: Any,
        keys: WorkerRedisKeys,
        ttl_seconds: int = 120,
    ) -> None:
        self.redis = (
            redis_client.client
            if hasattr(redis_client, "client")
            else redis_client
        )
        self.keys = keys
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        self.ttl_seconds = int(ttl_seconds)

    def publish(self, snapshot: CommandMetricsSnapshot) -> bool:
        try:
            key = self.keys.command_metrics(snapshot.worker_id)
            mapping = snapshot.to_redis_hash()
            pipeline = self.redis.pipeline(transaction=True)
            pipeline.hset(key, mapping=mapping)
            pipeline.expire(key, self.ttl_seconds)
            pipeline.execute()
            return True
        except Exception as exc:
            logger.warning(
                "Failed to publish worker command metrics worker_id=%s: %s",
                snapshot.worker_id,
                exc,
            )
            return False
