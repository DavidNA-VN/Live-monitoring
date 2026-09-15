from collections.abc import Iterable
from contextlib import ExitStack
import math

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
from models.macroblocking import (
    MacroblockingAnalyzerConfig,
    MacroblockingFusionStrategy,
)
from profiles.video_realtime.boundary_sampling import (
    BoundarySampleWorkspace,
    read_boundary_fingerprints,
)
from profiles.video_realtime.freeze_evidence import FreezeEvidenceAssembler
from profiles.video_realtime.macroblocking_sampling import (
    MacroblockingSampleWorkspace,
    read_macroblocking_observations,
)
from detectors.macroblocking import MacroblockingAnalyzer
from profiles.video_realtime.parser import (
    BlackdetectParser,
    FreezedetectParser,
    VideoFilterParser,
)


class VideoRealtimeProfile:
    name = "video_realtime"
    resource_class = AnalysisResourceClass.VIDEO_DECODE

    def __init__(
        self,
        *,
        pix_th: float = 0.10,
        pic_th: float = 0.98,
        freeze_noise_db: float = -60.0,
        freeze_detector_minimum_duration: float = 0.2,
        timeout: float = 20.0,
        runner: ProcessRunner | None = None,
        media_input_resolver: MediaInputResolver | None = None,
        parsers: Iterable[VideoFilterParser] | None = None,
        command_builder: VideoRealtimeCommandBuilder | None = None,
        enable_macroblocking: bool = False,
        macroblocking_analyzer: MacroblockingAnalyzer | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        configured = tuple(
            parsers
            if parsers is not None
            else (
                BlackdetectParser(pix_th=pix_th, pic_th=pic_th),
                FreezedetectParser(
                    noise_db=freeze_noise_db,
                    minimum_duration=freeze_detector_minimum_duration,
                ),
            )
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
        self.enable_macroblocking = enable_macroblocking
        self.provides = frozenset(
            requirements
            + (
                [AnalysisRequirement.MACROBLOCKING_OBSERVATIONS]
                if enable_macroblocking
                else []
            )
        )
        self.command_builder = command_builder or VideoRealtimeCommandBuilder()
        self.macroblocking_analyzer = macroblocking_analyzer or (
            MacroblockingAnalyzer(
                MacroblockingAnalyzerConfig(
                    fusion_strategy=(
                        MacroblockingFusionStrategy.MAX_WITH_CONSISTENCY
                    )
                )
            )
            if enable_macroblocking
            else None
        )
        self.freeze_parser = next(
            (
                parser
                for parser in configured
                if parser.requirement is AnalysisRequirement.FREEZE_INTERVALS
            ),
            None,
        )
        self.black_parser = next(
            (
                parser
                for parser in configured
                if parser.requirement is AnalysisRequirement.BLACK_INTERVALS
            ),
            None,
        )

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
        public_parsers = tuple(
            parser
            for parser in self.parsers
            if parser.requirement in requested
        )
        macroblocking_requested = (
            AnalysisRequirement.MACROBLOCKING_OBSERVATIONS in requested
        )
        if not public_parsers and not macroblocking_requested:
            raise ValueError("No video filter matches requested requirements")

        freeze_requested = AnalysisRequirement.FREEZE_INTERVALS in requested
        selected_parsers = list(public_parsers)
        if freeze_requested:
            if self.freeze_parser is None or self.black_parser is None:
                raise ValueError(
                    "Freeze analysis requires freeze and black supporting parsers"
                )
            if self.black_parser not in selected_parsers:
                selected_parsers.insert(0, self.black_parser)
        selected_parsers = tuple(selected_parsers)

        try:
            diagnostics: dict[str, int | float] = {}
            with self.media_input_resolver.open(segment) as media_input:
                with ExitStack() as stack:
                    boundary_workspace = (
                        stack.enter_context(BoundarySampleWorkspace())
                        if freeze_requested
                        else None
                    )
                    macroblocking_workspace = (
                        stack.enter_context(MacroblockingSampleWorkspace())
                        if macroblocking_requested
                        else None
                    )
                    command = self.command_builder.build(
                        media_input=media_input,
                        filter_expressions=tuple(
                            parser.filter_expression
                            for parser in selected_parsers
                        ),
                        boundary_raw_output=(
                            boundary_workspace.raw_path
                            if boundary_workspace is not None
                            else None
                        ),
                        macroblocking_raw_output=(
                            macroblocking_workspace.raw_path
                            if macroblocking_workspace is not None
                            else None
                        ),
                        macroblocking_width=(
                            self.macroblocking_analyzer.config.analysis_width
                            if self.macroblocking_analyzer is not None
                            else 480
                        ),
                        macroblocking_height=(
                            self.macroblocking_analyzer.config.analysis_height
                            if self.macroblocking_analyzer is not None
                            else 270
                        ),
                        macroblocking_sampling_fps=(
                            self.macroblocking_analyzer.config.sampling_fps
                            if self.macroblocking_analyzer is not None
                            else 1.0
                        ),
                        macroblocking_max_frames=(
                            math.ceil(
                                segment.duration
                                * self.macroblocking_analyzer.config.sampling_fps
                            )
                            + 2
                            if self.macroblocking_analyzer is not None
                            else 64
                        ),
                    )
                    result = self.runner.run(command, timeout=self.timeout)
                    if not result.ok:
                        return self._failure(
                            result.stderr.strip()
                            or f"FFmpeg exited with code {result.returncode}"
                        )

                    parsed = {
                        parser.requirement: parser.parse(
                            ffmpeg_output=result.stderr,
                            segment=segment,
                        )
                        for parser in selected_parsers
                    }
                    if freeze_requested:
                        first_frame, last_frame = read_boundary_fingerprints(
                            boundary_workspace.raw_path
                        )
                        assembler = FreezeEvidenceAssembler(
                            minimum_duration=self.freeze_parser.minimum_duration
                        )
                        raw_freeze = parsed[
                            AnalysisRequirement.FREEZE_INTERVALS
                        ]
                        effective_freeze = assembler.assemble(
                            raw_freeze_intervals=raw_freeze,
                            black_intervals=parsed[
                                AnalysisRequirement.BLACK_INTERVALS
                            ],
                            segment_duration=segment.duration,
                            first_frame=first_frame,
                            last_frame=last_frame,
                        )
                        parsed[AnalysisRequirement.FREEZE_INTERVALS] = (
                            effective_freeze
                        )
                        raw_seconds = sum(
                            interval.duration for interval in raw_freeze
                        )
                        effective_seconds = sum(
                            interval.duration for interval in effective_freeze
                        )
                        diagnostics = {
                            "video_freeze_raw_interval_total": len(raw_freeze),
                            "video_freeze_raw_seconds_total": raw_seconds,
                            "video_freeze_black_overlap_seconds_total": max(
                                0.0, raw_seconds - effective_seconds
                            ),
                            "video_freeze_boundary_fingerprint_total": sum(
                                int(item.start_boundary_fingerprint is not None)
                                + int(item.end_boundary_fingerprint is not None)
                                for item in effective_freeze
                            ),
                        }
                    if macroblocking_requested:
                        if (
                            self.macroblocking_analyzer is None
                            or macroblocking_workspace is None
                        ):
                            raise ValueError(
                                "Macroblocking analyzer is not configured"
                            )
                        observations = read_macroblocking_observations(
                            macroblocking_workspace.raw_path,
                            analyzer=self.macroblocking_analyzer,
                            width=(
                                self.macroblocking_analyzer.config.analysis_width
                            ),
                            height=(
                                self.macroblocking_analyzer.config.analysis_height
                            ),
                            sampling_fps=(
                                self.macroblocking_analyzer.config.sampling_fps
                            ),
                            segment_duration=segment.duration,
                        )
                        parsed[
                            AnalysisRequirement.MACROBLOCKING_OBSERVATIONS
                        ] = observations
                        diagnostics["macroblocking_sample_total"] = len(
                            observations
                        )
        except ProcessTimeoutError as exc:
            return self._failure(str(exc), timed_out=True)
        except ProcessStartError as exc:
            return self._failure(str(exc))
        except MediaInputError as exc:
            return self._failure(str(exc), retryable=exc.retryable)
        except (OSError, ValueError) as exc:
            return self._failure(f"Video frame sampling failed: {exc}")

        outputs = {
            requirement: parsed[requirement]
            for requirement in requested
        }
        return self._bundle(
            VideoRealtimeAnalysis(
                checked=True,
                outputs=outputs,
                diagnostics=diagnostics,
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
