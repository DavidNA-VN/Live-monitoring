from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.monitoring_worker import MonitoringWorkerApplication
from core.redis_client import RedisSettings
from core.redis_keys import RedisNamespace, RuntimeRedisKeys
from models.analysis import AnalysisResourceClass, ResourcePoolLimit
from models.stream_config import StreamConfig
from models.variant_selection import VariantSelectionMode, VariantSelectionPolicy
from scripts.generate_monitoring_test_cases import DEFAULT_OUTPUT
from scripts.publish_live_hls import HLS_ROOT, publish


COUNTER_SUFFIX = "_total"
DEFAULT_CASE = "combined"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


@dataclass(frozen=True)
class BenchmarkConfiguration:
    source_kind: str
    checks: str
    video_workers: int
    audio_workers: int
    global_process_limit: int
    per_stream_process_limit: int
    variant_selection: str
    publisher_speed: float


@dataclass(frozen=True)
class ResourceSample:
    elapsed_seconds: float
    system_cpu_percent: float | None
    process_tree_rss_bytes: int | None
    ffmpeg_processes: int | None


@dataclass(frozen=True)
class MetricSample:
    elapsed_seconds: float
    values: dict[str, float]


@dataclass(frozen=True)
class BenchmarkResult:
    configuration: BenchmarkConfiguration
    warmup_seconds: float
    steady_seconds: float
    cooldown_seconds: float
    sample_count: int
    queue_lag_start_seconds: float
    queue_lag_end_seconds: float
    queue_lag_after_cooldown_seconds: float
    queue_lag_max_seconds: float
    queue_lag_slope_seconds_per_second: float
    live_edge_lag_max_seconds: float | None
    work_completed: int
    work_completed_per_second: float
    work_failed: int
    work_timed_out: int
    dropped_media_segments: int
    coverage_gap_segments: int
    warmup_dropped_media_segments: int
    steady_dropped_media_segments: int
    cooldown_dropped_media_segments: int
    warmup_coverage_gap_segments: int
    steady_coverage_gap_segments: int
    cooldown_coverage_gap_segments: int
    peak_active_media_processes: int
    mean_system_cpu_percent: float | None
    max_system_cpu_percent: float | None
    max_process_tree_rss_bytes: int | None
    max_ffmpeg_processes: int | None
    passed: bool
    failures: tuple[str, ...]
    warmup_metric_deltas: dict[str, float]
    metric_deltas: dict[str, float]
    cooldown_metric_deltas: dict[str, float]
    final_metrics: dict[str, float]
    profile_execution_p95_max_seconds: dict[str, float]
    executor_wait_p95_max_seconds: dict[str, float]
    process_gate_wait_p95_max_seconds: dict[str, float]
    environment: dict[str, str | int | float | None]


def _number(value: object) -> float | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_redis_metrics(raw: dict[object, object]) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for key, value in raw.items():
        name = key.decode("utf-8") if isinstance(key, bytes) else str(key)
        numeric = _number(value)
        if numeric is not None:
            parsed[name] = numeric
    return parsed


def counter_delta(
    first: dict[str, float], last: dict[str, float]
) -> dict[str, float]:
    names = {
        name
        for metrics in (first, last)
        for name in metrics
        if name.endswith(COUNTER_SUFFIX)
    }
    return {
        name: max(0.0, last.get(name, 0.0) - first.get(name, 0.0))
        for name in sorted(names)
    }


def linear_slope(samples: list[tuple[float, float]]) -> float:
    if len(samples) < 2:
        return 0.0
    xs = [item[0] for item in samples]
    ys = [item[1] for item in samples]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    return sum(
        (x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)
    ) / denominator


def _optional_resource_sample(elapsed: float) -> ResourceSample:
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return ResourceSample(elapsed, None, None, None)

    root = psutil.Process(os.getpid())
    processes = [root, *root.children(recursive=True)]
    rss = 0
    ffmpeg_count = 0
    for process in processes:
        try:
            rss += process.memory_info().rss
            if process.name().lower() in {"ffmpeg", "ffmpeg.exe"}:
                ffmpeg_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return ResourceSample(
        elapsed_seconds=elapsed,
        system_cpu_percent=psutil.cpu_percent(interval=None),
        process_tree_rss_bytes=rss,
        ffmpeg_processes=ffmpeg_count,
    )


