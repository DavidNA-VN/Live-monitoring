class BlackScreenAlertPolicy:

    def __init__(
        self,
        direct_alert_duration: float = 3.0,
        repeated_event_count: int = 3,
        repeated_window: float = 120.0,
        repeated_update_every: int = 3,
        repeated_recovery_window: float = 120.0,
    ):
        if direct_alert_duration <= 0:
            raise ValueError(
                "direct_alert_duration must be > 0"
            )

        if repeated_event_count <= 0:
            raise ValueError(
                "repeated_event_count must be > 0"
            )

        if repeated_window <= 0:
            raise ValueError(
                "repeated_window must be > 0"
            )

        if repeated_update_every <= 0:
            raise ValueError(
                "repeated_update_every must be > 0"
            )

        if repeated_recovery_window <= 0:
            raise ValueError(
                (
                    "repeated_recovery_window "
                    "must be > 0"
                )
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

        self.repeated_recovery_window = (
            repeated_recovery_window
        )

    def should_alert_directly(
        self,
        duration: float,
    ) -> bool:

        return (
            duration
            >= self.direct_alert_duration
        )

    def is_repeated_candidate(
        self,
        duration: float,
    ) -> bool:

        return (
            0
            < duration
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