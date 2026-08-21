from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class BlackEventStatus(
    str,
    Enum,
):
    OPEN = "open"
    RESOLVED = "resolved"


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

    affected_segments: list[int] = field(
        default_factory=list
    )

    status: BlackEventStatus = (
        BlackEventStatus.OPEN
    )

    long_alert_sent: bool = False

    resolution_reason: str | None = None
    timeline_generation: int = 0
    start_media_revision: str = ""
    last_media_revision: str = ""
