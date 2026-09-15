from __future__ import annotations

import io

import numpy as np

from models.macroblocking import (
    MacroblockingAnalyzerConfig,
    MacroblockingFrameObservation,
    MacroblockingFusionStrategy,
)
from scripts import run_macroblocking_analyzer as runner


def test_read_exact_joins_partial_reads_and_rejects_truncated_frame():
    assert runner._read_exact(io.BytesIO(b"abcdef"), 6) == b"abcdef"

    try:
        runner._read_exact(io.BytesIO(b"abc"), 6)
    except RuntimeError as exc:
        assert "truncated" in str(exc)
    else:
        raise AssertionError("truncated frame must fail")


def test_analyze_manifest_keeps_business_area_outside_analyzer(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner,
        "load_manifest",
        lambda _: {
            "fixtures": [{"name": "sample", "playlist": "master.m3u8"}]
        },
    )
    frame = np.zeros((64, 64), dtype=np.uint8)
    monkeypatch.setattr(
        runner,
        "iter_luminance_frames",
        lambda *args, **kwargs: iter((frame,)),
    )
    class FakeAnalyzer:
        def __init__(self, config):
            self.config = config

        def analyze(self, frame, *, offset_seconds):
            return MacroblockingFrameObservation(
                offset_seconds=offset_seconds,
                affected_area_ratio=0.20,
                blocking_confidence=0.90,
                boundary_support_ratio=0.80,
            )

    monkeypatch.setattr(runner, "MacroblockingAnalyzer", FakeAnalyzer)
    config = MacroblockingAnalyzerConfig(
        fusion_strategy=MacroblockingFusionStrategy.MAX,
        analysis_width=64,
        analysis_height=64,
        relative_scale_divisors=(8, 4, 2),
        detector_confidence_threshold=0.0,
        heatmap_threshold=0.5,
        minimum_support_ratio=0.0,
    )

    low_threshold = runner.analyze_manifest(
        tmp_path / "manifest.json",
        config=config,
        area_threshold=0.01,
    )
    high_threshold = runner.analyze_manifest(
        tmp_path / "manifest.json",
        config=config,
        area_threshold=0.50,
    )

    low_observation = low_threshold["observations"][0]
    high_observation = high_threshold["observations"][0]
    assert low_observation["affected_area_ratio"] == high_observation[
        "affected_area_ratio"
    ]
    assert low_observation["candidate"] is True
    assert high_observation["candidate"] is False


def test_working_set_probe_is_non_negative():
    assert runner.current_working_set_bytes() >= 0
