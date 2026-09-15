from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "hls_output" / "monitoring_test_cases"
OWNERSHIP_MARKER = ".monitoring-test-cases"
FRAME_RATE = 10
SEGMENT_DURATION = 2.0


@dataclass(frozen=True)
class TimeRange:
    start: float
    end: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.start) or self.start < 0:
            raise ValueError("range start must be finite and >= 0")
        if not math.isfinite(self.end) or self.end <= self.start:
            raise ValueError("range end must be finite and greater than start")

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class ExpectedAlert:
    event_type: str
    states: tuple[str, ...]
    per_variant: int = 1


@dataclass(frozen=True)
class MonitoringCase:
    name: str
    duration: float
    black_ranges: tuple[TimeRange, ...] = ()
    freeze_ranges: tuple[TimeRange, ...] = ()
    silence_ranges: tuple[TimeRange, ...] = ()
    macroblocking_ranges: tuple[TimeRange, ...] = ()
    audio_track: bool = True
    expected_alerts: tuple[ExpectedAlert, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.duration) or self.duration <= 0:
            raise ValueError("case duration must be finite and > 0")
        ranges = (
            self.black_ranges
            + self.freeze_ranges
            + self.silence_ranges
            + self.macroblocking_ranges
        )
        if any(item.end >= self.duration for item in ranges):
            raise ValueError("all ranges must end before case duration")
        if not self.audio_track and self.silence_ranges:
            raise ValueError("audio-missing case cannot contain silence ranges")


CASES = (
    MonitoringCase(
        name="healthy",
        duration=24.0,
        notes=("No content alert expected when all checks are enabled.",),
    ),
    MonitoringCase(
        name="black_screen",
        duration=96.0,
        black_ranges=(
            TimeRange(6.0, 8.2),
            TimeRange(14.0, 16.2),
            TimeRange(22.0, 24.2),
            TimeRange(30.0, 92.0),
        ),
        expected_alerts=(
            ExpectedAlert(
                event_type="REPEATED_BLACK_SCREEN",
                states=("OPEN", "RESOLVED"),
            ),
            ExpectedAlert(
                event_type="BLACK_SCREEN",
                states=("OPEN", "RESOLVED"),
            ),
        ),
        notes=(
            "Enable black-screen and disable freeze for isolated validation.",
            "Three 2.2s events exceed one segment and open repeated alert.",
            "The 62s event opens the continuous alert.",
            "A full healthy segment confirms each public alert recovery.",
        ),
    ),
    MonitoringCase(
        name="audio_silence",
        duration=44.0,
        silence_ranges=(TimeRange(4.0, 39.0),),
        expected_alerts=(
            ExpectedAlert(
                event_type="AUDIO_LOSS",
                states=("OPEN", "RESOLVED"),
            ),
        ),
        notes=(
            "Audio track remains present but is silent for 35 seconds.",
            "Video stays in motion; expected cause is continuous_silence.",
        ),
    ),
    MonitoringCase(
        name="audio_missing",
        duration=40.0,
        audio_track=False,
        expected_alerts=(
            ExpectedAlert(
                event_type="AUDIO_LOSS",
                states=("OPEN",),
            ),
        ),
        notes=(
            "No audio elementary stream exists in any segment.",
            "The 40s cycle crosses the 30s audio-loss alert threshold.",
            "Expected cause is audio_stream_missing, distinct from silence.",
        ),
    ),
    MonitoringCase(
        name="video_freeze",
        duration=96.0,
        freeze_ranges=(
            TimeRange(2.0, 6.0),
            TimeRange(10.0, 14.0),
            TimeRange(18.0, 22.0),
            TimeRange(28.0, 90.0),
        ),
        expected_alerts=(
            ExpectedAlert(
                event_type="REPEATED_VIDEO_FREEZE",
                states=("OPEN", "RESOLVED"),
            ),
            ExpectedAlert(
                event_type="VIDEO_FREEZE",
                states=("OPEN", "RESOLVED"),
            ),
        ),
        notes=(
            "Three 4s candidates open and recover a repeated incident.",
            "The 62s interval opens continuous freeze at 60s.",
            "A full healthy segment confirms public alert recovery.",
        ),
    ),
    MonitoringCase(
        name="macroblocking",
        duration=24.0,
        macroblocking_ranges=(TimeRange(4.0, 18.0),),
        expected_alerts=(
            ExpectedAlert(
                event_type="MACROBLOCKING",
                states=("OPEN", "RESOLVED"),
            ),
        ),
        notes=(
            "Enable macroblocking and disable unrelated checks for isolation.",
            "A 40 percent pixelated region lasts 14 seconds and crosses the 10 second rule.",
            "Only the highest-quality variant is selected by production policy.",
            "The healthy segment after 18s confirms public alert recovery.",
        ),
    ),
    MonitoringCase(
        name="combined",
        duration=72.0,
        black_ranges=(
            TimeRange(4.0, 6.2),
            TimeRange(12.0, 14.2),
            TimeRange(20.0, 22.2),
        ),
        freeze_ranges=(
            TimeRange(26.0, 30.0),
            TimeRange(34.0, 38.0),
            TimeRange(42.0, 46.0),
        ),
        silence_ranges=(TimeRange(2.0, 37.0),),
        macroblocking_ranges=(TimeRange(50.0, 64.0),),
        expected_alerts=(
            ExpectedAlert(
                event_type="REPEATED_BLACK_SCREEN",
                states=("OPEN", "RESOLVED"),
            ),
            ExpectedAlert(
                event_type="AUDIO_LOSS",
                states=("OPEN", "RESOLVED"),
            ),
            ExpectedAlert(
                event_type="REPEATED_VIDEO_FREEZE",
                states=("OPEN", "RESOLVED"),
            ),
            ExpectedAlert(
                event_type="MACROBLOCKING",
                states=("OPEN", "RESOLVED"),
            ),
        ),
        notes=(
            "Enable all four checks for shared-profile and dashboard validation.",
            "Three short black and freeze events exercise repeated rules.",
            "Audio silence and macroblocking independently cross direct thresholds.",
            "Fault windows do not overlap, so alert ownership is deterministic.",
        ),
    ),
)

