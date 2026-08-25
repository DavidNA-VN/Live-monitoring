from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from models.audio import AudioTrackPresence, SilenceInterval


class AudioLossSignal(str, Enum):
    AUDIBLE = "audible"
    SILENT = "silent"
    MISSING = "missing"
    UNKNOWN = "unknown"


class AudioLossCause(str, Enum):
    CONTINUOUS_SILENCE = "continuous_silence"
    AUDIO_STREAM_MISSING = "audio_stream_missing"


class AudioLossEventStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


@dataclass
class AudioLossDetectionResult:
    variant_id: str
    sequence: int
    segment_uri: str
    segment_duration: float
    track_presence: AudioTrackPresence
    signal: AudioLossSignal
    program_date_time: datetime | None = None
    checked: bool = True
    error: str | None = None
    retryable: bool = True
    silence_intervals: list[SilenceInterval] = field(default_factory=list)


@dataclass
class AudioLossLiveEvent:
    event_id: str
    stream_id: str
    variant_id: str
    variant_stable_id: str
    discontinuity_sequence: int
    start_sequence: int
    end_sequence: int
    start_offset: float
    end_offset: float
    start_program_time: datetime | None
    end_program_time: datetime | None
    duration: float
    last_segment_duration: float
    primary_cause: AudioLossCause
    causes_seen: list[AudioLossCause] = field(default_factory=list)
    affected_segment_count: int = 1
    status: AudioLossEventStatus = AudioLossEventStatus.OPEN
    alert_sent: bool = False
    resolution_reason: str | None = None
    timeline_generation: int = 0
    start_media_revision: str = ""
    last_media_revision: str = ""
    audio_group: str | None = None
    rendition_name: str | None = None
    language: str | None = None
    rendition_default: bool = False
    rendition_autoselect: bool = False
    hls_stable_rendition_id: str | None = None
