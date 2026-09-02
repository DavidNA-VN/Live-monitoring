from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from models.analysis import AnalysisRequirement
from playlist.master_parser import Variant
from playlist.media_parser import parse_media_playlist
from profiles.video_realtime import VideoRealtimeProfile
from scripts.generate_video_freeze_fixtures import generate_fixtures


FREEZE_LINE = re.compile(
    r"lavfi\.freezedetect\.freeze_(start|duration|end):\s*([0-9.]+)"
)


def _detected_values(playlist) -> list[tuple[str, float]]:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "info",
            "-i",
            str(playlist),
            "-vf",
            "freezedetect=n=-60dB:d=0.2",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return [
        (match.group(1), float(match.group(2)))
        for match in FREEZE_LINE.finditer(completed.stderr)
    ]


@pytest.mark.integration
def test_generated_hls_has_exact_freeze_business_boundaries(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg is unavailable")
    output = tmp_path / "freeze-fixtures"
    generate_fixtures(output)

    filters = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert filters.returncode == 0
    assert "freezedetect" in filters.stdout

    expected = {
        "freeze_2_9s": [("start", 2.0), ("duration", 2.9), ("end", 4.9)],
        "freeze_3_2s": [("start", 2.0), ("duration", 3.2), ("end", 5.2)],
        "freeze_5_2s": [("start", 2.0), ("duration", 5.2), ("end", 7.2)],
        "freeze_cross_three_segments": [
            ("start", 1.2),
            ("duration", 5.6),
            ("end", 6.8),
        ],
    }
    for fixture_name, values in expected.items():
        playlist = output / fixture_name / "low" / "playlist.m3u8"
        detected = _detected_values(playlist)
        assert [name for name, _ in detected] == [name for name, _ in values]
        assert [value for _, value in detected] == pytest.approx(
            [value for _, value in values],
            abs=0.05,
        )

    playlist = output / "freeze_5_2s" / "low" / "playlist.m3u8"
    variant = Variant(
        id="low",
        stable_id="freeze-low",
        uri=playlist.as_uri(),
        bandwidth=250_000,
        resolution=(160, 90),
    )
    segments = parse_media_playlist(variant).segments
    profile = VideoRealtimeProfile(timeout=10.0)
    observed = []
    try:
        for index, segment in enumerate(segments[1:4], start=1):
            segment.uri = str(playlist.parent / f"segment_{index:05d}.ts")
            analysis = profile.analyze(
                segment,
                requirements=frozenset(
                    {AnalysisRequirement.FREEZE_INTERVALS}
                ),
            ).require_video_realtime()
            assert analysis.checked, analysis.error
            intervals = analysis.require_output(
                AnalysisRequirement.FREEZE_INTERVALS,
                tuple,
            )
            observed.append(
                [(item.start, item.end) for item in intervals]
            )
    finally:
        profile.close()

    assert observed == [
        [(0.0, pytest.approx(segments[1].duration))],
        [(0.0, pytest.approx(segments[2].duration))],
        [(0.0, pytest.approx(1.2, abs=0.05))],
    ]
