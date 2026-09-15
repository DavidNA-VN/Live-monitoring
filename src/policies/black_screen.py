import math


class BlackScreenAlertPolicy:

    def __init__(
        self,
        direct_alert_duration: float = 60.0,
        repeated_event_count: int = 3,
        repeated_window: float = 120.0,
        repeated_update_every: int = 3,
        recovery_healthy_segments: int = 1,
    ):
        if (
            not math.isfinite(direct_alert_duration)
            or direct_alert_duration <= 0
        ):
            raise ValueError(
                "direct_alert_duration must be > 0"
            )

        if repeated_event_count <= 0:
            raise ValueError(
                "repeated_event_count must be > 0"
            )

        if not math.isfinite(repeated_window) or repeated_window <= 0:
            raise ValueError(
                "repeated_window must be > 0"
            )

        if repeated_update_every <= 0:
            raise ValueError(
                "repeated_update_every must be > 0"
            )

        if recovery_healthy_segments <= 0:
            raise ValueError(
                "recovery_healthy_segments must be > 0"
            )

        self.direct_alert_duration = (
            direct_alert_duration
        )

        self.repeated_event_count = (
            repeated_event_count
        )

        self.repeated_window = (
            repeated_window
        )

        self.repeated_update_every = (
            repeated_update_every
        )

        self.recovery_healthy_segments = (
            recovery_healthy_segments
        )

    def should_alert_directly(
        self,
        duration: float,
    ) -> bool:

        return math.isfinite(duration) and duration >= self.direct_alert_duration

    def is_repeated_candidate(
        self,
        duration: float,
        reference_segment_duration: float | None = None,
    ) -> bool:
        return (
            math.isfinite(duration)
            and reference_segment_duration is not None
            and math.isfinite(reference_segment_duration)
            and reference_segment_duration > 0
            and reference_segment_duration
            <= duration
            < self.direct_alert_duration
        )

    def is_repeated_black(
        self,
        event_count: int,
    ) -> bool:

        return (
            event_count
            >= self.repeated_event_count
        )

    # Compatibility với code/test cũ trong thời gian
    # chuyển baseline.
    def is_long_black(
        self,
        duration: float,
    ) -> bool:

        return self.should_alert_directly(
            duration
        )
