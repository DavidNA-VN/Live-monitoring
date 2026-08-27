from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime

# Cấu hình kiểm tra màn hình đen
class BlackScreenCheck(BaseModel):
    enabled: bool

# Cấu hình kiểm tra mất âm thanh
class AudioLossCheck(BaseModel):
    enabled: bool
    threshold_dbfs: float = Field(..., le=0)
    duration_seconds: float = Field(..., gt=0)
    track_index: int = Field(default=0, ge=0)

# Gom nhóm các kiểm tra (checks)
class StreamChecks(BaseModel):
    black_screen: BlackScreenCheck
    audio_loss: AudioLossCheck

# DTO: Data Transfer Object dùng để nhận request tạo stream mới từ UI
class StreamConfigDTO(BaseModel):
    model_config = ConfigDict(extra="forbid") # Cấm truyền field lạ không có trong schema
    
    schema_version: Literal["1.0"] = "1.0"
    stream_id: str = Field(..., min_length=1, max_length=128)
    master_url: str = Field(..., min_length=1)
    checks: StreamChecks

# Phân loại Alert: lỗi nội dung (video/audio) hay lỗi hệ thống (runtime)
class AlertCategory(str, Enum):
    CONTENT = "content"
    RUNTIME = "runtime"

# Loại sự kiện
class EventType(str, Enum):
    BLACK_SCREEN = "BLACK_SCREEN"
    REPEATED_BLACK_SCREEN = "REPEATED_BLACK_SCREEN"
    AUDIO_LOSS = "AUDIO_LOSS"
    RUNTIME_HEALTH = "RUNTIME_HEALTH"

# Trạng thái của sự kiện: Bắt đầu (OPEN), Cập nhật (UPDATE), Đã giải quyết (RESOLVED)
class AlertState(str, Enum):
    OPEN = "OPEN"
    UPDATE = "UPDATE"
    RESOLVED = "RESOLVED"
    DEGRADED = "DEGRADED"
    RECOVERED = "RECOVERED"

# DTO mô tả thông tin Cảnh báo (Alert) gửi qua WebSocket
class AlertDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    attributes: Optional[Dict[str, str]] = None

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
class RuntimeStatusDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    stream_id: str = Field(..., min_length=1, max_length=128)
    status: StreamStatusEnum
    health: StreamHealthEnum
    started_at: Optional[datetime] = None
    last_poll_at: Optional[datetime] = None
    active_variant_count: int = Field(..., ge=0)
    queue_depth: int = Field(..., ge=0)
    queue_lag_seconds: Optional[float] = Field(None, ge=0)
    error: Optional[str] = None
    telemetry_available: bool = False
    health_reasons: Optional[List[str]] = None
    checks: Dict[str, Literal["ENABLED", "DISABLED"]]
