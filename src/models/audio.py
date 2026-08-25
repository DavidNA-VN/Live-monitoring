from dataclasses import dataclass
from enum import Enum


class AudioTrackHint(str, Enum):
    """Manifest-visible hint; FFmpeg remains authoritative for muxed media."""

    MUXED = "muxed"
    ABSENT = "absent"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class AudioTrackPresence(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SilenceInterval:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)
