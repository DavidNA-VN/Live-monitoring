from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.monitoring_worker import MonitoringWorkerApplication
from app.monitoring_worker_runner import MonitoringWorkerRunner
from presentation.api.main import create_app
from presentation.api.settings import ApiSettings
from scripts.generate_audio_loss_fixtures import generate_fixtures as generate_audio_fixtures
from scripts.publish_live_hls import HLS_ROOT, publish

pytestmark = [
    pytest.mark.mvp_e2e,
    pytest.mark.worker_e2e,
    pytest.mark.media_e2e,
    pytest.mark.live_validation,
]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return


def _check_prerequisites():
    if os.getenv("RUN_WORKER_MEDIA_E2E") != "1":
        pytest.skip("Set RUN_WORKER_MEDIA_E2E=1 to run real media E2E tests")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        if os.getenv("REQUIRE_MVP_E2E") == "1":
            pytest.fail("FFmpeg and FFprobe are required for MVP media E2E tests")
        pytest.skip("FFmpeg/FFprobe is not installed on the system")


def _generate_black_screen_vod(output_dir: Path, segment_duration: float = 1.0) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    playlist_path = output_dir / "stream.m3u8"
    master_path = output_dir / "master.m3u8"

    cmd = [
        shutil.which("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=128x72:r=10:d=6",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=128x72:r=10:d=4",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-g",
        "10",
        "-an",
        "-f",
        "hls",
        "-hls_time",
        str(segment_duration),
        "-hls_list_size",
        "0",
        "-hls_segment_filename",
        str(output_dir / "seg_%03d.ts"),
        str(playlist_path),
    ]
    subprocess.run(cmd, check=True, timeout=20)

    master_content = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=128x72\n'
        "stream.m3u8\n"
    )
    master_path.write_text(master_content, encoding="utf-8")


def _poll_until(predicate, timeout=10.0, interval=0.1, description="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        res = predicate()
        if res:
            return res
        time.sleep(interval)
    res = predicate()
    if res:
        return res
    raise AssertionError(f"Timed out after {timeout}s waiting for {description}")


def _start_websocket_collector(ws):
    messages: Queue = Queue()

    def collect() -> None:
        try:
            while True:
                messages.put(ws.receive_json())
        except BaseException as exc:
            messages.put(exc)

    Thread(target=collect, daemon=True).start()
    return messages


def _wait_websocket_alert(messages: Queue, *, event_type: str, state: str, timeout: float = 25.0):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"Timed out waiting for {event_type}/{state} WebSocket alert")
        try:
            item = messages.get(timeout=remaining)
        except Empty as exc:
            raise AssertionError(f"Timed out waiting for {event_type}/{state} WebSocket alert") from exc
        if isinstance(item, BaseException):
            raise AssertionError("WebSocket receiver stopped before expected alert") from item
        payload = item.get("payload", {})
        if payload.get("event_type") == event_type and payload.get("state") == state:
            return item


