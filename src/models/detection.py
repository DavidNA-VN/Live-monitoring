from dataclasses import dataclass, field
from datetime import datetime

@dataclass
#Khoang thoi gian co black screen trong 1 segment
class BlackInterval:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

#Gan ket qua blackdetection voi 1 segment, 1 segment co the co nhieu khoang black
@dataclass
class BlackDetectionResult:
    variant_id: str
    sequence: int
    segment_uri: str
    segment_duration: float
    program_date_time: datetime | None = None
    checked: bool = True
    error: str | None = None
    retryable: bool = True
    black_intervals: list[BlackInterval] = field(default_factory=list)

    @property
    def has_black(self) -> bool:
        return len(self.black_intervals) > 0

    @property
    def total_black_duration(self) -> float:
        return sum(interval.duration for interval in self.black_intervals)
