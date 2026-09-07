from datetime import datetime, timezone
import json

import pytest

from app.supervisor_runtime_status import SupervisorRuntimeStatusReader
from core.redis_keys import RuntimeRedisKeys
from core.runtime_status_reader import RuntimeStatusReader
from core.stream_session import (
    StreamSessionSnapshot,
    StreamSessionStatus,
)
from core.stream_supervisor import StreamSupervisor
from models.analysis import AnalysisResourceClass, ResourcePoolLimit
from models.runtime_status import (
    CheckStatus,
    PublicStreamStatus,
    RuntimeHealth,
)
from models.stream_config import StreamConfig


def make_config(
    name: str,
    *,
    enabled: bool = True,
    black_screen: bool = True,
    audio_loss: bool = True,
) -> StreamConfig:
    return StreamConfig(
        master_url=f"https://test/{name}/master.m3u8",
        stream_id=name,
        enabled=enabled,
        black_screen_enabled=black_screen,
        audio_loss_enabled=audio_loss,
        resource_limits={
            AnalysisResourceClass.VIDEO_DECODE: ResourcePoolLimit(1, 0)
        },
    )


class StubSession:
    def __init__(self, config: StreamConfig):
        self.config = config
        self.stream_id = config.identity.external_stream_id
        self.status = StreamSessionStatus.CREATED
        self.started_at = None

    def start(self):
        self.status = StreamSessionStatus.RUNNING
        self.started_at = datetime(2026, 8, 26, 10, 0, 0, tzinfo=timezone.utc)

    def stop(self, *, timeout=None, paused=False):
        self.status = (
            StreamSessionStatus.PAUSED if paused else StreamSessionStatus.STOPPED
        )
        return True

    def snapshot(self):
        return StreamSessionSnapshot(
            stream_id=self.stream_id,
            status=self.status,
            started_at=self.started_at,
        )


class StubFactory:
    def __init__(self):
        self.created = []

    def create(self, config: StreamConfig):
        session = StubSession(config)
        self.created.append(session)
        return session


class FakePipeline:
    def __init__(self, fake_redis):
        self.fake_redis = fake_redis
        self.calls = []

    def get(self, key):
        self.calls.append(("get", key))
        return self

    def hgetall(self, key):
        self.calls.append(("hgetall", key))
        return self

    def hlen(self, key):
        self.calls.append(("hlen", key))
        return self

    def execute(self):
        results = []
        for cmd, key in self.calls:
            if cmd == "get":
                results.append(self.fake_redis.storage.get(key))
            elif cmd == "hgetall":
                results.append(self.fake_redis.hashes.get(key, {}))
            elif cmd == "hlen":
                results.append(len(self.fake_redis.hashes.get(key, {})))
        return results


class FakeRedis:
    def __init__(self):
        self.storage = {}
        self.hashes = {}
        self.pipelines_created = []

    def pipeline(self, transaction=False):
        pipe = FakePipeline(self)
        self.pipelines_created.append(pipe)
        return pipe


class FailingRedis:
    def pipeline(self, transaction=False):
        raise ConnectionError("Redis connection refused")


def test_unknown_external_id_returns_none():
    supervisor = StreamSupervisor(session_factory=StubFactory())
    redis = FakeRedis()
    reader = SupervisorRuntimeStatusReader(supervisor=supervisor, redis_client=redis)

    assert reader.get("unknown-stream") is None


