from datetime import datetime
from enum import Enum
from typing import Dict, List, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PresentationDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

# Cấu hình kiểm tra màn hình đen
class BlackScreenCheck(PresentationDTO):
    enabled: bool

# Cấu hình kiểm tra mất âm thanh
class AudioLossCheck(PresentationDTO):
    enabled: bool
    threshold_dbfs: float = Field(..., le=0)
    duration_seconds: float = Field(..., gt=0)
    track_index: int = Field(default=0, ge=0)


class VideoFreezeCheck(PresentationDTO):
    enabled: bool = False
    noise_db: float = Field(default=-60.0, le=0)
    detector_minimum_duration: float = Field(default=0.2, gt=0)
    warning_duration_seconds: float = Field(default=3.0, gt=0)
    alert_duration_seconds: float = Field(default=5.0, gt=0)

    @model_validator(mode="after")
    def validate_threshold_order(self):
        if self.alert_duration_seconds <= self.warning_duration_seconds:
            raise ValueError(
                "alert_duration_seconds must be greater than "
                "warning_duration_seconds"
            )
        return self


class AdmissionConfig(PresentationDTO):
    startup_mode: Literal["bounded_history", "full_snapshot"] = (
        "bounded_history"
    )
    startup_lookback_segments: int = Field(default=4, gt=0)
    soft_lag_target_durations: float = Field(default=2.0, gt=0)
    recovery_lag_target_durations: float = Field(default=1.5, gt=0)
    hard_lag_target_durations: float = Field(default=6.0, gt=0)
    live_edge_retention_segments: int = Field(default=2, gt=0)
    transition_cycles: int = Field(default=3, gt=0)

    @model_validator(mode="after")
    def validate_lag_thresholds(self):
        if (
            self.recovery_lag_target_durations
            >= self.soft_lag_target_durations
        ):
            raise ValueError(
                "recovery_lag_target_durations must be less than "
                "soft_lag_target_durations"
            )
        if self.hard_lag_target_durations <= self.soft_lag_target_durations:
            raise ValueError(
                "hard_lag_target_durations must be greater than "
                "soft_lag_target_durations"
            )
        return self


class VariantSelectionConfig(PresentationDTO):
    mode: Literal["all", "representative", "explicit"] = "all"
    representative_count: int = Field(default=3, gt=0)
    explicit_variant_ids: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_selection(self):
        normalized = [value.strip() for value in self.explicit_variant_ids]
        if any(not value for value in normalized):
            raise ValueError("explicit_variant_ids must not contain empty IDs")
        if self.mode == "explicit" and not normalized:
            raise ValueError("explicit mode requires explicit_variant_ids")
        if self.mode != "explicit" and normalized:
            raise ValueError("explicit_variant_ids require explicit mode")
        self.explicit_variant_ids = list(dict.fromkeys(normalized))
        return self

# Gom nhóm các kiểm tra (checks)
class StreamChecks(PresentationDTO):
    black_screen: BlackScreenCheck
    audio_loss: AudioLossCheck
    video_freeze: VideoFreezeCheck = Field(
        default_factory=VideoFreezeCheck
    )

# DTO: Data Transfer Object dùng để nhận request tạo stream mới từ UI
class StreamConfigDTO(PresentationDTO):
    
    schema_version: Literal["1.0"] = "1.0"
    stream_id: str = Field(..., min_length=1, max_length=128)
    master_url: str = Field(..., min_length=1)
    checks: StreamChecks
    admission: AdmissionConfig = Field(default_factory=AdmissionConfig)
    variant_selection: VariantSelectionConfig = Field(
        default_factory=VariantSelectionConfig
    )

    @field_validator("master_url")
    @classmethod
    def validate_master_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("master_url must be an absolute HTTP(S) URL")
        return value

# Loại lệnh điều khiển
class CommandTypeEnum(str, Enum):
    START = "START"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STOP = "STOP"
    UPDATE_CONFIG = "UPDATE_CONFIG"

# Trạng thái kết quả thực thi lệnh
class CommandResultStatusEnum(str, Enum):
    APPLIED = "APPLIED"
    NOOP = "NOOP"
    REJECTED = "REJECTED"
    FAILED = "FAILED"

# DTO phản hồi khi tiếp nhận lệnh (202 Accepted)
class CommandSubmissionDTO(PresentationDTO):

    schema_version: Literal["1.0"] = "1.0"
    command_id: str = Field(..., min_length=1)
    stream_id: str = Field(..., min_length=1, max_length=128)
    status: Literal["ACCEPTED"] = "ACCEPTED"
    message: Optional[str] = None

# DTO trả về khi tra cứu kết quả xử lý của một lệnh
class CommandResultDTO(PresentationDTO):

    schema_version: Literal["1.0"] = "1.0"
    command_id: str = Field(..., min_length=1)
    command_type: CommandTypeEnum
    stream_id: str = Field(..., min_length=1, max_length=128)
    status: CommandResultStatusEnum
    changed: bool
    processed_at: datetime
    error_code: Optional[str] = None
    error: Optional[str] = None

