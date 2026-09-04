import argparse
import logging
import os
from threading import Event, Thread

from app.monitoring_worker import MonitoringWorkerApplication
from app.monitoring_worker_runner import MonitoringWorkerRunner
from core.redis_client import RedisClient
from core.redis_keys import RedisNamespace
from models.stream_config import StreamConfig
from models.admission import LiveAdmissionPolicy, StartupAdmissionMode
from reporting.live_console import LiveAlertConsole


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="Optional initial live HLS master URL")
    parser.add_argument(
        "--stream-id",
        default=None,
        help="Stable business/channel identity",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="Enable the optional debug alert consumer",
    )
    parser.add_argument(
        "--disable-black-screen",
        action="store_true",
        help="Disable black-screen monitoring",
    )
    parser.add_argument(
        "--disable-audio-loss",
        action="store_true",
        help="Disable audio-loss monitoring",
    )
    parser.add_argument(
        "--enable-video-freeze",
        action="store_true",
        help="Enable video-freeze monitoring",
    )
    parser.add_argument(
        "--freeze-noise-db",
        type=float,
        default=-60.0,
        help="FFmpeg freezedetect noise tolerance in dB",
    )
    parser.add_argument(
        "--freeze-detector-minimum-duration",
        type=float,
        default=0.2,
        help="Minimum FFmpeg freeze candidate duration in seconds",
    )
    parser.add_argument(
        "--freeze-warning-duration",
        type=float,
        default=3.0,
        help="Video-freeze warning duration in seconds",
    )
    parser.add_argument(
        "--freeze-alert-duration",
        type=float,
        default=5.0,
        help="Video-freeze alert duration in seconds",
    )
    parser.add_argument(
        "--silence-threshold-dbfs",
        type=float,
        default=-60.0,
        help="All-channel silence threshold in dBFS",
    )
    parser.add_argument(
        "--audio-loss-duration",
        type=float,
        default=30.0,
        help="Continuous audio-loss alert duration in seconds",
    )
    parser.add_argument(
        "--audio-track-index",
        type=int,
        default=0,
        help="Zero-based muxed audio track index",
    )
    parser.add_argument(
        "--max-media-processes",
        type=int,
        default=4,
        help="Per-stream concurrent FFmpeg process budget",
    )
    parser.add_argument(
        "--max-service-media-processes",
        type=int,
        default=8,
        help="Service-wide concurrent FFmpeg process budget",
    )
    parser.add_argument(
        "--video-decode-workers",
        type=int,
        default=4,
        help="Video analysis executor workers per stream",
    )
    parser.add_argument(
        "--audio-decode-workers",
        type=int,
        default=1,
        help="Audio analysis executor workers per stream",
    )
    parser.add_argument(
        "--startup-mode",
        choices=[mode.value for mode in StartupAdmissionMode],
        default=StartupAdmissionMode.BOUNDED_HISTORY.value,
        help="Initial playlist admission policy",
    )
    parser.add_argument(
        "--startup-lookback-segments",
        type=int,
        default=4,
        help="Newest segments admitted per variant at startup/reset",
    )
    parser.add_argument(
        "--command-worker",
        action="store_true",
        help="Consume monitoring lifecycle commands from Redis",
    )
    parser.add_argument("--max-streams", type=int, default=16)
    parser.add_argument("--redis-prefix", default="media-monitor:v1")
    parser.add_argument("--consumer-name", default=None)
    parser.add_argument(
        "--worker-id",
        default=None,
        help="Explicit unique worker identity (e.g. worker-local-01)",
    )
    parser.add_argument(
        "--worker-version",
        default=os.getenv("WORKER_VERSION", "dev"),
        help="Worker release version (default: WORKER_VERSION env or 'dev')",
    )
    parser.add_argument(
        "--projection-interval",
        type=float,
        default=2.0,
        help="Runtime status projection cycle interval in seconds",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=5.0,
        help="Worker heartbeat refresh interval in seconds",
    )
    parser.add_argument(
        "--heartbeat-ttl",
        type=int,
        default=15,
        help="Worker heartbeat Redis key TTL in seconds",
    )
    parser.add_argument(
        "--worker-discovery-window",
        type=int,
        default=30,
        help="Active worker discovery window retention in seconds",
    )
    parser.add_argument(
        "--shutdown-timeout",
        type=float,
        default=30.0,
        help="Overall graceful shutdown deadline in seconds",
    )
    args = parser.parse_args(argv)
    if not args.url and not args.command_worker:
        parser.error("one of --url or --command-worker is required")
    if args.max_streams <= 0:
        parser.error("--max-streams must be > 0")
    if args.max_media_processes <= 0:
        parser.error("--max-media-processes must be > 0")
    if args.max_service_media_processes <= 0:
        parser.error("--max-service-media-processes must be > 0")
    if args.max_media_processes > args.max_service_media_processes:
        parser.error(
            "--max-media-processes must be <= --max-service-media-processes"
        )
    if args.video_decode_workers <= 0:
        parser.error("--video-decode-workers must be > 0")
    if args.audio_decode_workers <= 0:
        parser.error("--audio-decode-workers must be > 0")
    if args.startup_lookback_segments <= 0:
        parser.error("--startup-lookback-segments must be > 0")
    if args.projection_interval <= 0:
        parser.error("--projection-interval must be > 0")
    if args.heartbeat_interval <= 0:
        parser.error("--heartbeat-interval must be > 0")
    if args.heartbeat_ttl <= args.heartbeat_interval:
        parser.error("--heartbeat-ttl must be > --heartbeat-interval")
    if args.worker_discovery_window < args.heartbeat_ttl:
        parser.error("--worker-discovery-window must be >= --heartbeat-ttl")
    if not args.worker_version or not args.worker_version.strip():
        parser.error("--worker-version must not be empty")
    if args.shutdown_timeout <= 0:
        parser.error("--shutdown-timeout must be > 0")
    return args


