# PHASE 2: PUBLIC RUNTIME STATUS API CONTRACT

## 1. Overview & Architecture

Phase 2 kết nối Presentation REST API với public runtime status projection do worker ghi vào Redis hash (`public:runtime-status`).

```text
StreamSupervisor (Worker Process)
        │
        ▼
SupervisorRuntimeStatusReader
        │
        ▼
RuntimeStatusProjectionService
        │
        ▼
Redis Hash: media-monitor:v1:public:runtime-status
  Field: <external_stream_id>
  Value: JSON snapshot
        │
        │ HGET (Single O(1) read)
        ▼
RedisRuntimeStatusReader (Presentation Adapter)
        │
        ▼
GET /api/v1/streams/{stream_id}/status
        │
        ▼
Dashboard / Client
```

---

## 2. Single Source of Truth & Boundary

- **Nguồn dữ liệu duy nhất**:
  - Endpoint `GET /api/v1/streams/{stream_id}/status` chỉ đọc duy nhất key:
    `HGET media-monitor:v1:public:runtime-status <external_stream_id>`
    thông qua `PublicRuntimeRedisKeys.current_statuses()`.
- **Ranh giới cấm tuyệt đối**:
  - Không đọc internal metrics: `RuntimeRedisKeys.health()`, `RuntimeRedisKeys.metrics()`, `RuntimeRedisKeys.active_variants()`.
  - Không truy cập `storage_id`, `StreamSupervisor` hay `StreamSession`.
  - Không chạy các lệnh Redis quét diện rộng: `HGETALL`, `KEYS`, `SCAN`.

---

## 3. Strict Public Schema Contract

Payload JSON trong Hash tuân thủ nghiêm ngặt [runtime-status.schema.json](file:///d:/ViettelIntern/media-monitor/contracts/runtime-status.schema.json):

### Required Fields (Bắt buộc)
| Field | Type | Validation & Anti-Coercion Rule |
| :--- | :--- | :--- |
| `schema_version` | `const "1.0"` | Bắt buộc đúng `"1.0"`. |
| `stream_id` | `string` | Độ dài 1..128, bắt buộc trùng với `requested_stream_id`. |
| `status` | `string` | Enum: `CREATED`, `STARTING`, `RUNNING`, `PAUSED`, `STOPPING`, `STOPPED`, `FAILED`. |
| `health` | `string` | Enum: `UNKNOWN`, `HEALTHY`, `DEGRADED`, `UNHEALTHY`. |
| `active_variant_count` | `integer` | Integer thật (`>= 0`). Reject `True`/`False`. |
| `queue_depth` | `integer` | Integer thật (`>= 0`). Reject `True`/`False`. |
| `checks` | `object` | Map từ check name $\to$ `"ENABLED"` hoặc `"DISABLED"`. |
| `worker_id` | `string` | Regex `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`. |
| `observed_at` | `string` | ISO-8601 có explicit timezone offset (reject naive datetime). |

### Optional Fields (Tùy chọn)
| Field | Type | Rule |
| :--- | :--- | :--- |
| `started_at` | `string \| null` | ISO-8601 có timezone offset. |
| `last_poll_at` | `string \| null` | ISO-8601 có timezone offset. |
| `queue_lag_seconds` | `number \| null` | Number hữu hạn $\ge 0$ (reject NaN, Inf, -Inf, boolean). |
| `error` | `string \| null` | Chuỗi mô tả lỗi nếu có. |
| `telemetry_available` | `boolean` | Boolean thật (`True`/`False`). |
| `health_reasons` | `array[string]` | Danh sách lý do ảnh hưởng health. |

### Prohibited Fields (Cấm)
Tuyệt đối không chấp nhận các trường nội bộ rò rỉ:
`storage_id`, `internal_metrics`, `redis_key`, `supervisor_state`.

---

## 4. Freshness Semantics vs Liveness

> [!IMPORTANT]
> **Freshness Guarantee**:
> "Runtime status endpoint returns the latest projected state, not an independent worker-liveness guarantee."

- `observed_at` cho biết thời điểm worker quan sát và project trạng thái lần cuối.
- API trả nguyên `observed_at` và `status` từ Redis mà không tự ý đổi `RUNNING` thành `FAILED` hoặc `HEALTHY` thành `UNHEALTHY`.
- API không kiểm tra worker heartbeat tại endpoint này để đảm bảo hiệu năng và tính độc lập của read model.

---

## 5. Eventual Consistency & Lifecycle

Read model trong Redis Hash mang tính chất **eventually consistent**:
- **START**: Sau khi worker khởi động session và chạy projection cycle, hash có key $\to$ API trả `RUNNING`.
- **PAUSE**: Command trả `202 Accepted` ngay; sau projection cycle, hash cập nhật $\to$ API trả `PAUSED`.
- **RESUME**: Sau projection cycle $\to$ API trả `RUNNING`.
- **STOP**: Worker projector gọi `HDEL public:runtime-status <stream_id>` $\to$ hash field bị xóa $\to$ API trả **`404 Not Found`**.

API không lưu trạng thái giả lập trong bộ nhớ và không cache snapshot cũ.

---

## 6. HTTP Failure Matrix & Error Mapping

| Trường hợp | HTTP Status | Response Body | Hành vi Server |
| :--- | :--- | :--- | :--- |
| **Snapshot hợp lệ** | `200 OK` | `RuntimeStatusDTO` | Trả dữ liệu JSON đã validate strict. |
| **Stream không tồn tại / Đã STOP** | `404 Not Found` | `{"detail": "Không tìm thấy stream này"}` | `HGET` trả `None`. |
| **Redis offline / Timeout** | `503 Service Unavailable` | `{"detail": "Monitoring service is temporarily unavailable"}` | Bắt `redis.RedisError`, log type error. |
| **JSON syntax hỏng** | `503 Service Unavailable` | `{"detail": "Monitoring service is temporarily unavailable"}` | Ném `CorruptRuntimeStatusError`, log server. |
| **Payload vi phạm schema / Extra fields** | `503 Service Unavailable` | `{"detail": "Monitoring service is temporarily unavailable"}` | Ném `CorruptRuntimeStatusError`, log server. |
| **Stream ID trong payload mismatch** | `503 Service Unavailable` | `{"detail": "Monitoring service is temporarily unavailable"}` | Ném `CorruptRuntimeStatusError`, log server. |

> [!CAUTION]
> Phản hồi lỗi HTTP không bao giờ chứa raw Redis payload, Redis URL/credentials hoặc stack trace.

---

## 7. Connection Ownership

Injected Redis clients are externally owned; Phase 4 composition root owns their lifecycle. Adapter `close()` method là no-op và không đóng shared Redis connection.

## 8. Presentation Error Boundary

Runtime-status exceptions keep concrete types for adapter tests and server logs,
but inherit the shared `PresentationServiceUnavailableError`. FastAPI therefore
uses one sanitized 503 handler for command, status, and future alert adapters
instead of growing one HTTP handler per Redis implementation.

The strict codec also wraps final Pydantic validation failures as
`RuntimeStatusCodecError`; malformed snapshots cannot escape as an unhandled
500 response.