def test_mvp_black_screen_api_to_worker_e2e(redis_context, probe):
    _check_prerequisites()
    client, namespace = redis_context
    worker_id = f"worker-mvp-black-{uuid4().hex[:6]}"

    HLS_ROOT.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="e2e-mvp-black-", dir=HLS_ROOT) as temp_dir:
        temp_path = Path(temp_dir)
        source_dir = temp_path / "source"
        live_dir = temp_path / "live"
        live_dir.mkdir(parents=True, exist_ok=True)
        _generate_black_screen_vod(source_dir, segment_duration=1.0)

        handler = partial(QuietHandler, directory=str(temp_path))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_port = server.server_address[1]
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        publisher_stop = Event()
        publisher_errors: list[BaseException] = []

        def run_publisher() -> None:
            try:
                publish(**{
                    "source": source_dir,
                    "output": live_dir,
                    "window_size": 5,
                    "retention_segments": 8,
                    "start_sequence": 0,
                    "loop": True,
                    "reset": True,
                    "max_publishes": None,
                    "speed": 6.0,
                    "stop_event": publisher_stop,
                })
            except BaseException as exc:
                publisher_errors.append(exc)

        publisher_thread = Thread(target=run_publisher, daemon=True)
        publisher_thread.start()

        master_file = live_dir / "master.m3u8"
        probe.wait_until(
            lambda: True if master_file.exists() else None,
            timeout=5.0,
            description="master.m3u8 generation",
        )

        master_url = f"http://127.0.0.1:{server_port}/live/master.m3u8"
        stream_id = f"chan-mvp-black-{uuid4().hex[:6]}"

        application = MonitoringWorkerApplication(
            redis_settings=client.settings,
            namespace=namespace,
            worker_id=worker_id,
            max_streams=5,
            projection_interval=0.2,
            heartbeat_interval=0.5,
        )
        runner = MonitoringWorkerRunner(application=application, command_worker=True, shutdown_timeout=5.0)
        runner_thread = Thread(target=runner.run, kwargs={"install_signal_handlers": False}, daemon=True)
        runner_thread.start()

        app = create_app(
            settings=ApiSettings(
                mode="redis",
                redis_url=client.settings.url,
                redis_prefix=namespace.prefix,
                enable_fake_generator=False,
                websocket_redis_block_ms=50,
            )
        )

        try:
            probe.wait_worker_state(worker_id, expected_state="READY", timeout=5.0)

            with TestClient(app) as test_client:
                # 1. Open WebSocket to stream
                with test_client.websocket_connect(f"/api/v1/ws/streams/{stream_id}") as ws:
                    ws_messages = _start_websocket_collector(ws)
                    # 2. Send START command via REST API
                    start_payload = {
                        "schema_version": "1.0",
                        "stream_id": stream_id,
                        "master_url": master_url,
                        "checks": {
                            "black_screen": {"enabled": True},
                            "audio_loss": {
                                "enabled": False,
                                "threshold_dbfs": -60.0,
                                "duration_seconds": 30.0,
                                "track_index": 0,
                            },
                        },
                    }
                    start_res = test_client.post(
                        f"/api/v1/streams/{stream_id}/start",
                        json=start_payload,
                        headers={"Idempotency-Key": f"idemp-{uuid4().hex[:8]}"},
                    )
                    assert start_res.status_code == 202
                    command_id = start_res.json()["command_id"]

                    # 3. Poll command result via REST API
                    def check_command_applied():
                        r = test_client.get(f"/api/v1/commands/{command_id}")
                        if r.status_code == 200 and r.json().get("status") == "APPLIED":
                            return r.json()
                        return None

                    cmd_final = _poll_until(check_command_applied, timeout=5.0, description="command APPLIED")
                    assert cmd_final["status"] == "APPLIED"

                    # 4. Poll public runtime status via REST API
                    def check_status_running():
                        r = test_client.get(f"/api/v1/streams/{stream_id}/status")
                        if r.status_code == 200 and r.json().get("status") == "RUNNING":
                            return r.json()
                        return None

                    status_json = _poll_until(check_status_running, timeout=10.0, description="status RUNNING")
                    assert status_json["status"] == "RUNNING"
                    assert status_json["worker_id"] == worker_id
                    assert "storage_id" not in status_json

                    # 5. Receive real BLACK_SCREEN alert from WebSocket
                    msg = _wait_websocket_alert(
                        ws_messages,
                        event_type="BLACK_SCREEN",
                        state="OPEN",
                    )
                    assert msg["message_type"] == "ALERT"
                    assert msg["stream_id"] == stream_id
                    payload = msg["payload"]
                    assert payload["event_type"] == "BLACK_SCREEN"
                    assert payload["state"] == "OPEN"
                    assert payload["stream_id"] == stream_id
                    assert "storage_id" not in payload
                    assert "storage_id" not in payload.get("attributes", {})

                    resolved_msg = _wait_websocket_alert(
                        ws_messages,
                        event_type="BLACK_SCREEN",
                        state="RESOLVED",
                    )
                    assert resolved_msg["payload"]["event_id"] == payload["event_id"]

                    # 6. Verify alert is also queryable in REST recent history
                    history_res = test_client.get(f"/api/v1/streams/{stream_id}/events?limit=50")
                    assert history_res.status_code == 200
                    history_items = history_res.json()
                    assert len(history_items) >= 1
                    history_alert_ids = {item["alert_id"] for item in history_items}
                    assert payload["alert_id"] in history_alert_ids
                    assert resolved_msg["payload"]["alert_id"] in history_alert_ids

                    duplicate_res = test_client.post(
                        f"/api/v1/streams/{stream_id}/start",
                        json=start_payload,
                        headers={"Idempotency-Key": f"idemp-duplicate-{uuid4().hex[:8]}"},
                    )
                    assert duplicate_res.status_code == 202
                    duplicate_command_id = duplicate_res.json()["command_id"]

                    def check_duplicate_noop():
                        response = test_client.get(f"/api/v1/commands/{duplicate_command_id}")
                        if response.status_code == 200 and response.json().get("status") == "NOOP":
                            return response.json()
                        return None

                    _poll_until(check_duplicate_noop, timeout=5.0, description="duplicate START NOOP")

                    # 7. Send STOP command
                    stop_res = test_client.post(
                        f"/api/v1/streams/{stream_id}/stop",
                        headers={"Idempotency-Key": f"idemp-stop-{uuid4().hex[:8]}"},
                    )
                    assert stop_res.status_code == 202
                    stop_cmd_id = stop_res.json()["command_id"]

                    def check_stop_applied():
                        r = test_client.get(f"/api/v1/commands/{stop_cmd_id}")
                        if r.status_code == 200 and r.json().get("status") == "APPLIED":
                            return r.json()
                        return None

                    _poll_until(check_stop_applied, timeout=5.0, description="STOP command APPLIED")

                    # 8. Verify status returns 404 after STOP
                    def check_status_removed():
                        r = test_client.get(f"/api/v1/streams/{stream_id}/status")
                        return True if r.status_code == 404 else None

                    _poll_until(check_status_removed, timeout=5.0, description="status 404 removed")

        finally:
            runner.request_shutdown()
            runner_thread.join(timeout=5.0)
            publisher_stop.set()
            publisher_thread.join(timeout=5.0)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5.0)

        assert not runner_thread.is_alive(), "Worker did not stop"
        assert not publisher_thread.is_alive(), "Publisher did not stop"
        assert not server_thread.is_alive(), "HTTP server did not stop"
        assert publisher_errors == []


