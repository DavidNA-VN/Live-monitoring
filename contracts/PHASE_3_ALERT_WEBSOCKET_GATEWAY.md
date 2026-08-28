# PHASE 3: ALERT HISTORY & WEBSOCKET GATEWAY CONTRACT

## 1. Overview & Architecture

Phase 3 kết nối Redis alert outbox (`media-monitor:v1:alerts:outbox`) với REST history và WebSocket realtime:

```text
Detectors (Black-Screen / Audio-Loss / Runtime-Health)
        │
        ▼
AlertEnvelope (Worker domain)
        │
        ▼
Redis Stream: media-monitor:v1:alerts:outbox
        │
        ├─── Paginated XREVRANGE ───► RedisAlertSource.recent() ───► GET /api/v1/streams/{id}/events
        │
        └─── Independent XREAD ─────► RedisAlertSource.subscribe() ──► WebSocket /api/v1/ws/streams/{id}
```

---

## 2. Public Alert Transport & Data Source

- **Nguồn dữ liệu duy nhất**:
  - Redis Stream: `media-monitor:v1:alerts:outbox` qua `AlertRedisKeys.outbox()`.
- **Ranh giới cấm**:
  - Không đọc trực tiếp internal event hashes (`stream:{storage_id}:check:{check}:...`).
  - Không đọc runtime metrics hay `storage_id`.
  - Không import domain model `AlertEnvelope` vào Presentation.

---

## 3. Flat Stream Entry & Flattened Attributes Format

Outbox lưu trữ flat key-value pairs kết hợp JSON `payload` (chứa `attributes`) và flattened attributes phục vụ công cụ giám sát Redis Insight:

```text
schema_version     = "1.0"
alert_id           = "alert-01"
event_id           = "event-01"
category           = "content"
type               = "BLACK_SCREEN"
state              = "OPEN"
stream_id          = "channel-01"
occurred_at        = "2026-08-28T10:00:00+00:00"
emitted_at         = "2026-08-28T10:00:01+00:00"
reason             = "Black screen detected"
payload            = "{\"duration\":\"8.0\",\"severity\":\"ALERT\"}"
duration           = "8.0"
severity           = "ALERT"
```

### Quy tắc Strict Codec:
1. `KNOWN_FIELDS`: `schema_version`, `alert_id`, `event_id`, `category`, `type`, `state`, `stream_id`, `occurred_at`, `emitted_at`, `reason`, `payload`, `check`, `variant_id`, `variant_stable_id`, `event_started_at`, `event_ended_at`.
2. Mọi field nằm ngoài `KNOWN_FIELDS` bắt buộc phải tồn tại trong `payload` JSON object và có giá trị string khớp chính xác.
3. Cấm tuyệt đối trường `storage_id`.
4. Bắt buộc ISO-8601 có explicit timezone offset. Naive datetime bị từ chối (`AlertCodecError`).

---

## 4. REST Recent History (`GET /api/v1/streams/{stream_id}/events`)

- **Thao tác Redis**: `XREVRANGE media-monitor:v1:alerts:outbox <cursor> - COUNT <page_size>`.
- **Thứ tự trả về**: Chronological (từ cũ đến mới, `matched.reverse()`).
- **Giới hạn duyệt (Bounded Scan)**: Quét tối đa `history_scan_limit` (mặc định 1000 entries) để gom đủ `limit` (mặc định 50, tối đa 100).
- **Poison Entry Isolation**: Entry bị lỗi format/corrupt JSON sẽ bị bỏ qua và ghi log `entry_id` + error type (không in raw payload) mà không làm crash toàn bộ history query.

---

## 5. WebSocket Realtime & Fan-out (`/api/v1/ws/streams/{stream_id}`)

### Fan-out không dùng Consumer Group
- Mỗi kết nối WebSocket chụp ID mới nhất của outbox đúng một lần bằng `XREVRANGE ... COUNT 1`, sau đó chỉ dùng local cursor cụ thể với `XREAD BLOCK 1000 COUNT 100 STREAMS outbox <cursor>`.
- Nếu outbox chưa tồn tại, cursor khởi tạo là `0-0`. Không truyền lại `$` sau timeout vì `$` được Redis diễn giải lại ở mỗi lệnh và có thể bỏ sót alert xuất hiện giữa hai lần `XREAD`.
- Tuyệt đối **không dùng** `XREADGROUP`, `XGROUP`, `XACK` hay shared cursor. Nhờ đó mọi browser kết nối cùng lúc đều nhận đủ toàn bộ alert realtime.

### WebSocket Envelope (`AlertMessageDTO`)
Dữ liệu gửi tới client được bọc trong envelope chuẩn:
```json
{
  "message_type": "ALERT",
  "stream_id": "channel-01",
  "payload": {
    "schema_version": "1.0",
    "alert_id": "alert-01",
    "event_id": "event-01",
    "category": "content",
    "event_type": "BLACK_SCREEN",
    "state": "OPEN",
    "stream_id": "channel-01",
    "occurred_at": "2026-08-28T10:00:00+00:00",
    "emitted_at": "2026-08-28T10:00:01+00:00",
    "reason": "Black screen detected",
    "attributes": {
      "duration": "8.0",
      "severity": "ALERT"
    }
  }
}
```

### Reconnection & Close Codes
- Khi gặp lỗi Redis trong lúc mở subscription hoặc đang subscribe: bounded exponential backoff (0.1s $\to$ 2.0s max) và **giữ nguyên cursor cụ thể** để không bị mất alerts sinh ra trong thời gian Redis gián đoạn.
- Khi WebSocket disconnect hoặc application shutdown, task đang block ở `XREAD` phải được cancel ngay và `CancelledError` phải tiếp tục propagate.
- **WebSocket Close Codes**:
  - `1000`: Normal closure (client ngắt kết nối).
  - `1013`: Temporary infrastructure failure (Redis unavailable không hồi phục).
  - `1011`: Unexpected internal server error.

---

## 6. Connection Ownership

Injected Redis clients are externally owned; Phase 4 composition root owns their lifecycle. Adapter `close()` method chỉ cancel các async subscription tasks cục bộ và không đóng shared Redis connection.
