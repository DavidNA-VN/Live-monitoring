from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "source_videos"

FPS = 24
WIDTH = 1280
HEIGHT = 720


def run(command: list[str]) -> None:
    print()
    print(" ".join(command))

    process = subprocess.run(
        command,
        text=True,
    )

    if process.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(command)}"
        )


def create_standard_source(
    output: Path,
    *,
    duration: float = 10.0,
    black_start: float | None = None,
    black_end: float | None = None,
    audio_mode: str = "active",
    with_audio: bool = True,
) -> None:

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        (
            f"testsrc2="
            f"size={WIDTH}x{HEIGHT}:"
            f"rate={FPS}:"
            f"duration={duration}"
        ),
    ]

    if with_audio:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                (
                    "sine="
                    "frequency=1000:"
                    "sample_rate=48000:"
                    f"duration={duration}"
                ),
            ]
        )

    filter_parts = []

    if (
        black_start is not None
        and black_end is not None
    ):
        filter_parts.append(
            (
                "[0:v]"
                "drawbox="
                "x=0:"
                "y=0:"
                "w=iw:"
                "h=ih:"
                "color=black:"
                "t=fill:"
                f"enable='between(t,{black_start},{black_end})',"
                "format=yuv420p"
                "[v]"
            )
        )
    else:
        filter_parts.append(
            "[0:v]format=yuv420p[v]"
        )

    if with_audio:
        if (
            audio_mode == "silent_during_black"
            and black_start is not None
            and black_end is not None
        ):
            filter_parts.append(
                (
                    "[1:a]"
                    "volume="
                    "volume=0:"
                    f"enable='between(t,{black_start},{black_end})'"
                    "[a]"
                )
            )
        else:
            filter_parts.append(
                "[1:a]anull[a]"
            )

    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[v]",
        ]
    )

    if with_audio:
        command.extend(
            [
                "-map",
                "[a]",
            ]
        )

    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-g",
            str(FPS * 2),
            "-keyint_min",
            str(FPS * 2),
            "-sc_threshold",
            "0",
        ]
    )

    if with_audio:
        command.extend(
            [
                "-c:a",
                "aac",
                "-b:a",
                "128k",
            ]
        )

    command.extend(
        [
            "-movflags",
            "+faststart",
            str(output),
        ]
    )

    run(command)


def create_intentional_fade_source(
    output: Path,
) -> None:
    """
    Timeline:

    0-3s  : normal
    3-4s  : fade-out
    4-6s  : pure black + silence
    6-7s  : fade-in
    7-10s : normal
    """

    command = [
        "ffmpeg",
        "-y",

        # Video 0-4s
        "-f",
        "lavfi",
        "-i",
        (
            f"testsrc2="
            f"size={WIDTH}x{HEIGHT}:"
            f"rate={FPS}:"
            "duration=4"
        ),

        # Black 4-6s
        "-f",
        "lavfi",
        "-i",
        (
            f"color="
            f"c=black:"
            f"s={WIDTH}x{HEIGHT}:"
            f"r={FPS}:"
            "d=2"
        ),

        # Video 6-10s
        "-f",
        "lavfi",
        "-i",
        (
            f"testsrc2="
            f"size={WIDTH}x{HEIGHT}:"
            f"rate={FPS}:"
            "duration=4"
        ),

        # Audio 0-4s
        "-f",
        "lavfi",
        "-i",
        (
            "sine="
            "frequency=1000:"
            "sample_rate=48000:"
            "duration=4"
        ),

        # Silence 4-6s
        "-f",
        "lavfi",
        "-i",
        (
            "anullsrc="
            "r=48000:"
            "cl=mono:"
            "d=2"
        ),

        # Audio 6-10s
        "-f",
        "lavfi",
        "-i",
        (
            "sine="
            "frequency=1000:"
            "sample_rate=48000:"
            "duration=4"
        ),

        "-filter_complex",
        (
            "[0:v]"
            "fade=t=out:st=3:d=1,"
            "format=yuv420p"
            "[v0];"

            "[1:v]"
            "format=yuv420p"
            "[v1];"

            "[2:v]"
            "fade=t=in:st=0:d=1,"
            "format=yuv420p"
            "[v2];"

            "[v0][v1][v2]"
            "concat=n=3:v=1:a=0"
            "[v];"

            "[3:a]"
            "afade=t=out:st=3:d=1"
            "[a0];"

            "[4:a]"
            "anull"
            "[a1];"

            "[5:a]"
            "afade=t=in:st=0:d=1"
            "[a2];"

            "[a0][a1][a2]"
            "concat=n=3:v=0:a=1"
            "[a]"
        ),

        "-map",
        "[v]",
        "-map",
        "[a]",

        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",

        "-g",
        str(FPS * 2),

        "-keyint_min",
        str(FPS * 2),

        "-sc_threshold",
        "0",

        "-c:a",
        "aac",
        "-b:a",
        "128k",

        "-movflags",
        "+faststart",

        str(output),
    ]

    run(command)


def generate() -> None:

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # =====================================================
    # CASE 00
    # Clean video, no black screen.
    # =====================================================

    create_standard_source(
        OUTPUT_DIR / "black_00_clean.mp4",
    )

    # =====================================================
    # CASE 01
    # Intentional black:
    #
    # normal -> fade out -> black + silence
    # -> fade in -> normal
    # =====================================================

    create_intentional_fade_source(
        OUTPUT_DIR
        / "black_01_intentional_fade_silence.mp4"
    )

    # =====================================================
    # CASE 02
    # Abrupt black while audio keeps playing.
    #
    # Expected:
    # - black = yes
    # - audio active = yes
    # - fade = false
    # - abrupt = true
    # =====================================================

    create_standard_source(
        OUTPUT_DIR
        / "black_02_abrupt_audio_active.mp4",
        black_start=3.0,
        black_end=7.0,
        audio_mode="active",
    )

    # =====================================================
    # CASE 03
    # Abrupt black + audio silence.
    #
    # More ambiguous than case 02.
    # =====================================================

    create_standard_source(
        OUTPUT_DIR
        / "black_03_abrupt_silence.mp4",
        black_start=3.0,
        black_end=7.0,
        audio_mode="silent_during_black",
    )

    # =====================================================
    # CASE 05
    # Black screen but no audio stream at all.
    #
    # Tests:
    # has_audio=False
    # =====================================================

    create_standard_source(
        OUTPUT_DIR
        / "black_05_no_audio.mp4",
        black_start=3.0,
        black_end=7.0,
        with_audio=False,
    )

    # =====================================================
    # CASE 07
    # Short black ~0.8 second.
    #
    # Tests duration scoring.
    # =====================================================

    create_standard_source(
        OUTPUT_DIR
        / "black_07_short_audio_active.mp4",
        black_start=4.0,
        black_end=4.8,
        audio_mode="active",
    )

    print()
    print("=" * 70)
    print("Generated black-screen test MP4 files")
    print("=" * 70)

    files = [
        "black_00_clean.mp4",
        "black_01_intentional_fade_silence.mp4",
        "black_02_abrupt_audio_active.mp4",
        "black_03_abrupt_silence.mp4",
        "black_05_no_audio.mp4",
        "black_07_short_audio_active.mp4",
    ]

    for name in files:
        print(
            OUTPUT_DIR / name
        )


if __name__ == "__main__":
    generate()