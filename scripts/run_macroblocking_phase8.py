from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import shutil
import statistics
import sys
import time
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from detectors.macroblocking import MacroblockingAnalyzer
from models.macroblocking import (
    MacroblockingAnalyzerConfig,
    MacroblockingFusionStrategy,
)
from policies.macroblocking import (
    MacroblockingAlertPolicy,
    MacroblockingCandidateRule,
)
from scripts.benchmark_macroblocking import evaluate, load_manifest
from scripts.run_macroblocking_analyzer import (
    analyze_manifest,
    iter_luminance_frames,
)


@dataclass(frozen=True)
class MatrixCase:
    width: int
    height: int
    sampling_fps: float
    fusion: MacroblockingFusionStrategy

    @property
    def name(self) -> str:
        return (
            f"{self.width}x{self.height}-{self.sampling_fps:g}fps-"
            f"{self.fusion.value}"
        )


def matrix_cases(
    *,
    resolutions: Iterable[tuple[int, int]],
    sampling_rates: Iterable[float],
    fusions: Iterable[MacroblockingFusionStrategy],
) -> tuple[MatrixCase, ...]:
    return tuple(
        MatrixCase(width, height, fps, fusion)
        for width, height in resolutions
        for fps in sampling_rates
        for fusion in fusions
    )


def run_fixture_matrix(
    manifest_path: Path,
    *,
    cases: Iterable[MatrixCase],
    ffmpeg: str,
) -> list[dict[str, object]]:
    manifest = load_manifest(manifest_path)
    results: list[dict[str, object]] = []
    for case in cases:
        config = MacroblockingAnalyzerConfig(
            fusion_strategy=case.fusion,
            analysis_width=case.width,
            analysis_height=case.height,
            sampling_fps=case.sampling_fps,
        )
        predictions = analyze_manifest(
            manifest_path,
            config=config,
            area_threshold=0.15,
            ffmpeg=ffmpeg,
        )
        report = evaluate(manifest, predictions)
        performance = report["performance"]
        assert isinstance(performance, dict)
        results.append(
            {
                "case": asdict(case) | {"fusion": case.fusion.value},
                "precision": report["precision"],
                "recall": report["recall"],
                "area_band_accuracy": report["area_band_accuracy"],
                "frames_per_second": performance["frames_per_second"],
                "realtime_ratio": (
                    performance["frames_per_second"] / case.sampling_fps
                ),
                "passes_throughput": (
                    performance["frames_per_second"] >= case.sampling_fps
                ),
            }
        )
    return results


def run_source_smoke(
    source: Path | str,
    *,
    config: MacroblockingAnalyzerConfig,
    ffmpeg: str,
    maximum_frames: int | None = None,
) -> dict[str, object]:
    analyzer = MacroblockingAnalyzer(config)
    rule = MacroblockingCandidateRule(
        alert_policy=MacroblockingAlertPolicy(affected_area_threshold=0.15),
        detector_confidence_threshold=config.detector_confidence_threshold,
    )
    candidates: list[bool] = []
    areas: list[float] = []
    confidences: list[float] = []
    frame_times: list[float] = []
    for index, frame in enumerate(
        iter_luminance_frames(
            source,
            width=config.analysis_width,
            height=config.analysis_height,
            sampling_fps=config.sampling_fps,
            ffmpeg=ffmpeg,
        )
    ):
        started = time.perf_counter()
        observation = analyzer.analyze(
            frame,
            offset_seconds=index / config.sampling_fps,
        )
        frame_times.append(time.perf_counter() - started)
        assert not isinstance(observation, tuple)
        candidates.append(rule.is_candidate(observation))
        areas.append(observation.affected_area_ratio)
        confidences.append(observation.blocking_confidence)
        if maximum_frames is not None and len(candidates) >= maximum_frames:
            break
    longest = _longest_true_run(candidates)
    positive_seconds = sum(candidates) / config.sampling_fps
    return {
        "source": str(source),
        "frame_count": len(candidates),
        "candidate_frame_count": sum(candidates),
        "candidate_coverage_ratio": (
            sum(candidates) / len(candidates) if candidates else 0.0
        ),
        "candidate_seconds": positive_seconds,
        "longest_candidate_run_seconds": longest / config.sampling_fps,
        "would_open_ten_second_event": longest / config.sampling_fps >= 10.0,
        "peak_affected_area_ratio": max(areas, default=0.0),
        "peak_blocking_confidence": max(confidences, default=0.0),
        "mean_analyzer_milliseconds": (
            statistics.fmean(frame_times) * 1000 if frame_times else 0.0
        ),
        "analyzer_frames_per_second": (
            len(frame_times) / sum(frame_times) if frame_times else 0.0
        ),
    }


def _longest_true_run(values: Iterable[bool]) -> int:
    longest = 0
    current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the macroblocking Phase 8 acceptance benchmark"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("hls_output/macroblocking_fixtures/manifest.json"),
    )
    parser.add_argument("--source")
    parser.add_argument(
        "--expect-source-event",
        action="store_true",
        help="Fail acceptance when the source has no continuous 10-second event",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--source-max-frames", type=int)
    args = parser.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        parser.error("FFmpeg is not installed or not on PATH")

    rates = (1.0,) if args.quick else (1.0, 3.0, 5.0, 10.0)
    resolutions = ((480, 270),) if args.quick else ((480, 270), (640, 360))
    fusions = (
        (MacroblockingFusionStrategy.MAX_WITH_CONSISTENCY,)
        if args.quick
        else tuple(MacroblockingFusionStrategy)
    )
    cases = matrix_cases(
        resolutions=resolutions,
        sampling_rates=rates,
        fusions=fusions,
    )
    fixture_matrix = run_fixture_matrix(
        args.manifest,
        cases=cases,
        ffmpeg=ffmpeg,
    )
    report: dict[str, object] = {
        "schema_version": "1.0",
        "fixture_matrix": fixture_matrix,
    }
    source_passes = True
    if args.source:
        external_source = run_source_smoke(
            args.source,
            config=MacroblockingAnalyzerConfig(
                fusion_strategy=MacroblockingFusionStrategy.MAX_WITH_CONSISTENCY,
            ),
            ffmpeg=ffmpeg,
            maximum_frames=args.source_max_frames,
        )
        report["external_source"] = external_source
        source_passes = bool(external_source["would_open_ten_second_event"])
    fixture_passes = all(
        item["precision"] is not None
        and item["precision"] >= 0.90
        and item["recall"] is not None
        and item["recall"] >= 0.90
        and item["passes_throughput"]
        for item in fixture_matrix
    )
    acceptance_passes = fixture_passes and (
        source_passes if args.expect_source_event else True
    )
    report["acceptance"] = {
        "fixture_precision_minimum": 0.90,
        "fixture_recall_minimum": 0.90,
        "fixture_throughput_required": True,
        "source_event_required": args.expect_source_event,
        "passes": acceptance_passes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
        newline="\n",
    )
    return 0 if acceptance_passes else 2


if __name__ == "__main__":
    raise SystemExit(main())
