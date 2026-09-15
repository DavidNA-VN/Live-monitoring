from __future__ import annotations

import math

from models.freeze import VideoFreezeSeverity


class VideoFreezeAlertPolicy:
    def __init__(
        self,
        *,
        direct_alert_duration: float = 60.0,
        repeated_event_count: int = 3,
        repeated_window: float = 120.0,
        repeated_update_every: int = 3,
        recovery_healthy_segments: int = 1,
        warning_duration: float | None = None,
        alert_duration: float | None = None,
    ) -> None:
        if alert_duration is not None:
            direct_alert_duration = alert_duration
        self._positive_finite("direct_alert_duration", direct_alert_duration)
        self._positive_finite("repeated_window", repeated_window)
        for name, value in (
            ("repeated_event_count", repeated_event_count),
            ("repeated_update_every", repeated_update_every),
            ("recovery_healthy_segments", recovery_healthy_segments),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if warning_duration is not None:
            self._positive_finite("warning_duration", warning_duration)

        self.direct_alert_duration = direct_alert_duration
        self.alert_duration = direct_alert_duration
        self.warning_duration = warning_duration
        self.repeated_event_count = repeated_event_count
        self.repeated_window = repeated_window
        self.repeated_update_every = repeated_update_every
        self.recovery_healthy_segments = recovery_healthy_segments

    def classify(self, duration: float) -> VideoFreezeSeverity | None:
        self._non_negative_finite("duration", duration)
        if duration >= self.direct_alert_duration:
            return VideoFreezeSeverity.ALERT
        return None

    def is_repeated_candidate(
        self,
        duration: float,
        reference_segment_duration: float | None = None,
    ) -> bool:
        self._non_negative_finite("duration", duration)
        if reference_segment_duration is None:
            reference_segment_duration = self.warning_duration
        if reference_segment_duration is None:
            raise ValueError("reference_segment_duration is required")
        self._positive_finite(
            "reference_segment_duration", reference_segment_duration
        )
        return (
            duration >= reference_segment_duration
            and duration < self.direct_alert_duration
        )

    def pending_notification(
        self,
        *,
        duration: float,
        warning_sent: bool,
        alert_sent: bool,
    ) -> VideoFreezeSeverity | None:
        del warning_sent
        severity = self.classify(duration)
        if severity is VideoFreezeSeverity.ALERT and not alert_sent:
            return severity
        return None

    def is_repeated_freeze(self, event_count: int) -> bool:
        if isinstance(event_count, bool) or not isinstance(event_count, int):
            raise ValueError("event_count must be an integer")
        if event_count < 0:
            raise ValueError("event_count must be >= 0")
        return event_count >= self.repeated_event_count

    def recovery_confirmed(self, healthy_segments_observed: int) -> bool:
        if (
            isinstance(healthy_segments_observed, bool)
            or not isinstance(healthy_segments_observed, int)
            or healthy_segments_observed < 0
        ):
            raise ValueError("healthy_segments_observed must be an integer >= 0")
        return healthy_segments_observed >= self.recovery_healthy_segments

    @staticmethod
    def _positive_finite(name: str, value: float) -> None:
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and > 0")

    @staticmethod
    def _non_negative_finite(name: str, value: float) -> None:
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and >= 0")
