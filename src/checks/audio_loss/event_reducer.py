from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum

from models.audio import SilenceInterval
from models.audio_loss import (
    AudioLossCause,
    AudioLossDetectionResult,
    AudioLossEventStatus,
    AudioLossLiveEvent,
    AudioLossSignal,
)
from models.segment import Segment


class AudioLossEventTransitionType(str, Enum):
    PERSIST_OPEN = "persist_open"
    RESOLVE = "resolve"
    MARK_COMMITTED = "mark_committed"


@dataclass(frozen=True)
class AudioLossEventTransition:
    type: AudioLossEventTransitionType
    event: AudioLossLiveEvent | None = None
    reason: str | None = None
    commits_segment: bool = False

    def __post_init__(self) -> None:
        needs_event = self.type in (
            AudioLossEventTransitionType.PERSIST_OPEN,
            AudioLossEventTransitionType.RESOLVE,
        )
        if needs_event and self.event is None:
            raise ValueError(f"{self.type.value} transition requires an event")
        if self.type is AudioLossEventTransitionType.RESOLVE and not self.reason:
            raise ValueError("resolve transition requires a reason")


@dataclass(frozen=True)
class _OutageInterval:
    interval: SilenceInterval
    cause: AudioLossCause


class AudioLossEventReducer:
    def __init__(
        self,
        *,
        stream_id: str,
        boundary_tolerance: float = 0.10,
    ) -> None:
        if boundary_tolerance < 0:
            raise ValueError("boundary_tolerance must be >= 0")
        self.stream_id = stream_id
        self.boundary_tolerance = boundary_tolerance

    def reduce(
        self,
        *,
        open_event: AudioLossLiveEvent | None,
        segment: Segment,
        result: AudioLossDetectionResult,
    ) -> list[AudioLossEventTransition]:
        if not result.checked or result.signal is AudioLossSignal.UNKNOWN:
            return []

        transitions: list[AudioLossEventTransition] = []
        current = self._copy_event(open_event)

        if current is not None and not self._sequence_can_follow(
            event=current,
            segment=segment,
        ):
            transitions.append(self._resolve(current, reason="observation_gap"))
            current = None

        outages = self._outage_intervals(segment=segment, result=result)
        if not outages:
            if current is None:
                transitions.append(
                    AudioLossEventTransition(
                        type=AudioLossEventTransitionType.MARK_COMMITTED,
                        commits_segment=True,
                    )
                )
            else:
                transitions.append(
                    self._resolve(
                        current,
                        reason="audio_returned",
                        commits_segment=True,
                    )
                )
            return transitions

        for index, outage in enumerate(outages):
            is_last = index == len(outages) - 1
            interval = outage.interval

            if current is None:
                current = self._new_event(
                    segment=segment,
                    interval=interval,
                    cause=outage.cause,
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
                    cause=outage.cause,
                )
            else:
                transitions.append(self._resolve(current, reason="audio_returned"))
                current = self._new_event(
                    segment=segment,
                    interval=interval,
                    cause=outage.cause,
                )

            if self._ends_before_segment_end(segment=segment, interval=interval):
                transitions.append(
                    self._resolve(
                        current,
                        reason="audio_returned",
                        commits_segment=is_last,
                    )
                )
                current = None
            else:
                transitions.append(
                    AudioLossEventTransition(
                        type=AudioLossEventTransitionType.PERSIST_OPEN,
                        event=self._copy_event(current),
                        commits_segment=is_last,
                    )
                )

        return transitions

    @staticmethod
    def _outage_intervals(
        *,
        segment: Segment,
        result: AudioLossDetectionResult,
    ) -> tuple[_OutageInterval, ...]:
        if result.signal is AudioLossSignal.MISSING:
            return (
                _OutageInterval(
                    interval=SilenceInterval(0.0, segment.duration),
                    cause=AudioLossCause.AUDIO_STREAM_MISSING,
                ),
            )
        if result.signal is not AudioLossSignal.SILENT:
            return ()
        return tuple(
            _OutageInterval(
                interval=interval,
                cause=AudioLossCause.CONTINUOUS_SILENCE,
            )
            for interval in sorted(
                result.silence_intervals,
                key=lambda item: item.start,
            )
        )

    @staticmethod
    def _copy_event(
        event: AudioLossLiveEvent | None,
    ) -> AudioLossLiveEvent | None:
        if event is None:
            return None
        return replace(
            event,
            causes_seen=list(event.causes_seen),
        )

    def _resolve(
        self,
        event: AudioLossLiveEvent,
        *,
        reason: str,
        commits_segment: bool = False,
    ) -> AudioLossEventTransition:
        resolved = self._copy_event(event)
        if resolved is None:
            raise RuntimeError("cannot resolve an empty audio-loss event")
        resolved.status = AudioLossEventStatus.RESOLVED
        resolved.resolution_reason = reason
        return AudioLossEventTransition(
            type=AudioLossEventTransitionType.RESOLVE,
            event=resolved,
            reason=reason,
            commits_segment=commits_segment,
        )

    @staticmethod
    def _sequence_can_follow(
        *,
        event: AudioLossLiveEvent,
        segment: Segment,
    ) -> bool:
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
        event: AudioLossLiveEvent,
        segment: Segment,
        interval: SilenceInterval,
    ) -> bool:
        if event.timeline_generation != segment.timeline_generation:
            return False
        if event.discontinuity_sequence != segment.discontinuity_sequence:
            return False
        if segment.sequence == event.end_sequence:
            if event.last_media_revision != segment.media_revision:
                return False
            return interval.start <= event.end_offset + self.boundary_tolerance
        if segment.sequence != event.end_sequence + 1:
            return False
        return (
            event.end_offset
            >= event.last_segment_duration - self.boundary_tolerance
            and interval.start <= self.boundary_tolerance
        )

    def _new_event(
        self,
        *,
        segment: Segment,
        interval: SilenceInterval,
        cause: AudioLossCause,
    ) -> AudioLossLiveEvent:
        return AudioLossLiveEvent(
            event_id=self._event_id(segment=segment, start_offset=interval.start),
            stream_id=self.stream_id,
            variant_id=segment.variant_id,
            variant_stable_id=segment.variant_stable_id,
            discontinuity_sequence=segment.discontinuity_sequence,
            start_sequence=segment.sequence,
            end_sequence=segment.sequence,
            start_offset=interval.start,
            end_offset=interval.end,
            start_program_time=self._program_time(segment, interval.start),
            end_program_time=self._program_time(segment, interval.end),
            duration=interval.duration,
            last_segment_duration=segment.duration,
            primary_cause=cause,
            causes_seen=[cause],
            affected_segment_count=1,
            timeline_generation=segment.timeline_generation,
            start_media_revision=segment.media_revision,
            last_media_revision=segment.media_revision,
            audio_group=segment.audio_group,
            rendition_name=segment.rendition_name,
            language=segment.language,
            rendition_default=segment.rendition_default,
            rendition_autoselect=segment.rendition_autoselect,
            hls_stable_rendition_id=segment.hls_stable_rendition_id,
        )

    @staticmethod
    def _extend_event(
        *,
        event: AudioLossLiveEvent,
        segment: Segment,
        interval: SilenceInterval,
        cause: AudioLossCause,
    ) -> None:
        if segment.sequence == event.end_sequence:
            added_duration = max(
                0.0,
                interval.end - max(event.end_offset, interval.start),
            )
        else:
            added_duration = interval.duration
        event.duration += added_duration
        is_new_segment = segment.sequence != event.end_sequence
        event.end_sequence = segment.sequence
        event.end_offset = interval.end
        event.end_program_time = AudioLossEventReducer._program_time(
            segment,
            interval.end,
        )
        event.last_segment_duration = segment.duration
        event.last_media_revision = segment.media_revision
        if cause not in event.causes_seen:
            event.causes_seen.append(cause)
        if is_new_segment:
            event.affected_segment_count += 1

    def _ends_before_segment_end(
        self,
        *,
        segment: Segment,
        interval: SilenceInterval,
    ) -> bool:
        return interval.end < segment.duration - self.boundary_tolerance

    def _event_id(self, *, segment: Segment, start_offset: float) -> str:
        raw = (
            f"{self.stream_id}|"
            f"{segment.variant_stable_id}|"
            f"{segment.timeline_generation}|"
            f"{segment.discontinuity_sequence}|"
            f"{segment.sequence}|"
            f"{segment.media_revision}|"
            f"{start_offset:.6f}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _program_time(segment: Segment, offset: float) -> datetime | None:
        if segment.program_date_time is None:
            return None
        return segment.program_date_time + timedelta(seconds=offset)
