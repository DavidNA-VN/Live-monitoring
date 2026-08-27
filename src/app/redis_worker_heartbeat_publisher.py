from __future__ import annotations

import json
import logging
from typing import Any

import redis

from core.redis_keys import WorkerRedisKeys
from core.worker_heartbeat import (
    WorkerHeartbeatPublisher,
    WorkerHeartbeatPublishError,
)
from models.worker_heartbeat import WorkerHeartbeat

logger = logging.getLogger(__name__)


class RedisWorkerHeartbeatPublisher(WorkerHeartbeatPublisher):
    """Publishes worker heartbeat to Redis String with TTL and maintains the active worker ZSET."""

    def __init__(
        self,
        *,
        redis_client: Any,
        keys: WorkerRedisKeys | None = None,
    ) -> None:
        self.redis = getattr(redis_client, "client", redis_client)
        self.keys = keys or WorkerRedisKeys()

    def publish(
        self,
        heartbeat: WorkerHeartbeat,
        *,
        ttl_seconds: int = 15,
        discovery_window_seconds: int = 30,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if discovery_window_seconds <= 0:
            raise ValueError("discovery_window_seconds must be > 0")
        if discovery_window_seconds < ttl_seconds:
            raise ValueError(
                "discovery_window_seconds must be >= ttl_seconds"
            )

        heartbeat_key = self.keys.heartbeat(heartbeat.worker_id)
        active_key = self.keys.active_workers()
        payload = json.dumps(heartbeat.to_dict(), separators=(",", ":"), sort_keys=True)

        try:
            redis_time = self.redis.time()
            score = float(redis_time[0]) + (float(redis_time[1]) / 1_000_000)
            stale_cutoff = score - discovery_window_seconds
            pipeline = self.redis.pipeline(transaction=True)
            pipeline.set(heartbeat_key, payload, ex=ttl_seconds)
            pipeline.zadd(active_key, {heartbeat.worker_id: score})
            pipeline.zremrangebyscore(active_key, "-inf", stale_cutoff)
            pipeline.execute()
        except redis.RedisError as exc:
            logger.error(
                "Redis heartbeat publish failed for worker '%s': %s",
                heartbeat.worker_id,
                exc,
            )
            raise WorkerHeartbeatPublishError(
                f"Redis heartbeat publish failed for worker '{heartbeat.worker_id}'"
            ) from exc
