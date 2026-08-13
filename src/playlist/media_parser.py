import m3u8

from models.segment import Segment
from playlist.master_parser import Variant


def parse_media_playlist(
    variant: Variant,
) -> list[Segment]:

    playlist = m3u8.load(variant.uri)

    media_sequence = playlist.media_sequence or 0

    segments: list[Segment] = []

    for index, segment in enumerate(playlist.segments):
        sequence = media_sequence + index

        segments.append(
            Segment(
                variant_id=variant.id,
                sequence=sequence,
                uri=segment.absolute_uri,
                duration=float(segment.duration),
            )
        )

    return segments