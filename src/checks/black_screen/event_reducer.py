from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum

from models.black_live import BlackLiveEvent
from models.detection import BlackDetectionResult, BlackInterval
from models.segment import Segment


class BlackEventTransitionType(str, Enum):
    PERSIST_OPEN = "persist_open"
    RESOLVE = "resolve"
    MARK_COMMITTED = "mark_committed"


@dataclass(frozen=True)
class BlackEventTransition:
    type: BlackEventTransitionType
    event: BlackLiveEvent | None = None
    reason: str | None = None
    commits_segment: bool = False

    def __post_init__(self) -> None:
        needs_event = self.type in (
            BlackEventTransitionType.PERSIST_OPEN,
            BlackEventTransitionType.RESOLVE,
        )
        if needs_event and self.event is None:
            raise ValueError(
                f"{self.type.value} transition requires an event"
            )
        if (
            self.type == BlackEventTransitionType.RESOLVE
            and not self.reason
        ):
            raise ValueError(
                "resolve transition requires a reason"
            )


class BlackEventReducer:
    def __init__(
        self,
        *,
        stream_id: str,
        boundary_tolerance: float = 0.10,
    ) -> None:
        if boundary_tolerance < 0:
            raise ValueError(
                "boundary_tolerance must be >= 0"
            )

        self.stream_id = stream_id
        self.boundary_tolerance = boundary_tolerance

    def reduce(
        self,
        *,
        open_event: BlackLiveEvent | None,
        segment: Segment,
        result: BlackDetectionResult,
    ) -> list[BlackEventTransition]:
        transitions: list[BlackEventTransition] = []
        current = self._copy_event(open_event)

        if (
            current is not None
            and not self._sequence_can_follow(
                event=current,
                segment=segment,
            )
        ):
            transitions.append(
                self._resolve(
                    current,
                    reason="observation_gap",
                )
            )
            current = None

        intervals = sorted(
            result.black_intervals,
            key=lambda item: item.start,
        )

        if not intervals:
            if current is None:
                transitions.append(
                    BlackEventTransition(
                        type=(
                            BlackEventTransitionType
                            .MARK_COMMITTED
                        ),
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
                current = self._new_event(
                    segment=segment,
                    interval=interval,
                )
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
                    self._resolve(
                        current,
                        reason="black_interrupted",
                    )
                )
                current = self._new_event(
                    segment=segment,
                    interval=interval,
                )

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
                    BlackEventTransition(
                        type=(
                            BlackEventTransitionType
                            .PERSIST_OPEN
                        ),
                        event=self._copy_event(current),
                        commits_segment=is_last,
                    )
                )

        return transitions

    @staticmethod
    def _copy_event(
        event: BlackLiveEvent | None,
    ) -> BlackLiveEvent | None:
        if event is None:
            return None

        return replace(
            event,
            affected_segments=list(
                event.affected_segments
            ),
        )

    def _resolve(
        self,
        event: BlackLiveEvent,
        *,
        reason: str,
        commits_segment: bool = False,
    ) -> BlackEventTransition:
        return BlackEventTransition(
            type=BlackEventTransitionType.RESOLVE,
            event=self._copy_event(event),
            reason=reason,
            commits_segment=commits_segment,
        )

    @staticmethod
    def _sequence_can_follow(
        *,
        event: BlackLiveEvent,
        segment: Segment,
    ) -> bool:
        if event.timeline_generation != segment.timeline_generation:
            return False

        if (
            event.discontinuity_sequence
            != segment.discontinuity_sequence
        ):
            return False

        if segment.sequence == event.end_sequence:
            return event.last_media_revision == segment.media_revision
        return segment.sequence == event.end_sequence + 1

    def _can_continue(
        self,
        *,
        event: BlackLiveEvent,
        segment: Segment,
        interval: BlackInterval,
    ) -> bool:
        if event.timeline_generation != segment.timeline_generation:
            return False

        if (
            event.discontinuity_sequence
            != segment.discontinuity_sequence
        ):
            return False

        if segment.sequence == event.end_sequence:
            if event.last_media_revision != segment.media_revision:
                return False
            return (
                interval.start
                <= event.end_offset + self.boundary_tolerance
            )

        if segment.sequence != event.end_sequence + 1:
            return False

        previous_reached_end = (
            event.end_offset
            >= (
                event.last_segment_duration
                - self.boundary_tolerance
            )
        )
        current_starts_at_beginning = (
            interval.start <= self.boundary_tolerance
        )

        return (
            previous_reached_end
            and current_starts_at_beginning
        )

    def _new_event(
        self,
        *,
        segment: Segment,
        interval: BlackInterval,
    ) -> BlackLiveEvent:
        return BlackLiveEvent(
            event_id=self._event_id(
                segment=segment,
                start_offset=interval.start,
            ),
            stream_id=self.stream_id,
            variant_id=segment.variant_id,
            variant_stable_id=segment.variant_stable_id,
            discontinuity_sequence=(
                segment.discontinuity_sequence
            ),
            start_sequence=segment.sequence,
            end_sequence=segment.sequence,
            start_offset=interval.start,
            end_offset=interval.end,
            start_program_time=(
                self._program_time(segment, interval.start)
            ),
            end_program_time=(
                self._program_time(segment, interval.end)
            ),
            duration=interval.duration,
            last_segment_duration=segment.duration,
            affected_segments=[segment.sequence],
            timeline_generation=segment.timeline_generation,
            start_media_revision=segment.media_revision,
            last_media_revision=segment.media_revision,
        )

    @staticmethod
    def _extend_event(
        *,
        event: BlackLiveEvent,
        segment: Segment,
        interval: BlackInterval,
    ) -> None:
        if segment.sequence == event.end_sequence:
            added_duration = max(
                0.0,
                interval.end
                - max(event.end_offset, interval.start),
            )
        else:
            added_duration = interval.duration

        event.duration += added_duration
        event.end_sequence = segment.sequence
        event.end_offset = interval.end
        event.end_program_time = (
            BlackEventReducer._program_time(
                segment,
                interval.end,
            )
        )
        event.last_segment_duration = segment.duration
        event.last_media_revision = segment.media_revision

        if segment.sequence not in event.affected_segments:
            event.affected_segments.append(segment.sequence)

    def _ends_before_segment_end(
        self,
        *,
        segment: Segment,
        interval: BlackInterval,
    ) -> bool:
        return (
            interval.end
            < segment.duration - self.boundary_tolerance
        )

    def _event_id(
        self,
        *,
        segment: Segment,
        start_offset: float,
    ) -> str:
        raw = (
            f"{self.stream_id}|"
            f"{segment.variant_stable_id}|"
            f"{segment.timeline_generation}|"
            f"{segment.discontinuity_sequence}|"
            f"{segment.sequence}|"
            f"{segment.media_revision}|"
            f"{start_offset:.6f}"
        )
        return hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()[:24]

    @staticmethod
    def _program_time(
        segment: Segment,
        offset: float,
    ) -> datetime | None:
        if segment.program_date_time is None:
            return None

        return segment.program_date_time + timedelta(
            seconds=offset
        )
