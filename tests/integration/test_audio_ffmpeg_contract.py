import shutil
import subprocess
from pathlib import Path

import pytest

from models.analysis import AnalysisRequirement
from models.audio import AudioTrackPresence
from profiles.audio_realtime import AudioRealtimeProfile
from tests.factories.hls import make_segment


pytestmark = pytest.mark.integration


def _run(command: list[str], *, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _generate(ffmpeg: str, output: Path, *input_and_output_options: str) -> None:
    result = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *input_and_output_options,
            str(output),
        ]
    )
    if result.returncode != 0:
        pytest.skip(f"FFmpeg cannot generate {output.name}: {result.stderr.strip()}")


def _corrupt_payload(source: Path, destination: Path) -> None:
    payload = bytearray(source.read_bytes())
    start = int(len(payload) * 0.65)
    end = int(len(payload) * 0.75)
    payload[start:end] = bytes(end - start)
    destination.write_bytes(payload)


def test_ffmpeg_audio_presence_and_silence_contract(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is not installed")

    audible = tmp_path / "audible.m4a"
    silence = tmp_path / "silence.m4a"
    no_audio = tmp_path / "no_audio.mp4"
    one_side_silent = tmp_path / "one_side_silent.m4a"
    corrupt = tmp_path / "corrupt.m4a"

    _generate(
        ffmpeg,
        audible,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=48000:duration=2",
        "-af",
        "pan=stereo|c0=c0|c1=c0",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
    )
    _generate(
        ffmpeg,
        silence,
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        "-t",
        "2",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
    )
    _generate(
        ffmpeg,
        no_audio,
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x64:r=10:d=2",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-an",
    )
    _generate(
        ffmpeg,
        one_side_silent,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=48000:duration=2",
        "-filter_complex",
        "[0:a]pan=stereo|c0=c0|c1=0*c0[a]",
        "-map",
        "[a]",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
    )
    _corrupt_payload(audible, corrupt)

    profile = AudioRealtimeProfile(timeout=10.0)
    try:
        results = {
            name: profile.analyze(
                make_segment(
                    sequence,
                    duration=2.0,
                    uri=str(media),
                )
            ).require_audio_realtime()
            for sequence, (name, media) in enumerate(
                {
                    "audible": audible,
                    "silence": silence,
                    "no_audio": no_audio,
                    "corrupt": corrupt,
                    "one_side_silent": one_side_silent,
                }.items()
            )
        }
    finally:
        profile.close()

    assert results["audible"].presence is AudioTrackPresence.PRESENT
    assert results["audible"].require_output(
        AnalysisRequirement.SILENCE_INTERVALS,
        tuple,
    ) == ()

    assert results["silence"].presence is AudioTrackPresence.PRESENT
    silence_intervals = results["silence"].require_output(
        AnalysisRequirement.SILENCE_INTERVALS,
        tuple,
    )
    assert len(silence_intervals) == 1
    assert silence_intervals[0].duration == pytest.approx(2.0, abs=0.03)

    assert results["no_audio"].presence is AudioTrackPresence.ABSENT
    assert results["no_audio"].checked is True

    assert results["corrupt"].presence is AudioTrackPresence.UNKNOWN
    assert results["corrupt"].checked is False
    assert "Invalid data" in results["corrupt"].error

    assert results["one_side_silent"].presence is AudioTrackPresence.PRESENT
    assert results["one_side_silent"].require_output(
        AnalysisRequirement.SILENCE_INTERVALS,
        tuple,
    ) == ()


@pytest.mark.parametrize(
    ("suffix", "codec", "sample_rate", "channel_layout"),
    [
        ("m4a", "aac", 44_100, "mono"),
        ("m4a", "aac", 48_000, "stereo"),
        ("mp3", "libmp3lame", 44_100, "mono"),
        ("mp3", "libmp3lame", 48_000, "stereo"),
    ],
)
def test_profile_handles_codec_rate_and_layout_changes(
    tmp_path: Path,
    suffix: str,
    codec: str,
    sample_rate: int,
    channel_layout: str,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is not installed")
    media = tmp_path / f"silence-{sample_rate}-{channel_layout}.{suffix}"
    _generate(
        ffmpeg,
        media,
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r={sample_rate}:cl={channel_layout}",
        "-t",
        "1",
        "-c:a",
        codec,
    )

    profile = AudioRealtimeProfile(timeout=10.0)
    try:
        analysis = profile.analyze(
            make_segment(1, duration=1.0, uri=str(media))
        ).require_audio_realtime()
    finally:
        profile.close()

    assert analysis.checked is True, analysis.error
    assert analysis.presence is AudioTrackPresence.PRESENT
    intervals = analysis.require_output(
        AnalysisRequirement.SILENCE_INTERVALS,
        tuple,
    )
    assert len(intervals) == 1
    assert intervals[0].duration == pytest.approx(1.0, abs=0.08)
