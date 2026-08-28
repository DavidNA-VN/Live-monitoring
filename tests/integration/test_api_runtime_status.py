from datetime import datetime, timezone
import json
import os
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
import redis.asyncio as aioredis

from app.monitoring_command_handler import MonitoringCommandHandler
from app.redis_monitoring_command_consumer import RedisMonitoringCommandConsumer
from app.redis_runtime_status_projector import RedisRuntimeStatusProjector
from core.monitoring_control import MonitoringAction, MonitoringControlResult
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import ControlRedisKeys, PublicRuntimeRedisKeys, RedisNamespace
from models.runtime_status import (
    CheckStatus,
    PublicStreamStatus,
    RuntimeHealth,
    RuntimeStatus,
)
from presentation.api.adapters.redis_command_result_reader import RedisCommandResultReader
from presentation.api.adapters.redis_monitoring_control import RedisMonitoringControl
from presentation.api.adapters.redis_runtime_status import (
    CorruptRuntimeStatusError,
    RedisRuntimeStatusReader,
    RuntimeStatusRedisUnavailableError,
)
from presentation.api.main import create_app

pytestmark = pytest.mark.redis_integration


class ProjectingWorkerSupervisor:
    """Supervisor kết hợp projector để mô phỏng chính xác worker pipeline."""

    def __init__(self, projector: RedisRuntimeStatusProjector) -> None:
        self.projector = projector
        self.streams = {}
        self.worker_id = "test-worker-node"

    def start(self, config):
        now = datetime.now(timezone.utc)
        self.streams[config.stream_id] = {
            "config": config,
            "status": PublicStreamStatus.RUNNING,
            "health": RuntimeHealth.HEALTHY,
            "started_at": now,
        }
        self.projector.project(
            RuntimeStatus(
                stream_id=config.stream_id,
                status=PublicStreamStatus.RUNNING,
                health=RuntimeHealth.HEALTHY,
                started_at=now,
                active_variant_count=2,
                queue_depth=1,
                queue_lag_seconds=0.1,
                telemetry_available=True,
                health_reasons=[],
                checks={
                    "black_screen": CheckStatus.ENABLED if config.black_screen_enabled else CheckStatus.DISABLED,
                    "audio_loss": CheckStatus.ENABLED if config.audio_loss_enabled else CheckStatus.DISABLED,
                },
                worker_id=self.worker_id,
                observed_at=now,
            )
        )
        return MonitoringControlResult(config.stream_id, MonitoringAction.START, True)

    def pause(self, stream_id):
        now = datetime.now(timezone.utc)
        if stream_id in self.streams:
            self.streams[stream_id]["status"] = PublicStreamStatus.PAUSED
            self.projector.project(
                RuntimeStatus(
                    stream_id=stream_id,
                    status=PublicStreamStatus.PAUSED,
                    health=RuntimeHealth.HEALTHY,
                    started_at=self.streams[stream_id]["started_at"],
                    active_variant_count=2,
                    queue_depth=0,
                    telemetry_available=True,
                    health_reasons=[],
                    checks={"black_screen": CheckStatus.ENABLED, "audio_loss": CheckStatus.DISABLED},
                    worker_id=self.worker_id,
                    observed_at=now,
                )
            )
            return MonitoringControlResult(stream_id, MonitoringAction.PAUSE, True)
        return MonitoringControlResult(stream_id, MonitoringAction.PAUSE, False)

    def resume(self, stream_id):
        now = datetime.now(timezone.utc)
        if stream_id in self.streams:
            self.streams[stream_id]["status"] = PublicStreamStatus.RUNNING
            self.projector.project(
                RuntimeStatus(
                    stream_id=stream_id,
                    status=PublicStreamStatus.RUNNING,
                    health=RuntimeHealth.HEALTHY,
                    started_at=self.streams[stream_id]["started_at"],
                    active_variant_count=2,
                    queue_depth=1,
                    telemetry_available=True,
                    health_reasons=[],
                    checks={"black_screen": CheckStatus.ENABLED, "audio_loss": CheckStatus.DISABLED},
                    worker_id=self.worker_id,
                    observed_at=now,
                )
            )
            return MonitoringControlResult(stream_id, MonitoringAction.RESUME, True)
        return MonitoringControlResult(stream_id, MonitoringAction.RESUME, False)

    def stop(self, stream_id):
        now = datetime.now(timezone.utc)
        self.streams.pop(stream_id, None)
        self.projector.remove(stream_id=stream_id, worker_id=self.worker_id, observed_at=now)
        return MonitoringControlResult(stream_id, MonitoringAction.STOP, True)

    def update_config(self, config):
        return MonitoringControlResult(config.stream_id, MonitoringAction.UPDATE_CONFIG, True)


