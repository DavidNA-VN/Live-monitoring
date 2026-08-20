from dataclasses import dataclass, field
from datetime import datetime

from models.segment import Segment


@dataclass
class MediaPlaylistSnapshot:
    """
    Immutable-in-practice representation of one observed media
    playlist state.

    A snapshot is only one observation of a live playlist. It is not
    the lifetime state of the stream.
    """

    variant_id: str
    variant_stable_id: str
    playlist_uri: str

    media_sequence: int
    discontinuity_sequence: int

    target_duration: float | None
    playlist_type: str | None
    is_endlist: bool

    observed_at: datetime

    part_target: float | None = None
    has_partial_segments: bool = False

    segments: list[Segment] = field(
        default_factory=list
    )

    @property
    def is_live(self) -> bool:
        return not self.is_endlist

    @property
    def first_sequence(self) -> int | None:
        if not self.segments:
            return None

        return self.segments[0].sequence

    @property
    def last_sequence(self) -> int | None:
        if not self.segments:
            return None

        return self.segments[-1].sequence

    @property
    def duration(self) -> float:
        return sum(
            segment.duration
            for segment in self.segments
        )
