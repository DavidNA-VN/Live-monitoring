from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any
from uuid import uuid4

import redis

from core.redis_keys import PublicRuntimeRedisKeys
from core.runtime_status_projector import RuntimeStatusProjector
from models.runtime_status import RuntimeStatus
from models.runtime_status_update import (
    RuntimeStatusUpdate,
    RuntimeStatusUpdateType,
)

logger = logging.getLogger(__name__)


class RuntimeStatusProjectionError(Exception):
    """Raised when projecting or removing runtime status to/from Redis fails."""


class RedisRuntimeStatusProjector(RuntimeStatusProjector):
    """Projects runtime status snapshots and events to Redis Hash and Stream."""

    def __init__(
        self,
        *,
        redis_client: Any,
        keys: PublicRuntimeRedisKeys | None = None,
        status_stream_max_length: int = 10_000,
    ) -> None:
        if status_stream_max_length <= 0:
            raise ValueError("status_stream_max_length must be > 0")
        self.redis = getattr(redis_client, "client", redis_client)
        self.keys = keys or PublicRuntimeRedisKeys()
        self.status_stream_max_length = status_stream_max_length

    def project(self, status: RuntimeStatus) -> bool:
        if status.worker_id is None:
            raise ValueError("RuntimeStatus must have worker_id for public projection")
        if status.observed_at is None:
            raise ValueError("RuntimeStatus must have observed_at for public projection")

        new_dict = status.to_dict()
        new_json = json.dumps(new_dict, separators=(",", ":"), sort_keys=True)
        new_semantic = {k: v for k, v in new_dict.items() if k != "observed_at"}

        try:
            existing_raw = self.redis.hget(self.keys.current_statuses(), status.stream_id)
            if existing_raw is not None:
                try:
                    existing_dict = json.loads(self._text(existing_raw))
                except (json.JSONDecodeError, TypeError):
                    existing_dict = {}
                existing_semantic = {k: v for k, v in existing_dict.items() if k != "observed_at"}
            else:
                existing_semantic = None

            if existing_semantic == new_semantic:
                # State is semantically unchanged; only refresh observed_at snapshot in hash
                self.redis.hset(self.keys.current_statuses(), status.stream_id, new_json)
                return False

            update = RuntimeStatusUpdate(
                update_id=str(uuid4()),
                update_type=RuntimeStatusUpdateType.SNAPSHOT,
                stream_id=status.stream_id,
                worker_id=status.worker_id,
                observed_at=status.observed_at,
                status=status,
            )
            update_json = json.dumps(update.to_dict(), separators=(",", ":"), sort_keys=True)

            pipeline = self.redis.pipeline(transaction=True)
            pipeline.hset(self.keys.current_statuses(), status.stream_id, new_json)
            pipeline.xadd(
                self.keys.status_updates(),
                {"payload": update_json},
                maxlen=self.status_stream_max_length,
                approximate=True,
            )
            pipeline.execute()
            return True
        except redis.RedisError as exc:
            logger.error("Redis projection failed for stream '%s': %s", status.stream_id, exc)
            raise RuntimeStatusProjectionError(f"Redis projection failed for stream '{status.stream_id}'") from exc

    def remove(
        self,
        *,
        stream_id: str,
        worker_id: str,
        observed_at: datetime,
    ) -> bool:
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        else:
            observed_at = observed_at.astimezone(timezone.utc)

        try:
            existing_raw = self.redis.hget(self.keys.current_statuses(), stream_id)
            if existing_raw is None:
                return False

            try:
                existing_dict = json.loads(self._text(existing_raw))
            except (json.JSONDecodeError, TypeError):
                existing_dict = {}

            existing_worker_id = existing_dict.get("worker_id")
            if existing_worker_id != worker_id:
                logger.warning(
                    "Ignoring remove for stream '%s': worker_id mismatch (existing '%s' != caller '%s')",
                    stream_id,
                    existing_worker_id,
                    worker_id,
                )
                return False

            update = RuntimeStatusUpdate(
                update_id=str(uuid4()),
                update_type=RuntimeStatusUpdateType.REMOVED,
                stream_id=stream_id,
                worker_id=worker_id,
                observed_at=observed_at,
                status=None,
            )
            update_json = json.dumps(update.to_dict(), separators=(",", ":"), sort_keys=True)

            pipeline = self.redis.pipeline(transaction=True)
            pipeline.xadd(
                self.keys.status_updates(),
                {"payload": update_json},
                maxlen=self.status_stream_max_length,
                approximate=True,
            )
            pipeline.hdel(self.keys.current_statuses(), stream_id)
            pipeline.execute()
            return True
        except redis.RedisError as exc:
            logger.error("Redis status removal failed for stream '%s': %s", stream_id, exc)
            raise RuntimeStatusProjectionError(f"Redis status removal failed for stream '{stream_id}'") from exc

    @staticmethod
    def _text(value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
