import json
import os
from threading import Thread
import time
from uuid import uuid4
import pytest

from app.monitoring_worker import MonitoringWorkerApplication
from app.monitoring_worker_runner import MonitoringWorkerRunner
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import ControlRedisKeys, RedisNamespace, WorkerRedisKeys
from tests.e2e.backend_probe import BackendProbe
from tests.e2e.fake_session import FakeSessionFactory

pytestmark = [pytest.mark.integration, pytest.mark.redis_integration]


@pytest.fixture
def redis_context():
    client = RedisClient(
        RedisSettings(
            url=os.getenv("REDIS_TEST_URL", "redis://localhost:6379/15"),
            socket_connect_timeout=1.0,
            socket_timeout=3.0,
        )
    )
    try:
        client.ping()
    except Exception as exc:
        client.close()
        pytest.skip(f"Disposable Redis is unavailable: {exc}")

    namespace = RedisNamespace(f"media-monitor:obs-test:{uuid4().hex}")
    try:
        yield client, namespace
    finally:
        try:
            found = list(client.client.scan_iter(match=f"{namespace.prefix}:*"))
            if found:
                client.client.delete(*found)
        except Exception:
            pass
        finally:
            client.close()


def test_command_metrics_published_to_redis_hash(redis_context):
    client, namespace = redis_context
    probe = BackendProbe(redis_client=client, namespace=namespace)
    worker_id = "worker-obs-01"
    factory = FakeSessionFactory()

    application = MonitoringWorkerApplication(
        redis_settings=client.settings,
        namespace=namespace,
        session_factory=factory,
        worker_id=worker_id,
        max_streams=5,
        projection_interval=0.1,
        heartbeat_interval=0.2,
        heartbeat_ttl=2,
    )
    runner = MonitoringWorkerRunner(application=application, command_worker=True, shutdown_timeout=5.0)
    runner_thread = Thread(target=runner.run, kwargs={"install_signal_handlers": False}, daemon=True)
    runner_thread.start()

    try:
        probe.wait_worker_state(worker_id, expected_state="READY", timeout=5.0)

        # 1. Valid START -> command_applied_total = 1
        req_time = "2026-08-27T10:00:00Z"
        cmd_start = probe.send_command("START", "channel-obs-1", requested_at=req_time)
        probe.wait_command_result(cmd_start, timeout=5.0)
        metrics = probe.wait_command_metrics(
            worker_id,
            predicate=lambda m: m.get("command_applied_total") == "1",
            timeout=5.0,
        )
        assert metrics["worker_id"] == worker_id
        assert metrics["schema_version"] == "1.0"
        assert metrics["command_applied_total"] == "1"
        assert metrics["command_noop_total"] == "0"
        assert float(metrics["command_processing_duration_ms_total"]) > 0

        # 2. Duplicate START with same payload -> command_duplicate_replay_total = 1
        probe.send_command("START", "channel-obs-1", command_id=cmd_start, requested_at=req_time)
        metrics = probe.wait_command_metrics(
            worker_id,
            predicate=lambda m: m.get("command_duplicate_replay_total") == "1",
            timeout=5.0,
        )
        assert metrics["command_duplicate_replay_total"] == "1"
        assert metrics["command_applied_total"] == "1"

        # 3. Valid PAUSE -> command_applied_total = 2
        cmd_pause = probe.send_command("PAUSE", "channel-obs-1")
        probe.wait_command_result(cmd_pause, timeout=5.0)
        metrics = probe.wait_command_metrics(
            worker_id,
            predicate=lambda m: m.get("command_applied_total") == "2",
            timeout=5.0,
        )
        assert metrics["command_applied_total"] == "2"

    finally:
        runner.request_shutdown()
        runner_thread.join(timeout=5.0)


