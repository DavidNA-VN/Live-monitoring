from dataclasses import replace

import pytest

from core.playlist_delta import PlaylistDeltaEngine
from models.segment import (
    ByteRange,
    MediaInitializationSection,
    SegmentEncryption,
)
from tests.factories.hls import make_segment, make_snapshot


@pytest.mark.parametrize(
    ("previous_metadata", "current_metadata", "reason"),
    [
        (
            {
                "init_section": MediaInitializationSection(
                    uri="https://media.test/init.mp4",
                    byte_range=ByteRange(100, 0),
                )
            },
            {
                "init_section": MediaInitializationSection(
                    uri="https://media.test/init.mp4",
                    byte_range=ByteRange(120, 0),
                )
            },
            "init_section_changed",
        ),
        (
            {
                "encryption": SegmentEncryption(
                    method="AES-128",
                    key_uri="https://media.test/key.bin",
                    iv="0x1",
                )
            },
            {
                "encryption": SegmentEncryption(
                    method="AES-128",
                    key_uri="https://media.test/key.bin",
                    iv="0x2",
                )
            },
            "encryption_changed",
        ),
    ],
)
def test_delta_detects_media_input_metadata_change(
    previous_metadata,
    current_metadata,
    reason,
):
    base = make_segment(100)
    previous_segment = replace(
        base,
        **previous_metadata,
    )
    current_segment = replace(
        base,
        **current_metadata,
    )
    previous = make_snapshot(
        [100],
        segments=[previous_segment],
    )
    current = make_snapshot(
        [100],
        segments=[current_segment],
    )

    delta = PlaylistDeltaEngine().compare(
        previous,
        current,
    )

    assert delta.timeline_conflict is True
    assert delta.replaced_segments[0].reason == reason

