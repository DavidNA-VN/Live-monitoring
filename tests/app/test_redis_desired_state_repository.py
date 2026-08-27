from datetime import datetime, timezone
import json
import pytest

from app.desired_state_codec import desired_state_to_dict
from app.redis_desired_state_repository import RedisDesiredStateRepository
from core.desired_state_repository import (
    DesiredStateCorruptedError,
    DesiredStatePersistenceError,
    DesiredStateUnavailableError,
)
from core.redis_keys import DesiredStateRedisKeys, RedisNamespace
from models.desired_stream_state import (
    DesiredLifecycleState,
    DesiredStreamState,
)
from models.stream_config import StreamConfig


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.streams = {}
        self.should_fail = False

    def hset(self, key, field, value):
        if self.should_fail:
            import redis
            raise redis.RedisError("Redis write failed")
        if key not in self.hashes:
            self.hashes[key] = {}
        self.hashes[key][field] = value
        return 1

    def hget(self, key, field):
        if self.should_fail:
            import redis
            raise redis.RedisError("Redis read failed")
        return self.hashes.get(key, {}).get(field)

    def hgetall(self, key):
        if self.should_fail:
            import redis
            raise redis.RedisError("Redis read failed")
        return dict(self.hashes.get(key, {}))

    def hdel(self, key, *fields):
        if self.should_fail:
            import redis
            raise redis.RedisError("Redis delete failed")
        h = self.hashes.get(key, {})
        removed = 0
        for f in fields:
            if f in h:
                del h[f]
                removed += 1
        return removed

    def xadd(self, key, fields, maxlen=None, approximate=False):
        if self.should_fail:
            import redis
            raise redis.RedisError("Redis xadd failed")
        self.streams.setdefault(key, []).append(fields)
        return f"{len(self.streams[key])}-0"

    def pipeline(self, transaction=True):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.operations = []

    def xadd(self, *args, **kwargs):
        self.operations.append(("xadd", args, kwargs))
        return self

    def hdel(self, *args, **kwargs):
        self.operations.append(("hdel", args, kwargs))
        return self

    def execute(self):
        return [
            getattr(self.redis, name)(*args, **kwargs)
            for name, args, kwargs in self.operations
        ]


def sample_record(stream_id="channel-01"):
    return DesiredStreamState(
        stream_id=stream_id,
        desired_state=DesiredLifecycleState.RUNNING,
        updated_at=datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc),
        config=StreamConfig(
            stream_id=stream_id,
            master_url="https://example.test/stream.m3u8",
            black_screen_enabled=True,
            audio_loss_enabled=False,
            silence_threshold_dbfs=-60.0,
            audio_loss_duration=30.0,
            audio_track_index=0,
        ),
        last_command_id="cmd-1",
    )


def test_repository_save_get_and_delete():
    fake_redis = FakeRedis()
    keys = DesiredStateRedisKeys(RedisNamespace("test"))
    repo = RedisDesiredStateRepository(redis_client=fake_redis, keys=keys)

    record = sample_record("channel-01")
    repo.save(record)

    # Verify saved
    fetched = repo.get("channel-01")
    assert fetched is not None
    assert fetched.stream_id == "channel-01"
    assert fetched.desired_state == DesiredLifecycleState.RUNNING
    assert fetched.config is not None
    assert fetched.config.master_url == "https://example.test/stream.m3u8"

    # Non-existent
    assert repo.get("channel-non-existent") is None

    # Delete
    assert repo.delete("channel-01") is True
    assert repo.get("channel-01") is None
    assert repo.delete("channel-01") is False


def test_repository_list_all_and_quarantine():
    fake_redis = FakeRedis()
    keys = DesiredStateRedisKeys(RedisNamespace("test"))
    repo = RedisDesiredStateRepository(redis_client=fake_redis, keys=keys)

    valid_record = sample_record("channel-valid")
    repo.save(valid_record)

    # Inject a corrupted entry into the hash
    fake_redis.hashes[keys.current_states()]["channel-corrupt"] = "{invalid json content"

    records = repo.list_all()
    assert len(records) == 1
    assert records[0].stream_id == "channel-valid"

    # Verify corrupt record was sent to quarantine stream
    errors = fake_redis.streams.get(keys.recovery_errors(), [])
    assert len(errors) == 1
    assert errors[0]["stream_id"] == "channel-corrupt"
    assert errors[0]["payload"] == "{invalid json content"
    assert "error" in errors[0]
    assert "channel-corrupt" not in fake_redis.hashes[keys.current_states()]


def test_repository_quarantines_hash_field_payload_identity_mismatch():
    fake_redis = FakeRedis()
    keys = DesiredStateRedisKeys(RedisNamespace("test"))
    repo = RedisDesiredStateRepository(redis_client=fake_redis, keys=keys)
    payload = json.dumps(desired_state_to_dict(sample_record("payload-stream")))
    fake_redis.hashes[keys.current_states()] = {"hash-field-stream": payload}

    assert repo.list_all() == []
    assert "hash-field-stream" not in fake_redis.hashes[keys.current_states()]
    error = fake_redis.streams[keys.recovery_errors()][0]
    assert error["stream_id"] == "hash-field-stream"


def test_repository_persistence_error_on_redis_failure():
    fake_redis = FakeRedis()
    keys = DesiredStateRedisKeys(RedisNamespace("test"))
    repo = RedisDesiredStateRepository(redis_client=fake_redis, keys=keys)

    fake_redis.should_fail = True
    record = sample_record("channel-01")

    with pytest.raises(DesiredStatePersistenceError):
        repo.save(record)

    with pytest.raises(DesiredStateUnavailableError):
        repo.get("channel-01")

    with pytest.raises(DesiredStateUnavailableError):
        repo.list_all()
