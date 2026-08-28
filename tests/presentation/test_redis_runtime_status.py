from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Tuple
import pytest
import redis.exceptions

from core.redis_keys import PublicRuntimeRedisKeys, RedisNamespace
from presentation.api.adapters.redis_runtime_status import (
    CorruptRuntimeStatusError,
    RedisRuntimeStatusReader,
    RuntimeStatusRedisUnavailableError,
)
from presentation.api.models import StreamHealthEnum, StreamStatusEnum


class FakeAsyncRedisHGet:
    """Mô phỏng async Redis client chỉ hỗ trợ HGET và theo dõi lịch sử gọi lệnh."""

    def __init__(self) -> None:
        self.hashes: Dict[str, Dict[str, str]] = {}
        self.calls: List[Tuple[str, str, str]] = []
        self.raise_exc: Exception | None = None

    async def hget(self, name: str, key: str) -> Any:
        self.calls.append(("HGET", name, key))
        if self.raise_exc:
            raise self.raise_exc
        return self.hashes.get(name, {}).get(key)


def make_valid_payload(stream_id: str = "chan-01") -> dict:
    return {
        "schema_version": "1.0",
        "stream_id": stream_id,
        "status": "RUNNING",
        "health": "HEALTHY",
        "started_at": "2026-08-28T09:00:00+00:00",
        "last_poll_at": "2026-08-28T10:00:00+00:00",
        "active_variant_count": 2,
        "queue_depth": 3,
        "queue_lag_seconds": 0.5,
        "error": None,
        "telemetry_available": True,
        "health_reasons": [],
        "checks": {"black_screen": "ENABLED", "audio_loss": "ENABLED"},
        "worker_id": "worker-01",
        "observed_at": "2026-08-28T10:00:05+00:00",
    }


@pytest.mark.anyio
async def test_redis_runtime_status_reader_success():
    fake_redis = FakeAsyncRedisHGet()
    keys = PublicRuntimeRedisKeys(RedisNamespace("test-monitor"))
    reader = RedisRuntimeStatusReader(redis_client=fake_redis, keys=keys)

    payload = make_valid_payload("chan-01")
    fake_redis.hashes[keys.current_statuses()] = {"chan-01": json.dumps(payload)}

    status = await reader.get_status("chan-01")
    assert status is not None
    assert status.stream_id == "chan-01"
    assert status.status == StreamStatusEnum.RUNNING
    assert status.health == StreamHealthEnum.HEALTHY
    assert status.worker_id == "worker-01"
    assert status.observed_at == datetime(2026, 8, 28, 10, 0, 5, tzinfo=timezone.utc)

    # Verify strictly one HGET call was performed
    assert len(fake_redis.calls) == 1
    assert fake_redis.calls[0] == ("HGET", keys.current_statuses(), "chan-01")


@pytest.mark.anyio
async def test_redis_runtime_status_reader_missing_field_returns_none():
    fake_redis = FakeAsyncRedisHGet()
    keys = PublicRuntimeRedisKeys(RedisNamespace("test-monitor"))
    reader = RedisRuntimeStatusReader(redis_client=fake_redis, keys=keys)

    status = await reader.get_status("non-existent-stream")
    assert status is None
    assert len(fake_redis.calls) == 1
    assert fake_redis.calls[0] == ("HGET", keys.current_statuses(), "non-existent-stream")


@pytest.mark.anyio
async def test_redis_runtime_status_reader_connection_error():
    fake_redis = FakeAsyncRedisHGet()
    fake_redis.raise_exc = redis.exceptions.ConnectionError("Redis down")
    keys = PublicRuntimeRedisKeys(RedisNamespace("test-monitor"))
    reader = RedisRuntimeStatusReader(redis_client=fake_redis, keys=keys)

    with pytest.raises(RuntimeStatusRedisUnavailableError):
        await reader.get_status("chan-01")


@pytest.mark.anyio
async def test_redis_runtime_status_reader_timeout_error():
    fake_redis = FakeAsyncRedisHGet()
    fake_redis.raise_exc = redis.exceptions.TimeoutError("Redis timeout")
    keys = PublicRuntimeRedisKeys(RedisNamespace("test-monitor"))
    reader = RedisRuntimeStatusReader(redis_client=fake_redis, keys=keys)

    with pytest.raises(RuntimeStatusRedisUnavailableError):
        await reader.get_status("chan-01")


@pytest.mark.anyio
async def test_redis_runtime_status_reader_corrupt_json():
    fake_redis = FakeAsyncRedisHGet()
    keys = PublicRuntimeRedisKeys(RedisNamespace("test-monitor"))
    reader = RedisRuntimeStatusReader(redis_client=fake_redis, keys=keys)

    fake_redis.hashes[keys.current_statuses()] = {"chan-01": "corrupt{json"}

    with pytest.raises(CorruptRuntimeStatusError):
        await reader.get_status("chan-01")


@pytest.mark.anyio
async def test_redis_runtime_status_reader_invalid_contract():
    fake_redis = FakeAsyncRedisHGet()
    keys = PublicRuntimeRedisKeys(RedisNamespace("test-monitor"))
    reader = RedisRuntimeStatusReader(redis_client=fake_redis, keys=keys)

    # Missing observed_at and worker_id
    invalid_payload = {"schema_version": "1.0", "stream_id": "chan-01"}
    fake_redis.hashes[keys.current_statuses()] = {"chan-01": json.dumps(invalid_payload)}

    with pytest.raises(CorruptRuntimeStatusError):
        await reader.get_status("chan-01")


@pytest.mark.anyio
async def test_redis_runtime_status_reader_empty_stream_id_rejected():
    fake_redis = FakeAsyncRedisHGet()
    reader = RedisRuntimeStatusReader(redis_client=fake_redis)

    with pytest.raises(ValueError, match="stream_id must not be empty"):
        await reader.get_status("")
