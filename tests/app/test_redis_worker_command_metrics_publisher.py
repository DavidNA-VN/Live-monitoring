from datetime import datetime, timezone
import pytest
import redis

from app.redis_worker_command_metrics_publisher import RedisWorkerCommandMetricsPublisher
from core.redis_keys import RedisNamespace, WorkerRedisKeys
from models.command_metrics import CommandMetricsSnapshot


class FakePipeline:
    def __init__(self, fake_redis):
        self.fake_redis = fake_redis
        self.ops = []

    def hset(self, key, mapping):
        self.ops.append(("hset", key, mapping))
        return self

    def expire(self, key, seconds):
        self.ops.append(("expire", key, seconds))
        return self

    def execute(self):
        if self.fake_redis.raise_on_execute:
            raise redis.ConnectionError("Redis connection lost")
        for op in self.ops:
            if op[0] == "hset":
                self.fake_redis.hashes[op[1]] = dict(op[2])
            elif op[0] == "expire":
                self.fake_redis.ttls[op[1]] = op[2]
        return []


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.ttls = {}
        self.raise_on_execute = False

    def pipeline(self, transaction=True):
        return FakePipeline(self)


def make_snapshot() -> CommandMetricsSnapshot:
    return CommandMetricsSnapshot(
        worker_id="worker-pub-test",
        observed_at=datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc),
        command_delivery_received_total=10,
        command_applied_total=8,
        command_noop_total=1,
        command_rejected_total=1,
        command_failed_total=0,
        command_reclaimed_total=0,
        command_duplicate_replay_total=2,
        command_dead_letter_total=0,
        command_oversized_total=0,
        command_stale_total=0,
        command_poll_failure_total=0,
        command_processing_duration_ms_total=120.5,
        command_pending_count=0,
        command_deferred_count=0,
        command_processing_duration_ms_max=25.0,
        last_command_processing_duration_ms=10.0,
    )


def test_publisher_writes_hash_and_sets_ttl():
    fake = FakeRedis()
    keys = WorkerRedisKeys(RedisNamespace("test"))
    publisher = RedisWorkerCommandMetricsPublisher(
        redis_client=fake,
        keys=keys,
        ttl_seconds=300,
    )

    snap = make_snapshot()
    ok = publisher.publish(snap)

    assert ok is True
    metric_key = "test:worker:worker-pub-test:command-metrics"
    assert metric_key in fake.hashes
    assert fake.hashes[metric_key]["worker_id"] == "worker-pub-test"
    assert fake.hashes[metric_key]["command_applied_total"] == "8"
    assert fake.ttls[metric_key] == 300


def test_publisher_handles_redis_error_gracefully():
    fake = FakeRedis()
    fake.raise_on_execute = True
    keys = WorkerRedisKeys(RedisNamespace("test"))
    publisher = RedisWorkerCommandMetricsPublisher(
        redis_client=fake,
        keys=keys,
    )

    snap = make_snapshot()
    ok = publisher.publish(snap)
    assert ok is False


def test_publisher_rejects_non_positive_ttl():
    with pytest.raises(ValueError, match="ttl_seconds must be > 0"):
        RedisWorkerCommandMetricsPublisher(
            redis_client=FakeRedis(),
            keys=WorkerRedisKeys(RedisNamespace("test")),
            ttl_seconds=0,
        )
