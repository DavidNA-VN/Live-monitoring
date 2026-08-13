import re
import subprocess

from events.black_event_aggregator import BlackScreenEvent
from models.black_analysis import TransitionEvidence
from playlist.master_parser import Variant


YAVG_PATTERN = re.compile(
    r"lavfi\.signalstats\.YAVG=([0-9]+(?:\.[0-9]+)?)"
)


class TransitionAnalyzer:

    def __init__(
        self,
        window: float = 1.0,
        fps: int = 8,
        timeout: int = 30,
        boundary_window: float = 0.25,
        fade_min_delta: float = 25.0,
        monotonic_ratio_threshold: float = 0.75,
        abrupt_jump_threshold: float = 45.0,
    ):
        self.window = window
        self.fps = fps
        self.timeout = timeout
        self.boundary_window = boundary_window
        self.fade_min_delta = fade_min_delta
        self.monotonic_ratio_threshold = (
            monotonic_ratio_threshold
        )
        self.abrupt_jump_threshold = (
            abrupt_jump_threshold
        )

    def analyze_event(
        self,
        variant: Variant,
        event: BlackScreenEvent,
    ) -> TransitionEvidence:

        try:
            pre_black_luma = self._sample_luma(
                uri=variant.uri,
                start=max(
                    0.0,
                    event.start_time - self.window,
                ),
                duration=min(
                    self.window,
                    event.start_time,
                ),
            )

            start_black_luma = self._sample_luma(
                uri=variant.uri,
                start=event.start_time,
                duration=min(
                    self.boundary_window,
                    event.duration,
                ),
            )

            end_black_luma = self._sample_luma(
                uri=variant.uri,
                start=max(
                    event.start_time,
                    event.end_time - self.boundary_window,
                ),
                duration=min(
                    self.boundary_window,
                    event.duration,
                ),
            )

            post_black_luma = self._sample_luma(
                uri=variant.uri,
                start=event.end_time,
                duration=self.window,
            )
        except RuntimeError as exc:
            return TransitionEvidence(
                checked=False,
                fade_out=False,
                fade_in=False,
                abrupt_start=False,
                abrupt_end=False,
                error=str(exc),
            )

        fade_out = self._is_decreasing(
            pre_black_luma
        )
        fade_in = self._is_increasing(
            post_black_luma
        )
        start_boundary_jump = self._start_boundary_jump(
            pre_black_luma,
            start_black_luma,
        )
        end_boundary_jump = self._end_boundary_jump(
            end_black_luma,
            post_black_luma,
        )

        return TransitionEvidence(
            checked=True,
            fade_out=fade_out,
            fade_in=fade_in,
            abrupt_start=(
                start_boundary_jump is not None
                and start_boundary_jump
                >= self.abrupt_jump_threshold
            ),
            abrupt_end=(
                end_boundary_jump is not None
                and end_boundary_jump
                >= self.abrupt_jump_threshold
            ),
            start_boundary_jump=start_boundary_jump,
            end_boundary_jump=end_boundary_jump,
            pre_black_luma=pre_black_luma,
            start_black_luma=start_black_luma,
            end_black_luma=end_black_luma,
            post_black_luma=post_black_luma,
        )

    def _sample_luma(
        self,
        uri: str,
        start: float,
        duration: float,
    ) -> list[float]:

        if duration <= 0:
            return []

        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "info",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            uri,
            "-map",
            "0:v:0",
            "-vf",
            (
                f"fps={self.fps},"
                "signalstats,"
                "metadata=print"
            ),
            "-an",
            "-sn",
            "-dn",
            "-f",
            "null",
            "-",
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
            raise RuntimeError(
                f"ffmpeg timeout after {exc.timeout}s"
            ) from exc

        if process.returncode != 0:
            raise RuntimeError(
                process.stderr.strip()
            )

        return [
            float(match.group(1))
            for match in YAVG_PATTERN.finditer(
                process.stdout + "\n" + process.stderr
            )
        ]

    def _is_decreasing(
        self,
        values: list[float],
    ) -> bool:

        if len(values) < 4:
            return False

        total_delta = values[0] - values[-1]

        if total_delta < self.fade_min_delta:
            return False

        return (
            self._monotonic_ratio(
                values,
                decreasing=True,
            )
            >= self.monotonic_ratio_threshold
        )

    def _is_increasing(
        self,
        values: list[float],
    ) -> bool:

        if len(values) < 4:
            return False

        total_delta = values[-1] - values[0]

        if total_delta < self.fade_min_delta:
            return False

        return (
            self._monotonic_ratio(
                values,
                decreasing=False,
            )
            >= self.monotonic_ratio_threshold
        )

    @staticmethod
    def _monotonic_ratio(
        values: list[float],
        decreasing: bool,
    ) -> float:

        if len(values) < 2:
            return 0.0

        matched_pairs = 0
        total_pairs = len(values) - 1

        for previous, current in zip(
            values,
            values[1:],
        ):
            if decreasing and current <= previous:
                matched_pairs += 1
            elif not decreasing and current >= previous:
                matched_pairs += 1

        return matched_pairs / total_pairs

    @staticmethod
    def _start_boundary_jump(
        pre_black_luma: list[float],
        start_black_luma: list[float],
    ) -> float | None:

        if not pre_black_luma or not start_black_luma:
            return None

        return max(
            0.0,
            pre_black_luma[-1] - start_black_luma[0],
        )

    @staticmethod
    def _end_boundary_jump(
        end_black_luma: list[float],
        post_black_luma: list[float],
    ) -> float | None:

        if not end_black_luma or not post_black_luma:
            return None

        return max(
            0.0,
            post_black_luma[0] - end_black_luma[-1],
        )