@pytest.fixture
def redis_context():
    redis_url = os.getenv("REDIS_TEST_URL", "redis://localhost:6379/15")
    sync_client = RedisClient(
        RedisSettings(
            url=redis_url,
            socket_connect_timeout=0.25,
            socket_timeout=1.0,
        )
    )
    try:
        sync_client.ping()
    except Exception as exc:
        sync_client.close()
        pytest.skip(f"Disposable Redis is unavailable: {exc}")

    async_client = aioredis.from_url(redis_url, decode_responses=False)
    namespace = RedisNamespace(f"media-monitor:test:{uuid4().hex}")
    control_keys = ControlRedisKeys(namespace)
    public_keys = PublicRuntimeRedisKeys(namespace)

    try:
        yield sync_client, async_client, control_keys, public_keys, namespace
    finally:
        found = list(sync_client.client.scan_iter(match=f"{namespace.prefix}:*"))
        if found:
            sync_client.client.delete(*found)
        sync_client.close()


def test_tier1_projector_to_api_status_read(redis_context):
    sync_client, async_client, control_keys, public_keys, namespace = redis_context

    projector = RedisRuntimeStatusProjector(redis_client=sync_client, keys=public_keys)
    reader = RedisRuntimeStatusReader(redis_client=async_client, keys=public_keys)
    control = RedisMonitoringControl(redis_client=async_client, keys=control_keys)
    cmd_reader = RedisCommandResultReader(redis_client=async_client, keys=control_keys)

    app = create_app(
        control=control,
        command_results=cmd_reader,
        status_reader=reader,
        enable_fake_generator=False,
    )

    t_obs = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    status = RuntimeStatus(
        stream_id="channel-tier1",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        started_at=t_obs,
        last_poll_at=t_obs,
        active_variant_count=3,
        queue_depth=7,
        queue_lag_seconds=0.42,
        telemetry_available=True,
        health_reasons=["Rendition 1080p OK", "Rendition 720p OK"],
        checks={"black_screen": CheckStatus.ENABLED, "audio_loss": CheckStatus.ENABLED},
        worker_id="worker-node-99",
        observed_at=t_obs,
    )
    projector.project(status)

    with TestClient(app) as client:
        res = client.get("/api/v1/streams/channel-tier1/status")
        assert res.status_code == 200
        data = res.json()
        assert data["schema_version"] == "1.0"
        assert data["stream_id"] == "channel-tier1"
        assert data["status"] == "RUNNING"
        assert data["health"] == "HEALTHY"
        assert data["worker_id"] == "worker-node-99"
        assert datetime.fromisoformat(data["observed_at"]) == t_obs
        assert data["active_variant_count"] == 3
        assert data["queue_depth"] == 7
        assert data["queue_lag_seconds"] == 0.42
        assert data["telemetry_available"] is True
        assert data["checks"] == {"black_screen": "ENABLED", "audio_loss": "ENABLED"}
        assert data["health_reasons"] == ["Rendition 1080p OK", "Rendition 720p OK"]
        assert "storage_id" not in data


