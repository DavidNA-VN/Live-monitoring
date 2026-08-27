from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any

import redis

from app.desired_state_codec import (
    desired_state_from_json,
    desired_state_to_dict,
)
from core.desired_state_repository import (
    DesiredStateCorruptedError,
    DesiredStatePersistenceError,
    DesiredStateRepository,
    DesiredStateUnavailableError,
)
from core.redis_keys import DesiredStateRedisKeys
from models.desired_stream_state import DesiredStreamState

logger = logging.getLogger(__name__)


class RedisDesiredStateRepository(DesiredStateRepository):
    """Redis Hash and Stream backed repository for desired stream states and error quarantine."""

    def __init__(
        self,
        *,
        redis_client: Any,
        keys: DesiredStateRedisKeys | None = None,
        quarantine_max_length: int = 1_000,
    ) -> None:
        if quarantine_max_length <= 0:
            raise ValueError("quarantine_max_length must be > 0")
        self.redis = getattr(redis_client, "client", redis_client)
        self.keys = keys or DesiredStateRedisKeys()
        self.quarantine_max_length = quarantine_max_length

    def get(self, stream_id: str) -> DesiredStreamState | None:
        try:
            raw = self.redis.hget(self.keys.current_states(), stream_id)
        except redis.RedisError as exc:
            raise DesiredStateUnavailableError(
                f"Failed to read desired state for stream '{stream_id}'"
            ) from exc

        if raw is None:
            return None

        try:
            record = desired_state_from_json(self._text(raw))
            if record.stream_id != stream_id:
                raise ValueError("hash field does not match payload stream_id")
            return record
        except Exception as exc:
            self.quarantine(stream_id, self._text(raw), str(exc))
            raise DesiredStateCorruptedError(
                f"Corrupt desired state for stream '{stream_id}': {exc}"
            ) from exc

    def list_all(self) -> list[DesiredStreamState]:
        try:
            raw_map = self.redis.hgetall(self.keys.current_states())
        except redis.RedisError as exc:
            raise DesiredStateUnavailableError(
                "Failed to list desired states"
            ) from exc

        records: list[DesiredStreamState] = []
        for raw_id, raw_val in raw_map.items():
            stream_id = self._text(raw_id)
            raw_payload = self._text(raw_val)
            try:
                record = desired_state_from_json(raw_payload)
                if record.stream_id != stream_id:
                    raise ValueError("hash field does not match payload stream_id")
                records.append(record)
            except Exception as exc:
                logger.warning(
                    "Quarantining corrupt desired state for stream '%s': %s",
                    stream_id,
                    exc,
                )
                self.quarantine(stream_id, raw_payload, str(exc))

        return records

    def save(self, record: DesiredStreamState) -> None:
        payload = json.dumps(
            desired_state_to_dict(record),
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            self.redis.hset(
                self.keys.current_states(),
                record.stream_id,
                payload,
            )
        except redis.RedisError as exc:
            raise DesiredStatePersistenceError(
                f"Failed to persist desired state for stream '{record.stream_id}'"
            ) from exc

    def delete(self, stream_id: str) -> bool:
        try:
            removed = self.redis.hdel(self.keys.current_states(), stream_id)
            return bool(removed)
        except redis.RedisError as exc:
            raise DesiredStateUnavailableError(
                f"Failed to delete desired state for stream '{stream_id}'"
            ) from exc

    def quarantine(self, stream_id: str, raw_payload: str, error: str) -> None:
        try:
            pipeline = self.redis.pipeline(transaction=True)
            pipeline.xadd(
                self.keys.recovery_errors(),
                {
                    "stream_id": stream_id,
                    "payload": raw_payload,
                    "error": error,
                    "quarantined_at": datetime.now(timezone.utc).isoformat(),
                },
                maxlen=self.quarantine_max_length,
                approximate=False,
            )
            pipeline.hdel(self.keys.current_states(), stream_id)
            pipeline.execute()
        except redis.RedisError as exc:
            logger.warning(
                "Failed to write quarantine error for stream '%s': %s",
                stream_id,
                exc,
            )

    @staticmethod
    def _text(value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
