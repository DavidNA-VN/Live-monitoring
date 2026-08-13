import re
import subprocess

from models.segment import Segment
from models.detection import (
    BlackInterval,
    BlackDetectionResult,
)


BLACK_START_PATTERN = re.compile(
    r"black_start:([0-9]+(?:\.[0-9]+)?)"
)

BLACK_END_PATTERN = re.compile(
    r"black_end:([0-9]+(?:\.[0-9]+)?)"
)


class BlackScreenDetector:

    def __init__(
        self,
        pix_th: float = 0.10,
        pic_th: float = 0.98,
    ):
        self.pix_th = pix_th
        self.pic_th = pic_th

    def detect(
        self,
        segment: Segment,
    ) -> BlackDetectionResult:

        filter_expression = (
            f"blackdetect="
            f"d=0:"
            f"pix_th={self.pix_th}:"
            f"pic_th={self.pic_th}"
        )

        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "info",
            "-i",
            segment.uri,
            "-map",
            "0:v:0",
            "-vf",
            filter_expression,
            "-an",
            "-sn",
            "-dn",
            "-f",
            "null",
            "-",
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
                f"FFmpeg failed.\n"
                f"Variant: {segment.variant_id}\n"
                f"Sequence: {segment.sequence}\n"
                f"URI: {segment.uri}\n\n"
                f"{process.stderr}"
            )

        intervals = self._parse_black_intervals(
            process.stderr
        )

        return BlackDetectionResult(
            variant_id=segment.variant_id,
            sequence=segment.sequence,
            segment_uri=segment.uri,
            segment_duration=segment.duration,
            black_intervals=intervals,
        )

    @staticmethod
    def _parse_black_intervals(
        ffmpeg_output: str,
    ) -> list[BlackInterval]:

        intervals: list[BlackInterval] = []

        for line in ffmpeg_output.splitlines():
            start_match = BLACK_START_PATTERN.search(line)
            end_match = BLACK_END_PATTERN.search(line)

            if not start_match or not end_match:
                continue

            start = float(start_match.group(1))
            end = float(end_match.group(1))

            intervals.append(
                BlackInterval(
                    start=start,
                    end=end,
                )
            )

        return intervals