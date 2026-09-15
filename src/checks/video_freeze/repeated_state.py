from dataclasses import dataclass


@dataclass(frozen=True)
class FreezeWarningRecord:
    event_id: str
    event_at: float
    duration: float
    start_sequence: int = -1
    end_sequence: int = -1
    start_segment_uri: str = ""
    end_segment_uri: str = ""
    affected_segment_count: int = 0


@dataclass(frozen=True)
class RepeatedFreezeIncident:
    incident_id: str
    first_event_id: str
    latest_event_id: str
    first_event_at: float
    last_event_at: float
    occurrences: int
    total_duration: float
    last_notified_occurrences: int = 0
    start_sequence: int = -1
    end_sequence: int = -1
    start_segment_uri: str = ""
    end_segment_uri: str = ""
    affected_segment_count: int = 0


@dataclass(frozen=True)
class RepeatedFreezeAlert:
    state: str
    event_id: str
    latest_event_id: str
    occurrences: int
    total_duration: float
    reason: str
    start_sequence: int = -1
    end_sequence: int = -1
    start_segment_uri: str = ""
    end_segment_uri: str = ""
    affected_segment_count: int = 0


@dataclass(frozen=True)
class RepeatedFreezeState:
    history: tuple[FreezeWarningRecord, ...] = ()
    incident: RepeatedFreezeIncident | None = None
