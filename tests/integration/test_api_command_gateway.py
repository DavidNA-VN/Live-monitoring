from datetime import datetime, timezone
import json
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
import redis.asyncio as aioredis

from app.monitoring_command_handler import MonitoringCommandHandler
from app.redis_monitoring_command_consumer import RedisMonitoringCommandConsumer
from core.monitoring_control import MonitoringAction, MonitoringControlResult
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import ControlRedisKeys, RedisNamespace
from presentation.api.adapters.redis_command_result_reader import RedisCommandResultReader
from presentation.api.adapters.redis_monitoring_control import RedisMonitoringControl
from presentation.api.adapters.fakes import FakeMonitoringControl
from presentation.api.main import create_app

pytestmark = pytest.mark.redis_integration


class MockWorkerSupervisorControl:
    """Mô phỏng supervisor xử lý commands ở tầng worker."""

    def __init__(self) -> None:
        self.streams = {}
        self.call_history = []

    def start(self, config):
        self.call_history.append(("START", config.stream_id))
        is_new = config.stream_id not in self.streams
        self.streams[config.stream_id] = config
        return MonitoringControlResult(
            stream_id=config.stream_id,
            action=MonitoringAction.START,
            changed=is_new,
        )

    def pause(self, stream_id):
        self.call_history.append(("PAUSE", stream_id))
        return MonitoringControlResult(
            stream_id=stream_id,
            action=MonitoringAction.PAUSE,
            changed=True,
        )

    def resume(self, stream_id):
        self.call_history.append(("RESUME", stream_id))
        return MonitoringControlResult(
            stream_id=stream_id,
            action=MonitoringAction.RESUME,
            changed=True,
        )

    def stop(self, stream_id):
        self.call_history.append(("STOP", stream_id))
        self.streams.pop(stream_id, None)
        return MonitoringControlResult(
            stream_id=stream_id,
            action=MonitoringAction.STOP,
            changed=True,
        )

    def update_config(self, config):
        self.call_history.append(("UPDATE_CONFIG", config.stream_id))
        self.streams[config.stream_id] = config
        return MonitoringControlResult(
            stream_id=config.stream_id,
            action=MonitoringAction.UPDATE_CONFIG,
            changed=True,
        )


@pytest.fixture
def redis_gateway_context():
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
    keys = ControlRedisKeys(namespace)

    try:
        yield sync_client, async_client, keys, namespace
    finally:
        found = list(sync_client.client.scan_iter(match=f"{namespace.prefix}:*"))
        if found:
            sync_client.client.delete(*found)
        sync_client.close()


