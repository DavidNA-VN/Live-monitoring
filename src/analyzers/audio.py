import re
import subprocess

from events.black_event_aggregator import BlackScreenEvent
from models.black_analysis import AudioEvidence
from playlist.master_parser import Variant


SILENCE_START_PATTERN = re.compile(
    r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)"
)
SILENCE_END_PATTERN = re.compile(
    r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)"
)


class AudioAnalyzer:

    def __init__(
        self,
        silence_db: int = -50,
        min_silence_duration: float = 0.5,
        timeout: int = 30,
    ):
        self.silence_db = silence_db
        self.min_silence_duration = min_silence_duration
        self.timeout = timeout

    def analyze_event(
        self,
        variant: Variant,
        event: BlackScreenEvent,
    ) -> AudioEvidence:

        duration = max(
            0.0,
            event.end_time - event.start_time,
        )

        return self.analyze_range(
            uri=variant.uri,
            start_time=event.start_time,
            duration=duration,
        )

    def analyze_range(
        self,
        uri: str,
        start_time: float,
        duration: float,
    ) -> AudioEvidence:

        if duration == 0:
            return AudioEvidence(
                checked=False,
                has_audio=False,
                audio_active_during_black=False,
                silence_ratio=0.0,
                error="audio range duration is zero",
            )

        has_audio, probe_error = self._has_audio_stream(
            uri
        )

        if probe_error is not None:
            return AudioEvidence(
                checked=False,
                has_audio=False,
                audio_active_during_black=False,
                silence_ratio=0.0,
                error=probe_error,
            )

        if not has_audio:
            return AudioEvidence(
                checked=True,
                has_audio=False,
                audio_active_during_black=False,
                silence_ratio=1.0,
            )

        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "info",
            "-ss",
            f"{start_time:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            uri,
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-af",
            (
                "silencedetect="
                f"n={self.silence_db}dB:"
                f"d={self.min_silence_duration}"
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
            return AudioEvidence(
                checked=False,
                has_audio=has_audio,
                audio_active_during_black=False,
                silence_ratio=0.0,
                error=f"ffmpeg timeout after {exc.timeout}s",
            )

        if process.returncode != 0:
            return AudioEvidence(
                checked=False,
                has_audio=has_audio,
                audio_active_during_black=False,
                silence_ratio=0.0,
                error=process.stderr.strip(),
            )

        silence_intervals = self._parse_silence_intervals(
            process.stderr,
            duration,
        )

        silent_duration = sum(
            end - start
            for start, end in silence_intervals
        )

        silence_ratio = min(
            1.0,
            silent_duration / duration,
        )

        return AudioEvidence(
            checked=True,
            has_audio=has_audio,
            audio_active_during_black=(
                has_audio
                and silence_ratio < 0.80
            ),
            silence_ratio=silence_ratio,
        )

    def _has_audio_stream(
        self,
        uri: str,
    ) -> tuple[bool, str | None]:

        command = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            uri,
        ]

        try:
            process = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return (
                False,
                f"ffprobe timeout after {exc.timeout}s",
            )

        if process.returncode != 0:
            return (
                False,
                process.stderr.strip(),
            )

        has_audio = any(
            line.strip() == "audio"
            for line in process.stdout.splitlines()
        )

        return has_audio, None

    @staticmethod
    def _parse_silence_intervals(
        ffmpeg_output: str,
        duration: float,
    ) -> list[tuple[float, float]]:

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
                    (
                        max(0.0, current_start),
                        min(duration, end),
                    )
                )
                current_start = None

        if current_start is not None:
            intervals.append(
                (
                    max(0.0, current_start),
                    duration,
                )
            )

        return intervals