# Phân loại Alert: lỗi nội dung (video/audio) hay lỗi hệ thống (runtime)
class AlertCategory(str, Enum):
    CONTENT = "content"
    RUNTIME = "runtime"

# Loại sự kiện
class EventType(str, Enum):
    BLACK_SCREEN = "BLACK_SCREEN"
    REPEATED_BLACK_SCREEN = "REPEATED_BLACK_SCREEN"
    AUDIO_LOSS = "AUDIO_LOSS"
    VIDEO_FREEZE = "VIDEO_FREEZE"
    REPEATED_VIDEO_FREEZE = "REPEATED_VIDEO_FREEZE"
    RUNTIME_HEALTH = "RUNTIME_HEALTH"

# Trạng thái của sự kiện: Bắt đầu (OPEN), Cập nhật (UPDATE), Đã giải quyết (RESOLVED)
class AlertState(str, Enum):
    OPEN = "OPEN"
    UPDATE = "UPDATE"
    RESOLVED = "RESOLVED"
    DEGRADED = "DEGRADED"
    RECOVERED = "RECOVERED"

# DTO mô tả thông tin Cảnh báo (Alert) gửi qua WebSocket
class AlertDTO(PresentationDTO):

    schema_version: Literal["1.0"] = "1.0"
    alert_id: str = Field(..., min_length=1)
    event_id: str = Field(..., min_length=1)
    category: AlertCategory
    event_type: EventType
    state: AlertState
    stream_id: str = Field(..., min_length=1, max_length=128)
    check: Optional[str] = None
    variant_id: Optional[str] = None
    variant_stable_id: Optional[str] = None
    occurred_at: datetime
    emitted_at: datetime
    event_started_at: Optional[datetime] = None
    event_ended_at: Optional[datetime] = None
    reason: str = Field(..., min_length=1)
    attributes: Dict[str, str] = Field(default_factory=dict)

# DTO bao gói Cảnh báo gửi qua WebSocket Realtime
class AlertMessageDTO(PresentationDTO):
    message_type: Literal["ALERT"] = "ALERT"
    stream_id: str = Field(..., min_length=1, max_length=128)
    payload: AlertDTO

    @model_validator(mode="after")
    def verify_stream_id_match(self) -> "AlertMessageDTO":
        if self.stream_id != self.payload.stream_id:
            raise ValueError(
                f"Envelope stream_id '{self.stream_id}' does not match payload stream_id '{self.payload.stream_id}'"
            )
        return self

# Trạng thái vòng đời của luồng live
class StreamStatusEnum(str, Enum):
    CREATED = "CREATED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"

# Tình trạng sức khỏe của luồng
class StreamHealthEnum(str, Enum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"

# DTO trả về khi gọi API lấy Status của Stream
class RuntimeStatusDTO(PresentationDTO):

    schema_version: Literal["1.0"] = "1.0"
    stream_id: str = Field(..., min_length=1, max_length=128)
    status: StreamStatusEnum
    health: StreamHealthEnum
    started_at: Optional[datetime] = None
    last_poll_at: Optional[datetime] = None
    active_variant_count: int = Field(..., ge=0)
    queue_depth: int = Field(..., ge=0)
    queue_lag_seconds: Optional[float] = Field(None, ge=0)
    live_edge_lag_seconds: Optional[float] = Field(None, ge=0)
    error: Optional[str] = None
    telemetry_available: bool = False
    health_reasons: List[str] = Field(default_factory=list)
    checks: Dict[str, Literal["ENABLED", "DISABLED"]]
    worker_id: Optional[str] = Field(None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    observed_at: Optional[datetime] = None
    admission_mode: Literal[
        "coverage", "catch_up", "live_edge_protection"
    ] = "coverage"
    startup_segments_outside_scope: int = Field(default=0, ge=0)
    dropped_expired_work: int = Field(default=0, ge=0)
    dropped_capacity_work: int = Field(default=0, ge=0)
    dropped_live_edge_work: int = Field(default=0, ge=0)
    coverage_gap_count: int = Field(default=0, ge=0)
    coverage_gap_segment_count: int = Field(default=0, ge=0)
    dropped_media_segment_count: int = Field(default=0, ge=0)
    profile_analysis_total: Dict[str, int] = Field(default_factory=dict)
    active_media_processes: int = Field(default=0, ge=0)
    max_media_processes: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_media_process_capacity(self):
        if self.active_media_processes > self.max_media_processes:
            raise ValueError(
                "active_media_processes cannot exceed max_media_processes"
            )
        if any(value < 0 for value in self.profile_analysis_total.values()):
            raise ValueError("profile_analysis_total values must be >= 0")
        return self
