from datetime import datetime, timezone
import json
import pytest

from app.redis_runtime_status_projector import (
    RedisRuntimeStatusProjector,
    RuntimeStatusProjectionError,
)
from core.redis_keys import PublicRuntimeRedisKeys, RedisNamespace
from models.runtime_status import (
    CheckStatus,
    PublicStreamStatus,
    RuntimeHealth,
    RuntimeStatus,
)
from models.runtime_status_update import RuntimeStatusUpdateType


class FakePipeline:
    def __init__(self, fake_redis):
        self.fake_redis = fake_redis
        self.ops = []

    def hset(self, key, field, value):
        self.ops.append(("hset", key, field, value))
        return self

    def hdel(self, key, field):
        self.ops.append(("hdel", key, field))
        return self

    def xadd(self, stream, fields, maxlen=None, approximate=False):
        self.ops.append(("xadd", stream, fields, maxlen))
        return self

    def execute(self):
        for op in self.ops:
            if op[0] == "hset":
                self.fake_redis.hset(op[1], op[2], op[3])
            elif op[0] == "hdel":
                self.fake_redis.hdel(op[1], op[2])
            elif op[0] == "xadd":
                self.fake_redis.xadd(op[1], op[2], maxlen=op[3])
        return []


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.streams = {}

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hset(self, key, field, value):
        if key not in self.hashes:
            self.hashes[key] = {}
        self.hashes[key][field] = value

    def hdel(self, key, field):
        if key in self.hashes and field in self.hashes[key]:
            del self.hashes[key][field]

    def xadd(self, stream, fields, maxlen=None, approximate=False):
        if stream not in self.streams:
            self.streams[stream] = []
        self.streams[stream].append(fields)
        if maxlen is not None and len(self.streams[stream]) > maxlen:
            self.streams[stream] = self.streams[stream][-maxlen:]

    def pipeline(self, transaction=True):
        return FakePipeline(self)


def make_status(
    stream_id="channel-01",
    worker_id="worker-local-01",
    status=PublicStreamStatus.RUNNING,
    health=RuntimeHealth.HEALTHY,
    observed_at=None,
    active_variant_count=2,
    queue_depth=0,
    last_poll_at=None,
):
    now = observed_at or datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    return RuntimeStatus(
        stream_id=stream_id,
        status=status,
        health=health,
        active_variant_count=active_variant_count,
        queue_depth=queue_depth,
        checks={"black_screen": CheckStatus.ENABLED},
        worker_id=worker_id,
        observed_at=now,
        last_poll_at=last_poll_at or now,
    )


def test_first_snapshot_publishes_to_hash_and_stream():
    redis_client = FakeRedis()
    keys = PublicRuntimeRedisKeys(RedisNamespace("test"))
    projector = RedisRuntimeStatusProjector(redis_client=redis_client, keys=keys)

    status = make_status()
    emitted = projector.project(status)

    assert emitted is True
    # Verify Hash entry
    raw_hash = redis_client.hget(keys.current_statuses(), "channel-01")
    assert raw_hash is not None
    hash_data = json.loads(raw_hash)
    assert hash_data["stream_id"] == "channel-01"
    assert hash_data["worker_id"] == "worker-local-01"
    assert hash_data["status"] == "RUNNING"
    assert "storage_id" not in hash_data

    # Verify Stream entry
    stream_entries = redis_client.streams.get(keys.status_updates(), [])
    assert len(stream_entries) == 1
    update_data = json.loads(stream_entries[0]["payload"])
    assert update_data["update_type"] == RuntimeStatusUpdateType.SNAPSHOT.value
    assert update_data["stream_id"] == "channel-01"
    assert update_data["worker_id"] == "worker-local-01"
    assert update_data["status"]["stream_id"] == "channel-01"


def test_only_observed_at_change_updates_hash_without_stream_spam():
    redis_client = FakeRedis()
    keys = PublicRuntimeRedisKeys(RedisNamespace("test"))
    projector = RedisRuntimeStatusProjector(redis_client=redis_client, keys=keys)

    t1 = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 27, 10, 0, 2, tzinfo=timezone.utc)

    status_t1 = make_status(observed_at=t1, last_poll_at=t1)
    status_t2 = make_status(observed_at=t2, last_poll_at=t1)  # same state, newer observed_at

    assert projector.project(status_t1) is True
    assert len(redis_client.streams.get(keys.status_updates(), [])) == 1

    # Second projection: only observed_at changed
    assert projector.project(status_t2) is False

    # Stream count must NOT increase
    assert len(redis_client.streams.get(keys.status_updates(), [])) == 1

    # Hash MUST contain the updated observed_at
    hash_data = json.loads(redis_client.hget(keys.current_statuses(), "channel-01"))
    assert hash_data["observed_at"] == "2026-08-27T10:00:02+00:00"


