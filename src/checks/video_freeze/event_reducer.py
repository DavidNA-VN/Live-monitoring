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
from core.frame_similarity import (
    FrameComparisonStatus,
    FrameFingerprintMatcher,
    NormalizedMaeFrameMatcher,
)


class VideoFreezeEventTransitionType(str, Enum):
    PERSIST_OPEN = "persist_open"
    CLOSE_EVENT = "close_event"
    RESOLVE = "resolve"
    MARK_COMMITTED = "mark_committed"
    HEALTHY_EVIDENCE = "healthy_evidence"
    UNKNOWN_GAP = "unknown_gap"


@dataclass(frozen=True)
class VideoFreezeEventTransition:
    type: VideoFreezeEventTransitionType
    event: VideoFreezeLiveEvent | None = None
    reason: str | None = None
    commits_segment: bool = False

    def __post_init__(self) -> None:
        if self.type in (
            VideoFreezeEventTransitionType.PERSIST_OPEN,
            VideoFreezeEventTransitionType.CLOSE_EVENT,
            VideoFreezeEventTransitionType.RESOLVE,
        ) and self.event is None:
            raise ValueError(f"{self.type.value} transition requires an event")
        if self.type in (
            VideoFreezeEventTransitionType.CLOSE_EVENT,
            VideoFreezeEventTransitionType.RESOLVE,
            VideoFreezeEventTransitionType.UNKNOWN_GAP,
        ) and not self.reason:
            raise ValueError("close/unknown transition requires a reason")


