from __future__ import annotations

import math

from models.macroblocking import MacroblockingFrameObservation


class MacroblockingAlertPolicy:
    """Business thresholds; analyzer geometry and fusion do not belong here."""

    def __init__(
        self,
        *,
        affected_area_threshold: float = 0.15,
        alert_duration: float = 10.0,
        maximum_negative_gap: float = 0.5,
        recovery_healthy_segments: int = 1,
    ) -> None:
        if (
            not math.isfinite(affected_area_threshold)
            or not 0 < affected_area_threshold <= 1
        ):
            raise ValueError(
                "affected_area_threshold must be finite and in (0, 1]"
            )
        for name, value in (
            ("alert_duration", alert_duration),
            ("maximum_negative_gap", maximum_negative_gap),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and >= 0")
        if alert_duration == 0:
            raise ValueError("alert_duration must be > 0")
        if (
            isinstance(recovery_healthy_segments, bool)
            or not isinstance(recovery_healthy_segments, int)
            or recovery_healthy_segments <= 0
        ):
            raise ValueError(
                "recovery_healthy_segments must be a positive integer"
            )
        self.affected_area_threshold = affected_area_threshold
        self.alert_duration = alert_duration
        self.maximum_negative_gap = maximum_negative_gap
        self.recovery_healthy_segments = recovery_healthy_segments

    def is_affected_area(self, affected_area_ratio: float) -> bool:
        if not math.isfinite(affected_area_ratio) or not 0 <= affected_area_ratio <= 1:
            raise ValueError(
                "affected_area_ratio must be finite and between 0 and 1"
            )
        return affected_area_ratio >= self.affected_area_threshold

    def should_alert(self, positive_duration: float) -> bool:
        if not math.isfinite(positive_duration) or positive_duration < 0:
            raise ValueError("positive_duration must be finite and >= 0")
        return positive_duration >= self.alert_duration

    def recovery_confirmed(self, healthy_segments_observed: int) -> bool:
        if (
            isinstance(healthy_segments_observed, bool)
            or not isinstance(healthy_segments_observed, int)
            or healthy_segments_observed < 0
        ):
            raise ValueError("healthy_segments_observed must be an integer >= 0")
        return healthy_segments_observed >= self.recovery_healthy_segments


class MacroblockingCandidateRule:
    """Combines trusted detector evidence with the business area threshold."""

    def __init__(
        self,
        *,
        alert_policy: MacroblockingAlertPolicy | None = None,
        detector_confidence_threshold: float = 0.70,
    ) -> None:
        if (
            not math.isfinite(detector_confidence_threshold)
            or not 0 <= detector_confidence_threshold <= 1
        ):
            raise ValueError(
                "detector_confidence_threshold must be finite and between 0 and 1"
            )
        self.alert_policy = alert_policy or MacroblockingAlertPolicy()
        self.detector_confidence_threshold = detector_confidence_threshold

    def is_candidate(self, observation: MacroblockingFrameObservation) -> bool:
        return (
            observation.valid
            and observation.blocking_confidence
            >= self.detector_confidence_threshold
            and self.alert_policy.is_affected_area(
                observation.affected_area_ratio
            )
        )
