from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import shutil
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "hls_output" / "macroblocking_fixtures"
OWNERSHIP_MARKER = ".macroblocking-fixtures"
SCHEMA_VERSION = "1.0"
WIDTH = 640
HEIGHT = 360
FRAME_RATE = 10
SEGMENT_DURATION = 2.0
DURATION = 16.0


@dataclass(frozen=True)
class AreaBand:
    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.minimum, self.maximum)):
            raise ValueError("area band values must be finite")
        if not 0 <= self.minimum <= self.maximum <= 1:
            raise ValueError("area band must satisfy 0 <= minimum <= maximum <= 1")


@dataclass(frozen=True)
class GroundTruthRange:
    start: float
    end: float
    label: str
    expected_area: AreaBand | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.start) or self.start < 0:
            raise ValueError("ground-truth start must be finite and >= 0")
        if not math.isfinite(self.end) or self.end <= self.start:
            raise ValueError("ground-truth end must be finite and > start")
        if self.label == "macroblocking" and self.expected_area is None:
            raise ValueError("macroblocking ground truth requires an area band")


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    artifact_kind: str
    ranges: tuple[GroundTruthRange, ...] = ()
    notes: tuple[str, ...] = ()


POSITIVE_RANGE = (2.0, 14.0)
FIXTURES = (
    FixtureSpec(
        "clean_motion",
        "none",
        notes=("Moving natural test pattern without injected blocking.",),
    ),
    FixtureSpec(
        "natural_periodic_grid",
        "natural_grid_negative",
        notes=("Periodic lines are a hard negative for grid-only detectors.",),
    ),
    FixtureSpec(
        "local_small",
        "synthetic_block_grid",
        ranges=(
            GroundTruthRange(
                *POSITIVE_RANGE,
                "macroblocking",
                AreaBand(0.12, 0.22),
            ),
        ),
        notes=("Injected region covers approximately 16 percent of the frame.",),
    ),
    FixtureSpec(
        "local_large",
        "synthetic_block_grid",
        ranges=(
            GroundTruthRange(
                *POSITIVE_RANGE,
                "macroblocking",
                AreaBand(0.34, 0.46),
            ),
        ),
        notes=("Injected region covers approximately 40 percent of the frame.",),
    ),
    FixtureSpec(
        "full_frame",
        "synthetic_block_grid",
        ranges=(
            GroundTruthRange(
                *POSITIVE_RANGE,
                "macroblocking",
                AreaBand(0.90, 1.00),
            ),
        ),
        notes=("Full-frame synthetic blocking; edge transition times are excluded.",),
    ),
)


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


def build_filter_graph(spec: FixtureSpec) -> str | None:
    enabled = f"between(t,{POSITIVE_RANGE[0]:g},{POSITIVE_RANGE[1]:g})"
    if spec.artifact_kind == "none":
        return None
    if spec.artifact_kind == "natural_grid_negative":
        return (
            "drawgrid=width=40:height=40:thickness=2:"
            "color=white@0.7"
        )
    if spec.name == "local_small":
        width, height, x, y = 256, 144, 32, 72
    elif spec.name == "local_large":
        width, height, x, y = 400, 230, 120, 64
    elif spec.name == "full_frame":
        return (
            "split=2[clean][blocked];"
            "[blocked]noise=alls=32:allf=t+u,scale=40:22:flags=area,"
            "scale=640:360:flags=neighbor[pixelated];"
            f"[clean][pixelated]overlay=0:0:enable='{enabled}'"
        )
    else:
        raise ValueError(f"Unsupported fixture: {spec.name}")
    down_width = max(4, width // 16)
    down_height = max(4, height // 16)
    return (
        "split=2[base][region_source];"
        f"[region_source]crop={width}:{height}:{x}:{y},"
        "noise=alls=32:allf=t+u,"
        f"scale={down_width}:{down_height}:flags=area,"
        f"scale={width}:{height}:flags=neighbor[pixelated];"
        f"[base][pixelated]overlay={x}:{y}:enable='{enabled}'"
    )


def build_ffmpeg_command(ffmpeg: str, spec: FixtureSpec, output: Path) -> list[str]:
    playlist = output / spec.name / "high" / "playlist.m3u8"
    segment_pattern = output / spec.name / "high" / "segment_%05d.ts"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={WIDTH}x{HEIGHT}:rate={FRAME_RATE}:duration={DURATION:g}",
    ]
    graph = build_filter_graph(spec)
    if graph is not None:
        command.extend(("-filter_complex", f"[0:v]{graph}[video]", "-map", "[video]"))
    else:
        command.extend(("-map", "0:v:0"))
    command.extend(
        (
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "12",
            "-pix_fmt",
            "yuv420p",
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
            str(segment_pattern),
            str(playlist),
        )
    )
    return command


def _write_master(case_dir: Path) -> None:
    content = "\n".join(
        (
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            (
                "#EXT-X-STREAM-INF:BANDWIDTH=1500000,"
                f"RESOLUTION={WIDTH}x{HEIGHT},CODECS=\"avc1.42c01e\""
            ),
            "high/playlist.m3u8",
            "",
        )
    )
    (case_dir / "master.m3u8").write_text(content, encoding="ascii", newline="\n")


def manifest_dict() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "frame_rate": FRAME_RATE,
        "segment_duration": SEGMENT_DURATION,
        "duration": DURATION,
        "resolution": [WIDTH, HEIGHT],
        "fixtures": [
            {
                "name": spec.name,
                "playlist": f"{spec.name}/master.m3u8",
                "artifact_kind": spec.artifact_kind,
                "ranges": [
                    {
                        "start_seconds": item.start,
                        "end_seconds": item.end,
                        "label": item.label,
                        "expected_area_ratio": [
                            item.expected_area.minimum,
                            item.expected_area.maximum,
                        ],
                    }
                    for item in spec.ranges
                ],
                "notes": list(spec.notes),
            }
            for spec in FIXTURES
        ],
    }


def generate_fixtures(output: Path, *, reset: bool = False) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is not installed or not on PATH")
    output = output.resolve()
    if output.exists():
        if not reset:
            raise FileExistsError(f"Output already exists: {output}; use --reset")
        if not (output / OWNERSHIP_MARKER).is_file():
            raise ValueError("Refusing to reset a directory not owned by this generator")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    (output / OWNERSHIP_MARKER).write_text("owned\n", encoding="ascii")
    for spec in FIXTURES:
        case_dir = output / spec.name
        (case_dir / "high").mkdir(parents=True)
        _run(build_ffmpeg_command(ffmpeg, spec, output))
        _write_master(case_dir)
    (output / "manifest.json").write_text(
        json.dumps(manifest_dict(), indent=2, sort_keys=True) + "\n",
        encoding="ascii",
        newline="\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate macroblocking HLS fixtures")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    generate_fixtures(args.output, reset=args.reset)
    print(f"Generated macroblocking fixtures: {args.output.resolve()}")


if __name__ == "__main__":
    main()
