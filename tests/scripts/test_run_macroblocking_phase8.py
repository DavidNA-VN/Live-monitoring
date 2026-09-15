from pathlib import Path

import numpy as np

from models.macroblocking import MacroblockingFusionStrategy
from scripts.run_macroblocking_phase8 import (
    MatrixCase,
    _longest_true_run,
    matrix_cases,
    run_source_smoke,
)


def test_matrix_cases_build_cartesian_product():
    cases = matrix_cases(
        resolutions=((480, 270), (640, 360)),
        sampling_rates=(1.0, 3.0),
        fusions=(MacroblockingFusionStrategy.MAX,),
    )

    assert len(cases) == 4
    assert cases[0].name == "480x270-1fps-max"
    assert cases[-1] == MatrixCase(
        640,
        360,
        3.0,
        MacroblockingFusionStrategy.MAX,
    )


def test_longest_true_run_does_not_join_observation_gaps():
    assert _longest_true_run((True, True, False, True, True, True)) == 3
    assert _longest_true_run(()) == 0


def test_source_smoke_reports_temporal_gate(monkeypatch):
    frames = [np.full((8, 8), index, dtype=np.uint8) for index in range(12)]
    monkeypatch.setattr(
        "scripts.run_macroblocking_phase8.iter_luminance_frames",
        lambda *args, **kwargs: iter(frames),
    )

    class Observation:
        affected_area_ratio = 0.2
        blocking_confidence = 0.8

    monkeypatch.setattr(
        "scripts.run_macroblocking_phase8.MacroblockingAnalyzer.analyze",
        lambda *args, **kwargs: Observation(),
    )
    monkeypatch.setattr(
        "scripts.run_macroblocking_phase8.MacroblockingCandidateRule.is_candidate",
        lambda *args, **kwargs: True,
    )
    from models.macroblocking import MacroblockingAnalyzerConfig

    report = run_source_smoke(
        Path("fixture.m3u8"),
        config=MacroblockingAnalyzerConfig(
            fusion_strategy=MacroblockingFusionStrategy.MAX,
            analysis_width=8,
            analysis_height=8,
            sampling_fps=1.0,
            relative_scale_divisors=(8, 4, 2),
        ),
        ffmpeg="ffmpeg",
    )

    assert report["would_open_ten_second_event"] is True
    assert report["longest_candidate_run_seconds"] == 12.0
    assert report["candidate_coverage_ratio"] == 1.0
