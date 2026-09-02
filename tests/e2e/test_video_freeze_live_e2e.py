from __future__ import annotations

from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from threading import Event, Thread
from uuid import uuid4

import pytest

from app.monitoring_worker import MonitoringWorkerApplication
from app.monitoring_worker_runner import MonitoringWorkerRunner
from scripts.generate_video_freeze_fixtures import generate_fixtures
from scripts.publish_live_hls import HLS_ROOT, publish


pytestmark = [
    pytest.mark.worker_e2e,
    pytest.mark.media_e2e,
    pytest.mark.live_validation,
]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return


def _check_prerequisites() -> None:
    if os.getenv("RUN_WORKER_MEDIA_E2E") != "1":
        pytest.skip("Set RUN_WORKER_MEDIA_E2E=1 to run real media E2E tests")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg/FFprobe is not installed")


@pytest.fixture(scope="module")
def freeze_fixture_root(tmp_path_factory):
    _check_prerequisites()
    root = tmp_path_factory.mktemp("video-freeze-live")
    source = root / "fixtures"
    generate_fixtures(source)
    return source


def _public_config(stream_id: str, master_url: str) -> dict:
    return {
        "schema_version": "1.0",
        "stream_id": stream_id,
        "master_url": master_url,
        "checks": {
            "black_screen": {"enabled": False},
            "audio_loss": {
                "enabled": False,
                "threshold_dbfs": -60.0,
                "duration_seconds": 30.0,
                "track_index": 0,
            },
            "video_freeze": {
                "enabled": True,
                "noise_db": -60.0,
                "detector_minimum_duration": 0.2,
                "warning_duration_seconds": 3.0,
                "alert_duration_seconds": 5.0,
            },
        },
    }


@contextmanager
def _running_freeze_case(
    *,
    source: Path,
    client,
    namespace,
    probe,
    stream_id: str,
):
    HLS_ROOT.mkdir(parents=True, exist_ok=True)
    live_temp = TemporaryDirectory(
        prefix=f"freeze-{uuid4().hex[:8]}-",
        dir=HLS_ROOT,
    )
    output = Path(live_temp.name)
    handler = partial(QuietHandler, directory=str(HLS_ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    publisher_stop = Event()
    publisher_errors: list[BaseException] = []

    def run_publisher() -> None:
        try:
            publish(
                source=source,
                output=output,
                window_size=6,
                retention_segments=12,
                start_sequence=0,
                loop=False,
                reset=True,
                max_publishes=None,
                speed=2.0,
                stop_event=publisher_stop,
            )
        except BaseException as exc:
            publisher_errors.append(exc)

    publisher_thread = Thread(target=run_publisher, daemon=True)
    publisher_thread.start()
    master = output / "master.m3u8"

    def publisher_ready():
        if publisher_errors:
            raise publisher_errors[0]
        return True if master.exists() else None

    probe.wait_until(
        publisher_ready,
        timeout=10.0,
        description="freeze live master playlist",
    )
    relative_master = master.relative_to(HLS_ROOT).as_posix()
    master_url = (
        f"http://127.0.0.1:{server.server_address[1]}/{relative_master}"
    )
    worker_id = f"worker-{stream_id}"
    application = MonitoringWorkerApplication(
        redis_settings=client.settings,
        namespace=namespace,
        worker_id=worker_id,
        max_streams=2,
        projection_interval=0.2,
        heartbeat_interval=0.5,
    )
    runner = MonitoringWorkerRunner(
        application=application,
        command_worker=True,
        shutdown_timeout=8.0,
    )
    runner_thread = Thread(
        target=runner.run,
        kwargs={"install_signal_handlers": False},
        daemon=True,
    )
    runner_thread.start()

    try:
        probe.wait_worker_state(worker_id, expected_state="READY", timeout=8.0)
        command_id = probe.send_command(
            "START",
            stream_id,
            config=_public_config(stream_id, master_url),
        )
        result = probe.wait_command_result(command_id, timeout=8.0)
        assert result["status"] == "APPLIED"
        status = probe.wait_runtime_status(
            stream_id,
            expected_status="RUNNING",
            timeout=8.0,
        )
        assert status["checks"]["video_freeze"] == "ENABLED"
        yield
    finally:
        try:
            stop_id = probe.send_command("STOP", stream_id)
            probe.wait_command_result(stop_id, timeout=5.0)
        except Exception:
            pass
        runner.request_shutdown()
        runner_thread.join(timeout=8.0)
        publisher_stop.set()
        publisher_thread.join(timeout=8.0)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5.0)
        live_temp.cleanup()

    assert not runner_thread.is_alive(), "freeze worker did not stop"
    assert not publisher_thread.is_alive(), "freeze publisher did not stop"
    assert not server_thread.is_alive(), "freeze HTTP server did not stop"
    assert publisher_errors == []


def test_freeze_warning_escalation_and_resolution_on_two_variants(
    redis_context,
    probe,
    freeze_fixture_root,
):
    client, namespace = redis_context
    fixtures = freeze_fixture_root
    stream_id = "freeze-live-escalation"
    with _running_freeze_case(
        source=fixtures / "freeze_5_2s",
        client=client,
        namespace=namespace,
        probe=probe,
        stream_id=stream_id,
    ):
        def complete_lifecycles():
            alerts = [
                item
                for item in probe.read_alerts(stream_id)
                if item.event_type == "VIDEO_FREEZE"
            ]
            grouped = {}
            for alert in alerts:
                grouped.setdefault(alert.event_id, []).append(alert)
            complete = [
                items
                for items in grouped.values()
                if [item.state for item in items]
                == ["OPEN", "UPDATE", "RESOLVED"]
            ]
            return complete if len(complete) == 2 else None

        lifecycles = probe.wait_until(
            complete_lifecycles,
            timeout=35.0,
            description="two freeze alert lifecycles",
        )

    assert len(lifecycles) == 2
    variant_ids = {
        item.variant_stable_id for items in lifecycles for item in items
    }
    assert None not in variant_ids
    assert len(variant_ids) == 2
    for items in lifecycles:
        assert [item.attributes["severity"] for item in items] == [
            "WARNING",
            "ALERT",
            "ALERT",
        ]
        assert items[-1].reason == "video_returned"
        assert len({item.event_id for item in items}) == 1


def test_three_warning_freezes_emit_one_repeated_alert_per_variant(
    redis_context,
    probe,
    freeze_fixture_root,
):
    client, namespace = redis_context
    fixtures = freeze_fixture_root
    stream_id = "freeze-live-repeated"
    with _running_freeze_case(
        source=fixtures / "three_warning_freezes",
        client=client,
        namespace=namespace,
        probe=probe,
        stream_id=stream_id,
    ):
        def repeated_alerts():
            items = [
                item
                for item in probe.read_alerts(stream_id)
                if item.event_type == "REPEATED_VIDEO_FREEZE"
                and item.state == "OPEN"
            ]
            return items if len(items) == 2 else None

        alerts = probe.wait_until(
            repeated_alerts,
            timeout=45.0,
            description="repeated freeze alerts for both variants",
        )

    variant_ids = {item.variant_stable_id for item in alerts}
    assert None not in variant_ids
    assert len(variant_ids) == 2
    assert all(item.attributes["severity"] == "ALERT" for item in alerts)
    assert all(item.attributes["occurrences"] == "3" for item in alerts)