def environment_snapshot() -> dict[str, str | int | float | None]:
    snapshot: dict[str, str | int | float | None] = {
        "platform": platform.platform(),
        "processor": platform.processor() or None,
        "python": platform.python_version(),
        "logical_cpu_count": os.cpu_count(),
        "ffmpeg": None,
        "memory_bytes": None,
    }
    try:
        completed = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout:
            snapshot["ffmpeg"] = completed.stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        import psutil  # type: ignore[import-not-found]

        snapshot["memory_bytes"] = psutil.virtual_memory().total
    except ImportError:
        pass
    return snapshot


def _sum_matching(deltas: dict[str, float], suffix: str) -> int:
    return int(
        sum(
            value
            for name, value in deltas.items()
            if name.startswith("perf_")
            and not name.startswith("perf_resource_")
            and name.endswith(suffix)
        )
    )


def _profile_gauge_maxima(
    samples: list[MetricSample], suffix: str
) -> dict[str, float]:
    maxima: dict[str, float] = {}
    for sample in samples:
        for name, value in sample.values.items():
            if (
                name.startswith("perf_")
                and not name.startswith("perf_resource_")
                and name.endswith(suffix)
            ):
                profile_name = name[len("perf_") : -len(suffix)].rstrip("_")
                maxima[profile_name] = max(maxima.get(profile_name, 0.0), value)
    return maxima


