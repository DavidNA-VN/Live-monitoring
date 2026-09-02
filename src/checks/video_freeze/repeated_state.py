from dataclasses import dataclass


@dataclass(frozen=True)
class FreezeWarningRecord:
    event_id: str
    event_at: float
    duration: float


@dataclass(frozen=True)
class RepeatedFreezeIncident:
    incident_id: str
    first_event_id: str
    latest_event_id: str
    first_event_at: float
    last_event_at: float
    occurrences: int
    total_duration: float


@dataclass(frozen=True)
class RepeatedFreezeAlert:
    state: str
    event_id: str
    latest_event_id: str
    occurrences: int
    total_duration: float
    reason: str
