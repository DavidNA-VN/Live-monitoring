from dataclasses import dataclass, field


@dataclass
class BlackInterval:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class BlackDetectionResult:
    variant_id: str
    sequence: int
    segment_uri: str
    segment_duration: float

    black_intervals: list[BlackInterval] = field(
        default_factory=list
    )

    @property
    def has_black(self) -> bool:
        return len(self.black_intervals) > 0

    @property
    def total_black_duration(self) -> float:
        return sum(
            interval.duration
            for interval in self.black_intervals
        )