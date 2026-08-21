from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import logging
from typing import Protocol

import redis

from core.context import MonitoringContext, build_monitoring_context
from core.playlist_delta import PlaylistDeltaEngine
from core.redis_client import RedisUnavailableError
from core.redis_keys import RuntimeRedisKeys
from models.playlist_snapshot import MediaPlaylistSnapshot
from models.playlist_delta import MediaPlaylistDelta
from models.runtime import LiveCycleStats
from models.stream import StreamIdentity


logger = logging.getLogger(__name__)


class TimelineGenerationStore(Protocol):
    def get_timeline_generation(
        self,
        *,
        stream_id: str,
        variant_stable_id: str,
    ) -> int:
        ...

    def advance_timeline_generation(
        self,
        *,
        stream_id: str,
        variant_stable_id: str,
        expected_generation: int,
    ) -> int:
        ...


class InMemoryTimelineGenerationStore:
    def __init__(self) -> None:
        self.generations: dict[tuple[str, str], int] = {}

    def get_timeline_generation(
        self,
        *,
        stream_id: str,
        variant_stable_id: str,
    ) -> int:
        return self.generations.get((stream_id, variant_stable_id), 0)

    def advance_timeline_generation(
        self,
        *,
        stream_id: str,
        variant_stable_id: str,
        expected_generation: int,
    ) -> int:
        key = (stream_id, variant_stable_id)
        current = self.generations.get(key, 0)
        if current == expected_generation:
            current += 1
            self.generations[key] = current
        return current


@dataclass(frozen=True)
class PlaylistObservation:
    snapshot: MediaPlaylistSnapshot
    timeline_generation: int
    delta: MediaPlaylistDelta | None = None


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
        *,
        stream_id: str = "local",
        generation_store: TimelineGenerationStore | None = None,
    ) -> None:
        self.delta_engine = delta_engine or PlaylistDeltaEngine()
        self.stream_id = stream_id
        self.generation_store = (
            generation_store or InMemoryTimelineGenerationStore()
        )
        self.previous_snapshots: dict[str, MediaPlaylistSnapshot] = {}
        self.generations: dict[str, int] = {}

    def observe(
        self,
        *,
        snapshot: MediaPlaylistSnapshot,
        stats: LiveCycleStats,
    ) -> PlaylistObservation:
        variant_id = snapshot.variant_stable_id
        previous = self.previous_snapshots.get(variant_id)
        generation = self.generations.get(variant_id)
        persisted_generation = self.generation_store.get_timeline_generation(
            stream_id=self.stream_id,
            variant_stable_id=variant_id,
        )
        if generation is None:
            generation = persisted_generation

        delta = None
        if previous is not None:
            delta = self.delta_engine.compare(previous, snapshot)
            if delta.timeline_reset:
                generation = (
                    self.generation_store.advance_timeline_generation(
                        stream_id=self.stream_id,
                        variant_stable_id=variant_id,
                        expected_generation=generation,
                    )
                )
            elif persisted_generation > generation:
                generation = persisted_generation
                delta.timeline_reset = True
                delta.timeline_reset_reason = "generation_advanced"
                delta.new_segments = list(snapshot.segments)
                delta.retained_segments = []
                delta.replaced_segments = []

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

        enriched = self._enrich_snapshot(
            snapshot=snapshot,
            previous=previous,
            delta=delta,
            generation=generation,
        )
        self.previous_snapshots[variant_id] = enriched
        self.generations[variant_id] = generation
        return PlaylistObservation(
            snapshot=enriched,
            timeline_generation=generation,
            delta=delta,
        )

    def _enrich_snapshot(
        self,
        *,
        snapshot: MediaPlaylistSnapshot,
        previous: MediaPlaylistSnapshot | None,
        delta: MediaPlaylistDelta | None,
        generation: int,
    ) -> MediaPlaylistSnapshot:
        previous_by_sequence = {
            segment.sequence: segment
            for segment in (previous.segments if previous is not None else [])
        }
        retained_sequences = (
            {segment.sequence for segment in delta.retained_segments}
            if delta is not None and not delta.timeline_reset
            else set()
        )
        segments = []
        for segment in snapshot.segments:
            previous_segment = previous_by_sequence.get(segment.sequence)
            if (
                previous_segment is not None
                and segment.sequence in retained_sequences
                and previous_segment.timeline_generation == generation
            ):
                revision = previous_segment.media_revision
            else:
                revision = self.delta_engine.media_revision(segment)
            segments.append(
                replace(
                    segment,
                    timeline_generation=generation,
                    media_revision=revision,
                )
            )
        return replace(snapshot, segments=segments)


class RedisActiveVariantRegistry:
    def __init__(
        self,
        *,
        stream_id: str,
        redis_client,
        runtime_keys: RuntimeRedisKeys,
        ttl_seconds: int = 60,
    ) -> None:
        self.stream_id = stream_id
        self.redis = redis_client
        self.keys = runtime_keys
        self.ttl_seconds = ttl_seconds

    def refresh(self, context: MonitoringContext) -> None:
        key = self.keys.active_variants(self.stream_id)
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
