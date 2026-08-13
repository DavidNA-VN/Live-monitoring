import re
import subprocess

from checks.audio_loss.models import (
    AudioSilenceDetectionResult,
    AudioSilenceInterval,
)
from models.segment import Segment


SILENCE_START_PATTERN = re.compile(
    r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)"
)
SILENCE_END_PATTERN = re.compile(
    r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)"
)


class AudioSilenceDetector:

    def __init__(
        self,
        silence_db: int = -60,
        detector_min_duration: float = 0.1,
        timeout: int = 30,
    ):
        self.silence_db = silence_db
        self.detector_min_duration = detector_min_duration
        self.timeout = timeout

    def detect(
        self,
        segment: Segment,
    ) -> AudioSilenceDetectionResult:

        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "info",
            "-i",
            segment.uri,
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-af",
            (
                "asetpts=PTS-STARTPTS,"
                "silencedetect="
                f"n={self.silence_db}dB:"
                f"d={self.detector_min_duration}"
            ),
            "-f",
            "null",
            "-",
        ]

        try:
            process = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return AudioSilenceDetectionResult(
                variant_id=segment.variant_id,
                sequence=segment.sequence,
                segment_uri=segment.uri,
                segment_duration=segment.duration,
                checked=False,
                error=f"ffmpeg timeout after {exc.timeout}s",
            )

        if process.returncode != 0:
            return AudioSilenceDetectionResult(
                variant_id=segment.variant_id,
                sequence=segment.sequence,
                segment_uri=segment.uri,
                segment_duration=segment.duration,
                checked=False,
                error=process.stderr.strip(),
            )

        return AudioSilenceDetectionResult(
            variant_id=segment.variant_id,
            sequence=segment.sequence,
            segment_uri=segment.uri,
            segment_duration=segment.duration,
            intervals=self._parse_silence_intervals(
                sequence=segment.sequence,
                ffmpeg_output=process.stderr,
                segment_duration=segment.duration,
            ),
        )

    @staticmethod
    def _parse_silence_intervals(
        sequence: int,
        ffmpeg_output: str,
        segment_duration: float,
    ) -> list[AudioSilenceInterval]:

        intervals = []
        current_start = None

        for line in ffmpeg_output.splitlines():
            start_match = SILENCE_START_PATTERN.search(
                line
            )
            end_match = SILENCE_END_PATTERN.search(line)

            if start_match:
                current_start = float(
                    start_match.group(1)
                )

            if end_match and current_start is not None:
                end = float(end_match.group(1))
                intervals.append(
                    AudioSilenceInterval(
                        sequence=sequence,
                        start=max(
                            0.0,
                            current_start,
                        ),
                        end=min(
                            segment_duration,
                            end,
                        ),
                    )
                )
                current_start = None

        if current_start is not None:
            intervals.append(
                AudioSilenceInterval(
                    sequence=sequence,
                    start=max(
                        0.0,
                        current_start,
                    ),
                    end=segment_duration,
                )
            )

        return [
            interval
            for interval in intervals
            if interval.duration > 0
        ]
