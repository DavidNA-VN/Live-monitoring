from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from policies.black_screen import BlackScreenAlertPolicy


@dataclass(frozen=True)
class ShortBlackRecord:
    event_id: str
    event_at: float
    duration: float
    start_sequence: int = -1
    end_sequence: int = -1
    start_segment_uri: str = ""
    end_segment_uri: str = ""
    affected_segment_count: int = 0


@dataclass(frozen=True)
class RepeatedBlackIncident:
    incident_id: str
    first_event_id: str
    latest_event_id: str
    first_event_at: float
    last_event_at: float
    occurrences: int
    total_black_duration: float
    last_notified_occurrences: int
    start_sequence: int = -1
    end_sequence: int = -1
    start_segment_uri: str = ""
    end_segment_uri: str = ""
    affected_segment_count: int = 0


@dataclass(frozen=True)
class RepeatedBlackState:
    history: tuple[ShortBlackRecord, ...] = ()
    incident: RepeatedBlackIncident | None = None


class RepeatedAlertState(str, Enum):
    OPEN = "OPEN"
    UPDATE = "UPDATE"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True)
class RepeatedBlackAlert:
    state: RepeatedAlertState
    event_id: str
    latest_event_id: str
    occurrences: int
    total_black_duration: float
    reason: str
    start_sequence: int = -1
    end_sequence: int = -1
    start_segment_uri: str = ""
    end_segment_uri: str = ""
    affected_segment_count: int = 0


@dataclass(frozen=True)
class RepeatedBlackReduction:
    state: RepeatedBlackState
    alert: RepeatedBlackAlert | None = None
    clear_state: bool = False


class RepeatedBlackReducer:
    def __init__(
        self,
        policy: BlackScreenAlertPolicy,
    ) -> None:
        self.policy = policy

    def record_short_event(
        self,
        *,
        state: RepeatedBlackState,
        record: ShortBlackRecord,
    ) -> RepeatedBlackReduction:
        minimum_time = (
            record.event_at - self.policy.repeated_window
        )
        records_by_id = {
            item.event_id: item
            for item in state.history
            if item.event_at > minimum_time
        }
        is_new_record = record.event_id not in records_by_id
        records_by_id[record.event_id] = record
        history = tuple(
            sorted(
                records_by_id.values(),
                key=lambda item: (
                    item.event_at,
                    item.event_id,
                ),
            )
        )

        if state.incident is not None:
            if not is_new_record:
                return RepeatedBlackReduction(
                    state=RepeatedBlackState(
                        history=history,
                        incident=state.incident,
                    )
                )
            incident = state.incident
            occurrences = incident.occurrences + 1
            total_duration = (
                incident.total_black_duration
                + record.duration
            )
            should_update = (
                occurrences
                - incident.last_notified_occurrences
                >= self.policy.repeated_update_every
            )
            updated = RepeatedBlackIncident(
                incident_id=incident.incident_id,
                first_event_id=incident.first_event_id,
                latest_event_id=record.event_id,
                first_event_at=incident.first_event_at,
                last_event_at=record.event_at,
                occurrences=occurrences,
                total_black_duration=total_duration,
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
                self._alert(
                    RepeatedAlertState.UPDATE,
                    updated,
                    reason="repeated_short_black",
                )
                if should_update
                else None
            )
            return RepeatedBlackReduction(
                state=RepeatedBlackState(
                    history=history,
                    incident=updated,
                ),
                alert=alert,
            )

        if not self.policy.is_repeated_black(len(history)):
            return RepeatedBlackReduction(
                state=RepeatedBlackState(history=history)
            )

        first = history[0]
        incident = RepeatedBlackIncident(
            incident_id=record.event_id,
            first_event_id=first.event_id,
            latest_event_id=record.event_id,
            first_event_at=first.event_at,
            last_event_at=record.event_at,
            occurrences=len(history),
            total_black_duration=sum(
                item.duration for item in history
            ),
            last_notified_occurrences=len(history),
            start_sequence=first.start_sequence,
            end_sequence=record.end_sequence,
            start_segment_uri=first.start_segment_uri,
            end_segment_uri=record.end_segment_uri,
            affected_segment_count=sum(
                item.affected_segment_count for item in history
            ),
        )
        return RepeatedBlackReduction(
            state=RepeatedBlackState(
                history=history,
                incident=incident,
            ),
            alert=self._alert(
                RepeatedAlertState.OPEN,
                incident,
                reason="repeated_short_black",
            ),
        )

    def resolve_incident(
        self,
        *,
        state: RepeatedBlackState,
        reason: str = "healthy_recovery_confirmed",
    ) -> RepeatedBlackReduction:
        incident = state.incident
        if incident is None:
            return RepeatedBlackReduction(state=state)

        return RepeatedBlackReduction(
            state=RepeatedBlackState(history=(), incident=None),
            alert=self._alert(
                RepeatedAlertState.RESOLVED,
                incident,
                reason=reason,
            ),
            clear_state=True,
        )

    @staticmethod
    def _alert(
        state: RepeatedAlertState,
        incident: RepeatedBlackIncident,
        *,
        reason: str,
    ) -> RepeatedBlackAlert:
        return RepeatedBlackAlert(
            state=state,
            event_id=incident.incident_id,
            latest_event_id=incident.latest_event_id,
            occurrences=incident.occurrences,
            total_black_duration=(
                incident.total_black_duration
            ),
            reason=reason,
            start_sequence=incident.start_sequence,
            end_sequence=incident.end_sequence,
            start_segment_uri=incident.start_segment_uri,
            end_segment_uri=incident.end_segment_uri,
            affected_segment_count=incident.affected_segment_count,
        )