def test_oversized_command_rejection_and_safe_dlq_metadata(redis_context):
    client, namespace = redis_context
    probe = BackendProbe(redis_client=client, namespace=namespace)
    worker_id = "worker-obs-oversized"
    factory = FakeSessionFactory()
    control_keys = ControlRedisKeys(namespace)

    application = MonitoringWorkerApplication(
        redis_settings=client.settings,
        namespace=namespace,
        session_factory=factory,
        worker_id=worker_id,
        max_command_payload_bytes=200,
        projection_interval=0.1,
        heartbeat_interval=0.2,
        heartbeat_ttl=2,
    )
    runner = MonitoringWorkerRunner(application=application, command_worker=True, shutdown_timeout=5.0)
    runner_thread = Thread(target=runner.run, kwargs={"install_signal_handlers": False}, daemon=True)
    runner_thread.start()

    try:
        probe.wait_worker_state(worker_id, expected_state="READY", timeout=5.0)

        # Post an oversized command (500 bytes)
        huge_payload = json.dumps({
            "schema_version": "1.0",
            "command_id": "cmd-huge",
            "command_type": "START",
            "stream_id": "channel-huge",
            "requested_at": "2026-08-27T10:00:00Z",
            "padding": "x" * 400,
        })
        client.client.xadd(control_keys.commands(), {"payload": huge_payload})

        # Wait for metrics to record oversized and dead letter
        metrics = probe.wait_command_metrics(
            worker_id,
            predicate=lambda m: m.get("command_oversized_total") == "1",
            timeout=5.0,
        )
        assert metrics["command_oversized_total"] == "1"
        assert metrics["command_dead_letter_total"] == "1"

        # Verify DLQ entries
        dlq_entries = client.client.xrange(control_keys.dead_letter())
        assert len(dlq_entries) == 1
        _, fields = dlq_entries[0]
        fields_str = {
            (k.decode("utf-8") if isinstance(k, bytes) else str(k)): (v.decode("utf-8") if isinstance(v, bytes) else str(v))
            for k, v in fields.items()
        }
        assert fields_str["error_code"] == "COMMAND_PAYLOAD_TOO_LARGE"
        assert int(fields_str["payload_size_bytes"]) >= 400
        # CRITICAL: raw payload must NOT be saved in DLQ when oversized
        assert "payload" not in fields_str

    finally:
        runner.request_shutdown()
        runner_thread.join(timeout=5.0)


def test_result_and_dlq_stream_retention_trimming(redis_context):
    client, namespace = redis_context
    probe = BackendProbe(redis_client=client, namespace=namespace)
    worker_id = "worker-obs-trim"
    factory = FakeSessionFactory()
    control_keys = ControlRedisKeys(namespace)

    application = MonitoringWorkerApplication(
        redis_settings=client.settings,
        namespace=namespace,
        session_factory=factory,
        worker_id=worker_id,
        projection_interval=0.1,
        heartbeat_interval=0.2,
        heartbeat_ttl=2,
    )
    # Set tight limits for testing trimming
    application.command_consumer.result_max_length = 5
    application.command_consumer.dead_letter_max_length = 5

    runner = MonitoringWorkerRunner(application=application, command_worker=True, shutdown_timeout=5.0)
    runner_thread = Thread(target=runner.run, kwargs={"install_signal_handlers": False}, daemon=True)
    runner_thread.start()

    try:
        probe.wait_worker_state(worker_id, expected_state="READY", timeout=5.0)

        # Send 8 valid commands
        for i in range(8):
            cid = probe.send_command("START", f"stream-{i}")
            probe.wait_command_result(cid, timeout=5.0)

        # Send 8 invalid commands directly into stream
        for i in range(8):
            client.client.xadd(control_keys.commands(), {"payload": f"invalid-json-{i}"})

        # Wait until metrics show 8 dead letters
        probe.wait_command_metrics(
            worker_id,
            predicate=lambda m: int(m.get("command_dead_letter_total", 0)) >= 8,
            timeout=5.0,
        )

        # Assert stream lengths do not exceed retention maxlen
        res_len = client.client.xlen(control_keys.command_results())
        assert res_len <= 5

        dlq_len = client.client.xlen(control_keys.dead_letter())
        assert dlq_len <= 5

    finally:
        runner.request_shutdown()
        runner_thread.join(timeout=5.0)
