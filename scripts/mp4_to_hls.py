from __future__ import annotations

import argparse
import json
import subprocess
import sys

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


# ============================================================
# PATH CONFIG
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SOURCE_DIR = PROJECT_ROOT / "source_videos"
OUTPUT_DIR = PROJECT_ROOT / "hls_output"


# ============================================================
# HLS CONFIG
# ============================================================

SEGMENT_DURATION = 2

PRESET = "fast"

VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"

PIX_FMT = "yuv420p"


# ============================================================
# ENCODING LADDER
# ============================================================

LADDER = [
    {
        "name": "1080p",
        "width": 1920,
        "height": 1080,
        "video_bitrate": "5000k",
        "maxrate": "5350k",
        "bufsize": "7500k",
        "audio_bitrate": "128k",
    },
    {
        "name": "720p",
        "width": 1280,
        "height": 720,
        "video_bitrate": "2800k",
        "maxrate": "2996k",
        "bufsize": "4200k",
        "audio_bitrate": "128k",
    },
    {
        "name": "480p",
        "width": 854,
        "height": 480,
        "video_bitrate": "1400k",
        "maxrate": "1498k",
        "bufsize": "2100k",
        "audio_bitrate": "96k",
    },
]


# ============================================================
# DATA MODEL
# ============================================================

@dataclass
class SourceInfo:
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool


# ============================================================
# COMMAND HELPER
# ============================================================

def run_command(command: list[str]) -> None:
    print("\nFFmpeg command:\n")
    print(" ".join(command))
    print()

    subprocess.run(
        command,
        check=True,
    )


# ============================================================
# FFPROBE
# ============================================================

def parse_fps(value: str) -> float:
    """
    Convert ffprobe FPS values:

        30/1
        25/1
        30000/1001

    into float.
    """

    if not value or value == "0/0":
        raise ValueError(
            "Invalid FPS returned by ffprobe."
        )

    return float(Fraction(value))


def probe_video(input_file: Path) -> SourceInfo:
    """
    Read important source information using ffprobe.
    """

    command = [
        "ffprobe",
        "-v",
        "error",

        "-show_entries",
        (
            "stream=index,codec_type,width,height,avg_frame_rate:"
            "format=duration"
        ),

        "-of",
        "json",

        str(input_file),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)

    streams = data.get("streams", [])

    video_stream = None
    has_audio = False

    for stream in streams:

        codec_type = stream.get("codec_type")

        if (
            codec_type == "video"
            and video_stream is None
        ):
            video_stream = stream

        elif codec_type == "audio":
            has_audio = True

    if video_stream is None:
        raise RuntimeError(
            f"No video stream found: {input_file}"
        )

    width = int(
        video_stream["width"]
    )

    height = int(
        video_stream["height"]
    )

    fps = parse_fps(
        video_stream.get(
            "avg_frame_rate",
            "0/0",
        )
    )

    duration_raw = (
        data
        .get("format", {})
        .get("duration")
    )

    duration = (
        float(duration_raw)
        if duration_raw is not None
        else 0.0
    )

    return SourceInfo(
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        has_audio=has_audio,
    )


# ============================================================
# LADDER SELECTION
# ============================================================

def select_variants(
    source: SourceInfo,
) -> list[dict]:
    """
    Select only renditions that do not upscale
    the source video.
    """

    selected = []

    for variant in LADDER:

        target_width = variant["width"]
        target_height = variant["height"]

        if (
            target_width <= source.width
            and target_height <= source.height
        ):
            selected.append(variant)

    if not selected:
        raise RuntimeError(
            f"Source resolution "
            f"{source.width}x{source.height} "
            f"is smaller than the lowest "
            f"configured rendition."
        )

    return selected


# ============================================================
# FILTER COMPLEX
# ============================================================

def build_filter_complex(
    variants: list[dict],
) -> tuple[str, list[str]]:
    """
    Create FFmpeg filter graph.

    Example:

        source
          |
        split
        / | \
       /  |  \
    1080 720 480
    """

    count = len(variants)

    input_labels = [
        f"[split{i}]"
        for i in range(count)
    ]

    output_labels = [
        f"v{i}out"
        for i in range(count)
    ]

    filters = []

    # --------------------------------------------------------
    # Split source
    # --------------------------------------------------------

    if count == 1:

        filters.append(
            f"[0:v]null{input_labels[0]}"
        )

    else:

        filters.append(
            f"[0:v]split={count}"
            + "".join(input_labels)
        )

    # --------------------------------------------------------
    # Scale each rendition
    # --------------------------------------------------------

    for index, variant in enumerate(
        variants
    ):

        width = variant["width"]
        height = variant["height"]

        filters.append(
            f"{input_labels[index]}"
            f"scale="
            f"w={width}:"
            f"h={height}:"
            f"force_original_aspect_ratio=decrease,"
            f"pad="
            f"{width}:"
            f"{height}:"
            f"(ow-iw)/2:"
            f"(oh-ih)/2,"
            f"setsar=1"
            f"[{output_labels[index]}]"
        )

    filter_complex = ";".join(
        filters
    )

    return (
        filter_complex,
        output_labels,
    )


