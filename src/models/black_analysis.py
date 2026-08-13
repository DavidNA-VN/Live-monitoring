from dataclasses import dataclass, field


@dataclass
class BitstreamIssue:
    issue_type: str
    message: str
    timestamp: float | None = None


@dataclass
class BitstreamSegmentCheck:
    sequence: int
    uri: str
    checked: bool
    has_error: bool
    returncode: int
    issues: list[BitstreamIssue] = field(
        default_factory=list
    )
    analyzer_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.checked and not self.has_error

    @property
    def error_lines(self) -> list[str]:
        return [
            issue.message
            for issue in self.issues
        ]


@dataclass
class BitstreamEvidence:
    checked_segment_count: int
    failed_segment_count: int
    has_bitstream_error: bool
    segment_checks: list[BitstreamSegmentCheck] = field(
        default_factory=list
    )


@dataclass
class AudioEvidence:
    checked: bool
    has_audio: bool
    audio_active_during_black: bool
    silence_ratio: float
    error: str | None = None


@dataclass
class TransitionEvidence:
    checked: bool
    fade_out: bool
    fade_in: bool
    abrupt_start: bool
    abrupt_end: bool
    start_boundary_jump: float | None = None
    end_boundary_jump: float | None = None
    pre_black_luma: list[float] = field(
        default_factory=list
    )
    start_black_luma: list[float] = field(
        default_factory=list
    )
    end_black_luma: list[float] = field(
        default_factory=list
    )
    post_black_luma: list[float] = field(
        default_factory=list
    )
    error: str | None = None


@dataclass
class CrossVariantEvidence:
    checked: bool
    analyzed_variant_count: int
    overlapping_variant_ids: list[str] = field(
        default_factory=list
    )
    min_overlap_ratio: float = 0.0

    @property
    def all_variants_affected(self) -> bool:
        return (
            self.checked
            and self.analyzed_variant_count > 0
            and len(self.overlapping_variant_ids)
            == self.analyzed_variant_count
        )

    @property
    def only_this_variant_affected(self) -> bool:
        return (
            self.checked
            and self.analyzed_variant_count > 1
            and len(self.overlapping_variant_ids) == 1
        )


@dataclass
class BlackEventAnalysis:
    variant_id: str
    start_time: float
    end_time: float
    affected_segments: list[int]
    classification: str
    technical_score: float
    confidence: float
    bitstream: BitstreamEvidence
    audio: AudioEvidence | None = None
    transition: TransitionEvidence | None = None
    cross_variant: CrossVariantEvidence | None = None
