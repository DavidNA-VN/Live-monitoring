from __future__ import annotations

from models.segment import (
    ByteRange,
    MediaInitializationSection,
    SegmentEncryption,
)


def parse_byte_range(
    raw_value: str | None,
    *,
    implicit_offset: int | None,
) -> ByteRange | None:
    if not raw_value:
        return None

    length_text, separator, offset_text = (
        raw_value.partition("@")
    )

    try:
        length = int(length_text)
        offset = (
            int(offset_text)
            if separator
            else implicit_offset
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid HLS byte range: {raw_value!r}"
        ) from exc

    if offset is None:
        raise ValueError(
            (
                "HLS byte range omitted its offset "
                "without a previous range for the "
                "same resource."
            )
        )

    return ByteRange(
        length=length,
        offset=offset,
    )


class MediaMetadataTracker:
    def __init__(self) -> None:
        self.previous_range_uri: str | None = None
        self.previous_range_end: int | None = None
        self.next_init_offset_by_uri: dict[str, int] = {}
        self.init_section_cache: dict[
            tuple[str, str | None],
            MediaInitializationSection,
        ] = {}

    def segment_byte_range(
        self,
        uri: str,
        raw_value: str | None,
    ) -> ByteRange | None:
        byte_range = parse_byte_range(
            raw_value,
            implicit_offset=(
                self.previous_range_end
                if self.previous_range_uri == uri
                else None
            ),
        )

        if byte_range is None:
            self.previous_range_uri = None
            self.previous_range_end = None
        else:
            self.previous_range_uri = uri
            self.previous_range_end = (
                byte_range.end_exclusive
            )

        return byte_range

    def initialization_section(
        self,
        raw_section,
    ) -> MediaInitializationSection | None:
        if raw_section is None:
            return None

        uri = raw_section.absolute_uri
        raw_range = getattr(
            raw_section,
            "byterange",
            None,
        )
        cache_key = (uri, raw_range)
        cached = self.init_section_cache.get(
            cache_key
        )

        if cached is not None:
            return cached

        byte_range = parse_byte_range(
            raw_range,
            implicit_offset=(
                self.next_init_offset_by_uri.get(uri)
            ),
        )
        section = MediaInitializationSection(
            uri=uri,
            byte_range=byte_range,
        )
        self.init_section_cache[cache_key] = section

        if byte_range is not None:
            self.next_init_offset_by_uri[
                uri
            ] = byte_range.end_exclusive

        return section


def segment_encryption(
    playlist_segment,
) -> SegmentEncryption | None:
    key = getattr(
        playlist_segment,
        "key",
        None,
    )
    method = getattr(
        key,
        "method",
        None,
    )

    if not method or method.upper() == "NONE":
        return None

    return SegmentEncryption(
        method=method.upper(),
        key_uri=getattr(
            key,
            "absolute_uri",
            None,
        ),
        iv=getattr(key, "iv", None),
        key_format=getattr(
            key,
            "keyformat",
            None,
        ),
    )

