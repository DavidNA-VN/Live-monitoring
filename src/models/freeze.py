from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from models.frame_fingerprint import BoundaryFrameFingerprint


class VideoFreezeSeverity(str, Enum):
    WARNING = "WARNING"
    ALERT = "ALERT"


class VideoFreezeEventStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class VideoFreezeAlertType(str, Enum):
    CONTINUOUS = "continuous"
    REPEATED = "repeated"


@dataclass(frozen=True)
class FreezeInterval:
    start: float
    end: float
    start_boundary_fingerprint: BoundaryFrameFingerprint | None = None
    end_boundary_fingerprint: BoundaryFrameFingerprint | None = None

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
    reference_segment_duration: float = 0.0
    detection_closed: bool = False
    last_boundary_fingerprint: BoundaryFrameFingerprint | None = None
    start_segment_uri: str = ""
    end_segment_uri: str = ""
    coverage_complete: bool = True


@dataclass(frozen=True)
class VideoFreezeAlertRecoveryState:
    alert_event_id: str
    alert_type: VideoFreezeAlertType
    recovery_pending: bool = False
    healthy_segments_observed: int = 0
    last_observed_sequence: int = -1
    timeline_generation: int = 0
    discontinuity_sequence: int = 0

    def __post_init__(self) -> None:
        if not self.alert_event_id:
            raise ValueError("alert_event_id must not be empty")
        if not isinstance(self.alert_type, VideoFreezeAlertType):
            object.__setattr__(
                self, "alert_type", VideoFreezeAlertType(self.alert_type)
            )
        if not isinstance(self.recovery_pending, bool):
            raise TypeError("recovery_pending must be a bool")
        for name in (
            "healthy_segments_observed",
            "last_observed_sequence",
            "timeline_generation",
            "discontinuity_sequence",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int")
        if self.healthy_segments_observed < 0:
            raise ValueError("healthy_segments_observed must be >= 0")
        if self.last_observed_sequence < -1:
            raise ValueError("last_observed_sequence must be >= -1")
        if self.timeline_generation < 0:
            raise ValueError("timeline_generation must be >= 0")
        if self.discontinuity_sequence < 0:
            raise ValueError("discontinuity_sequence must be >= 0")