VARIANTS = (
    ("low", 320, 180, 350_000),
    ("high", 640, 360, 900_000),
)


def case_by_name(name: str) -> MonitoringCase:
    try:
        return next(item for item in CASES if item.name == name)
    except StopIteration as exc:
        raise ValueError(f"Unknown monitoring test case: {name}") from exc


def _frame_index(timestamp: float) -> int:
    return int(round(timestamp * FRAME_RATE))


def _video_filter_graph(spec: MonitoringCase) -> str | None:
    stages: list[str] = []
    source = "0:v"
    for index, item in enumerate(spec.freeze_ranges):
        first = _frame_index(item.start)
        last = _frame_index(item.end) - 1
        main = f"main{index}"
        reference = f"reference{index}"
        output = f"freeze{index}"
        stages.append(f"[{source}]split=2[{main}][{reference}]")
        stages.append(
            f"[{main}][{reference}]freezeframes="
            f"first={first}:last={last}:replace={first}[{output}]"
        )
        source = output
    for index, item in enumerate(spec.macroblocking_ranges):
        base = f"macro_base{index}"
        region_source = f"macro_source{index}"
        pixelated = f"macro_pixels{index}"
        output = f"macro{index}"
        stages.append(f"[{source}]split=2[{base}][{region_source}]")
        stages.append(
            f"[{region_source}]crop=iw*0.625:ih*0.64:iw*0.1875:ih*0.18,"
            "noise=alls=32:allf=t+u,scale=iw/16:ih/16:flags=area,"
            f"scale=iw*16:ih*16:flags=neighbor[{pixelated}]"
        )
        stages.append(
            f"[{base}][{pixelated}]overlay=x=main_w*0.1875:y=main_h*0.18:"
            f"enable='gte(t,{item.start:g})*lt(t,{item.end:g})'[{output}]"
        )
        source = output
    if spec.macroblocking_ranges:
        recovery_start = spec.macroblocking_ranges[-1].end
        recovery_base = "macro_recovery_base"
        recovery = "macro_recovery"
        stages.append(
            f"[{source}]drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill:"
            f"enable='gte(t,{recovery_start:g})'[{recovery_base}]"
        )
        stages.append(
            f"[{recovery_base}]drawbox=x=iw*0.2:y=ih*0.2:w=iw*0.35:h=ih*0.35:"
            f"color=white:t=fill:enable='gte(t,{recovery_start:g})'"
            f"[{recovery}]"
        )
        source = recovery
    if spec.black_ranges:
        enables = "+".join(
            f"gte(t,{item.start:g})*lt(t,{item.end:g})"
            for item in spec.black_ranges
        )
        stages.append(
            f"[{source}]drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:"
            f"enable='{enables}'[video]"
        )
        source = "video"
    if not stages:
        return None
    if source != "video":
        stages.append(f"[{source}]null[video]")
    return ";".join(stages)


def _audio_filter(spec: MonitoringCase) -> str | None:
    if not spec.silence_ranges:
        return None
    enables = "+".join(
        f"gte(t,{item.start:g})*lt(t,{item.end:g})"
        for item in spec.silence_ranges
    )
    return f"volume=volume=0:enable='{enables}'"


def _run(command: list[str], *, timeout: float = 120.0) -> None:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "FFmpeg failed")


