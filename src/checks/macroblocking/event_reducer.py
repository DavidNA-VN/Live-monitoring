from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum

from models.macroblocking import (
    MacroblockingDetectionResult,
    MacroblockingEventStatus,
    MacroblockingInterval,
    MacroblockingLiveEvent,
)
from models.segment import Segment
from policies.macroblocking import MacroblockingAlertPolicy


class MacroblockingEventTransitionType(str, Enum):
    PERSIST_OPEN = "persist_open"
    OPEN_ALERT = "open_alert"
    CLOSE_EVENT = "close_event"
    MARK_COMMITTED = "mark_committed"
    HEALTHY_EVIDENCE = "healthy_evidence"
    UNKNOWN_GAP = "unknown_gap"


@dataclass(frozen=True)
class MacroblockingEventTransition:
    type: MacroblockingEventTransitionType
    event: MacroblockingLiveEvent | None = None
    reason: str | None = None
    commits_segment: bool = False

    def __post_init__(self) -> None:
        if self.type in (
            MacroblockingEventTransitionType.PERSIST_OPEN,
            MacroblockingEventTransitionType.OPEN_ALERT,
            MacroblockingEventTransitionType.CLOSE_EVENT,
        ) and self.event is None:
            raise ValueError(f"{self.type.value} transition requires an event")
        if self.type in (
            MacroblockingEventTransitionType.CLOSE_EVENT,
            MacroblockingEventTransitionType.UNKNOWN_GAP,
        ) and not self.reason:
            raise ValueError(f"{self.type.value} transition requires a reason")