def summarize_run(
    *,
    configuration: BenchmarkConfiguration,
    warmup_seconds: float,
    steady_seconds: float,
    cooldown_seconds: float,
    metric_samples: list[MetricSample],
    resource_samples: list[ResourceSample],
    max_queue_lag_seconds: float,
    max_queue_slope: float,
    allow_dropped_segments: int,
) -> BenchmarkResult:
    if not metric_samples:
        raise ValueError("at least one metric sample is required")
    warmup_samples = [
        sample
        for sample in metric_samples
        if sample.elapsed_seconds <= warmup_seconds
    ]
    steady_end = warmup_seconds + steady_seconds
    steady_samples = [
        sample
        for sample in metric_samples
        if warmup_seconds <= sample.elapsed_seconds <= steady_end
    ]
    if not steady_samples:
        raise ValueError("steady-state must contain at least one metric sample")
    queue_points = [
        (sample.elapsed_seconds, sample.values.get("queue_lag_seconds", 0.0))
        for sample in steady_samples
    ]
    queue_values = [item[1] for item in queue_points]
    live_edge_values = [
        value
        for sample in steady_samples
        if (value := sample.values.get("live_edge_lag_seconds")) is not None
    ]
    first = metric_samples[0]
    warmup_boundary = warmup_samples[-1] if warmup_samples else first
    steady_boundary = steady_samples[-1]
    # Every benchmark uses an isolated Redis namespace, so counters start at
    # zero even when the first sample is collected after work has completed.
    warmup_deltas = counter_delta({}, warmup_boundary.values)
    deltas = counter_delta(warmup_boundary.values, steady_boundary.values)
    cooldown_deltas = counter_delta(
        steady_boundary.values,
        metric_samples[-1].values,
    )
    final = metric_samples[-1].values
    warmup_dropped = int(
        warmup_deltas.get("dropped_media_segments_total", 0.0)
    )
    steady_dropped = int(
        deltas.get("dropped_media_segments_total", 0.0)
    )
    cooldown_dropped = int(
        cooldown_deltas.get("dropped_media_segments_total", 0.0)
    )
    dropped = warmup_dropped + steady_dropped + cooldown_dropped
    warmup_coverage_gaps = int(
        warmup_deltas.get("coverage_gap_segment_total", 0.0)
    )
    steady_coverage_gaps = int(
        deltas.get("coverage_gap_segment_total", 0.0)
    )
    cooldown_coverage_gaps = int(
        cooldown_deltas.get("coverage_gap_segment_total", 0.0)
    )
    coverage_gaps = (
        warmup_coverage_gaps
        + steady_coverage_gaps
        + cooldown_coverage_gaps
    )
    completed = _sum_matching(deltas, "work_completed_total")
    failed = _sum_matching(deltas, "work_failed_total")
    timed_out = _sum_matching(deltas, "work_timed_out_total")
    slope = linear_slope(queue_points)

    failures: list[str] = []
    if max(queue_values) > max_queue_lag_seconds:
        failures.append(
            f"queue lag {max(queue_values):.3f}s exceeds {max_queue_lag_seconds:.3f}s"
        )
    if slope > max_queue_slope:
        failures.append(
            f"queue slope {slope:.4f}s/s exceeds {max_queue_slope:.4f}s/s"
        )
    if steady_dropped > allow_dropped_segments:
        failures.append(
            "steady-state dropped media segments "
            f"{steady_dropped} exceeds {allow_dropped_segments}"
        )
    if timed_out:
        failures.append(f"profile timeouts observed: {timed_out}")
    if failed:
        failures.append(f"profile failures observed: {failed}")

    steady_resource_samples = [
        sample
        for sample in resource_samples
        if warmup_seconds <= sample.elapsed_seconds <= steady_end
    ]
    cpu_values = [
        sample.system_cpu_percent
        for sample in steady_resource_samples
        if sample.system_cpu_percent is not None
    ]
    rss_values = [
        sample.process_tree_rss_bytes
        for sample in steady_resource_samples
        if sample.process_tree_rss_bytes is not None
    ]
    ffmpeg_values = [
        sample.ffmpeg_processes
        for sample in steady_resource_samples
        if sample.ffmpeg_processes is not None
    ]
    return BenchmarkResult(
        configuration=configuration,
        warmup_seconds=warmup_seconds,
        steady_seconds=steady_seconds,
        cooldown_seconds=cooldown_seconds,
        sample_count=len(metric_samples),
        queue_lag_start_seconds=queue_values[0],
        queue_lag_end_seconds=queue_values[-1],
        queue_lag_after_cooldown_seconds=final.get(
            "queue_lag_seconds", queue_values[-1]
        ),
        queue_lag_max_seconds=max(queue_values),
        queue_lag_slope_seconds_per_second=slope,
        live_edge_lag_max_seconds=max(live_edge_values) if live_edge_values else None,
        work_completed=completed,
        work_completed_per_second=completed / steady_seconds,
        work_failed=failed,
        work_timed_out=timed_out,
        dropped_media_segments=dropped,
        coverage_gap_segments=coverage_gaps,
        warmup_dropped_media_segments=warmup_dropped,
        steady_dropped_media_segments=steady_dropped,
        cooldown_dropped_media_segments=cooldown_dropped,
        warmup_coverage_gap_segments=warmup_coverage_gaps,
        steady_coverage_gap_segments=steady_coverage_gaps,
        cooldown_coverage_gap_segments=cooldown_coverage_gaps,
        peak_active_media_processes=int(
            max(sample.values.get("peak_active_media_processes", 0.0) for sample in metric_samples)
        ),
        mean_system_cpu_percent=(statistics.fmean(cpu_values) if cpu_values else None),
        max_system_cpu_percent=max(cpu_values) if cpu_values else None,
        max_process_tree_rss_bytes=max(rss_values) if rss_values else None,
        max_ffmpeg_processes=max(ffmpeg_values) if ffmpeg_values else None,
        passed=not failures,
        failures=tuple(failures),
        warmup_metric_deltas=warmup_deltas,
        metric_deltas=deltas,
        cooldown_metric_deltas=cooldown_deltas,
        final_metrics=final,
        profile_execution_p95_max_seconds=_profile_gauge_maxima(
            steady_samples, "profile_execution_seconds_p95"
        ),
        executor_wait_p95_max_seconds=_profile_gauge_maxima(
            steady_samples, "executor_wait_seconds_p95"
        ),
        process_gate_wait_p95_max_seconds=_profile_gauge_maxima(
            steady_samples, "process_gate_wait_seconds_p95"
        ),
        environment=environment_snapshot(),
    )


def _stream_config(
    *,
    master_url: str,
    stream_id: str,
    configuration: BenchmarkConfiguration,
) -> StreamConfig:
    black, freeze, audio, macroblocking = _checks(configuration.checks)
    return StreamConfig(
        master_url=master_url,
        stream_id=stream_id,
        black_screen_enabled=black,
        video_freeze_enabled=freeze,
        audio_loss_enabled=audio,
        macroblocking_enabled=macroblocking,
        max_concurrent_media_processes=configuration.per_stream_process_limit,
        resource_limits={
            AnalysisResourceClass.VIDEO_DECODE: ResourcePoolLimit(
                configuration.video_workers, 128
            ),
            AnalysisResourceClass.AUDIO_DECODE: ResourcePoolLimit(
                configuration.audio_workers, 128
            ),
        },
        variant_selection=VariantSelectionPolicy(
            mode=VariantSelectionMode(configuration.variant_selection)
        ),
    )


