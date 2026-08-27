from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Event, Thread
import pytest

from app.monitoring_worker import MonitoringWorkerApplication
from app.monitoring_worker_runner import MonitoringWorkerRunner
from scripts.generate_audio_loss_fixtures import generate_fixtures as generate_audio_fixtures
from scripts.publish_live_hls import HLS_ROOT, publish

pytestmark = [
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
        pytest.skip("FFmpeg/FFprobe is not installed on the system")


def _generate_black_screen_vod(output_dir: Path, segment_duration: float = 1.0, total_duration: int = 10) -> None:
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
        f"color=c=black:s=128x72:r=10:d={total_duration}",
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


def test_black_screen_alert_end_to_end(redis_context, probe):
    _check_prerequisites()
    client, namespace = redis_context
    worker_id = "worker-e2e-black-media"

    HLS_ROOT.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="e2e-black-", dir=HLS_ROOT) as temp_dir:
        temp_path = Path(temp_dir)
        source_dir = temp_path / "source"
        live_dir = temp_path / "live"
        live_dir.mkdir(parents=True, exist_ok=True)
        _generate_black_screen_vod(source_dir, segment_duration=1.0, total_duration=10)

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

        publisher_thread = Thread(
            target=run_publisher,
            daemon=True,
        )
        publisher_thread.start()

        # Wait for initial master.m3u8 to be rendered by publisher
        master_file = live_dir / "master.m3u8"
        def black_publisher_ready():
            if publisher_errors:
                raise publisher_errors[0]
            return True if master_file.exists() else None

        probe.wait_until(black_publisher_ready, timeout=5.0, description="master.m3u8 generation")

        master_url = f"http://127.0.0.1:{server_port}/live/master.m3u8"
        stream_id = "channel-e2e-black"

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

        try:
            probe.wait_worker_state(worker_id, expected_state="READY", timeout=5.0)

            cmd_config = {
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
            cmd_id = probe.send_command("START", stream_id, config=cmd_config)
            res = probe.wait_command_result(cmd_id, timeout=5.0)
            assert res["status"] == "APPLIED"
            probe.wait_runtime_status(stream_id, expected_status="RUNNING", timeout=5.0)

            # Wait for black screen alert in public outbox
            alert = probe.wait_alert(
                lambda env: env.stream_id == stream_id and env.event_type == "BLACK_SCREEN",
                stream_id=stream_id,
                timeout=25.0,
            )

            assert alert.stream_id == stream_id
            assert alert.event_type == "BLACK_SCREEN"
            assert alert.alert_id is not None
            assert alert.variant_stable_id is not None
            assert "storage_id" not in alert.attributes
            assert "storage_id" not in alert.to_redis_fields()["payload"]

            # Clean stop
            stop_id = probe.send_command("STOP", stream_id)
            probe.wait_command_result(stop_id, timeout=5.0)

        finally:
            runner.request_shutdown()
            runner_thread.join(timeout=5.0)
            publisher_stop.set()
            publisher_thread.join(timeout=5.0)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5.0)

        assert not runner_thread.is_alive(), "black media worker did not stop"
        assert not publisher_thread.is_alive(), "black HLS publisher did not stop"
        assert not server_thread.is_alive(), "black HLS server did not stop"
        assert publisher_errors == []


def test_audio_loss_alert_end_to_end(redis_context, probe):
    _check_prerequisites()
    client, namespace = redis_context
    worker_id = "worker-e2e-audio-media"

    HLS_ROOT.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="e2e-audio-", dir=HLS_ROOT) as temp_dir:
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

        publisher_thread = Thread(
            target=run_publisher,
            daemon=True,
        )
        publisher_thread.start()

        master_file = live_root / "master.m3u8"
        def audio_publisher_ready():
            if publisher_errors:
                raise publisher_errors[0]
            return True if master_file.exists() else None

        probe.wait_until(audio_publisher_ready, timeout=5.0, description="master.m3u8 generation")

        master_url = f"http://127.0.0.1:{server_port}/live/master.m3u8"
        stream_id = "channel-e2e-audio"

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

        try:
            probe.wait_worker_state(worker_id, expected_state="READY", timeout=5.0)

            cmd_config = {
                "schema_version": "1.0",
                "stream_id": stream_id,
                "master_url": master_url,
                "checks": {
                    "black_screen": {"enabled": False},
                    "audio_loss": {
                        "enabled": True,
                        "threshold_dbfs": -60.0,
                        "duration_seconds": 30.0,
                        "track_index": 0,
                    },
                },
            }
            cmd_id = probe.send_command("START", stream_id, config=cmd_config)
            res = probe.wait_command_result(cmd_id, timeout=5.0)
            assert res["status"] == "APPLIED"
            probe.wait_runtime_status(stream_id, expected_status="RUNNING", timeout=5.0)

            # Wait for audio loss alert in public outbox
            alert = probe.wait_alert(
                lambda env: env.stream_id == stream_id and env.event_type == "AUDIO_LOSS",
                stream_id=stream_id,
                timeout=30.0,
            )

            assert alert.stream_id == stream_id
            assert alert.event_type == "AUDIO_LOSS"
            assert alert.alert_id is not None
            assert alert.variant_stable_id is not None
            assert "storage_id" not in alert.attributes
            assert "storage_id" not in alert.to_redis_fields()["payload"]

            # Clean stop
            stop_id = probe.send_command("STOP", stream_id)
            probe.wait_command_result(stop_id, timeout=5.0)

        finally:
            runner.request_shutdown()
            runner_thread.join(timeout=5.0)
            publisher_stop.set()
            publisher_thread.join(timeout=5.0)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5.0)

        assert not runner_thread.is_alive(), "audio media worker did not stop"
        assert not publisher_thread.is_alive(), "audio HLS publisher did not stop"
        assert not server_thread.is_alive(), "audio HLS server did not stop"
        assert publisher_errors == []
