from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import time
import tracemalloc
from uuid import uuid4

import pytest

from app.monitoring_session_factory import MonitoringSessionFactory
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from core.stream_supervisor import StreamSupervisor
from models.alert import AlertEnvelope
from models.stream_config import StreamConfig
from scripts.generate_audio_loss_fixtures import generate_fixtures
from scripts.publish_live_hls import HLS_ROOT, publish


pytestmark = [pytest.mark.integration, pytest.mark.live_validation]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return


def _audio_alerts(client: RedisClient, key: str, stream_id: str):
    envelopes = (
        AlertEnvelope.from_redis_fields(fields)
        for _, fields in client.client.xrange(key)
    )
    return [
        envelope
        for envelope in envelopes
        if envelope.stream_id == stream_id
        and envelope.event_type == "AUDIO_LOSS"
    ]


def test_live_loops_are_deterministic_and_drain_queue():
    if os.getenv("RUN_PHASE6_LIVE") != "1":
        pytest.skip("Set RUN_PHASE6_LIVE=1 to run the long live validation")
    loop_count = int(os.getenv("LIVE_VALIDATION_LOOPS", "3"))
    publish_speed = float(os.getenv("LIVE_VALIDATION_SPEED", "4"))
    validation_timeout = float(
        os.getenv(
            "LIVE_VALIDATION_TIMEOUT",
            str((22 * 2.0 * loop_count / publish_speed) + 30.0),
        )
    )
    if loop_count <= 0 or publish_speed <= 0 or validation_timeout <= 0:
        raise ValueError("Live validation controls must be > 0")
    expected_alert_count = loop_count * 2 * 2

    redis_client = RedisClient(
        RedisSettings(
            url=os.getenv("REDIS_TEST_URL", "redis://localhost:6379/15"),
            socket_connect_timeout=0.25,
            socket_timeout=1.0,
        )
    )
    try:
        redis_client.ping()
    except Exception as exc:
        redis_client.close()
        pytest.skip(f"Disposable Redis is unavailable: {exc}")

    namespace = RedisNamespace(f"media-monitor:phase6:{uuid4().hex}")
    alert_keys = AlertRedisKeys(namespace)
    runtime_keys = RuntimeRedisKeys(namespace)
    configured_stream_id = f"phase6-audio-{uuid4().hex}"
    stream_id = ""
    publisher_errors: list[BaseException] = []
    supervisor: StreamSupervisor | None = None
    server: ThreadingHTTPServer | None = None
    server_thread: Thread | None = None
    publisher_thread: Thread | None = None
    started = time.monotonic()
    cpu_started = time.process_time()
    tracemalloc.start()

    try:
        HLS_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="phase6-", dir=HLS_ROOT) as temp:
            root = Path(temp)
            source_root = root / "source"
            live_root = root / "live"
            generate_fixtures(source_root, segment_duration=2.0)
            source = source_root / "audio_silence_35s"

            handler = partial(QuietHandler, directory=str(root))
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            server_thread = Thread(target=server.serve_forever, daemon=True)
            server_thread.start()

            def run_publisher():
                try:
                    publish(
                        source=source,
                        output=live_root,
                        window_size=8,
                        retention_segments=16,
                        start_sequence=0,
                        loop=True,
                        reset=False,
                        max_publishes=22 * loop_count,
                        speed=publish_speed,
                    )
                except BaseException as exc:
                    publisher_errors.append(exc)

            publisher_thread = Thread(target=run_publisher, daemon=True)
            publisher_thread.start()
            deadline = time.monotonic() + 10
            while not (live_root / "high" / "playlist.m3u8").is_file():
                if publisher_errors:
                    raise publisher_errors[0]
                if time.monotonic() >= deadline:
                    raise TimeoutError("Live publisher did not become ready")
                time.sleep(0.05)

            port = server.server_address[1]
            config = StreamConfig(
                master_url=f"http://127.0.0.1:{port}/live/master.m3u8",
                stream_id=configured_stream_id,
                black_screen_enabled=False,
                audio_loss_enabled=True,
                audio_loss_duration=6.0,
                max_concurrent_media_processes=2,
                max_work_age_seconds=300.0,
            )
            supervisor = StreamSupervisor(
                session_factory=MonitoringSessionFactory(
                    redis_settings=redis_client.settings,
                    namespace=namespace,
                ),
                max_streams=1,
            )
            stream_id = supervisor.add(config)
            stored_config = supervisor.configuration(stream_id)
            assert stored_config is not None
            storage_id = stored_config.identity.storage_id

            deadline = time.monotonic() + validation_timeout
            alerts = []
            while time.monotonic() < deadline:
                if publisher_errors:
                    raise publisher_errors[0]
                snapshot = supervisor.snapshots()[stream_id]
                if snapshot.error is not None:
                    raise RuntimeError(snapshot.error)
                alerts = _audio_alerts(
                    redis_client,
                    alert_keys.outbox(),
                    stream_id,
                )
                metrics = redis_client.client.hgetall(
                    runtime_keys.metrics(storage_id)
                )
                publisher_done = (
                    publisher_thread is not None
                    and not publisher_thread.is_alive()
                )
                if (
                    len(alerts) == expected_alert_count
                    and publisher_done
                    and metrics.get("queue_depth") == "0"
                ):
                    break
                time.sleep(0.25)

            diagnostic = {
                "alert_count": len(alerts),
                "metrics": metrics,
                "session": supervisor.snapshots()[stream_id],
                "outbox_length": redis_client.client.xlen(
                    alert_keys.outbox()
                ),
            }
            assert len(alerts) == expected_alert_count, diagnostic
            by_variant: dict[str, list[AlertEnvelope]] = {}
            for alert in alerts:
                stable_id = alert.variant_stable_id
                assert stable_id is not None
                by_variant.setdefault(stable_id, []).append(alert)
            assert len(by_variant) == 2
            for variant_alerts in by_variant.values():
                assert [item.state for item in variant_alerts] == (
                    ["OPEN", "RESOLVED"] * loop_count
                )
                assert all(
                    item.attributes["primary_cause"] == "continuous_silence"
                    for item in variant_alerts
                )
                for opened, resolved in zip(
                    variant_alerts[::2], variant_alerts[1::2]
                ):
                    assert opened.event_id == resolved.event_id

            metrics = redis_client.client.hgetall(
                runtime_keys.metrics(storage_id)
            )
            assert metrics["queue_depth"] == "0"
            assert metrics["dropped_work"] == "0"
            assert metrics["ffmpeg_timeout_total"] == "0"
            assert metrics["audio_analysis_total"] == str(22 * loop_count * 2)
            assert metrics["audio_loss_open_total"] == str(loop_count * 2)
            assert metrics["audio_loss_resolved_total"] == str(loop_count * 2)
            _, peak_bytes = tracemalloc.get_traced_memory()
            baseline = {
                "wall_seconds": time.monotonic() - started,
                "python_cpu_seconds": time.process_time() - cpu_started,
                "python_peak_bytes": peak_bytes,
                "runtime_metrics": metrics,
            }
            print("PHASE6_BASELINE=" + json.dumps(baseline, sort_keys=True))
    finally:
        if supervisor is not None:
            supervisor.stop_all()
        if publisher_thread is not None:
            publisher_thread.join(timeout=5)
        if server is not None:
            server.shutdown()
            server.server_close()
        if server_thread is not None:
            server_thread.join(timeout=5)
        tracemalloc.stop()
        found = list(
            redis_client.client.scan_iter(match=f"{namespace.prefix}:*")
        )
        if found:
            redis_client.client.delete(*found)
        redis_client.close()