def main():
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s %(levelname)s %(name)s - %(message)s"
        ),
    )
    args = parse_args()
    application = MonitoringWorkerApplication(
        namespace=RedisNamespace(args.redis_prefix),
        max_streams=args.max_streams,
        max_concurrent_media_processes=args.max_service_media_processes,
        per_stream_media_processes=args.max_media_processes,
        video_decode_workers=args.video_decode_workers,
        audio_decode_workers=args.audio_decode_workers,
        consumer_name=args.consumer_name,
        worker_id=args.worker_id,
        worker_version=args.worker_version,
        command_consumer_required=args.command_worker,
        projection_interval=args.projection_interval,
        heartbeat_interval=args.heartbeat_interval,
        heartbeat_ttl=args.heartbeat_ttl,
        worker_discovery_window=args.worker_discovery_window,
    )
    console_client = None
    console_thread = None
    console_stop = Event()
    runner = MonitoringWorkerRunner(
        application=application,
        command_worker=args.command_worker,
        shutdown_timeout=args.shutdown_timeout,
    )

    try:
        if args.url:
            application.ping()
            result = application.control.start(
                StreamConfig(
                    master_url=args.url,
                    stream_id=args.stream_id,
                    black_screen_enabled=not args.disable_black_screen,
                    video_freeze_enabled=args.enable_video_freeze,
                    freeze_noise_db=args.freeze_noise_db,
                    freeze_detector_minimum_duration=(
                        args.freeze_detector_minimum_duration
                    ),
                    freeze_warning_duration=args.freeze_warning_duration,
                    freeze_alert_duration=args.freeze_alert_duration,
                    audio_loss_enabled=not args.disable_audio_loss,
                    silence_threshold_dbfs=args.silence_threshold_dbfs,
                    audio_loss_duration=args.audio_loss_duration,
                    audio_track_index=args.audio_track_index,
                    max_concurrent_media_processes=args.max_media_processes,
                    admission_policy=LiveAdmissionPolicy(
                        startup_mode=StartupAdmissionMode(args.startup_mode),
                        startup_lookback_segments=(
                            args.startup_lookback_segments
                        ),
                    ),
                )
            )
            runner.stream_id = result.stream_id

        if args.console:
            console_client = RedisClient()
            console_client.ping()
            console = LiveAlertConsole(redis_client=console_client)
            console_thread = Thread(
                target=console.run,
                args=(console_stop,),
                name="live-alert-console",
                daemon=True,
            )
            console_thread.start()

        return runner.run(install_signal_handlers=True)
    finally:
        runner.shutdown()
        console_stop.set()
        if console_thread is not None:
            console_thread.join(timeout=2.0)
        if console_client is not None:
            console_client.close()

if __name__ == "__main__":
    raise SystemExit(main())
