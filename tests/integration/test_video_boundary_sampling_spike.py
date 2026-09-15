from __future__ import annotations

import shutil
import subprocess

import pytest

from profiles.video_realtime.boundary_sampling import (
    BoundarySampleWorkspace,
    build_boundary_sampling_spike_command,
    read_boundary_fingerprints,
)
from core.frame_similarity import (
    FrameComparisonStatus,
    NormalizedMaeFrameMatcher,
)
from models.analysis import AnalysisRequirement
from profiles.video_realtime import VideoRealtimeProfile
from tests.factories.hls import make_segment


@pytest.mark.integration
def test_one_ffmpeg_process_emits_filter_metadata_and_boundary_samples(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg is unavailable")

    source = tmp_path / "source.mp4"
    generated = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=160x90:rate=10:duration=1",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr

    with BoundarySampleWorkspace() as workspace:
        workspace_path = workspace.path
        command = build_boundary_sampling_spike_command(
            input_uri=str(source),
            raw_output_path=workspace.raw_path,
            filter_expressions=(
                "blackdetect=d=0:pix_th=0.1:pic_th=0.98",
                "freezedetect=n=-60dB:d=0.2",
            ),
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert command.count("ffmpeg") == 1
        assert "freeze_start" in completed.stderr

        first, last = read_boundary_fingerprints(workspace.raw_path)
        comparison = NormalizedMaeFrameMatcher().compare(first, last)
        assert comparison.status is FrameComparisonStatus.MATCH
        assert workspace.raw_path.stat().st_size >= 2 * 32 * 32

    assert not workspace_path.exists()


def test_workspace_cleans_up_after_exception():
    with pytest.raises(RuntimeError):
        with BoundarySampleWorkspace() as workspace:
            workspace_path = workspace.path
            workspace.raw_path.write_bytes(bytes(32 * 32))
            raise RuntimeError("simulated timeout")
    assert not workspace_path.exists()


def test_reader_rejects_truncated_raw_frame(tmp_path):
    raw_path = tmp_path / "truncated.raw"
    raw_path.write_bytes(b"not-a-complete-frame")
    with pytest.raises(ValueError, match="truncated"):
        read_boundary_fingerprints(raw_path)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("color", "expect_freeze"),
    [("black", False), ("red", True)],
)
def test_profile_excludes_black_but_keeps_non_black_static_video(
    tmp_path,
    color,
    expect_freeze,
):
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg is unavailable")

    source = tmp_path / f"{color}.mp4"
    generated = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:size=160x90:rate=10:duration=1",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr

    segment = make_segment(1, duration=1.0)
    segment.uri = str(source)
    profile = VideoRealtimeProfile(timeout=10.0)
    try:
        analysis = profile.analyze(
            segment,
            requirements=frozenset({AnalysisRequirement.FREEZE_INTERVALS}),
        ).require_video_realtime()
    finally:
        profile.close()

    assert analysis.checked, analysis.error
    intervals = analysis.require_output(
        AnalysisRequirement.FREEZE_INTERVALS,
        tuple,
    )
    if not expect_freeze:
        assert intervals == ()
        return

    assert len(intervals) == 1
    assert intervals[0].start_boundary_fingerprint is not None
    assert intervals[0].end_boundary_fingerprint is not None
    assert intervals[0].start_boundary_fingerprint.is_black is False
