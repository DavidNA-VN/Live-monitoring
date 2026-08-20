from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from threading import Event
from time import monotonic

from core.analysis_profile import AnalysisProfile
from core.context import build_monitoring_context
from core.live_polling import (
    LivePlaylistPoller,
    PlaylistObservationTracker,
    RedisActiveVariantRegistry,
    calculate_poll_interval,
    calculate_playlist_staleness,
)
from core.metrics import RuntimeMetricCollector
from core.playlist_delta import PlaylistDeltaEngine
from core.profile_scheduler import ProfileScheduler
from core.redis_client import RedisUnavailableError
from core.runtime_health import RedisRuntimeHealthReporter
from core.segment_processor import SegmentProcessor
from core.segment_state import RedisSegmentStateStore
from models.runtime import LiveCycleStats
from models.stream import StreamIdentity
from playlist.errors import PlaylistLoadError


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveRuntimeSettings:
    playlist_timeout: float = 5.0
    poll_factor: float = 0.5
    min_poll_interval: float = 0.5
    max_poll_interval: float = 5.0
    error_retry_interval: float = 1.0
    max_workers: int = 4
    max_pending_tasks: int = 16
    max_admitted_work: int = 2048
    max_work_age_seconds: float = 120.0
    max_segments_per_batch: int = 20
    media_playlist_workers: int = 4
    request_headers: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        positive = {
            "playlist_timeout": self.playlist_timeout,
            "poll_factor": self.poll_factor,
            "min_poll_interval": self.min_poll_interval,
            "error_retry_interval": self.error_retry_interval,
            "max_workers": self.max_workers,
            "max_admitted_work": self.max_admitted_work,
            "max_work_age_seconds": self.max_work_age_seconds,
            "max_segments_per_batch": self.max_segments_per_batch,
            "media_playlist_workers": self.media_playlist_workers,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.max_pending_tasks < 0:
            raise ValueError("max_pending_tasks must be >= 0")
        if self.max_poll_interval < self.min_poll_interval:
            raise ValueError(
                "max_poll_interval must be >= min_poll_interval"
            )


class LiveMonitoringRuntime:
    def __init__(
        self,
        stream: StreamIdentity,
        state_store: RedisSegmentStateStore,
        processors: list[SegmentProcessor],
        analysis_profiles: list[AnalysisProfile],
        health_reporter: RedisRuntimeHealthReporter | None = None,
        settings: LiveRuntimeSettings | None = None,
        delta_engine: PlaylistDeltaEngine | None = None,
    ) -> None:
        self.stream = stream
        self.health_reporter = health_reporter
        self.settings = settings or LiveRuntimeSettings()
        self.poller = LivePlaylistPoller(
            timeout=self.settings.playlist_timeout,
            media_playlist_workers=self.settings.media_playlist_workers,
            request_headers=self.settings.request_headers,
            loader=build_monitoring_context,
        )
        self.observations = PlaylistObservationTracker(delta_engine)
        self.variant_registry = RedisActiveVariantRegistry(
            stream_id=stream.stream_id,
            redis_client=state_store.redis,
            key_builder=state_store.key_builder,
        )
        self.metrics = RuntimeMetricCollector()
        self.profile_scheduler = ProfileScheduler(
            stream=stream,
            state_store=state_store,
            processors=processors,
            analysis_profiles=analysis_profiles,
            max_workers=self.settings.max_workers,
            max_pending_tasks=self.settings.max_pending_tasks,
            max_admitted_work=self.settings.max_admitted_work,
            max_work_age_seconds=self.settings.max_work_age_seconds,
            max_segments_per_batch=self.settings.max_segments_per_batch,
            metrics=self.metrics,
        )
        self.stop_event = Event()

    def run_forever(self) -> None:
        logger.info("Live monitoring runtime started.")
        try:
            while not self.stop_event.is_set():
                cycle_start = monotonic()
                try:
                    stats = self.run_cycle()
                    if self.health_reporter is not None:
                        self.health_reporter.publish(stats)
                    poll_interval = stats.poll_interval
                except PlaylistLoadError as exc:
                    logger.warning(
                        "Master playlist poll failed: %s",
                        exc,
                        extra={
                            "event_name": "master_playlist_unavailable",
                            "stream_id": self.stream.stream_id,
                        },
                    )
                    if self.health_reporter is not None:
                        try:
                            self.health_reporter.publish_failure(
                                "master_playlist_unavailable"
                            )
                        except RedisUnavailableError:
                            pass
                    poll_interval = self.settings.error_retry_interval
                except RedisUnavailableError as exc:
                    logger.error("Redis unavailable: %s", exc)
                    poll_interval = self.settings.error_retry_interval
                elapsed = monotonic() - cycle_start
                self.stop_event.wait(max(0.0, poll_interval - elapsed))
        finally:
            self.profile_scheduler.shutdown(wait=True)
            logger.info("Live monitoring runtime stopped.")

    def run_cycle(self) -> LiveCycleStats:
        stats = LiveCycleStats(started_at=datetime.now(timezone.utc))
        self.profile_scheduler.dispatch_pending(stats=stats)
        poll_started = monotonic()
        context = self.poller.poll(self.stream)
        stats.playlist_fetch_latency_seconds = monotonic() - poll_started
        stats.playlist_staleness_seconds = calculate_playlist_staleness(
            context
        )
        self.variant_registry.refresh(context)
        stats.variant_count = len(context.variants)
        stats.successful_snapshots = len(context.snapshots_by_variant)
        stats.failed_snapshots = len(context.snapshot_errors_by_variant)
        for variant_id, error in context.snapshot_errors_by_variant.items():
            logger.warning(
                "Media playlist unavailable variant=%s error=%s",
                variant_id,
                error,
                extra={
                    "event_name": "media_playlist_unavailable",
                    "stream_id": self.stream.stream_id,
                    "variant_id": variant_id,
                },
            )
        for variant in context.variants:
            snapshot = context.snapshot_for_variant(variant)
            if snapshot is None:
                continue
            self.observations.observe(snapshot=snapshot, stats=stats)
            self.profile_scheduler.admit_snapshot(
                snapshot=snapshot,
                stats=stats,
            )
        self.profile_scheduler.dispatch_pending(stats=stats)
        worker_metrics = self.metrics.drain()
        stats.analysis_count = worker_metrics.analysis_count
        stats.analysis_duration_seconds_total = (
            worker_metrics.analysis_duration_seconds_total
        )
        stats.segment_age_seconds_max = (
            worker_metrics.segment_age_seconds_max
        )
        stats.retry_total = worker_metrics.retry_total
        stats.ffmpeg_timeout_total = worker_metrics.ffmpeg_timeout_total
        stats.poll_interval = calculate_poll_interval(
            context,
            poll_factor=self.settings.poll_factor,
            minimum=self.settings.min_poll_interval,
            maximum=self.settings.max_poll_interval,
            fallback=self.settings.error_retry_interval,
        )
        stats.finished_at = datetime.now(timezone.utc)
        return stats

    def stop(self) -> None:
        self.stop_event.set()
