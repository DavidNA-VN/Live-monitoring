from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import logging

import redis

from core.context import MonitoringContext, build_monitoring_context
from core.playlist_delta import PlaylistDeltaEngine
from core.redis_client import RedisUnavailableError
from core.redis_keys import RedisKeyBuilder
from models.playlist_snapshot import MediaPlaylistSnapshot
from models.runtime import LiveCycleStats
from models.stream import StreamIdentity


logger = logging.getLogger(__name__)


class LivePlaylistPoller:
    def __init__(
        self,
        *,
        timeout: float,
        media_playlist_workers: int,
        request_headers: Mapping[str, str] | None = None,
        loader: Callable[..., MonitoringContext] = build_monitoring_context,
    ) -> None:
        self.timeout = timeout
        self.media_playlist_workers = media_playlist_workers
        self.request_headers = request_headers
        self.loader = loader

    def poll(self, stream: StreamIdentity) -> MonitoringContext:
        return self.loader(
            master_url=stream.master_url,
            playlist_timeout=self.timeout,
            request_headers=self.request_headers,
            media_playlist_workers=self.media_playlist_workers,
        )


class PlaylistObservationTracker:
    def __init__(
        self,
        delta_engine: PlaylistDeltaEngine | None = None,
    ) -> None:
        self.delta_engine = delta_engine or PlaylistDeltaEngine()
        self.previous_snapshots: dict[str, MediaPlaylistSnapshot] = {}

    def observe(
        self,
        *,
        snapshot: MediaPlaylistSnapshot,
        stats: LiveCycleStats,
    ) -> None:
        previous = self.previous_snapshots.get(snapshot.variant_stable_id)
        if previous is not None:
            delta = self.delta_engine.compare(previous, snapshot)
            stats.declared_gap_count += len(delta.declared_gap_segments)
            stats.missed_sequence_count += sum(
                item.count for item in delta.missed_sequence_ranges
            )
            stats.timeline_reset_count += int(delta.timeline_reset)
            stats.timeline_conflict_count += int(delta.timeline_conflict)
            if delta.timeline_reset:
                logger.warning(
                    "Playlist timeline reset variant=%s reason=%s",
                    snapshot.variant_id,
                    delta.timeline_reset_reason,
                )
            if delta.timeline_conflict:
                logger.warning(
                    "Playlist timeline conflict variant=%s",
                    snapshot.variant_id,
                )
            for missed in delta.missed_sequence_ranges:
                logger.warning(
                    "Playlist observation gap variant=%s seq=%s->%s",
                    snapshot.variant_id,
                    missed.start_sequence,
                    missed.end_sequence,
                )
        self.previous_snapshots[snapshot.variant_stable_id] = snapshot


class RedisActiveVariantRegistry:
    def __init__(
        self,
        *,
        stream_id: str,
        redis_client,
        key_builder: RedisKeyBuilder,
        ttl_seconds: int = 60,
    ) -> None:
        self.stream_id = stream_id
        self.redis = redis_client
        self.keys = key_builder
        self.ttl_seconds = ttl_seconds

    def refresh(self, context: MonitoringContext) -> None:
        key = self.keys.runtime_active_variants(self.stream_id)
        mapping = {
            snapshot.variant_stable_id: snapshot.variant_id
            for snapshot in context.snapshots_by_variant.values()
        }
        try:
            pipeline = self.redis.pipeline(transaction=True)
            pipeline.delete(key)
            if mapping:
                pipeline.hset(key, mapping=mapping)
            pipeline.expire(key, self.ttl_seconds)
            pipeline.execute()
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                f"Unable to refresh active variant registry: {exc}"
            ) from exc


def calculate_poll_interval(
    context: MonitoringContext,
    *,
    poll_factor: float,
    minimum: float,
    maximum: float,
    fallback: float,
) -> float:
    targets = [
        snapshot.target_duration
        for snapshot in context.snapshots_by_variant.values()
        if snapshot.target_duration is not None
        and snapshot.target_duration > 0
    ]
    if not targets:
        return fallback
    return max(minimum, min(maximum, min(targets) * poll_factor))


def calculate_playlist_staleness(context: MonitoringContext) -> float:
    latest_end = None
    for snapshot in context.snapshots_by_variant.values():
        for segment in snapshot.segments:
            if segment.program_date_time is None:
                continue
            end = segment.program_date_time.timestamp() + segment.duration
            latest_end = end if latest_end is None else max(latest_end, end)
    if latest_end is None:
        return 0.0
    return max(0.0, datetime.now(timezone.utc).timestamp() - latest_end)