def test_running_and_healthy_telemetry_mapped_correctly():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    redis = FakeRedis()
    reader = SupervisorRuntimeStatusReader(supervisor=supervisor, redis_client=redis)

    cfg = make_config("channel-01", black_screen=True, audio_loss=False)
    supervisor.add(cfg, start=True)

    storage_id = cfg.identity.storage_id
    keys = RuntimeRedisKeys()

    # Seed Redis telemetry with storage_id
    health_payload = json.dumps({"state": "HEALTHY", "reasons": ["playlist_ok", "segments_decoding"]})
    redis.storage[keys.health(storage_id)] = health_payload
    redis.hashes[keys.metrics(storage_id)] = {
        "finished_at": "2026-08-26T10:05:00+00:00",
        "queue_depth": "4",
        "queue_lag_seconds": "1.500000",
        "live_edge_lag_seconds": "3.250000",
        "admission_mode": "live_edge_protection",
        "startup_segments_outside_scope_total": "2",
        "dropped_expired_work_total": "1",
        "dropped_capacity_work_total": "3",
        "dropped_live_edge_work_total": "4",
        "coverage_gap_total": "2",
        "coverage_gap_segment_total": "5",
        "dropped_media_segments_total": "3",
        "video_analysis_total": "12",
        "audio_analysis_total": "8",
        "active_media_processes": "2",
        "max_media_processes": "4",
    }
    redis.hashes[keys.active_variants(storage_id)] = {"v720": "...", "v1080": "..."}

    status = reader.get("channel-01")

    assert status is not None
    assert status.stream_id == "channel-01"
    assert not hasattr(status, "storage_id")
    assert status.status is PublicStreamStatus.RUNNING
    assert status.health is RuntimeHealth.HEALTHY
    assert status.health_reasons == ("playlist_ok", "segments_decoding")
    assert status.active_variant_count == 2
    assert status.queue_depth == 4
    assert status.queue_lag_seconds == 1.5
    assert status.live_edge_lag_seconds == 3.25
    assert status.started_at == datetime(2026, 8, 26, 10, 0, 0, tzinfo=timezone.utc)
    assert status.last_poll_at == datetime(2026, 8, 26, 10, 5, 0, tzinfo=timezone.utc)
    assert status.telemetry_available is True
    assert status.admission_mode == "live_edge_protection"
    assert status.startup_segments_outside_scope == 2
    assert status.dropped_expired_work == 1
    assert status.dropped_capacity_work == 3
    assert status.dropped_live_edge_work == 4
    assert status.coverage_gap_count == 2
    assert status.coverage_gap_segment_count == 5
    assert status.dropped_media_segment_count == 3
    assert status.profile_analysis_total == {
        "video_realtime": 12,
        "audio_realtime": 8,
    }
    assert status.active_media_processes == 2
    assert status.max_media_processes == 4
    assert status.checks == {
        "black_screen": CheckStatus.ENABLED,
        "video_freeze": CheckStatus.DISABLED,
        "audio_loss": CheckStatus.DISABLED,
    }

    # Verify Redis keys used storage_id and were queried in a single pipeline
    assert len(redis.pipelines_created) == 1
    queried_keys = [call[1] for call in redis.pipelines_created[0].calls]
    assert queried_keys == [
        keys.health(storage_id),
        keys.metrics(storage_id),
        keys.active_variants(storage_id),
    ]


def test_running_and_degraded_telemetry():
    supervisor = StreamSupervisor(session_factory=StubFactory())
    redis = FakeRedis()
    reader = SupervisorRuntimeStatusReader(supervisor=supervisor, redis_client=redis)

    cfg = make_config("channel-01")
    supervisor.add(cfg, start=True)
    storage_id = cfg.identity.storage_id
    keys = RuntimeRedisKeys()

    redis.storage[keys.health(storage_id)] = json.dumps({
        "state": "DEGRADED",
        "reasons": ["master_playlist_timeout"],
    })

    status = reader.get("channel-01")
    assert status.health is RuntimeHealth.DEGRADED
    assert status.health_reasons == ("master_playlist_timeout",)


def test_failed_session_is_always_unhealthy_and_ignores_stale_telemetry():
    class FailingFactory:
        def create(self, _config):
            raise RuntimeError("FFmpeg process crashed")

    supervisor = StreamSupervisor(session_factory=FailingFactory())
    redis = FakeRedis()
    reader = SupervisorRuntimeStatusReader(supervisor=supervisor, redis_client=redis)

    cfg = make_config("channel-01")
    with pytest.raises(RuntimeError):
        supervisor.add(cfg, start=True)

    # Even if stale redis has HEALTHY
    keys = RuntimeRedisKeys()
    redis.storage[keys.health(cfg.identity.storage_id)] = json.dumps({"state": "HEALTHY"})

    status = reader.get("channel-01")
    assert status is not None
    assert status.status is PublicStreamStatus.FAILED
    assert status.health is RuntimeHealth.UNHEALTHY
    assert status.error == "FFmpeg process crashed"
    assert status.active_variant_count == 0
    assert status.queue_depth == 0
    assert status.last_poll_at is None


