from datetime import datetime, timezone

from core.runtime_health import RedisRuntimeHealthReporter
from models.runtime import LiveCycleStats


class FakePipeline:
    def __init__(self):
        self.mapping = None
        self.expiry = None

    def hset(self, _key, *, mapping):
        self.mapping = mapping
        return self

    def expire(self, _key, seconds):
        self.expiry = seconds
        return self

    def execute(self):
        return []


class FakeRedis:
    def __init__(self):
        self.pipeline_instance = FakePipeline()

    def eval(self, *_args):
        return 0

    def pipeline(self, *, transaction):
        assert transaction is True
        return self.pipeline_instance


class FakeRedisClient:
    def __init__(self):
        self.client = FakeRedis()


def test_publish_exposes_queue_and_drop_metrics_separately_from_health():
    client = FakeRedisClient()
    reporter = RedisRuntimeHealthReporter(
        stream_id="stream-1",
        redis_client=client,
        health_ttl_seconds=45,
    )
    stats = LiveCycleStats(
        started_at=datetime.now(timezone.utc),
        successful_snapshots=1,
        queue_depth=7,
        queue_lag_seconds=2.5,
        backpressure_deferred_work_count=3,
        dropped_work_count=1,
        dropped_capacity_work_count=1,
    )

    reporter.publish(stats)

    mapping = client.client.pipeline_instance.mapping
    assert mapping["queue_depth"] == 7
    assert mapping["queue_lag_seconds"] == "2.500000"
    assert mapping["backpressure_deferred_work"] == 3
    assert mapping["dropped_capacity_work"] == 1
    assert client.client.pipeline_instance.expiry == 45
