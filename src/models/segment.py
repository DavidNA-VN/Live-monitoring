from dataclasses import dataclass


@dataclass
class Segment:
    variant_id: str
    sequence: int
    uri: str
    duration: float