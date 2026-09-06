from dataclasses import dataclass
from enum import Enum
import math


class StartupAdmissionMode(str, Enum):
    BOUNDED_HISTORY = "bounded_history"
    FULL_SNAPSHOT = "full_snapshot"


class AdmissionMode(str, Enum):
    COVERAGE = "coverage"
    CATCH_UP = "catch_up"


@dataclass(frozen=True)
class LiveAdmissionPolicy:
    startup_mode: StartupAdmissionMode = StartupAdmissionMode.BOUNDED_HISTORY
    startup_lookback_segments: int = 4
    soft_lag_target_durations: float = 2.0
    recovery_lag_target_durations: float = 1.5
    transition_cycles: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "startup_mode",
            StartupAdmissionMode(self.startup_mode),
        )
        if self.startup_lookback_segments <= 0:
            raise ValueError("startup_lookback_segments must be > 0")
        lag_values = {
            "soft_lag_target_durations": self.soft_lag_target_durations,
            "recovery_lag_target_durations": (
                self.recovery_lag_target_durations
            ),
        }
        for name, value in lag_values.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and > 0")
        if (
            self.recovery_lag_target_durations
            >= self.soft_lag_target_durations
        ):
            raise ValueError(
                "recovery_lag_target_durations must be less than "
                "soft_lag_target_durations"
            )
        if self.transition_cycles <= 0:
            raise ValueError("transition_cycles must be > 0")
