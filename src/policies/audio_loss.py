import math


class AudioLossAlertPolicy:
    def __init__(self, *, alert_duration: float = 30.0) -> None:
        if not math.isfinite(alert_duration) or alert_duration <= 0:
            raise ValueError("alert_duration must be finite and > 0")
        self.alert_duration = alert_duration

    def should_alert(self, duration: float) -> bool:
        return duration >= self.alert_duration
