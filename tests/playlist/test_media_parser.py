from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from playlist import media_parser
from playlist.master_parser import Variant
import m3u8


def test_parse_media_playlist_copies_program_date_time(
    monkeypatch,
):
    program_date_time = datetime(
        2026,
        8,
        17,
        10,
        0,
        0,
        tzinfo=timezone.utc,
    )

    monkeypatch.setattr(
        media_parser.m3u8,
        "load",
        lambda _uri, timeout: SimpleNamespace(
            is_variant=False,
            media_sequence=42,
            discontinuity_sequence=0,
            target_duration=2.0,
            playlist_type=None,
            is_endlist=False,
            segments=[
                SimpleNamespace(
                    absolute_uri="http://example.test/seg.ts",
                    duration=2.0,
                    current_program_date_time=None,
                    program_date_time=program_date_time,
                    discontinuity=False,
                    gap_tag=False,
                    gap=False,
                    byterange=None,
                )
            ],
        ),
    )

    variant = Variant(
        id="720p",
        stable_id="v720",
        uri="http://example.test/playlist.m3u8",
        bandwidth=1_000_000,
        resolution=(1280, 720),
    )

    snapshot = media_parser.parse_media_playlist(
        variant
    )

    segments = snapshot.segments

    assert snapshot.variant_stable_id == "v720"
    assert segments[0].sequence == 42
    assert segments[0].program_date_time == program_date_time


def test_parse_media_playlist_normalizes_hls_media_metadata(
    monkeypatch,
):
    playlist = m3u8.loads(
        """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:10
#EXT-X-MAP:URI="init.mp4",BYTERANGE="100@0"
#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x1
#EXTINF:4,
#EXT-X-BYTERANGE:5@100
media.mp4
#EXTINF:4,
#EXT-X-BYTERANGE:6
media.mp4
""",
        uri="https://media.test/live/index.m3u8",
    )
    monkeypatch.setattr(
        media_parser.m3u8,
        "load",
        lambda _uri, timeout: playlist,
    )
    variant = Variant(
        id="720p",
        stable_id="v720",
        uri="https://media.test/live/index.m3u8",
        bandwidth=1_000_000,
        resolution=(1280, 720),
    )

    snapshot = media_parser.parse_media_playlist(
        variant
    )
    first, second = snapshot.segments

    assert first.byte_range.length == 5
    assert first.byte_range.offset == 100
    assert second.byte_range.length == 6
    assert second.byte_range.offset == 105
    assert first.init_section.uri == (
        "https://media.test/live/init.mp4"
    )
    assert first.init_section.byte_range.length == 100
    assert first.init_section.byte_range.offset == 0
    assert first.encryption.method == "AES-128"
    assert first.encryption.key_uri == (
        "https://media.test/live/key.bin"
    )
    assert first.encryption.iv == "0x1"


def test_implicit_byte_range_requires_previous_same_resource(
    monkeypatch,
):
    playlist = m3u8.loads(
        """#EXTM3U
#EXT-X-TARGETDURATION:4
#EXTINF:4,
#EXT-X-BYTERANGE:5
media.ts
""",
        uri="https://media.test/live/index.m3u8",
    )
    monkeypatch.setattr(
        media_parser.m3u8,
        "load",
        lambda _uri, timeout: playlist,
    )
    variant = Variant(
        id="720p",
        stable_id="v720",
        uri="https://media.test/live/index.m3u8",
        bandwidth=1_000_000,
        resolution=(1280, 720),
    )

    with pytest.raises(
        ValueError,
        match="omitted its offset",
    ):
        media_parser.parse_media_playlist(
            variant
        )


def test_low_latency_metadata_is_recorded_but_only_full_segments_are_used(
    monkeypatch,
):
    playlist = m3u8.loads(
        """#EXTM3U
#EXT-X-VERSION:9
#EXT-X-TARGETDURATION:4
#EXT-X-PART-INF:PART-TARGET=0.5
#EXT-X-PART:DURATION=0.5,URI="part-1.m4s"
#EXT-X-PART:DURATION=0.5,URI="part-2.m4s"
#EXTINF:4,
segment.m4s
""",
        uri="https://media.test/live/index.m3u8",
    )
    monkeypatch.setattr(
        media_parser.m3u8,
        "load",
        lambda _uri, timeout: playlist,
    )
    variant = Variant(
        id="720p",
        stable_id="v720",
        uri="https://media.test/live/index.m3u8",
        bandwidth=1_000_000,
        resolution=(1280, 720),
    )

    snapshot = media_parser.parse_media_playlist(
        variant
    )

    assert snapshot.part_target == 0.5
    assert snapshot.has_partial_segments is True
    assert len(snapshot.segments) == 1
    assert snapshot.segments[0].uri.endswith(
        "segment.m4s"
    )
