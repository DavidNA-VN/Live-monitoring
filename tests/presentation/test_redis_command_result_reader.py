from datetime import datetime, timezone
import json
from typing import Any, Dict
import pytest
import redis.exceptions

from core.redis_keys import ControlRedisKeys, RedisNamespace
from presentation.api.adapters.base import CommandLookupState
from presentation.api.adapters.redis_command_result_reader import (
    CorruptMarkerError,
    ReaderRedisUnavailableError,
    RedisCommandResultReader,
)
from presentation.api.models import CommandResultStatusEnum, CommandTypeEnum


class FakeAsyncRedisReader:
    def __init__(self) -> None:
        self.storage: Dict[str, str] = {}
        self.fail: bool = False

    async def get(self, key: str) -> Any:
        if self.fail:
            raise redis.exceptions.ConnectionError("Redis connection lost")
        return self.storage.get(key)


@pytest.mark.anyio
async def test_redis_command_result_reader_final_applied():
    fake_redis = FakeAsyncRedisReader()
    keys = ControlRedisKeys(RedisNamespace("test-monitor"))
    reader = RedisCommandResultReader(redis_client=fake_redis, keys=keys)

    processed_key = keys.processed_command("cmd-applied-1")
    marker = {
        "fingerprint": "abc12345",
        "result": {
            "schema_version": "1.0",
            "command_id": "cmd-applied-1",
            "command_type": "START",
            "stream_id": "channel-01",
            "status": "APPLIED",
            "changed": True,
            "processed_at": "2026-08-28T10:05:00+00:00",
            "error_code": None,
            "error": None,
        },
    }
    fake_redis.storage[processed_key] = json.dumps(marker)

    lookup = await reader.get_command_result("cmd-applied-1")
    assert lookup.state == CommandLookupState.FINAL
    assert lookup.result is not None
    assert lookup.result.status == CommandResultStatusEnum.APPLIED
    assert lookup.result.changed is True
    assert lookup.result.command_type == CommandTypeEnum.START


@pytest.mark.anyio
async def test_redis_command_result_reader_pending():
    fake_redis = FakeAsyncRedisReader()
    keys = ControlRedisKeys(RedisNamespace("test-monitor"))
    reader = RedisCommandResultReader(redis_client=fake_redis, keys=keys)

    submitted_key = keys.submitted_command("cmd-pending-1")
    sub_record = {
        "schema_version": "1.0",
        "command_id": "cmd-pending-1",
        "stream_id": "channel-01",
        "status": "ACCEPTED",
        "fingerprint": "a" * 64,
        "submitted_at": "2026-08-28T10:00:00+00:00",
    }
    fake_redis.storage[submitted_key] = json.dumps(sub_record)

    lookup = await reader.get_command_result("cmd-pending-1")
    assert lookup.state == CommandLookupState.PENDING
    assert lookup.submission is not None
    assert lookup.submission.command_id == "cmd-pending-1"
    assert lookup.submission.status == "ACCEPTED"


@pytest.mark.anyio
async def test_redis_command_result_reader_missing():
    fake_redis = FakeAsyncRedisReader()
    keys = ControlRedisKeys(RedisNamespace("test-monitor"))
    reader = RedisCommandResultReader(redis_client=fake_redis, keys=keys)

    lookup = await reader.get_command_result("cmd-non-existent")
    assert lookup.state == CommandLookupState.MISSING
    assert lookup.result is None
    assert lookup.submission is None


@pytest.mark.anyio
async def test_redis_command_result_reader_corrupt_marker():
    fake_redis = FakeAsyncRedisReader()
    keys = ControlRedisKeys(RedisNamespace("test-monitor"))
    reader = RedisCommandResultReader(redis_client=fake_redis, keys=keys)

    processed_key = keys.processed_command("cmd-corrupt-1")
    fake_redis.storage[processed_key] = "not-valid-json{{{"

    with pytest.raises(CorruptMarkerError):
        await reader.get_command_result("cmd-corrupt-1")


@pytest.mark.anyio
async def test_redis_command_result_reader_redis_unavailable():
    fake_redis = FakeAsyncRedisReader()
    fake_redis.fail = True
    keys = ControlRedisKeys(RedisNamespace("test-monitor"))
    reader = RedisCommandResultReader(redis_client=fake_redis, keys=keys)

    with pytest.raises(ReaderRedisUnavailableError):
        await reader.get_command_result("cmd-1")


@pytest.mark.anyio
@pytest.mark.parametrize("changed", ["false", 0, 1, None])
async def test_redis_command_result_reader_rejects_non_boolean_changed(changed):
    fake_redis = FakeAsyncRedisReader()
    keys = ControlRedisKeys(RedisNamespace("test-monitor"))
    reader = RedisCommandResultReader(redis_client=fake_redis, keys=keys)
    marker = {
        "fingerprint": "a" * 64,
        "result": {
            "schema_version": "1.0",
            "command_id": "cmd-invalid-bool",
            "command_type": "START",
            "stream_id": "channel-01",
            "status": "APPLIED",
            "changed": changed,
            "processed_at": "2026-08-28T10:05:00+00:00",
            "error_code": None,
            "error": None,
        },
    }
    fake_redis.storage[keys.processed_command("cmd-invalid-bool")] = json.dumps(marker)

    with pytest.raises(CorruptMarkerError):
        await reader.get_command_result("cmd-invalid-bool")


@pytest.mark.anyio
async def test_redis_command_result_reader_rejects_mismatched_command_id():
    fake_redis = FakeAsyncRedisReader()
    keys = ControlRedisKeys(RedisNamespace("test-monitor"))
    reader = RedisCommandResultReader(redis_client=fake_redis, keys=keys)
    marker = {
        "fingerprint": "a" * 64,
        "result": {
            "schema_version": "1.0",
            "command_id": "different-command",
            "command_type": "START",
            "stream_id": "channel-01",
            "status": "APPLIED",
            "changed": True,
            "processed_at": "2026-08-28T10:05:00+00:00",
            "error_code": None,
            "error": None,
        },
    }
    fake_redis.storage[keys.processed_command("expected-command")] = json.dumps(marker)

    with pytest.raises(CorruptMarkerError):
        await reader.get_command_result("expected-command")
