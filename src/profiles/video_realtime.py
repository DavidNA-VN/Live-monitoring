import re

from core.process_runner import (
    ProcessRunner,
    ProcessStartError,
    ProcessTimeoutError,
)
from core.analysis_profile import AnalysisResourceClass
from media.errors import MediaInputError
from media.input_resolver import (
    HlsMediaInputResolver,
    MediaInputResolver,
)
from models.analysis import (
    AnalysisRequirement,
    SegmentAnalysisBundle,
    VideoRealtimeAnalysis,
)
from models.detection import BlackInterval
from models.segment import Segment


BLACK_START_PATTERN = re.compile(
    r"black_start:([0-9]+(?:\.[0-9]+)?)"
)
BLACK_END_PATTERN = re.compile(
    r"black_end:([0-9]+(?:\.[0-9]+)?)"
)


class VideoRealtimeProfile:
    name = "video_realtime"
    resource_class = AnalysisResourceClass.VIDEO_DECODE
    provides = frozenset(
        {AnalysisRequirement.BLACK_INTERVALS}
    )

    def __init__(
        self,
        *,
        pix_th: float = 0.10,
        pic_th: float = 0.98,
        timeout: float = 20.0,
        runner: ProcessRunner | None = None,
        media_input_resolver: MediaInputResolver | None = None,
    ) -> None:
        if not 0.0 <= pix_th <= 1.0:
            raise ValueError(
                "pix_th must be between 0 and 1"
            )
        if not 0.0 <= pic_th <= 1.0:
            raise ValueError(
                "pic_th must be between 0 and 1"
            )
        if timeout <= 0:
            raise ValueError("timeout must be > 0")

        self.pix_th = pix_th
        self.pic_th = pic_th
        self.timeout = timeout
        self.runner = runner or ProcessRunner()
        self.media_input_resolver = (
            media_input_resolver
            or HlsMediaInputResolver()
        )

    @staticmethod
    def supports_segment(segment: Segment) -> bool:
        return segment.has_video

    def close(self) -> None:
        close = getattr(
            self.media_input_resolver,
            "close",
            None,
        )
        if callable(close):
            close()

    def analyze(
        self,
        segment: Segment,
    ) -> SegmentAnalysisBundle:
        if segment.gap:
            return self._bundle(
                VideoRealtimeAnalysis(
                    checked=False,
                    retryable=False,
                    error=(
                        "HLS segment declared unavailable "
                        "by EXT-X-GAP"
                    ),
                )
            )

        filter_expression = (
            "blackdetect="
            "d=0:"
            f"pix_th={self.pix_th}:"
            f"pic_th={self.pic_th}"
        )

        try:
            with self.media_input_resolver.open(
                segment
            ) as media_input:
                command = [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostdin",
                    "-nostats",
                    "-loglevel",
                    "info",
                    *media_input.ffmpeg_input_options,
                    "-i",
                    media_input.uri,
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
                result = self.runner.run(
                    command,
                    timeout=self.timeout,
                )
        except ProcessTimeoutError as exc:
            return self._failure(str(exc), timed_out=True)
        except ProcessStartError as exc:
            return self._failure(str(exc))
        except MediaInputError as exc:
            return self._failure(
                str(exc),
                retryable=exc.retryable,
            )

        if not result.ok:
            return self._failure(
                result.stderr.strip()
                or f"FFmpeg exited with code {result.returncode}"
            )

        return self._bundle(
            VideoRealtimeAnalysis(
                checked=True,
                black_intervals=tuple(
                    self._parse_black_intervals(
                        ffmpeg_output=result.stderr,
                        segment_duration=segment.duration,
                    )
                ),
            )
        )

    def _failure(
        self,
        error: str,
        *,
        retryable: bool = True,
        timed_out: bool = False,
    ) -> SegmentAnalysisBundle:
        return self._bundle(
            VideoRealtimeAnalysis(
                checked=False,
                error=error,
                retryable=retryable,
                timed_out=timed_out,
            )
        )

    def _bundle(
        self,
        analysis: VideoRealtimeAnalysis,
    ) -> SegmentAnalysisBundle:
        return SegmentAnalysisBundle(
            profile_name=self.name,
            video_realtime=analysis,
        )

    @staticmethod
    def _parse_black_intervals(
        *,
        ffmpeg_output: str,
        segment_duration: float,
    ) -> list[BlackInterval]:
        intervals: list[BlackInterval] = []
        current_start: float | None = None

        for line in ffmpeg_output.splitlines():
            start_match = BLACK_START_PATTERN.search(line)
            end_match = BLACK_END_PATTERN.search(line)

            if start_match:
                current_start = float(start_match.group(1))

            if end_match and current_start is not None:
                interval = BlackInterval(
                    start=max(0.0, current_start),
                    end=min(
                        segment_duration,
                        float(end_match.group(1)),
                    ),
                )
                if interval.duration > 0:
                    intervals.append(interval)
                current_start = None

        if current_start is not None:
            interval = BlackInterval(
                start=max(0.0, current_start),
                end=segment_duration,
            )
            if interval.duration > 0:
                intervals.append(interval)

        return intervals
