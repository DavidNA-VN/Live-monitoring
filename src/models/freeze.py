from dataclasses import dataclass


@dataclass
class FreezeInterval:
    start: float
    end: float
    duration: float


@dataclass
class FreezeEvent:
    variant_id: str

    start_time: float
    end_time: float
    duration: float

    start_sequence: int
    end_sequence: int

    start_offset: float
    end_offset: float

    affected_segments: list[int]