def test_mvp_audio_loss_api_to_worker_e2e(redis_context, probe):
    _check_prerequisites()
    client, namespace = redis_context
    worker_id = f"worker-mvp-audio-{uuid4().hex[:6]}"

    HLS_ROOT.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="e2e-mvp-audio-", dir=HLS_ROOT) as temp_dir:
        temp_path = Path(temp_dir)
        source_root = temp_path / "source"
        live_root = temp_path / "live"
        live_root.mkdir(parents=True, exist_ok=True)
        generate_audio_fixtures(source_root, segment_duration=2.0)
        source = source_root / "audio_silence_35s"

        handler = partial(QuietHandler, directory=str(temp_path))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_port = server.server_address[1]
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        publisher_stop = Event()
        publisher_errors: list[BaseException] = []

        def run_publisher() -> None:
            try:
                publish(**{
                    "source": source,
                    "output": live_root,
                    "window_size": 6,
                    "retention_segments": 10,
                    "start_sequence": 0,
                    "loop": True,
                    "reset": True,
                    "max_publishes": None,
                    "speed": 6.0,
                    "stop_event": publisher_stop,
                })
            except BaseException as exc:
                publisher_errors.append(exc)

        publisher_thread = Thread(target=run_publisher, daemon=True)
        publisher_thread.start()

        master_file = live_root / "master.m3u8"
        probe.wait_until(
            lambda: True if master_file.exists() else None,
            timeout=5.0,
            description="master.m3u8 generation",
        )

        master_url = f"http://127.0.0.1:{server_port}/live/master.m3u8"
        stream_id = f"chan-mvp-audio-{uuid4().hex[:6]}"

        application = MonitoringWorkerApplication(
            redis_settings=client.settings,
            namespace=namespace,
            worker_id=worker_id,
            max_streams=5,
            projection_interval=0.2,
            heartbeat_interval=0.5,
        )
        runner = MonitoringWorkerRunner(application=application, command_worker=True, shutdown_timeout=5.0)
        runner_thread = Thread(target=runner.run, kwargs={"install_signal_handlers": False}, daemon=True)
        runner_thread.start()

        app = create_app(
            settings=ApiSettings(
                mode="redis",
                redis_url=client.settings.url,
                redis_prefix=namespace.prefix,
                enable_fake_generator=False,
                websocket_redis_block_ms=50,
            )
        )

        try:
            probe.wait_worker_state(worker_id, expected_state="READY", timeout=5.0)

            with TestClient(app) as test_client:
                # 1. Open WebSocket to stream
                with test_client.websocket_connect(f"/api/v1/ws/streams/{stream_id}") as ws:
                    ws_messages = _start_websocket_collector(ws)
                    # 2. Send START command
                    start_payload = {
                        "schema_version": "1.0",
                        "stream_id": stream_id,
                        "master_url": master_url,
                        "checks": {
                            "black_screen": {"enabled": False},
                            "audio_loss": {
                                "enabled": True,
                                "threshold_dbfs": -30.0,
                                "duration_seconds": 6.0,
                                "track_index": 0,
                            },
                        },
                    }
                    start_res = test_client.post(
                        f"/api/v1/streams/{stream_id}/start",
                        json=start_payload,
                        headers={"Idempotency-Key": f"idemp-{uuid4().hex[:8]}"},
                    )
                    assert start_res.status_code == 202
                    command_id = start_res.json()["command_id"]

                    # 3. Poll command result -> APPLIED
                    def check_start_applied():
                        r = test_client.get(f"/api/v1/commands/{command_id}")
                        if r.status_code == 200 and r.json().get("status") == "APPLIED":
                            return r.json()
                        return None

                    _poll_until(check_start_applied, timeout=5.0, description="START APPLIED")

                    # 4. Poll status -> RUNNING
                    def check_status_running():
                        r = test_client.get(f"/api/v1/streams/{stream_id}/status")
                        if r.status_code == 200 and r.json().get("status") == "RUNNING":
                            return r.json()
                        return None

                    _poll_until(check_status_running, timeout=10.0, description="status RUNNING")

                    # 5. Receive AUDIO_LOSS alert from WebSocket
                    msg = _wait_websocket_alert(
                        ws_messages,
                        event_type="AUDIO_LOSS",
                        state="OPEN",
                    )
                    assert msg["message_type"] == "ALERT"
                    assert msg["stream_id"] == stream_id
                    payload = msg["payload"]
                    assert payload["event_type"] == "AUDIO_LOSS"
                    assert payload["state"] == "OPEN"

                    # 6. Test PAUSE via REST API
                    pause_res = test_client.post(
                        f"/api/v1/streams/{stream_id}/pause",
                        headers={"Idempotency-Key": f"idemp-pause-{uuid4().hex[:8]}"},
                    )
                    assert pause_res.status_code == 202
                    pause_cmd_id = pause_res.json()["command_id"]

                    def check_pause_applied():
                        r = test_client.get(f"/api/v1/commands/{pause_cmd_id}")
                        if r.status_code == 200 and r.json().get("status") in ("APPLIED", "NOOP"):
                            return r.json()
                        return None

                    _poll_until(check_pause_applied, timeout=5.0, description="PAUSE APPLIED")

                    def check_status_paused():
                        r = test_client.get(f"/api/v1/streams/{stream_id}/status")
                        if r.status_code == 200 and r.json().get("status") == "PAUSED":
                            return r.json()
                        return None

                    _poll_until(check_status_paused, timeout=5.0, description="status PAUSED")

                    # 7. Test RESUME via REST API
                    resume_res = test_client.post(
                        f"/api/v1/streams/{stream_id}/resume",
                        headers={"Idempotency-Key": f"idemp-resume-{uuid4().hex[:8]}"},
                    )
                    assert resume_res.status_code == 202
                    resume_cmd_id = resume_res.json()["command_id"]

                    def check_resume_applied():
                        r = test_client.get(f"/api/v1/commands/{resume_cmd_id}")
                        if r.status_code == 200 and r.json().get("status") in ("APPLIED", "NOOP"):
                            return r.json()
                        return None

                    _poll_until(check_resume_applied, timeout=5.0, description="RESUME APPLIED")

                    def check_status_resumed():
                        r = test_client.get(f"/api/v1/streams/{stream_id}/status")
                        if r.status_code == 200 and r.json().get("status") == "RUNNING":
                            return r.json()
                        return None

                    _poll_until(check_status_resumed, timeout=5.0, description="status RUNNING after RESUME")

                    # 8. Test STOP via REST API
                    stop_res = test_client.post(
                        f"/api/v1/streams/{stream_id}/stop",
                        headers={"Idempotency-Key": f"idemp-stop-{uuid4().hex[:8]}"},
                    )
                    assert stop_res.status_code == 202
                    stop_cmd_id = stop_res.json()["command_id"]

                    def check_stop_applied():
                        r = test_client.get(f"/api/v1/commands/{stop_cmd_id}")
                        if r.status_code == 200 and r.json().get("status") == "APPLIED":
                            return r.json()
                        return None

                    _poll_until(check_stop_applied, timeout=5.0, description="STOP APPLIED")

                    def check_status_removed():
                        r = test_client.get(f"/api/v1/streams/{stream_id}/status")
                        return True if r.status_code == 404 else None

                    _poll_until(check_status_removed, timeout=5.0, description="status 404 removed")

        finally:
            runner.request_shutdown()
            runner_thread.join(timeout=5.0)
            publisher_stop.set()
            publisher_thread.join(timeout=5.0)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5.0)

        assert not runner_thread.is_alive(), "Worker did not stop"
        assert not publisher_thread.is_alive(), "Publisher did not stop"
        assert not server_thread.is_alive(), "HTTP server did not stop"
        assert publisher_errors == []