def _delete_benchmark_namespace(
    application: MonitoringWorkerApplication,
    namespace: RedisNamespace,
) -> None:
    keys = list(
        application.redis_client.client.scan_iter(
            match=f"{namespace.prefix}:*"
        )
    )
    if keys:
        application.redis_client.client.delete(*keys)


def _sample_application(
    *,
    args: argparse.Namespace,
    application: MonitoringWorkerApplication,
    metrics_key: str,
    cooldown_seconds: float,
    stop_input: Event | None,
) -> tuple[list[MetricSample], list[ResourceSample]]:
    metric_samples: list[MetricSample] = []
    resource_samples: list[ResourceSample] = []
    start = time.monotonic()
    steady_start = start + args.warmup_seconds
    steady_end = steady_start + args.steady_seconds
    run_end = steady_end + cooldown_seconds
    while time.monotonic() < run_end:
        now = time.monotonic()
        if stop_input is not None and now >= steady_end:
            stop_input.set()
        elapsed = now - start
        raw = application.redis_client.client.hgetall(metrics_key)
        parsed = parse_redis_metrics(raw)
        if parsed:
            metric_samples.append(MetricSample(elapsed, parsed))
        resource_samples.append(_optional_resource_sample(elapsed))
        time.sleep(args.sample_interval)
    return metric_samples, resource_samples


def _ensure_fixture(case_name: str) -> Path:
    source = DEFAULT_OUTPUT / case_name
    if (source / "master.m3u8").is_file():
        return source
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "generate_monitoring_test_cases.py"),
        "--case",
        case_name,
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if completed.returncode != 0 or not (source / "master.m3u8").is_file():
        raise RuntimeError(f"Unable to generate benchmark fixture: {case_name}")
    return source


def _checks(name: str) -> tuple[bool, bool, bool, bool]:
    mapping = {
        "video": (True, True, False, True),
        "audio": (False, False, True, False),
        "all": (True, True, True, True),
    }
    return mapping[name]


def run_configuration(
    args: argparse.Namespace,
    configuration: BenchmarkConfiguration,
) -> BenchmarkResult:
    source = _ensure_fixture(args.case)
    namespace = RedisNamespace(
        f"{args.redis_prefix}:{uuid4().hex[:10]}"
    )
    redis_settings = RedisSettings(url=args.redis_url)
    stop_publisher = Event()
    publisher_errors: list[BaseException] = []

    HLS_ROOT.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix="capacity-benchmark-", dir=HLS_ROOT
    ) as temp_dir:
        root = Path(temp_dir)
        live = root / "live"
        handler = partial(QuietHandler, directory=str(root))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        def run_publisher() -> None:
            try:
                publish(
                    source=source,
                    output=live,
                    window_size=args.window_size,
                    retention_segments=args.retention_segments,
                    start_sequence=0,
                    loop=True,
                    reset=True,
                    max_publishes=None,
                    speed=configuration.publisher_speed,
                    stop_event=stop_publisher,
                )
            except BaseException as exc:
                publisher_errors.append(exc)

        publisher_thread = Thread(target=run_publisher, daemon=True)
        publisher_thread.start()
        deadline = time.monotonic() + 10.0
        while not (live / "master.m3u8").is_file():
            if publisher_errors:
                raise RuntimeError("Fixture publisher failed") from publisher_errors[0]
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for benchmark master playlist")
            time.sleep(0.05)

        app = MonitoringWorkerApplication(
            redis_settings=redis_settings,
            namespace=namespace,
            max_streams=1,
            max_concurrent_media_processes=configuration.global_process_limit,
            per_stream_media_processes=configuration.per_stream_process_limit,
            video_decode_workers=configuration.video_workers,
            audio_decode_workers=configuration.audio_workers,
            command_consumer_required=False,
        )
        app.ping()
        stream_id = f"capacity-{uuid4().hex[:10]}"
        config = _stream_config(
            master_url=(
                f"http://127.0.0.1:{server.server_address[1]}/live/master.m3u8"
            ),
            stream_id=stream_id,
            configuration=configuration,
        )
        identity = config.identity
        metrics_key = RuntimeRedisKeys(namespace).metrics(identity.storage_id)
        try:
            app.control.start(config)
            metric_samples, resource_samples = _sample_application(
                args=args,
                application=app,
                metrics_key=metrics_key,
                cooldown_seconds=args.cooldown_seconds,
                stop_input=stop_publisher,
            )
        finally:
            stop_publisher.set()
            app.stop_streams(timeout=max(10.0, args.cooldown_seconds + 5.0))
            try:
                _delete_benchmark_namespace(app, namespace)
            finally:
                app.close_redis()
            server.shutdown()
            server.server_close()
            publisher_thread.join(timeout=5.0)
            server_thread.join(timeout=5.0)

    if publisher_errors:
        raise RuntimeError("Fixture publisher failed") from publisher_errors[0]
    return summarize_run(
        configuration=configuration,
        warmup_seconds=args.warmup_seconds,
        steady_seconds=args.steady_seconds,
        cooldown_seconds=args.cooldown_seconds,
        metric_samples=metric_samples,
        resource_samples=resource_samples,
        max_queue_lag_seconds=args.max_queue_lag_seconds,
        max_queue_slope=args.max_queue_slope,
        allow_dropped_segments=args.allow_dropped_segments,
    )


