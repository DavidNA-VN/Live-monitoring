from datetime import datetime, timezone
import json
import pytest

from app.redis_worker_heartbeat_publisher import RedisWorkerHeartbeatPublisher
from core.redis_keys import RedisNamespace, WorkerRedisKeys
from core.worker_heartbeat import WorkerHeartbeatPublishError
from models.worker_heartbeat import WorkerHeartbeat, WorkerState


class FakePipeline:
    def __init__(self, fake_redis):
        self.fake_redis = fake_redis
        self.ops = []

    def set(self, key, value, ex=None):
        self.ops.append(("set", key, value, ex))
        return self

    def zadd(self, key, mapping):
        self.ops.append(("zadd", key, mapping))
        return self

    def zremrangebyscore(self, key, min_score, max_score):
        self.ops.append(("zremrangebyscore", key, min_score, max_score))
        return self

    def execute(self):
        for op in self.ops:
            if op[0] == "set":
                self.fake_redis.strings[op[1]] = (op[2], op[3])
            elif op[0] == "zadd":
                if op[1] not in self.fake_redis.zsets:
                    self.fake_redis.zsets[op[1]] = {}
                self.fake_redis.zsets[op[1]].update(op[2])
            elif op[0] == "zremrangebyscore":
                zset = self.fake_redis.zsets.get(op[1], {})
                cutoff = float(op[3])
                to_del = [m for m, s in zset.items() if float(s) < cutoff]
                for m in to_del:
                    del zset[m]
        return []


class FakeRedis:
    def __init__(self):
        self.strings = {}
        self.zsets = {}
        self.server_time = (1_700_000_000, 500_000)

    def time(self):
        return self.server_time

    def pipeline(self, transaction=True):
        return FakePipeline(self)


def test_publisher_sets_heartbeat_and_zadd_in_transaction():
    fake_redis = FakeRedis()
    keys = WorkerRedisKeys(RedisNamespace("test"))
    publisher = RedisWorkerHeartbeatPublisher(redis_client=fake_redis, keys=keys)

    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    hb = WorkerHeartbeat(
        worker_id="worker-local-01",
        state=WorkerState.READY,
        started_at=now,
        last_seen_at=now,
        command_consumer_ready=True,
        active_stream_count=1,
        max_streams=4,
        active_media_processes=2,
        max_media_processes=4,
        version="v1.0.0",
    )

    publisher.publish(hb, ttl_seconds=15, discovery_window_seconds=30)

    # Verify Heartbeat String
    hb_key = keys.heartbeat("worker-local-01")
    assert hb_key in fake_redis.strings
    raw_payload, ttl = fake_redis.strings[hb_key]
    assert ttl == 15
    data = json.loads(raw_payload)
    assert data["worker_id"] == "worker-local-01"
    assert data["state"] == "READY"

    # Verify Discovery ZSET
    zset_key = keys.active_workers()
    assert zset_key in fake_redis.zsets
    assert "worker-local-01" in fake_redis.zsets[zset_key]
    assert fake_redis.zsets[zset_key]["worker-local-01"] == 1_700_000_000.5


def test_publisher_cleans_up_stale_discovery_members():
    fake_redis = FakeRedis()
    fake_redis.server_time = (1100, 0)
    keys = WorkerRedisKeys(RedisNamespace("test"))
    publisher = RedisWorkerHeartbeatPublisher(redis_client=fake_redis, keys=keys)

    zset_key = keys.active_workers()
    # Pre-populate with stale worker from 100 seconds ago
    fake_redis.zsets[zset_key] = {"worker-stale": 1000.0}

    now = datetime.fromtimestamp(1100.0, tz=timezone.utc)
    hb = WorkerHeartbeat(
        worker_id="worker-fresh",
        state=WorkerState.READY,
        started_at=now,
        last_seen_at=now,
        command_consumer_ready=True,
        active_stream_count=0,
        max_streams=4,
        active_media_processes=0,
        max_media_processes=4,
        version="1.0",
    )

    # 30s window cutoff: 1100 - 30 = 1070 -> worker-stale (score 1000) should be removed
    publisher.publish(hb, ttl_seconds=15, discovery_window_seconds=30)

    assert "worker-stale" not in fake_redis.zsets[zset_key]
    assert "worker-fresh" in fake_redis.zsets[zset_key]


def test_publisher_normalizes_redis_error():
    class FailingRedis:
        def time(self):
            import redis
            raise redis.RedisError("Redis down")

        def pipeline(self, transaction=True):
            raise AssertionError("pipeline must not be reached")

    publisher = RedisWorkerHeartbeatPublisher(redis_client=FailingRedis())
    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    hb = WorkerHeartbeat(
        worker_id="worker-01",
        state=WorkerState.READY,
        started_at=now,
        last_seen_at=now,
        command_consumer_ready=True,
        active_stream_count=0,
        max_streams=1,
        active_media_processes=0,
        max_media_processes=1,
        version="1.0",
    )

    with pytest.raises(WorkerHeartbeatPublishError):
        publisher.publish(hb)


def test_publisher_rejects_discovery_window_shorter_than_ttl():
    publisher = RedisWorkerHeartbeatPublisher(redis_client=FakeRedis())
    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    heartbeat = WorkerHeartbeat(
        worker_id="worker-01",
        state=WorkerState.READY,
        started_at=now,
        last_seen_at=now,
        command_consumer_ready=True,
        active_stream_count=0,
        max_streams=1,
        active_media_processes=0,
        max_media_processes=1,
        version="1.0",
    )

    with pytest.raises(ValueError, match="discovery_window_seconds"):
        publisher.publish(
            heartbeat,
            ttl_seconds=15,
            discovery_window_seconds=10,
        )
