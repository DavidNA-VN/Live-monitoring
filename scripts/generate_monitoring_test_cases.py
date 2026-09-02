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
class MonitoringCase:
    name: str
    duration: float
    black_ranges: tuple[TimeRange, ...] = ()
    freeze_ranges: tuple[TimeRange, ...] = ()
    silence_ranges: tuple[TimeRange, ...] = ()
    audio_track: bool = True
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.duration) or self.duration <= 0:
            raise ValueError("case duration must be finite and > 0")
        ranges = self.black_ranges + self.freeze_ranges + self.silence_ranges
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
        duration=44.0,
        black_ranges=(
            TimeRange(6.0, 8.0),
            TimeRange(14.0, 16.0),
            TimeRange(22.0, 24.0),
            TimeRange(30.0, 38.0),
        ),
        notes=(
            "Enable black-screen and disable freeze for isolated validation.",
            "Three 2s events exercise repeated-warning policy; 8s alerts directly.",
        ),
    ),
    MonitoringCase(
        name="audio_silence",
        duration=44.0,
        silence_ranges=(TimeRange(4.0, 39.0),),
        notes=(
            "Audio track remains present but is silent for 35 seconds.",
            "Video stays in motion; expected cause is continuous_silence.",
        ),
    ),
    MonitoringCase(
        name="audio_missing",
        duration=40.0,
        audio_track=False,
        notes=(
            "No audio elementary stream exists in any segment.",
            "The 40s cycle crosses the 30s audio-loss alert threshold.",
            "Expected cause is audio_stream_missing, distinct from silence.",
        ),
    ),
    MonitoringCase(
        name="video_freeze",
        duration=36.0,
        freeze_ranges=(
            TimeRange(2.0, 4.9),
            TimeRange(7.0, 10.2),
            TimeRange(13.0, 18.2),
            TimeRange(21.0, 24.2),
            TimeRange(27.0, 30.2),
        ),
        notes=(
            "Covers 2.9s no-public, 3.2s warning and 5.2s alert boundaries.",
            "Three 3.2s events occur inside the repeated 120s window.",
        ),
    ),
    MonitoringCase(
        name="combined",
        duration=64.0,
        black_ranges=(TimeRange(8.0, 10.0), TimeRange(44.0, 52.0)),
        freeze_ranges=(
            TimeRange(14.0, 17.2),
            TimeRange(24.0, 29.2),
            TimeRange(56.0, 59.2),
        ),
        silence_ranges=(TimeRange(4.0, 39.0),),
        notes=(
            "Enable all checks for shared-video-profile and dashboard validation.",
            "Black frames are static and may independently satisfy freeze detection.",
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
    if spec.black_ranges:
        enables = "+".join(
            f"between(t,{item.start:g},{item.end:g})"
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
        f"between(t,{item.start:g},{item.end:g})"
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
    payload = {
        "case": spec.name,
        "duration": spec.duration,
        "frame_rate": FRAME_RATE,
        "segment_duration": SEGMENT_DURATION,
        "variant_count": len(VARIANTS),
        "audio_track_present": spec.audio_track,
        "black_ranges": _ranges(spec.black_ranges),
        "freeze_ranges": _ranges(spec.freeze_ranges),
        "silence_ranges": _ranges(spec.silence_ranges),
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
