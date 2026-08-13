import re
import subprocess

from models.freeze import FreezeInterval


FREEZE_START_PATTERN = re.compile(
    r"lavfi\.freezedetect\.freeze_start:\s*(-?\d+(?:\.\d+)?)"
)

FREEZE_END_PATTERN = re.compile(
    r"lavfi\.freezedetect\.freeze_end:\s*(-?\d+(?:\.\d+)?)"
)

FREEZE_DURATION_PATTERN = re.compile(
    r"lavfi\.freezedetect\.freeze_duration:\s*(-?\d+(?:\.\d+)?)"
)


def detect_freeze(
    media_playlist_url: str,
    total_duration: float,
    noise: float = 0.003,
    min_duration: float = 2.0,
) -> list[FreezeInterval]:

    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",

        "-i",
        media_playlist_url,

        "-map",
        "0:v:0",

        "-vf",
        (
            "setpts=PTS-STARTPTS,"
            f"freezedetect=n={noise}:d={min_duration}"
        ),

        "-an",
        "-f",
        "null",
        "-"
    ]

    process = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if process.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed for:\n"
            f"{media_playlist_url}\n\n"
            f"{process.stderr}"
        )

    return _parse_freeze_log(
        log=process.stderr,
        total_duration=total_duration,
    )


def _parse_freeze_log(
    log: str,
    total_duration: float,
) -> list[FreezeInterval]:

    intervals: list[FreezeInterval] = []

    current_start: float | None = None
    current_duration: float | None = None

    for line in log.splitlines():

        start_match = FREEZE_START_PATTERN.search(line)

        if start_match:
            current_start = float(
                start_match.group(1)
            )

            current_duration = None
            continue

        duration_match = FREEZE_DURATION_PATTERN.search(
            line
        )

        if duration_match:
            current_duration = float(
                duration_match.group(1)
            )
            continue

        end_match = FREEZE_END_PATTERN.search(line)

        if end_match and current_start is not None:
            end = float(
                end_match.group(1)
            )

            duration = (
                current_duration
                if current_duration is not None
                else end - current_start
            )

            intervals.append(
                FreezeInterval(
                    start=current_start,
                    end=end,
                    duration=duration,
                )
            )

            current_start = None
            current_duration = None

    # Freeze kéo dài tới EOF:
    #
    # freezedetect có thể có freeze_start
    # nhưng không có freeze_end vì video kết thúc
    # khi hình vẫn đang đứng.
    if current_start is not None:

        end = total_duration

        intervals.append(
            FreezeInterval(
                start=current_start,
                end=end,
                duration=end - current_start,
            )
        )

    return intervals