def test_paused_session_ignores_stale_redis_health():
    supervisor = StreamSupervisor(session_factory=StubFactory())
    redis = FakeRedis()
    reader = SupervisorRuntimeStatusReader(supervisor=supervisor, redis_client=redis)

    cfg = make_config("channel-01")
    supervisor.add(cfg, start=True)
    supervisor.pause("channel-01")

    # Stale redis contains HEALTHY
    keys = RuntimeRedisKeys()
    redis.storage[keys.health(cfg.identity.storage_id)] = json.dumps({"state": "HEALTHY"})

    status = reader.get("channel-01")
    assert status.status is PublicStreamStatus.PAUSED
    assert status.health is RuntimeHealth.UNKNOWN
    assert status.active_variant_count == 0
    assert status.queue_depth == 0
    assert status.telemetry_available is False


def test_redis_unavailable_returns_lifecycle_with_telemetry_unavailable():
    supervisor = StreamSupervisor(session_factory=StubFactory())
    failing_redis = FailingRedis()
    reader = SupervisorRuntimeStatusReader(
        supervisor=supervisor, redis_client=failing_redis
    )

    cfg = make_config("channel-01")
    supervisor.add(cfg, start=True)

    status = reader.get("channel-01")
    assert status is not None
    assert status.stream_id == "channel-01"
    assert status.status is PublicStreamStatus.RUNNING
    assert status.health is RuntimeHealth.UNKNOWN
    assert status.telemetry_available is False
    assert status.active_variant_count == 0
    assert status.queue_depth == 0


def test_malformed_health_json_does_not_crash():
    supervisor = StreamSupervisor(session_factory=StubFactory())
    redis = FakeRedis()
    reader = SupervisorRuntimeStatusReader(supervisor=supervisor, redis_client=redis)

    cfg = make_config("channel-01")
    supervisor.add(cfg, start=True)

    keys = RuntimeRedisKeys()
    redis.storage[keys.health(cfg.identity.storage_id)] = "{invalid json::"

    status = reader.get("channel-01")
    assert status is not None
    assert status.health is RuntimeHealth.UNKNOWN
    assert status.health_reasons == ()


def test_invalid_metric_values_use_safe_defaults():
    supervisor = StreamSupervisor(session_factory=StubFactory())
    redis = FakeRedis()
    reader = SupervisorRuntimeStatusReader(supervisor=supervisor, redis_client=redis)

    cfg = make_config("channel-01")
    supervisor.add(cfg, start=True)

    keys = RuntimeRedisKeys()
    redis.hashes[keys.metrics(cfg.identity.storage_id)] = {
        "queue_depth": "not-an-int",
        "queue_lag_seconds": "-5.0",
        "finished_at": "invalid-datetime",
    }

    status = reader.get("channel-01")
    assert status is not None
    assert status.queue_depth == 0
    assert status.queue_lag_seconds is None
    assert status.last_poll_at is None


def test_non_finite_queue_lag_uses_safe_default():
    supervisor = StreamSupervisor(session_factory=StubFactory())
    redis = FakeRedis()
    reader = SupervisorRuntimeStatusReader(supervisor=supervisor, redis_client=redis)
    cfg = make_config("channel-01")
    supervisor.add(cfg, start=True)
    keys = RuntimeRedisKeys()
    redis.hashes[keys.metrics(cfg.identity.storage_id)] = {
        "queue_lag_seconds": "Infinity",
    }

    status = reader.get("channel-01")

    assert status is not None
    assert status.queue_lag_seconds is None