# ============================================================
# BUILD FFMPEG COMMAND
# ============================================================

def build_ffmpeg_command(
    input_file: Path,
    output_dir: Path,
    source: SourceInfo,
    variants: list[dict],
) -> list[str]:

    # --------------------------------------------------------
    # GOP
    # --------------------------------------------------------

    gop_size = max(
        1,
        round(
            source.fps
            * SEGMENT_DURATION
        ),
    )

    # --------------------------------------------------------
    # Filter graph
    # --------------------------------------------------------

    (
        filter_complex,
        output_labels,
    ) = build_filter_complex(
        variants
    )

    command = [
        "ffmpeg",
        "-y",

        "-i",
        str(input_file),

        "-filter_complex",
        filter_complex,
    ]

    # ========================================================
    # MAP + ENCODE EACH VARIANT
    # ========================================================

    for index, variant in enumerate(
        variants
    ):

        # ----------------------------------------------------
        # VIDEO MAP
        # ----------------------------------------------------

        command += [
            "-map",
            f"[{output_labels[index]}]",
        ]

        # ----------------------------------------------------
        # AUDIO MAP
        # ----------------------------------------------------

        if source.has_audio:

            command += [
                "-map",
                "0:a:0",
            ]

        # ----------------------------------------------------
        # VIDEO ENCODING
        # ----------------------------------------------------

        command += [
            f"-c:v:{index}",
            VIDEO_CODEC,

            f"-preset:v:{index}",
            PRESET,

            f"-pix_fmt:v:{index}",
            PIX_FMT,

            f"-b:v:{index}",
            variant["video_bitrate"],

            f"-maxrate:v:{index}",
            variant["maxrate"],

            f"-bufsize:v:{index}",
            variant["bufsize"],

            # Fixed GOP
            f"-g:v:{index}",
            str(gop_size),

            f"-keyint_min:v:{index}",
            str(gop_size),

            # Disable scene-cut keyframes
            f"-sc_threshold:v:{index}",
            "0",
        ]

        # ----------------------------------------------------
        # AUDIO ENCODING
        # ----------------------------------------------------

        if source.has_audio:

            command += [
                f"-c:a:{index}",
                AUDIO_CODEC,

                f"-b:a:{index}",
                variant[
                    "audio_bitrate"
                ],
            ]

    # ========================================================
    # FORCE KEYFRAMES AT SEGMENT BOUNDARIES
    # ========================================================

    command += [
        "-force_key_frames",
        (
            "expr:gte("
            "t,"
            f"n_forced*{SEGMENT_DURATION}"
            ")"
        ),
    ]

    # ========================================================
    # VAR STREAM MAP
    # ========================================================

    stream_map_parts = []

    for index, variant in enumerate(
        variants
    ):

        if source.has_audio:

            stream_map_parts.append(
                f"v:{index},"
                f"a:{index},"
                f"name:{variant['name']}"
            )

        else:

            stream_map_parts.append(
                f"v:{index},"
                f"name:{variant['name']}"
            )

    var_stream_map = " ".join(
        stream_map_parts
    )

    # ========================================================
    # WINDOWS-SAFE HLS PATHS
    # ========================================================

    # IMPORTANT:
    #
    # Path -> str on Windows:
    #
    #   D:\...\720p\playlist.m3u8
    #
    # HLS URI must use "/".
    #
    # Therefore use as_posix():
    #
    #   D:/.../720p/playlist.m3u8

    segment_pattern = (
        output_dir
        / "%v"
        / "segment_%05d.ts"
    ).as_posix()

    playlist_pattern = (
        output_dir
        / "%v"
        / "playlist.m3u8"
    ).as_posix()

    # ========================================================
    # HLS SETTINGS
    # ========================================================

    command += [
        "-f",
        "hls",

        "-hls_time",
        str(SEGMENT_DURATION),

        "-hls_playlist_type",
        "vod",

        "-hls_flags",
        "independent_segments",

        "-master_pl_name",
        "master.m3u8",

        "-var_stream_map",
        var_stream_map,

        "-hls_segment_filename",
        segment_pattern,

        playlist_pattern,
    ]

    return command


