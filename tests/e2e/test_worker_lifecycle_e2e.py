from datetime import datetime, timezone
from threading import Thread
import pytest

from app.monitoring_worker import MonitoringWorkerApplication
from app.monitoring_worker_runner import MonitoringWorkerRunner
from tests.e2e.fake_session import FakeSessionFactory

pytestmark = pytest.mark.worker_e2e


def test_full_lifecycle_fast_e2e(redis_context, probe):
    client, namespace = redis_context
    factory = FakeSessionFactory()
    worker_id = "worker-e2e-lifecycle-01"

    application = MonitoringWorkerApplication(
        redis_settings=client.settings,
        namespace=namespace,
        session_factory=factory,
        worker_id=worker_id,
        max_streams=10,
        projection_interval=0.1,
        heartbeat_interval=0.2,
        heartbeat_ttl=2,
    )

    runner = MonitoringWorkerRunner(
        application=application,
        command_worker=True,
        shutdown_timeout=5.0,
    )

    runner_thread = Thread(
        target=runner.run,
        kwargs={"install_signal_handlers": False},
        name="test-runner-thread",
        daemon=True,
    )
    runner_thread.start()

    try:
        # Step 0: Wait for worker to become READY
        probe.wait_worker_state(worker_id, expected_state="READY", timeout=5.0)

        # Step 1: START command -> APPLIED -> runtime RUNNING -> desired RUNNING
        requested_at = datetime.now(timezone.utc)
        cmd_start = probe.send_command(
            "START",
            "channel-01",
            requested_at=requested_at,
        )
        res_start = probe.wait_command_result(cmd_start, timeout=5.0)
        assert res_start["status"] == "APPLIED"
        assert res_start["changed"] is True

        snap_running = probe.wait_runtime_status("channel-01", expected_status="RUNNING", timeout=5.0)
        assert snap_running["status"] == "RUNNING"
        assert snap_running["worker_id"] == worker_id
        assert "storage_id" not in snap_running

        desired_running = probe.get_desired_state("channel-01")
        assert desired_running is not None
        assert desired_running["desired_state"] == "RUNNING"
        assert desired_running["config"]["master_url"] == "https://example.test/live.m3u8"
        assert factory.started_count == 1

        # Step 2: Duplicate START command (same command_id) -> idempotent, no duplicate session
        probe.send_command(
            "START",
            "channel-01",
            command_id=cmd_start,
            requested_at=requested_at,
        )
        probe.wait_command_delivery_settled(timeout=5.0)
        assert factory.started_count == 1
        assert factory.created_count == 1
        assert probe.command_result_count(cmd_start) == 1

        # Step 3: PAUSE command -> APPLIED -> runtime PAUSED -> desired PAUSED
        cmd_pause = probe.send_command("PAUSE", "channel-01")
        res_pause = probe.wait_command_result(cmd_pause, timeout=5.0)
        assert res_pause["status"] == "APPLIED"
        assert res_pause["changed"] is True

        snap_paused = probe.wait_runtime_status("channel-01", expected_status="PAUSED", timeout=5.0)
        assert snap_paused["status"] == "PAUSED"

        desired_paused = probe.get_desired_state("channel-01")
        assert desired_paused is not None
        assert desired_paused["desired_state"] == "PAUSED"
        assert desired_paused["config"] is not None

        # Step 4: RESUME command -> APPLIED -> runtime RUNNING -> desired RUNNING
        cmd_resume = probe.send_command("RESUME", "channel-01")
        res_resume = probe.wait_command_result(cmd_resume, timeout=5.0)
        assert res_resume["status"] == "APPLIED"

        snap_resumed = probe.wait_runtime_status("channel-01", expected_status="RUNNING", timeout=5.0)
        assert snap_resumed["status"] == "RUNNING"

        # Step 5: UPDATE_CONFIG command -> APPLIED -> config updated
        new_config = {
            "schema_version": "1.0",
            "stream_id": "channel-01",
            "master_url": "https://example.test/live.m3u8",
            "checks": {
                "black_screen": {"enabled": True},
                "audio_loss": {
                    "enabled": True,
                    "threshold_dbfs": -45.0,
                    "duration_seconds": 20.0,
                    "track_index": 0,
                },
            },
        }
        cmd_update = probe.send_command("UPDATE_CONFIG", "channel-01", config=new_config)
        res_update = probe.wait_command_result(cmd_update, timeout=5.0)
        assert res_update["status"] == "APPLIED"

        desired_updated = probe.get_desired_state("channel-01")
        assert desired_updated["config"]["checks"]["audio_loss"]["threshold_dbfs"] == -45.0

        # Step 6: STOP command -> APPLIED -> runtime REMOVED -> desired STOPPED
        cmd_stop = probe.send_command("STOP", "channel-01")
        res_stop = probe.wait_command_result(cmd_stop, timeout=5.0)
        assert res_stop["status"] == "APPLIED"

        probe.wait_runtime_status_removed("channel-01", timeout=5.0)
        desired_stopped = probe.get_desired_state("channel-01")
        assert desired_stopped["desired_state"] == "STOPPED"
        assert desired_stopped["config"] is None

        # Step 7: Verify worker command metrics published to Redis
        metrics = probe.wait_command_metrics(
            worker_id,
            predicate=lambda m: int(m.get("command_applied_total", 0)) >= 5,
            timeout=5.0,
        )
        assert metrics["worker_id"] == worker_id
        assert metrics["schema_version"] == "1.0"
        assert metrics["command_applied_total"] == "5"
        assert metrics["command_duplicate_replay_total"] == "1"
        assert metrics["command_dead_letter_total"] == "0"
        assert float(metrics["command_processing_duration_ms_total"]) > 0

    finally:
        runner.request_shutdown()
        runner_thread.join(timeout=5.0)
        assert not runner_thread.is_alive(), "worker runner did not stop"
        report = runner.shutdown()

    assert report.commands_drained is True
    assert report.streams_drained is True
    assert report.heartbeat_stopped is True
    assert report.projection_stopped is True
    assert report.redis_closed is True
    assert report.timed_out is False
    assert report.errors == ()