def run_external_configuration(
    args: argparse.Namespace,
    configuration: BenchmarkConfiguration,
) -> BenchmarkResult:
    namespace = RedisNamespace(
        f"{args.redis_prefix}:{uuid4().hex[:10]}"
    )
    app = MonitoringWorkerApplication(
        redis_settings=RedisSettings(url=args.redis_url),
        namespace=namespace,
        max_streams=1,
        max_concurrent_media_processes=configuration.global_process_limit,
        per_stream_media_processes=configuration.per_stream_process_limit,
        video_decode_workers=configuration.video_workers,
        audio_decode_workers=configuration.audio_workers,
        command_consumer_required=False,
    )
    app.ping()
    config = _stream_config(
        master_url=args.url,
        stream_id=f"external-capacity-{uuid4().hex[:10]}",
        configuration=configuration,
    )
    metrics_key = RuntimeRedisKeys(namespace).metrics(
        config.identity.storage_id
    )
    try:
        app.control.start(config)
        metric_samples, resource_samples = _sample_application(
            args=args,
            application=app,
            metrics_key=metrics_key,
            cooldown_seconds=0.0,
            stop_input=None,
        )
    finally:
        app.stop_streams(timeout=30.0)
        try:
            _delete_benchmark_namespace(app, namespace)
        finally:
            app.close_redis()
    return summarize_run(
        configuration=configuration,
        warmup_seconds=args.warmup_seconds,
        steady_seconds=args.steady_seconds,
        cooldown_seconds=0.0,
        metric_samples=metric_samples,
        resource_samples=resource_samples,
        max_queue_lag_seconds=args.max_queue_lag_seconds,
        max_queue_slope=args.max_queue_slope,
        allow_dropped_segments=args.allow_dropped_segments,
    )


def _ints(value: str) -> list[int]:
    values = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    return values


def _floats(value: str) -> list[float]:
    try:
        values = [
            float(item.strip()) for item in value.split(",") if item.strip()
        ]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected comma-separated positive numbers"
        ) from exc
    if not values or any(
        not math.isfinite(item) or item <= 0 for item in values
    ):
        raise argparse.ArgumentTypeError(
            "expected comma-separated positive numbers"
        )
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark the production monitoring path with deterministic live HLS."
    )
    parser.add_argument("--case", default=DEFAULT_CASE)
    parser.add_argument(
        "--url",
        help=(
            "External master HLS URL. It is used in memory and is never "
            "written to benchmark reports."
        ),
    )
    parser.add_argument("--redis-url", default=os.getenv("REDIS_TEST_URL", "redis://localhost:6379/15"))
    parser.add_argument("--redis-prefix", default="media-monitor:benchmark")
    parser.add_argument("--checks", choices=("video", "audio", "all"), default="all")
    parser.add_argument("--video-workers", type=_ints, default=[4])
    parser.add_argument("--audio-workers", type=_ints, default=[1])
    parser.add_argument("--global-process-limits", type=_ints, default=[4])
    parser.add_argument("--per-stream-process-limit", type=int, default=4)
    parser.add_argument(
        "--variant-selection",
        choices=(
            VariantSelectionMode.HIGHEST_QUALITY.value,
            VariantSelectionMode.ALL.value,
            VariantSelectionMode.REPRESENTATIVE.value,
        ),
        default=VariantSelectionMode.HIGHEST_QUALITY.value,
    )
    parser.add_argument("--warmup-seconds", type=float, default=15.0)
    parser.add_argument("--steady-seconds", type=float, default=60.0)
    parser.add_argument("--cooldown-seconds", type=float, default=10.0)
    parser.add_argument("--sample-interval", type=float, default=1.0)
    parser.add_argument("--publisher-speeds", type=_floats, default=[1.0])
    parser.add_argument("--window-size", type=int, default=6)
    parser.add_argument("--retention-segments", type=int, default=12)
    parser.add_argument("--max-queue-lag-seconds", type=float, default=12.0)
    parser.add_argument("--max-queue-slope", type=float, default=0.05)
    parser.add_argument("--allow-dropped-segments", type=int, default=0)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "benchmark_results")
    return parser