def _generate_variant(
    *,
    ffmpeg: str,
    case_dir: Path,
    spec: MonitoringCase,
    variant_name: str,
    width: int,
    height: int,
    bandwidth: int,
) -> None:
    variant_dir = case_dir / variant_name
    variant_dir.mkdir(parents=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={width}x{height}:rate={FRAME_RATE}:duration={spec.duration:g}",
    ]
    if spec.audio_track:
        command.extend(
            (
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=1000:sample_rate=48000:duration={spec.duration:g}",
            )
        )
    video_filter = _video_filter_graph(spec)
    if video_filter is not None:
        command.extend(("-filter_complex", video_filter, "-map", "[video]"))
    else:
        command.extend(("-map", "0:v:0"))
    if spec.audio_track:
        command.extend(("-map", "1:a:0"))
        audio_filter = _audio_filter(spec)
        if audio_filter is not None:
            command.extend(("-af", audio_filter))
    else:
        command.append("-an")
    command.extend(
        (
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            str(bandwidth),
            "-g",
            str(int(FRAME_RATE * SEGMENT_DURATION)),
            "-keyint_min",
            str(int(FRAME_RATE * SEGMENT_DURATION)),
            "-sc_threshold",
            "0",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{SEGMENT_DURATION:g})",
        )
    )
    if spec.audio_track:
        command.extend(("-c:a", "aac", "-b:a", "96k"))
    command.extend(
        (
            "-f",
            "hls",
            "-hls_time",
            f"{SEGMENT_DURATION:g}",
            "-hls_playlist_type",
            "vod",
            "-hls_segment_filename",
            str(variant_dir / "segment_%05d.ts"),
            str(variant_dir / "playlist.m3u8"),
        )
    )
    _run(command)


def _write_master(case_dir: Path, spec: MonitoringCase) -> None:
    codecs = 'CODECS="avc1.42c01e,mp4a.40.2"' if spec.audio_track else 'CODECS="avc1.42c01e"'
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for variant_name, width, height, bandwidth in VARIANTS:
        lines.extend(
            (
                f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={width}x{height},{codecs}",
                f"{variant_name}/playlist.m3u8",
            )
        )
    (case_dir / "master.m3u8").write_text(
        "\n".join(lines) + "\n", encoding="ascii", newline="\n"
    )


def _ranges(items: tuple[TimeRange, ...]) -> list[dict[str, float]]:
    return [
        {"start": item.start, "end": item.end, "duration": item.duration}
        for item in items
    ]


def _write_expected(case_dir: Path, spec: MonitoringCase) -> None:
    validate_all = spec.name == "healthy"
    payload = {
        "case": spec.name,
        "duration": spec.duration,
        "frame_rate": FRAME_RATE,
        "segment_duration": SEGMENT_DURATION,
        "variant_count": len(VARIANTS),
        "audio_track_present": spec.audio_track,
        "recommended_checks": {
            "black_screen": validate_all or bool(spec.black_ranges),
            "video_freeze": validate_all or bool(spec.freeze_ranges),
            "audio_loss": (
                validate_all or bool(spec.silence_ranges) or not spec.audio_track
            ),
            "macroblocking": validate_all or bool(spec.macroblocking_ranges),
        },
        "black_ranges": _ranges(spec.black_ranges),
        "freeze_ranges": _ranges(spec.freeze_ranges),
        "silence_ranges": _ranges(spec.silence_ranges),
        "macroblocking_ranges": _ranges(spec.macroblocking_ranges),
        "expected_alerts": [
            {
                "event_type": item.event_type,
                "states": list(item.states),
                "per_variant": item.per_variant,
            }
            for item in spec.expected_alerts
        ],
        "notes": list(spec.notes),
    }
    (case_dir / "expected.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="ascii", newline="\n"
    )


def generate_cases(
    output: Path,
    *,
    selected: tuple[str, ...] | None = None,
    reset: bool = False,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is not installed or not on PATH")
    output = output.resolve()
    if output.exists():
        if not reset:
            raise FileExistsError(f"Output already exists: {output}; use --reset")
        if not (output / OWNERSHIP_MARKER).is_file():
            raise ValueError(
                "Refusing to reset a directory not owned by this generator: "
                f"{output}"
            )
        shutil.rmtree(output)
    output.mkdir(parents=True)
    (output / OWNERSHIP_MARKER).write_text("owned\n", encoding="ascii")

    specs = CASES if selected is None else tuple(case_by_name(name) for name in selected)
    for spec in specs:
        case_dir = output / spec.name
        case_dir.mkdir()
        for variant_name, width, height, bandwidth in VARIANTS:
            _generate_variant(
                ffmpeg=ffmpeg,
                case_dir=case_dir,
                spec=spec,
                variant_name=variant_name,
                width=width,
                height=height,
                bandwidth=bandwidth,
            )
        _write_master(case_dir, spec)
        _write_expected(case_dir, spec)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate deterministic HLS cases for all implemented checks."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--case",
        action="append",
        choices=tuple(item.name for item in CASES),
        dest="cases",
        help="Generate only this case; may be repeated.",
    )
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    generate_cases(
        args.output,
        selected=tuple(args.cases) if args.cases else None,
        reset=args.reset,
    )
    print(f"Generated monitoring cases: {args.output.resolve()}")


if __name__ == "__main__":
    main()