class VideoFreezeEventReducer:
    def __init__(
        self,
        *,
        storage_id: str,
        external_stream_id: str,
        boundary_tolerance: float = 0.10,
        frame_matcher: FrameFingerprintMatcher | None = None,
    ) -> None:
        if not math.isfinite(boundary_tolerance) or boundary_tolerance < 0:
            raise ValueError("boundary_tolerance must be finite and >= 0")
        self.storage_id = storage_id
        self.external_stream_id = external_stream_id
        self.boundary_tolerance = boundary_tolerance
        self.frame_matcher = frame_matcher or NormalizedMaeFrameMatcher()

    def reduce(self, *, open_event, segment, result):
        transitions: list[VideoFreezeEventTransition] = []
        current = replace(open_event) if open_event is not None else None
        if current is not None:
            current.stream_id = self.external_stream_id

        if not result.checked:
            reason = result.error or "decode_failure"
            if current is not None:
                transitions.append(
                    self._close(
                        current,
                        reason=reason,
                        transition_type=VideoFreezeEventTransitionType.UNKNOWN_GAP,
                    )
                )
            transitions.append(
                VideoFreezeEventTransition(
                    VideoFreezeEventTransitionType.UNKNOWN_GAP,
                    reason=reason,
                    commits_segment=True,
                )
            )
            return transitions

        had_gap = False
        if current is not None and not self._sequence_can_follow(current, segment):
            had_gap = True
            reason = (
                "timeline_discontinued"
                if self._timeline_changed(current, segment)
                else "observation_gap"
            )
            transitions.append(
                self._close(
                    current,
                    reason=reason,
                    transition_type=VideoFreezeEventTransitionType.UNKNOWN_GAP,
                )
            )
            current = None

        intervals = sorted(result.freeze_intervals, key=lambda item: item.start)
        if not intervals:
            if current is not None:
                transitions.append(self._close(current, reason="video_returned"))
            transitions.append(
                VideoFreezeEventTransition(
                    (
                        VideoFreezeEventTransitionType.MARK_COMMITTED
                        if had_gap
                        else VideoFreezeEventTransitionType.HEALTHY_EVIDENCE
                    ),
                    commits_segment=True,
                )
            )
            return transitions

        for index, interval in enumerate(intervals):
            is_last = index == len(intervals) - 1
            if current is None:
                current = self._new_event(segment, interval)
            elif self._can_continue(current, segment, interval):
                self._extend_event(current, segment, interval)
            else:
                transitions.append(
                    self._close(
                        current,
                        reason=(
                            "freeze_content_changed"
                            if segment.sequence != current.end_sequence
                            else "freeze_interrupted"
                        ),
                    )
                )
                current = self._new_event(segment, interval)

            next_continues = (
                not is_last
                and intervals[index + 1].start
                <= interval.end + self.boundary_tolerance
            )
            if next_continues:
                continue
            if interval.end < segment.duration - self.boundary_tolerance:
                transitions.append(
                    self._close(
                        current,
                        reason="video_returned",
                        commits_segment=is_last,
                    )
                )
                current = None
            else:
                transitions.append(
                    VideoFreezeEventTransition(
                        VideoFreezeEventTransitionType.PERSIST_OPEN,
                        event=replace(current),
                        commits_segment=is_last,
                    )
                )
        return transitions

    def _close(
        self,
        event,
        *,
        reason,
        transition_type=VideoFreezeEventTransitionType.CLOSE_EVENT,
        commits_segment=False,
    ):
        closed = replace(event)
        closed.detection_closed = True
        closed.status = VideoFreezeEventStatus.RESOLVED
        closed.resolution_reason = reason
        return VideoFreezeEventTransition(
            transition_type,
            event=closed,
            reason=reason,
            commits_segment=commits_segment,
        )

    @staticmethod
    def _timeline_changed(event, segment):
        return (
            event.timeline_generation != segment.timeline_generation
            or event.discontinuity_sequence != segment.discontinuity_sequence
        )

    @staticmethod
    def _sequence_can_follow(event, segment):
        if event.variant_stable_id != segment.variant_stable_id:
            return False
        if VideoFreezeEventReducer._timeline_changed(event, segment):
            return False
        if segment.sequence == event.end_sequence:
            return event.last_media_revision == segment.media_revision
        return segment.sequence == event.end_sequence + 1

    def _can_continue(self, event, segment, interval):
        if not self._sequence_can_follow(event, segment):
            return False
        if segment.sequence == event.end_sequence:
            return interval.start <= event.end_offset + self.boundary_tolerance
        if not (
            event.end_offset >= event.last_segment_duration - self.boundary_tolerance
            and interval.start <= self.boundary_tolerance
        ):
            return False
        if (
            event.last_boundary_fingerprint is None
            or interval.start_boundary_fingerprint is None
        ):
            return False
        return self.frame_matcher.compare(
            event.last_boundary_fingerprint,
            interval.start_boundary_fingerprint,
        ).status is FrameComparisonStatus.MATCH

    def _new_event(self, segment, interval):
        return VideoFreezeLiveEvent(
            event_id=self._event_id(segment, interval.start),
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
            reference_segment_duration=segment.duration,
            last_boundary_fingerprint=interval.end_boundary_fingerprint,
            start_segment_uri=segment.uri,
            end_segment_uri=segment.uri,
            coverage_complete=True,
        )

    @staticmethod
    def _extend_event(event, segment, interval):
        is_new_segment = segment.sequence != event.end_sequence
        added_duration = (
            max(0.0, event.last_segment_duration - event.end_offset) + interval.end
            if is_new_segment
            else max(0.0, interval.end - event.end_offset)
        )
        event.duration += added_duration
        event.end_sequence = segment.sequence
        event.end_offset = interval.end
        event.end_program_time = VideoFreezeEventReducer._program_time(
            segment, interval.end
        )
        event.last_segment_duration = segment.duration
        event.last_media_revision = segment.media_revision
        event.end_segment_uri = segment.uri
        event.last_boundary_fingerprint = interval.end_boundary_fingerprint
        if is_new_segment:
            event.affected_segment_count += 1

    def _event_id(self, segment, start_offset):
        raw = (
            f"{self.storage_id}|{segment.variant_stable_id}|"
            f"{segment.timeline_generation}|{segment.discontinuity_sequence}|"
            f"{segment.sequence}|{segment.media_revision}|{start_offset:.6f}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _program_time(segment, offset) -> datetime | None:
        if segment.program_date_time is None:
            return None
        return segment.program_date_time + timedelta(seconds=offset)