# ============================================================
# OUTPUT DIRECTORY
# ============================================================

def prepare_output_directories(
    output_dir: Path,
    variants: list[dict],
) -> None:

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    for variant in variants:

        variant_dir = (
            output_dir
            / variant["name"]
        )

        variant_dir.mkdir(
            parents=True,
            exist_ok=True,
        )


# ============================================================
# PROCESS ONE VIDEO
# ============================================================

def get_master_playlist(
    input_file: Path,
) -> Path:

    return (
        OUTPUT_DIR
        / input_file.stem
        / "master.m3u8"
    )


def should_skip_video(
    input_file: Path,
    force: bool,
) -> bool:

    if force:
        return False

    return get_master_playlist(
        input_file
    ).is_file()


def process_video(
    input_file: Path,
) -> None:

    print()
    print("=" * 70)
    print(
        f"INPUT: {input_file.name}"
    )
    print("=" * 70)

    # --------------------------------------------------------
    # STEP 1 - FFPROBE
    # --------------------------------------------------------

    source = probe_video(
        input_file
    )

    print(
        "\nSource information:"
    )

    print(
        f"  Resolution : "
        f"{source.width}x"
        f"{source.height}"
    )

    print(
        f"  FPS        : "
        f"{source.fps:.3f}"
    )

    print(
        f"  Duration   : "
        f"{source.duration:.2f}s"
    )

    print(
        f"  Audio      : "
        f"{'yes' if source.has_audio else 'no'}"
    )

    # --------------------------------------------------------
    # STEP 2 - GOP
    # --------------------------------------------------------

    gop_size = max(
        1,
        round(
            source.fps
            * SEGMENT_DURATION
        ),
    )

    print(
        f"  GOP        : "
        f"{gop_size} frames"
    )

    # --------------------------------------------------------
    # STEP 3 - SELECT LADDER
    # --------------------------------------------------------

    variants = select_variants(
        source
    )

    print(
        "\nSelected HLS ladder:"
    )

    for variant in variants:

        print(
            f"  {variant['name']} "
            f"{variant['width']}x"
            f"{variant['height']} "
            f"{variant['video_bitrate']}"
        )

    # --------------------------------------------------------
    # STEP 4 - OUTPUT
    # --------------------------------------------------------

    video_name = (
        input_file.stem
    )

    output_dir = (
        OUTPUT_DIR
        / video_name
    )

    prepare_output_directories(
        output_dir,
        variants,
    )

    # --------------------------------------------------------
    # STEP 5 - BUILD COMMAND
    # --------------------------------------------------------

    command = build_ffmpeg_command(
        input_file=input_file,
        output_dir=output_dir,
        source=source,
        variants=variants,
    )

    # --------------------------------------------------------
    # STEP 6 - RUN FFMPEG
    # --------------------------------------------------------

    run_command(
        command
    )

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    master_file = (
        output_dir
        / "master.m3u8"
    )

    print()
    print("Completed.")

    print(
        "\nMaster playlist:"
    )

    print(
        master_file
    )

    print(
        "\nMASTER URL:"
    )

    print(
        f"http://127.0.0.1:8000/"
        f"{video_name}/master.m3u8"
    )


# ============================================================
# MAIN
# ============================================================

def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=(
            "Convert MP4 files in source_videos to HLS."
        )
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Convert again even when the HLS master playlist "
            "already exists."
        ),
    )

    return parser.parse_args()


def main() -> None:

    args = parse_args()

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    videos = sorted(
        SOURCE_DIR.glob(
            "*.mp4"
        )
    )

    if not videos:

        print(
            f"No MP4 files found in:\n"
            f"{SOURCE_DIR}"
        )

        sys.exit(1)

    print(
        f"Found "
        f"{len(videos)} "
        f"MP4 file(s)."
    )

    success = 0
    failed = 0
    skipped = 0

    for video in videos:

        if should_skip_video(
            video,
            args.force,
        ):

            skipped += 1

            print()
            print(
                f"SKIPPED: {video.name}"
            )
            print(
                "Reason: HLS output already exists. "
                "Use --force to convert again."
            )

            continue

        try:

            process_video(
                video
            )

            success += 1

        except (
            subprocess.CalledProcessError,
            RuntimeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:

            failed += 1

            print()
            print(
                f"FAILED: "
                f"{video.name}"
            )

            print(
                f"Reason: {exc}"
            )

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(
        f"Success: {success}"
    )

    print(
        f"Skipped: {skipped}"
    )

    print(
        f"Failed : {failed}"
    )


if __name__ == "__main__":
    main()
