from dataclasses import dataclass
from enum import Enum


class StartupAdmissionMode(str, Enum):
    BOUNDED_HISTORY = "bounded_history"
    FULL_SNAPSHOT = "full_snapshot"


@dataclass(frozen=True)
class LiveAdmissionPolicy:
    startup_mode: StartupAdmissionMode = StartupAdmissionMode.BOUNDED_HISTORY
    startup_lookback_segments: int = 4

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "startup_mode",
            StartupAdmissionMode(self.startup_mode),
        )
        if self.startup_lookback_segments <= 0:
            raise ValueError("startup_lookback_segments must be > 0")
