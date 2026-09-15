from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from detectors.macroblocking import MacroblockingAnalyzer
from models.macroblocking import (
    MacroblockingAnalyzerConfig,
    MacroblockingFusionStrategy,
)
from profiles.video_realtime.macroblocking_sampling import (
    MacroblockingSampleWorkspace,
    read_macroblocking_observations,
)


def _analyzer() -> MacroblockingAnalyzer:
    return MacroblockingAnalyzer(
        MacroblockingAnalyzerConfig(
            fusion_strategy=MacroblockingFusionStrategy.MAX_WITH_CONSISTENCY,
            analysis_width=32,
            analysis_height=24,
            sampling_fps=1.0,
            relative_scale_divisors=(8, 4, 2),
        )
    )


def test_reader_preserves_frame_order_and_offsets(tmp_path):
    path = tmp_path / "frames.raw"
    frames = (
        np.arange(32 * 24, dtype=np.uint8).reshape(24, 32),
        np.flipud(np.arange(32 * 24, dtype=np.uint8).reshape(24, 32)),
    )
    path.write_bytes(b"".join(frame.tobytes() for frame in frames))

    observations = read_macroblocking_observations(
        path,
        analyzer=_analyzer(),
        width=32,
        height=24,
        sampling_fps=1.0,
        segment_duration=2.0,
    )

    assert [item.offset_seconds for item in observations] == [0.0, 1.0]


@pytest.mark.parametrize("payload", [b"", b"123"])
def test_reader_rejects_empty_or_truncated_output(tmp_path, payload):
    path = tmp_path / "frames.raw"
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="empty|truncated"):
        read_macroblocking_observations(
            path,
            analyzer=_analyzer(),
            width=32,
            height=24,
            sampling_fps=1.0,
            segment_duration=2.0,
        )


def test_reader_rejects_unbounded_frame_count(tmp_path):
    path = tmp_path / "frames.raw"
    path.write_bytes(bytes(32 * 24 * 5))
    with pytest.raises(ValueError, match="bounded frame count"):
        read_macroblocking_observations(
            path,
            analyzer=_analyzer(),
            width=32,
            height=24,
            sampling_fps=1.0,
            segment_duration=1.0,
        )


def test_workspace_removes_raw_artifact_on_exit():
    with MacroblockingSampleWorkspace() as workspace:
        raw_path = workspace.raw_path
        raw_path.write_bytes(b"sample")
        assert raw_path.exists()
    assert not raw_path.exists()
