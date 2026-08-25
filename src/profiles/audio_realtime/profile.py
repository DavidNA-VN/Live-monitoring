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
    AudioRealtimeAnalysis,
    SegmentAnalysisBundle,
)
from models.audio import AudioTrackHint, AudioTrackPresence
from models.segment import Segment
from models.rendition import MediaRenditionKind
from profiles.audio_realtime.command_builder import AudioRealtimeCommandBuilder
from profiles.audio_realtime.parser import AudioFilterParser, SilencedetectParser


_MISSING_AUDIO_MAP_DIAGNOSTIC = "matches no streams"


class AudioRealtimeProfile:
    name = "audio_realtime"
    resource_class = AnalysisResourceClass.AUDIO_DECODE

    def __init__(
        self,
        *,
        threshold_dbfs: float = -60.0,
        detector_minimum_duration: float = 0.1,
        track_index: int = 0,
        timeout: float = 20.0,
        runner: ProcessRunner | None = None,
        media_input_resolver: MediaInputResolver | None = None,
        parsers: Iterable[AudioFilterParser] | None = None,
        command_builder: AudioRealtimeCommandBuilder | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        configured = tuple(
            parsers
            if parsers is not None
            else (
                SilencedetectParser(
                    threshold_dbfs=threshold_dbfs,
                    minimum_duration=detector_minimum_duration,
                ),
            )
        )
        if not configured:
            raise ValueError("At least one audio filter parser is required")
        requirements = [parser.requirement for parser in configured]
        if len(set(requirements)) != len(requirements):
            raise ValueError("Audio filter parser requirements must be unique")

        self.timeout = timeout
        self.runner = runner or ProcessRunner()
        self.media_input_resolver = (
            media_input_resolver or HlsMediaInputResolver()
        )
        self.parsers = configured
        self.provides = frozenset(requirements)
        self.command_builder = command_builder or AudioRealtimeCommandBuilder(
            track_index=track_index
        )

    @staticmethod
    def supports_segment(segment: Segment) -> bool:
        return segment.audio_track_hint is not AudioTrackHint.EXTERNAL

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
        requested, selected_parsers = self._select_parsers(requirements)

        if segment.gap:
            return self._failure(
                "HLS segment declared unavailable by EXT-X-GAP",
                retryable=False,
            )
        if segment.audio_track_hint is AudioTrackHint.EXTERNAL:
            return self._failure(
                "External HLS audio rendition is not supported by baseline",
                retryable=False,
            )
        if segment.audio_track_hint is AudioTrackHint.ABSENT:
            return self._absent(requested)

        try:
            with self.media_input_resolver.open(segment) as media_input:
                command = self.command_builder.build(
                    media_input=media_input,
                    filter_expressions=tuple(
                        parser.filter_expression for parser in selected_parsers
                    ),
                    track_index=(
                        0
                        if segment.rendition_kind is MediaRenditionKind.AUDIO
                        else None
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
            if _MISSING_AUDIO_MAP_DIAGNOSTIC in result.stderr:
                return self._absent(requested)
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
            AudioRealtimeAnalysis(
                checked=True,
                presence=AudioTrackPresence.PRESENT,
                outputs=outputs,
            )
        )

    def _select_parsers(
        self,
        requirements: frozenset[AnalysisRequirement] | None,
    ) -> tuple[frozenset[AnalysisRequirement], tuple[AudioFilterParser, ...]]:
        requested = requirements if requirements is not None else self.provides
        unsupported = requested.difference(self.provides)
        if unsupported:
            raise ValueError(
                f"Unsupported audio requirements: {sorted(unsupported)}"
            )
        selected = tuple(
            parser
            for parser in self.parsers
            if parser.requirement in requested
        )
        if not selected:
            raise ValueError("No audio filter matches requested requirements")
        return requested, selected

    def _absent(
        self,
        requirements: frozenset[AnalysisRequirement],
    ) -> SegmentAnalysisBundle:
        return self._bundle(
            AudioRealtimeAnalysis(
                checked=True,
                presence=AudioTrackPresence.ABSENT,
                retryable=False,
                outputs={requirement: () for requirement in requirements},
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
            AudioRealtimeAnalysis(
                checked=False,
                presence=AudioTrackPresence.UNKNOWN,
                error=error,
                retryable=retryable,
                timed_out=timed_out,
            )
        )

    def _bundle(
        self,
        analysis: AudioRealtimeAnalysis,
    ) -> SegmentAnalysisBundle:
        return SegmentAnalysisBundle(
            profile_name=self.name,
            audio_realtime=analysis,
        )
