# PHASE 1: REDIS COMMAND GATEWAY CONTRACT

## 1. Overview
Phase 1 kết nối REST API Presentation với monitoring worker pipeline qua Redis command stream mà không import logic detector, supervisor hay core domain vào Presentation.

```text
Dashboard / Client
        │
        │ POST /api/v1/streams/... (với optional Header: Idempotency-Key)
        ▼
FastAPI (/src/presentation/api)
        │
        │ RedisMonitoringControl (redis.asyncio + Lua atomic check & XADD)
        ▼
Redis Stream: media-monitor:v1:monitoring:commands
        │
        ▼
RedisMonitoringCommandConsumer (Worker process)
        │
        ▼
MonitoringCommandHandler -> Supervisor
        │
        ▼
Redis Markers:
- media-monitor:v1:monitoring:processed:{sha256(command_id)[:32]}
- media-monitor:v1:monitoring:command-results (Stream)
        │
        ▼
RedisCommandResultReader (O(1) lookup: processed -> submitted -> missing)
        │
        ▼
GET /api/v1/commands/{command_id}
```

---

## 2. Redis Key Schema & TTL

| Redis Key | Pattern | Kiểu | Mô tả & TTL |
| :--- | :--- | :--- | :--- |
| **Commands Stream** | `{prefix}:monitoring:commands` | Stream | Chứa command entries (`{"payload": "..."}`). Worker consume qua consumer group. |
| **Command Results Stream** | `{prefix}:monitoring:command-results` | Stream | Worker publish kết quả terminal (`APPLIED`, `NOOP`, `REJECTED`, `FAILED`). |
| **Submitted Marker** | `{prefix}:monitoring:submitted:{digest}` | String (JSON) | Đánh dấu command đã được API ghi vào Redis. TTL: 86400s (24h). `digest = sha256(command_id)[:32]`. |
| **Processed Marker** | `{prefix}:monitoring:processed:{digest}` | String (JSON) | Worker ghi sau khi thực thi xong. Chứa `{"fingerprint": "...", "result": {...}}`. TTL: 86400s (24h). |
| **Idempotency Token** | `{prefix}:monitoring:idempotency:{digest}` | String (JSON) | Ánh xạ token HTTP retry về cùng `command_id` và logical fingerprint. TTL: 86400s. `digest = sha256(token)[:32]`. |
| **Dead Letter Stream** | `{prefix}:monitoring:command-dead-letter` | Stream | Chứa các command hỏng hoặc duplicate ID khác payload do worker phát hiện. |

---

## 3. Command Payload Contract
Payload tuân thủ nghiêm ngặt [monitoring-command.schema.json](file:///d:/ViettelIntern/media-monitor/contracts/monitoring-command.schema.json):

```json
{
  "schema_version": "1.0",
  "command_id": "8f6831d1-6c2e-48a6-896f-44eb1202e88a",
  "command_type": "START",
  "stream_id": "channel-01",
  "requested_at": "2026-08-28T10:00:00+00:00",
  "config": {
    "schema_version": "1.0",
    "stream_id": "channel-01",
    "master_url": "https://example.com/master.m3u8",
    "checks": {
      "black_screen": { "enabled": true },
      "audio_loss": {
        "enabled": true,
        "threshold_dbfs": -35.0,
        "duration_seconds": 5.0,
        "track_index": 0
      }
    }
  }
}
```
- `config`: Bắt buộc với `START` và `UPDATE_CONFIG`. Tuyệt đối không gửi kèm `PAUSE`, `RESUME`, `STOP`.
- Entry trong Redis Stream chỉ có duy nhất trường `"payload": compact_json`.

---

## 4. Idempotency & Concurrency Rules
1. **Header `Idempotency-Key`**:
   - Nếu không có header: API tạo UUID `command_id` ngẫu nhiên cho mỗi request.
   - Nếu có header:
     - Lần đầu: API tạo `command_id`, lưu receipt vào `idempotency_key` marker và `XADD` vào Redis Stream.
     - Retry cùng token & cùng logical payload (`command_type`, `stream_id`, `config`): API trả lại `command_id` cũ với HTTP 202 Accepted, không `XADD` lần hai.
     - Tái sử dụng token với payload khác: API trả về **HTTP 409 Conflict**.
2. **Nguyên tử (Atomicity)**:
   - Toàn bộ thao tác kiểm tra token, lưu receipt và `XADD` được thực hiện trong một **Lua script** duy nhất để triệt tiêu hoàn toàn race condition khi có nhiều request đồng thời.
   - Script kiểm tra Redis key type trước khi ghi. Command được `XADD` trước khi tạo submitted/idempotency markers, tránh để lại receipt giả khi command stream có kiểu dữ liệu không hợp lệ.
   - `submission_ttl_seconds` phải lớn hơn 0; `Idempotency-Key` dài tối đa 256 ký tự.

---

## 5. HTTP Response Semantics & Command Lookup ($O(1)$)

| Trường hợp | HTTP Status | Response Body | Mô tả |
| :--- | :--- | :--- | :--- |
| **Gửi command thành công** | `202 Accepted` | `CommandSubmissionDTO` | Redis đã nhận command, đang chờ worker xử lý. |
| **Retry cùng Idempotency-Key** | `202 Accepted` | `CommandSubmissionDTO` | Trả lại receipt cũ, không tạo command trùng lặp. |
| **Trùng Idempotency-Key khác payload** | `409 Conflict` | `{"detail": "..."}` | Từ chối xung đột idempotency token. |
| **Tra cứu command đang chờ** | `202 Accepted` | `CommandSubmissionDTO` | Đã tồn tại trong `submitted_command`, chưa có trong `processed_command`. |
| **Tra cứu command đã hoàn tất** | `200 OK` | `CommandResultDTO` | Đã có trong `processed_command` (`APPLIED`, `NOOP`, `REJECTED`, `FAILED`). |
| **Tra cứu command không tồn tại** | `404 Not Found` | `{"detail": "..."}` | Cả `submitted_command` và `processed_command` đều không có. |
| **Redis mất kết nối / Timeout** | `503 Service Unavailable` | `{"detail": "..."}` | Bắt `redis.RedisError` và trả lỗi kiểm soát. |
| **Marker Redis bị hỏng dữ liệu** | `503 Service Unavailable` | `{"detail": "..."}` | Dữ liệu marker không hợp lệ, không ném unhandled 500. |

HTTP layer maps adapter failures through shared presentation error categories,
so later Redis adapters do not require one concrete exception handler each.

---

## 6. Boundary Guarantees
- **Async Non-blocking**: Toàn bộ thao tác Redis ở Presentation sử dụng `redis.asyncio`, không bao giờ block event loop của FastAPI.
- **Không scan Redis stream**: Không sử dụng `XRANGE`/`XREVRANGE` để tra cứu command result mà dựa hoàn toàn vào key lookup $O(1)$.
- **Không leak internal state**: Không import supervisor, detector, profile hay expose trường nội bộ `storage_id`.
- **Composition fail-fast**: Khi inject custom control adapter, command-result reader và runtime-status reader phải được cung cấp rõ ràng; API không tự trộn Redis control với fake readers.
- **Strict marker parsing**: Reader reject field thừa/thiếu, datetime thiếu timezone, `changed` không phải boolean và command ID không khớp lookup key.
