import argparse
import logging
import signal
from threading import Event, Thread

from app.monitoring_session_factory import MonitoringSessionFactory
from core.redis_client import RedisClient
from core.stream_session import StreamSessionStatus
from core.stream_supervisor import StreamSupervisor
from models.stream_config import StreamConfig
from reporting.live_console import LiveAlertConsole


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url", required=True, help="Live HLS master playlist URL"
    )
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
    return parser.parse_args(argv)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s %(levelname)s %(name)s - %(message)s"
        ),
    )
    args = parse_args()
    shutdown_event = Event()
    supervisor = StreamSupervisor(
        session_factory=MonitoringSessionFactory(
            max_concurrent_media_processes=(
                args.max_service_media_processes
            )
        ),
        max_streams=1,
    )
    console_client = None
    console_thread = None
    console_stop = Event()

    def shutdown_handler(_signum, _frame):
        shutdown_event.set()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        stream_id = supervisor.add(
            StreamConfig(
                master_url=args.url,
                stream_id=args.stream_id,
                black_screen_enabled=(
                    not args.disable_black_screen
                ),
                audio_loss_enabled=(
                    not args.disable_audio_loss
                ),
                silence_threshold_dbfs=(
                    args.silence_threshold_dbfs
                ),
                audio_loss_duration=args.audio_loss_duration,
                audio_track_index=args.audio_track_index,
                max_concurrent_media_processes=args.max_media_processes,
            )
        )
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

        while not shutdown_event.wait(1.0):
            status = supervisor.snapshots()[stream_id].status
            if status in (
                StreamSessionStatus.FAILED,
                StreamSessionStatus.STOPPED,
            ):
                break
    finally:
        supervisor.stop_all()
        console_stop.set()
        if console_thread is not None:
            console_thread.join(timeout=2.0)
        if console_client is not None:
            console_client.close()


if __name__ == "__main__":
    main()
