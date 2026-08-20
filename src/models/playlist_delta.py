from dataclasses import dataclass, field
from datetime import datetime

from models.segment import Segment


@dataclass(frozen=True)
class MissedSequenceRange:
    start_sequence: int
    end_sequence: int

    @property
    def count(self) -> int:
        return max(
            0,
            self.end_sequence
            - self.start_sequence
            + 1,
        )


@dataclass(frozen=True)
class SegmentReplacement:
    """
    Same media-sequence number appeared in two consecutive snapshots,
    but the underlying timeline/resource no longer looks equivalent.

    This is not treated as an ordinary retained segment.
    """

    previous: Segment
    current: Segment
    reason: str


@dataclass
class MediaPlaylistDelta:
    variant_id: str

    previous_observed_at: datetime
    current_observed_at: datetime

    new_segments: list[Segment] = field(
        default_factory=list
    )

    retained_segments: list[Segment] = field(
        default_factory=list
    )

    replaced_segments: list[
        SegmentReplacement
    ] = field(
        default_factory=list
    )

    removed_sequences: list[int] = field(
        default_factory=list
    )

    declared_gap_segments: list[Segment] = field(
        default_factory=list
    )

    missed_sequence_ranges: list[
        MissedSequenceRange
    ] = field(
        default_factory=list
    )

    timeline_reset: bool = False
    timeline_reset_reason: str | None = None

    timeline_conflict: bool = False

    @property
    def has_new_media(self) -> bool:
        return bool(
            self.new_segments
            or self.replaced_segments
        )

    @property
    def has_observation_gap(self) -> bool:
        return bool(
            self.missed_sequence_ranges
            or self.declared_gap_segments
        )

    @property
    def safe_for_event_continuity(self) -> bool:
        return not (
            self.timeline_reset
            or self.timeline_conflict
            or self.has_observation_gap
        )