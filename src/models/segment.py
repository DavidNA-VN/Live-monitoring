from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ByteRange:
    length: int
    offset: int

    def __post_init__(self) -> None:
        if self.length <= 0:
            raise ValueError("Byte range length must be > 0")

        if self.offset < 0:
            raise ValueError("Byte range offset must be >= 0")

    @property
    def end_exclusive(self) -> int:
        return self.offset + self.length


@dataclass(frozen=True)
class MediaInitializationSection:
    uri: str
    byte_range: ByteRange | None = None


@dataclass(frozen=True)
class SegmentEncryption:
    method: str
    key_uri: str | None = None
    iv: str | None = None
    key_format: str | None = None


@dataclass
class Segment:
    variant_id: str
    variant_stable_id: str
    sequence: int
    uri: str
    duration: float

    program_date_time: datetime | None = None

    discontinuity: bool = False
    discontinuity_sequence: int = 0
    gap: bool = False
    byte_range: ByteRange | None = None
    init_section: MediaInitializationSection | None = None
    encryption: SegmentEncryption | None = None
    has_video: bool = True

    # Assigned by PlaylistObservationTracker, not by the HLS parser.
    timeline_generation: int = 0
    media_revision: str = ""
