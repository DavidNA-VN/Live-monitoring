import shutil
import subprocess

import pytest

from models.analysis import AnalysisRequirement
from models.audio import AudioTrackHint, AudioTrackPresence
from playlist.master_parser import Variant
from playlist.media_parser import parse_media_playlist
from profiles.audio_realtime import AudioRealtimeProfile


pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("name", "hls_options"),
    [
        ("single-file-ts", ["-hls_flags", "single_file"]),
        ("fragmented-mp4", ["-hls_segment_type", "fmp4"]),
    ],
)
@pytest.mark.parametrize(
    ("source", "expect_silence"),
    [
        ("sine=frequency=1000:sample_rate=48000:duration=1", False),
        (
            "anoisesrc=amplitude=0.0001:sample_rate=48000:duration=1",
            True,
        ),
    ],
)
def test_generated_hls_audio_is_resolved_and_analyzed(
    tmp_path,
    name,
    hls_options,
    source,
    expect_silence,
):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is not installed")

    playlist_path = tmp_path / f"{name}.m3u8"
    generated = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            source,
            "-t",
            "1",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-f",
            "hls",
            "-hls_time",
            "0.5",
            "-hls_list_size",
            "0",
            *hls_options,
            str(playlist_path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        cwd=tmp_path,
    )
    if generated.returncode != 0:
        pytest.skip(
            "FFmpeg cannot generate the audio HLS fixture: "
            f"{generated.stderr.strip()}"
        )

    variant = Variant(
        id="audio",
        stable_id="audio-main",
        uri=playlist_path.as_uri(),
        bandwidth=128_000,
        resolution=None,
        codecs="mp4a.40.2",
        has_video=False,
        audio_track_hint=AudioTrackHint.MUXED,
    )
    segment = parse_media_playlist(variant).segments[0]
    profile = AudioRealtimeProfile(timeout=10.0)
    try:
        analysis = profile.analyze(segment).require_audio_realtime()
    finally:
        profile.close()

    assert analysis.checked is True, analysis.error
    assert analysis.presence is AudioTrackPresence.PRESENT
    intervals = analysis.require_output(
        AnalysisRequirement.SILENCE_INTERVALS,
        tuple,
    )
    if expect_silence:
        assert len(intervals) == 1
        assert intervals[0].start == pytest.approx(0.0, abs=0.03)
        assert intervals[0].end == pytest.approx(
            segment.duration,
            abs=0.03,
        )
    else:
        assert intervals == ()
