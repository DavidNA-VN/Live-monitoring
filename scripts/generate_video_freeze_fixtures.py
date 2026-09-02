from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "hls_output" / "video_freeze_fixtures"
OWNERSHIP_MARKER = ".video-freeze-fixtures"
FRAME_RATE = 10
SEGMENT_DURATION = 2.0


@dataclass(frozen=True)
class FreezeRange:
    start: float
    end: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.start) or self.start < 0:
            raise ValueError("freeze start must be finite and >= 0")
        if not math.isfinite(self.end) or self.end <= self.start:
            raise ValueError("freeze end must be finite and greater than start")

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    duration: float
    freezes: tuple[FreezeRange, ...]
    expected_public_severity: str | None
    expected_event_type: str | None

    def __post_init__(self) -> None:
        if not math.isfinite(self.duration) or self.duration <= 0:
            raise ValueError("fixture duration must be finite and > 0")
        if any(item.end >= self.duration for item in self.freezes):
            raise ValueError("freeze must end before fixture duration")


FIXTURES = (
    FixtureSpec("motion_only", 8.0, (), None, None),
    FixtureSpec(
        "freeze_2_9s",
        8.0,
        (FreezeRange(2.0, 4.9),),
        None,
        None,
    ),
    FixtureSpec(
        "freeze_3_2s",
        8.0,
        (FreezeRange(2.0, 5.2),),
        "WARNING",
        "VIDEO_FREEZE",
    ),
    FixtureSpec(
        "freeze_5_2s",
        10.0,
        (FreezeRange(2.0, 7.2),),
        "ALERT",
        "VIDEO_FREEZE",
    ),
    FixtureSpec(
        "freeze_cross_three_segments",
        10.0,
        (FreezeRange(1.2, 6.8),),
        "ALERT",
        "VIDEO_FREEZE",
    ),
    FixtureSpec(
        "three_warning_freezes",
        22.0,
        (
            FreezeRange(2.0, 5.2),
            FreezeRange(8.0, 11.2),
            FreezeRange(14.0, 17.2),
        ),
        "ALERT",
        "REPEATED_VIDEO_FREEZE",
    ),
)

VARIANTS = (
    ("low", 160, 90, 250_000),
    ("high", 320, 180, 500_000),
)


def _run(command: list[str], *, timeout: float = 60.0) -> None:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "FFmpeg failed")


def _frame_index(timestamp: float) -> int:
    return int(round(timestamp * FRAME_RATE))


def _freeze_filter_graph(freezes: tuple[FreezeRange, ...]) -> str | None:
    if not freezes:
        return None
    stages = []
    source = "0:v"
    for index, item in enumerate(freezes):
        first = _frame_index(item.start)
        last = _frame_index(item.end) - 1
        main = f"main{index}"
        reference = f"ref{index}"
        output = "vout" if index == len(freezes) - 1 else f"stage{index}"
        stages.append(f"[{source}]split=2[{main}][{reference}]")
        stages.append(
            f"[{main}][{reference}]freezeframes="
            f"first={first}:last={last}:replace={first}[{output}]"
        )
        source = output
    return ";".join(stages)


def _generate_variant(
    *,
    ffmpeg: str,
    fixture_dir: Path,
    spec: FixtureSpec,
    variant_name: str,
    width: int,
    height: int,
    bandwidth: int,
) -> None:
    variant_dir = fixture_dir / variant_name
    variant_dir.mkdir(parents=True)
    playlist_path = variant_dir / "playlist.m3u8"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        (
            f"testsrc2=size={width}x{height}:rate={FRAME_RATE}:"
            f"duration={spec.duration}"
        ),
    ]
    filter_graph = _freeze_filter_graph(spec.freezes)
    if filter_graph is not None:
        command.extend(("-filter_complex", filter_graph, "-map", "[vout]"))
    else:
        command.extend(("-map", "0:v:0"))
    command.extend(
        [
        "-an",
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
        "-f",
        "hls",
        "-hls_time",
        f"{SEGMENT_DURATION:g}",
        "-hls_playlist_type",
        "vod",
        "-hls_segment_filename",
        str(variant_dir / "segment_%05d.ts"),
        str(playlist_path),
        ]
    )
    _run(command)


def _write_master(fixture_dir: Path) -> None:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for variant_name, width, height, bandwidth in VARIANTS:
        lines.extend(
            (
                (
                    f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},"
                    f"RESOLUTION={width}x{height},CODECS=\"avc1.42c00a\""
                ),
                f"{variant_name}/playlist.m3u8",
            )
        )
    (fixture_dir / "master.m3u8").write_text(
        "\n".join(lines) + "\n",
        encoding="ascii",
        newline="\n",
    )


def _write_expected(fixture_dir: Path, spec: FixtureSpec) -> None:
    expected = {
        "fixture": spec.name,
        "duration": spec.duration,
        "frame_rate": FRAME_RATE,
        "segment_duration": SEGMENT_DURATION,
        "variant_count": len(VARIANTS),
        "freeze_intervals": [
            {
                "start": item.start,
                "end": item.end,
                "duration": item.duration,
            }
            for item in spec.freezes
        ],
        "expected_public_severity": spec.expected_public_severity,
        "expected_event_type": spec.expected_event_type,
    }
    (fixture_dir / "expected.json").write_text(
        json.dumps(expected, indent=2) + "\n",
        encoding="ascii",
        newline="\n",
    )


def generate_fixtures(output: Path, *, reset: bool = False) -> None:
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

    for spec in FIXTURES:
        fixture_dir = output / spec.name
        fixture_dir.mkdir()
        for variant_name, width, height, bandwidth in VARIANTS:
            _generate_variant(
                ffmpeg=ffmpeg,
                fixture_dir=fixture_dir,
                spec=spec,
                variant_name=variant_name,
                width=width,
                height=height,
                bandwidth=bandwidth,
            )
        _write_master(fixture_dir)
        _write_expected(fixture_dir, spec)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate deterministic video-freeze HLS fixtures."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    generate_fixtures(args.output, reset=args.reset)
    print(f"Generated video-freeze fixtures: {args.output.resolve()}")


if __name__ == "__main__":
    main()
