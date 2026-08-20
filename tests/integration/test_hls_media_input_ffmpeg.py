import shutil
import subprocess

import pytest

from profiles.video_realtime import VideoRealtimeProfile
from playlist.master_parser import Variant
from playlist.media_parser import parse_media_playlist


pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("name", "hls_options"),
    [
        (
            "single-file-ts",
            ["-hls_flags", "single_file"],
        ),
        (
            "fragmented-mp4",
            ["-hls_segment_type", "fmp4"],
        ),
    ],
)
def test_generated_hls_media_is_resolved_and_analyzed(
    tmp_path,
    name,
    hls_options,
):
    ffmpeg = shutil.which("ffmpeg")

    if ffmpeg is None:
        pytest.skip("FFmpeg is not installed")

    playlist_path = tmp_path / f"{name}.m3u8"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=64x64:r=10:d=1",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-g",
        "10",
        "-an",
        "-f",
        "hls",
        "-hls_time",
        "0.5",
        "-hls_list_size",
        "0",
        *hls_options,
        str(playlist_path),
    ]
    generated = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        cwd=tmp_path,
    )

    if generated.returncode != 0:
        pytest.skip(
            "FFmpeg cannot generate the HLS fixture: "
            f"{generated.stderr.strip()}"
        )

    variant = Variant(
        id="test",
        stable_id="test-video",
        uri=playlist_path.as_uri(),
        bandwidth=100_000,
        resolution=(64, 64),
    )
    snapshot = parse_media_playlist(variant)
    segment = snapshot.segments[0]
    profile = VideoRealtimeProfile(timeout=10.0)

    try:
        result = profile.analyze(
            segment
        ).require_video_realtime()
    finally:
        profile.close()

    assert result.checked is True, result.error
    assert result.black_intervals
