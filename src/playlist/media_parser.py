from datetime import datetime, timezone
from collections.abc import Mapping

import m3u8

from models.playlist_snapshot import (
    MediaPlaylistSnapshot,
)
from models.segment import Segment
from playlist.master_parser import Variant
from playlist.errors import PlaylistLoadError
from playlist.media_metadata import (
    MediaMetadataTracker,
    segment_encryption,
)


def parse_media_playlist(
    variant: Variant,
    timeout: float = 5.0,
    request_headers: Mapping[str, str] | None = None,
) -> MediaPlaylistSnapshot:

    try:
        load_options = {
            "timeout": timeout,
        }

        if request_headers:
            load_options["headers"] = dict(
                request_headers
            )

        playlist = m3u8.load(
            variant.uri,
            **load_options,
        )
    except (
        OSError,
        ValueError,
        m3u8.ParseError,
    ) as exc:
        raise PlaylistLoadError(
            uri=variant.uri,
            message=str(exc),
        ) from exc

    if playlist.is_variant:
        raise ValueError(
            (
                "Expected media playlist for "
                f"variant {variant.id!r}, "
                "but received another master playlist."
            )
        )

    observed_at = datetime.now(
        timezone.utc
    )

    media_sequence = int(
        playlist.media_sequence or 0
    )

    base_discontinuity_sequence = int(
        playlist.discontinuity_sequence or 0
    )

    current_discontinuity_sequence = (
        base_discontinuity_sequence
    )

    segments: list[Segment] = []
    metadata_tracker = MediaMetadataTracker()

    for index, playlist_segment in enumerate(
        playlist.segments
    ):
        segment_sequence = getattr(
            playlist_segment,
            "media_sequence",
            None,
        )

        if segment_sequence is None:
            sequence = (
                media_sequence
                + index
            )
        else:
            sequence = int(
                segment_sequence
            )

        has_discontinuity = bool(
            getattr(
                playlist_segment,
                "discontinuity",
                False,
            )
        )

        if has_discontinuity:
            current_discontinuity_sequence += 1

        effective_program_date_time = getattr(
            playlist_segment,
            "current_program_date_time",
            None,
        )

        if effective_program_date_time is None:
            effective_program_date_time = getattr(
                playlist_segment,
                "program_date_time",
                None,
            )

        gap = bool(
            getattr(
                playlist_segment,
                "gap_tag",
                False,
            )
            or getattr(
                playlist_segment,
                "gap",
                False,
            )
        )

        segment_uri = playlist_segment.absolute_uri

        raw_byte_range = getattr(
            playlist_segment,
            "byterange",
            None,
        )

        byte_range = metadata_tracker.segment_byte_range(
            segment_uri,
            raw_byte_range,
        )

        raw_init_section = getattr(
            playlist_segment,
            "init_section",
            None,
        )

        init_section = (
            metadata_tracker.initialization_section(
                raw_init_section
            )
        )

        segments.append(
            Segment(
                variant_id=variant.id,
                variant_stable_id=variant.stable_id,
                sequence=sequence,
                uri=segment_uri,
                duration=float(
                    playlist_segment.duration
                ),
                program_date_time=(
                    effective_program_date_time
                ),
                discontinuity=(
                    has_discontinuity
                ),
                discontinuity_sequence=(
                    current_discontinuity_sequence
                ),
                gap=gap,
                byte_range=byte_range,
                init_section=init_section,
                encryption=(
                    segment_encryption(
                        playlist_segment
                    )
                ),
                has_video=variant.has_video,
            )
        )

    target_duration = (
        float(playlist.target_duration)
        if playlist.target_duration is not None
        else None
    )

    part_information = getattr(
        playlist,
        "part_inf",
        None,
    )
    raw_part_target = getattr(
        part_information,
        "part_target",
        None,
    )
    part_target = (
        float(raw_part_target)
        if raw_part_target is not None
        else None
    )
    has_partial_segments = any(
        bool(
            getattr(
                playlist_segment,
                "parts",
                [],
            )
        )
        for playlist_segment in playlist.segments
    )

    return MediaPlaylistSnapshot(
        variant_id=variant.id,
        variant_stable_id=variant.stable_id,
        playlist_uri=variant.uri,
        media_sequence=media_sequence,
        discontinuity_sequence=(
            base_discontinuity_sequence
        ),
        target_duration=target_duration,
        playlist_type=playlist.playlist_type,
        is_endlist=bool(
            playlist.is_endlist
        ),
        observed_at=observed_at,
        part_target=part_target,
        has_partial_segments=(
            has_partial_segments
        ),
        segments=segments,
    )
