from datetime import datetime, timezone

from models.playlist_snapshot import MediaPlaylistSnapshot
from models.segment import Segment


def make_segment(
    sequence: int,
    *,
    duration: float = 6.0,
    variant_id: str = "720p",
    variant_stable_id: str = "v720",
    discontinuity_sequence: int = 0,
    gap: bool = False,
    program_date_time=None,
    uri: str | None = None,
) -> Segment:
    return Segment(
        variant_id=variant_id,
        variant_stable_id=variant_stable_id,
        sequence=sequence,
        uri=uri
        or f"https://example.test/{variant_id}/{sequence}.ts",
        duration=duration,
        program_date_time=program_date_time,
        discontinuity_sequence=discontinuity_sequence,
        gap=gap,
    )


def make_snapshot(
    sequences: list[int],
    *,
    media_sequence: int | None = None,
    variant_id: str = "720p",
    variant_stable_id: str = "v720",
    discontinuity_sequence: int = 0,
    observed_at: datetime | None = None,
    segments: list[Segment] | None = None,
) -> MediaPlaylistSnapshot:
    snapshot_segments = segments or [
        make_segment(
            sequence,
            variant_id=variant_id,
            variant_stable_id=variant_stable_id,
            discontinuity_sequence=discontinuity_sequence,
        )
        for sequence in sequences
    ]

    first_sequence = (
        sequences[0]
        if sequences
        else (
            snapshot_segments[0].sequence
            if snapshot_segments
            else 0
        )
    )

    return MediaPlaylistSnapshot(
        variant_id=variant_id,
        variant_stable_id=variant_stable_id,
        playlist_uri=(
            f"https://example.test/{variant_id}/index.m3u8"
        ),
        media_sequence=(
            media_sequence
            if media_sequence is not None
            else first_sequence
        ),
        discontinuity_sequence=discontinuity_sequence,
        target_duration=6.0,
        playlist_type=None,
        is_endlist=False,
        observed_at=observed_at
        or datetime.now(timezone.utc),
        segments=snapshot_segments,
    )
