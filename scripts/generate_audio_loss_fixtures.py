from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "hls_output" / "audio_loss_fixtures"
OWNERSHIP_MARKER = ".audio-loss-fixtures"


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    segment_kinds: tuple[str, ...]
    expected_cause: str | None


FIXTURES = (
    FixtureSpec("audio_clean", ("audible",) * 8, None),
    FixtureSpec(
        "audio_silence_35s",
        ("audible",) * 2 + ("silent",) * 18 + ("audible",) * 2,
        "continuous_silence",
    ),
    FixtureSpec(
        "audio_missing_35s",
        ("audible",) * 2 + ("missing",) * 18 + ("audible",) * 2,
        "audio_stream_missing",
    ),
    FixtureSpec(
        "audio_missing_to_silence",
        ("audible",) * 2
        + ("missing",) * 10
        + ("silent",) * 8
        + ("audible",) * 2,
        "audio_stream_missing",
    ),
    FixtureSpec(
        "audio_one_channel_silent",
        ("one_channel",) * 8,
        None,
    ),
    FixtureSpec(
        "audio_corrupt",
        ("audible",) * 2 + ("corrupt",) + ("audible",) * 2,
        None,
    ),
)


VARIANTS = (
    ("low", 64, 64, 180_000),
    ("high", 96, 64, 260_000),
)


def _run(command: list[str]) -> None:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "FFmpeg failed")


def _audio_input(kind: str, duration: float) -> tuple[list[str], list[str]]:
    if kind == "missing":
        return [], ["-an"]
    if kind == "silent":
        source = f"anullsrc=r=48000:cl=stereo:d={duration}"
        return ["-f", "lavfi", "-i", source], ["-map", "1:a:0"]
    if kind == "one_channel":
        source = (
            f"aevalsrc=0|0.125*sin(2*PI*1000*t):"
            f"s=48000:d={duration}"
        )
        return ["-f", "lavfi", "-i", source], ["-map", "1:a:0"]
    source = f"sine=frequency=1000:sample_rate=48000:duration={duration}"
    return ["-f", "lavfi", "-i", source], ["-map", "1:a:0"]


def _generate_template(
    *,
    ffmpeg: str,
    output: Path,
    kind: str,
    width: int,
    height: int,
    duration: float,
) -> None:
    if kind == "corrupt":
        output.write_bytes(b"not-a-transport-stream")
        return
    audio_input, audio_map = _audio_input(kind, duration)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=blue:s={width}x{height}:r=10:d={duration}",
        *audio_input,
        "-map",
        "0:v:0",
        *audio_map,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "10",
        "-sc_threshold",
        "0",
    ]
    if kind != "missing":
        command.extend(("-c:a", "aac", "-b:a", "96k"))
    command.extend(("-f", "mpegts", str(output)))
    _run(command)


def _media_playlist(spec: FixtureSpec, duration: float) -> str:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{max(1, int(duration + 0.999999))}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    for index in range(len(spec.segment_kinds)):
        lines.extend((f"#EXTINF:{duration:.6f},", f"segment_{index:05d}.ts"))
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def generate_fixtures(
    output: Path,
    *,
    segment_duration: float = 2.0,
    reset: bool = False,
) -> None:
    if segment_duration <= 0:
        raise ValueError("segment_duration must be > 0")
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
        for variant_name, width, height, _ in VARIANTS:
            variant_dir = fixture_dir / variant_name
            variant_dir.mkdir(parents=True)
            templates: dict[str, Path] = {}
            for kind in set(spec.segment_kinds):
                template = variant_dir / f"template_{kind}.ts"
                _generate_template(
                    ffmpeg=ffmpeg,
                    output=template,
                    kind=kind,
                    width=width,
                    height=height,
                    duration=segment_duration,
                )
                templates[kind] = template
            for index, kind in enumerate(spec.segment_kinds):
                shutil.copy2(templates[kind], variant_dir / f"segment_{index:05d}.ts")
            for template in templates.values():
                template.unlink()
            (variant_dir / "playlist.m3u8").write_text(
                _media_playlist(spec, segment_duration),
                encoding="ascii",
                newline="\n",
            )

        master = ["#EXTM3U", "#EXT-X-VERSION:3"]
        for variant_name, width, height, bandwidth in VARIANTS:
            master.extend(
                (
                    (
                        f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},"
                        f"RESOLUTION={width}x{height},"
                        'CODECS="avc1.42c00a,mp4a.40.2"'
                    ),
                    f"{variant_name}/playlist.m3u8",
                )
            )
        (fixture_dir / "master.m3u8").write_text(
            "\n".join(master) + "\n",
            encoding="ascii",
            newline="\n",
        )
        expected = {
            "fixture": spec.name,
            "variant_count": len(VARIANTS),
            "segment_duration": segment_duration,
            "segment_kinds": spec.segment_kinds,
            "expected_alert": spec.expected_cause is not None,
            "expected_primary_cause": spec.expected_cause,
        }
        (fixture_dir / "expected.json").write_text(
            json.dumps(expected, indent=2) + "\n",
            encoding="ascii",
            newline="\n",
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate deterministic two-variant audio-loss HLS fixtures."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--segment-duration", type=float, default=2.0)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    generate_fixtures(
        args.output,
        segment_duration=args.segment_duration,
        reset=args.reset,
    )
    print(f"Generated audio-loss fixtures: {args.output.resolve()}")


if __name__ == "__main__":
    main()
