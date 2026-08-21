from collections.abc import Iterable

from core.analysis_profile import AnalysisResourceClass
from core.process_runner import (
    ProcessRunner,
    ProcessStartError,
    ProcessTimeoutError,
)
from media.errors import MediaInputError
from media.input_resolver import HlsMediaInputResolver, MediaInputResolver
from models.analysis import (
    AnalysisRequirement,
    SegmentAnalysisBundle,
    VideoRealtimeAnalysis,
)
from models.segment import Segment
from profiles.video_realtime.command_builder import (
    VideoRealtimeCommandBuilder,
)
from profiles.video_realtime.parser import BlackdetectParser, VideoFilterParser


class VideoRealtimeProfile:
    name = "video_realtime"
    resource_class = AnalysisResourceClass.VIDEO_DECODE

    def __init__(
        self,
        *,
        pix_th: float = 0.10,
        pic_th: float = 0.98,
        timeout: float = 20.0,
        runner: ProcessRunner | None = None,
        media_input_resolver: MediaInputResolver | None = None,
        parsers: Iterable[VideoFilterParser] | None = None,
        command_builder: VideoRealtimeCommandBuilder | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        configured = tuple(
            parsers
            if parsers is not None
            else (BlackdetectParser(pix_th=pix_th, pic_th=pic_th),)
        )
        if not configured:
            raise ValueError("At least one video filter parser is required")
        requirements = [parser.requirement for parser in configured]
        if len(set(requirements)) != len(requirements):
            raise ValueError("Video filter parser requirements must be unique")

        self.timeout = timeout
        self.runner = runner or ProcessRunner()
        self.media_input_resolver = (
            media_input_resolver or HlsMediaInputResolver()
        )
        self.parsers = configured
        self.provides = frozenset(requirements)
        self.command_builder = command_builder or VideoRealtimeCommandBuilder()

    @staticmethod
    def supports_segment(segment: Segment) -> bool:
        return segment.has_video

    def close(self) -> None:
        close = getattr(self.media_input_resolver, "close", None)
        if callable(close):
            close()

    def analyze(
        self,
        segment: Segment,
        *,
        requirements: frozenset[AnalysisRequirement] | None = None,
    ) -> SegmentAnalysisBundle:
        if segment.gap:
            return self._bundle(
                VideoRealtimeAnalysis(
                    checked=False,
                    retryable=False,
                    error="HLS segment declared unavailable by EXT-X-GAP",
                )
            )

        requested = requirements if requirements is not None else self.provides
        unsupported = requested.difference(self.provides)
        if unsupported:
            raise ValueError(
                f"Unsupported video requirements: {sorted(unsupported)}"
            )
        selected_parsers = tuple(
            parser
            for parser in self.parsers
            if parser.requirement in requested
        )
        if not selected_parsers:
            raise ValueError("No video filter matches requested requirements")

        try:
            with self.media_input_resolver.open(segment) as media_input:
                command = self.command_builder.build(
                    media_input=media_input,
                    filter_expressions=tuple(
                        parser.filter_expression for parser in selected_parsers
                    ),
                )
                result = self.runner.run(command, timeout=self.timeout)
        except ProcessTimeoutError as exc:
            return self._failure(str(exc), timed_out=True)
        except ProcessStartError as exc:
            return self._failure(str(exc))
        except MediaInputError as exc:
            return self._failure(str(exc), retryable=exc.retryable)

        if not result.ok:
            return self._failure(
                result.stderr.strip()
                or f"FFmpeg exited with code {result.returncode}"
            )

        outputs = {
            parser.requirement: parser.parse(
                ffmpeg_output=result.stderr,
                segment=segment,
            )
            for parser in selected_parsers
        }
        return self._bundle(
            VideoRealtimeAnalysis(checked=True, outputs=outputs)
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