def test_full_command_gateway_lifecycle_and_idempotency(redis_gateway_context):
    sync_client, async_client, keys, namespace = redis_gateway_context

    supervisor = MockWorkerSupervisorControl()
    handler = MonitoringCommandHandler(supervisor)
    consumer = RedisMonitoringCommandConsumer(
        redis_client=sync_client,
        handler=handler,
        keys=keys,
        consumer_name="gateway-test-worker",
        block_milliseconds=10,
        claim_idle_milliseconds=10,
    )
    consumer.ensure_group()

    redis_control = RedisMonitoringControl(
        redis_client=async_client,
        keys=keys,
    )
    redis_results = RedisCommandResultReader(
        redis_client=async_client,
        keys=keys,
    )

    app = create_app(
        control=redis_control,
        command_results=redis_results,
        status_reader=FakeMonitoringControl(),
        enable_fake_generator=False,
    )

    with TestClient(app) as client:
        start_payload = {
            "schema_version": "1.0",
            "stream_id": "channel-real-01",
            "master_url": "https://example.com/live/master.m3u8",
            "checks": {
                "black_screen": {"enabled": True},
                "audio_loss": {
                    "enabled": True,
                    "threshold_dbfs": -35.0,
                    "duration_seconds": 5.0,
                    "track_index": 0,
                },
            },
        }

        # 1. POST START -> 202 ACCEPTED
        res_start = client.post(
            "/api/v1/streams/channel-real-01/start",
            json=start_payload,
            headers={"Idempotency-Key": "idemp-stream-1"},
        )
        assert res_start.status_code == 202
        start_sub = res_start.json()
        command_id = start_sub["command_id"]
        assert start_sub["status"] == "ACCEPTED"

        # 2. Query immediately before worker consumes -> 202 PENDING
        res_pending = client.get(f"/api/v1/commands/{command_id}")
        assert res_pending.status_code == 202
        assert res_pending.json()["status"] == "ACCEPTED"

        # 3. Worker consumes command from stream
        handled = consumer.poll_once()
        assert handled == 1

        # 4. Query again after worker finishes -> 200 FINAL APPLIED
        res_final = client.get(f"/api/v1/commands/{command_id}")
        assert res_final.status_code == 200
        cmd_result = res_final.json()
        assert cmd_result["command_id"] == command_id
        assert cmd_result["command_type"] == "START"
        assert cmd_result["status"] == "APPLIED"
        assert cmd_result["changed"] is True

        # 5. Retry same START with same Idempotency-Key -> returns original command_id, no second session
        res_retry = client.post(
            "/api/v1/streams/channel-real-01/start",
            json=start_payload,
            headers={"Idempotency-Key": "idemp-stream-1"},
        )
        assert res_retry.status_code == 202
        assert res_retry.json()["command_id"] == command_id
        # Worker sees duplicate replay, acknowledges without creating duplicate session
        assert consumer.poll_once() == 0
        assert len(supervisor.call_history) == 1

        # 6. Reusing same Idempotency-Key with different action/payload -> 409 Conflict
        res_conflict = client.post(
            "/api/v1/streams/channel-real-01/pause",
            headers={"Idempotency-Key": "idemp-stream-1"},
        )
        assert res_conflict.status_code == 409

        # 7. POST PAUSE -> 202
        res_pause = client.post("/api/v1/streams/channel-real-01/pause")
        assert res_pause.status_code == 202
        pause_cmd_id = res_pause.json()["command_id"]

        assert consumer.poll_once() == 1
        res_pause_final = client.get(f"/api/v1/commands/{pause_cmd_id}")
        assert res_pause_final.status_code == 200
        assert res_pause_final.json()["status"] == "APPLIED"
        assert res_pause_final.json()["command_type"] == "PAUSE"

        # 8. POST RESUME -> 202
        res_resume = client.post("/api/v1/streams/channel-real-01/resume")
        assert res_resume.status_code == 202
        resume_cmd_id = res_resume.json()["command_id"]

        assert consumer.poll_once() == 1
        res_resume_final = client.get(f"/api/v1/commands/{resume_cmd_id}")
        assert res_resume_final.status_code == 200
        assert res_resume_final.json()["status"] == "APPLIED"
        assert res_resume_final.json()["command_type"] == "RESUME"

        # 9. POST STOP -> 202
        res_stop = client.post("/api/v1/streams/channel-real-01/stop")
        assert res_stop.status_code == 202
        stop_cmd_id = res_stop.json()["command_id"]

        assert consumer.poll_once() == 1
        res_stop_final = client.get(f"/api/v1/commands/{stop_cmd_id}")
        assert res_stop_final.status_code == 200
        assert res_stop_final.json()["status"] == "APPLIED"
        assert res_stop_final.json()["command_type"] == "STOP"

        # A poisoned keyspace must fail without leaving a phantom receipt.
        sync_client.client.delete(keys.commands())
        sync_client.client.set(keys.commands(), "wrong-type")
        poisoned_token = "idemp-poisoned-stream"
        res_poisoned = client.post(
            "/api/v1/streams/channel-real-01/pause",
            headers={"Idempotency-Key": poisoned_token},
        )
        assert res_poisoned.status_code == 503
        assert sync_client.client.get(keys.idempotency_key(poisoned_token)) is None
