from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core.redis_client import RedisClient
from scripts.generate_monitoring_test_cases import (
    CASES,
    DEFAULT_OUTPUT,
    SEGMENT_DURATION,
    case_by_name,
)


def expected_content_alerts(expected: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (item["event_type"], state)
        for item in expected["expected_alerts"]
        for state in item["states"]
    }


def expected_content_alert_counts(
    expected: dict[str, Any],
) -> Counter[tuple[str, str]]:
    return Counter(
        {
            (item["event_type"], state): int(item.get("per_variant", 1))
            for item in expected["expected_alerts"]
            for state in item["states"]
        }
    )


def observed_content_alerts(entries: list[dict[str, str]]) -> set[tuple[str, str]]:
    return {
        (item.get("type", ""), item.get("state", ""))
        for item in entries
        if item.get("category") == "content"
    }


def observed_content_alert_counts(
    entries: list[dict[str, str]],
) -> Counter[tuple[str, str]]:
    return Counter(
        (item.get("type", ""), item.get("state", ""))
        for item in entries
        if item.get("category") == "content"
    )


def verify_case(
    name: str,
    *,
    source_root: Path,
    live_root: Path,
    public_base_url: str,
    redis_prefix: str,
    speed: float,
    timeout: float,
) -> dict[str, Any]:
    spec = case_by_name(name)
    source = source_root / name
    expected = json.loads(
        (source / "expected.json").read_text(encoding="ascii")
    )
    stream_id = f"fixture-{name}-{uuid4().hex[:8]}"
    namespace = f"{redis_prefix}:{name}:{uuid4().hex[:8]}"
    live_case = live_root / name
    master_url = f"{public_base_url.rstrip('/')}/{live_root.name}/{name}/master.m3u8"
    checks = expected["recommended_checks"]
    worker_command = [
        sys.executable,
        str(SRC_ROOT / "live_main.py"),
        "--url",
        master_url,
        "--stream-id",
        stream_id,
        "--redis-prefix",
        namespace,
        "--max-streams",
        "1",
        "--variant-selection",
        "highest_quality",
    ]
    if not checks["black_screen"]:
        worker_command.append("--disable-black-screen")
    if not checks["audio_loss"]:
        worker_command.append("--disable-audio-loss")
    if checks["video_freeze"]:
        worker_command.append("--enable-video-freeze")
    if checks["macroblocking"]:
        worker_command.append("--enable-macroblocking")

    publisher_command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "publish_monitoring_test_stream.py"),
        name,
        "--source-root",
        str(source_root),
        "--output-root",
        str(live_root),
        "--speed",
        f"{speed:g}",
        "--max-publishes",
        str(round(spec.duration / SEGMENT_DURATION)),
        "--reset",
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(SRC_ROOT), str(PROJECT_ROOT))
    )
    creationflags = (
        subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    )
    worker = subprocess.Popen(
        worker_command,
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    publisher: subprocess.Popen[bytes] | None = None
    redis_client = RedisClient()
    try:
        time.sleep(1.0)
        publisher = subprocess.Popen(
            publisher_command,
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        publisher_timeout = spec.duration / speed + 20.0
        publisher_code = publisher.wait(timeout=publisher_timeout)
        if publisher_code != 0:
            raise RuntimeError(f"Publisher failed for {name}: {publisher_code}")

        expected_counts = expected_content_alert_counts(expected)
        deadline = time.monotonic() + timeout
        entries: list[dict[str, str]] = []
        metrics: dict[str, str] = {}
        expected_segments = round(spec.duration / SEGMENT_DURATION)
        while time.monotonic() < deadline:
            raw_entries = redis_client.client.xrange(f"{namespace}:alerts:outbox")
            entries = [fields for _entry_id, fields in raw_entries]
            metrics_keys = tuple(
                redis_client.client.scan_iter(
                    match=f"{namespace}:stream:*:runtime:metrics"
                )
            )
            metrics = (
                redis_client.client.hgetall(metrics_keys[0])
                if metrics_keys
                else {}
            )
            completed = int(
                metrics.get("perf_video_realtime_work_completed_total", "0")
            ) + int(metrics.get("perf_audio_realtime_work_completed_total", "0"))
            if (
                not (expected_counts - observed_content_alert_counts(entries))
                and completed >= expected_segments
            ):
                break
            time.sleep(0.25)

        observed_counts = observed_content_alert_counts(entries)
        missing_counts = expected_counts - observed_counts
        unexpected_counts = observed_counts - expected_counts
        missing = sorted(missing_counts.elements())
        unexpected = sorted(unexpected_counts.elements())
        dropped = int(metrics.get("dropped_media_segments_total", "0"))
        return {
            "case": name,
            "stream_id": stream_id,
            "master_url": master_url,
            "expected": sorted(expected_counts.elements()),
            "observed": sorted(observed_counts.elements()),
            "missing": missing,
            "unexpected": unexpected,
            "dropped_media_segments_total": dropped,
            "variant_count": int(metrics.get("variant_count", "0")),
            "passed": not missing and not unexpected and dropped == 0,
        }
    finally:
        redis_client.close()
        if publisher is not None and publisher.poll() is None:
            publisher.terminate()
            publisher.wait(timeout=5)
        _stop_worker(worker)


def _stop_worker(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify deterministic HLS cases through the production worker"
    )
    parser.add_argument(
        "--case",
        action="append",
        choices=tuple(item.name for item in CASES),
        dest="cases",
    )
    parser.add_argument("--source-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--live-root",
        type=Path,
        default=PROJECT_ROOT / "hls_output" / "live_validation",
    )
    parser.add_argument(
        "--public-base-url",
        default="http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--redis-prefix",
        default="media-monitor:fixture-verification",
    )
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / ".benchmark" / "monitoring-fixtures.json",
    )
    args = parser.parse_args()
    if args.speed <= 0 or args.timeout <= 0:
        parser.error("speed and timeout must be > 0")

    selected = args.cases or [item.name for item in CASES]
    results = [
        verify_case(
            name,
            source_root=args.source_root.resolve(),
            live_root=args.live_root.resolve(),
            public_base_url=args.public_base_url,
            redis_prefix=args.redis_prefix,
            speed=args.speed,
            timeout=args.timeout,
        )
        for name in selected
    ]
    report = {
        "schema_version": "1.0",
        "passed": all(item["passed"] for item in results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
        newline="\n",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
