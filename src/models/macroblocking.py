from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import math


def _unit_interval(name: str, value: float) -> None:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be finite and between 0 and 1")


class MacroblockingFusionStrategy(str, Enum):
    MAX = "max"
    MAX_WITH_CONSISTENCY = "max_with_consistency"
    TOP_TWO_WEIGHTED = "top_two_weighted"


@dataclass(frozen=True)
class MacroblockingAnalyzerConfig:
    """Detector tuning only; business area/duration rules are excluded."""

    fusion_strategy: MacroblockingFusionStrategy
    analysis_width: int = 480
    analysis_height: int = 270
    sampling_fps: float = 1.0
    relative_scale_divisors: tuple[int, ...] = (15, 8, 4)
    stride_ratio: float = 0.5
    detector_confidence_threshold: float = 0.65
    local_confidence_threshold: float = 0.55
    heatmap_threshold: float = 0.54
    minimum_support_ratio: float = 0.50
    minimum_region_area_ratio: float = 0.002
    region_fill_minimum_bbox_ratio: float = 0.90
    region_fill_minimum_density: float = 0.50
    minimum_frame_std: float = 2.0
    minimum_period: int = 2
    maximum_period: int = 24
    candidate_grid_periods: tuple[int, ...] = (4, 6, 8, 12, 16)

    def __post_init__(self) -> None:
        if not isinstance(self.fusion_strategy, MacroblockingFusionStrategy):
            raise TypeError("fusion_strategy must be explicitly selected")
        for name in ("analysis_width", "analysis_height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not math.isfinite(self.sampling_fps) or self.sampling_fps <= 0:
            raise ValueError("sampling_fps must be finite and > 0")
        divisors = tuple(self.relative_scale_divisors)
        if len(divisors) != 3 or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            for value in divisors
        ):
            raise ValueError(
                "relative_scale_divisors must contain three positive integers"
            )
        if len(set(divisors)) != len(divisors):
            raise ValueError("relative_scale_divisors must be unique")
        if not math.isfinite(self.stride_ratio) or not 0 < self.stride_ratio <= 1:
            raise ValueError("stride_ratio must be finite and in (0, 1]")
        _unit_interval(
            "detector_confidence_threshold",
            self.detector_confidence_threshold,
        )
        for name in (
            "local_confidence_threshold",
            "heatmap_threshold",
            "minimum_support_ratio",
            "minimum_region_area_ratio",
            "region_fill_minimum_bbox_ratio",
            "region_fill_minimum_density",
        ):
            _unit_interval(name, getattr(self, name))
        if not math.isfinite(self.minimum_frame_std) or self.minimum_frame_std < 0:
            raise ValueError("minimum_frame_std must be finite and >= 0")
        for name in ("minimum_period", "maximum_period"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError(f"{name} must be an integer >= 2")
        if self.maximum_period < self.minimum_period:
            raise ValueError("maximum_period must be >= minimum_period")
        periods = tuple(self.candidate_grid_periods)
        if not periods or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not self.minimum_period <= value <= self.maximum_period
            for value in periods
        ):
            raise ValueError(
                "candidate_grid_periods must be integers within the period range"
            )
        if len(set(periods)) != len(periods):
            raise ValueError("candidate_grid_periods must be unique")
        object.__setattr__(self, "relative_scale_divisors", divisors)
        object.__setattr__(self, "candidate_grid_periods", periods)


@dataclass(frozen=True)
class MacroblockingScaleEvidence:
    """Debug evidence from one scale; never part of persisted event state."""

    window_size: int
    blocking_confidence: float
    boundary_support_ratio: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.window_size, bool)
            or not isinstance(self.window_size, int)
            or self.window_size <= 0
        ):
            raise ValueError("window_size must be a positive integer")
        _unit_interval("blocking_confidence", self.blocking_confidence)
        _unit_interval("boundary_support_ratio", self.boundary_support_ratio)


@dataclass(frozen=True)
class MacroblockingFrameObservation:
    offset_seconds: float
    affected_area_ratio: float
    blocking_confidence: float
    boundary_support_ratio: float
    valid: bool = True
    invalid_reason: str | None = None
    region_count: int = 0
    scale_evidence: tuple[MacroblockingScaleEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.offset_seconds) or self.offset_seconds < 0:
            raise ValueError("offset_seconds must be finite and >= 0")
        _unit_interval("affected_area_ratio", self.affected_area_ratio)
        _unit_interval("blocking_confidence", self.blocking_confidence)
        _unit_interval("boundary_support_ratio", self.boundary_support_ratio)
        if not isinstance(self.valid, bool):
            raise TypeError("valid must be a bool")
        if (
            isinstance(self.region_count, bool)
            or not isinstance(self.region_count, int)
            or self.region_count < 0
        ):
            raise ValueError("region_count must be a non-negative integer")
        reason = self.invalid_reason.strip() if self.invalid_reason else None
        if self.valid and reason is not None:
            raise ValueError("valid observation cannot have invalid_reason")
        if not self.valid and reason is None:
            raise ValueError("invalid observation requires invalid_reason")
        object.__setattr__(self, "invalid_reason", reason)
        object.__setattr__(self, "scale_evidence", tuple(self.scale_evidence))


