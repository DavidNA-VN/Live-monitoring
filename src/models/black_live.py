from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class BlackEventStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class BlackAlertType(str, Enum):
    CONTINUOUS = "continuous"
    REPEATED = "repeated"


@dataclass
class BlackLiveEvent:
    event_id: str
    stream_id: str
    variant_id: str
    variant_stable_id: str
    discontinuity_sequence: int
    start_sequence: int
    end_sequence: int
    start_offset: float
    end_offset: float
    start_program_time: datetime | None
    end_program_time: datetime | None
    duration: float
    last_segment_duration: float
    affected_segments: list[int] = field(default_factory=list)
    status: BlackEventStatus = BlackEventStatus.OPEN
    long_alert_sent: bool = False
    resolution_reason: str | None = None
    timeline_generation: int = 0
    start_media_revision: str = ""
    last_media_revision: str = ""
    reference_segment_duration: float = 0.0
    detection_closed: bool = False
    start_segment_uri: str = ""
    end_segment_uri: str = ""
    coverage_complete: bool = True


@dataclass(frozen=True)
class BlackAlertRecoveryState:
    alert_event_id: str
    alert_type: BlackAlertType
    recovery_pending: bool = False
    healthy_segments_observed: int = 0
    last_observed_sequence: int = -1
    timeline_generation: int = 0
    discontinuity_sequence: int = 0

    def __post_init__(self) -> None:
        if not self.alert_event_id:
            raise ValueError("alert_event_id must not be empty")
        if not isinstance(self.alert_type, BlackAlertType):
            object.__setattr__(
                self, "alert_type", BlackAlertType(self.alert_type)
            )
        if not isinstance(self.recovery_pending, bool):
            raise TypeError("recovery_pending must be a bool")
        if isinstance(self.healthy_segments_observed, bool):
            raise TypeError("healthy_segments_observed must be an int")
        if isinstance(self.last_observed_sequence, bool):
            raise TypeError("last_observed_sequence must be an int")
        if isinstance(self.timeline_generation, bool):
            raise TypeError("timeline_generation must be an int")
        if isinstance(self.discontinuity_sequence, bool):
            raise TypeError("discontinuity_sequence must be an int")
        if self.healthy_segments_observed < 0:
            raise ValueError("healthy_segments_observed must be >= 0")
        if self.last_observed_sequence < -1:
            raise ValueError("last_observed_sequence must be >= -1")
        if self.timeline_generation < 0:
            raise ValueError("timeline_generation must be >= 0")
        if self.discontinuity_sequence < 0:
            raise ValueError("discontinuity_sequence must be >= 0")
