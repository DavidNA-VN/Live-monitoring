from __future__ import annotations

import shutil
import subprocess

import pytest

from detectors.macroblocking import MacroblockingAnalyzer
from models.analysis import AnalysisRequirement
from models.macroblocking import (
    MacroblockingAnalyzerConfig,
    MacroblockingFusionStrategy,
)
from profiles.video_realtime import VideoRealtimeProfile
from tests.factories.hls import make_segment


@pytest.mark.integration
def test_one_decode_produces_filter_metadata_and_macroblocking_frames(tmp_path):
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
            "testsrc2=size=160x90:rate=10:duration=1",
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

    analyzer = MacroblockingAnalyzer(
        MacroblockingAnalyzerConfig(
            fusion_strategy=MacroblockingFusionStrategy.MAX_WITH_CONSISTENCY,
            analysis_width=64,
            analysis_height=36,
            sampling_fps=1.0,
            relative_scale_divisors=(8, 4, 2),
        )
    )
    profile = VideoRealtimeProfile(
        timeout=10.0,
        enable_macroblocking=True,
        macroblocking_analyzer=analyzer,
    )
    segment = make_segment(1, duration=1.0, uri=str(source))
    try:
        analysis = profile.analyze(
            segment,
            requirements=frozenset(
                {
                    AnalysisRequirement.BLACK_INTERVALS,
                    AnalysisRequirement.FREEZE_INTERVALS,
                    AnalysisRequirement.MACROBLOCKING_OBSERVATIONS,
                }
            ),
        ).require_video_realtime()
    finally:
        profile.close()

    assert analysis.checked, analysis.error
    assert isinstance(
        analysis.require_output(AnalysisRequirement.BLACK_INTERVALS, tuple),
        tuple,
    )
    assert isinstance(
        analysis.require_output(AnalysisRequirement.FREEZE_INTERVALS, tuple),
        tuple,
    )
    observations = analysis.require_output(
        AnalysisRequirement.MACROBLOCKING_OBSERVATIONS,
        tuple,
    )
    assert len(observations) == 1
    assert observations[0].offset_seconds == 0.0
    assert analysis.diagnostics["macroblocking_sample_total"] == 1
