from __future__ import annotations

import math
from models.freeze import VideoFreezeSeverity


class VideoFreezeAlertPolicy:
    def __init__(
        self,
        *,
        warning_duration: float = 3.0,
        alert_duration: float = 5.0,
        repeated_event_count: int = 3,
        repeated_window: float = 120.0,
    ) -> None:
        self._positive_finite("warning_duration", warning_duration)
        self._positive_finite("alert_duration", alert_duration)
        self._positive_finite("repeated_window", repeated_window)
        if alert_duration <= warning_duration:
            raise ValueError(
                "alert_duration must be greater than warning_duration"
            )
        if (
            isinstance(repeated_event_count, bool)
            or not isinstance(repeated_event_count, int)
            or repeated_event_count <= 0
        ):
            raise ValueError("repeated_event_count must be a positive integer")

        self.warning_duration = warning_duration
        self.alert_duration = alert_duration
        self.repeated_event_count = repeated_event_count
        self.repeated_window = repeated_window

    def classify(self, duration: float) -> VideoFreezeSeverity | None:
        self._non_negative_finite("duration", duration)
        if duration >= self.alert_duration:
            return VideoFreezeSeverity.ALERT
        if duration >= self.warning_duration:
            return VideoFreezeSeverity.WARNING
        return None

    def is_repeated_candidate(self, duration: float) -> bool:
        return self.classify(duration) is VideoFreezeSeverity.WARNING

    def pending_notification(
        self,
        *,
        duration: float,
        warning_sent: bool,
        alert_sent: bool,
    ) -> VideoFreezeSeverity | None:
        severity = self.classify(duration)
        if severity is VideoFreezeSeverity.ALERT:
            return None if alert_sent else VideoFreezeSeverity.ALERT
        if severity is VideoFreezeSeverity.WARNING:
            return None if warning_sent or alert_sent else severity
        return None

    def is_repeated_freeze(self, event_count: int) -> bool:
        if isinstance(event_count, bool) or not isinstance(event_count, int):
            raise ValueError("event_count must be an integer")
        if event_count < 0:
            raise ValueError("event_count must be >= 0")
        return event_count >= self.repeated_event_count

    @staticmethod
    def _positive_finite(name: str, value: float) -> None:
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and > 0")

    @staticmethod
    def _non_negative_finite(name: str, value: float) -> None:
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and >= 0")