def _validate(args: argparse.Namespace) -> None:
    positive = (
        "per_stream_process_limit",
        "warmup_seconds",
        "steady_seconds",
        "sample_interval",
        "window_size",
        "retention_segments",
        "max_queue_lag_seconds",
    )
    for name in positive:
        if getattr(args, name) <= 0:
            raise ValueError(f"--{name.replace('_', '-')} must be > 0")
    if args.cooldown_seconds < 0 or args.allow_dropped_segments < 0:
        raise ValueError("cooldown and allowed dropped segments must be >= 0")
    if args.url:
        parsed = urlsplit(args.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("--url must be an absolute HTTP(S) URL")
        if not parsed.path.lower().endswith(".m3u8"):
            raise ValueError("--url must point to an HLS .m3u8 playlist")
        if args.publisher_speeds != [1.0]:
            raise ValueError(
                "--publisher-speeds only applies to local fixtures"
            )


def _write_results(results: list[BenchmarkResult], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"capacity-benchmark-{stamp}.json"
    csv_path = output_dir / f"capacity-benchmark-{stamp}.csv"
    payload = [asdict(result) for result in results]
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    rows = []
    for result in results:
        row = asdict(result)
        configuration = row.pop("configuration")
        row.update(configuration)
        row["failures"] = "; ".join(row["failures"])
        row.pop("warmup_metric_deltas")
        row.pop("metric_deltas")
        row.pop("cooldown_metric_deltas")
        row.pop("final_metrics")
        row["profile_execution_p95_max_seconds"] = json.dumps(
            row["profile_execution_p95_max_seconds"], sort_keys=True
        )
        row["executor_wait_p95_max_seconds"] = json.dumps(
            row["executor_wait_p95_max_seconds"], sort_keys=True
        )
        row["process_gate_wait_p95_max_seconds"] = json.dumps(
            row["process_gate_wait_p95_max_seconds"], sort_keys=True
        )
        row["environment"] = json.dumps(row["environment"], sort_keys=True)
        rows.append(row)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _validate(args)
    configurations = [
        BenchmarkConfiguration(
            source_kind="external_live" if args.url else "local_fixture",
            checks=args.checks,
            video_workers=video_workers,
            audio_workers=audio_workers,
            global_process_limit=global_limit,
            per_stream_process_limit=min(args.per_stream_process_limit, global_limit),
            variant_selection=args.variant_selection,
            publisher_speed=publisher_speed,
        )
        for video_workers in args.video_workers
        for audio_workers in args.audio_workers
        for global_limit in args.global_process_limits
        for publisher_speed in args.publisher_speeds
    ]
    results: list[BenchmarkResult] = []
    for index, configuration in enumerate(configurations, start=1):
        print(f"[{index}/{len(configurations)}] {configuration}", flush=True)
        result = (
            run_external_configuration(args, configuration)
            if args.url
            else run_configuration(args, configuration)
        )
        results.append(result)
        print(
            f"  {'PASS' if result.passed else 'FAIL'} lag_max={result.queue_lag_max_seconds:.3f}s "
            f"slope={result.queue_lag_slope_seconds_per_second:.4f}s/s "
            f"completed={result.work_completed} "
            "dropped(warmup/steady/cooldown)="
            f"{result.warmup_dropped_media_segments}/"
            f"{result.steady_dropped_media_segments}/"
            f"{result.cooldown_dropped_media_segments}",
            flush=True,
        )
    json_path, csv_path = _write_results(results, args.output_dir)
    print(f"JSON report: {json_path}")
    print(f"CSV summary: {csv_path}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
