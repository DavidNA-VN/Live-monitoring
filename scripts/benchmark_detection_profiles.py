from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from time import perf_counter

from models.analysis import AnalysisRequirement
from playlist.master_parser import Variant
from playlist.media_parser import parse_media_playlist
from profiles.audio_realtime import AudioRealtimeProfile
from profiles.video_realtime import VideoRealtimeProfile
from scripts.generate_video_freeze_fixtures import generate_fixtures


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURE_ROOT = PROJECT_ROOT / "hls_output" / "video_freeze_fixtures"


def _segments(playlist: Path):
    variant = Variant(
        id="benchmark",
        stable_id="benchmark",
        uri=playlist.as_uri(),
        bandwidth=250_000,
        resolution=(160, 90),
    )
    segments = parse_media_playlist(variant).segments
    for segment in segments:
        segment.uri = str(playlist.parent / Path(segment.uri).name)
    return segments


def _run_mode(mode: str, segments, iterations: int) -> dict[str, object]:
    video_requirements = {
        "black": frozenset({AnalysisRequirement.BLACK_INTERVALS}),
        "freeze": frozenset({AnalysisRequirement.FREEZE_INTERVALS}),
        "black_freeze": frozenset(
            {
                AnalysisRequirement.BLACK_INTERVALS,
                AnalysisRequirement.FREEZE_INTERVALS,
            }
        ),
        "black_freeze_audio": frozenset(
            {
                AnalysisRequirement.BLACK_INTERVALS,
                AnalysisRequirement.FREEZE_INTERVALS,
            }
        ),
    }[mode]
    video = VideoRealtimeProfile(timeout=20.0)
    audio = (
        AudioRealtimeProfile(timeout=20.0)
        if mode == "black_freeze_audio"
        else None
    )
    checked = 0
    failures = 0
    started = perf_counter()
    try:
        for _iteration in range(iterations):
            for segment in segments:
                result = video.analyze(
                    segment,
                    requirements=video_requirements,
                ).require_video_realtime()
                checked += 1
                failures += int(not result.checked)
                if audio is not None:
                    audio_result = audio.analyze(
                        segment,
                        requirements=frozenset(
                            {AnalysisRequirement.SILENCE_INTERVALS}
                        ),
                    ).require_audio_realtime()
                    checked += 1
                    failures += int(not audio_result.checked)
    finally:
        if audio is not None:
            audio.close()
        video.close()
    elapsed = perf_counter() - started
    media_seconds = sum(segment.duration for segment in segments) * iterations
    return {
        "mode": mode,
        "iterations": iterations,
        "segments": len(segments) * iterations,
        "profile_results": checked,
        "failures": failures,
        "media_seconds": round(media_seconds, 6),
        "wall_seconds": round(elapsed, 6),
        "realtime_factor": round(elapsed / media_seconds, 6),
        "expected_processes_per_segment": (
            2 if mode == "black_freeze_audio" else 1
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark shared video detection profiles on HLS segments."
    )
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--fixture-root", type=Path, default=DEFAULT_FIXTURE_ROOT)
    parser.add_argument(
        "--mode",
        choices=("black", "freeze", "black_freeze", "black_freeze_audio", "all"),
        default="all",
    )
    args = parser.parse_args()
    if args.iterations <= 0:
        parser.error("--iterations must be > 0")
    if shutil.which("ffmpeg") is None:
        parser.error("ffmpeg is required")

    playlist = args.fixture_root / "freeze_5_2s" / "low" / "playlist.m3u8"
    if not playlist.exists():
        generate_fixtures(args.fixture_root)
    segments = _segments(playlist)
    modes = (
        ("black", "freeze", "black_freeze", "black_freeze_audio")
        if args.mode == "all"
        else (args.mode,)
    )
    report = {
        "fixture": str(playlist),
        "note": (
            "Local baseline only; production capacity requires representative "
            "codec, resolution, variants and target host."
        ),
        "results": [_run_mode(mode, segments, args.iterations) for mode in modes],
    }
    print(json.dumps(report, indent=2))
    return int(any(item["failures"] for item in report["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
