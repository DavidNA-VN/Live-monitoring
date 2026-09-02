from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum

from models.freeze import (
    FreezeInterval,
    VideoFreezeDetectionResult,
    VideoFreezeEventStatus,
    VideoFreezeLiveEvent,
)
from models.segment import Segment


class VideoFreezeEventTransitionType(str, Enum):
    PERSIST_OPEN = "persist_open"
    RESOLVE = "resolve"
    MARK_COMMITTED = "mark_committed"


@dataclass(frozen=True)
class VideoFreezeEventTransition:
    type: VideoFreezeEventTransitionType
    event: VideoFreezeLiveEvent | None = None
    reason: str | None = None
    commits_segment: bool = False

    def __post_init__(self) -> None:
        needs_event = self.type in (
            VideoFreezeEventTransitionType.PERSIST_OPEN,
            VideoFreezeEventTransitionType.RESOLVE,
        )
        if needs_event and self.event is None:
            raise ValueError(f"{self.type.value} transition requires an event")
        if self.type is VideoFreezeEventTransitionType.RESOLVE and not self.reason:
            raise ValueError("resolve transition requires a reason")


class VideoFreezeEventReducer:
    def __init__(
        self,
        *,
        storage_id: str,
        external_stream_id: str,
        boundary_tolerance: float = 0.10,
    ) -> None:
        if not math.isfinite(boundary_tolerance) or boundary_tolerance < 0:
            raise ValueError("boundary_tolerance must be finite and >= 0")
        self.storage_id = storage_id
        self.external_stream_id = external_stream_id
        self.boundary_tolerance = boundary_tolerance

    def reduce(
        self,
        *,
        open_event: VideoFreezeLiveEvent | None,
        segment: Segment,
        result: VideoFreezeDetectionResult,
    ) -> list[VideoFreezeEventTransition]:
        if not result.checked:
            return []

        transitions: list[VideoFreezeEventTransition] = []
        current = self._copy_event(open_event)
        if current is not None:
            current.stream_id = self.external_stream_id

        if current is not None and not self._sequence_can_follow(
            event=current,
            segment=segment,
        ):
            transitions.append(
                self._resolve(current, reason="observation_gap")
            )
            current = None

        intervals = sorted(result.freeze_intervals, key=lambda item: item.start)
        if not intervals:
            if current is None:
                transitions.append(
                    VideoFreezeEventTransition(
                        type=VideoFreezeEventTransitionType.MARK_COMMITTED,
                        commits_segment=True,
                    )
                )
            else:
                transitions.append(
                    self._resolve(
                        current,
                        reason="video_returned",
                        commits_segment=True,
                    )
                )
            return transitions

        for index, interval in enumerate(intervals):
            is_last = index == len(intervals) - 1
            if current is None:
                current = self._new_event(segment=segment, interval=interval)
            elif self._can_continue(
                event=current,
                segment=segment,
                interval=interval,
            ):
                self._extend_event(
                    event=current,
                    segment=segment,
                    interval=interval,
                )
            else:
                transitions.append(
                    self._resolve(current, reason="video_returned")
                )
                current = self._new_event(segment=segment, interval=interval)

            if self._ends_before_segment_end(
                segment=segment,
                interval=interval,
            ):
                transitions.append(
                    self._resolve(
                        current,
                        reason="video_returned",
                        commits_segment=is_last,
                    )
                )
                current = None
            else:
                transitions.append(
                    VideoFreezeEventTransition(
                        type=VideoFreezeEventTransitionType.PERSIST_OPEN,
                        event=self._copy_event(current),
                        commits_segment=is_last,
                    )
                )
        return transitions

    @staticmethod
    def _copy_event(
        event: VideoFreezeLiveEvent | None,
    ) -> VideoFreezeLiveEvent | None:
        return replace(event) if event is not None else None

    def _resolve(
        self,
        event: VideoFreezeLiveEvent,
        *,
        reason: str,
        commits_segment: bool = False,
    ) -> VideoFreezeEventTransition:
        resolved = self._copy_event(event)
        if resolved is None:
            raise RuntimeError("cannot resolve an empty video-freeze event")
        resolved.status = VideoFreezeEventStatus.RESOLVED
        resolved.resolution_reason = reason
        return VideoFreezeEventTransition(
            type=VideoFreezeEventTransitionType.RESOLVE,
            event=resolved,
            reason=reason,
            commits_segment=commits_segment,
        )

    @staticmethod
    def _sequence_can_follow(
        *,
        event: VideoFreezeLiveEvent,
        segment: Segment,
    ) -> bool:
        if event.variant_stable_id != segment.variant_stable_id:
            return False
        if event.timeline_generation != segment.timeline_generation:
            return False
        if event.discontinuity_sequence != segment.discontinuity_sequence:
            return False
        if segment.sequence == event.end_sequence:
            return event.last_media_revision == segment.media_revision
        return segment.sequence == event.end_sequence + 1

    def _can_continue(
        self,
        *,
        event: VideoFreezeLiveEvent,
        segment: Segment,
        interval: FreezeInterval,
    ) -> bool:
        if not self._sequence_can_follow(event=event, segment=segment):
            return False
        if segment.sequence == event.end_sequence:
            return interval.start <= event.end_offset + self.boundary_tolerance
        return (
            event.end_offset
            >= event.last_segment_duration - self.boundary_tolerance
            and interval.start <= self.boundary_tolerance
        )

    def _new_event(
        self,
        *,
        segment: Segment,
        interval: FreezeInterval,
    ) -> VideoFreezeLiveEvent:
        return VideoFreezeLiveEvent(
            event_id=self._event_id(
                segment=segment,
                start_offset=interval.start,
            ),
            stream_id=self.external_stream_id,
            variant_id=segment.variant_id,
            variant_stable_id=segment.variant_stable_id,
            timeline_generation=segment.timeline_generation,
            discontinuity_sequence=segment.discontinuity_sequence,
            start_sequence=segment.sequence,
            end_sequence=segment.sequence,
            start_offset=interval.start,
            end_offset=interval.end,
            start_program_time=self._program_time(segment, interval.start),
            end_program_time=self._program_time(segment, interval.end),
            duration=interval.duration,
            last_segment_duration=segment.duration,
            start_media_revision=segment.media_revision,
            last_media_revision=segment.media_revision,
        )

    @staticmethod
    def _extend_event(
        *,
        event: VideoFreezeLiveEvent,
        segment: Segment,
        interval: FreezeInterval,
    ) -> None:
        is_new_segment = segment.sequence != event.end_sequence
        if is_new_segment:
            added_duration = interval.duration
        else:
            added_duration = max(
                0.0,
                interval.end - max(event.end_offset, interval.start),
            )
        event.duration += added_duration
        event.end_sequence = segment.sequence
        event.end_offset = interval.end
        event.end_program_time = VideoFreezeEventReducer._program_time(
            segment,
            interval.end,
        )
        event.last_segment_duration = segment.duration
        event.last_media_revision = segment.media_revision
        if is_new_segment:
            event.affected_segment_count += 1

    def _ends_before_segment_end(
        self,
        *,
        segment: Segment,
        interval: FreezeInterval,
    ) -> bool:
        return interval.end < segment.duration - self.boundary_tolerance

    def _event_id(
        self,
        *,
        segment: Segment,
        start_offset: float,
    ) -> str:
        raw = (
            f"{self.storage_id}|"
            f"{segment.variant_stable_id}|"
            f"{segment.timeline_generation}|"
            f"{segment.discontinuity_sequence}|"
            f"{segment.sequence}|"
            f"{segment.media_revision}|"
            f"{start_offset:.6f}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _program_time(
        segment: Segment,
        offset: float,
    ) -> datetime | None:
        if segment.program_date_time is None:
            return None
        return segment.program_date_time + timedelta(seconds=offset)