def test_semantic_state_change_emits_stream_update():
    redis_client = FakeRedis()
    keys = PublicRuntimeRedisKeys(RedisNamespace("test"))
    projector = RedisRuntimeStatusProjector(redis_client=redis_client, keys=keys)

    t1 = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 27, 10, 0, 2, tzinfo=timezone.utc)

    status_healthy = make_status(health=RuntimeHealth.HEALTHY, observed_at=t1)
    status_degraded = make_status(health=RuntimeHealth.DEGRADED, observed_at=t2)

    assert projector.project(status_healthy) is True
    assert projector.project(status_degraded) is True

    stream_entries = redis_client.streams.get(keys.status_updates(), [])
    assert len(stream_entries) == 2
    update_2 = json.loads(stream_entries[1]["payload"])
    assert update_2["status"]["health"] == "DEGRADED"


def test_remove_matching_worker_emits_removed_and_deletes_hash():
    redis_client = FakeRedis()
    keys = PublicRuntimeRedisKeys(RedisNamespace("test"))
    projector = RedisRuntimeStatusProjector(redis_client=redis_client, keys=keys)

    status = make_status(worker_id="worker-local-01")
    projector.project(status)

    now = datetime(2026, 8, 27, 10, 10, 0, tzinfo=timezone.utc)
    removed = projector.remove(
        stream_id="channel-01",
        worker_id="worker-local-01",
        observed_at=now,
    )

    assert removed is True
    # Hash entry deleted
    assert redis_client.hget(keys.current_statuses(), "channel-01") is None

    # Stream contains REMOVED event
    stream_entries = redis_client.streams.get(keys.status_updates(), [])
    assert len(stream_entries) == 2
    rem_event = json.loads(stream_entries[1]["payload"])
    assert rem_event["update_type"] == RuntimeStatusUpdateType.REMOVED.value
    assert rem_event["stream_id"] == "channel-01"
    assert rem_event["worker_id"] == "worker-local-01"
    assert "status" not in rem_event


def test_remove_mismatched_worker_is_rejected():
    redis_client = FakeRedis()
    keys = PublicRuntimeRedisKeys(RedisNamespace("test"))
    projector = RedisRuntimeStatusProjector(redis_client=redis_client, keys=keys)

    status = make_status(worker_id="worker-owner-01")
    projector.project(status)

    now = datetime(2026, 8, 27, 10, 10, 0, tzinfo=timezone.utc)
    # Different worker attempts removal
    removed = projector.remove(
        stream_id="channel-01",
        worker_id="worker-other-02",
        observed_at=now,
    )

    assert removed is False
    # Hash entry NOT deleted
    assert redis_client.hget(keys.current_statuses(), "channel-01") is not None
    # No extra stream event emitted
    assert len(redis_client.streams.get(keys.status_updates(), [])) == 1


def test_remove_rejects_snapshot_without_worker_identity():
    redis_client = FakeRedis()
    keys = PublicRuntimeRedisKeys(RedisNamespace("test"))
    projector = RedisRuntimeStatusProjector(redis_client=redis_client, keys=keys)
    redis_client.hset(
        keys.current_statuses(),
        "channel-01",
        json.dumps({"stream_id": "channel-01", "status": "RUNNING"}),
    )

    removed = projector.remove(
        stream_id="channel-01",
        worker_id="worker-local-01",
        observed_at=datetime.now(timezone.utc),
    )

    assert removed is False
    assert redis_client.hget(keys.current_statuses(), "channel-01") is not None


def test_remove_missing_stream_returns_false():
    redis_client = FakeRedis()
    keys = PublicRuntimeRedisKeys(RedisNamespace("test"))
    projector = RedisRuntimeStatusProjector(redis_client=redis_client, keys=keys)

    now = datetime(2026, 8, 27, 10, 10, 0, tzinfo=timezone.utc)
    assert projector.remove(
        stream_id="unknown-channel",
        worker_id="worker-local-01",
        observed_at=now,
    ) is False


def test_projector_requires_worker_id_and_observed_at():
    redis_client = FakeRedis()
    projector = RedisRuntimeStatusProjector(redis_client=redis_client)

    # Missing worker_id
    status_no_worker = RuntimeStatus(
        stream_id="channel-01",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=1,
        queue_depth=0,
        checks={},
        observed_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ValueError, match="worker_id"):
        projector.project(status_no_worker)


def test_projector_translates_redis_errors():
    class FailingRedis:
        def hget(self, *args, **kwargs):
            import redis
            raise redis.RedisError("Connection lost")

    projector = RedisRuntimeStatusProjector(redis_client=FailingRedis())
    status = make_status()
    with pytest.raises(RuntimeStatusProjectionError):
        projector.project(status)