def test_tier2_lifecycle_e2e_runtime_status(redis_context):
    sync_client, async_client, control_keys, public_keys, namespace = redis_context

    projector = RedisRuntimeStatusProjector(redis_client=sync_client, keys=public_keys)
    supervisor = ProjectingWorkerSupervisor(projector)
    handler = MonitoringCommandHandler(supervisor)
    consumer = RedisMonitoringCommandConsumer(
        redis_client=sync_client,
        handler=handler,
        keys=control_keys,
        consumer_name="lifecycle-worker",
        block_milliseconds=10,
        claim_idle_milliseconds=10,
    )
    consumer.ensure_group()

    control = RedisMonitoringControl(redis_client=async_client, keys=control_keys)
    cmd_reader = RedisCommandResultReader(redis_client=async_client, keys=control_keys)
    status_reader = RedisRuntimeStatusReader(redis_client=async_client, keys=public_keys)

    app = create_app(
        control=control,
        command_results=cmd_reader,
        status_reader=status_reader,
        enable_fake_generator=False,
    )

    with TestClient(app) as client:
        # 1. Missing stream initially -> 404
        assert client.get("/api/v1/streams/chan-live-01/status").status_code == 404

        # 2. START stream -> 202
        start_payload = {
            "schema_version": "1.0",
            "stream_id": "chan-live-01",
            "master_url": "https://example.com/live.m3u8",
            "checks": {
                "black_screen": {"enabled": True},
                "audio_loss": {"enabled": False, "threshold_dbfs": -30.0, "duration_seconds": 5.0},
            },
        }
        res_start = client.post("/api/v1/streams/chan-live-01/start", json=start_payload)
        assert res_start.status_code == 202

        # Process command on worker
        assert consumer.poll_once() == 1

        # Query status -> 200 RUNNING
        res_st = client.get("/api/v1/streams/chan-live-01/status")
        assert res_st.status_code == 200
        assert res_st.json()["status"] == "RUNNING"
        assert res_st.json()["worker_id"] == "test-worker-node"

        # 3. PAUSE stream -> 202
        res_pause = client.post("/api/v1/streams/chan-live-01/pause")
        assert res_pause.status_code == 202
        assert consumer.poll_once() == 1

        # Query status -> 200 PAUSED
        res_paused = client.get("/api/v1/streams/chan-live-01/status")
        assert res_paused.status_code == 200
        assert res_paused.json()["status"] == "PAUSED"

        # 4. RESUME stream -> 202
        res_resume = client.post("/api/v1/streams/chan-live-01/resume")
        assert res_resume.status_code == 202
        assert consumer.poll_once() == 1

        # Query status -> 200 RUNNING
        res_resumed = client.get("/api/v1/streams/chan-live-01/status")
        assert res_resumed.status_code == 200
        assert res_resumed.json()["status"] == "RUNNING"

        # 5. STOP stream -> 202
        res_stop = client.post("/api/v1/streams/chan-live-01/stop")
        assert res_stop.status_code == 202
        assert consumer.poll_once() == 1

        # Query status -> 404 Not Found (hash field was removed)
        res_stopped = client.get("/api/v1/streams/chan-live-01/status")
        assert res_stopped.status_code == 404


def test_corrupt_hash_payload_returns_controlled_503(redis_context):
    sync_client, async_client, control_keys, public_keys, namespace = redis_context

    # Manually insert corrupt JSON into hash
    sync_client.client.hset(public_keys.current_statuses(), "corrupt-stream", "not-a-valid-json{{")

    reader = RedisRuntimeStatusReader(redis_client=async_client, keys=public_keys)
    control = RedisMonitoringControl(redis_client=async_client, keys=control_keys)
    cmd_reader = RedisCommandResultReader(redis_client=async_client, keys=control_keys)

    app = create_app(
        control=control,
        command_results=cmd_reader,
        status_reader=reader,
        enable_fake_generator=False,
    )

    with TestClient(app) as client:
        res = client.get("/api/v1/streams/corrupt-stream/status")
        assert res.status_code == 503
        data = res.json()
        assert data["detail"] == "Monitoring service is temporarily unavailable"
        assert "not-a-valid-json" not in json.dumps(data)