class MacroblockingEventReducer:
    """Pure temporal reduction for one variant's macroblocking observations."""

    def __init__(
        self,
        *,
        storage_id: str,
        external_stream_id: str,
        policy: MacroblockingAlertPolicy | None = None,
    ) -> None:
        if not storage_id:
            raise ValueError("storage_id must not be empty")
        if not external_stream_id:
            raise ValueError("external_stream_id must not be empty")
        self.storage_id = storage_id
        self.external_stream_id = external_stream_id
        self.policy = policy or MacroblockingAlertPolicy()

    def reduce(
        self,
        *,
        open_event: MacroblockingLiveEvent | None,
        segment: Segment,
        result: MacroblockingDetectionResult,
    ) -> list[MacroblockingEventTransition]:
        self._validate_result_identity(segment, result)
        transitions: list[MacroblockingEventTransition] = []
        current = replace(open_event) if open_event is not None else None
        if current is not None:
            current.stream_id = self.external_stream_id

        if not result.checked or not result.coverage_complete:
            reason = result.error or "incomplete_macroblocking_observation"
            if current is not None:
                transitions.append(self._close(current, reason=reason))
            transitions.append(
                MacroblockingEventTransition(
                    MacroblockingEventTransitionType.UNKNOWN_GAP,
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
            transitions.append(self._close(current, reason=reason))
            transitions.append(
                MacroblockingEventTransition(
                    MacroblockingEventTransitionType.UNKNOWN_GAP,
                    reason=reason,
                )
            )
            current = None

        intervals = sorted(result.intervals, key=lambda item: item.start)
        if not intervals:
            if current is not None:
                transitions.append(self._close(current, reason="video_returned"))
            transitions.append(
                MacroblockingEventTransition(
                    MacroblockingEventTransitionType.MARK_COMMITTED
                    if had_gap
                    else MacroblockingEventTransitionType.HEALTHY_EVIDENCE,
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
                self._append_open_alert_if_due(transitions, current)
                transitions.append(
                    self._close(current, reason="macroblocking_interrupted")
                )
                current = self._new_event(segment, interval)

            next_continues = (
                not is_last
                and intervals[index + 1].start
                <= interval.end + self.policy.maximum_negative_gap
            )
            if next_continues:
                continue

            if interval.end < segment.duration - self.policy.maximum_negative_gap:
                self._append_open_alert_if_due(transitions, current)
                transitions.append(
                    self._close(
                        current,
                        reason="video_returned",
                        commits_segment=is_last,
                    )
                )
                current = None
                continue

            transition_type = MacroblockingEventTransitionType.PERSIST_OPEN
            if not current.alert_sent and self.policy.should_alert(current.duration):
                current.alert_sent = True
                transition_type = MacroblockingEventTransitionType.OPEN_ALERT
            transitions.append(
                MacroblockingEventTransition(
                    transition_type,
                    event=replace(current),
                    commits_segment=is_last,
                )
            )
        return transitions

    def _append_open_alert_if_due(
        self,
        transitions: list[MacroblockingEventTransition],
        event: MacroblockingLiveEvent,
    ) -> None:
        if event.alert_sent or not self.policy.should_alert(event.duration):
            return
        event.alert_sent = True
        transitions.append(
            MacroblockingEventTransition(
                MacroblockingEventTransitionType.OPEN_ALERT,
                event=replace(event),
            )
        )

    def _close(
        self,
        event: MacroblockingLiveEvent,
        *,
        reason: str,
        commits_segment: bool = False,
    ) -> MacroblockingEventTransition:
        closed = replace(event)
        closed.detection_closed = True
        closed.status = MacroblockingEventStatus.RESOLVED
        closed.resolution_reason = reason
        return MacroblockingEventTransition(
            MacroblockingEventTransitionType.CLOSE_EVENT,
            event=closed,
            reason=reason,
            commits_segment=commits_segment,
        )

    @staticmethod
    def _validate_result_identity(
        segment: Segment,
        result: MacroblockingDetectionResult,
    ) -> None:
        if result.variant_id != segment.variant_id:
            raise ValueError("macroblocking result variant does not match segment")
        if result.sequence != segment.sequence:
            raise ValueError("macroblocking result sequence does not match segment")
        if result.segment_uri != segment.uri:
            raise ValueError("macroblocking result URI does not match segment")
        if not math.isclose(result.segment_duration, segment.duration):
            raise ValueError("macroblocking result duration does not match segment")

    @staticmethod
    def _timeline_changed(event: MacroblockingLiveEvent, segment: Segment) -> bool:
        return (
            event.timeline_generation != segment.timeline_generation
            or event.discontinuity_sequence != segment.discontinuity_sequence
        )

    @staticmethod
    def _sequence_can_follow(
        event: MacroblockingLiveEvent,
        segment: Segment,
    ) -> bool:
        if event.variant_stable_id != segment.variant_stable_id:
            return False
        if MacroblockingEventReducer._timeline_changed(event, segment):
            return False
        if segment.sequence == event.end_sequence:
            return event.last_media_revision == segment.media_revision
        return segment.sequence == event.end_sequence + 1

    def _can_continue(
        self,
        event: MacroblockingLiveEvent,
        segment: Segment,
        interval: MacroblockingInterval,
    ) -> bool:
        if not self._sequence_can_follow(event, segment):
            return False
        tolerance = self.policy.maximum_negative_gap
        if segment.sequence == event.end_sequence:
            return interval.start <= event.end_offset + tolerance
        return (
            event.end_offset >= event.last_segment_duration - tolerance
            and interval.start <= tolerance
        )

    def _new_event(
        self,
        segment: Segment,
        interval: MacroblockingInterval,
    ) -> MacroblockingLiveEvent:
        return MacroblockingLiveEvent(
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
            average_affected_area_ratio=interval.average_affected_area_ratio,
            peak_affected_area_ratio=interval.peak_affected_area_ratio,
            average_blocking_confidence=interval.average_blocking_confidence,
            peak_blocking_confidence=interval.peak_blocking_confidence,
            average_boundary_support_ratio=interval.average_boundary_support_ratio,
            start_media_revision=segment.media_revision,
            last_media_revision=segment.media_revision,
            start_segment_uri=segment.uri,
            end_segment_uri=segment.uri,
            coverage_complete=True,
            evidence_duration=interval.duration,
        )

    @staticmethod
    def _extend_event(
        event: MacroblockingLiveEvent,
        segment: Segment,
        interval: MacroblockingInterval,
    ) -> None:
        is_new_segment = segment.sequence != event.end_sequence
        if not is_new_segment and interval.end <= event.end_offset:
            return

        previous_end = event.end_offset
        added_span = (
            max(0.0, event.last_segment_duration - previous_end) + interval.end
            if is_new_segment
            else interval.end - previous_end
        )
        evidence_added = interval.duration if is_new_segment else min(
            interval.duration,
            interval.end - previous_end,
        )
        prior_evidence = event.evidence_duration or event.duration
        total_evidence = prior_evidence + evidence_added

        def weighted(prior: float, current: float) -> float:
            return (
                prior * prior_evidence + current * evidence_added
            ) / total_evidence

        event.average_affected_area_ratio = weighted(
            event.average_affected_area_ratio,
            interval.average_affected_area_ratio,
        )
        event.average_blocking_confidence = weighted(
            event.average_blocking_confidence,
            interval.average_blocking_confidence,
        )
        event.average_boundary_support_ratio = weighted(
            event.average_boundary_support_ratio,
            interval.average_boundary_support_ratio,
        )
        event.peak_affected_area_ratio = max(
            event.peak_affected_area_ratio,
            interval.peak_affected_area_ratio,
        )
        event.peak_blocking_confidence = max(
            event.peak_blocking_confidence,
            interval.peak_blocking_confidence,
        )
        event.duration += added_span
        event.evidence_duration = total_evidence
        event.end_sequence = segment.sequence
        event.end_offset = interval.end
        event.end_program_time = MacroblockingEventReducer._program_time(
            segment, interval.end
        )
        event.last_segment_duration = segment.duration
        event.last_media_revision = segment.media_revision
        event.end_segment_uri = segment.uri
        if is_new_segment:
            event.affected_segment_count += 1

    def _event_id(self, segment: Segment, start_offset: float) -> str:
        raw = (
            f"{self.storage_id}|{segment.variant_stable_id}|"
            f"{segment.timeline_generation}|{segment.discontinuity_sequence}|"
            f"{segment.sequence}|{segment.media_revision}|{start_offset:.6f}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _program_time(segment: Segment, offset: float) -> datetime | None:
        if segment.program_date_time is None:
            return None
        return segment.program_date_time + timedelta(seconds=offset)