@dataclass(frozen=True)
class MacroblockingInterval:
    start: float
    end: float
    average_affected_area_ratio: float
    peak_affected_area_ratio: float
    average_blocking_confidence: float
    peak_blocking_confidence: float
    average_boundary_support_ratio: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.start) or self.start < 0:
            raise ValueError("start must be finite and >= 0")
        if not math.isfinite(self.end) or self.end <= self.start:
            raise ValueError("end must be finite and greater than start")
        for name in (
            "average_affected_area_ratio",
            "peak_affected_area_ratio",
            "average_blocking_confidence",
            "peak_blocking_confidence",
            "average_boundary_support_ratio",
        ):
            _unit_interval(name, getattr(self, name))
        if self.peak_affected_area_ratio < self.average_affected_area_ratio:
            raise ValueError("peak affected area must be >= average")
        if self.peak_blocking_confidence < self.average_blocking_confidence:
            raise ValueError("peak confidence must be >= average")

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class MacroblockingDetectionResult:
    variant_id: str
    sequence: int
    segment_uri: str
    segment_duration: float
    observations: tuple[MacroblockingFrameObservation, ...] = ()
    intervals: tuple[MacroblockingInterval, ...] = ()
    program_date_time: datetime | None = None
    checked: bool = True
    error: str | None = None
    retryable: bool = True
    coverage_complete: bool = True
    invalid_observation_count: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("sequence must be an int")
        if not self.segment_uri:
            raise ValueError("segment_uri must not be empty")
        if not math.isfinite(self.segment_duration) or self.segment_duration <= 0:
            raise ValueError("segment_duration must be finite and > 0")
        if not isinstance(self.checked, bool) or not isinstance(self.retryable, bool):
            raise TypeError("checked and retryable must be bools")
        if not isinstance(self.coverage_complete, bool):
            raise TypeError("coverage_complete must be a bool")
        if (
            isinstance(self.invalid_observation_count, bool)
            or not isinstance(self.invalid_observation_count, int)
            or self.invalid_observation_count < 0
        ):
            raise ValueError("invalid_observation_count must be an integer >= 0")
        if not all(
            isinstance(item, MacroblockingFrameObservation)
            for item in self.observations
        ):
            raise TypeError("observations must contain frame observations")
        if not all(isinstance(item, MacroblockingInterval) for item in self.intervals):
            raise TypeError("intervals must contain macroblocking intervals")
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "intervals", tuple(self.intervals))


class MacroblockingEventStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


@dataclass
class MacroblockingLiveEvent:
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
    average_affected_area_ratio: float
    peak_affected_area_ratio: float
    average_blocking_confidence: float
    peak_blocking_confidence: float
    average_boundary_support_ratio: float
    start_media_revision: str
    last_media_revision: str
    affected_segment_count: int = 1
    status: MacroblockingEventStatus = MacroblockingEventStatus.OPEN
    alert_sent: bool = False
    resolution_reason: str | None = None
    detection_closed: bool = False
    start_segment_uri: str = ""
    end_segment_uri: str = ""
    coverage_complete: bool = True
    # Positive evidence only. ``duration`` may include tolerated short gaps.
    evidence_duration: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "stream_id",
            "variant_id",
            "variant_stable_id",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} must not be empty")
        for name in (
            "timeline_generation",
            "discontinuity_sequence",
            "start_sequence",
            "end_sequence",
            "affected_segment_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int")
        if self.timeline_generation < 0 or self.discontinuity_sequence < 0:
            raise ValueError("timeline counters must be >= 0")
        if self.end_sequence < self.start_sequence:
            raise ValueError("end_sequence must be >= start_sequence")
        if self.affected_segment_count <= 0:
            raise ValueError("affected_segment_count must be > 0")
        for name in (
            "start_offset",
            "end_offset",
            "duration",
            "evidence_duration",
            "last_segment_duration",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and >= 0")
        if self.end_offset < self.start_offset and self.end_sequence == self.start_sequence:
            raise ValueError("end_offset must be >= start_offset in one segment")
        if self.evidence_duration > self.duration:
            raise ValueError("evidence_duration must be <= duration")
        for name in (
            "average_affected_area_ratio",
            "peak_affected_area_ratio",
            "average_blocking_confidence",
            "peak_blocking_confidence",
            "average_boundary_support_ratio",
        ):
            _unit_interval(name, getattr(self, name))
        if self.peak_affected_area_ratio < self.average_affected_area_ratio:
            raise ValueError("peak affected area must be >= average")
        if self.peak_blocking_confidence < self.average_blocking_confidence:
            raise ValueError("peak confidence must be >= average")
        if not isinstance(self.status, MacroblockingEventStatus):
            raise TypeError("status must be MacroblockingEventStatus")
        for name in ("alert_sent", "detection_closed", "coverage_complete"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a bool")


@dataclass(frozen=True)
class MacroblockingAlertRecoveryState:
    alert_event_id: str
    recovery_pending: bool = False
    healthy_segments_observed: int = 0
    last_observed_sequence: int = -1
    timeline_generation: int = 0
    discontinuity_sequence: int = 0

    def __post_init__(self) -> None:
        if not self.alert_event_id:
            raise ValueError("alert_event_id must not be empty")
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
        if self.timeline_generation < 0 or self.discontinuity_sequence < 0:
            raise ValueError("timeline counters must be >= 0")
