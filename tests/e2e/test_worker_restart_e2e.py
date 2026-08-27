from threading import Thread
import pytest

from app.monitoring_worker import MonitoringWorkerApplication
from app.monitoring_worker_runner import MonitoringWorkerRunner
from tests.e2e.fake_session import FakeSessionFactory

pytestmark = pytest.mark.worker_e2e


def test_worker_restart_restores_running_and_paused_without_backend_resend(redis_context, probe):
    client, namespace = redis_context

    # --- Phase 1: Run Worker A and set up initial state ---
    factory_a = FakeSessionFactory()
    worker_a = MonitoringWorkerApplication(
        redis_settings=client.settings,
        namespace=namespace,
        session_factory=factory_a,
        worker_id="worker-restart-A",
        max_streams=10,
        projection_interval=0.1,
        heartbeat_interval=0.2,
        heartbeat_ttl=2,
    )
    runner_a = MonitoringWorkerRunner(application=worker_a, command_worker=True, shutdown_timeout=5.0)
    thread_a = Thread(target=runner_a.run, kwargs={"install_signal_handlers": False}, daemon=True)
    thread_a.start()

    try:
        probe.wait_worker_state("worker-restart-A", expected_state="READY", timeout=5.0)

        # 1. channel-01 -> RUNNING
        cmd_1 = probe.send_command("START", "channel-01")
        probe.wait_command_result(cmd_1, timeout=5.0)
        probe.wait_runtime_status("channel-01", expected_status="RUNNING", timeout=5.0)

        # 2. channel-02 -> PAUSED
        cmd_2 = probe.send_command("START", "channel-02")
        probe.wait_command_result(cmd_2, timeout=5.0)
        cmd_2_pause = probe.send_command("PAUSE", "channel-02")
        probe.wait_command_result(cmd_2_pause, timeout=5.0)
        probe.wait_runtime_status("channel-02", expected_status="PAUSED", timeout=5.0)

        # 3. channel-03 -> STOPPED
        cmd_3 = probe.send_command("START", "channel-03")
        probe.wait_command_result(cmd_3, timeout=5.0)
        cmd_3_stop = probe.send_command("STOP", "channel-03")
        probe.wait_command_result(cmd_3_stop, timeout=5.0)
        probe.wait_runtime_status_removed("channel-03", timeout=5.0)

    finally:
        runner_a.request_shutdown()
        thread_a.join(timeout=5.0)
        assert not thread_a.is_alive(), "worker A did not stop"

    # --- Phase 2: Start Worker B in the same namespace ---
    factory_b = FakeSessionFactory()
    worker_b = MonitoringWorkerApplication(
        redis_settings=client.settings,
        namespace=namespace,
        session_factory=factory_b,
        worker_id="worker-restart-B",
        max_streams=10,
        projection_interval=0.1,
        heartbeat_interval=0.2,
        heartbeat_ttl=2,
    )
    runner_b = MonitoringWorkerRunner(application=worker_b, command_worker=True, shutdown_timeout=5.0)
    thread_b = Thread(target=runner_b.run, kwargs={"install_signal_handlers": False}, daemon=True)
    thread_b.start()

    try:
        # Worker B starts and becomes READY after reconciling
        probe.wait_worker_state("worker-restart-B", expected_state="READY", timeout=5.0)

        # channel-01 must be RUNNING automatically without new backend command
        snap_1 = probe.wait_runtime_status("channel-01", expected_status="RUNNING", timeout=5.0)
        assert snap_1["status"] == "RUNNING"
        assert snap_1["worker_id"] == "worker-restart-B"

        # channel-02 must be PAUSED
        snap_2 = probe.wait_runtime_status("channel-02", expected_status="PAUSED", timeout=5.0)
        assert snap_2["status"] == "PAUSED"

        # channel-03 must NOT be resurrected
        assert probe.get_desired_state("channel-03")["desired_state"] == "STOPPED"
        probe.wait_runtime_status_removed("channel-03", timeout=2.0)

        # Verify only channel-01 was started; channel-02 was never started in Worker B
        assert factory_b.started_count == 1
        assert "channel-01" in factory_b.sessions
        assert "channel-02" not in factory_b.sessions

    finally:
        runner_b.request_shutdown()
        thread_b.join(timeout=5.0)
        assert not thread_b.is_alive(), "worker B did not stop"


def test_worker_crash_before_ack_reclaims_without_duplicate_session(redis_context, probe):
    client, namespace = redis_context
    control_keys = probe.control_keys

    # Ensure consumer group
    try:
        client.client.xgroup_create(
            control_keys.commands(),
            "monitoring-workers",
            id="0-0",
            mkstream=True,
        )
    except Exception:
        pass

    # Send command
    cmd_id = probe.send_command("START", "channel-claim-01")

    # Dead worker reads command into pending list but dies before ACK
    client.client.xreadgroup(
        "monitoring-workers",
        "worker-dead-commands",
        {control_keys.commands(): ">"},
        count=1,
    )

    # Worker B starts with claim_idle_milliseconds=0 to claim abandoned pending
    factory_b = FakeSessionFactory()
    worker_b = MonitoringWorkerApplication(
        redis_settings=client.settings,
        namespace=namespace,
        session_factory=factory_b,
        consumer_name="worker-live-commands",
        worker_id="worker-live-01",
        max_streams=10,
        projection_interval=0.1,
        heartbeat_interval=0.2,
        heartbeat_ttl=2,
    )
    worker_b.command_consumer.claim_idle_milliseconds = 0

    runner_b = MonitoringWorkerRunner(
        application=worker_b,
        command_worker=True,
        shutdown_timeout=5.0,
    )
    thread_b = Thread(
        target=runner_b.run,
        kwargs={"install_signal_handlers": False},
        daemon=True,
    )
    thread_b.start()

    try:
        probe.wait_worker_state("worker-live-01", expected_state="READY", timeout=5.0)
        res = probe.wait_command_result(cmd_id, timeout=5.0)
        assert res["status"] == "APPLIED"

        snap = probe.wait_runtime_status("channel-claim-01", expected_status="RUNNING", timeout=5.0)
        assert snap["status"] == "RUNNING"

        assert factory_b.started_count == 1
        assert factory_b.created_count == 1
    finally:
        runner_b.request_shutdown()
        thread_b.join(timeout=5.0)
        assert not thread_b.is_alive(), "recovery worker did not stop"
