from dataclasses import dataclass, field

from playlist.master_parser import Variant


MISSING_AUDIO_STREAM = "MISSING_AUDIO_STREAM"
AUDIO_DECODE_ERROR = "AUDIO_DECODE_ERROR"
AUDIO_TIMESTAMP_ERROR = "AUDIO_TIMESTAMP_ERROR"
CONTINUOUS_AUDIO_SILENCE = (
    "CONTINUOUS_AUDIO_SILENCE"
)
INTERMITTENT_AUDIO_LOSS = (
    "INTERMITTENT_AUDIO_LOSS"
)
AUDIO_PACKET_LOSS = "AUDIO_PACKET_LOSS"


@dataclass
class AudioStreamInfo:
    index: int
    codec_name: str | None
    sample_rate: str | None
    channels: int | None


@dataclass
class AudioIssue:
    issue_type: str
    variant_id: str
    message: str
    start_sequence: int | None = None
    end_sequence: int | None = None
    start_time: float | None = None
    end_time: float | None = None
    affected_segments: list[int] = field(
        default_factory=list
    )


@dataclass
class AudioStreamAnalysis:
    variant_id: str
    sequence: int
    segment_uri: str
    segment_duration: float
    has_audio_stream: bool
    decodable: bool
    stream_infos: list[AudioStreamInfo] = field(
        default_factory=list
    )
    decode_errors: list[str] = field(
        default_factory=list
    )
    timestamp_errors: list[str] = field(
        default_factory=list
    )


@dataclass
class AudioSilenceInterval:
    sequence: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(
            0.0,
            self.end - self.start,
        )


@dataclass
class AudioSilenceDetectionResult:
    variant_id: str
    sequence: int
    segment_uri: str
    segment_duration: float
    intervals: list[AudioSilenceInterval] = field(
        default_factory=list
    )
    checked: bool = True
    error: str | None = None


@dataclass
class AudioSilenceCandidate:
    variant_id: str
    start_time: float
    end_time: float
    duration: float
    start_sequence: int
    end_sequence: int
    affected_segments: list[int]


@dataclass
class AudioPacket:
    pts: float
    duration: float | None
    size: int | None = None


@dataclass
class AudioPacketGap:
    sequence: int
    previous_pts: float
    current_pts: float
    expected_duration: float
    actual_gap: float
    estimated_missing_packets: int


@dataclass
class AudioPacketAnalysis:
    variant_id: str
    sequence: int
    segment_uri: str
    segment_duration: float
    packet_count: int
    expected_packet_duration: float | None = None
    gaps: list[AudioPacketGap] = field(
        default_factory=list
    )
    checked: bool = True
    error: str | None = None

    @property
    def estimated_missing_packets(self) -> int:
        return sum(
            gap.estimated_missing_packets
            for gap in self.gaps
        )

    @property
    def estimated_packet_count(self) -> int:
        return (
            self.packet_count
            + self.estimated_missing_packets
        )

    @property
    def packet_loss_ratio(self) -> float:
        total = self.estimated_packet_count

        if total <= 0:
            return 0.0

        return self.estimated_missing_packets / total


@dataclass
class AudioLossVariantResult:
    variant: Variant
    checked_duration: float
    stream_results: list[AudioStreamAnalysis] = field(
        default_factory=list
    )
    packet_results: list[AudioPacketAnalysis] = field(
        default_factory=list
    )
    silence_candidates: list[
        AudioSilenceCandidate
    ] = field(default_factory=list)
    issues: list[AudioIssue] = field(
        default_factory=list
    )

    @property
    def has_issue(self) -> bool:
        return bool(self.issues)
