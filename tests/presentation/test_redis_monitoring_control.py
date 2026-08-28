from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
import pytest
import redis.exceptions

from core.redis_keys import ControlRedisKeys, RedisNamespace
from presentation.api.adapters.redis_monitoring_control import (
    ControlRedisUnavailableError,
    IdempotencyConflictError,
    RedisMonitoringControl,
)
from presentation.api.models import (
    AudioLossCheck,
    BlackScreenCheck,
    StreamChecks,
    StreamConfigDTO,
)


class FakeAsyncRedis:
    """Mock async Redis client mô phỏng hoạt động của Lua script và Redis storage."""

    def __init__(self) -> None:
        self.storage: Dict[str, str] = {}
        self.streams: Dict[str, List[Dict[str, str]]] = {}
        self._scripts: Dict[str, Any] = {}
        self.fail_on_call: bool = False

    def register_script(self, script_code: str) -> Any:
        async def _eval(*, keys: List[str], args: List[str]):
            if self.fail_on_call:
                raise redis.exceptions.ConnectionError("Redis connection lost")

            idempotency_key = keys[0]
            submitted_key = keys[1]
            stream_key = keys[2]

            fingerprint = args[0]
            submission_json = args[1]
            command_json = args[2]
            ttl_seconds = int(args[3])
            has_idempotency = args[4]

            if has_idempotency == "1":
                existing = self.storage.get(idempotency_key)
                if existing:
                    receipt = json.loads(existing)
                    if receipt.get("fingerprint") == fingerprint:
                        return [1, existing]
                    else:
                        return [2, "IDEMPOTENCY_CONFLICT"]
                self.storage[idempotency_key] = submission_json

            self.storage[submitted_key] = submission_json
            if stream_key not in self.streams:
                self.streams[stream_key] = []
            self.streams[stream_key].append({"payload": command_json})

            return [0, submission_json]

        return _eval


@pytest.fixture
def sample_config() -> StreamConfigDTO:
    return StreamConfigDTO(
        stream_id="channel-01",
        master_url="https://example.com/master.m3u8",
        checks=StreamChecks(
            black_screen=BlackScreenCheck(enabled=True),
            audio_loss=AudioLossCheck(
                enabled=True,
                threshold_dbfs=-30.0,
                duration_seconds=5.0,
                track_index=0,
            ),
        ),
    )


@pytest.mark.anyio
async def test_redis_monitoring_control_start_stream_payload(sample_config):
    fake_redis = FakeAsyncRedis()
    keys = ControlRedisKeys(RedisNamespace("test-monitor"))
    now = datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)

    control = RedisMonitoringControl(
        redis_client=fake_redis,
        keys=keys,
        utc_now=lambda: now,
        command_id_factory=lambda: "cmd-fixed-uuid-1",
    )

    submission = await control.start_stream(sample_config)
    assert submission.schema_version == "1.0"
    assert submission.command_id == "cmd-fixed-uuid-1"
    assert submission.stream_id == "channel-01"
    assert submission.status == "ACCEPTED"

    # Check submitted marker in storage
    submitted_key = keys.submitted_command("cmd-fixed-uuid-1")
    assert submitted_key in fake_redis.storage
    sub_data = json.loads(fake_redis.storage[submitted_key])
    assert sub_data["command_id"] == "cmd-fixed-uuid-1"
    assert sub_data["status"] == "ACCEPTED"

    # Check entry in Redis Stream
    stream_key = keys.commands()
    assert stream_key in fake_redis.streams
    assert len(fake_redis.streams[stream_key]) == 1

    entry = fake_redis.streams[stream_key][0]
    assert set(entry.keys()) == {"payload"}  # Only "payload" field allowed

    cmd_payload = json.loads(entry["payload"])
    assert cmd_payload["schema_version"] == "1.0"
    assert cmd_payload["command_id"] == "cmd-fixed-uuid-1"
    assert cmd_payload["command_type"] == "START"
    assert cmd_payload["stream_id"] == "channel-01"
    assert cmd_payload["requested_at"] == "2026-08-28T10:00:00+00:00"
    assert cmd_payload["config"]["stream_id"] == "channel-01"
    assert "storage_id" not in cmd_payload
    assert "storage_id" not in cmd_payload["config"]


@pytest.mark.anyio
async def test_redis_monitoring_control_lifecycle_commands_omit_config():
    fake_redis = FakeAsyncRedis()
    keys = ControlRedisKeys(RedisNamespace("test-monitor"))
    control = RedisMonitoringControl(
        redis_client=fake_redis,
        keys=keys,
        command_id_factory=lambda: "cmd-pause-1",
    )

    await control.pause_stream("channel-01")
    stream_key = keys.commands()
    assert len(fake_redis.streams[stream_key]) == 1

    entry = json.loads(fake_redis.streams[stream_key][0]["payload"])
    assert entry["command_type"] == "PAUSE"
    assert entry["stream_id"] == "channel-01"
    assert "config" not in entry


@pytest.mark.anyio
async def test_redis_monitoring_control_idempotency_replay(sample_config):
    fake_redis = FakeAsyncRedis()
    keys = ControlRedisKeys(RedisNamespace("test-monitor"))

    counter = 0

    def generate_id():
        nonlocal counter
        counter += 1
        return f"cmd-uuid-{counter}"

    control = RedisMonitoringControl(
        redis_client=fake_redis,
        keys=keys,
        command_id_factory=generate_id,
    )

    # 1. Gửi lần đầu kèm Idempotency-Key
    sub1 = await control.start_stream(sample_config, idempotency_key="idemp-key-100")
    assert sub1.command_id == "cmd-uuid-1"
    assert len(fake_redis.streams[keys.commands()]) == 1

    # 2. Retry cùng Idempotency-Key và cùng payload
    sub2 = await control.start_stream(sample_config, idempotency_key="idemp-key-100")
    assert sub2.command_id == "cmd-uuid-1"  # Trả về cùng command_id cũ
    assert len(fake_redis.streams[keys.commands()]) == 1  # Không XADD thêm entry thứ 2


@pytest.mark.anyio
async def test_redis_monitoring_control_idempotency_conflict(sample_config):
    fake_redis = FakeAsyncRedis()
    keys = ControlRedisKeys(RedisNamespace("test-monitor"))
    control = RedisMonitoringControl(redis_client=fake_redis, keys=keys)

    # 1. Gửi START với key "shared-key"
    await control.start_stream(sample_config, idempotency_key="shared-key")

    # 2. Gửi lệnh PAUSE với cùng key "shared-key" nhưng khác payload -> phải conflict
    with pytest.raises(IdempotencyConflictError):
        await control.pause_stream("channel-01", idempotency_key="shared-key")


@pytest.mark.anyio
async def test_redis_monitoring_control_redis_unavailable(sample_config):
    fake_redis = FakeAsyncRedis()
    fake_redis.fail_on_call = True
    control = RedisMonitoringControl(redis_client=fake_redis)

    with pytest.raises(ControlRedisUnavailableError):
        await control.start_stream(sample_config)


def test_redis_monitoring_control_rejects_invalid_ttl():
    with pytest.raises(ValueError, match="submission_ttl_seconds"):
        RedisMonitoringControl(
            redis_client=FakeAsyncRedis(),
            submission_ttl_seconds=0,
        )
