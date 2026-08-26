from datetime import datetime, timezone

from core.runtime_health import (
    RedisRuntimeHealthReporter,
    runtime_health_event_id,
)
from models.runtime import LiveCycleStats


class FakePipeline:
    def __init__(self):
        self.mapping = None
        self.mapping_key = None
        self.expiry = None
        self.increments = {}

    def hset(self, key, *, mapping):
        self.mapping_key = key
        self.mapping = mapping
        return self

    def expire(self, _key, seconds):
        self.expiry = seconds
        return self

    def hincrby(self, _key, name, value):
        self.increments[name] = value
        return self

    def hincrbyfloat(self, _key, name, value):
        self.increments[name] = value
        return self

    def execute(self):
        return []


class FakeRedis:
    def __init__(self):
        self.pipeline_instance = FakePipeline()
        self.eval_calls = []

    def eval(self, *args):
        self.eval_calls.append(args)
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
        storage_id="storage-1",
        external_stream_id="channel-01",
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
        audio_analysis_total=7,
        audio_analysis_failure_total=2,
        audio_analysis_timeout_total=1,
        audio_track_missing_total=3,
        audio_silence_seconds_total=12.5,
    )

    reporter.publish(stats)

    assert (
        client.client.pipeline_instance.mapping_key
        == reporter.runtime_keys.metrics("storage-1")
    )
    mapping = client.client.pipeline_instance.mapping
    assert mapping["queue_depth"] == 7
    assert mapping["queue_lag_seconds"] == "2.500000"
    assert mapping["backpressure_deferred_work"] == 3
    assert mapping["dropped_capacity_work"] == 1
    increments = client.client.pipeline_instance.increments
    assert increments["audio_analysis_total"] == 7
    assert increments["audio_analysis_failure_total"] == 2
    assert increments["audio_analysis_timeout_total"] == 1
    assert increments["audio_track_missing_total"] == 3
    assert increments["audio_silence_seconds_total"] == 12.5
    assert client.client.pipeline_instance.expiry == 45

    # Check EVAL args for health and outbox
    eval_call = client.client.eval_calls[0]
    # eval_call: (SCRIPT, numkeys, key1(health), key2(outbox), key3(metrics), payload, ttl, state, stream_id, reasons, schema_version, alert_id, event_id, occurred_at, max_len)
    assert eval_call[2] == reporter.runtime_keys.health("storage-1")
    assert eval_call[4] == reporter.runtime_keys.metrics("storage-1")
    assert eval_call[8] == "channel-01"  # ARGV[4] stream_id
    assert eval_call[12] == runtime_health_event_id("storage-1")
    assert "storage-1" not in eval_call[12]
