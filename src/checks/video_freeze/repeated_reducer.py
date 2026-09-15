from __future__ import annotations

from dataclasses import dataclass

from checks.video_freeze.repeated_state import (
    FreezeWarningRecord,
    RepeatedFreezeAlert,
    RepeatedFreezeIncident,
    RepeatedFreezeState,
)
from policies.video_freeze import VideoFreezeAlertPolicy


@dataclass(frozen=True)
class RepeatedFreezeReduction:
    state: RepeatedFreezeState
    alert: RepeatedFreezeAlert | None = None
    clear_state: bool = False


class RepeatedFreezeReducer:
    def __init__(self, policy: VideoFreezeAlertPolicy) -> None:
        self.policy = policy

    def record_candidate(
        self,
        *,
        state: RepeatedFreezeState,
        record: FreezeWarningRecord,
    ) -> RepeatedFreezeReduction:
        minimum = record.event_at - self.policy.repeated_window
        records = {
            item.event_id: item
            for item in state.history
            if item.event_at > minimum
        }
        is_new_record = record.event_id not in records
        records[record.event_id] = record
        history = tuple(
            sorted(records.values(), key=lambda item: (item.event_at, item.event_id))
        )

        if state.incident is not None:
            if not is_new_record:
                return RepeatedFreezeReduction(
                    RepeatedFreezeState(history, state.incident)
                )
            incident = state.incident
            occurrences = incident.occurrences + 1
            should_update = (
                occurrences - incident.last_notified_occurrences
                >= self.policy.repeated_update_every
            )
            updated = RepeatedFreezeIncident(
                incident_id=incident.incident_id,
                first_event_id=incident.first_event_id,
                latest_event_id=record.event_id,
                first_event_at=incident.first_event_at,
                last_event_at=record.event_at,
                occurrences=occurrences,
                total_duration=incident.total_duration + record.duration,
                last_notified_occurrences=(
                    occurrences
                    if should_update
                    else incident.last_notified_occurrences
                ),
                start_sequence=incident.start_sequence,
                end_sequence=record.end_sequence,
                start_segment_uri=incident.start_segment_uri,
                end_segment_uri=record.end_segment_uri,
                affected_segment_count=(
                    incident.affected_segment_count
                    + record.affected_segment_count
                ),
            )
            alert = (
                self._alert("UPDATE", updated, "repeated_video_freeze")
                if should_update
                else None
            )
            return RepeatedFreezeReduction(
                RepeatedFreezeState(history, updated), alert
            )

        if not self.policy.is_repeated_freeze(len(history)):
            return RepeatedFreezeReduction(RepeatedFreezeState(history))

        first = history[0]
        incident = RepeatedFreezeIncident(
            incident_id=record.event_id,
            first_event_id=first.event_id,
            latest_event_id=record.event_id,
            first_event_at=first.event_at,
            last_event_at=record.event_at,
            occurrences=len(history),
            total_duration=sum(item.duration for item in history),
            last_notified_occurrences=len(history),
            start_sequence=first.start_sequence,
            end_sequence=record.end_sequence,
            start_segment_uri=first.start_segment_uri,
            end_segment_uri=record.end_segment_uri,
            affected_segment_count=sum(
                item.affected_segment_count for item in history
            ),
        )
        return RepeatedFreezeReduction(
            RepeatedFreezeState(history, incident),
            self._alert("OPEN", incident, "repeated_video_freeze"),
        )

    def resolve_incident(
        self,
        *,
        state: RepeatedFreezeState,
        reason: str = "healthy_recovery_confirmed",
    ) -> RepeatedFreezeReduction:
        if state.incident is None:
            return RepeatedFreezeReduction(state)
        return RepeatedFreezeReduction(
            RepeatedFreezeState(),
            self._alert("RESOLVED", state.incident, reason),
            clear_state=True,
        )

    @staticmethod
    def _alert(state, incident, reason):
        return RepeatedFreezeAlert(
            state=state,
            event_id=incident.incident_id,
            latest_event_id=incident.latest_event_id,
            occurrences=incident.occurrences,
            total_duration=incident.total_duration,
            reason=reason,
            start_sequence=incident.start_sequence,
            end_sequence=incident.end_sequence,
            start_segment_uri=incident.start_segment_uri,
            end_segment_uri=incident.end_segment_uri,
            affected_segment_count=incident.affected_segment_count,
        )
