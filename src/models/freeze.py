from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class VideoFreezeSeverity(str, Enum):
    WARNING = "WARNING"
    ALERT = "ALERT"


class VideoFreezeEventStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class FreezeInterval:
    start: float
    end: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.start) or self.start < 0:
            raise ValueError("freeze interval start must be finite and >= 0")
        if not math.isfinite(self.end) or self.end <= self.start:
            raise ValueError(
                "freeze interval end must be finite and greater than start"
            )

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class VideoFreezeDetectionResult:
    variant_id: str
    sequence: int
    segment_uri: str
    segment_duration: float
    program_date_time: datetime | None = None
    checked: bool = True
    error: str | None = None
    retryable: bool = True
    freeze_intervals: list[FreezeInterval] = field(default_factory=list)

    @property
    def has_freeze(self) -> bool:
        return bool(self.freeze_intervals)

    @property
    def total_freeze_duration(self) -> float:
        return sum(interval.duration for interval in self.freeze_intervals)


@dataclass
class VideoFreezeLiveEvent:
    event_id: str
    stream_id: str
    variant_id: str
    variant_stable_id: str
    timeline_generation: int
    discontinuity_sequence: int
    start_sequence: int
    end_sequence: int
    start_offset: float
    end_offset: float
    start_program_time: datetime | None
    end_program_time: datetime | None
    duration: float
    last_segment_duration: float
    start_media_revision: str
    last_media_revision: str
    affected_segment_count: int = 1
    status: VideoFreezeEventStatus = VideoFreezeEventStatus.OPEN
    highest_severity: VideoFreezeSeverity | None = None
    warning_sent: bool = False
    alert_sent: bool = False
    resolution_reason: str | None = None
